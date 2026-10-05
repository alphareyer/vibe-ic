"""Focused regression for the subservient catalog/pad contract."""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

PROJECT = Path(os.environ.get("JSUB_PROJECT", "/project"))


def _copy_project(tmp_path: Path) -> Path:
    dst = tmp_path / "run"
    shutil.copytree(PROJECT, dst)
    return dst


def test_base_catalog_gate_is_red_and_missing_declaration_is_fail_closed(tmp_path):
    import catalog_synth_safe_params_check as catalog
    import design_one_shot_runner as runner

    rc, report = catalog.run(PROJECT, None)
    assert rc == 1
    assert "CATALOG_REUSE_DECLARED_NOT_INSTANTIATED" in report["failure_codes"]
    assert report["reached_ips"] == []

    empty = tmp_path / "empty"
    (empty / "input" / "docs").mkdir(parents=True)
    (empty / "input" / "docs" / "README.md").write_text(
        "chip with no catalog declaration\n", encoding="utf-8")
    assert runner._declared_reused_ip(empty) is False


def test_explicit_alias_pair_resolves_the_sixteen_unmatched_bits(tmp_path):
    import renamed_interface_derive as derive
    import slot_pad_budget_check as pad

    project = _copy_project(tmp_path)
    manifest_path = project / "phase2/stage1/rtl/SOURCE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = derive.apply_to_manifest(project, manifest)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n",
                             encoding="utf-8")
    pairs = manifest["derived_pad_pairs"]
    assert {tuple(p["rtl"]) for p in pairs} == {
        ("i_sram_rdata",), ("o_sram_wdata",)}
    assert derive.check(project, {}, derived=pairs)["verdict"] == "PASS"
    out = project / "reports/slot-focused.json"
    rc = pad.main([str(project), "--json", str(out)])
    report = json.loads(out.read_text(encoding="utf-8"))
    assert rc == 0, report
    assert report["verdict"] == "FITS"
    arith = report["own_ring_arithmetic"]
    assert arith["signal_pads_placed_by_the_ring"] == arith["signal_pads_owed"]
    assert arith["signal_pads_owed_with_no_pad"] == 0


def test_alias_mutations_remain_fail_closed(tmp_path):
    import renamed_interface_derive as derive

    for mutation in ("missing", "width", "direction"):
        project = _copy_project(tmp_path / mutation)
        doc = project / "input/docs/L3_external_interface.md"
        text = doc.read_text(encoding="utf-8")
        if mutation == "missing":
            text = text.replace(" (or `o_sram_wdata`)", "")
            text = text.replace(" (or `i_sram_rdata`)", "")
        elif mutation == "width":
            text = text.replace("| 8-bit | output | 寫入資料", "| 16-bit | output | 寫入資料")
            text = text.replace("| 8-bit | input | 讀取資料", "| 16-bit | input | 讀取資料")
        else:
            text = text.replace("| 8-bit | input | 讀取資料", "| 8-bit | output | 讀取資料")
            text = text.replace("| 8-bit | output | 寫入資料", "| 8-bit | input | 寫入資料")
        doc.write_text(text, encoding="utf-8")
        result = derive.derive(project)
        assert result["pairs"] == [], mutation
        assert {u["port"] for u in result["unresolved"]} == {
            "i_sram_rdata", "o_sram_wdata"}, mutation
