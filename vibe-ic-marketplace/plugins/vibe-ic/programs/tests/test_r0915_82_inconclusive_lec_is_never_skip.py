#!/usr/bin/env python3
"""R-0915-82 — an INCONCLUSIVE LEC record is not one state, and none of its
states is SKIP.

MEASURED, sha256 run16 (main c0dcb5e27, image 89a8fd72…, sky130A, the
R-0915-67 carry-save RTL). `reports/lec.json` said:

    verdict INCONCLUSIVE | compared_points 846 | unproven_points 481
    non_equivalent_points 0 | budget_exhausted false | exhausted_resource null
    progress_stalled false | induction_wall_kind "induction_depth"

i.e. the ladder ran to its LAST rung (`equiv_induct -seq 64`, the top of
`lec_run.LEC_LADDER`) in 8306 s of a 28800 s budget and did not close. The step
booked that SKIP. Then a re-entered run read the SAME kept record in 2 s,
printed the SAME sentence, booked the SAME SKIP, phase 2 reported
PASS_WITH_WAIVERS, and phase 3 measured DRC/LVS/STA sign-off on a netlist whose
equivalence to the RTL nobody had proven.

Two defects, pinned here:
  (1) INCONCLUSIVE -> SKIP was UNCONDITIONAL, on a docstring premise ("0 points
      compared") that the record itself contradicted.
  (2) a reused record was indistinguishable from a fresh proof in the step's
      own sentence, although `lec_run` records the reuse in full.

These tests drive the PURE functions with dicts. They are behaviour tests, not
text-presence tests: each asserts the STATUS a record earns, and the three
INCONCLUSIVE sentences are required to DIFFER from one another — a naming that
collapses is a naming that says nothing.
"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import design_one_shot_runner as dosr  # noqa: E402


#: The record sha256 run16 actually produced, field for field.
RUN16_UNCLOSED = {
    "verdict": "INCONCLUSIVE",
    "equivalent": False,
    "compared_points": 846,
    "unproven_points": 481,
    "non_equivalent_points": 0,
    "budget_exhausted": False,
    "exhausted_resource": None,
    "progress_stalled": False,
    "step_budget_exhausted": False,
    "step_budget_stopped_this_proof": False,
    "induction_wall_kind": "induction_depth",
    "execution_mode": "fresh-yosys-proof",
}

#: The state R-0915-48's backstop produces: the proof was CUT OFF.
BUDGET_KILLED = dict(RUN16_UNCLOSED, budget_exhausted=True)

#: The state the old docstring described: the miter judged nothing.
NOTHING_COMPARED = {
    "verdict": "INCONCLUSIVE", "equivalent": False,
    "compared_points": 0, "unproven_points": 0,
    "budget_exhausted": False, "exhausted_resource": None,
}

#: What `lec_run` wrote for run16 pass 2: a 2 s process that climbed no rung.
RESUMED = dict(
    RUN16_UNCLOSED,
    elapsed_sec=1.05,
    lec_resume={"resumed": True,
                "resumed_from": {"rung": "equiv_induct_seq64"},
                "rungs_recorded_this_run": [],
                "state": "COMPLETE"},
    lec_ladder={"legs": [{"rungs_in_this_process": 0,
                          "resumed_from_rung": "equiv_induct_seq64"}]},
)


def _status_for(doc) -> str:
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "lec.json"
        f.write_text(doc if isinstance(doc, str) else json.dumps(doc))
        return dosr.lec_step_status_from_report(f)[0]


# ---------------------------------------------------------------------------
# (1) the three INCONCLUSIVE states, and none of them is SKIP
#
# EVERY assertion below is a BARE `assert` on purpose. pytest rewrites those and
# puts the COMPARED VALUES into the failure text, so a control run over the
# pre-fix tree reports `assert 'SKIP' == 'FAIL'` -- a value that was observed --
# rather than a bespoke message a reader (or `control_substance_check`) cannot
# tell apart from a symbol that simply was not there. unittest's assertEqual
# does not get rewritten, and that is exactly the difference between evidence
# and a claim about evidence.
# ---------------------------------------------------------------------------
def test_a_completed_ladder_that_did_not_close_FAILS():
    """THE REGRESSION. Points were compared, the ladder finished, nothing ran
    out -- the equivalence question is OPEN and the flow may not walk past it.
    Reverting to `INCONCLUSIVE -> SKIP` fails here."""
    status, reason = dosr.lec_inconclusive_disposition(RUN16_UNCLOSED)
    assert status == "FAIL"
    assert "846" in reason and "481" in reason


def test_the_step_mapper_agrees_with_the_disposition():
    """The mapper must DELEGATE, not keep its own opinion."""
    assert _status_for(RUN16_UNCLOSED) == "FAIL"


def test_a_proof_that_was_cut_off_is_NOT_a_fail():
    """R-0915-5 / R-0915-48: when the backstop fires, nothing is known.
    Asserting a non-equivalence nobody measured is #192 inverted, and it would
    also break R-48's contract that phase 3 proceeds. Deleting the exhaustion
    check fails here."""
    status, reason = dosr.lec_inconclusive_disposition(BUDGET_KILLED)
    assert status == dosr.NOT_EXECUTED_STATUS
    assert "NOT_MEASURED" in reason


@pytest.mark.parametrize("route, doc", [
    ("step_budget_exhausted",
     dict(RUN16_UNCLOSED, step_budget_exhausted=True)),
    ("step_budget_stopped_this_proof",
     dict(RUN16_UNCLOSED, step_budget_stopped_this_proof=True)),
    ("progress_stalled", dict(RUN16_UNCLOSED, progress_stalled=True)),
    ("exhausted_resource",
     dict(RUN16_UNCLOSED, exhausted_resource="wall_clock")),
    ("bounded_rung_policy",
     dict(RUN16_UNCLOSED, bounded_rung_policy={"limit_reached": True})),
    ("killed_by_budget",
     dict(RUN16_UNCLOSED,
          lec_attempts_detail=[{"killed_by_budget": True}])),
])
def test_every_recorded_exhaustion_route_is_honoured(route, doc):
    """Each is a different way for `lec_run` to say "I was stopped". A fix that
    only knows about `budget_exhausted` fails here."""
    assert dosr.lec_inconclusive_disposition(doc)[0] == dosr.NOT_EXECUTED_STATUS


def test_zero_compared_points_is_not_measured_not_a_failure():
    """The state the OLD docstring described, kept intact in substance: never a
    vacuous PASS, never a cascading FAIL. Only its NAME changed, from an
    undifferentiated SKIP to NOT_MEASURED with the reason."""
    status, reason = dosr.lec_inconclusive_disposition(NOTHING_COMPARED)
    assert status == dosr.NOT_EXECUTED_STATUS
    assert status not in ("PASS", "FAIL")
    assert "0 point" in reason


def test_the_three_reasons_are_three_different_sentences():
    """A naming that collapses says nothing. Any implementation that emits one
    sentence for all three fails here."""
    reasons = [dosr.lec_inconclusive_disposition(d)[1]
               for d in (RUN16_UNCLOSED, BUDGET_KILLED, NOTHING_COMPARED)]
    assert len(set(reasons)) == 3


def test_exhaustion_is_decided_BEFORE_the_point_count():
    """A proof cut off before comparing anything must read as cut off, not as
    "the miter judged nothing" -- the two have different remedies."""
    killed_early = dict(NOTHING_COMPARED, budget_exhausted=True)
    assert "stopped before it finished" in \
        dosr.lec_inconclusive_disposition(killed_early)[1]


def test_PASS_is_untouched():
    assert _status_for({"verdict": "PASS", "equivalent": True,
                        "compared_points": 65}) == "PASS"


def test_FAIL_is_untouched():
    assert _status_for({"verdict": "FAIL", "equivalent": False,
                        "compared_points": 66,
                        "unproven_points": 2}) == "FAIL"


def test_SKIPPED_CONDITION_is_untouched():
    assert _status_for({"verdict": "SKIPPED-CONDITION",
                        "equivalent": False}) == "SKIP"


def test_an_unreadable_report_is_untouched():
    assert _status_for("{ not json") == "SKIP"


# ---------------------------------------------------------------------------
# (2) a verdict earned by an earlier run must say whose work it was
# ---------------------------------------------------------------------------
def test_a_resumed_record_that_climbed_no_rung_is_named_reused():
    note = dosr.lec_record_reuse_note(RESUMED)
    assert "REUSED" in note
    assert "equiv_induct_seq64" in note


def test_a_reused_record_still_earns_the_same_status():
    """Reuse is a disclosure, never a discount. The 2 s re-read of run16's
    record must FAIL exactly as the 8306 s proof did."""
    assert _status_for(RESUMED) == "FAIL"


def test_a_pass_cache_hit_is_named_reused():
    note = dosr.lec_record_reuse_note(
        {"verdict": "PASS", "execution_mode": "exact-pass-cache-hit"})
    assert "REUSED" in note


def test_a_fresh_proof_is_NOT_called_reused():
    """THE CONTROL. A note that is always non-empty measures nothing. run16
    pass 1 really did climb five rungs."""
    fresh = dict(
        RUN16_UNCLOSED,
        lec_resume={"resumed": False, "rungs_recorded_this_run":
                    ["equiv_simple_short", "equiv_induct_seq64"]},
        lec_ladder={"legs": [{"rungs_in_this_process": 5}]})
    assert dosr.lec_record_reuse_note(fresh) == ""


def test_a_run_that_never_RESUMED_is_not_called_reused():
    """The `resumed` flag has to be load-bearing ON ITS OWN. Without this case
    a mutation that drops the resume guard SURVIVES, because the realistic
    fresh record is rejected by the OTHER condition and the guard is never
    tested alone. Measured: the first version of this suite passed that
    mutant."""
    never_resumed = dict(
        RUN16_UNCLOSED,
        lec_resume={"resumed": False, "rungs_recorded_this_run": []},
        lec_ladder={"legs": [{"rungs_in_this_process": 0}]})
    assert dosr.lec_record_reuse_note(never_resumed) == ""


def test_a_record_with_no_resume_block_at_all_is_not_called_reused():
    """A producer that never wrote `lec_resume` says nothing about reuse;
    absence is not evidence of it."""
    assert dosr.lec_record_reuse_note(RUN16_UNCLOSED) == ""


def test_a_resume_that_DID_climb_a_rung_is_not_called_reused():
    """Resuming and then doing real work is not reuse -- the honest boundary is
    "how many rungs did THIS invocation climb"."""
    worked = dict(
        RUN16_UNCLOSED,
        lec_resume={"resumed": True,
                    "resumed_from": {"rung": "equiv_induct_seq16"},
                    "rungs_recorded_this_run": ["equiv_induct_seq64"]},
        lec_ladder={"legs": [{"rungs_in_this_process": 1}]})
    assert dosr.lec_record_reuse_note(worked) == ""


# ---------------------------------------------------------------------------
# (3) SECOND ORDER -- phase 3 must be able to grade the row this change emits
#
# `run_step11_dft_after_synth` republishes `step_dft_lec_chain`'s rows VERBATIM
# as `step11_<name>`. So the moment the LEC step can answer NOT_EXECUTED, that
# word reaches `_aggregate_verdict`, whose known set is `union(_TIERS.values())`
# and which REFUSES BY NAME anything outside it (vibe-ic#2153). Unclassified, a
# correction to ONE step would have turned every budget-stopped run's whole
# phase-3 verdict into `UNKNOWN_STATUS:` -- a fix acquiring a second defect one
# file away.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def p3():
    return pytest.importorskip("phase3_one_shot_runner")


def test_NOT_EXECUTED_is_inside_the_known_vocabulary(p3):
    plan = [p3.StepResult("pnr", "PASS"),
            p3.StepResult("step11_lec_equivalence", dosr.NOT_EXECUTED_STATUS)]
    verdict = p3._aggregate_verdict(plan)
    assert not str(verdict).startswith("UNKNOWN_STATUS:")


def test_a_stopped_proof_does_not_block_phase3(p3):
    """R-0915-48 word for word: the backstop fires, the flow proceeds. The tier
    must be the disclosed-gap tier, not FAIL."""
    plan = [p3.StepResult("pnr", "PASS"),
            p3.StepResult("step11_lec_equivalence", dosr.NOT_EXECUTED_STATUS)]
    assert p3._aggregate_verdict(plan) == "PASS_WITH_WAIVERS"


def test_an_unclosed_ladder_DOES_block_phase3(p3):
    """THE OTHER DIRECTION. Classifying NOT_EXECUTED must not also let the FAIL
    through -- that would re-open the hole from the other side."""
    plan = [p3.StepResult("pnr", "PASS"),
            p3.StepResult("step11_lec_equivalence", "FAIL")]
    assert p3._aggregate_verdict(plan) == "FAIL"
