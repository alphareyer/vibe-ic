"""A completed zero DRC route has a real, receipt-bound empty output."""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

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
    receipt = report.with_name("routed_router.drc.receipt.json")
    # The alias route itself, independent of any walk order.
    assert audit._empty_router_drc_receipt(alias) == receipt
    out = tmp_path / "router_audit.json"
    # NAME the alias as the subject. A walk would merge it with the canonical
    # report by inode and keep whichever rglob reaches first -- a filesystem
    # name-hash accident (review wave 57: red 3/3 on a host that lists
    # `phase3` before `steps`), which left the alias unexercised there.
    cmd = [sys.executable, str(Path(audit.__file__)), str(tmp_path),
           "--mode", "drc", "--subject", str(alias.relative_to(tmp_path)),
           "--subject", "reports/phase3/drc_router.rpt",
           "--json", str(out)]
    run = subprocess.run(cmd, capture_output=True, text=True)
    result = json.loads(out.read_text())
    assert run.returncode == 0, result
    assert result["passed"] is True
    evidence = result["summary"]["empty_report_evidence"]
    assert evidence == {str(alias.relative_to(tmp_path)): [
        "phase3/stage3/pnr/routed_router.drc.receipt.json"]}
    record = json.loads(receipt.read_text())
    record["current_invocation_count"] = 1
    receipt.write_text(json.dumps(record))
    refused = subprocess.run(cmd, capture_output=True, text=True)
    refusal = json.loads(out.read_text())
    assert refused.returncode == 1
    assert refusal["passed"] is False
    assert any(item["rule"] == "DRC_EMPTY_NOT_MEASURED"
               for item in refusal["findings"])


def test_router_iteration_report_does_not_vote_on_final_route(tmp_path):
    """The source broad scope contains a zero-byte intermediate iteration."""
    report = _route(tmp_path)
    report.with_name("routed_router.drc.iter0.rpt").write_bytes(b"")
    sibling = tmp_path / "reports/phase3/drc_router.rpt"
    sibling.parent.mkdir(parents=True)
    body = ("# OpenROAD detailed_route DRC summary\n"
            "# Tool: openroad detailed_route (drt)\n"
            "violation report: 0\ntotal violations: 0\n"
            "categories: spacing width density antenna via enclosure\n")
    sibling.write_text(body + "    Completing 100% with 0 violations.\n" * 70)
    out = tmp_path / "broad_audit.json"
    cmd = [sys.executable, str(Path(audit.__file__)), str(tmp_path),
           "--mode", "drc", "--under", "phase3/stage3/pnr",
           "--under", "reports/phase3/drc_router.rpt", "--json", str(out)]
    run = subprocess.run(cmd, capture_output=True, text=True)
    result = json.loads(out.read_text())
    assert run.returncode == 0, result
    assert result["passed"] is True
    assert not any("iter0" in path for path in result["summary"]["empty_reports"])
    assert result["summary"]["ignored_intermediate_reports"] == [
        "phase3/stage3/pnr/routed_router.drc.iter0.rpt"]


def test_step_alias_cannot_borrow_another_projects_receipt(tmp_path):
    foreign = _route(tmp_path / "foreign")
    alias = (tmp_path / "subject/steps/phase3/stage3/21_routing_global_detailed"
             / "routed_router.drc.rpt")
    alias.parent.mkdir(parents=True)
    alias.symlink_to(os.path.relpath(foreign, alias.parent))
    assert audit._empty_router_drc_receipt(alias) is None
    assert flow._live_artefact_state(alias)[0] is False


@pytest.mark.parametrize("order", ["sorted", "reverse"])
def test_walk_order_cannot_change_which_receipt_certifies_the_route(
        tmp_path, monkeypatch, order):
    """Both walk orders: the alias and the canonical report are one physical
    file, and whichever path survives discovery is certified by the same
    canonical receipt."""
    report = _route(tmp_path)
    alias = (tmp_path / "steps/phase3/stage3/21_routing_global_detailed"
             / "routed_router.drc.rpt")
    alias.parent.mkdir(parents=True)
    alias.symlink_to(os.path.relpath(report, alias.parent))
    walk = pathlib.Path.rglob
    monkeypatch.setattr(pathlib.Path, "rglob", lambda self, pat: iter(sorted(
        walk(self, pat), key=str, reverse=order == "reverse")))
    result = audit._check_drc(tmp_path)
    evidence = result.summary["empty_report_evidence"]
    assert len(evidence) == 1, evidence
    (key, receipts), = evidence.items()
    assert key in (REPORT, str(alias.relative_to(tmp_path)))
    assert receipts == ["phase3/stage3/pnr/routed_router.drc.receipt.json"]
