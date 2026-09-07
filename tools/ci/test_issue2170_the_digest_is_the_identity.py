"""The image pin is an identity carried by the DIGEST, not by the reference.

WHAT WAS MEASURED, AND WHY IT IS A DEFECT AND NOT A DEPLOYMENT MISTAKE
=====================================================================
vibe-ic#2170. Lane czab3 ran the three landing tiers in BOTH settings of
``VIBEIC_EDA_IMAGE_REPO`` and found no setting under which the tip was green:
with the env UNSET, 23 engine-driving cases in
``programs/tests/test_landing_merge_verdict`` recorded
``[NORECORD] hermetic candidate: fixed image inspection failed: No such image:
ghcr.io/vibeic/vibeic-eda@sha256:…`` -- about bytes that WERE on the host,
pulled from the fleet mirror and therefore held under a different repository
name. Same bytes, same digest, different string.

RE-MEASURED HERE 2026-09-07 on 8HD-8 and 8HD-9. 8HD-8 happens to hold the
pinned image under BOTH names, so both arms are green on it; 8HD-9 holds it
under the mirror name ONLY::

    8HD-9 $ docker image inspect --format '{{json .RepoDigests}}' <mirror>@<pin>
    ["<fleet-mirror>/vibeic-eda@sha256:89a8fd72…"]

A check whose answer depends on which of two equivalent names a host happened
to pull under is measuring the host's network, not the runtime -- and the two
hosts disagreeing about the SAME bytes is the proof of that.

THE RULING THIS FILE ENFORCES: the digest is the identity, the repository is
configuration. So there are exactly three directions, and all three are here:

  * a DIFFERENT digest is REFUSED -- as strictly as before, and nothing about
    this change may widen the comparison to "any image";
  * the SAME digest under a DIFFERENT repository is ACCEPTED;
  * NOTHING local carrying the digest is REFUSED, and refused WITHOUT starting
    a pull -- the direction that keeps `test_with_nothing_local_it_refuses_
    rather_than_starting_a_pull` meaningful.

EVERY TEST HERE IS A FAKE-DOCKER UNIT TEST. Not one of them asks what this
host holds: a test whose premise is the host's image list asserts something
different on every machine, which is exactly the failure being fixed.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent


def _load(name: str, path: Path):
    """The module under its CANONICAL name, registered before it executes.

    Both, deliberately. `hermetic_candidate_runner` defines a dataclass, and a
    dataclass cannot be built while its own module is missing from
    `sys.modules` -- `_is_type` reads `sys.modules[cls.__module__].__dict__`
    and dies on the None. And loading it under a PRIVATE name would give this
    file a second `Refusal` class, so every `pytest.raises` below would miss
    the exception it was written to catch.
    """
    existing = sys.modules.get(name)
    if existing is not None and getattr(existing, "__file__", None) == str(path):
        return existing
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PIN = _load("_eda_pin",
            _REPO / "vibe-ic-marketplace/plugins/vibe-ic/programs/_eda_pin.py")
RUN = _load("hermetic_candidate_runner",
            _HERE / "hermetic_candidate_runner.py")

#: A digest that is NOT the pin, spelled in the exact identity shape so that
#: nothing is refused merely for being malformed. If a refusal below fires on
#: shape rather than on identity, it is not proving what it claims.
OTHER_DIGEST = "sha256:" + ("b" * 64)
MIRROR = "registry.invalid:5000/vibeic-eda"
PUBLISHED = "ghcr.io/vibeic/vibeic-eda"


class FakeDocker:
    """A host whose image list is stated, not discovered.

    TAGGED AND UNTAGGED ARE DIFFERENT STATES, AND THIS MODELS BOTH — the lesson
    of #2170's second miss. The first version of this fake answered `image ls`
    with every RepoDigest of every held image, which is what `docker image ls
    -a` does; the production code ran `docker image ls`, which lists TAGGED
    images only. The fake was MORE CAPABLE THAN THE COMMAND, so the whole file
    was 13 passed on the very host that was answering IMAGE_NOT_PRESENT at the
    same hour. A fake that models a different command than the code invokes is
    not a test of the code.

    So: `image ls` shows the tagged rows, `image ls -a` shows all of them, and
    an image pulled by digest and never tagged — the state a digest pin
    actually produces — is INVISIBLE to the un-flagged form. That asymmetry is
    the only reason the arms below can fail.

    Every argv is recorded, so an arm can assert what was ASKED as well as what
    was answered: "it refused" and "it refused without reaching the network"
    are different claims and only the argv separates them.
    """

    def __init__(self, tagged=None, untagged=None):
        #: reference -> the RepoDigests docker would report for it.
        self.tagged = dict(tagged or {})
        self.untagged = dict(untagged or {})
        self.held = {**self.tagged, **self.untagged}
        self.argv: list[tuple[str, ...]] = []

    def _rows(self, all_images: bool):
        source = self.held if all_images else self.tagged
        return sorted({d for entries in source.values() for d in entries})

    def __call__(self, *argv: str, timeout: int = 0):
        self.argv.append(tuple(argv))
        if argv[:2] == ("image", "inspect"):
            ref = argv[-1]
            if ref not in self.held:
                return 1, "", f"Error: No such image: {ref}"
            return 0, json.dumps(self.held[ref]) + "\n", ""
        if argv[:2] == ("image", "ls"):
            rows = self._rows(all_images="-a" in argv or "--all" in argv)
            return 0, "\n".join(rows) + "\n", ""
        raise AssertionError(f"the fake host was asked something it does not "
                             f"model: {list(argv)}")

    def pulled(self) -> bool:
        return any(a and a[0] in {"pull", "run", "create"} for a in self.argv)

    def listings(self):
        return [a for a in self.argv if a[:2] == ("image", "ls")]


def _host(monkeypatch, held=None, *, tagged=None, untagged=None):
    fake = FakeDocker(tagged=tagged if tagged is not None else held,
                      untagged=untagged)
    monkeypatch.setattr(PIN, "_docker", fake)
    return fake


# ── direction 1: a different digest is REFUSED ─────────────────────────────
def test_a_different_digest_is_refused_under_the_configured_repository(
        monkeypatch):
    """The strictness that must survive the fix.

    This host holds an image, under exactly the repository the operator
    configured, and its digest is not the pin. Accepting it would be the
    "widen the comparison to any image" failure this change is forbidden to
    make, so it is asserted first.
    """
    fake = _host(monkeypatch, {
        f"{MIRROR}@{PIN.IMAGE_DIGEST}": [f"{MIRROR}@{OTHER_DIGEST}"],
    })
    ref, why = PIN.pinned_image_present({PIN.IMAGE_REPO_ENV: MIRROR})
    assert ref is None, (
        f"an image whose digest is {OTHER_DIGEST} was accepted as the pinned "
        f"runtime {PIN.IMAGE_DIGEST}; the digest is the identity and this is "
        f"the one comparison that may never be widened (#2170)")
    assert PIN.IMAGE_NOT_PRESENT in why
    assert PIN.IMAGE_DIGEST in why, (
        f"the refusal does not name the digest it wanted, so a reader cannot "
        f"tell a stale mirror from a mis-set repository: {why!r}")
    assert not fake.pulled()


def test_a_different_digest_is_refused_under_every_repository(monkeypatch):
    """And the digest-wide rung is not a back door.

    The same wrong bytes, this time held under SEVERAL names, so that the
    fallback introduced by #2170 -- "under any repository" -- is the thing
    being exercised. It must find nothing, because none of those names carries
    the pinned digest.
    """
    _host(monkeypatch, {
        f"{PUBLISHED}@{OTHER_DIGEST}": [f"{PUBLISHED}@{OTHER_DIGEST}",
                                        f"{MIRROR}@{OTHER_DIGEST}"],
    })
    ref, why = PIN.pinned_image_present({PIN.IMAGE_REPO_ENV: PUBLISHED})
    assert ref is None
    assert PIN.IMAGE_NOT_PRESENT in why


# ── direction 2: the same digest under a different repository is ACCEPTED ───
def test_the_same_digest_under_a_different_repository_is_accepted(monkeypatch):
    """THE MEASURED RED, as a unit.

    The operator has configured nothing, so the published repository is asked
    for. This host pulled the very same bytes from the fleet mirror, and holds
    them under that name only -- 8HD-9's state, verbatim. Before #2170 this
    was `IMAGE_NOT_PRESENT` about an image that was present.
    """
    fake = _host(monkeypatch, {
        f"{MIRROR}@{PIN.IMAGE_DIGEST}": [f"{MIRROR}@{PIN.IMAGE_DIGEST}"],
    })
    ref, why = PIN.pinned_image_present({})
    assert why == "", (
        f"the pinned bytes are on this host under {MIRROR} and were refused "
        f"because the string in front of the digest differs: {why!r}")
    assert PIN.reference_digest(ref) == PIN.IMAGE_DIGEST
    assert PIN.repository_of(ref) == MIRROR, (
        f"the accepted reference must be one this host can actually run; "
        f"got {ref!r}")
    assert not fake.pulled()


def test_the_configured_repository_is_still_the_name_answered_with(monkeypatch):
    """Configuration is recorded, it is simply not the identity.

    When the configured repository DOES hold the pinned bytes, the reference
    handed back is the one the operator set -- a reader who configured a mirror
    should see the mirror named, not a second name the same bytes also answer
    to.
    """
    _host(monkeypatch, {
        f"{MIRROR}@{PIN.IMAGE_DIGEST}": [f"{MIRROR}@{PIN.IMAGE_DIGEST}",
                                         f"{PUBLISHED}@{PIN.IMAGE_DIGEST}"],
    })
    ref, why = PIN.pinned_image_present({PIN.IMAGE_REPO_ENV: MIRROR})
    assert why == ""
    assert ref == f"{MIRROR}@{PIN.IMAGE_DIGEST}"


# ── direction 3: nothing local is REFUSED, and without a pull ───────────────
def test_with_no_local_image_carrying_the_digest_it_refuses_without_pulling(
        monkeypatch):
    """The direction that keeps the no-pull guarantee meaningful.

    An empty host. The answer must be a refusal that names the digest, and the
    argv the fake recorded must contain no route that fetches -- a check that
    starts a multi-gigabyte pull is a check people switch off.
    """
    fake = _host(monkeypatch, {})
    ref, why = PIN.pinned_image_present({})
    assert ref is None
    assert PIN.IMAGE_NOT_PRESENT in why and PIN.IMAGE_DIGEST in why
    assert not fake.pulled(), (
        f"a presence check reached a fetching route: {fake.argv}")


def test_an_unreadable_host_is_not_an_absent_image(monkeypatch):
    """"Could not read it" is not "read it and it was empty".

    A docker that cannot be run says so; it does not get to be evidence that
    the pinned image is missing.
    """
    def unusable(*argv, timeout=0):
        return -1, "", "docker unusable: OSError: [Errno 2]"
    monkeypatch.setattr(PIN, "_docker", unusable)
    ref, why = PIN.pinned_image_present({})
    assert ref is None
    assert "docker unusable" in why


# ── the same three directions on the landing runner ────────────────────────
def test_the_runner_recognises_the_pin_by_digest_alone():
    assert RUN.carries_pinned_digest(f"{MIRROR}@{RUN.IMAGE_DIGEST}")
    assert RUN.carries_pinned_digest(f"{PUBLISHED}@{RUN.IMAGE_DIGEST}")
    assert not RUN.carries_pinned_digest(f"{MIRROR}@{OTHER_DIGEST}")
    # A bare Id is not a reference, and a digest with no repository in front of
    # it is the same malformed shape (#2085).
    assert not RUN.carries_pinned_digest(RUN.IMAGE_DIGEST)
    assert not RUN.carries_pinned_digest(f"@{RUN.IMAGE_DIGEST}")
    assert not RUN.carries_pinned_digest(None)


class _RunnerDocker:
    """`hermetic_candidate_runner.Docker`'s call shape, over a stated host.

    Same tagged/untagged asymmetry as `FakeDocker`, and for the same reason:
    the runner's fallback runs `docker image ls`, and an image pulled by digest
    is not in that listing unless `-a` is passed.
    """

    def __init__(self, tagged=None, untagged=None):
        self.tagged = dict(tagged or {})
        self.untagged = dict(untagged or {})
        self.held = {**self.tagged, **self.untagged}
        self.argv = []

    def call(self, args):
        args = list(args)
        self.argv.append(tuple(args))

        class P:
            pass
        p = P()
        if args[:2] == ["image", "inspect"]:
            ref = args[-1]
            if ref not in self.held:
                p.returncode, p.stdout = 1, b""
                p.stderr = f"Error: No such image: {ref}".encode()
                return p
            p.returncode = 0
            p.stdout = json.dumps([self.held[ref]]).encode()
            p.stderr = b""
            return p
        if args[:2] == ["image", "ls"]:
            source = self.held if ("-a" in args or "--all" in args) else self.tagged
            refs = sorted({d for doc in source.values()
                           for d in doc["RepoDigests"]})
            p.returncode, p.stdout, p.stderr = 0, ("\n".join(refs) + "\n").encode(), b""
            return p
        raise AssertionError(f"unmodelled docker call: {args}")


def _doc(repo_digests):
    return {"Id": "sha256:" + ("c" * 64), "RepoDigests": list(repo_digests),
            "Os": "linux", "Architecture": "amd64"}


def test_the_runner_binds_the_pin_held_under_another_repository(monkeypatch):
    """The 23 NORECORD cases, as a unit.

    `IMAGE` is composed from the configured repository; this host holds the
    pinned digest under the mirror only. The profile must bind, and the
    reference it reports must be the one a `docker run` on this host resolves.
    """
    monkeypatch.setattr(RUN, "IMAGE", f"{PUBLISHED}@{RUN.IMAGE_DIGEST}")
    monkeypatch.setattr(RUN, "IMAGE_REPO_DIGEST", f"{PUBLISHED}@{RUN.IMAGE_DIGEST}")
    docker = _RunnerDocker({
        f"{MIRROR}@{RUN.IMAGE_DIGEST}": _doc([f"{MIRROR}@{RUN.IMAGE_DIGEST}"]),
    })
    profile = RUN._image_profile(docker)
    assert RUN.carries_pinned_digest(profile["reference"])
    assert profile["reference"] == f"{MIRROR}@{RUN.IMAGE_DIGEST}"
    assert RUN.IMAGE == f"{MIRROR}@{RUN.IMAGE_DIGEST}", (
        "the containers below are started from the module global, so a "
        "resolution that does not move it would run a different image from "
        "the one the provenance record names")


def test_the_runner_refuses_a_host_holding_only_another_digest(monkeypatch):
    monkeypatch.setattr(RUN, "IMAGE", f"{PUBLISHED}@{RUN.IMAGE_DIGEST}")
    monkeypatch.setattr(RUN, "IMAGE_REPO_DIGEST", f"{PUBLISHED}@{RUN.IMAGE_DIGEST}")
    docker = _RunnerDocker({
        f"{MIRROR}@{OTHER_DIGEST}": _doc([f"{MIRROR}@{OTHER_DIGEST}"]),
    })
    with pytest.raises(RUN.Refusal):
        RUN._image_profile(docker)


def test_the_runner_refuses_an_empty_host_without_pulling(monkeypatch):
    monkeypatch.setattr(RUN, "IMAGE", f"{PUBLISHED}@{RUN.IMAGE_DIGEST}")
    monkeypatch.setattr(RUN, "IMAGE_REPO_DIGEST", f"{PUBLISHED}@{RUN.IMAGE_DIGEST}")
    docker = _RunnerDocker({})
    with pytest.raises(RUN.Refusal):
        RUN._image_profile(docker)
    assert all(a[0] not in {"pull", "run", "create"} for a in docker.argv), (
        f"the image binding reached a fetching route: {docker.argv}")


def test_the_runner_refuses_an_image_that_binds_no_registry_digest(monkeypatch):
    """A locally BUILT image resolves and carries no RepoDigests at all.

    It is not the pinned runtime, whatever it is called, and the refusal must
    fire on the binding rather than on the lookup.
    """
    monkeypatch.setattr(RUN, "IMAGE", f"{PUBLISHED}@{RUN.IMAGE_DIGEST}")
    monkeypatch.setattr(RUN, "IMAGE_REPO_DIGEST", f"{PUBLISHED}@{RUN.IMAGE_DIGEST}")
    docker = _RunnerDocker({f"{PUBLISHED}@{RUN.IMAGE_DIGEST}": _doc([])})
    with pytest.raises(RUN.Refusal):
        RUN._image_profile(docker)


# ── the substitution is DISCLOSED BY NAME ──────────────────────────────────
# ORCHESTRATOR RULING, 2026-09-07. The digest decides identity, so the same
# bytes under another repository are the same image and refusing them protects
# nothing. But the OLD contract these tests replaced was defending something
# real: it made `VIBEIC_EDA_IMAGE_REPO` MEANINGFUL. A fallback that is silent
# hands the operator a knob that does nothing and never tells them the name
# they configured is absent from this host. So the substitution is recorded --
# which reference was CONFIGURED, which was USED -- and a reader can audit it
# after the fact instead of inferring it.

def test_the_receipt_discloses_both_references_when_they_differ(monkeypatch,
                                                                capsys):
    monkeypatch.setattr(RUN, "IMAGE", f"{PUBLISHED}@{RUN.IMAGE_DIGEST}")
    monkeypatch.setattr(RUN, "IMAGE_REPO_DIGEST", f"{PUBLISHED}@{RUN.IMAGE_DIGEST}")
    docker = _RunnerDocker({
        f"{MIRROR}@{RUN.IMAGE_DIGEST}": _doc([f"{MIRROR}@{RUN.IMAGE_DIGEST}"]),
    })
    profile = RUN._image_profile(docker)
    assert profile["configured_reference"] == f"{PUBLISHED}@{RUN.IMAGE_DIGEST}"
    assert profile["reference"] == f"{MIRROR}@{RUN.IMAGE_DIGEST}"
    assert profile["configured_reference"] != profile["reference"], (
        "this arm is only meaningful when a substitution actually happened")
    said = capsys.readouterr().err
    assert "[DISCLOSURE]" in said
    assert PUBLISHED in said and MIRROR in said, (
        f"a disclosure that does not NAME both references cannot be acted on: "
        f"{said!r}")


def test_the_disclosure_records_the_configured_name_even_when_nothing_moved(
        monkeypatch, capsys):
    """MANDATORY, NOT CONDITIONAL.

    `configured_reference` is present on the un-substituted arm too. A key that
    appears only when something went sideways is a key whose ABSENCE carries
    the meaning, and absence is what nobody audits. The stderr line is the part
    that is conditional -- there is nothing to disclose when the configured
    name is the one that answered.
    """
    monkeypatch.setattr(RUN, "IMAGE", f"{MIRROR}@{RUN.IMAGE_DIGEST}")
    monkeypatch.setattr(RUN, "IMAGE_REPO_DIGEST", f"{MIRROR}@{RUN.IMAGE_DIGEST}")
    docker = _RunnerDocker({
        f"{MIRROR}@{RUN.IMAGE_DIGEST}": _doc([f"{MIRROR}@{RUN.IMAGE_DIGEST}"]),
    })
    profile = RUN._image_profile(docker)
    assert profile["configured_reference"] == profile["reference"]
    assert "[DISCLOSURE]" not in capsys.readouterr().err

# The verifier half of the disclosure -- that a receipt DROPPING
# `configured_reference`, or carrying a foreign one, is REFUSED -- is proved in
# tools/ci/test_hermetic_candidate_runner.py against a receipt this runner
# actually produced, rather than against a dict assembled here. A hand-built
# receipt only proves that `_exact_keys` compares sets, which was never in
# doubt; the driven one proves the producer emits the field and the verifier
# refuses its absence.


# ══════════════════════════════════════════════════════════════════════════
# THE STATE A DIGEST PIN ACTUALLY PRODUCES: HELD, AND UNTAGGED
#
# ISOLATED BY LANE czpinconf ON 8HD-9, 2026-09-07, and it is the reason the
# first cut of this fix changed nothing in the direction it was written for.
# An image pulled BY DIGEST is never given a tag, so it is DANGLING, and
# `docker image ls` lists tagged images only. Measured on that host: 0 rows
# without `-a`, 1 row with it. `local_references_for_digest` therefore returned
# `((), "")` — empty AND errorless — about a host that held the pin, and the
# unset arm stayed at 23 failed with membership IDENTICAL to main's.
#
# These arms hold the untagged state, which is what the fleet is really in.
# ══════════════════════════════════════════════════════════════════════════

def test_the_pin_is_found_when_it_is_held_untagged(monkeypatch):
    """THE MEASURED FLEET STATE. Pulled by digest, never tagged, one name."""
    ref = f"{MIRROR}@{PIN.IMAGE_DIGEST}"
    fake = _host(monkeypatch, tagged={}, untagged={ref: [ref]})
    got, why = PIN.pinned_image_present({})
    assert why == "", (
        f"the pinned image is on this host, untagged, and was reported absent: "
        f"{why!r}. `docker image ls` without `-a` hides a dangling image.")
    assert got == ref
    assert not fake.pulled()


def test_the_digest_wide_listing_asks_docker_for_all_images(monkeypatch):
    """THE ARGV, ASSERTED. A future edit that drops `-a` reddens here.

    The behaviour above can also be satisfied by a fake that ignores the flag,
    which is exactly how this defect survived its first test file. So the flag
    is asserted on the argv the code actually hands docker, independently of
    what any fake chooses to answer.
    """
    fake = _host(monkeypatch, tagged={}, untagged={})
    PIN.pinned_image_present({})
    listings = [a for a in fake.argv if a[:2] == ("image", "ls")]
    assert listings, "the digest-wide rung never listed anything"
    for call in listings:
        assert "-a" in call or "--all" in call, (
            f"`docker image ls` without `-a` lists TAGGED images only, and an "
            f"image pulled by digest carries no tag — so this asks a question "
            f"that cannot see the pin on a digest-pinned host: {list(call)}")


def test_an_untagged_image_at_another_digest_is_still_refused(monkeypatch):
    """`-a` widens WHAT IS LISTED, never what is ACCEPTED."""
    other = f"{MIRROR}@{OTHER_DIGEST}"
    _host(monkeypatch, tagged={}, untagged={other: [other]})
    got, why = PIN.pinned_image_present({})
    assert got is None
    assert PIN.IMAGE_NOT_PRESENT in why and PIN.IMAGE_DIGEST in why


def test_the_runner_binds_the_pin_held_untagged(monkeypatch):
    monkeypatch.setattr(RUN, "IMAGE", f"{PUBLISHED}@{RUN.IMAGE_DIGEST}")
    monkeypatch.setattr(RUN, "IMAGE_REPO_DIGEST", f"{PUBLISHED}@{RUN.IMAGE_DIGEST}")
    ref = f"{MIRROR}@{RUN.IMAGE_DIGEST}"
    docker = _RunnerDocker(tagged={}, untagged={ref: _doc([ref])})
    profile = RUN._image_profile(docker)
    assert profile["reference"] == ref
    listings = [a for a in docker.argv if a[:2] == ("image", "ls")]
    assert listings and all("-a" in a or "--all" in a for a in listings), (
        f"the runner's fallback cannot see a dangling image: {docker.argv}")
