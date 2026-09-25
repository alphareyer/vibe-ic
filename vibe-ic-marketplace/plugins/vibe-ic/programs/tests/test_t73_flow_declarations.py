"""T73 controls for declared outputs and measured gate evidence."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import yaml

PROGRAMS = Path(__file__).resolve().parent.parent
ROOT = PROGRAMS.parent
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(PROGRAMS / "tests"))

import design_one_shot_runner as design
import flow_compliance_check as flow
import flow_gate_enforcement_audit as enforcement
import flow_step_output_content_check as content
import matrix_d7_artifact_graph as d7
import vibe_ic_one_shot_runner as front


def _steps():
    return {str(row["id"]): row for row in yaml.safe_load(
        (ROOT / "flow/phase1_phase2_phase3.yaml").read_text())["steps"]}


def test_rom_lint_manifest_cannot_author_a_pass(tmp_path):
    rtl = tmp_path / "phase2/stage1/rtl"
    rtl.mkdir(parents=True)
    (rtl / "unit.v").write_text("module unit; endmodule\n")
    (tmp_path / "reports").mkdir()
    design.step_emit_phase2_manifests(tmp_path, [])
    report = tmp_path / "reports/phase2/lint/rom_init_lint.json"
    assert not report.exists(), "the manifest writer did not run the ROM gate"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"verdict": "PASS", "evidence": "otp_image_check step"}))
    assert content.check(tmp_path, "rom_lint"), "canned PASS must be refused"
    gate = subprocess.run([sys.executable, str(PROGRAMS / "rom_init_lint.py"),
                           "--json", str(report), str(rtl / "unit.v")],
                          capture_output=True, text=True)
    assert gate.returncode == 0, gate.stderr
    assert json.loads(report.read_text()) == []
    assert content.check(tmp_path, "rom_lint") == []


def test_missing_promised_rtl_fails_p0():
    assert flow._p0_umbrella_status(None, [])[0] == "FAIL"


def test_content_gate_declares_the_blocking_tier_the_audit_reads():
    assert enforcement.declared_intent(PROGRAMS,
                                       "flow_step_output_content_check") == "blocking"
    clauses = str(_steps()["1"]["gate"])
    assert "program_exit_zero" in clauses and "flow_step_output_content_check" in clauses


def test_phase1_dynamic_l_docs_are_declared_and_seen_by_d7():
    names = ("L14_PROTOCOL_VERSIONING", "L15_ENCODING_TABLES",
             "L16_COMPLIANCE_PROPERTIES", "L17_CHANNEL_SIGNAL_CATALOG",
             "L18_INTERCONNECT_TOPOLOGY", "L20_DFT_SCAN_TOPOLOGY",
             "L23_SECURITY_REQUIREMENTS", "L24_SIGNOFF",
             "L25_RELIABILITY_MISSION_PROFILE",
             "L26_MECHANICAL_TRANSDUCTION", "L27_MEMORY_MODULE_SPD")
    declared = {r["path"] for r in _steps()["D1"]["program_outputs"]}
    import ast
    written = {"/".join(p) for p in d7._collect_writes(ast.parse(
        (PROGRAMS / "phase1_doc_one_shot_runner.py").read_text()))}
    for name in names:
        path = f"phase1/generated_docs/{name}.json"
        assert path in declared
        assert path in written


def test_step1_names_all_declaration_producers():
    producers = {r["program"] for r in _steps()["1"]["program_outputs"]
                 if r["path"] == "plugin_output/declaration.json"}
    assert {"ip_catalog_pull", "spec_declaration_emit",
            "arith_declaration_emit"} <= producers


def test_synthesis_inputs_match_runner_order():
    inputs = _steps()["9"]["required_inputs"]
    assert not any(str(r.get("from")) == "7" and
                   "constraints/*.sdc" in r.get("path", "") for r in inputs)
    src = (PROGRAMS / "design_one_shot_runner.py").read_text()
    assert src.index('step_yosys_synth, project') < src.index(
        'plan.append(step_sdc_gen(project, args.top_name, ic_class))')


def test_step7_orders_the_stage1_population_it_audits():
    steps = _steps()
    assert {"1", "3", "6"} <= {str(x) for x in steps["7"]["blocks_on"]}
    assert any("stage1_compliance" in str(clause) for clause in
               steps["7"]["gate"]["all_of"])


def test_whole_flow_audit_reaches_front_door_rollup(tmp_path):
    """Run the checker, consume its fresh report through the actual front door."""
    started = time.time()
    audit = tmp_path / "reports/audit/phase23_completion_audit.json"
    proc = subprocess.run([sys.executable, str(PROGRAMS / "flow_compliance_check.py"),
                           str(tmp_path), "--strict", "--json", str(audit)],
                          capture_output=True, text=True, timeout=180)
    assert proc.returncode != 0
    doc = json.loads(audit.read_text())
    assert doc["scope"]["whole_flow"] is True
    assert {str(s["id"]) for s in doc["steps"]} >= {"7", "8", "9"}
    assert all(str(s["status"]) not in {"PASS", "PASS_WITH_WAIVERS"}
               for s in doc["steps"] if str(s["id"]) in {"7", "8", "9"})
    axis = front._completion_audit_axis(tmp_path, phase3_ran=True,
                                        started_at=started)
    verdict, reasons = front._roll_up([("phase3", "PASS", 0)], audit_axis=axis)
    assert axis["state"] == "FAIL" and verdict == "FAIL"
    assert any("completion audit" in line for line in reasons)


def test_phase3_strict_failure_is_not_reported_as_info():
    src = (PROGRAMS / "phase3_one_shot_runner.py").read_text()
    start = src.index('if _fc.returncode != 0:')
    end = src.index('except Exception as _fc_exc:', start)
    block = src[start:end]
    assert '_audit_tier == "FAIL"' in block
    assert '"ERROR"' in block
    assert '[INFO] flow_compliance refresh returned' not in block
