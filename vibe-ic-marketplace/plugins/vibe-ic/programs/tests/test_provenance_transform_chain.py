"""The real Phase-3 GDS and SPEF finishing call sites retain tool bytes."""
import hashlib
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import phase3_one_shot_runner as R  # noqa: E402
import provenance_output_hash_completeness_check as C  # noqa: E402
import foundry_handoff_package_check as H  # noqa: E402


def _sha(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _rows(project):
    return [json.loads(line) for line in (project / "provenance.jsonl").read_text().splitlines()]


def _project(tmp_path):
    project = tmp_path / "run"
    pnr = project / "phase3/stage3/pnr"
    pnr.mkdir(parents=True)
    (pnr / "unit.def").write_text("DESIGN unit ;\nEND DESIGN\n")
    (project / "provenance.jsonl").write_text("")
    R.set_invocation_provenance_sink(project)
    return project


def test_magic_fill_retains_stream_and_declares_finished_head(tmp_path, monkeypatch):
    project = _project(tmp_path)
    pdk = SimpleNamespace(drc_deck=None, stdcell_marker_layer=None,
                          dummy_fill=None, same_net_heal=None,
                          port_label_restore=None)
    monkeypatch.setattr(R, "_layout_basis", lambda *a: ("basis", None))
    monkeypatch.setattr(R._ga, "gate_passed", lambda *a: True)
    monkeypatch.setattr(R, "_vacuous_on_unrouted", lambda *a: None)
    monkeypatch.setattr(R, "_streamout_top", lambda *a: ("unit", ""))
    monkeypatch.setattr(R, "publish_database_unit_declaration",
                        lambda *a: {"verdict": "PASS", "reason": "fixture"})
    monkeypatch.setattr(R, "publish_tapeout_declarations",
                        lambda *a: {"published": [], "not_determined": [],
                                    "already_answered": []})

    def magic(_project, _top, _pdk, _container, path):
        path.write_bytes(b"streamed layout")
        R._log_invocation("magic gds write", 0, 1, outputs=[path])
        return True, "streamed"

    def fill(_project, _top, _pdk, path, _container):
        path.write_bytes(path.read_bytes() + b" + fill")
        return True, "filled"

    monkeypatch.setattr(R, "_magic_def_to_gds", magic)
    monkeypatch.setattr(R, "_gds_grid_snap", lambda *a: (False, "no snap"))
    monkeypatch.setattr(R, "_die_finishing", lambda *a: (False, "no seal"))
    monkeypatch.setattr(R, "_step34_gds_tool_arm", lambda *a: None)
    monkeypatch.setattr(R, "_density_metal_fill", fill)
    monkeypatch.setattr(R, "_die_density_fill", lambda *a: (False, "no die fill"))
    monkeypatch.setattr(R, "_restore_port_labels_if_missing",
                        lambda *a: (False, "no labels"))
    monkeypatch.setattr(R, "_gds_substance_gate", lambda *a: None)
    try:
        result = R._step_gds_direct(project, "unit", pdk, "fake")
    finally:
        R.set_invocation_provenance_sink(None)
    assert result.status == "PASS", result.detail
    rows = _rows(project)
    tool = next(r for r in rows if r.get("record") == "invocation")
    source, source_hash = next(iter(tool["outputs"].items()))
    target = "phase3/stage3/pnr/unit.gds"
    assert source != target, (source, target)
    chain = [r for r in rows if r.get("record") == "declared_transform"]
    assert [r["producing_step"] for r in chain] == [
        "gds:density_metal_fill", "gds:finishing"]
    assert chain[0]["inputs"] == {source: source_hash}
    assert chain[1]["inputs"] == chain[0]["outputs"]
    assert (project / source).read_bytes() == b"streamed layout"
    assert (project / target).read_bytes() == b"streamed layout + fill"
    assert chain[-1]["outputs"] == {target: _sha(b"streamed layout + fill")}
    assert H.layout_member_sources(project) == {"unit.gds": project / target}
    assert C.audit(project)[0] == "PASS"
    (project / target).write_bytes(b"edited without a transform")
    assert "PROVENANCE_HASH_MISMATCH" in {f.rule for f in C.audit(project)[1]}


def test_reextracted_spef_retains_openrcx_bytes_and_declares_augmentation(tmp_path,
                                                                         monkeypatch):
    project = _project(tmp_path)
    extracted = project / "phase3/stage3/extracted"
    extracted.mkdir(parents=True)
    output = extracted / "unit.spef"
    pdk = SimpleNamespace(tech_lef=str(tmp_path / "tech.lef"),
                          cell_lef=str(tmp_path / "cell.lef"),
                          liberty=str(tmp_path / "cells.lib"), metal_prefix="metal")
    (tmp_path / "tech.lef").write_text("VERSION 5.8 ;")
    monkeypatch.setattr(R, "_to_container_path", lambda path, *a: str(path))
    monkeypatch.setattr(R, "_def_reopen_extra_lefs_c", lambda *a: [])
    monkeypatch.setattr(R, "_extra_lef_read_block", lambda *a: "")
    monkeypatch.setattr(R, "_openrcx_ruleset_declaration",
                        lambda *a: {"status": "ABSENT", "detail": "fixture"})
    monkeypatch.setattr(R, "_write_rcx_declaration_record", lambda *a: None)

    emitted = []

    def fake_openroad(_container, _cmd, *, marker, outputs):
        deck = Path(marker).read_text()
        path = Path(re.findall(r"write_spef\s+([^\s}]+)", deck)[-1])
        data = f"raw parasitics {len(emitted) + 1}".encode()
        emitted.append(data)
        path.write_bytes(data)
        R._log_invocation("openroad extraction", 0, 1, outputs=outputs)
        return 0, "", ""

    def augment(_def, _lef, path, _notes):
        path.write_bytes(path.read_bytes() + b" + coupling")
        return True

    monkeypatch.setattr(R, "_docker_exec", fake_openroad)
    monkeypatch.setattr(R, "_emit_spef_coupling_augment", augment)
    try:
        assert R._emit_spef(project, "unit", pdk, "fake", output, [])
    finally:
        R.set_invocation_provenance_sink(None)
    rows = _rows(project)
    tool = next(r for r in rows if r.get("record") == "invocation")
    source, source_hash = next(iter(tool["outputs"].items()))
    target = "phase3/stage3/extracted/unit.spef"
    assert source != target, (source, target)
    finished = next(r for r in rows if r.get("producing_step") == "spef:extraction_finishing")
    assert (project / source).read_bytes() == b"raw parasitics 1"
    assert output.read_bytes() == b"raw parasitics 1 + coupling"
    assert finished["inputs"] == {source: source_hash}
    assert finished["outputs"] == {target: _sha(output.read_bytes())}
    assert sorted(p.name for p in extracted.glob("*.spef")) == ["unit.spef"]
    assert C.audit(project)[0] == "PASS"
    R.set_invocation_provenance_sink(project)
    try:
        assert R._emit_spef(project, "unit", pdk, "fake", output, [])
    finally:
        R.set_invocation_provenance_sink(None)
    newer = _rows(project)[-1]
    newer_source = next(iter(newer["inputs"]))
    assert newer_source != source
    assert (project / source).read_bytes() == b"raw parasitics 1"
    assert (project / newer_source).read_bytes() == b"raw parasitics 2"
    assert output.read_bytes() == b"raw parasitics 2 + coupling"
    assert C.audit(project)[0] == "PASS"
    output.write_bytes(b"unlogged rewrite")
    assert "PROVENANCE_HASH_MISMATCH" in {f.rule for f in C.audit(project)[1]}
