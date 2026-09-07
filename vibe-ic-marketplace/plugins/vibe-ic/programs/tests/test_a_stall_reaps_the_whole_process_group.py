#!/usr/bin/env python3
"""`_progress_run.run(start_new_session=True)` reaps the GROUP — and without it, it does not.

WHY THIS FILE EXISTS, AND WHY IT IS THE SECOND ARM THAT MATTERS.
`_progress_run.run` injects its own `popen_factory` (it has to: `cwd`, `shell` and the
stdin file are its own). An injected factory is exactly what `_watchdog.run_supervised`
does NOT decorate with `start_new_session=True`, and `_watchdog._default_kill` signals a
process GROUP only when the child is its own group LEADER — it asks `os.getpgid(pid) ==
pid` before it signals, because killing the group of a child that is NOT a leader would
kill the supervisor. So for every caller of this primitive the group reap was quietly
absent, and the three sites converted under ruling R4 that had hand-written group kills
would have started orphaning the moment they were converted.

`start_new_session=` is the repair, and it is OFF by default so no existing call site
changed. The claim in the primitive's docstring is "measured both ways in
`test_a_stall_reaps_the_whole_process_group.py`" — this file — and until it existed that
citation pointed at nothing. A docstring that cites evidence which does not exist is worse
than one that cites none: it spends a reader's trust without earning it.

THE SUBJECT IS A REAL TREE, not a fake: `bash` backgrounds a `sleep 3000` and waits. The
whole tree is motionless on every signal the supervisor reads — no output, no CPU, no
block I/O — so the stall is what stops it, at a tightened CADENCE and never a tightened
predicate.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_TESTS.parent))
import _progress_run as PR  # noqa: E402


def _drive(tmp_path: Path, *, new_session: bool):
    """Run a silent parent that backgrounds a long-lived child. Returns its pid."""
    pidfile = tmp_path / f"grandchild_{new_session}.pid"
    argv = ["bash", "-lc", f"sleep 3000 & echo $! > {pidfile}; wait"]
    try:
        PR.run(argv, stall_looks=4, poll_s=0.25,
               start_new_session=new_session)
    except PR.Stalled:
        pass
    else:                                          # pragma: no cover - the
        raise AssertionError(                      # child cannot exit on its own
            "the motionless child was not reported as a stall at all")
    for _ in range(40):                            # LOOKS, never a deadline
        if pidfile.exists() and pidfile.read_text().strip():
            break
        time.sleep(0.1)
    return int(pidfile.read_text().strip())


def _alive(pid: int) -> bool:
    for _ in range(25):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        time.sleep(0.2)
    return True


def test_with_start_new_session_the_stall_takes_the_whole_group(tmp_path):
    """THE ACCEPT ARM. The parent is stopped and its backgrounded child goes with
    it, because the child tree is its own process group and the supervisor
    signals the group."""
    pid = _drive(tmp_path, new_session=True)
    assert not _alive(pid), (
        f"grandchild {pid} outlived the stall even with start_new_session=True — "
        f"the group reap is not reaching it")


def test_without_it_the_grandchild_SURVIVES_which_is_why_the_argument_exists(
        tmp_path):
    """THE ARM THAT JUSTIFIES THE PARAMETER, and the one that was only ever
    asserted before this file existed.

    Same command, same cadence, ONE difference: the child is not made a group
    leader, so `_default_kill`'s `pgid == pid` guard refuses to signal the group
    and only the `bash` is killed. The `sleep` is reparented to init and lives on.
    If this test ever goes GREEN — the orphan dying anyway — then the supervisor
    reaps trees without help and `start_new_session=` can be deleted, which is
    the honest way for this argument to expire.

    THE ORPHAN IS THIS TEST'S OWN AND IT CLEANS IT UP. `sleep 3000` outliving the
    session would be a live descendant of the pytest run, which the per-file
    driver reports as an unfinished session — a real red for every file behind it.
    """
    pid = _drive(tmp_path, new_session=False)
    survived = _alive(pid)
    if survived:                                   # our own planted child
        try:
            os.kill(pid, 9)
        except ProcessLookupError:                 # pragma: no cover
            pass
    assert survived, (
        f"grandchild {pid} died without start_new_session=True. That is BETTER "
        f"than the documented behaviour, not worse — but the primitive's "
        f"docstring and the three R4 conversions all rest on this arm failing, "
        f"so re-read them before deleting the argument")
