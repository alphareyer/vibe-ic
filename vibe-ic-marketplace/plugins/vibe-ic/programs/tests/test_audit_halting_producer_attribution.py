"""A skipped canonicalizer must not be charged as an earlier output defect."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml


PLUGIN = Path(__file__).resolve().parents[2]
PROGRAM = PLUGIN / "programs" / "flow_compliance_check.py"
FLOW = PLUGIN / "flow" / "phase1_phase2_phase3.yaml"


def _case(tmp_path: Path, *, producer_ran: bool = False,
          halt_recorded: bool = True,
          terminal_record: bool = False,
          independent_gate_failure: bool = False,
          independent_condition_failure: bool = False) -> tuple[Path, Path]:
    """Use the shipped declarations, with a tiny runner record and no EDA."""
    canonical = yaml.safe_load(FLOW.read_text())
    declared = {str(s["id"]): s for s in canonical["steps"]}
    steps = []
    for sid, stage in (("7", "stage2"), ("22", "stage3")):
        original = declared[sid]
        steps.append({"id": int(sid), "name": original["name"],
                      "stage": stage,
                      "required_outputs": original["required_outputs"]})
    if independent_gate_failure:
        steps[0]["gate"] = {"files_exist": [
            "reports/phase2/gates/independent.flag"]}
    if independent_condition_failure:
        steps[0]["condition_owner"] = {"step": 6, "declaration": "route"}
        steps.insert(0, {"id": 6, "name": "Route declaration owner",
                         "stage": "stage2",
                         "required_outputs": ["reports/phase2/owner_record.json"],
                         "condition_declarations": {"route": {"files_exist": [
                             "reports/phase2/owner_decl.flag"]}}})
    steps.append({"id": 32, "name": declared["32"]["name"],
                  "stage": "stage3",
                  "required_outputs": ["reports/phase3/repair_gate.json"],
                  "gate": {"files_exist": ["reports/phase3/repair_accept.flag"]}})
    flow = tmp_path / "flow.yaml"
    flow.write_text(yaml.safe_dump({
        "version": 2, "flow_name": "halt_attribution",
        "total_steps": len(steps), "analog_steps": 0,
        "stages": [{"id": "stage2", "name": "pre-layout",
                    "steps": ([6, 7] if independent_condition_failure else [7])},
                   {"id": "stage3", "name": "backend", "steps": [22, 32]}],
        "steps": steps,
    }))
    project = tmp_path / "project"
    (project / "phase2/stage2/constraints").mkdir(parents=True)
    (project / "phase2/stage2/constraints/pvt_matrix.json").write_text("{}\n")
    (project / "reports/phase3").mkdir(parents=True)
    (project / "reports/phase3/repair_gate.json").write_text("{}\n")
    if independent_condition_failure:
        (project / "reports/phase2").mkdir(parents=True)
        (project / "reports/phase2/owner_record.json").write_text("{}\n")
    (project / "reports/orchestrator").mkdir(parents=True)
    (project / "reports/audit").mkdir(parents=True)
    repair_status = "FAIL" if halt_recorded else "PASS"
    repair_code = "REPAIR_REFUSED"
    (project / "reports/phase3/librelane_postroute_repair.json").write_text(
        json.dumps({"step": "32", "verdict": repair_status,
                    "code": repair_code, "reason": "repair limit violated"}))
    orchestrator = {"project": str(project),
                    "steps": [
                        {"name": "prelayout_signoff", "status": "PASS"},
                        {"name": "pnr", "status": "PASS"},
                        {"name": "postroute_repair_librelane",
                         "status": repair_status,
                         "detail": f"{repair_code}: repair limit violated"},
                        {"name": "canonicalize_artefacts",
                         "status": "PASS" if producer_ran else "NOT_MEASURED",
                         "reason_class": ("" if producer_ran else "upstream_failed")},
                    ]}
    if terminal_record:
        orchestrator.update({"producer": {"recipe_sha256": "fixture-recipe"},
                             "steps_verdict": "FAIL", "verdict": "FAIL"})
    else:
        orchestrator["program"] = "phase3_one_shot_runner"
    (project / "reports/orchestrator/phase3_one_shot.json").write_text(
        json.dumps(orchestrator))
    (project / "reports/audit/flow_declared_producer_run.json").write_text(
        json.dumps({"program": "flow_declared_producer_run",
                    "skipped": [{"step": "22",
                                 "target": "reports/phase2/gates/spef_extraction.json",
                                 "siblings": [declared["22"]["required_outputs"][0]],
                                 "why": "the run did not perform this step"}]}))
    return project, flow


def _audit(project: Path, flow: Path) -> tuple[subprocess.CompletedProcess, dict]:
    env = os.environ.copy()
    env.pop("VIBEIC_EDA_IMAGE_REPO", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, str(PROGRAM), str(project), "--flow-def", str(flow),
         "--json", str(project / "reports/audit/phase23_completion_audit.json")],
        cwd=project, env=env, text=True, capture_output=True, timeout=180)
    audit = json.loads((project / "reports/audit/phase23_completion_audit.json").read_text())
    return proc, audit


def _rows(audit: dict) -> dict[str, dict]:
    return {str(row["id"]): row for row in audit["steps"]}


@pytest.mark.parametrize("terminal_record", [False, True])
def test_uninvoked_producer_is_attributed_to_halting_step(tmp_path, terminal_record):
    project, flow = _case(tmp_path, terminal_record=terminal_record)
    proc, audit = _audit(project, flow)
    rows = _rows(audit)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert audit["run_status"] == "FAIL"
    assert rows["32"]["status"] == "FAIL"
    for sid in ("7", "22"):
        assert rows[sid]["status"] == "NOT_MEASURED", rows[sid]
        assert rows[sid]["reason_class"] == "upstream_failed", rows[sid]
        assert rows[sid]["cascade_note"] == "blocked-by-upstream(32)", rows[sid]
        assert any("fix step 32 first" in r for r in rows[sid]["reasons"])
        blocker = next(b for b in audit["blockers"] if str(b["step_id"]) == sid)
        assert "32" in blocker["derived_from"], blocker


@pytest.mark.parametrize("producer_ran,halt_recorded", [(True, True), (False, False)])
def test_missing_output_without_a_skipped_producer_and_halt_stays_fail(
        tmp_path, producer_ran, halt_recorded):
    project, flow = _case(tmp_path, producer_ran=producer_ran,
                          halt_recorded=halt_recorded)
    proc, audit = _audit(project, flow)
    rows = _rows(audit)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert audit["run_status"] == "FAIL"
    assert rows["7"]["status"] == "FAIL", rows["7"]
    assert rows["22"]["status"] == "FAIL", rows["22"]


def test_independent_gate_failure_survives_producer_halt(tmp_path):
    project, flow = _case(tmp_path, independent_gate_failure=True)
    proc, audit = _audit(project, flow)
    rows = _rows(audit)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert audit["run_status"] == "FAIL"
    independent = rows["7"]
    assert independent["status"] == "FAIL", independent
    assert independent["reason_class"] == "", independent
    assert any("reports/phase2/gates/independent.flag" in reason
               for reason in independent["reasons"]), independent
    assert not independent["cascade_note"], independent
    assert "producer_halt" not in (independent["output_binding"] or {})
    assert rows["22"]["status"] == "NOT_MEASURED", rows["22"]
    assert rows["22"]["cascade_note"] == "blocked-by-upstream(32)"


def test_independent_condition_failure_survives_producer_halt(tmp_path):
    project, flow = _case(tmp_path, independent_condition_failure=True)
    proc, audit = _audit(project, flow)
    rows = _rows(audit)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert rows["6"]["status"] == "PASS", rows["6"]
    independent = rows["7"]
    assert independent["status"] == "FAIL", independent
    assert independent["reason_class"] == "missing_artefact", independent
    assert any("route declaration is MISSING" in reason
               for reason in independent["reasons"]), independent
    assert independent["cascade_note"] == "blocked-by-upstream(6)"
    assert "producer_halt" not in (independent["output_binding"] or {})
    assert rows["22"]["status"] == "NOT_MEASURED", rows["22"]
