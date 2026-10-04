import hashlib
import json
from pathlib import Path

import pytest

import synth_handoff_netlist_check as H


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path) -> tuple[Path, Path, dict]:
    project = tmp_path / "project"
    folder = project / "phase3/librelane/02-yosys-synthesis"
    rtl_dir = project / "phase2/stage1/rtl"
    folder.mkdir(parents=True)
    rtl_dir.mkdir(parents=True)
    rtl = rtl_dir / "spm.v"
    rtl.write_text("module spm(input wire a, output wire y); assign y=a; endmodule\n")
    netlist = folder / "spm.nl.v"
    netlist.write_text("module spm(input a, output y); assign y=a; endmodule\n")
    reports = folder / "reports"
    reports.mkdir()
    (reports / "stat.json").write_text(json.dumps({"modules": {"\\spm": {"num_submodules": 0}}}))
    resolved = project / "phase3/librelane/synthesis_resolved.json"
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text("{}\n")
    old_root = Path("/old/root")
    old_rtl = str(old_root / "phase2/stage1/rtl/spm.v")
    old_state = str(old_root / "phase3/librelane/01-yosys-jsonheader/spm.h.json")
    state_input = folder / "input-state.json"
    state_input.write_text("{}\n")
    mapped_state = project / "phase3/librelane/01-yosys-jsonheader/spm.h.json"
    mapped_state.parent.mkdir(parents=True)
    mapped_state.write_text(state_input.read_text())
    state = {"nl": str(old_root / "phase3/librelane/02-yosys-synthesis/spm.nl.v"),
             "metrics": {"design__instance_unmapped__count": 0,
                         "synthesis__check_error__count": 0}}
    (folder / "state_out.json").write_text(json.dumps(state))
    cfg = {"meta": {"step": "Yosys.Synthesis"}, "PDK": "gf180mcuD",
           "DESIGN_NAME": "spm", "VERILOG_FILES": [old_rtl],
           "SYNTH_TIEHI_CELL": "hi", "SYNTH_TIELO_CELL": "lo"}
    (folder / "config.json").write_text(json.dumps(cfg))
    inp = {"step": "Yosys.Synthesis", "config": _sha(resolved),
           "config_files": {old_rtl: _sha(rtl)},
           "state_files": {old_state: _sha(state_input)}}
    receipt = {"input": inp, "sha256": {
        "state_out.json": _sha(folder / "state_out.json"),
        "config.json": _sha(folder / "config.json"),
        "reports/stat.json": _sha(reports / "stat.json"),
        "spm.nl.v": _sha(netlist)}}
    (folder / "vibeic_receipt.json").write_text(json.dumps(receipt))
    return project, folder, receipt


def _patch_external(monkeypatch, project: Path):
    import _rtl_include_hub as hub
    import librelane_contract as lc
    monkeypatch.setattr(H, "_receipt_pdk_mounts", lambda folder, receipt: [])
    monkeypatch.setattr(H, "check", lambda *args: {"verdict": "PASS"})
    monkeypatch.setattr(lc, "phase2_pdk", lambda project: ("gf180mcuD", ""))
    monkeypatch.setattr(lc, "config_file_hashes", lambda config, mounts: {})
    monkeypatch.setattr(hub, "silicon_rtl_selection",
                        lambda rtl_dir: [project / "phase2/stage1/rtl/spm.v"])


def test_real_native_consumer_rebases_same_tree(tmp_path, monkeypatch):
    project, folder, _ = _fixture(tmp_path)
    _patch_external(monkeypatch, project)
    raw, _, _ = H._native_synthesis(project, folder, "spm")
    assert raw == folder / "spm.nl.v"


def test_real_native_consumer_rejects_foreign_root(tmp_path, monkeypatch):
    project, folder, receipt = _fixture(tmp_path)
    _patch_external(monkeypatch, project)
    state_path = folder / "state_out.json"
    state = json.loads(state_path.read_text())
    state["nl"] = "/foreign/root/phase3/librelane/02-yosys-synthesis/spm.nl.v"
    state_path.write_text(json.dumps(state))
    receipt["sha256"]["state_out.json"] = _sha(state_path)
    (folder / "vibeic_receipt.json").write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        H._native_synthesis(project, folder, "spm")


def test_real_native_consumer_rejects_duplicate_rtl(tmp_path, monkeypatch):
    project, folder, receipt = _fixture(tmp_path)
    _patch_external(monkeypatch, project)
    config_path = folder / "config.json"
    config = json.loads(config_path.read_text())
    config["VERILOG_FILES"].append(config["VERILOG_FILES"][0])
    config_path.write_text(json.dumps(config))
    receipt["sha256"]["config.json"] = _sha(config_path)
    (folder / "vibeic_receipt.json").write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="duplicated"):
        H._native_synthesis(project, folder, "spm")


def test_read_only_consumer_verifies_without_writing(tmp_path, monkeypatch):
    project, folder, _ = _fixture(tmp_path)
    mapped = project / "phase2/stage2/synth/spm_synth.v"
    mapped.parent.mkdir(parents=True)
    mapped.write_bytes((folder / "spm.nl.v").read_bytes())
    _patch_external(monkeypatch, project)

    def fail_write(*args, **kwargs):
        raise AssertionError("read-only consumer must not write producer evidence")

    monkeypatch.setattr(H, "write_json", fail_write)
    result = H.verify_handoff(project, folder, mapped, "spm")
    assert result["verdict"] == "PASS"
    assert result["read_only"] is True
    assert result["canonical_checked"] is False
