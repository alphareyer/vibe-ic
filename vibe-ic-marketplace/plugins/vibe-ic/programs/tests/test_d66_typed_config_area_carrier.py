"""Focused controls for the supported typed floorplan-config carrier."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import _submission_template as ST  # noqa: E402
import _tapeout_declaration as TD  # noqa: E402
import area_budget_basis_gen as AB  # noqa: E402
import tapeout_declaration_gen as GEN  # noqa: E402


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _answers() -> dict:
    return {
        "schema": "vibe-ic/step_0_5ic_answers/1",
        "answers": {
            "deliverable": "DIE",
            "deliverable_rationale": "Owner selected a standalone die.",
            "top_cell": "neutral_top",
            "synthesis_area_budget": "NOT_DETERMINED",
        },
        "answer_provenance": {
            "deliverable": {
                "answered_by": "owner",
                "citation": "R-0000-NEUTRAL owner selected a standalone die.",
            }
        },
    }


def _config(project: Path, width: int = 100, height: int = 100) -> None:
    _write_json(project / "input/config.json", {
        "DESIGN_NAME": "neutral_top",
        "FP_SIZING": "absolute",
        "DIE_AREA": [0, 0, width, height],
    })


def _operator_slot(project: Path, doc: dict, width: int, height: int) -> None:
    source = project / ST.STAGED_TEMPLATE_REL / "neutral_slot.json"
    _write_json(source, {
        "SLOT": "neutral_slot", "DIE_AREA": [0, 0, width, height]})
    record = ST.slot_record(source, json.loads(source.read_text()), source.parent)
    _write_json(project / ST.SLOTS_DIR_REL / "neutral_slot.yaml", record)
    doc["operator_template"] = {"slot": "neutral_slot"}
    doc["answers"]["die_area_um"] = [0, 0, width, height]
    doc["answer_provenance"]["operator_template.slot"] = {
        "answered_by": "owner", "citation": "Owner selected neutral_slot."}


def test_typed_config_reaches_basis_and_declaration_consumer(tmp_path):
    project = tmp_path / "project"
    _config(project)
    answers = _answers()
    _write_json(project / "input/step_0_5ic_answers.json", answers)

    got = AB.derive(project, answers)
    assert got["status"] == "WRITE", got
    assert got["budget"]["max_die_dimensions_um"] == [100.0, 100.0]
    assert got["budget"]["basis"]["die_fixed_by_the_input"] == {
        "wxh_um": "100x100", "source": "input/config.json"}
    assert "input/config.json" in got["inputs"]

    assert AB.main([str(project)]) == 0
    assert GEN.main([str(project), "--answers",
                     str(project / "input/step_0_5ic_answers.json"),
                     "--json", str(project / "declaration-gen.json")]) == 0
    declaration = json.loads((project / TD.DECLARATION_REL).read_text())
    resolved = TD.area_budget_resolution(declaration)
    assert resolved["status"] == "LIMIT", resolved
    assert resolved["ceiling_wxh_um"] == "100x100", resolved


def test_absent_config_and_prose_stays_not_determined(tmp_path):
    got = AB.derive(tmp_path, _answers())
    assert got["status"] == "NOT_DETERMINED", got
    assert "budget" not in got


def test_malformed_config_fails_closed_as_not_determined(tmp_path):
    path = tmp_path / "input/config.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"DIE_AREA": [0, 0, 100,', encoding="utf-8")
    got = AB.derive(tmp_path, _answers())
    assert got["status"] == "NOT_DETERMINED", got
    assert "budget" not in got


@pytest.mark.parametrize("name", ["floorplan.md", "floorplan"])
def test_typed_config_conflicting_with_prose_refuses(tmp_path, name):
    _config(tmp_path, 100, 100)
    prose = tmp_path / "input/docs" / name
    prose.parent.mkdir(parents=True)
    prose.write_text("The die dimensions are fixed at 120 x 100 um.\n")
    got = AB.derive(tmp_path, _answers())
    assert got["status"] == "REFUSED", got
    assert "input/config.json" in got["reason"]
    assert f"input/docs/{name}" in got["reason"]


def test_typed_config_conflicting_with_operator_slot_refuses(tmp_path):
    _config(tmp_path, 100, 100)
    answers = _answers()
    _operator_slot(tmp_path, answers, 120, 100)
    got = AB.derive(tmp_path, answers)
    assert got["status"] == "REFUSED", got
    assert "operator slot" in got["reason"]


def test_unsupported_prose_extension_is_not_admitted(tmp_path):
    _config(tmp_path, 100, 100)
    prose = tmp_path / "input/docs/floorplan.unsupported"
    prose.parent.mkdir(parents=True)
    prose.write_text("The die dimensions are fixed at 120 x 100 um.\n")
    got = AB.derive(tmp_path, _answers())
    assert got["status"] == "WRITE", got
    assert got["budget"]["max_die_dimensions_um"] == [100.0, 100.0]
    assert got["budget"]["basis"]["die_fixed_by_the_input"] == {
        "wxh_um": "100x100", "source": "input/config.json"}


def test_valid_prose_path_is_unchanged(tmp_path):
    prose = tmp_path / "input/docs/floorplan.md"
    prose.parent.mkdir(parents=True)
    prose.write_text("The die dimensions are fixed at 300 x 200 um.\n")
    got = AB.derive(tmp_path, _answers())
    assert got["status"] == "WRITE", got
    assert got["budget"]["max_die_dimensions_um"] == [300.0, 200.0]
    assert got["budget"]["basis"]["die_fixed_by_the_input"] == {
        "wxh_um": "300x200", "source": "input/docs/floorplan.md"}
