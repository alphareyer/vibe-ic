"""A stale technology transcription is refreshable; an operator answer is not."""
from __future__ import annotations

import json
from types import SimpleNamespace
from pathlib import Path
import sys

_PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROGRAMS))

import _tapeout_declaration as TD  # noqa: E402
import phase3_one_shot_runner as R  # noqa: E402


def _project(tmp_path: Path, answer: float, technology: dict | None) -> Path:
    declaration = tmp_path / TD.DECLARATION_REL
    declaration.parent.mkdir(parents=True)
    doc = {"answers": {"database_unit_um": answer}}
    if technology is not None:
        doc[TD.TECHNOLOGY_KEY] = {"database_unit_um": technology}
    declaration.write_text(json.dumps(doc))
    return tmp_path


def _measure(monkeypatch, project: Path):
    monkeypatch.setattr(R, "_read_technology_text",
                        lambda *_: "DATABASE MICRONS 2000 ;\nMANUFACTURINGGRID 0.005 ;")
    monkeypatch.setattr(R, "_pdk_stream_database_unit_um", lambda *_: 0.001)
    monkeypatch.setattr(R._pl, "reports_dir", lambda p: p / "reports")
    pdk = SimpleNamespace(name="test-pdk", tech_lef="tech.lef",
                          cell_gds="cells.gds")
    return R.publish_database_unit_declaration(
        project, pdk, "unused", project / "routed.def")


def test_stale_technology_fact_is_replaced_by_measured_stream(monkeypatch, tmp_path):
    project = _project(tmp_path, 0.0005, {
        "value": 0.0005, "source": "tech.lef:40",
        "statement": "DATABASE MICRONS 2000", "pdk": "test-pdk"})
    (project / "routed.def").write_text("UNITS DISTANCE MICRONS 2000 ;\n")
    got = _measure(monkeypatch, project)
    doc, err = TD.load(project / TD.DECLARATION_REL)
    assert err is None
    assert got["published"] is True
    assert got["superseded_technology_fact"]["source"] == "tech.lef:40"
    assert TD.answer(doc, "database_unit_um") == 0.001
    fact = doc[TD.TECHNOLOGY_KEY]["database_unit_um"]
    assert fact["source"] == "cells.gds:UNITS"
    assert fact["value"] == 0.001


def test_unprovenanced_operator_answer_is_not_overwritten(monkeypatch, tmp_path):
    project = _project(tmp_path, 0.0005, None)
    (project / "routed.def").write_text("UNITS DISTANCE MICRONS 2000 ;\n")
    got = _measure(monkeypatch, project)
    doc, err = TD.load(project / TD.DECLARATION_REL)
    assert err is None
    assert got["published"] is False
    assert TD.answer(doc, "database_unit_um") == 0.0005
    assert TD.TECHNOLOGY_KEY not in doc
