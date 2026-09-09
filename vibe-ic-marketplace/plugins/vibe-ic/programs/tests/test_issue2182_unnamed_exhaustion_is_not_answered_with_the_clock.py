#!/usr/bin/env python3
"""test_issue2182_unnamed_exhaustion_is_not_answered_with_the_clock.py

#2182. `lec_equivalence_check` refuses to DEFAULT an unrecorded exhausted
resource to wall-clock -- the comment on `_EXHAUSTED_RESOURCE_KEYS` says so in
its own words: "Never defaulted: an exhaustion whose resource was not recorded
is reported as unnamed, not as wall-clock."  The LABEL honoured that.  The
REMEDY did not: `"unnamed"` fell through the remedy dispatch's `else` branch and
was answered with "Re-run the step with a budget it can finish inside" -- the
clock, one sentence after refusing to name the clock.  A remedy aimed at the
wrong resource is the same defect as the wrong label, which is the rule the same
dispatch already states for `memory_bytes` and `not_measured`.

MEASURED, lane cysha108, 8HD-6, live main v1.20.18, image
ghcr.io/vibeic/vibeic-eda@sha256:89a8fd7295208ee6d06e216ade9edc6161d26db52099e9f22ceb77a2d76e3f49
(content id da2314d4100c246a1a6655a4e5f7eca816232f18ac20bcfa132e6e46cf6112f2).
sha256 step 13, RTL vs post_dft_netlist.v in functional scan mode, run to its
natural end with no `timeout`, no kill and no deadline:

    verdict                        INCONCLUSIVE
    compared_points / miter_points 1314 / 1612
    unproven_points                298
    non_equivalent_points          0
    step_budget_sec                7200
    elapsed_sec                    13050.16
    step_budget_exhausted          True
    step_budget_stopped_this_proof False      <- no budget stopped this proof
    exhausted_resource             None       <- producer named none
    induction_wall_kind            "induction_depth"
    non_convergence                True

The consumer then printed `LEC_BUDGET_EXHAUSTED (unnamed)` and advised "re-run
with a budget it can finish inside".  Both halves are wrong about the same run:
the ladder ran to its END and did not converge, so no budget stopped it and a
larger one cannot change the outcome -- and the record already NAMES what the
proof ran into, `induction_wall_kind: induction_depth`.

NOT relaxed by this test or its fix: the state stays NOT_MEASURED, stays
non-waiver-eligible, `passed` stays False, and the budget stays a RECORDING
ceiling.  Only the sentence that tells a reader WHAT TO REPAIR changes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import lec_equivalence_check as L  # noqa: E402

# The cysha108 sha256 step-13 record, reduced to the fields this state is made
# of.  `exhausted_resource` is absent because the producer recorded it null.
_SHA256_V12018 = {
    "equivalent": False,
    "compared_points": 1314,
    "miter_points": 1612,
    "non_equivalent_points": 0,
    "unproven_points": 298,
    "verdict": "INCONCLUSIVE",
    "inconclusive": True,
    "non_convergence": True,
    "induction_wall_kind": "induction_depth",
    "program": "lec_run",
    "elapsed_sec": 13050.16,
    "step_elapsed_sec": 13050.46,
    "lec_attempts": 4,
    "step_budget_sec": 7200,
    "step_budget_exhausted": True,
    "step_budget_stopped_this_proof": False,
    "tool": "yosys equiv_make+equiv_simple+equiv_induct",
}

_CLOCK_REMEDY = "Re-run the step with a budget it can finish inside"


def _project(tmp_path: Path, **over) -> Path:
    doc = dict(_SHA256_V12018)
    doc.update(over)
    (tmp_path / "reports").mkdir(parents=True, exist_ok=True)
    (tmp_path / "reports" / "lec.json").write_text(json.dumps(doc))
    (tmp_path / "reports" / "lec.rpt").write_text(
        "Found 1612 $equiv cells in equiv:\n"
        "  Of those cells 1314 are proven and 298 are unproven.\n")
    return tmp_path


def _finding(res):
    for f in res.findings:
        if f.rule == "LEC_BUDGET_EXHAUSTED":
            return f
    raise AssertionError("LEC_BUDGET_EXHAUSTED was not reported at all")


def test_an_unnamed_resource_is_not_answered_with_the_clock(tmp_path):
    """The whole defect, in one assertion: the resource is refused a default,
    so the remedy must be refused one too."""
    res = L.audit(_project(tmp_path))
    assert res.exhausted_resource == "unnamed", res.exhausted_resource
    msg = _finding(res).message
    assert _CLOCK_REMEDY not in msg, (
        "an exhaustion whose resource the producer never named was answered "
        "with the wall-clock remedy — the same file refuses to DEFAULT the "
        "resource to the clock and then prescribes the clock anyway")
    assert "do not assume the clock" in msg, (
        "an unnamed resource must get the same discipline as an unmeasured "
        "one: establish the cause before naming a remedy")


def test_the_remedy_says_no_budget_stopped_this_proof(tmp_path):
    """`step_budget_stopped_this_proof: False` is a measurement the record
    already carries; a remedy that ignores it advises a change that provably
    cannot alter the outcome."""
    msg = _finding(L.audit(_project(tmp_path))).message
    assert "step_budget_stopped_this_proof" in msg, (
        "the record says no budget stopped this proof and the remedy never "
        "tells the reader, so 'raise the budget' reads as actionable")


def test_the_remedy_names_the_wall_the_producer_measured(tmp_path):
    """Item 3 of #2182 — say what makes it unprovable. The producer measured
    `induction_wall_kind`; the consumer must hand that to the reader instead of
    a ceiling the run never hit."""
    msg = _finding(L.audit(_project(tmp_path))).message
    assert "induction_depth" in msg, (
        "the producer recorded induction_wall_kind and the consumer dropped "
        "it, leaving the reader with a budget to raise and no defect to fix")


def test_a_named_wall_clock_exhaustion_still_gets_the_budget_remedy(tmp_path):
    """NO-LEAK, direction 1. A run the budget REALLY stopped, with the resource
    named, keeps the remedy it had. Nothing about the clock case changes."""
    res = L.audit(_project(tmp_path,
                           exhausted_resource="wall_clock_seconds",
                           step_budget_stopped_this_proof=True))
    assert res.exhausted_resource == "wall_clock_seconds"
    assert _CLOCK_REMEDY in _finding(res).message, (
        "the genuine wall-clock case lost its remedy — the repair was supposed "
        "to touch only the UNNAMED branch")


def test_the_state_is_still_not_measured_and_still_non_waivable(tmp_path):
    """NO-LEAK, direction 2. The fix changes one sentence. It must not soften
    the verdict, promote the step, or make it waiver-eligible."""
    res = L.audit(_project(tmp_path))
    assert res.not_measured is True
    assert res.inconclusive is False
    assert res.passed is False
    assert _finding(res).severity == "ERROR"
    assert "NOT eligible for a waiver row" in _finding(res).message


def test_a_memory_exhaustion_is_untouched(tmp_path):
    """NO-LEAK, direction 3. The `memory_bytes` branch that #2182 added earlier
    still fires and still refuses the clock."""
    res = L.audit(_project(tmp_path, exhausted_resource="memory_bytes"))
    msg = _finding(res).message
    assert "The resource that ran out is MEMORY" in msg
    assert _CLOCK_REMEDY not in msg
