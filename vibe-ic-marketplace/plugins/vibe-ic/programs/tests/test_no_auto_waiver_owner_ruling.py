"""FPGA absence is measured as absence; only a dated owner decision can waive."""
import json
import subprocess
import sys
from pathlib import Path
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import flow_compliance_check as audit  # noqa: E402
import waivers_materialize as materialize  # noqa: E402


def _project(tmp_path):
    project = tmp_path / "processor_cpu"
    fpga = project / "reports/phase2/fpga"
    fpga.mkdir(parents=True)
    (fpga / "quartus_map_audit.json").write_text(json.dumps({
        "verdict": "SKIP", "sof_present": False,
        "skip_reason": "not_attempted",
    }))
    orchestrator = project / "reports/orchestrator"
    orchestrator.mkdir()
    (orchestrator / "phase2_one_shot.json").write_text(json.dumps({
        "steps": [{"name": "fpga_compile", "status": "SKIP",
                   "detail": "fpga/processor_cpu.qsf missing"}]
    }))
    return project


def _board_step(sid):
    return {"id": sid, "name": "FPGA board", "stage": "stage1",
            "required_outputs": ["phase2/stage1/fpga/final/*.sof"],
            "gate": {"files_exist": ["phase2/stage1/fpga/final/*.sof"]}}


def test_no_contract_creates_no_waiver_and_reports_both_board_rows(tmp_path):
    project = _project(tmp_path)
    count, ids = materialize.materialize(project)
    assert (count, ids) == (0, [])
    assert not (project / "waivers.json").exists()
    waivers = audit._load_waivers(project)
    assert waivers == {}
    for sid in audit._FPGA_BOARD_STEP_IDS:
        row = audit.check_step(project, _board_step(sid), waivers)
        assert row.status == "NOT_MEASURED"
        assert row.excluded_from_verdict
        assert "2026-09-25" in " ".join(row.reasons)


def test_materializer_refuses_even_a_supplied_machine_proposal(tmp_path,
                                                               monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setattr(materialize, "sanctioned_auto_waivers", lambda _p: {
        6: {"id": 6, "reason": "Machine proposes skipping absent board test",
            "approver": "field-agent-attest (fpga-board cap-gap tier)",
            "ticket": "machine-proposal", "review_required": True,
            "_env_unavailable": True}})
    assert materialize.materialize(project) == (0, [])
    assert not (project / "waivers.json").exists()


def test_machine_waiver_is_reported_and_not_honoured(tmp_path):
    project = _project(tmp_path)
    (project / "waivers.json").write_text(json.dumps({"waived_steps": [{
        "id": 31, "reason": "Machine generated a physical verification waiver",
        "approver": "field-agent-attest (fpga-board cap-gap tier)",
        "approved_at": "2026-09-25", "auto_synthesized": True,
        "ticket": "machine-ticket", "review_required": True,
    }]}))
    waivers = audit._load_waivers(project)
    assert 31 not in waivers
    assert any("OWNER" in item and "31" in item
               for item in audit._ENV_WAIVER_REJECTIONS)


def test_cli_audit_reports_refusal_and_keeps_the_step_red(tmp_path):
    project = _project(tmp_path)
    (project / "waivers.json").write_text(json.dumps({"waived_steps": [{
        "id": 31, "reason": "Machine generated a physical verification waiver",
        "approver": "field-agent-attest (signoff tier)",
        "auto_synthesized": True, "review_required": True,
        "ticket": "machine-ticket",
    }]}))
    flow = tmp_path / "flow.yaml"
    flow.write_text(yaml.safe_dump({"steps": [{
        "id": 31, "name": "Physical verification", "stage": "stage4",
        "required_outputs": ["reports/physical/absent.json"],
        "gate": {"files_exist": ["reports/physical/absent.json"]},
    }]}))
    report = tmp_path / "audit.json"
    run = subprocess.run([sys.executable, str(Path(audit.__file__)),
                          str(project), "--flow-def", str(flow),
                          "--json", str(report)], capture_output=True,
                         text=True)
    assert report.is_file(), run.stdout + run.stderr
    doc = json.loads(report.read_text())
    assert doc["steps"][0]["status"] == "FAIL"
    assert any("OWNER WAIVER REFUSED" in note
               for note in doc.get("advisories", []))


def test_dated_owner_record_is_waived_not_pass(tmp_path):
    project = _project(tmp_path)
    (project / "waivers.json").write_text(json.dumps({"waived_steps": [{
        "id": 31, "reason": "Owner accepts the specifically named deferred check",
        "approver": "reyerchu", "approved_at": "2026-09-28",
        "owner_statement": "I approve the stated step 31 deferral for this run.",
        "ticket": "owner-review-31", "review_required": True,
    }]}))
    waivers = audit._load_waivers(project)
    assert 31 in waivers
    row = audit.check_step(project, {
        "id": 31, "name": "Physical verification", "stage": "stage4",
        "required_outputs": ["reports/physical/absent.json"],
        "gate": {"files_exist": ["reports/physical/absent.json"]},
    }, waivers)
    assert row.status == "PASS_WITH_WAIVERS"
    assert row.status != "PASS"


def test_a9_skip_hardware_without_owner_record_is_unmeasured(tmp_path):
    project = tmp_path / "analog"
    project.mkdir()
    step = {"id": "A9", "name": "Analog bench correlation",
            "stage": "stage_analog"}
    row = audit.check_step(project, step, {}, skip_hardware=True)
    assert row.status == "NOT_MEASURED", (row.status, row.reasons)
    assert row.reason_class == "input_absent"
    assert any("skip-hardware" in reason and "bench" in reason
               for reason in row.reasons)


def test_a9_skip_hardware_with_accepted_owner_record_is_waived(tmp_path):
    project = tmp_path / "analog"
    project.mkdir()
    (project / "waivers.json").write_text(json.dumps({"waived_steps": [{
        "id": "A9", "reason": "Owner defers analog bench measurement",
        "approver": "reyerchu", "approved_at": "2026-09-28",
        "owner_statement": "I approve this specific A9 bench deferral for this run.",
        "ticket": "owner-review-A9", "review_required": True,
    }]}))
    waivers = audit._load_waivers(project)
    assert "A9" in waivers
    row = audit.check_step(project, {"id": "A9",
                                     "name": "Analog bench correlation",
                                     "stage": "stage_analog"},
                           waivers, skip_hardware=True)
    assert row.status == "PASS_WITH_WAIVERS", (row.status, row.reasons)
    assert any("approver: reyerchu" in reason for reason in row.reasons)


@pytest.mark.parametrize("change", [
    {"approver": "field-agent-attest"},
    {"approved_at": ""},
    {"approved_at": "not-a-date"},
    {"owner_statement": ""},
    {"auto_synthesized": True},
])
def test_owner_signature_fields_cannot_be_replaced_by_a_machine_marker(
        tmp_path, change):
    project = _project(tmp_path)
    entry = {
        "id": 31, "reason": "Specifically scoped physical check deferral",
        "approver": "reyerchu", "approved_at": "2026-09-28",
        "owner_statement": "I approve this specific deferred check for this run.",
        "ticket": "owner-review-31", "review_required": True,
    }
    entry.update(change)
    (project / "waivers.json").write_text(json.dumps({"waived_steps": [entry]}))
    assert 31 not in audit._load_waivers(project)
    assert any("OWNER WAIVER REFUSED" in item
               for item in audit._ENV_WAIVER_REJECTIONS)
