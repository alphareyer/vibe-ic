"""`restart-eda.sh` must name the image the rest of the flow resolves.

THE DEFECT, MEASURED on 8HD-6, 2026-09-21 (whole-main census).
`restart-eda.sh` is the plugin's own remedy for a stale or wrong-version
`vibeic-eda` container. It defaulted `IMAGE_REPO` to the PRE-GHCR name, so:

    RESTART_EDA_PRINT_IMAGE=1 ./restart-eda.sh 0.3.67
        -> vibeic/vibeic-eda:0.3.67

and `docker images` held ZERO images under `vibeic/vibeic-eda` on any host
measured, while `ghcr.io/vibeic/vibeic-eda:0.3.67` was present and the fleet
registry served the same tag. The dispatcher had to pass the full reference by
hand to recreate the shared container. A remedy that cannot name the image it
is remedying is not a remedy.

R-0915-96 already settled where the repository comes from: the DIGEST is the
identity, the REPOSITORY is deployment configuration, resolved through
`VIBEIC_EDA_IMAGE_REPO` over `_eda_pin.IMAGE_REPO_DEFAULT`. This file pins that
this script agrees with `_eda_pin` -- by ASKING BOTH, never by repeating a
literal, so a future move of the default cannot leave the two disagreeing while
a test that hard-codes the old address still passes.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROGS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGS))

import _eda_pin  # noqa: E402

REPO = Path(__file__).resolve().parents[5]
REL = "tools/vibeic-eda/restart-eda.sh"
COPIES = (REPO / REL,
          REPO / "vibe-ic-marketplace/plugins/vibe-ic" / REL)


def _resolve(script: Path, arg: str, **env_extra) -> str:
    env = dict(os.environ)
    env.pop("VIBEIC_EDA_IMAGE_REPO", None)
    env.pop("IMAGE_REPO", None)
    env["RESTART_EDA_PRINT_IMAGE"] = "1"
    env.update({k: v for k, v in env_extra.items() if v is not None})
    r = subprocess.run(["bash", str(script), arg], capture_output=True,
                       text=True, timeout=30, env=env, cwd=str(script.parent))
    assert r.returncode == 0, (r.stdout, r.stderr)
    return r.stdout.strip().splitlines()[-1].strip()


@pytest.mark.parametrize("script", COPIES, ids=lambda p: p.parts[-4])
def test_a_bare_tag_resolves_to_the_repository_eda_pin_resolves(script):
    """THE DEFECT, both directions in one line: the resolved repository is
    ASKED of `_eda_pin`, so this fails on the old `vibeic/vibeic-eda` default
    and on any future default the two stop sharing."""
    assert script.is_file(), script
    got = _resolve(script, "0.3.67")
    assert got == f"{_eda_pin.IMAGE_REPO_DEFAULT}:0.3.67", (
        f"{script} resolved a bare tag to {got!r}, but the flow resolves "
        f"images under {_eda_pin.IMAGE_REPO_DEFAULT!r} "
        f"(_eda_pin.IMAGE_REPO_DEFAULT). The plugin's own remedy for a stale "
        f"container must name the image the rest of the flow pins.")


@pytest.mark.parametrize("script", COPIES, ids=lambda p: p.parts[-4])
def test_the_one_config_point_is_honoured_the_same_way_eda_pin_honours_it(script):
    """`VIBEIC_EDA_IMAGE_REPO` is the SAME variable `_eda_pin` and
    `run_suite_in_eda_image.sh` read, so a host configured once is configured
    for all three. Before this, restart-eda read only its own `IMAGE_REPO` and
    a fleet that had set the variable everywhere was ignored here."""
    where = "registry.invalid:5000/vibeic-eda"
    got = _resolve(script, "0.3.67", VIBEIC_EDA_IMAGE_REPO=where)
    assert got == f"{where}:0.3.67", got
    # ...and `_eda_pin` reads the very same variable off the very same env.
    assert _eda_pin.image_repo({_eda_pin.IMAGE_REPO_ENV: where}) == where


@pytest.mark.parametrize("script", COPIES, ids=lambda p: p.parts[-4])
def test_an_explicit_repo_and_a_full_reference_are_still_the_callers_own(script):
    """THE NEGATIVE CONTROL. A change that made this script ALWAYS resolve the
    pinned repository would pass both tests above and take the operator's two
    documented escapes away: `IMAGE_REPO=` outranks the fleet variable, and a
    full reference passed as the argument is never touched at all."""
    got = _resolve(script, "0.3.67",
                   IMAGE_REPO="example.invalid/x",
                   VIBEIC_EDA_IMAGE_REPO="registry.invalid:5000/vibeic-eda")
    assert got == "example.invalid/x:0.3.67", got
    full = "ghcr.io/vibeic/vibeic-eda:0.3.63"
    assert _resolve(script, full) == full, "a full reference must pass through"


def test_the_two_shipped_copies_do_not_drift():
    """The repository copy and the INSTALLED plugin copy are byte-identical on
    main, and the installed one is what a user actually runs. Nothing gates
    that pair, so a fix applied to one of them ships only half -- which is how
    this defect would have survived its own repair."""
    a, b = (p.read_bytes() for p in COPIES)
    assert a == b, (
        f"{COPIES[0]} and {COPIES[1]} have drifted. They are the same script "
        f"shipped twice; a change to either must be made to both.")
