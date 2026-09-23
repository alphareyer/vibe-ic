#!/usr/bin/env python3
"""R-0915-159 — an audit that could not certify is not an audit that failed.

THE MEASUREMENT (subservient x gf180mcuD as a DIE, lane icsub5, run2 on main
1f537b5c0, 2026-09-23). The phase-2 audit's own tally:

    PASS=4  PASS_WITH_WAIVERS=1  WAIVED-DEFERRED=1  FAIL=4  NOT_MEASURED=5
            NOT_APPLICABLE=17
    Overall: NOT_MEASURED (strict=True)

Five steps measured nothing — P0 (partial_population), D1 (awaiting_agent_pass),
Step 2 (execution_error), Step 4 and Step 5 (partial_population). `step_final_audit`
had no branch for that word, so it fell through to FAIL; phase 2's verdict became
FAIL; and `vibe_ic_one_shot_runner` halted before phase 3. A DIE deliverable could
never reach its own sign-off, because the audit of a phase that had not run yet
was read as a defect in the phase that had.

NOT_MEASURED is "cannot certify", never "found a defect" — R-0915-140/156, and
the rule this same function already applies to a partial structural population.

THIS IS NOT A SOFTENING, AND THE SECOND HALF OF THIS DECK IS THE PROOF: stacked
on next/icslot70, the front door's roll-up puts a NOT_MEASURED phase into
`unmeasured`, and a run with anything unmeasured can never roll up to PASS. So
the flow PROCEEDS past phase 2 and still REFUSES TO CERTIFY. Both halves are
exercised together here, because either one alone would be the wrong change.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parents[1]
if str(PROG) not in sys.path:
    sys.path.insert(0, str(PROG))

import design_one_shot_runner as D        # noqa: E402
import vibe_ic_one_shot_runner as O       # noqa: E402

#: The audit transcript shape, transcribed from the run above.
NM_OUT = """=== Vibe-IC phase1_phase2_phase3 compliance ===
  … [NOT_MEASURED     ] Step P0: Structural-RTL gates (P0 umbrella)  (stage1) (partial_population)
  … [NOT_MEASURED     ] Step D1: Phase 1 Doc Extraction  (stage_phase1) (awaiting_agent_pass)
  … [NOT_MEASURED     ] Step  2: RTL validation  (stage1) (execution_error)
  … [NOT_MEASURED     ] Step  4: Simulation  (stage1) (partial_population)
  … [NOT_MEASURED     ] Step  5: Formal verification  (stage1) (partial_population)
  ✗ [FAIL             ] Step 15.5ic: Pad Ring  (stage3) (missing_artefact)
  PASS=4  PASS_WITH_WAIVERS=1  WAIVED-DEFERRED=1  FAIL=4  NOT_MEASURED=5  NOT_APPLICABLE=17
Overall: NOT_MEASURED  (strict=True)
STRUCTURAL MEASUREMENT: registered=245 invoked=245 no_verdict=0
"""
FAIL_OUT = NM_OUT.replace("Overall: NOT_MEASURED", "Overall: FAIL")


# ── 1. the rows, which are the information the word throws away ───────────
def test_the_not_measured_rows_are_extracted_in_order():
    rows = D._not_measured_rows(NM_OUT)
    assert len(rows) == 5
    assert rows[0].startswith("Step P0:")
    assert "partial_population" in rows[0]
    assert any("execution_error" in r for r in rows)


def test_a_FAIL_row_is_never_collected_as_not_measured():
    """THE DISCRIMINATION. The two tiers must not blur into each other."""
    assert all("15.5ic" not in r for r in D._not_measured_rows(NM_OUT))


def test_a_transcript_with_none_yields_an_empty_list_not_a_guess():
    assert D._not_measured_rows("Overall: PASS\n") == []
    assert D._not_measured_rows("") == []


# ── 2. the mapping, both directions ───────────────────────────────────────
def _drive(out: str, tmp_path, monkeypatch):
    """Run step_final_audit against a canned audit transcript."""
    (tmp_path / "reports" / "audit").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(D, "_run", lambda *a, **k: (1, out, ""))
    monkeypatch.setattr(D.PROGRAMS_DIR.__class__, "is_file",
                        lambda self: True, raising=False)
    return D.step_final_audit(tmp_path, phase=2)


def test_an_overall_NOT_MEASURED_becomes_NOT_MEASURED_not_FAIL(tmp_path,
                                                               monkeypatch):
    r = _drive(NM_OUT, tmp_path, monkeypatch)
    assert r.status == "NOT_MEASURED", r.status
    assert r.extras.get("not_measured_rows")
    assert len(r.extras["not_measured_rows"]) == 5
    assert "cannot certify" in r.detail or "not a defect found" in r.detail


def test_an_overall_FAIL_is_STILL_a_FAIL(tmp_path, monkeypatch):
    """THE NEGATIVE ARM. This changes one word and only one word."""
    r = _drive(FAIL_OUT, tmp_path, monkeypatch)
    assert r.status == "FAIL", r.status


# ── 3. the other half: proceeding must NOT become passing ─────────────────
def test_a_NOT_MEASURED_phase_cannot_roll_up_to_PASS():
    """Stacked on next/icslot70. The whole safety of R-0915-159 rests here: if
    a NOT_MEASURED phase could still reach PASS, turning FAIL into NOT_MEASURED
    would be a softening. It cannot."""
    verdict, why = O._roll_up([("phase1", "PASS", 0),
                               ("phase2", "NOT_MEASURED", 0)],
                              audit_axis={"state": "NOT_APPLICABLE",
                                          "reason": ""})
    assert verdict == "NOT_MEASURED", (verdict, why)
    assert any("not measured" in w for w in why)


def test_a_NOT_MEASURED_completion_audit_row_cannot_roll_up_to_PASS():
    verdict, why = O._roll_up([("phase1", "PASS", 0), ("phase2", "PASS", 0)],
                              audit_axis={"state": "NOT_MEASURED",
                                          "reason": "no verdict could be read"})
    assert verdict == "NOT_MEASURED", (verdict, why)


def test_the_flow_PROCEEDS_on_a_not_measured_phase_but_never_certifies():
    """Both halves in one statement: NOT_MEASURED is not a halt word for the
    orchestrator (only FAIL is), and it is not a pass word for the roll-up."""
    assert O._roll_up([("phase1", "PASS", 0), ("phase2", "NOT_MEASURED", 0),
                       ("phase3", "PASS", 0)],
                      audit_axis={"state": "PASS", "reason": ""})[0] \
        == "NOT_MEASURED"
    # and a genuinely clean run is still a clean PASS
    assert O._roll_up([("phase1", "PASS", 0), ("phase2", "PASS", 0),
                       ("phase3", "PASS", 0)],
                      audit_axis={"state": "PASS", "reason": ""})[0] == "PASS"


# ── 4. MUTATION: the old fall-through must go red ─────────────────────────
def test_MUTATION_falling_through_to_FAIL_is_caught():
    """Re-plant the defect: no NOT_MEASURED branch, so the word falls to FAIL."""
    def defect(out):
        if "Overall: PASS_WITH_WAIVERS" in out or "Overall: PASS" in out:
            return "PASS"
        return "FAIL"                      # the pre-R-0915-159 behaviour

    assert defect(NM_OUT) == "FAIL"        # the defect, reproduced
    assert defect(FAIL_OUT) == "FAIL"      # and it cannot tell the two apart
