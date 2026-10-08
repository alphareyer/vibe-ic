from __future__ import annotations

import json
from pathlib import Path
import sys
import types

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import execution_backend_producers as backend_producers
import execution_backend_worker as backend_worker
import flow_declared_producer_run as declared


def _step15_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    pnr = project / "phase3/stage3/pnr"
    pnr.mkdir(parents=True)
    # `owed` requires a sibling output to prove that this step actually ran.
    (pnr / "floorplan.def").write_text("synthetic floorplan\n")
    (pnr / "pdn.done").write_text("synthetic pdn\n")
    return project


def test_current_step_producer_scope_stamps_step15_and_skips_other_steps(tmp_path):
    project = _step15_project(tmp_path)

    record = declared.run_for_step(project, "15", timeout=30)

    assert record["scope"] == "current-step"
    assert record["step_id"] == "15"
    assert record["owed"] == 1
    assert record["executed"] == 1
    assert record["documents_now_present"] == 1
    assert all(row["step"] == "15" for row in record["results"])
    assert not any(row["step"] == "26" for row in record["results"])

    report = project / "reports/phase2/gates/stage2_compliance.json"
    assert report.is_file()
    assert json.loads(report.read_text())["invoked_as"] == "producer"


def test_current_step_replaces_audit_authored_target_with_producer_receipt(tmp_path):
    project = _step15_project(tmp_path)
    report = project / "reports/phase2/gates/stage2_compliance.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({"invoked_as": "audit", "verdict": "PASS"}) + "\n")

    record = declared.run_for_step(project, "15", timeout=30)

    assert record["owed"] == 1
    assert record["executed"] == 1
    assert json.loads(report.read_text())["invoked_as"] == "producer"


def test_current_step_scope_filters_before_owed_and_run(tmp_path, monkeypatch):
    clauses = [
        {"step": "15", "program": "stage2_compliance", "target": "15.json"},
        {"step": "26", "program": "antenna_report_check", "target": "26.json"},
    ]
    owed_seen = []
    run_seen = []

    def fake_owed(project, selected):
        owed_seen.extend(selected)
        return list(selected), []

    def fake_run_one(project, row, timeout):
        run_seen.append(row["step"])
        return {"step": row["step"], "executed": True,
                "target_exists_after": True}

    monkeypatch.setattr(declared, "declared_producer_clauses", lambda: clauses)
    monkeypatch.setattr(declared, "owed", fake_owed)
    monkeypatch.setattr(declared, "_run_one", fake_run_one)

    record = declared.run_for_step(tmp_path, "15")

    assert [row["step"] for row in owed_seen] == ["15"]
    assert run_seen == ["15"]
    assert record["step_id"] == "15"
    assert record["executed"] == 1


def test_backend_producer_orders_current_declared_outputs_before_consumer(
        tmp_path, monkeypatch):
    events = []

    def fake_native(project, params):
        events.append("native")
        return {"verdict": "PASS", "detail": "fixture"}

    def fake_declared(project, step_id):
        events.append(("declared", str(step_id)))
        return {"scope": "current-step", "step_id": str(step_id),
                "verdict": "PRODUCED", "executed": 1}

    monkeypatch.setattr(backend_producers, "produce_pnr", fake_native)
    # Inject the existing producer module seam instead of stubbing the new
    # private helper.  On the parent revision `produce` never imports this
    # module, so the control observes the missing current-step invocation as a
    # value (the event list), rather than failing only because a new symbol is
    # absent.
    fake_module = types.SimpleNamespace(run_for_step=fake_declared)
    monkeypatch.setitem(sys.modules, "flow_declared_producer_run", fake_module)

    result = backend_producers.produce(tmp_path, {"step_id": "15"})

    assert events == ["native", ("declared", "15")]
    assert result["current_step_declared_producers"]["step_id"] == "15"


def test_real_backend_worker_runs_declared_producer_before_consumer(
        tmp_path, monkeypatch):
    inputs = tmp_path / "inputs"
    project_input = inputs / "project"
    project_input.mkdir(parents=True)
    (project_input / "fixture.sdc").write_text("create_clock -period 10 [get_ports clk]\n")
    outputs = tmp_path / "outputs"
    events = []

    def fake_native(project, params):
        events.append("native")
        pnr = project / "phase3/stage3/pnr"
        pnr.mkdir(parents=True, exist_ok=True)
        (pnr / "floorplan.def").write_text("synthetic floorplan\n")
        (pnr / "pdn.done").write_text("synthetic pdn\n")
        return {"verdict": "PASS", "detail": "fixture native"}

    def observed_consumer(project, row):
        report = project / "reports/phase2/gates/stage2_compliance.json"
        assert json.loads(report.read_text())["invoked_as"] == "producer"
        events.append("consumer")
        return {"status": "PASS", "ledger": []}

    monkeypatch.setattr(backend_producers, "produce_pnr", fake_native)
    monkeypatch.setattr(backend_worker, "produce", backend_producers.produce)
    monkeypatch.setattr(backend_worker, "evaluate", observed_consumer)
    monkeypatch.setenv("VIBEIC_EXECUTION_BINDING", json.dumps({
        "source_sha": "fixture", "required_gates": []}))

    result = backend_worker.execute(
        inputs, outputs, step_id="15",
        params={"input_contract": [{"from": "external", "path": "fixture.sdc"}]})

    assert events == ["native", "consumer"]
    assert result["verdict"] == "PASS"
    assert "reports/phase2/gates/stage2_compliance.json" in result["outputs"]
