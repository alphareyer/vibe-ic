"""A completed zero DRC route has a real, receipt-bound empty output."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import flow_compliance_check as flow
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
