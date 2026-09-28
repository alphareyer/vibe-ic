"""Allowed declaration choices disclose divergence from a design-input example."""
import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import spec_declaration_emit as emitter


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
