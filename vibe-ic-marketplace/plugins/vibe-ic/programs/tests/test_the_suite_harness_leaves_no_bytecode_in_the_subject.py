"""The suite harness must not write bytecode into the tree it is measuring.

THE DEFECT, MEASURED (whole-main census, 8HD-6, 2026-09-21).
`tools/ci/run_suite_in_eda_image.sh` reads the image pin on the HOST, before any
container exists, by importing `_eda_pin` from the subject's own `programs/`
directory.  That import wrote `programs/__pycache__/` into the subject:

    fresh clone                                   __pycache__ dirs 0
    after the harness's own pin-read command      __pycache__ dirs 1
    `git status --porcelain` on both sides        0 lines

`git status` cannot see it -- the path is ignored -- and the attestation drift
instrument CAN, so an operator who runs the suite and then the hygiene set in
the same checkout measures a `[PREFLIGHT] ... bytecode/cache artefact(s)`
finding they created by measuring.  The census hit exactly that and had to
re-run its hygiene tier in a checkout the harness had never touched.

WHY THIS TEST RUNS THE SHIPPED LINE RATHER THAN DESCRIBING IT.  A test that
asserts the string `-B` appears in the script passes on a tree where the flag
has been moved to a different command, and fails on a reflow that changes
nothing.  So the line is LIFTED OUT OF THE SHIPPED FILE and EXECUTED, against a
subject that holds a real copy of `_eda_pin.py` -- which imports nothing but the
standard library, so it is exercised exactly as the harness exercises it.  The
assertion is on the FILESYSTEM afterwards, which is the property that matters.

BOTH DIRECTIONS ARE IN THIS FILE: the same extraction with the guard stripped
must leave the residue, or the test is not measuring what it claims.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[5]
HARNESS = REPO / "tools" / "ci" / "run_suite_in_eda_image.sh"
PIN_SRC = (REPO / "vibe-ic-marketplace" / "plugins" / "vibe-ic" / "programs"
           / "_eda_pin.py")


def _shipped_pin_read() -> str:
    """The `_PIN_PY=` and `_PIN_PARTS=` lines exactly as the harness ships them."""
    text = HARNESS.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines()
             if ln.startswith("_PIN_PY=") or ln.startswith("_PIN_PARTS=")]
    assert len(lines) == 2, (
        "expected exactly one `_PIN_PY=` and one `_PIN_PARTS=` assignment in "
        f"{HARNESS}; found {lines!r}. If the pin read was restructured, this "
        "test must be pointed at whatever replaced it -- not deleted.")
    return "\n".join(lines)


def _subject(tmp_path: Path) -> Path:
    """A minimal subject tree holding the module the pin read imports."""
    progs = tmp_path / "vibe-ic-marketplace" / "plugins" / "vibe-ic" / "programs"
    progs.mkdir(parents=True)
    shutil.copy2(PIN_SRC, progs / "_eda_pin.py")
    return progs


def _run(progs: Path, script_body: str) -> subprocess.CompletedProcess:
    """...with `PYTHONDONTWRITEBYTECODE` CLEARED FROM THE AMBIENT ENVIRONMENT.

    THIS IS LOAD-BEARING, and it is the trap this test fell into first. The
    suite harness gives its own container `-e PYTHONDONTWRITEBYTECODE=1`, so
    every test running under it inherits the variable, and a subprocess started
    from here inherits it too. With it inherited, the UNGUARDED control below
    also left a clean tree -- the guarded assertion would have been passing on
    the ambient environment rather than on the guard it names, and the control
    said so instead of letting that stand.

    Clearing it models the condition the defect actually occurs in: line 166
    runs ON THE HOST, before any container exists, in whatever environment the
    operator has -- which is generally not one that sets this variable.

    `PYTHONPYCACHEPREFIX` IS CLEARED FOR THE SAME REASON, AND IT IS THE ONE
    THAT MAKES THIS TEST POSSIBLE AT ALL. MEASURED: the pinned image ships
    `PYTHONPYCACHEPREFIX=/tmp/pycache`, so INSIDE the container bytecode never
    lands beside its source and this defect is structurally invisible -- the
    unguarded control below left a clean tree until this was cleared. The
    defect is a HOST-side one (line 166 runs before any container exists) and
    the suite runs inside the image, so the host condition has to be
    reconstructed here or the test is measuring the image's redirection rather
    than the harness's guard.

    Both variables are removed rather than set, so this reconstructs an
    ordinary operator environment and does not invent one."""
    env = dict(os.environ)
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    return subprocess.run(
        ["bash", "-c", f'_PIN_DIR="{progs}"\n{script_body}\n'
                       'printf "%s" "$_PIN_PARTS"'],
        capture_output=True, text=True, timeout=300, env=env)


def _pycache(root: Path) -> list:
    return sorted(str(p.relative_to(root)) for p in root.rglob("__pycache__"))


def test_the_shipped_pin_read_leaves_the_subject_clean(tmp_path):
    progs = _subject(tmp_path)
    assert _pycache(tmp_path) == [], "the subject was not clean to begin with"

    r = _run(progs, _shipped_pin_read())

    # The line must actually have RUN -- otherwise a clean tree proves nothing.
    # `|| true` swallows its rc, so the resolved digest is the evidence.
    assert "sha256:" in r.stdout, (
        "the pin read produced no digest, so this test measured nothing about "
        f"bytecode. stdout={r.stdout!r} stderr={r.stderr[-2000:]!r}")
    assert _pycache(tmp_path) == [], (
        "the harness's pin read wrote bytecode INTO THE SUBJECT TREE. "
        "`git status` cannot see it and the attestation drift instrument can, "
        "so the next hygiene run in this checkout reports a residue the "
        "measurement itself created (vibe-ic#2008).")


def test_without_the_guard_the_same_line_does_leave_residue(tmp_path):
    """THE OTHER DIRECTION. A check that cannot fail is not a check: strip the
    guard off the SHIPPED line and the residue must come back. If this test
    ever goes green, the one above has stopped measuring anything -- the
    interpreter stopped writing bytecode for some other reason, and the
    property is no longer being proved by the assertion that claims it."""
    progs = _subject(tmp_path)
    stripped = _shipped_pin_read()
    stripped = stripped.replace("PYTHONDONTWRITEBYTECODE=1 ", "")
    stripped = re.sub(r"python3 -B ", "python3 ", stripped)
    assert "-B" not in stripped and "DONTWRITE" not in stripped, stripped

    r = _run(progs, stripped)
    if "sha256:" not in r.stdout:
        pytest.fail(
            "the stripped control produced no digest, so it is not a control "
            f"for anything. stdout={r.stdout!r} stderr={r.stderr[-2000:]!r}")
    assert _pycache(tmp_path) != [], (
        "the UNGUARDED pin read left no bytecode, so this environment does not "
        "write it at all and the guard above is untested here. Do not read the "
        "test above as a pass until this one fails without the guard.")
