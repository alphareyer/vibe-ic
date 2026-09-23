#!/usr/bin/env python3
""""Did THIS run write it" must be a recorded fact, through the REAL chain.

ROUND-6 (lane icspm5, 2026-09-23). `flow_compliance_check` demotes a
declared-N/A clause only when the step produced every declared output, and
round 5 made that mean "produced by THIS RUN" by requiring the ledger's
`in_run_window`. The review then measured what the producer actually emits:

    929 ledgers on this host, 368 with a known t0
    -> ZERO `produced` entries carry `in_run_window`

`step_write_ledger.build()` computed the flag per ENTRY and then copied a
subset of fields into each `produced` dict, without it. So the field the
decision reads was never written, the demotion never fired on a real run, and
the round-5 tests were green only because their fixtures hand-wrote it.

And the window itself was inferred: with no marker, `resolve_run_window`
takes the EARLIEST surviving orchestrator summary, and an `--entry-step` run
into phase 2 does not rewrite the phase-1 summary. On
`campaign_v1574/spm/converge_1.5.74_sky130A` step 7's outputs are twelve days
older than the latest run and would still read in-window.

`mark_run_start` existed for exactly this and had no production caller.

EVERY ASSERTION HERE GOES THROUGH THE REAL WRITERS: the run is marked the way
a runner marks it, a producer writes a real file, `step_write_ledger.build()`
emits the ledger, and `check_step` decides. Nothing hand-writes a ledger
field -- that is the whole point, because hand-writing it is what hid this.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import step_write_ledger as SWL  # noqa: E402


def _project(tmp_path):
    d = tmp_path / "phase2" / "stage2" / "constraints"
    d.mkdir(parents=True, exist_ok=True)
    return tmp_path


def _write_output(tmp_path, age_days=0.0):
    p = tmp_path / "phase2" / "stage2" / "constraints" / "spm.sdc"
    p.write_text("create_clock -period 10 [get_ports clk]\n")
    if age_days:
        old = time.time() - age_days * 86400
        os.utime(p, (old, old))
    return p


def _ledger(tmp_path):
    """The REAL builder, over the real tree, reading the real flow."""
    return SWL.build(tmp_path)


def _produced_window(led, spec_tail="constraints"):
    """The window the ledger recorded for our output, wherever it lands.

    `build()` reads the shipped flow, so the entry we planted appears under
    whichever declared step owns `phase2/stage2/constraints/*.sdc`. We look it
    up by the artefact rather than by a step id, so the test does not pin a
    step number the flow is free to renumber.
    """
    for row in (led.get("steps") or []):
        for e in (row.get("produced") or []):
            if spec_tail in str(e.get("rel", "")):
                return e.get("in_run_window"), e
    return "NO-PRODUCED-ENTRY", None


# ---------------------------------------------------------------- POSITIVE

def test_a_marked_run_records_that_it_wrote_its_own_output(tmp_path):
    """Runner marks -> producer writes -> ledger emits `in_run_window: True`."""
    proj = _project(tmp_path)
    os.environ.pop(SWL.RUN_ID_ENV, None)
    try:
        assert SWL.begin_run(proj)          # the call a RUNNER makes
        _write_output(proj)
        win, entry = _produced_window(_ledger(proj))
        assert win is True, (win, entry)
    finally:
        os.environ.pop(SWL.RUN_ID_ENV, None)


# ---------------------------------------------------------------- NEGATIVE

def test_an_output_older_than_the_mark_is_not_this_runs(tmp_path):
    """The reviewer's real case, in miniature: an artefact twelve days older
    than the run that is now auditing it."""
    proj = _project(tmp_path)
    _write_output(proj, age_days=12)
    os.environ.pop(SWL.RUN_ID_ENV, None)
    try:
        assert SWL.begin_run(proj)
        win, entry = _produced_window(_ledger(proj))
        assert win is False, (win, entry)
    finally:
        os.environ.pop(SWL.RUN_ID_ENV, None)


def test_an_unmarked_run_says_UNKNOWN_rather_than_guessing(tmp_path):
    """No mark -> no claim. The derived window (earliest surviving summary)
    cannot answer this question, so the per-entry field is None and the
    consumer must not demote."""
    proj = _project(tmp_path)
    (proj / "reports").mkdir(exist_ok=True)
    # An orchestrator summary old enough that the DERIVED t0 would wrongly
    # admit a twelve-day-old artefact -- exactly the --entry-step shape.
    summ = proj / "reports" / "phase1_one_shot.json"
    summ.write_text(json.dumps({"duration_s": 60}))
    old = time.time() - 20 * 86400
    os.utime(summ, (old, old))
    _write_output(proj, age_days=12)
    win, entry = _produced_window(_ledger(proj))
    assert win is None, (
        "a derived window answered a question only a mark can answer: "
        f"{win!r} {entry!r}")


def test_the_field_reaches_the_produced_entry_at_all(tmp_path):
    """THE ROUND-6 HIGH, stated directly: the decision reads `produced[].
    in_run_window`, so it must BE there. 929 real ledgers carried none."""
    proj = _project(tmp_path)
    os.environ.pop(SWL.RUN_ID_ENV, None)
    SWL.begin_run(proj)
    _write_output(proj)
    _win, entry = _produced_window(_ledger(proj))
    os.environ.pop(SWL.RUN_ID_ENV, None)
    assert entry is not None, "no produced entry at all"
    assert "in_run_window" in entry, sorted(entry)


# ===========================================================================
# ROUND-7 — A RUN IS AN IDENTITY, NOT A TIMESTAMP.
#
# Marking on a timestamp alone was wrong in three reachable ways:
#   H1 every runner re-marked unconditionally, and the front door launches
#      phase1 / design / phase3 as SUBPROCESSES -- so the surviving t0 was
#      PHASE 3's start and every phase-1/2 output of the SAME RUN (step-4/5
#      sim, coverage, formal) read out-of-window in the final audit;
#   H2 the mark was written BEFORE is_dir(), the single-driver lock and the
#      admission refusals: `mkdir(parents=True)` CREATED a missing project,
#      and a run about to be refused had already overwritten the LIVE run's
#      t0;
#   M  a standalone analog rebuild reused whatever marker was last written.
# ===========================================================================

def _clear_run_id():
    os.environ.pop(SWL.RUN_ID_ENV, None)


def test_a_nested_runner_inherits_and_never_re_marks(tmp_path):
    """H1. The child must not move the window its parent established."""
    proj = _project(tmp_path)
    _clear_run_id()
    try:
        parent = SWL.begin_run(proj)                 # the front door
        assert parent
        t0_parent = json.loads(
            (proj / "steps" / ".write_ledger_t0.json").read_text())["t0_epoch"]
        time.sleep(0.05)
        child = SWL.begin_run(proj)                  # phase3, as a subprocess
        assert child == parent, (child, parent)
        t0_after = json.loads(
            (proj / "steps" / ".write_ledger_t0.json").read_text())["t0_epoch"]
        assert t0_after == t0_parent, "a nested runner moved the run window"
    finally:
        _clear_run_id()


def test_an_output_written_before_a_later_phase_is_still_this_runs(tmp_path):
    """H1, at the level that matters: a phase-1 output written EARLY in the
    run must still read in-window when the final audit asks, after phase 3
    has started."""
    proj = _project(tmp_path)
    _clear_run_id()
    try:
        SWL.begin_run(proj)                          # front door marks t0
        _write_output(proj)                          # phase-1/2 writes early
        time.sleep(0.05)
        SWL.begin_run(proj)                          # phase 3 starts (inherits)
        win, entry = _produced_window(_ledger(proj))
        assert win is True, (win, entry)
    finally:
        _clear_run_id()


def test_marking_never_creates_the_project(tmp_path):
    """H2. `mkdir(parents=True)` conjured a missing project, which destroyed
    the runners' own rc=2 "not a directory" refusal and left a stray tree on
    a typo."""
    missing = tmp_path / "no_such_project"
    _clear_run_id()
    try:
        assert SWL.mark_run_start(missing, "rid") is False
        assert not missing.exists(), "the marker created the project"
        assert SWL.begin_run(missing) is None
        assert not missing.exists()
        assert SWL.current_run_id() is None, (
            "a run id was exported for a project that does not exist")
    finally:
        _clear_run_id()


def test_a_standalone_rebuild_does_not_inherit_a_stale_marker(tmp_path):
    """M. A later, unrelated invocation (analog rebuild, a re-audit) finds the
    marker of a run it does not belong to. A real timestamp is still not an
    answer to "did THIS run write it"."""
    proj = _project(tmp_path)
    _clear_run_id()
    try:
        SWL.begin_run(proj)
        _write_output(proj)
        assert _produced_window(_ledger(proj))[0] is True
    finally:
        _clear_run_id()
    # ...and now a process that belongs to no run reads the same tree.
    win, entry = _produced_window(_ledger(proj))
    assert win is None, (
        "a marker from another run was read as this one's: %r %r" % (win, entry))
