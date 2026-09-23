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
    assert SWL.mark_run_start(proj) is True
    _write_output(proj)
    win, entry = _produced_window(_ledger(proj))
    assert win is True, (win, entry)


# ---------------------------------------------------------------- NEGATIVE

def test_an_output_older_than_the_mark_is_not_this_runs(tmp_path):
    """The reviewer's real case, in miniature: an artefact twelve days older
    than the run that is now auditing it."""
    proj = _project(tmp_path)
    _write_output(proj, age_days=12)
    assert SWL.mark_run_start(proj) is True
    win, entry = _produced_window(_ledger(proj))
    assert win is False, (win, entry)


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
    SWL.mark_run_start(proj)
    _write_output(proj)
    _win, entry = _produced_window(_ledger(proj))
    assert entry is not None, "no produced entry at all"
    assert "in_run_window" in entry, sorted(entry)
