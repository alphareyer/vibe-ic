"""`fault_scan_chain_insert` must not own an image pointer of its own.

WHY THIS EXISTS
    A module-level `DEFAULT_IMAGE = "ghcr.io/vibeic/vibeic-eda:0.2.x"` does not
    fail loudly. It FREEZES: the code keeps running one toolchain while
    everything around it moves, and every gate reports green. This module is a
    likely place for one, because its two ghcr tags sit inside `MEASURED (...)`
    comment blocks that quote a tool's verbatim stdout beside the tag that
    produced it — so a reader scanning for pointers sees tags here and may
    conclude the module owns one.

    It does not. It runs whatever `fault_atpg_run` resolves, and
    `fault_atpg_run` asks `_eda_image.resolve()`.

    WHAT CHANGED. This used to be phrased as an exemption from
    `sync_image_version.py --check`, a repo-root drift net keyed on
    `tools/vibeic-eda/VERSION`. Both are deleted: that file held vibeic-eda's
    version number inside the vibe-ic repo, so every image release needed a PR
    here. The RULE did not change and is now held by
    `test_the_eda_image_is_resolved_not_remembered
    ::test_no_module_level_constant_freezes_an_image_version`, which ships with
    the plugin instead of running only in this repo's CI.

    These tests assert on VALUES the modules actually expose at runtime — the
    resolved image string and the module namespace — never on source text, so
    they cannot be satisfied by moving a comment around.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import fault_atpg_run as far
import fault_scan_chain_insert as fsci

_PROGRAMS = Path(far.__file__).resolve().parent
_REPO = _PROGRAMS.parents[3]

_GHCR = re.compile(r"^ghcr\.io/vibeic/vibeic-eda:\d+\.\d+\.\d+$")


def test_this_repo_stores_no_vibeic_eda_version():
    """A guard on this file's own premise. If an anchor file comes back, the
    module below can start reading it again and everything here still passes."""
    assert not (_REPO / "tools" / "vibeic-eda" / "VERSION").exists(), (
        "the anchor is back; every vibeic-eda release now needs a PR here again")


def test_a_total_blackout_STILL_ANSWERS_THE_PIN_AND_SAYS_SO(monkeypatch,
                                                             capsys):
    """With no env override and NOTHING reachable — no registry, no local
    image — the resolver answers the PINNED reference and announces that this
    host does not hold it.

    THE CONTRACT THIS PINS MOVED, AND IT MOVED THE STRICT WAY (vibe-ic#2100).
    What stood here asserted `got == _eda_image.LEGACY_IMAGE` — the resolver's
    old last rung, upstream `hpretl/iic-osic-tools:latest`, which carries
    neither Fault nor the patched yosys.  `_eda_image.resolve` no longer has
    that rung, or the two above it: it composes `<configured repo>@<pinned
    digest>` and returns it, because "the registry's current latest", "the
    newest local tag" and "upstream" each name a DIFFERENT toolchain and none
    of them is the one anybody pinned.  So the assertion was red on every
    fleet host in BOTH env arms — MEASURED 2026-09-07 on 8HD-4, answering
    `<repo>@sha256:8c5694…` where it demanded `hpretl/iic-osic-tools:latest`
    — and the fix is not to restore a silent downgrade.

    The PROPERTY the old test carried is kept and strengthened.  It was
    "a degradation is never SILENT"; it is now "there is no degradation to be
    silent about, and an absent image is still announced by name".  A blackout
    can no longer produce a toolchain the caller did not ask for, and
    `IMAGE_NOT_PRESENT` still reaches stderr so an operator is told the run
    will fetch rather than left to discover it.

    THE BLACKOUT IS STAGED ON EVERY LAUNCHER, INCLUDING `_eda_pin`'s.  That
    module reaches docker through its OWN `subprocess`, so substituting only
    the three this test used to substitute left the real daemon answering the
    presence question — which is a fact about the host, not about the code.
    """
    monkeypatch.delenv("VIBEIC_EDA_IMAGE", raising=False)
    monkeypatch.delenv("IIC_EDA_IMAGE", raising=False)

    def _never_present(*a, **k):
        raise OSError("no docker in this test")

    # A module reaches a process through `subprocess`, through `_pr`
    # (`_progress_run`, the progress-supervised drop-in) or through both.
    # Substituting on only one leaves the real launcher answering the
    # question this test is asking about a fake one, and the test then
    # passes or fails for a reason that has nothing to do with its subject.
    import _progress_run as _pr                                # noqa: PLC0415
    import _eda_image as M                                     # noqa: PLC0415
    import _eda_pin as _pin                                    # noqa: PLC0415
    # `_eda_image` and `_eda_pin` are DIFFERENT modules and each resolves the
    # daemon its own way, so substituting only on `far`'s own launcher leaves
    # the real daemon answering the blackout this test is staging.
    for _launcher in (getattr(far, "subprocess", None),
                      getattr(far, "_pr", None), _pr,
                      getattr(M, "subprocess", None),
                      getattr(_pin, "subprocess", None)):
        if _launcher is not None:
            monkeypatch.setattr(_launcher, "run", _never_present)
            monkeypatch.setattr(_launcher, "run_best_effort", _never_present,
                                raising=False)
    got = far._resolve_docker_image()
    assert got == _pin.image_reference(), got
    assert got != M.LEGACY_IMAGE, (
        "a blackout answered upstream iic-osic-tools, which carries neither "
        "Fault nor the patched yosys")
    assert "@sha256:" in got, ("a blackout answered a floating tag, which is "
                               "the one answer that means 'whatever this "
                               "machine happened to pull'")
    assert _pin.IMAGE_NOT_PRESENT in capsys.readouterr().err


def test_the_resolver_never_substitutes_a_local_tag_for_the_pin(monkeypatch):
    """The property the deleted anchor used to carry here, at its current
    strength.

    It was: with the registry unreachable, a DFT step must not silently drop to
    upstream iic-osic-tools — it must run a fork image this machine already
    holds, `<repo>:0.3.13` in the arm below.  That arm asserted a resolution
    ORDER (`registry_digest` → `local_tags` → upstream) that `_eda_image.
    resolve` no longer walks, so it was red on every host in both env arms
    (MEASURED 2026-09-07 on 8HD-4: `<repo>@sha256:8c5694…` where it demanded
    `ghcr.io/vibeic/vibeic-eda:0.3.13`).

    The stronger version of the same requirement, and the one that holds: a
    local tag can no longer be substituted AT ALL.  Neither a registry answer
    nor whatever semver tags this machine happens to carry may move what is
    run; only the digest may, and only by moving the pin.  The two stubs are
    kept precisely so the assertion is that they are NOT consulted.
    """
    monkeypatch.delenv("VIBEIC_EDA_IMAGE", raising=False)
    monkeypatch.delenv("IIC_EDA_IMAGE", raising=False)
    import _eda_image as M                                     # noqa: PLC0415
    import _eda_pin as _pin                                    # noqa: PLC0415
    monkeypatch.setattr(M, "registry_digest", lambda *a, **k: None)
    monkeypatch.setattr(M, "local_tags", lambda *a, **k: ["0.3.13"])
    # AND STAGE THE ONE CONDITION UNDER WHICH A SUBSTITUTION WAS EVER POSSIBLE:
    # this host does NOT hold the pinned bytes. Without this the arm is vacuous
    # on any host that does — MEASURED on 8HD-4 while driving the M3 mutation
    # (give `resolve` its local-tag/legacy rungs back): the mutation survived
    # here, because `local_image()` answered the pinned reference and the
    # substitution branch was never reached. A check that cannot fail is not a
    # check.
    monkeypatch.setattr(M, "local_image", lambda *a, **k: None)
    got = far._resolve_docker_image()
    assert got == _pin.image_reference(), got
    assert got != f"{M.IMAGE_REPO}:0.3.13", (
        "a local semver tag was substituted for the pinned digest")
    assert got != M.LEGACY_IMAGE, got


def test_an_explicit_env_image_still_wins(monkeypatch):
    """The escape hatch keeps working — otherwise the assertion above would be
    pinning a constant rather than a resolution order."""
    monkeypatch.setenv("VIBEIC_EDA_IMAGE", "example.invalid/some/image:9.9.9")
    assert far._resolve_docker_image() == "example.invalid/some/image:9.9.9"


def test_scan_chain_module_declares_no_image_of_its_own():
    """Walks the module NAMESPACE (runtime values), not the file text. Any
    module-level string that is a fully-qualified vibeic-eda image reference is a
    pointer that freezes: nothing advances it, and the code keeps pulling one
    toolchain while everything around it moves.
    """
    importlib.reload(fsci)
    offenders = {
        name: val
        for name, val in vars(fsci).items()
        if isinstance(val, str) and _GHCR.match(val.strip())
    }
    assert offenders == {}, (
        f"{Path(fsci.__file__).name} declares {offenders}, which nothing will "
        f"ever advance — this repo stores no vibeic-eda version any more, by "
        f"design. Resolve the image through fault_atpg_run, which asks "
        f"_eda_image.resolve()."
    )


def test_scan_chain_reports_the_image_the_atpg_module_resolved():
    """The value the module would publish in its report is the SAME value the
    registered module resolved — that indirection is what makes the exemption
    cost no live coverage."""
    assert fsci._fatpg is far
    assert fsci._fatpg.DOCKER_IMAGE == far.DOCKER_IMAGE
