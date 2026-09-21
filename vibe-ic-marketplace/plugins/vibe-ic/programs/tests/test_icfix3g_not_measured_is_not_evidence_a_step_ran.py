"""(6) NOT_MEASURED is not evidence that a step RAN, and it is not evidence
that it did not.

MEASURED on the spm runs (8HD-4, `_lane_icspm5`) — ONE design, TWO runs, the
SAME three steps, opposite outcomes, from
`reports/orchestrator/phase2_one_shot.json`:

    run8   qsf_gen SKIP            fpga_compile SKIP          fpga_burn SKIP
    run13  qsf_gen NOT_APPLICABLE  fpga_compile NOT_MEASURED  fpga_burn NOT_MEASURED
                                   (reason_class=input_absent)

`DID_NOT_RUN_STATUSES` held SKIP and NOT_APPLICABLE but not NOT_MEASURED, and
`step_execution_status` treats anything outside that set as "positive execution
evidence — decisive". So on run13 the guard refused the FPGA waiver as stale,
every run, saying:

    STALE WAIVER REFUSED: step 'fpga_compile' actually EXECUTED in this run
    (status='NOT_MEASURED')

about a step whose own reason says its INPUT WAS ABSENT — it could not start.
MEASURED both ways on the real reports: pre-fix run13 holds=False, run8
holds=True; with the fix both hold, and they hold FOR DIFFERENT REASONS.

WHY A SEPARATE SET AND NOT THREE MORE ENTRIES IN THE OLD ONE. That set says, in
its own words, that its members "mean the step DID NOT actually execute".
NOT_MEASURED does not mean that — it means no measurement was taken. Filing it
there would make the guard ASSERT a did-not-run it cannot support, which is the
same credit-a-silence error pointed the other way. The tri-state this module
already returns has a slot for "no evidence at all"; nothing reached it by this
route until now.

NOT WEAKENED: a real execution status still refuses the waiver, and that arm is
asserted below.

chip-AGNOSTIC: generic step names, no PDK, vendor or design.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import waiver_staleness as W  # noqa: E402

_ENTRY = {
    "verdict_tier": "ENV_UNAVAILABLE",
    "step": "fpga_compile",
    "_waiver_condition": {"kind": W.CONDITION_STEP_DID_NOT_EXECUTE,
                          "run_id": "an_earlier_run"},
}


def _project(tmp_path, statuses):
    """A project carrying one phase report, in the shape the runner writes."""
    d = tmp_path / "reports" / "orchestrator"
    d.mkdir(parents=True, exist_ok=True)
    (d / "phase2_one_shot.json").write_text(json.dumps({
        "run_id": "this_run",
        "steps": [{"name": k, "status": v} for k, v in statuses.items()],
    }, indent=2))
    return tmp_path


def test_not_measured_is_not_positive_execution_evidence(tmp_path):
    """THE DEFECT, in one assertion — the run13 shape."""
    proj = _project(tmp_path, {"fpga_compile": "NOT_MEASURED"})
    assert W.step_executed(proj, "fpga_compile") is None, (
        "NOT_MEASURED was read as evidence the step RAN; it records that no "
        "measurement was taken, which is evidence of neither")


def test_the_waiver_is_not_refused_as_stale_on_that_run(tmp_path):
    """What the defect cost: the FPGA waiver went stale every run."""
    proj = _project(tmp_path, {"fpga_compile": "NOT_MEASURED"})
    holds, why = W.condition_holds(_ENTRY, proj)
    assert holds, why
    assert "no execution evidence" in why, why


def test_a_skip_still_asserts_did_not_run_and_says_so(tmp_path):
    """The run8 shape is UNCHANGED, and it keeps its own distinct reason: SKIP
    asserts the step did not run, NOT_MEASURED only declines to assert that it
    did. Collapsing the two messages would lose exactly the distinction this
    branch exists to preserve."""
    proj = _project(tmp_path, {"fpga_compile": "SKIP"})
    assert W.step_executed(proj, "fpga_compile") is False
    holds, why = W.condition_holds(_ENTRY, proj)
    assert holds and "did-not-run" in why, why


def test_a_step_that_really_ran_still_refuses_the_waiver(tmp_path):
    """THE OTHER DIRECTION, and the one that says this is not a weakening."""
    proj = _project(tmp_path, {"fpga_compile": "PASS"})
    assert W.step_executed(proj, "fpga_compile") is True
    holds, why = W.condition_holds(_ENTRY, proj)
    assert not holds
    assert "STALE WAIVER REFUSED" in why, why


def test_a_failure_still_refuses_the_waiver(tmp_path):
    """A FAIL is execution too — the step ran and the waiver's premise is gone.
    The module's own message says a failure is NOT excused."""
    proj = _project(tmp_path, {"fpga_compile": "FAIL"})
    holds, why = W.condition_holds(_ENTRY, proj)
    assert not holds and "STALE WAIVER REFUSED" in why, why


def test_real_evidence_in_another_report_still_wins_over_not_measured(tmp_path):
    """NOT_MEASURED must not MASK positive evidence. One report says the step
    was not measured and another says it PASSED: the step ran, and the scan
    must keep looking rather than stopping at the first inconclusive row."""
    proj = _project(tmp_path, {"fpga_compile": "NOT_MEASURED"})
    d = proj / "reports"
    (d / "phase3_one_shot.json").write_text(json.dumps({
        "steps": [{"name": "fpga_compile", "status": "PASS"}]}, indent=2))
    assert W.step_executed(proj, "fpga_compile") is True
    holds, why = W.condition_holds(_ENTRY, proj)
    assert not holds, why


def test_not_measured_does_not_masquerade_as_a_did_not_run_status(tmp_path):
    """The set boundary, asserted. NOT_MEASURED must NOT be in the set whose
    members mean "the step DID NOT execute" — putting it there would make the
    guard assert something it cannot support."""
    assert "NOT_MEASURED" not in W.DID_NOT_RUN_STATUSES
    assert "NOT_MEASURED" in W.NO_EXECUTION_EVIDENCE_STATUSES
    assert not (set(W.DID_NOT_RUN_STATUSES)
                & set(W.NO_EXECUTION_EVIDENCE_STATUSES))
