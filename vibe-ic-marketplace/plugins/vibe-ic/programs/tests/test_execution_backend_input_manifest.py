from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import execution_backend_worker as worker
import execution_policy as policy
import _flow_yaml


def test_resolve_input_contract_aggregates_all_missing_declarations(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    declarations = [
        {"path": "phase2/stage2/synth/missing_a.v"},
        {"path": "reports/phase2/gates/missing_b.json"},
    ]

    assert worker.resolve_input_contract(project, declarations) == [
        "phase2/stage2/synth/missing_a.v",
        "reports/phase2/gates/missing_b.json",
    ]


def test_execute_missing_manifest_inputs_does_not_call_native_producer(
        tmp_path, monkeypatch):
    inputs = tmp_path / "inputs"
    (inputs / "project").mkdir(parents=True)
    outputs = tmp_path / "outputs"
    declarations = [
        {"path": "phase2/stage2/synth/missing_a.v"},
        {"path": "reports/phase2/gates/missing_b.json"},
    ]
    calls = []
    monkeypatch.setattr(worker, "produce",
                        lambda project, params: calls.append("produce"))
    monkeypatch.setenv("VIBEIC_EXECUTION_BINDING", json.dumps({
        "required_gates": [],
    }))

    result = worker.execute(
        inputs, outputs, step_id="15",
        params={"resolved_input_manifest": {
            "declarations": declarations, "files": {}, "missing": []}})

    assert set(result["missing_inputs"]) == {
        "phase2/stage2/synth/missing_a.v",
        "reports/phase2/gates/missing_b.json",
    }
    assert calls == []


def test_manifest_hash_mismatch_is_fail_closed(tmp_path, monkeypatch):
    inputs = tmp_path / "inputs"
    project = inputs / "project"
    project.mkdir(parents=True)
    artifact = project / "phase2/stage2/synth/netlist.v"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("module top; endmodule\n")
    calls = []
    monkeypatch.setattr(worker, "produce",
                        lambda project, params: calls.append("produce"))
    monkeypatch.setenv("VIBEIC_EXECUTION_BINDING", json.dumps({
        "required_gates": [],
    }))

    result = worker.execute(
        inputs, tmp_path / "outputs", step_id="15",
        params={"resolved_input_manifest": {
            "declarations": [{"path": "phase2/stage2/synth/netlist.v"}],
            "files": {"phase2/stage2/synth/netlist.v": "0" * 64},
            "missing": [],
        }})

    assert "phase2/stage2/synth/netlist.v" in result["missing_inputs"]
    assert result["verdict"] == "NOT_MEASURED"
    assert calls == []


def test_malformed_manifest_schema_does_not_call_producer_or_consumer(
        tmp_path, monkeypatch):
    inputs = tmp_path / "inputs"
    (inputs / "project").mkdir(parents=True)
    calls = []
    monkeypatch.setattr(worker, "produce",
                        lambda project, params: calls.append("produce"))
    monkeypatch.setattr(worker, "evaluate",
                        lambda project, row: calls.append("evaluate"))
    monkeypatch.setenv("VIBEIC_EXECUTION_BINDING", json.dumps({
        "required_gates": [],
    }))

    result = worker.execute(
        inputs, tmp_path / "outputs", step_id="15",
        params={"resolved_input_manifest": {
            "declarations": None, "files": {}, "missing": []}})

    assert result["missing_inputs"] == ["resolved_input_manifest"]
    assert result["verdict"] == "NOT_MEASURED"
    assert calls == []


def test_manifest_accepts_pathless_external_check_none_declaration():
    assert worker._valid_resolved_manifest({
        "declarations": [{
            "from": "external", "check": "none",
            "what": "the PDK device models / tech decks",
        }],
        "files": {}, "missing": [],
    })


def test_manifest_accepts_numeric_upstream_step_declaration():
    assert worker._valid_resolved_manifest({
        "declarations": [{
            "from": 12, "path": "phase2/stage2/synth/netlist.v",
        }],
        "files": {}, "missing": [],
    })


def test_backend_manifest_transport_rebinds_and_restores_component_argv():
    @dataclass(frozen=True)
    class FakeComponent:
        argv: tuple[str, ...]

    @dataclass(frozen=True)
    class FakeArm:
        arm_id: str
        components: tuple[FakeComponent, ...]

    original_argv = (
        "/usr/bin/python3", "execution_backend_worker.py", "{inputs}",
        "{outputs}", "--step-id", "15", "--params-json",
        json.dumps({"pdk_name": "gf180mcuD"}, sort_keys=True),
    )
    arm = FakeArm("backend_15", (FakeComponent(original_argv),))

    class FakeRegistry:
        def __init__(self):
            self._adapters = {arm.arm_id: arm}

        def adapters(self, step_id):
            return [self._adapters[arm.arm_id]] if str(step_id) == "15" else []

    registry = FakeRegistry()
    runtime = {"registry": registry}
    manifest = {
        "declarations": [{"from": 12, "path": "phase2/stage2/synth/netlist.v"}],
        "files": {"phase2/stage2/synth/netlist.v": "a" * 64},
        "missing": [],
    }

    with policy._backend_manifest_parameters(runtime, "15", manifest):
        current = registry.adapters("15")[0].components[0].argv
        encoded = json.loads(current[current.index("--params-json") + 1])
        assert encoded["resolved_input_manifest"] == manifest
        assert encoded["resolved_input_manifest"]["declarations"][0]["from"] == 12

    assert registry.adapters("15")[0].components[0].argv == original_argv


def test_directory_declaration_requires_nonempty_contained_file(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    template = project / "input/submission_template"
    template.mkdir(parents=True)
    (template / "README.md").write_text("template\n")
    assert worker.resolve_input_contract(
        project, ["input/submission_template"]) == []

    empty = project / "input/empty_template"
    empty.mkdir()
    assert worker.resolve_input_contract(
        project, ["input/empty_template"]) == ["input/empty_template"]


def test_issuer_manifest_aggregates_missing_row_inputs_before_dispatch(
        tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()

    class EmptyRegistry:
        def adapters(self, step_id):
            return []

    monkeypatch.setattr(_flow_yaml, "load", lambda: {"steps": [{
        "id": "15",
        "required_inputs": [
            {"from": "external", "path": "phase2/stage2/synth/missing_a.v"},
            {"from": "external", "path": "reports/phase2/gates/missing_b.json"},
        ],
    }]})
    parameters = {}
    files = policy._fixed_inputs(
        {"project": project, "registry": EmptyRegistry()}, "15",
        parameters=parameters)

    assert files == {}
    assert set(parameters["resolved_input_manifest"]["missing"]) >= {
        "phase2/stage2/synth/missing_a.v",
        "reports/phase2/gates/missing_b.json",
        "input/submission_template",
    }
