"""The declared step gates must reject a producer's damaged bytes."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from programs import flow_compliance_check as compliance

FLOW = Path(__file__).resolve().parents[2] / "flow/phase1_phase2_phase3.yaml"


def _write(root: Path, rel: str, value: str | dict) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) if isinstance(value, dict) else value)
    return path


def _steps() -> dict[str, dict]:
    doc = yaml.safe_load(FLOW.read_text())
    return {str(step["id"]): step for step in doc["steps"] if "id" in step}


CASES = {
    "1": ("phase2/stage1/rtl/top.v", "module top(); endmodule\n", ""),
    "14": ("phase2/stage2/synth/netlist.v", "module top(); endmodule\n", ""),
    "18": ("phase3/stage3/pnr/spare_cells.json", {
        "count": 2, "placed_cells_est": 50, "actual_density": 0.04,
        "tied_off": True,
        "instances": [{"name": "s0", "llx": 0, "lly": 0, "keep": True},
                      {"name": "s1", "llx": 10, "lly": 10, "keep": True}]}, ""),
    "27": ("reports/phase3/si_crosstalk.json", {
        "max_crosstalk_noise": 0.02, "violations_count": 0}, ""),
    "32": ("phase3/stage3/postroute_timing_repair/postroute_timing_repair_decision.json",
           {"repair_needed": False}, ""),
    "35": ("reports/phase3/dfm_screen.json",
           {"verdict": "PASS", "findings": []}, ""),
    "P0": ("phase2/stage1/rtl/top.v", "module top(); endmodule\n", ""),
}


@pytest.mark.parametrize("step_id", CASES)
def test_real_gate_accepts_good_bytes_and_rejects_blank_output(tmp_path, step_id):
    rel, good, damaged = CASES[step_id]
    path = _write(tmp_path, rel, good)
    if step_id == "27":
        _write(tmp_path, "reports/phase3/si_mcf_sta.json", {
            "nominal": {"worst_setup_slack_ns": 1.0},
            "corners": {"setup": {"worst_slack_after_ns": 0.9}}})
    if step_id == "32":
        _write(tmp_path, "phase3/stage3/postroute_timing_repair/no_repair_needed.flag",
               "no repair required\n")
    gate = _steps()[step_id].get("gate")
    assert gate is not None, f"step {step_id} has no declared content gate"
    # Step 27 also runs an older optional MCF checker whose independent
    # SPEF inputs are absent from this content-only fixture. Exercise the new
    # blocking clauses themselves; its full flow path is covered separately.
    subject_gate = gate
    if step_id == "27":
        subject_gate = {"all_of": gate["all_of"][:2]}
    good_ok, good_reasons = compliance._evaluate_gate(tmp_path, subject_gate)
    assert good_ok, (step_id, good_reasons)
    path.write_text(damaged)
    bad_ok, bad_reasons = compliance._evaluate_gate(tmp_path, subject_gate)
    assert not bad_ok, (step_id, bad_reasons)


def test_step14_blocks_corrupt_declared_analog_report(tmp_path):
    _write(tmp_path, "phase1/analog/analog_block_list.json", {"blocks": []})
    report = _write(tmp_path, "reports/analog/stage_analog_compliance.json",
                    {"overall": "PASS"})
    gate = _steps()["14"]["gate"]
    clause = next(x for x in gate["all_of"] if x.get("program_exit_zero", "").endswith("--mode stage_analog"))
    assert compliance._evaluate_gate(tmp_path, clause)[0]
    report.write_text("{damaged")
    assert not compliance._evaluate_gate(tmp_path, clause)[0]
    report.unlink()
    assert not compliance._evaluate_gate(tmp_path, clause)[0]


@pytest.mark.parametrize("step_id", ("1", "14", "18", "27", "32", "35", "P0"))
def test_each_listed_step_declares_a_blocking_content_program(step_id):
    step = _steps()[step_id]
    def commands(gate):
        if isinstance(gate, dict):
            if "program_exit_zero" in gate:
                yield gate["program_exit_zero"]
            for key in ("all_of", "any_of"):
                for sub in gate.get(key, []) if isinstance(gate.get(key), list) else []:
                    yield from commands(sub)
    assert list(commands(step.get("gate"))), step_id


@pytest.mark.parametrize("step_id", (
    "40", "41", "42", "43", "44", "A1", "A2", "A3", "A4", "A5",
    "A6", "A7", "A8", "A9", "DT1", "FS1", "M1", "M2", "M3", "M4"))
def test_each_existing_condition_has_a_reviewed_kind(step_id):
    expected = "setup_required" if step_id == "FS1" else "design_dependent"
    step = _steps()[step_id]
    assert step.get("condition")
    assert step.get("condition_kind") == expected
