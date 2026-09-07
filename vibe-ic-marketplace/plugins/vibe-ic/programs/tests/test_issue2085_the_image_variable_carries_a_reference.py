#!/usr/bin/env python3
"""#2085 — `VIBEIC_EDA_IMAGE` carries a REFERENCE, and a bare image Id is
refused by name instead of being spliced into `sha256@sha256:<digest>`.

WHAT WAS MEASURED
=================
The documented front door, with `--container`::

    python3 programs/vibe_ic_one_shot_runner.py <project> --pdk gf180mcuD \
            --container <name> --require-image <repo>@sha256:<digest>

`_capture_container_image` verified the container and exported the container's
`.Image` -- a bare `sha256:<64 hex>` **Id** -- as `VIBEIC_EDA_IMAGE`. An Id is a
valid ARGUMENT to `docker run` and is not a REFERENCE: it has no repository
half. `_eda_image.judged_image()` read the variable as a reference, and its own
`_repo_of` split `sha256:842d64…` into repository `sha256` + tag `842d64…`,
re-attached the pinned digest, and returned `sha256@sha256:<digest>` -- while
reporting `why_not = ''`, i.e. SUCCESS.

MEASURED 2026-09-07 on 8hd-3 against the frozen base 623bbb841bb7, container
started from the pinned reference, control taken by explicit blob swap::

    control   VIBEIC_EDA_IMAGE   = sha256:8c5694ab…                 (a bare Id)
              judged_image().ref = 'sha256@sha256:8c5694ab…'  why_not = ''
              database_unit_um   = None
              unavailable        = "the tech LEF for 'gf180mcuD' could not be
                                   read in the image (…; rc=125): [\"Run
                                   'docker run --help' for more information\"]"
    fixed     VIBEIC_EDA_IMAGE   = <repo>@sha256:8c5694ab…
              judged_image().ref = <repo>@sha256:8c5694ab…
              database_unit_um   = 0.0005  (DATABASE MICRONS 2000 ; at
                                   …/gf180mcu_fd_sc_mcu7t5v0__nom.tlef:40)

The degradation is the part that had to be fixed twice. `docker run
sha256@sha256:…` exits 125 ("pull access denied for sha256"), the step recorded
`unavailable`, and the declaration published `database_unit_um` as
NOT_DETERMINED -- so "the reference I was handed was malformed" and "this
technology states no database unit" reached the reader as the same answer. This
repo holds every other input to the rule that those two must never read alike;
the image identity was not held to it.

ONE VARIABLE, ONE SHAPE
=======================
The variable carries the identity every other site in this tree uses --
`<repo>@sha256:<digest>`, the shape `_eda_pin.image_reference()` composes. So
both ends are asserted here:

  * the PRODUCER (`vibe_ic_one_shot_runner._propagatable_image`) exports a
    reference, and exports NOTHING rather than an Id when it cannot name one;
  * the CONSUMER (`_eda_image.judged_image`) REFUSES a bare Id and says what
    arrived, naming `IMAGE_ID_NOT_A_REFERENCE`;
  * and the splice itself is unreachable: `_eda_image._repo_of` answers "" for
    a shape with no repository, and `_pinned` will not compose `@` onto it.

EVERY ASSERTION IS BIDIRECTIONAL. Each test names the state it refuses, and the
refusal path is exercised as well as the acceptance path -- a check that cannot
fail is not a check.

NO DOCKER. The container inspection is faked at the one seam each module owns
(`_eda_image._run`, `_eda_pin._docker`, `container_image_provenance.verify`), in
exactly the shape docker prints. The real-silicon run is in the lane record.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import pathlib
import sys

import pytest

_PROGRAMS = pathlib.Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import _eda_image as EI          # noqa: E402
import _eda_pin as EP            # noqa: E402

#: A digest-shaped identity that is not this tree's pin, so nothing here can
#: pass by accidentally agreeing with the shipped constant.
_DIGEST = "sha256:" + "ab12cd34" * 8
#: A repository with no host literal in it. The fleet's registry is deployment
#: CONFIGURATION and is never written into this tree.
_REPO = "example.invalid/eda"
_REF = f"{_REPO}@{_DIGEST}"
#: What `docker inspect <container> --format '{{.Image}}'` prints: an Id.
_ID = "sha256:" + "0123456789abcdef" * 4

_SPLICE = "sha256@sha256:"


class _Proc:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def _fake_eda_image_run(repo_digests, image_id, version="9.9.9"):
    """A stand-in for `_eda_image._run` answering the three formats that module
    asks for, in docker's own output shape."""
    def run(*argv, timeout=None):
        argv = list(argv)
        fmt = argv[argv.index("--format") + 1] if "--format" in argv else ""
        if "RepoDigests" in fmt:
            return _Proc(0, json.dumps(repo_digests) + "\t" + image_id + "\n")
        if "image.version" in fmt:
            return _Proc(0, version + "\n")
        return _Proc(1, "", "unexpected format: " + fmt)
    return run


# ── the consumer: a bare Id is refused, and the splice is unreachable ──────
def test_a_bare_image_id_override_is_refused_and_names_its_shape(monkeypatch):
    """THE DEFECT, in one assertion. The override is an Id; the answer must be
    a refusal that says so, never a reference built out of it.

    The fake resolves the Id happily -- that is the point. On the pre-fix code
    this path SUCCEEDS and returns `sha256@sha256:<digest>`; the refusal is not
    "docker could not find it", it is "that is not a reference"."""
    monkeypatch.setattr(EI, "_run", _fake_eda_image_run([_REF], _ID))
    j = EI.judged_image(env={"VIBEIC_EDA_IMAGE": _ID})
    assert j.ref is None, f"a bare Id was accepted as a reference: {j.ref!r}"
    assert _SPLICE not in (j.ref or ""), j.ref
    assert EP.IMAGE_ID_NOT_A_REFERENCE in j.why_not, j.why_not
    assert _ID in j.why_not, "the refusal must name the value that arrived"
    assert j.source == "override", j.source


def test_the_refusal_says_what_to_pass_instead(monkeypatch):
    """A refusal the reader cannot act on is a refusal they re-run. It has to
    name the shape that IS accepted, not only the one that is not."""
    monkeypatch.setattr(EI, "_run", _fake_eda_image_run([_REF], _ID))
    why = EI.judged_image(env={"VIBEIC_EDA_IMAGE": _ID}).why_not
    assert "@sha256:<digest>" in why, why
    assert "image_reference" in why, why


def test_a_proper_reference_override_is_STILL_ACCEPTED(monkeypatch):
    """THE OTHER DIRECTION, and the one that keeps the refusal from being a
    blanket ban. Naming an image by hand is the operator's deliberate call and
    it still works -- only the shape that cannot be named again is refused."""
    monkeypatch.setattr(EI, "_run", _fake_eda_image_run([_REF], _ID))
    j = EI.judged_image(env={"VIBEIC_EDA_IMAGE": _REF})
    assert j.ref == _REF, j
    assert j.digest == _DIGEST and j.why_not == ""


def test_a_tag_override_is_still_accepted_and_still_pinned(monkeypatch):
    """And a TAG override still resolves to the digest-pinned reference it
    names -- `judged_image`'s whole contract. A repository is present here, so
    composing `<repo>@<digest>` is legitimate and must not be caught by the new
    guard."""
    monkeypatch.setattr(EI, "_run", _fake_eda_image_run([_REF], _ID))
    j = EI.judged_image(env={"VIBEIC_EDA_IMAGE": f"{_REPO}:1.2.3"})
    assert j.ref == _REF, j
    assert _SPLICE not in j.ref


def test_the_repository_of_a_bare_id_is_nothing_not_the_word_sha256():
    """THE SPLICE AT ITS SOURCE. `rpartition(':')` on an Id yields the
    repository `sha256`, which is how a valid digest ended up behind an
    invented repository. There is no repository half, and "" is how this tree
    says so."""
    assert EI._repo_of(_ID) == "", EI._repo_of(_ID)
    assert EP.repository_of(_ID) == ""
    # ...while a real reference still parses, including one whose colon is a
    # PORT rather than a tag.
    assert EI._repo_of(_REF) == _REPO
    assert EI._repo_of("host.invalid:5000/eda:1.2.3") == "host.invalid:5000/eda"


def test_pinned_never_composes_an_at_reference_onto_no_repository():
    """The composer itself. Given a ref that names no place, `_pinned` returns
    the digest rather than gluing `@` onto an empty or invented repository."""
    got = EI._pinned(_ID, _DIGEST, "repo-digest")
    assert _SPLICE not in got, got
    assert got == _DIGEST, got
    # ...and it still composes when there IS a repository.
    assert EI._pinned(f"{_REPO}:1.2.3", _DIGEST, "repo-digest") == _REF


# ── the producer: the runner exports a reference, or nothing ──────────────
def _runner():
    spec = importlib.util.spec_from_file_location(
        "vibe_ic_one_shot_runner", _PROGRAMS / "vibe_ic_one_shot_runner.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["vibe_ic_one_shot_runner"] = mod
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


def _fake_eda_pin_docker(config_image=None, image_id=None, repo_digests=None):
    """A stand-in for `_eda_pin._docker`, dispatching on the FORMAT the caller
    asked for — the two questions that module puts to docker have different
    answers and a fake that conflates them is not a fake of docker."""
    def docker(*argv, timeout=None):
        argv = list(argv)
        fmt = argv[argv.index("--format") + 1] if "--format" in argv else ""
        if config_image is None:
            return 1, "", "Error: No such object"
        if "Config.Image" in fmt:
            return 0, f"{image_id}\t{config_image}\n", ""
        if "RepoDigests" in fmt:
            return 0, json.dumps(repo_digests or []) + "\n", ""
        return 1, "", f"unexpected format: {fmt}"
    return docker


def _capture(rec, tmp, *, docker=None, env=None):
    """Drive `_capture_container_image` with a stubbed prober and a stubbed
    docker, and return `(record, VIBEIC_EDA_IMAGE after)`."""
    mod = _runner()
    import container_image_provenance as _cip
    real_verify, real_docker = _cip.verify, EP._docker
    saved = {k: os.environ.get(k) for k in ("VIBEIC_EDA_IMAGE",
                                            "IIC_EDA_IMAGE")}
    try:
        _cip.verify = lambda *a, **k: dict(rec)               # type: ignore
        EP._docker = docker or _fake_eda_pin_docker()         # type: ignore
        for k in ("VIBEIC_EDA_IMAGE", "IIC_EDA_IMAGE"):
            os.environ.pop(k, None)
        os.environ.update(env or {})
        out = mod._capture_container_image(tmp, "c", None)
        return out, os.environ.get("VIBEIC_EDA_IMAGE")
    finally:
        _cip.verify = real_verify                             # type: ignore
        EP._docker = real_docker                              # type: ignore
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


def test_the_runner_exports_a_reference_never_the_container_image_id(tmp_path):
    """THE PRODUCER HALF OF THE DEFECT. The container was started from a
    digest-pinned reference, so docker records that reference verbatim in
    `.Config.Image`; the run discarded it in favour of `.Image`, the Id. It is
    the reference that must reach the child."""
    rec, exported = _capture(
        {"verdict": "PASS", "image_ref": _REF, "image_id": _ID}, tmp_path)
    assert exported == _REF, exported
    assert not EP.is_bare_image_id(exported), exported
    assert rec["propagated_to_child_docker_run"] == _REF


def test_a_container_started_from_a_TAG_propagates_its_repo_digest(tmp_path):
    """The immutability the old preference was reaching for, in a shape that
    resolves. A tag can be re-pointed, so the portable digest of the bytes the
    container is actually running wins over the tag -- and it is a reference,
    which the Id was not."""
    rec, exported = _capture(
        {"verdict": "PASS", "image_ref": f"{_REPO}:1.2.3", "image_id": _ID},
        tmp_path, docker=_fake_eda_pin_docker(f"{_REPO}:1.2.3", _ID, [_REF]))
    assert exported == _REF, exported
    assert rec["propagated_to_child_docker_run"] == _REF


def test_the_tag_is_propagated_when_no_portable_digest_can_be_named(tmp_path):
    """A tag is mutable and is still a REFERENCE. When the bytes carry no
    registry digest -- a locally built image -- the tag is what a child can
    run, and withholding it would revive the defect this capture exists for
    (a child resolving an image of its own and landing on upstream)."""
    _rec, exported = _capture(
        {"verdict": "PASS", "image_ref": f"{_REPO}:1.2.3", "image_id": _ID},
        tmp_path, docker=_fake_eda_pin_docker(f"{_REPO}:1.2.3", _ID, []))
    assert exported == f"{_REPO}:1.2.3", exported


def test_nothing_is_propagated_when_only_an_id_is_known(tmp_path):
    """THE REFUSAL DIRECTION, and the one the old code got backwards. A
    container started FROM an Id, whose image carries no registry digest, has
    no reference to hand on. Exporting the Id "because it is better than
    nothing" is precisely what produced a malformed reference that read as a
    successful one -- so nothing is exported, and the record says why."""
    rec, exported = _capture(
        {"verdict": "PASS", "image_ref": _ID, "image_id": _ID},
        tmp_path, docker=_fake_eda_pin_docker(_ID, _ID, []))
    assert exported is None, exported
    assert rec["propagated_to_child_docker_run"] is None
    assert rec["propagation_withheld"], rec
    assert "not a reference" in rec["propagation_withheld"]


def test_an_unresolvable_image_still_propagates_NOTHING_and_claims_nothing(
        tmp_path):
    """NO-LEAK, carried over: when the probe identified no image at all there
    is nothing verified to propagate, and the record must not grow a withheld
    reason for a propagation that was never possible."""
    rec, exported = _capture(
        {"verdict": "SKIP", "reason": "image identity unverifiable"}, tmp_path)
    assert exported is None
    assert "propagated_to_child_docker_run" not in rec
    assert "propagation_withheld" not in rec


def test_an_operator_set_override_is_still_not_overwritten(tmp_path):
    """NO-LEAK, carried over: deliberately running the flow against another
    image is a real experiment. This fills an EMPTY slot only."""
    rec, exported = _capture(
        {"verdict": "PASS", "image_ref": _REF, "image_id": _ID}, tmp_path,
        env={"VIBEIC_EDA_IMAGE": "operator/pinned:1.2.3"})
    assert exported == "operator/pinned:1.2.3"
    assert rec["propagated_to_child_docker_run"] is None


# ── the shared predicate, both directions ────────────────────────────────
@pytest.mark.parametrize("value", [
    "sha256:" + "a" * 64,          # the canonical Id docker inspect prints
    "sha256:4e89590fcb9c",         # the truncated form docker also resolves
])
def test_these_are_image_ids(value):
    assert EP.is_bare_image_id(value), value
    assert EP.repository_of(value) == ""


@pytest.mark.parametrize("value", [
    _REF,                          # a digest-pinned reference
    f"{_REPO}:1.2.3",              # a tag
    "host.invalid:5000/eda:1.2.3",  # a tag behind a PORT
    "",                            # nothing at all
    "sha256",                      # the invented repository, on its own
])
def test_these_are_not_image_ids(value):
    assert not EP.is_bare_image_id(value), value


def test_a_repository_less_digest_is_not_a_reference_either():
    """`@sha256:<digest>` with nothing in front of it is the same malformed
    shape as `sha256@sha256:<digest>`; neither may be recognised as one."""
    assert EP.reference_digest(_REF) == _DIGEST
    assert EP.reference_digest("@" + _DIGEST) is None
    assert EP.reference_digest(_DIGEST) is None
    assert EP.reference_digest(f"{_REPO}:1.2.3") is None
