"""Focused controls for the issued native synthesis census binding."""

import hashlib
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "synth_netlist_check.py"
sys.path.insert(0, str(SCRIPT.parent))
import synth_netlist_check as snc  # noqa: E402


NETLIST = """module top(input wire a, output wire y);
  sky130_fd_sc_hd__inv_2 u0 (.A(a), .Y(y));
endmodule
"""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path) -> dict:
    project = tmp_path
    synth = project / "phase2/stage2/synth"
    native = project / "phase3/librelane/02-yosys-synthesis"
    synth.mkdir(parents=True)
    native.mkdir(parents=True)

    audited = synth / "netlist.v"
    mapped = synth / "subservient_synth.v"
    generic = synth / "netlist_yosys.v"
    state = native / "state_out.json"
    native_netlist = native / "native.nl.v"
    receipt = native / "vibeic_receipt.json"
    audited.write_text(NETLIST)
    mapped.write_text(NETLIST)
    generic.write_text(NETLIST.replace("sky130_fd_sc_hd__inv_2", "$_NOT_"))
    native_netlist.write_text(NETLIST)
    state.write_text(json.dumps({"nl": str(native_netlist)}))
    receipt.write_text(json.dumps({
        "input": {"step": "Yosys.Synthesis"},
        "sha256": {
            "state_out.json": _sha(state),
            "native.nl.v": _sha(native_netlist),
        },
    }))
    (synth / "synth_inputs.json").write_text(json.dumps({
        "librelane_synthesis": {
            "folder": "phase3/librelane/02-yosys-synthesis",
            "mapped": "phase2/stage2/synth/subservient_synth.v",
            "netlist_sha256": _sha(mapped),
            "receipt_sha256": _sha(receipt),
            "schema": "vibe-ic/librelane-synthesis-handoff/1",
            "top": "top",
        },
        "netlist": "subservient_synth.v",
    }))
    return {
        "audited": audited,
        "mapped": mapped,
        "generic": generic,
        "state": state,
        "native": native_netlist,
        "receipt": receipt,
        "sidecar": synth / "synth_inputs.json",
    }


def _audit(paths: dict):
    total, counts = snc.count_cell_instances(paths["audited"].read_text())
    return snc.audit_cell_census(paths["audited"], counts, total)


def _rewrite_sidecar(paths: dict, binding):
    doc = json.loads(paths["sidecar"].read_text())
    doc["librelane_synthesis"] = binding
    paths["sidecar"].write_text(json.dumps(doc))


def test_native_binding_uses_receipt_covered_state_and_netlist(tmp_path):
    paths = _fixture(tmp_path)
    findings, info = _audit(paths)
    assert findings == []
    assert info["status"] == "AGREE"
    assert info["source"] == str(paths["native"])
    assert info["tool_total_cells"] == info["audited_total_cells"] == 1


def test_state_or_selected_native_mutation_refuses_without_receipt_refresh(tmp_path):
    paths = _fixture(tmp_path)
    paths["state"].write_text(json.dumps({"nl": str(paths["native"]), "changed": True}))
    findings, info = _audit(paths)
    assert info["status"] == "NATIVE_BINDING_REFUSED"
    assert [finding.category for finding in findings] == ["TOOL_NETLIST_BINDING_CHANGED"]

    paths = _fixture(tmp_path / "native-change")
    paths["native"].write_text(NETLIST + "// changed native bytes\n")
    findings, info = _audit(paths)
    assert info["status"] == "NATIVE_BINDING_REFUSED"
    assert [finding.category for finding in findings] == ["TOOL_NETLIST_BINDING_CHANGED"]


def test_malformed_binding_and_state_are_stable_refusals(tmp_path):
    paths = _fixture(tmp_path / "binding")
    _rewrite_sidecar(paths, [])
    findings, info = _audit(paths)
    assert info["status"] == "NATIVE_BINDING_REFUSED"
    assert [finding.category for finding in findings] == ["TOOL_NETLIST_BINDING_CHANGED"]

    paths = _fixture(tmp_path / "receipt")
    receipt = json.loads(paths["receipt"].read_text())
    del receipt["sha256"]["state_out.json"]
    paths["receipt"].write_text(json.dumps(receipt))
    binding = json.loads(paths["sidecar"].read_text())["librelane_synthesis"]
    binding["receipt_sha256"] = _sha(paths["receipt"])
    _rewrite_sidecar(paths, binding)
    findings, info = _audit(paths)
    assert info["status"] == "NATIVE_BINDING_REFUSED"
    assert [finding.category for finding in findings] == ["TOOL_NETLIST_BINDING_CHANGED"]

    paths = _fixture(tmp_path / "state")
    paths["state"].write_text("[]")
    findings, info = _audit(paths)
    assert info["status"] == "NATIVE_BINDING_REFUSED"
    assert [finding.category for finding in findings] == ["TOOL_NETLIST_BINDING_CHANGED"]


def test_escaping_or_symlinked_native_folder_refuses(tmp_path):
    paths = _fixture(tmp_path / "escape")
    binding = json.loads(paths["sidecar"].read_text())["librelane_synthesis"]
    binding["folder"] = "../outside"
    _rewrite_sidecar(paths, binding)
    findings, info = _audit(paths)
    assert info["status"] == "NATIVE_BINDING_REFUSED"
    assert [finding.category for finding in findings] == ["TOOL_NETLIST_BINDING_CHANGED"]

    paths = _fixture(tmp_path / "symlink")
    alias = paths["native"].parent / "alias"
    alias.symlink_to(paths["native"], target_is_directory=True)
    binding = json.loads(paths["sidecar"].read_text())["librelane_synthesis"]
    binding["folder"] = "phase3/librelane/alias"
    _rewrite_sidecar(paths, binding)
    findings, info = _audit(paths)
    assert info["status"] == "NATIVE_BINDING_REFUSED"
    assert [finding.category for finding in findings] == ["TOOL_NETLIST_BINDING_CHANGED"]
