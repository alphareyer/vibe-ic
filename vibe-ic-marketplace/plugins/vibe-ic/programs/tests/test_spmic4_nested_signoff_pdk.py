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


# ── review wave 57 (SPMFOUNDRYPDK) ──────────────────────────────────────────
import json

import _foundry_signoff_pdk as fsp
import pytest

_MEMBERS = ("mask_spec.json", "wat_plan.json", "corner_test_vectors.json")
_HD = "phase3/stage4/foundry_handoff"


def _kit(tmp_path, pdk, flow_text=None, omit_key=False):
    """A GF180 sign-off flow beside a complete kit declaring `pdk`."""
    flow = tmp_path / "phase3/stage3/pnr"
    flow.mkdir(parents=True, exist_ok=True)
    (flow / "pnr.tcl").write_text(
        flow_text if flow_text is not None else
        "read_liberty /foss/pdks/ciel/gf180mcu/versions/abc123/"
        "gf180mcuD/libs.ref/cell/lib/a.lib\n")
    hd = tmp_path / _HD
    hd.mkdir(parents=True, exist_ok=True)
    for member in _MEMBERS:
        body = {"doc": member} if omit_key else {"pdk": pdk}
        (hd / member).write_text(json.dumps(body))
    (hd / "scribe_line_layout.PENDING_FOUNDRY.txt").write_text(
        f"# pdk: {pdk}\n")
    return tmp_path


def _run(tree, tmp_path):
    out = tmp_path / "gate.json"
    rc = check.main([str(tree), "--json", str(out)])
    return rc, json.loads(out.read_text())


def _rules(report):
    return [item["rule"] for item in report["findings"]]


def test_gate_main_fails_a_signoff_flow_beside_a_wrong_process_kit(tmp_path):
    """The gate's own verdict, not the helper's: deleting the wiring in
    main() must turn this red (review wave 57 MAJOR)."""
    rc, report = _run(_kit(tmp_path / "t", "sky130"), tmp_path)
    assert rc == 1
    assert report["verdict"] == "FAIL"
    mism = [f for f in report["findings"]
            if f["rule"] == "FOUNDRY_HANDOFF_PDK_MISMATCH"]
    assert len(mism) == 3
    for member in _MEMBERS:
        assert sum(f"{_HD}/{member}" in f["message"] for f in mism) == 1


def test_gate_main_passes_the_same_kit_labelled_with_the_signoff_pdk(tmp_path):
    rc, report = _run(_kit(tmp_path / "t", "gf180mcuD"), tmp_path)
    assert rc == 0
    assert report["verdict"] == "PASS"
    assert "FOUNDRY_HANDOFF_PDK_MISMATCH" not in _rules(report)


def test_a_waived_pdk_mismatch_is_waived_never_pass(tmp_path):
    tree = _kit(tmp_path / "t", "sky130")
    (tree / "waivers.json").write_text(json.dumps({"waived_steps": [
        {"id": "foundry_handoff", "ticket": "T-1", "reason": "r"}]}))
    rc, report = _run(tree, tmp_path)
    assert report["verdict"] == "WAIVED"
    assert rc == 0
    # The waived disagreement stays on the record beside the waiver.
    assert "STEP_WAIVED" in _rules(report)
    assert _rules(report).count("FOUNDRY_HANDOFF_PDK_MISMATCH") == 3


def test_a_member_without_a_pdk_key_is_undeclared_not_skipped(tmp_path):
    rc, report = _run(_kit(tmp_path / "t", "sky130", omit_key=True), tmp_path)
    assert rc == 1
    assert report["verdict"] == "FAIL"
    undeclared = [f for f in report["findings"]
                  if f["rule"] == "FOUNDRY_HANDOFF_PDK_UNDECLARED"]
    assert len(undeclared) == 3
    assert all(f["severity"] == "ERROR" for f in undeclared)


@pytest.mark.parametrize("text", [
    # a versioned root at end of file (no delimiter after the variant)
    "set pdk_root /foss/pdks/ciel/gf180mcu/versions/abc/gf180mcuD",
    "set pdk_root /foss/pdks/volare/gf180mcu/versions/abc/gf180mcuD",
])
def test_a_versioned_root_at_end_of_file_names_its_variant(text):
    assert fsp.names_in_text(text) == {"gf180mcuD"}


@pytest.mark.parametrize("text", [
    "export PDK_ROOT=/foss/pdks/ciel/gf180mcu/versions/abc/\n"
    "read_lef $PDK_ROOT/gf180mcuD/libs.ref/x/lef/a.lef\n",
    "export PDK_ROOT=/foss/pdks/volare/sky130/versions/abc/\n",
    "export PDK_ROOT=/foss/pdks/ciel/\n",
])
def test_a_store_root_is_never_reported_as_a_pdk(text, tmp_path):
    """'ciel'/'volare' are content-addressed stores, not processes."""
    assert not (fsp.names_in_text(text) & {"ciel", "volare"})
    tree = tmp_path / "t"
    flow = tree / "phase3/stage3/pnr"
    flow.mkdir(parents=True)
    (flow / "pnr.tcl").write_text(text)
    assert fsp.pdk_from_signoff_flow(tree) is None


def test_a_store_root_plus_a_direct_path_is_the_direct_pdk(tmp_path):
    tree = tmp_path / "t"
    flow = tree / "phase3/stage3/pnr"
    flow.mkdir(parents=True)
    (flow / "pnr.tcl").write_text(
        "read_lef /foss/pdks/gf180mcuD/libs.ref/x/lef/a.lef\n"
        "set pdk_root /foss/pdks/ciel/gf180mcu/versions/abc/gf180mcuD")
    assert fsp.pdk_from_signoff_flow(tree) == "gf180mcuD"


@pytest.mark.parametrize("flow_text, state", [
    ("# no pdk path at all\n", "NOT_DETERMINED:absent"),
    ("read_lef /foss/pdks/sky130A/libs.ref/x/lef/a.lef\n"
     "read_lef /foss/pdks/ihp-sg13g2/libs.ref/x/lef/b.lef\n",
     "NOT_DETERMINED:ambiguous['ihp-sg13g2', 'sky130A']"),
])
def test_an_undeterminable_signoff_pdk_is_reported(tmp_path, flow_text, state):
    rc, report = _run(_kit(tmp_path / "t", "sky130A", flow_text=flow_text),
                      tmp_path)
    assert report["signoff_pdk"] is None
    assert report["pdk_consistency"] == state
    info = [f for f in report["findings"]
            if f["rule"] == "FOUNDRY_HANDOFF_PDK_NOT_DETERMINED"]
    assert len(info) == 1 and info[0]["severity"] == "INFO"
    # Not determined is never a comparison that passed.
    assert "FOUNDRY_HANDOFF_PDK_MISMATCH" not in _rules(report)


@pytest.mark.parametrize("label, rc_expected", [("sky130", 1),
                                                 ("gf180mcuD", 0)])
def test_every_verdict_records_the_consistency_state(tmp_path, label,
                                                    rc_expected):
    rc, report = _run(_kit(tmp_path / "t", label), tmp_path)
    assert rc == rc_expected
    assert report["signoff_pdk"] == "gf180mcuD"
    assert report["pdk_consistency"] == "CHECKED"
