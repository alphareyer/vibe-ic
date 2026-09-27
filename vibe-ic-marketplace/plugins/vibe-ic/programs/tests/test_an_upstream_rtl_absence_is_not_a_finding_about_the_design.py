#!/usr/bin/env python3
"""An RTL consumer whose input step 1 never delivered has measured nothing.

MEASURED on lane rvp2's sha256 run (IC_STATUS_0928). The class is
crypto_accelerator with rtl_gen=null, so step 1 hands the design to the
spec-to-rtl author; its row is NOT_MEASURED(awaiting_signed_judgement).
Nobody authored it, so phase2/stage1/rtl/ holds no RTL. rtl_validate, sim,
yosys_synth and dft_lec_chain went through the pre-flight gate and booked
REFUSED TO RUN / NOT_MEASURED(input_absent), naming step 1. Two consumers were
not behind that gate and FAILed on the same absence:

    step4_functional_evidence  FAIL  "INCOMPLETE: no step-4 simulation evidence"
    fmeda_fault_injection      FAIL  "--rtl-dir 'phase2/stage1/rtl' does not exist"

The discriminator is the run's own record: step 1 said it did not deliver.
Without that record the landed behaviour stands, and these tests keep it
standing too:
  * a bare call on an empty tree still FAILs step 4 (#1975);
  * a run whose step 1 is declared out of scope still FAILs step 4 (#2208);
  * an rtl/ with no source is still the FMEDA producer's own NOT_MEASURED,
    with its report (FS1);
  * with RTL present, the design is judged as before.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parent.parent
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import design_one_shot_runner as R  # noqa: E402

DIGITAL = "crypto_accelerator"   # the measured class: rtl_gen=null, not analog
RTL = "module dut_core(input clk, output q); assign q = clk; endmodule\n"

#: rtl_gen rows that say step 1 did not deliver. The first is sha256's.
WAIVED_ROWS = {
    "awaiting_author_judgement": dict(
        status="NOT_MEASURED", reason_class="awaiting_signed_judgement",
        detail="IC class registered but rtl_gen=null. Recommended action: AI "
               "invokes skill `spec-to-rtl`."),
    "waive_to_author": dict(
        status="PASS_WITH_WAIVERS", extras={"fallback_skill": "spec-to-rtl"},
        detail="rtl_gen=null; WAIVE to spec-to-rtl"),
    "generator_failed": dict(status="FAIL", detail="registered generator failed"),
}


def _rtl_gen_row(kind: str) -> "R.StepResult":
    spec = dict(WAIVED_ROWS[kind])
    return R.StepResult("rtl_gen", spec.pop("status"), 0.0, spec.pop("detail"),
                        **spec)


def _with_rtl(project: Path) -> Path:
    rtl = project / "phase2" / "stage1" / "rtl"
    rtl.mkdir(parents=True, exist_ok=True)
    (rtl / "dut_core.v").write_text(RTL)
    return project


def _call(step: str, project: Path, **kw):
    if step == "step4":
        return R.step_step4_functional_evidence(project, DIGITAL, **kw)
    return R.step_fmeda_fault_injection(project, **kw)


# ── through the runner: the sha256 shape ─────────────────────────────────────
def _drive_main(tmp_path: Path, monkeypatch, rtl_gen_row: "R.StepResult"):
    """main() with every step stubbed except the two subjects, as #2208 does,
    and step 1 answering with ``rtl_gen_row``."""
    docs = tmp_path / "phase1/generated_docs"
    docs.mkdir(parents=True)
    for n in range(1, 14):
        (docs / f"L{n}.json").write_text("{}")
    subjects = {"step_step4_functional_evidence", "step_fmeda_fault_injection"}
    for name in list(vars(R)):
        if not name.startswith("step_") or name in subjects:
            continue

        def stub(*_a, _name=name, **_kw):
            if _name == "step_rtl_gen":
                return rtl_gen_row
            row = R.StepResult(_name.removeprefix("step_"), "NOT_APPLICABLE",
                               0.0, "unrelated tool stub",
                               declared_by="this test stubs every step but "
                                           "its subjects")
            return [row] if _name == "step_dft_lec_chain" else row
        monkeypatch.setattr(R, name, stub)
    monkeypatch.setattr(R._spf, "gate", lambda project, owner, site, refuse,
                        fn, *a, **kw: fn(*a, **{k: v for k, v in kw.items()
                                                if not k.startswith("_preflight")}))
    monkeypatch.setattr(R._pl, "emit_final_summary", lambda *a, **kw: False)

    def publish(project, programs, owner, summary, name, only_steps=None):
        out = tmp_path / name
        out.write_text(json.dumps(summary))
        return {}, out
    monkeypatch.setattr(R._pl, "publish_report_then_steps_view", publish)
    monkeypatch.setattr(sys, "argv", [
        "runner", str(tmp_path), "--skip-hardware", "--skip-phase3",
        "--skip-analog", "--max-rtl-repair-retries", "0"])
    R.main()
    report = json.loads((tmp_path / "phase2_one_shot.json").read_text())
    return {row["name"]: row for row in report["steps"]}


@pytest.mark.parametrize("kind", sorted(WAIVED_ROWS))
def test_the_run_books_both_steps_as_the_upstream_absence(tmp_path,
                                                          monkeypatch, kind):
    """RED on main 7fac744e1: both rows FAIL, as in the sha256 run."""
    rows = _drive_main(tmp_path, monkeypatch, _rtl_gen_row(kind))
    for name in ("step4_functional_evidence", "fmeda_fault_injection"):
        row = rows[name]
        assert (row["status"], row["reason_class"]) == (
            "NOT_MEASURED", "input_absent"), (name, row["status"], row["detail"])
        assert row["extras"]["producer_step"] == "rtl_gen"
        assert "rtl_gen" in row["detail"] and "upstream" in row["detail"]


def test_a_run_whose_step1_is_out_of_scope_still_fails_step4(tmp_path,
                                                             monkeypatch):
    """#2208's own shape: step 1 NOT_APPLICABLE says nothing about why rtl/ is
    empty, so the absence is not step 1's to own and step 4 stays FAIL."""
    rows = _drive_main(tmp_path, monkeypatch, R.StepResult(
        "rtl_gen", "NOT_APPLICABLE", 0.0, "stubbed", declared_by="stub"))
    assert rows["step4_functional_evidence"]["status"] == "FAIL"


# ── the step, called with the run's step-1 row ───────────────────────────────
@pytest.mark.parametrize("step", ["step4", "fmeda"])
def test_it_is_the_same_booking_the_preflight_gives_sim(tmp_path, step):
    """"Consistent with the rest" is a comparison, so it is made against the
    pre-flight's own row for step 4 on the very same tree."""
    called = []
    ref = R._spf.gate(tmp_path, "design_one_shot_runner", "sim",
                      R._preflight_refusal("sim"),
                      lambda: called.append(1) or "DISPATCHED")
    assert not called, "the pre-flight dispatched `sim` on a tree with no RTL"
    row = _call(step, tmp_path,
                rtl_producer=_rtl_gen_row("awaiting_author_judgement"))
    assert (row.status, row.reason_class) == (ref.status, ref.reason_class), \
        (row, ref)
    assert R._aggregate_verdict([row]) != "FAIL"


def test_an_analog_design_with_no_rtl_track_is_not_applicable(tmp_path):
    """No digital RTL track at all is the analog route, which the pre-flight
    books NOT_APPLICABLE for dft_lec_chain; FMEDA follows it."""
    row = R.step_fmeda_fault_injection(
        tmp_path, ic_class="pure_analog",
        rtl_producer=_rtl_gen_row("waive_to_author"))
    assert row.status == "NOT_APPLICABLE", (row.status, row.detail)


# ── what stays exactly as landed ─────────────────────────────────────────────
def test_a_bare_call_on_an_empty_tree_still_fails_step4(tmp_path):
    """#1975's contract: no run record, no RTL, no testbench -> FAIL."""
    row = R.step_step4_functional_evidence(tmp_path, DIGITAL)
    assert row.status == "FAIL", (row.status, row.detail)


@pytest.mark.parametrize("step", ["step4", "fmeda"])
def test_rtl_present_the_design_is_judged_as_before(tmp_path, step):
    """Even with step 1's row saying it did not deliver: RTL exists (an author
    wrote it afterwards), so the real verdict stands."""
    row = _call(step, _with_rtl(tmp_path),
                rtl_producer=_rtl_gen_row("awaiting_author_judgement"))
    assert row.extras.get("refused_for") != "absent_declared_input", row
    if step == "step4":
        assert row.status == "FAIL" and "no step-4 simulation evidence" \
            in row.detail, (row.status, row.detail)
    else:
        assert "producer_exit" in row.extras, (row.status, row.detail)
