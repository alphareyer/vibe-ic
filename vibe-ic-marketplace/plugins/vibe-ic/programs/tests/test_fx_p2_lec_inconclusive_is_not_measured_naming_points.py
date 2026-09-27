#!/usr/bin/env python3
"""FX_P2 (2) — an LEC that ran, ran out of nothing, found no counterexample and
left points unproven is NOT_MEASURED(inconclusive), naming the points and the
engine limit. Never PASS. A recorded counterexample is still FAIL.

MEASURED on subservient (reused serv 1.4.0, 8HD-4, 2026-09-28): step 13 FAILed
"251 of 256 point(s) proven, 5 unproven", and phase 2 halted. The five are
`core.rf_mem_if.o_sram_wdata[0..4]`. The ladder climbed equiv_simple ->
equiv_induct -seq 4 (251) -> -seq 16 (+0) and stopped on no progress. Tried
beyond it, about 15 min in the image: `equiv_induct -seq 64`, `-undef -seq 16`,
`equiv_simple -seq 8/20` and `-undef -seq 20` each proved 0; a port miter with
`sat -tempinduct` to depth 24 found no mismatch on any output from reset but
could not close the induction; `sat -seq 96` timed out. No counterexample
anywhere. The owner's ruling for this case: NOT_MEASURED naming the points and
the engine limit, never PASS.

The record below is subservient's own `reports/lec.json`, in the producer's
field names. chip-AGNOSTIC: the point names are data, not program logic.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import design_one_shot_runner as D  # noqa: E402

STOP = ("rung equiv_induct_seq16 proved nothing — 251 proven at entry, 5 "
        "still unproven at exit")
CELLS = ["\\core.o_sram_wdata[2]", "\\core.o_sram_wdata[0]",
         "\\core.rf_mem_if.o_sram_wdata[3]", "\\core.rf_mem_if.o_sram_wdata[1]",
         "\\core.o_sram_wdata[4]"]
SUBSERVIENT = {
    "verdict": "INCONCLUSIVE", "equivalent": None,
    "compared_points": 251, "unproven_points": 5, "miter_points": 256,
    "non_equivalent_points": 0, "budget_exhausted": False,
    "exhausted_resource": None, "progress_stalled": False,
    "unproven_cells": CELLS, "induction_wall_kind": "induction_depth",
    "lec_ladder": {"rungs": ["equiv_simple_short", "equiv_simple_full",
                             "equiv_induct_seq4", "equiv_induct_seq16",
                             "equiv_induct_seq64"],
                   "stopped_because": STOP, "stopped_on_no_progress": True},
}


def test_an_unclosed_ladder_is_not_measured_inconclusive():
    status, reason = D.lec_inconclusive_disposition(SUBSERVIENT)
    assert status == "NOT_MEASURED", (status, reason)
    assert D.lec_inconclusive_reason_class(SUBSERVIENT) == "inconclusive"
    assert "never a PASS" in reason


def test_the_reason_names_every_unproven_point_and_the_engine_limit():
    _status, reason = D.lec_inconclusive_disposition(SUBSERVIENT)
    for cell in CELLS:
        assert cell.lstrip("\\") in reason, cell
    assert STOP in reason
    assert "251 of 256" in reason and "5 unproven" in reason


def test_a_recorded_counterexample_is_still_FAIL():
    """TEETH: a non-equivalent point is a measurement, and it stays red."""
    doc = dict(SUBSERVIENT, non_equivalent_points=1)
    status, _reason = D.lec_inconclusive_disposition(doc)
    assert status == "FAIL"
    assert D.lec_inconclusive_reason_class(doc) == ""


def test_a_cut_off_proof_keeps_its_own_reason():
    """TEETH: budget exhaustion is still `budget_exhausted`, decided first."""
    doc = dict(SUBSERVIENT, budget_exhausted=True)
    status, reason = D.lec_inconclusive_disposition(doc)
    assert status == "NOT_MEASURED" and "stopped before it finished" in reason
    assert D.lec_inconclusive_reason_class(doc) == "budget_exhausted"
