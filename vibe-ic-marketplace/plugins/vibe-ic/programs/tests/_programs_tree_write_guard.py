"""No test may create or write a file inside the repository's `programs/` tree.

MEASURED (FX_559_WRITES_INTO_TREE): `test_issue559_drift_check_rule_b_blindspot`
wrote `programs/brand_new_hand_rolled_check.py` (and `brand_new_semantic_check.py`)
into the tree, ran the ratchet, and unlinked it in `finally`. A second process
snapshotting `git status --porcelain` inside that window saw
`?? .../programs/brand_new_hand_rolled_check.py` every time; under xdist that
is another worker's `suite_write_guard`, which then failed a session whose
tests had all passed (`test_the_victim_passes_after_the_leaking_file`, 1 in
216 on an -n 8 run). A write that is undone before the session ends is still a
write every concurrent reader of the tree can see.

HOW. One audit hook (`sys.addaudithook`, installed once per process) records
every in-process event that can create or modify a path — `open` in a write
mode or with write/create flags, `os.mkdir`, `os.rename`/`os.replace`,
`os.symlink`, `os.link` — whose target resolves under `programs/`. The autouse
fixture names the test it happened in and fails that test at teardown.
`__pycache__` is not the test's doing and is ignored.

WHAT IT CANNOT SEE: a write made by a SUBPROCESS (a gate run by `subprocess`,
a tool in a container). The session-level `suite_write_guard` still compares
porcelain for those; this guard exists because that comparison is blind to a
write that was undone before it looked.
"""
from __future__ import annotations

import os
import sys
from typing import List, Optional

import pytest

_TESTS = os.path.realpath(os.path.dirname(__file__))
_PROGRAMS = os.path.dirname(_TESTS)

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND

#: Paths written under programs/ since the current test started (None = no
#: test is running, e.g. collection; those are not attributed here).
_CURRENT: Optional[List[str]] = None


def _inside_programs(path) -> Optional[str]:
    try:
        if isinstance(path, int):
            return None
        p = os.fsdecode(path)
    except TypeError:
        return None
    real = os.path.realpath(p)
    if not real.startswith(_PROGRAMS + os.sep):
        return None
    if os.sep + "__pycache__" in real or real.endswith((".pyc", ".pyo")):
        return None
    return real


def _is_write_open(mode, flags) -> bool:
    if isinstance(mode, str) and any(c in mode for c in "wax+"):
        return True
    return isinstance(flags, int) and bool(flags & _WRITE_FLAGS)


def _hook(event: str, args) -> None:
    if _CURRENT is None:
        return
    target = None
    if event == "open":
        if len(args) >= 3 and _is_write_open(args[1], args[2]):
            target = args[0]
    elif event in ("os.mkdir", "os.symlink", "os.link"):
        target = args[1] if event in ("os.symlink", "os.link") else args[0]
        if event == "os.mkdir" and os.path.lexists(target):
            # Path.mkdir(parents=True, exist_ok=True) audits the attempted
            # mkdir even when the directory already exists. No path changed.
            return
    elif event in ("os.rename", "os.replace"):
        target = args[1]
    else:
        return
    hit = _inside_programs(target) if target is not None else None
    if hit is not None:
        _CURRENT.append(f"{event} {os.path.relpath(hit, _PROGRAMS)}")


_INSTALLED = False


def _install() -> None:
    global _INSTALLED
    if not _INSTALLED:
        sys.addaudithook(_hook)
        _INSTALLED = True


@pytest.fixture(autouse=True)
def _no_write_into_the_programs_tree():
    """Collect writes for the call-phase hook to report as a plain FAIL."""
    global _CURRENT
    _install()
    _CURRENT = []
    try:
        yield
    finally:
        _CURRENT = None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Record transient writes as a call-phase FAIL, never a teardown ERROR."""
    outcome = yield
    report = outcome.get_result()
    if report.when != "call":
        return
    hits = _CURRENT or []
    if not hits:
        return
    message = (
        f"{item.nodeid} wrote into the repository's programs/ tree "
        f"({len(hits)}): {sorted(set(hits))[:8]}. Nothing that reads this "
        f"tree may write to it, even briefly: a concurrent reader (another "
        f"xdist worker's write guard, a census) sees the path. Build it "
        f"under tmp_path and point the code at it through its own seam.")
    if report.failed:
        report.sections.append(("programs tree write", message))
    else:
        report.outcome = "failed"
        report.longrepr = message
