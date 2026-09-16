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
import unittest
from pathlib import Path

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


def _status_for(doc) -> str:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "lec.json"
        p.write_text(doc if isinstance(doc, str) else json.dumps(doc))
        return dosr.lec_step_status_from_report(p)[0]


class InconclusiveIsNeverSkip(unittest.TestCase):
    """(1) — the three INCONCLUSIVE states, and none of them is SKIP."""

    def test_a_completed_ladder_that_did_not_close_FAILS(self):
        """THE REGRESSION. Points were compared, the ladder finished, nothing
        ran out — the equivalence question is OPEN and the flow may not walk
        past it. Reverting to `INCONCLUSIVE -> SKIP` fails here."""
        status, reason = dosr.lec_inconclusive_disposition(RUN16_UNCLOSED)
        self.assertEqual(status, "FAIL")
        self.assertNotEqual(status, "SKIP")
        self.assertIn("846", reason)
        self.assertIn("481", reason)

    def test_the_step_mapper_agrees_with_the_disposition(self):
        """The mapper must DELEGATE, not keep its own opinion."""
        self.assertEqual(_status_for(RUN16_UNCLOSED), "FAIL")

    def test_a_proof_that_was_cut_off_is_NOT_a_fail(self):
        """R-0915-5 / R-0915-48: when the backstop fires, nothing is known.
        Asserting a non-equivalence nobody measured is #192 inverted, and it
        would also break R-48's contract that phase 3 proceeds. Deleting the
        exhaustion check fails here."""
        status, reason = dosr.lec_inconclusive_disposition(BUDGET_KILLED)
        self.assertEqual(status, dosr.NOT_EXECUTED_STATUS)
        self.assertNotEqual(status, "FAIL")
        self.assertIn("NOT_MEASURED", reason)

    def test_every_recorded_exhaustion_route_is_honoured(self):
        """Each is a different way for `lec_run` to say "I was stopped". A fix
        that only knows about `budget_exhausted` fails here."""
        for mutate in (
            dict(RUN16_UNCLOSED, step_budget_exhausted=True),
            dict(RUN16_UNCLOSED, step_budget_stopped_this_proof=True),
            dict(RUN16_UNCLOSED, progress_stalled=True),
            dict(RUN16_UNCLOSED, exhausted_resource="wall_clock"),
            dict(RUN16_UNCLOSED,
                 bounded_rung_policy={"limit_reached": True}),
            dict(RUN16_UNCLOSED,
                 lec_attempts_detail=[{"killed_by_budget": True}]),
        ):
            with self.subTest(route=sorted(set(mutate) - set(RUN16_UNCLOSED))
                              or "flag"):
                self.assertEqual(
                    dosr.lec_inconclusive_disposition(mutate)[0],
                    dosr.NOT_EXECUTED_STATUS)

    def test_zero_compared_points_is_not_measured_not_a_failure(self):
        """The state the OLD docstring described, kept intact in substance:
        never a vacuous PASS, never a cascading FAIL. Only its NAME changed,
        from an undifferentiated SKIP to NOT_MEASURED with the reason."""
        status, reason = dosr.lec_inconclusive_disposition(NOTHING_COMPARED)
        self.assertEqual(status, dosr.NOT_EXECUTED_STATUS)
        self.assertNotIn(status, ("PASS", "FAIL"))
        self.assertIn("0 point", reason)

    def test_the_three_reasons_are_three_different_sentences(self):
        """A naming that collapses says nothing. Any implementation that emits
        one sentence for all three fails here."""
        reasons = {
            dosr.lec_inconclusive_disposition(RUN16_UNCLOSED)[1],
            dosr.lec_inconclusive_disposition(BUDGET_KILLED)[1],
            dosr.lec_inconclusive_disposition(NOTHING_COMPARED)[1],
        }
        self.assertEqual(len(reasons), 3)

    def test_exhaustion_is_decided_BEFORE_the_point_count(self):
        """A proof cut off before comparing anything must read as cut off, not
        as "the miter judged nothing" — the two have different remedies."""
        killed_early = dict(NOTHING_COMPARED, budget_exhausted=True)
        self.assertIn("stopped before it finished",
                      dosr.lec_inconclusive_disposition(killed_early)[1])

    def test_PASS_and_FAIL_and_SKIPPED_CONDITION_are_untouched(self):
        """This change may not move any verdict it was not written for."""
        self.assertEqual(_status_for({"verdict": "PASS", "equivalent": True,
                                      "compared_points": 65}), "PASS")
        self.assertEqual(_status_for({"verdict": "FAIL", "equivalent": False,
                                      "compared_points": 66,
                                      "unproven_points": 2}), "FAIL")
        self.assertEqual(_status_for({"verdict": "SKIPPED-CONDITION",
                                      "equivalent": False}), "SKIP")
        self.assertEqual(_status_for("{ not json"), "SKIP")


class AReusedRecordSaysSo(unittest.TestCase):
    """(2) — a verdict earned by an earlier run must say whose work it was."""

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

    def test_a_resumed_record_that_climbed_no_rung_is_named_reused(self):
        note = dosr.lec_record_reuse_note(self.RESUMED)
        self.assertTrue(note)
        self.assertIn("REUSED", note)
        self.assertIn("equiv_induct_seq64", note)

    def test_a_reused_record_still_earns_the_same_status(self):
        """Reuse is a disclosure, never a discount. The 2 s re-read of run16's
        record must FAIL exactly as the 8306 s proof did."""
        self.assertEqual(_status_for(self.RESUMED), "FAIL")

    def test_a_pass_cache_hit_is_named_reused(self):
        note = dosr.lec_record_reuse_note(
            {"verdict": "PASS", "execution_mode": "exact-pass-cache-hit"})
        self.assertIn("REUSED", note)

    def test_a_fresh_proof_is_NOT_called_reused(self):
        """THE CONTROL. A note that is always non-empty measures nothing.
        run16 pass 1 really did climb five rungs."""
        fresh = dict(
            RUN16_UNCLOSED,
            lec_resume={"resumed": False, "rungs_recorded_this_run":
                        ["equiv_simple_short", "equiv_induct_seq64"]},
            lec_ladder={"legs": [{"rungs_in_this_process": 5}]})
        self.assertEqual(dosr.lec_record_reuse_note(fresh), "")

    def test_a_run_that_never_RESUMED_is_not_called_reused(self):
        """The `resumed` flag has to be load-bearing on its own. Without this
        case a mutation that drops the resume guard survives, because the
        realistic fresh record also carries a non-empty
        `rungs_recorded_this_run` and is rejected by the second condition —
        two guards, one of them never exercised alone. Here nothing resumed
        and nothing was climbed, so there is no earlier run to credit."""
        never_resumed = dict(
            RUN16_UNCLOSED,
            lec_resume={"resumed": False, "rungs_recorded_this_run": []},
            lec_ladder={"legs": [{"rungs_in_this_process": 0}]})
        self.assertEqual(dosr.lec_record_reuse_note(never_resumed), "")

    def test_a_record_with_no_resume_block_at_all_is_not_called_reused(self):
        """A producer that never wrote `lec_resume` says nothing about reuse;
        absence is not evidence of it."""
        self.assertEqual(dosr.lec_record_reuse_note(RUN16_UNCLOSED), "")

    def test_a_resume_that_DID_climb_a_rung_is_not_called_reused(self):
        """Resuming and then doing real work is not reuse — the honest
        boundary is "how many rungs did THIS invocation climb"."""
        worked = dict(
            RUN16_UNCLOSED,
            lec_resume={"resumed": True,
                        "resumed_from": {"rung": "equiv_induct_seq16"},
                        "rungs_recorded_this_run": ["equiv_induct_seq64"]},
            lec_ladder={"legs": [{"rungs_in_this_process": 1}]})
        self.assertEqual(dosr.lec_record_reuse_note(worked), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class Phase3CanGradeTheNewRow(unittest.TestCase):
    """R-0915-82, SECOND ORDER — the phase-3 aggregator must be able to grade
    the row this change newly emits.

    `run_step11_dft_after_synth` republishes `step_dft_lec_chain`'s rows
    VERBATIM as `step11_<name>` (phase3_one_shot_runner.py, "Re-publish the
    producer's OWN verdicts as phase-3 rows, verbatim"). So the moment the LEC
    step can answer NOT_EXECUTED, that word reaches `_aggregate_verdict` — whose
    known set is `union(_TIERS.values())` and which REFUSES BY NAME anything
    outside it (vibe-ic#2153). Unclassified, a correction to ONE step would have
    turned every budget-stopped run's whole phase-3 verdict into
    `UNKNOWN_STATUS:` — a fix acquiring a second defect one file away.

    Both directions are required: the disclosed gap must NOT block (R-0915-48's
    contract) and the unclosed ladder must."""

    def setUp(self):
        try:
            import phase3_one_shot_runner as p3
        except Exception as exc:                     # pragma: no cover
            self.skipTest(f"phase3 runner not importable: {exc}")
        self.p3 = p3

    def test_NOT_EXECUTED_is_inside_the_known_vocabulary(self):
        plan = [self.p3.StepResult("pnr", "PASS"),
                self.p3.StepResult("step11_lec_equivalence",
                                   dosr.NOT_EXECUTED_STATUS)]
        v = self.p3._aggregate_verdict(plan)
        self.assertFalse(str(v).startswith("UNKNOWN_STATUS:"),
                         f"phase 3 cannot grade the row this change emits: {v}")

    def test_a_stopped_proof_does_not_block_phase3(self):
        """R-0915-48 word for word: the backstop fires, the flow proceeds. The
        tier must be the disclosed-gap tier, not FAIL."""
        plan = [self.p3.StepResult("pnr", "PASS"),
                self.p3.StepResult("step11_lec_equivalence",
                                   dosr.NOT_EXECUTED_STATUS)]
        self.assertEqual(self.p3._aggregate_verdict(plan), "PASS_WITH_WAIVERS")

    def test_an_unclosed_ladder_DOES_block_phase3(self):
        """THE OTHER DIRECTION. Classifying NOT_EXECUTED must not also let the
        FAIL through — that would re-open the hole from the other side."""
        plan = [self.p3.StepResult("pnr", "PASS"),
                self.p3.StepResult("step11_lec_equivalence", "FAIL")]
        self.assertEqual(self.p3._aggregate_verdict(plan), "FAIL")
