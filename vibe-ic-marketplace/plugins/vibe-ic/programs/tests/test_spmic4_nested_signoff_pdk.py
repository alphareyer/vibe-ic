"""A nested, content-addressed PDK path names its variant, not its cache root."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import foundry_handoff_pack_gen as pack
import foundry_handoff_package_check as check


def test_nested_signoff_pdk_overrides_spec_target(tmp_path):
    flow = tmp_path / "phase3/stage3/pnr"
    flow.mkdir(parents=True)
    (flow / "pnr.tcl").write_text(
        "read_liberty /foss/pdks/ciel/gf180mcu/versions/abc123/"
        "gf180mcuD/libs.ref/gf180mcu_fd_sc_mcu7t5v0/lib/a.lib\n"
        "read_lef /foss/pdks/gf180mcuD/libs.ref/gf180mcu_fd_sc_mcu7t5v0/lef/a.lef\n"
        "set pdk_root /foss/pdks/ciel/gf180mcu/versions/abc123/gf180mcuD\n")
    # Binary flow artefacts are not sign-off scripts. Decoding their bytes as
    # replacement text could manufacture an unrelated path-shaped candidate.
    (flow / "routed.gds").write_bytes(b"\0/foss/pdks/sky130A/libs.ref/fake\0")
    doc = tmp_path / "phase1/generated_docs/L19_CONSTRAINTS_PDK.json"
    doc.parent.mkdir(parents=True)
    doc.write_text('{"doc_id":"L19","fields":{"pdk_target":"sky130"}}')
    assert pack._pdk_from_signoff_flow(tmp_path) == "gf180mcuD"
    pdk, _node, mismatch = pack._resolve_pdk_and_node(tmp_path, None, None)
    assert (pdk, mismatch) == ("gf180mcuD", "sky130")


def test_distinct_nested_and_direct_pdks_remain_ambiguous(tmp_path):
    flow = tmp_path / "phase3/stage3/pnr"
    flow.mkdir(parents=True)
    (flow / "pnr.tcl").write_text(
        "read_liberty /foss/pdks/ciel/gf180mcu/versions/abc123/"
        "gf180mcuD/libs.ref/cell/lib/a.lib\n"
        "read_lef /foss/pdks/sky130A/libs.ref/cell/lef/a.lef\n")
    assert pack._pdk_from_signoff_flow(tmp_path) is None


def test_package_gate_rejects_wrong_process_in_each_json_member(tmp_path):
    hd = tmp_path / "phase3/stage4/foundry_handoff"
    hd.mkdir(parents=True)
    for member in ("mask_spec.json", "wat_plan.json", "corner_test_vectors.json"):
        (hd / member).write_text('{"pdk":"sky130"}')
    findings = check.pdk_consistency_findings(tmp_path, "gf180mcuD")
    assert len(findings) == 3
    assert {item["rule"] for item in findings} == {
        "FOUNDRY_HANDOFF_PDK_MISMATCH"}
    for member in hd.glob("*.json"):
        member.write_text('{"pdk":"gf180mcuD"}')
    assert check.pdk_consistency_findings(tmp_path, "gf180mcuD") == []
