"""Step 37.5ic keeps foundry text only when its whole cell is PDK-identical."""
from __future__ import annotations

import struct
from pathlib import Path

import _gds_geometry as geom
import _pdk_layer_authority as authority
import general_precheck as precheck
from _hostpaths import require_repo
from test_general_precheck import _project, _rect, _step, write_gds


def _rec(kind: int, dtype: int = 0, payload: bytes = b"") -> bytes:
    if len(payload) % 2:
        payload += b"\0"
    return struct.pack(">HBB", len(payload) + 4, kind, dtype) + payload


def _text(value: str) -> bytes:
    return b"".join((_rec(0x0C), _rec(0x0D, 2, struct.pack(">H", 58)),
                     _rec(0x16, 2, struct.pack(">H", 0)),
                     _rec(0x10, 3, struct.pack(">ii", 0, 0)),
                     _rec(0x19, 6, value.encode("ascii")), _rec(0x11)))


def _boundary() -> bytes:
    return b"".join((_rec(0x08), _rec(0x0D, 2, struct.pack(">H", 58)),
                     _rec(0x0E, 2, struct.pack(">H", 0)),
                     _rec(0x10, 3, struct.pack(">10i", 0, 0, 10, 0, 10, 10,
                                               0, 10, 0, 0)), _rec(0x11)))


def _place(gds: Path, item: bytes, cell: str) -> None:
    raw = gds.read_bytes()
    end = raw.find(_rec(0x07)) if cell == "lib_pad" else raw.rfind(_rec(0x07))
    assert end > 0
    gds.write_bytes(raw[:end] + item + raw[end:])


def _make(tmp_path: Path, *, geometry: bool = False,
          design_text: bool = False, modified: bool = False,
          declared: bool = False):
    volume = tmp_path / "pdk"
    library = volume / "libs.ref" / "io" / "gds" / "io.gds"
    write_gds(library, {"lib_pad": {"boundaries": [(34, 0,
                  _rect(0, 0, 100, 100))]}})
    _place(library, _text("PAD"), "lib_pad")
    if geometry:
        _place(library, _boundary(), "lib_pad")

    def design(path: Path) -> Path:
        write_gds(path, {
            "lib_pad": {"boundaries": [(34, 0, _rect(0, 0, 100, 100))]},
            "chip_top": {"srefs": [("lib_pad", 0, 0)]},
        })
        _place(path, _text("EDIT" if modified else "PAD"), "lib_pad")
        if geometry:
            _place(path, _boundary(), "lib_pad")
        if design_text:
            _place(path, _text("DESIGN"), "chip_top")
        return path

    answers = {"deliverable": "DIE", "top_cell": "chip_top",
               "database_unit_um": 0.001}
    if declared:
        answers["forbidden_layers"] = ["58/0"]
    project = _project(tmp_path / "run", design, answers)
    return project, volume, library


def _evaluate(monkeypatch, project: Path, volume: Path):
    # Exercise evaluate's real 37.5ic rung with a local, synthetic PDK volume.
    monkeypatch.setattr(precheck, "pdk_reader_for",
                        lambda *_: (authority.LocalReader(), "test volume"))
    monkeypatch.setattr(precheck._pdkauth, "resolve_volume",
                        lambda *_args, **_kw: (volume, "test volume", [str(volume)]))
    monkeypatch.setattr(precheck._pdkauth, "layer_table",
                        lambda *_args, **_kw: ({(34, 0)}, "test layer table", []))
    return _step(precheck.evaluate(project, runner=lambda *_: (2, "", "unrun")),
                 "General.ForbiddenLayers")


def test_foundry_text_is_counted_and_gds_bytes_are_preserved(tmp_path, monkeypatch):
    project, volume, library = _make(tmp_path)
    gds = project / "phase3/stage4/gds/chip_top.gds"
    before = gds.read_bytes()
    assert library.is_file()
    ev = _evaluate(monkeypatch, project, volume)
    assert ev.verdict == precheck.PASS, ev.evidence
    assert ev.measured["unmapped_layers"] == []
    rows = ev.measured["foundry_text_annotations"]
    assert [(r["layer"], r["cell"], r["text_count"]) for r in rows] == [
        ("58/0", "lib_pad", 1)]
    assert rows[0]["pdk_gds"].startswith(str(library) + "#sha256=")
    assert gds.read_bytes() == before


def test_checked_in_gds_cell_content_is_matched_by_bytes(tmp_path):
    real = require_repo("vibe-ic-marketplace", "plugins", "vibe-ic", "programs",
                        "tests", "fixtures", "density_fill", "filled.gds")
    volume = tmp_path / "pdk"
    library = volume / "libs.ref" / "cells" / "gds" / "cells.gds"
    library.parent.mkdir(parents=True)
    library.write_bytes(real.read_bytes())
    _place(library, _text("PAD"), "DENSITY_FILL_FIXTURE")
    design = tmp_path / "design.gds"
    design.write_bytes(library.read_bytes())
    lay = geom.read_layout(design)
    ev = precheck.StepEvidence("General.ForbiddenLayers", "forbidden", 6,
                               "technology", precheck.NOT_DETERMINED, "fail")
    precheck._step_forbidden_layers(
        ev, geom.layers_used(lay), [],
        allowed=set(geom.layers_used(geom.read_layout(real))),
        authority="fixture layer table", layout=lay, volume=volume,
        reader=authority.LocalReader())
    assert ev.verdict == precheck.PASS, ev.evidence
    assert [(r["layer"], r["cell"], r["text_count"]) for r in
            ev.measured["foundry_text_annotations"]] == [
                ("58/0", "DENSITY_FILL_FIXTURE", 1)]


def test_undefined_geometry_design_text_and_modified_foundry_cell_fail(
        tmp_path, monkeypatch):
    for case in ("geometry", "design_text", "modified", "declared"):
        project, volume, _ = _make(tmp_path / case, **{case: True})
        ev = _evaluate(monkeypatch, project, volume)
        assert ev.verdict == precheck.FAIL, (case, ev.evidence)
        assert "58/0" in ev.evidence, case
        if case != "declared":
            assert ev.measured["unmapped_layers"] == ["58/0"], case
        if case == "geometry":
            assert ev.measured["foundry_text_annotations"][0]["pdk_gds"]
            assert any(r.get("kind") == "BOUNDARY" for r in
                       ev.measured["unverified_undefined_elements"])
        if case == "design_text":
            assert any(r["cell"] == "chip_top" for r in
                       ev.measured["unverified_undefined_elements"])
        if case == "modified":
            assert any(r["cell"] == "lib_pad" and r["pdk_gds"] is None for r in
                       ev.measured["unverified_undefined_elements"])


def test_missing_pdk_proof_keeps_text_forbidden(tmp_path, monkeypatch):
    project, volume, library = _make(tmp_path)
    library.unlink()
    ev = _evaluate(monkeypatch, project, volume)
    assert ev.verdict == precheck.FAIL
    assert ev.measured["unmapped_layers"] == ["58/0"]
