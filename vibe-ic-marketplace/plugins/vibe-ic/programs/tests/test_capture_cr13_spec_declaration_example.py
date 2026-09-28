"""Allowed declaration choices disclose divergence from a design-input example."""
import json
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import spec_declaration_emit as emitter
import spec_required_artifact_check as artifact_gate


def _project(tmp_path, name, field, first, second, *, conflict=False):
    root = tmp_path / name
    docs = root / "input/docs"
    docs.mkdir(parents=True)
    second_note = "(golden)" if conflict else ""
    (docs / "declaration.md").write_text(
        "The Plugin MUST declare `plugin_output/declaration.json` before authoring:\n\n"
        "| Field | Required | Example |\n|---|---|---|\n"
        f"| `{field}` | Yes | `{first}`(golden) / `{second}`{second_note} |\n")
    contract, why, _ = emitter.select_contract(root)
    assert why == "OK"
    return root, contract


def _verify(root, contract, declaration):
    path = root / "plugin_output/declaration.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(declaration))
    return emitter.verify_declaration(root, contract, path)


def test_other_allowed_encoding_discloses_without_blocking(tmp_path):
    root, contract = _project(tmp_path, "vector_codec", "integer_encoding",
                              "signed_2c", "unsigned")
    same = _verify(root, contract, {"integer_encoding": "signed_2c"})
    assert same["verdict"] == "PASS"
    different = _verify(root, contract, {"integer_encoding": "unsigned"})
    assert different["verdict"] == "DISCLOSE"
    assert different["spec_example_disclosures"][0]["field"] == "integer_encoding"
    assert not different["spec_example_disclosures"][0]["reason_present"]
    assert emitter.main([str(root), "--verify"]) == 0
    saved = json.loads((root / "reports/phase2/gates/spec_declaration_verify.json").read_text())
    assert saved["verdict"] == "DISCLOSE"
    reasoned = _verify(root, contract, {
        "integer_encoding": "unsigned",
        "declaration_rationale": {"integer_encoding": "Chosen for the unsigned interface"}})
    assert reasoned["verdict"] == "PASS"
    assert reasoned["spec_example_disclosures"][0]["reason_present"]


def test_second_design_and_conflicting_input_are_disclosed(tmp_path):
    root, contract = _project(tmp_path, "packet_codec", "wire_order",
                              "little", "big")
    assert _verify(root, contract, {"wire_order": "big"})["verdict"] == "DISCLOSE"
    root2, contract2 = _project(tmp_path, "packet_mux", "wire_order",
                                "little", "big", conflict=True)
    report = _verify(root2, contract2, {"wire_order": "little"})
    assert report["verdict"] == "OWNER_REVIEW"
    assert report["spec_example_conflicts"][0]["values"] == ["big", "little"]


def test_numeric_example_matches_numeric_declaration_without_weakening_provenance(tmp_path):
    root, contract = _project(tmp_path, "pipeline", "latency_cycles", "1", "2")
    report = _verify(root, contract, {"latency_cycles": 1})
    assert report["verdict"] == "PASS"
    assert report["spec_example_disclosures"] == []
    assert emitter._same_declared_value(1, "1") is False
    assert emitter._same_declared_value(1, True) is False
    assert _verify(root, contract, {"latency_cycles": 2})["verdict"] == "DISCLOSE"


@pytest.mark.parametrize("example,declared,other", [
    ("true", True, False),
    ("false", False, True),
])
def test_boolean_example_matches_only_the_same_json_boolean(
    tmp_path, example, declared, other
):
    root, contract = _project(tmp_path, "boolean_choice", "signed_mode",
                              example, str(other).lower())
    same = _verify(root, contract, {"signed_mode": declared})
    assert same["verdict"] == "PASS"
    assert same["spec_example_disclosures"] == []
    different = _verify(root, contract, {"signed_mode": other})
    assert different["verdict"] == "DISCLOSE"
    assert different["spec_example_disclosures"][0]["declared"] is other
    assert emitter._same_declared_value(declared, example) is False
    assert emitter._same_declared_value(declared, int(declared)) is False


def test_artifact_gate_preserves_a_disclosed_spec_example_difference(tmp_path, capsys):
    root, contract = _project(tmp_path, "declared_order", "wire_order",
                              "little", "big")
    declaration = {"wire_order": "big"}
    verified = _verify(root, contract, declaration)
    assert verified["verdict"] == "DISCLOSE"
    assert artifact_gate.main([str(root)]) == 0
    report = json.loads((root / "reports/phase2/gates/spec_required_artifacts.json").read_text())
    assert report["verdict"] == "PASS"
    assert report["failed_count"] == 0
    assert report["advisory_status"] == "DISCLOSE"
    assert report["advisory_count"] == 1
    item = report["results"][0]
    assert item["status"] == item["substance_status"] == "DISCLOSE"
    assert item["spec_example_disclosures"] == verified["spec_example_disclosures"]
    assert item["spec_example_disclosures"][0] == {
        "field": "wire_order", "declared": "big", "spec_example": "little",
        "source": contract["source"], "reason": None, "reason_present": False}
    assert artifact_gate.main([str(root), "--preflight"]) == 0
    assert "ALL 1 spec-declared artifact(s) already present" in capsys.readouterr().out


def test_optional_conflicting_examples_are_reported_and_gate_keeps_owner_review(tmp_path):
    root = tmp_path / "optional_conflict"
    docs = root / "input/docs"
    docs.mkdir(parents=True)
    (docs / "declaration.md").write_text(
        "The Plugin MUST declare `plugin_output/declaration.json` before authoring:\n\n"
        "| Field | Required | Example |\n|---|---|---|\n"
        "| `mode` | Yes | `fast` |\n"
        "| `wire_order` | No | `little`(golden) / `big`(golden) |\n")
    contract, why, _ = emitter.select_contract(root)
    assert why == "OK"
    report = _verify(root, contract, {"mode": "fast"})
    assert report["verdict"] == "OWNER_REVIEW"
    assert report["spec_example_conflicts"] == [{
        "field": "wire_order", "values": ["big", "little"],
        "source": contract["source"]}]
    path = root / "plugin_output/declaration.json"
    status, reason, source = artifact_gate._substance_of(
        root, "plugin_output/declaration.json", path)
    assert (status, source) == ("OWNER_REVIEW", "CONTRACT")
    assert "conflicting examples" in reason
    assert artifact_gate.main([str(root)]) == 0
    gate_report = json.loads((root / "reports/phase2/gates/spec_required_artifacts.json").read_text())
    assert gate_report["verdict"] == "PASS"
    assert gate_report["advisory_status"] == "OWNER_REVIEW"
    assert gate_report["results"][0]["status"] == "OWNER_REVIEW"


def test_required_conflict_survives_artifact_gate_status(tmp_path):
    root, contract = _project(tmp_path, "ordered_stream", "wire_order",
                              "little", "big", conflict=True)
    _verify(root, contract, {"wire_order": "little"})
    path = root / "plugin_output/declaration.json"
    status, _, source = artifact_gate._substance_of(
        root, "plugin_output/declaration.json", path)
    assert (status, source) == ("OWNER_REVIEW", "CONTRACT")
    assert artifact_gate.main([str(root)]) == 0
    gate_report = json.loads((root / "reports/phase2/gates/spec_required_artifacts.json").read_text())
    assert gate_report["advisory_status"] == "OWNER_REVIEW"
    assert gate_report["results"][0]["status"] == "OWNER_REVIEW"
