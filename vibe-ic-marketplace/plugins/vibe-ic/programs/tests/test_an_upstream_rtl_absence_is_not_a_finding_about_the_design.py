#!/usr/bin/env python3
"""An RTL consumer whose input was never produced has measured nothing.

MEASURED on lane rvp2's sha256 run (IC_STATUS_0928, crypto_accelerator:
rtl_gen=null, so step 1 hands the design to the spec-to-rtl author). Nobody
authored it, so phase2/stage1/rtl/ holds no RTL. rtl_validate, sim,
yosys_synth and dft_lec_chain went through the pre-flight gate and booked
REFUSED TO RUN / NOT_MEASURED(input_absent), naming step 1 as the owner.
Two consumers were not behind that gate and FAILed on the same absence:

    step4_functional_evidence  FAIL  "INCOMPLETE: no step-4 simulation evidence"
    fmeda_fault_injection      FAIL  "--rtl-dir 'phase2/stage1/rtl' does not exist"

Both were the run's only reds, and neither was about the design.

Both shapes are tested. With no RTL, the steps book the upstream absence
exactly as the pre-flight books it for `sim`. With RTL present, they judge the
design as before, so a step-4 FAIL over real RTL with no evidence stays FAIL.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parent.parent
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import design_one_shot_runner as R  # noqa: E402

DIGITAL = "crypto_accelerator"   # the measured class: rtl_gen=null, not analog
RTL = "module dut_core(input clk, output q); assign q = clk; endmodule\n"


def _with_rtl(project: Path) -> Path:
    rtl = project / "phase2" / "stage1" / "rtl"
    rtl.mkdir(parents=True, exist_ok=True)
    (rtl / "dut_core.v").write_text(RTL)
    return project


def _sim_preflight_row(project: Path):
    """What the pre-flight gate books for `sim` (step 4) on the same tree."""
    called = []
    row = R._spf.gate(project, "design_one_shot_runner", "sim",
                      R._preflight_refusal("sim"),
                      lambda: called.append(1) or "DISPATCHED")
    assert not called, "the pre-flight dispatched `sim` on a tree with no RTL"
    return row


# ── the upstream absence ─────────────────────────────────────────────────────
@pytest.mark.parametrize("step", ["step4", "fmeda"])
def test_no_rtl_is_booked_as_the_upstream_absence(tmp_path, step):
    """RED on main 7fac744e1 (both FAIL)."""
    row = (R.step_step4_functional_evidence(tmp_path, DIGITAL) if step == "step4"
           else R.step_fmeda_fault_injection(tmp_path))
    assert row.status == "NOT_MEASURED", (row.status, row.detail)
    assert row.reason_class == "input_absent", row
    assert row.extras.get("producer_step") == "rtl_gen", row.extras
    assert "rtl_gen" in row.detail and "upstream" in row.detail
    assert R._aggregate_verdict([row]) != "FAIL"


@pytest.mark.parametrize("step", ["step4", "fmeda"])
def test_it_is_the_same_booking_the_preflight_gives_sim(tmp_path, step):
    """"Consistent with the rest" is a comparison, so it is made against the
    pre-flight's own row for step 4 on the very same tree."""
    ref = _sim_preflight_row(tmp_path)
    row = (R.step_step4_functional_evidence(tmp_path, DIGITAL) if step == "step4"
           else R.step_fmeda_fault_injection(tmp_path))
    assert (row.status, row.reason_class) == (ref.status, ref.reason_class), \
        (row, ref)


def test_an_analog_design_with_no_rtl_track_is_not_applicable(tmp_path):
    """No digital RTL track at all is the analog route, which the pre-flight
    books NOT_APPLICABLE for dft_lec_chain; FMEDA follows it."""
    row = R.step_fmeda_fault_injection(tmp_path, ic_class="pure_analog")
    assert row.status == "NOT_APPLICABLE", (row.status, row.detail)


# ── the design is still judged when its RTL exists ───────────────────────────
def test_rtl_present_and_no_evidence_is_still_a_step4_fail(tmp_path):
    """The real FAIL is untouched: the design exists and nothing tested it."""
    row = R.step_step4_functional_evidence(_with_rtl(tmp_path), DIGITAL)
    assert row.status == "FAIL", (row.status, row.detail)
    assert "no step-4 simulation evidence" in row.detail
    assert row.extras.get("refused_for") != "absent_declared_input"


def test_rtl_present_the_fmeda_producer_runs(tmp_path):
    """With RTL the producer is dispatched and its own verdict stands."""
    row = R.step_fmeda_fault_injection(_with_rtl(tmp_path))
    assert row.extras.get("refused_for") != "absent_declared_input", row
    assert "producer_exit" in row.extras, (row.status, row.detail)
