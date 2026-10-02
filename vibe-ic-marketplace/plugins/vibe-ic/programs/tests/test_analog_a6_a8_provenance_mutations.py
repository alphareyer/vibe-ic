"""Reverse controls for the Default A6/A8 byte bindings."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analog_a6_block_pv_check as A6  # noqa: E402
import analog_a8_hardmacro_gen_check as A8  # noqa: E402


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_a6_clean_witness_turns_red_when_current_layout_bytes_change(tmp_path):
    project = tmp_path / "p"
    bdir = project / "phase3" / "analog" / "blk"
    bdir.mkdir(parents=True)
    (project / "phase3/analog/analog_block_list.json").write_text(
        json.dumps({"blocks": ["blk"]}))
    gds = bdir / "blk.gds"
    gds.write_bytes(b"gds-v1")
    digest = _sha(gds)
    (bdir / "drc.report").write_text(
        f"violations: 0\nlayout_path: phase3/analog/blk/blk.gds\n"
        f"layout_sha256: {digest}\n")
    (bdir / "comp.json").write_text(json.dumps({
        "result": "match", "layout_sha256": digest}))
    assert A6._check_block(project, "blk")[0] == "PASS"
    gds.write_bytes(b"gds-v2")
    status, findings = A6._check_block(project, "blk")
    assert status == "FAIL"
    assert any(f["rule"] == "A6_PV_LAYOUT_MUTATED" for f in findings)


def test_a8_manifest_turns_red_when_m1_view_bytes_change(tmp_path):
    project = tmp_path / "p"
    hdir = project / "phase3" / "analog" / "hardmacro" / "blk"
    hdir.mkdir(parents=True)
    source = project / "phase3/analog/blk/blk.gds"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source-gds")
    views = {}
    for suffix, body in ((".lef", b"lef"), (".lib", b"lib"),
                         (".gds", b"macro-gds"), (".v", b"module blk;")):
        path = hdir / f"blk{suffix}"
        path.write_bytes(body)
        views[suffix] = {"path": str(path.relative_to(project)),
                         "sha256": _sha(path)}
    manifest = {"schema": "vibe-ic/analog_a8_views/1",
                "producer": "analog_a8_hardmacro_emit", "block": "blk",
                "source_gds": str(source.relative_to(project)),
                "source_gds_sha256": _sha(source), "views": views}
    (hdir / "a8_views_provenance.json").write_text(json.dumps(manifest))
    assert A8._view_provenance_findings(project, "blk") == []
    (hdir / "blk.v").write_bytes(b"module blk_mutated;")
    findings = A8._view_provenance_findings(project, "blk")
    assert any(f["rule"] == "A8_VIEW_MUTATED" and f["rel_path"].endswith("blk.v")
               for f in findings)
