"""A completed zero DRC route has a real, receipt-bound empty output."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import flow_compliance_check as flow
import eda_report_audit as audit
import phase3_one_shot_runner as runner
import step_write_ledger as ledger


REPORT = "phase3/stage3/pnr/routed_router.drc.rpt"
LOG = ("[INFO DRT-0195] Start detail routing.\n"
       "[INFO DRT-0199] Number of violations = 0.\n"
       "[INFO DRT-0198] Complete detail routing.\n")


def _route(project: Path) -> Path:
    pnr = project / "phase3/stage3/pnr"
    pnr.mkdir(parents=True)
    report = pnr / "routed_router.drc.rpt"
    report.write_bytes(b"")  # OpenROAD's clean -output_drc output
    (pnr / "openroad.log").write_text(LOG)
    routed = pnr / "routed.def"
    routed.write_text("VERSION 5.8 ;\nDESIGN widget ;\nEND DESIGN\n")
    assert runner._write_router_drc_receipt(pnr, routed, LOG)
    return report


def test_completed_zero_route_is_produced_in_ledger_and_flow(tmp_path):
    report = _route(tmp_path)
    entry = ledger.snapshot(tmp_path)["entries"][REPORT]
    assert entry["produced"] is True, entry
    assert flow._live_artefact_state(report)[0] is True
    result = flow.StepResult(21, "Routing", "stage3", "PASS", evidence=[REPORT])
    assert flow._evidence_integrity_scan(tmp_path, result).status == "PASS"


def test_changed_receipt_cannot_certify_empty_output(tmp_path):
    report = _route(tmp_path)
    receipt = report.with_name("routed_router.drc.receipt.json")
    body = json.loads(receipt.read_text())
    body["current_invocation_count"] = 1
    receipt.write_text(json.dumps(body))
    assert ledger.snapshot(tmp_path)["entries"][REPORT]["produced"] is False
    assert flow._live_artefact_state(report)[0] is False
    result = flow.StepResult(21, "Routing", "stage3", "PASS", evidence=[REPORT])
    assert flow._evidence_integrity_scan(tmp_path, result).status == "FAIL"


def test_step_snapshot_symlink_uses_the_canonical_zero_receipt(tmp_path):
    """The source run's DRC gate discovers Step 21's link to the real report."""
    report = _route(tmp_path)
    alias = (tmp_path / "steps/phase3/stage3/21_routing_global_detailed"
             / "routed_router.drc.rpt")
    alias.parent.mkdir(parents=True)
    alias.symlink_to(os.path.relpath(report, alias.parent))
    assert flow._live_artefact_state(alias)[0] is True
    step = flow.StepResult(21, "Routing", "stage3", "PASS", evidence=[
        str(alias.relative_to(tmp_path))])
    assert flow._evidence_integrity_scan(tmp_path, step).status == "PASS"
    sibling = tmp_path / "reports/phase3/drc_router.rpt"
    sibling.parent.mkdir(parents=True)
    body = ("# OpenROAD detailed_route DRC summary\n"
            "# Tool: openroad detailed_route (drt)\n"
            "violation report: 0\ntotal violations: 0\n"
            "categories: spacing width density antenna via enclosure\n")
    body += "    Completing 100% with 0 violations.\n" * 70
    sibling.write_text(body)
    out = tmp_path / "router_audit.json"
    cmd = [sys.executable, str(Path(audit.__file__)), str(tmp_path),
           "--mode", "drc", "--under", REPORT,
           "--under", "reports/phase3/drc_router.rpt",
           "--json", str(out)]
    run = subprocess.run(cmd, capture_output=True, text=True)
    result = json.loads(out.read_text())
    assert run.returncode == 0, result
    assert result["passed"] is True
    evidence = result["summary"]["empty_report_evidence"]
    assert str(alias.relative_to(tmp_path)) in evidence
    assert evidence[str(alias.relative_to(tmp_path))] == [
        "phase3/stage3/pnr/routed_router.drc.receipt.json"]
    receipt = report.with_name("routed_router.drc.receipt.json")
    record = json.loads(receipt.read_text())
    record["current_invocation_count"] = 1
    receipt.write_text(json.dumps(record))
    refused = subprocess.run(cmd, capture_output=True, text=True)
    refusal = json.loads(out.read_text())
    assert refused.returncode == 1
    assert refusal["passed"] is False
    assert any(item["rule"] == "DRC_EMPTY_NOT_MEASURED"
               for item in refusal["findings"])
