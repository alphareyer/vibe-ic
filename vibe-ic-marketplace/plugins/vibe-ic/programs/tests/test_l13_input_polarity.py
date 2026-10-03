"""Source-backed L13 polarity through the ordinary emitter and overlay."""
import json
from pathlib import Path

import pytest

import phase1_doc_one_shot_runner as p1
import spi_protocol_synth as spi


def _emit(tmp_path, text):
    docs = tmp_path / "input" / "docs"
    docs.mkdir(parents=True)
    (docs / "calibration.md").write_text(text, encoding="utf-8")
    p1.gen_l13_lab_calibration(tmp_path, {"calibration.md": text})
    gd = tmp_path / "phase1" / "generated_docs"
    spi.apply_spi_synth(gd, False, None)
    return json.loads((gd / "L13_LAB_CALIBRATION.json").read_text())


def test_original_negative_calibration_stays_provenance(tmp_path):
    text = (Path(__file__).parent / "fixtures" / "subservient_input_docs" /
            "L6_calibration.md").read_text(encoding="utf-8")
    doc = _emit(tmp_path, text)
    assert doc["calibration_steps"] == doc["test_cases"] == doc["trim_loop"] == []
    assert doc["calibration_targets"] == []
    for key in ("no_lab_calibration_in_input", "no_calibration_steps_in_input",
                "no_l13_test_cases_in_input"):
        assert doc[key] is True
    assert doc["lab_calibration_present"] is False
    assert doc["source_documents"] == ["input/docs/calibration.md"]
    evidence = doc["extraction_evidence"]["input/docs/calibration.md"]
    assert any("無 trimming" in e["literal"] for e in evidence)
    assert any("不需產生 calibration controller" in e["literal"] for e in evidence)
    assert "BusClock" not in doc.get("notes", "")


@pytest.mark.parametrize("prefix", ["", "No OTP calibration is required; "])
def test_affirmative_procedure_survives_independent_denial(tmp_path, prefix):
    actions = ["1. Measure oscillator frequency.",
               "2. Adjust TRIM_OSC until frequency is 10 MHz."]
    doc = _emit(tmp_path, "# Calibration / Lab Procedures\n" + prefix +
                "\n".join(actions))
    assert [step["action"] for step in doc["calibration_steps"]] == actions
    assert doc["test_cases"] == doc["trim_loop"] == doc["calibration_steps"]
    assert {target["name"] for target in doc["calibration_targets"]} == {"TRIM_OSC"}
    assert doc["no_lab_calibration_in_input"] is False
    assert doc["no_calibration_steps_in_input"] is False
    assert doc["lab_calibration_present"] is True
    assert "BusClock" not in doc.get("notes", "")


def test_negated_target_and_postposed_na_are_not_procedures(tmp_path):
    doc = _emit(tmp_path, "# Calibration\nCalibration is not required.\n"
                "1. No TRIM_OSC or measurement procedure.\nCalibration: N/A\n")
    assert doc["calibration_steps"] == doc["calibration_targets"] == []
    assert doc["no_lab_calibration_in_input"] is True


def test_declared_name_vendor_row_is_not_reported_absent(tmp_path):
    doc = _emit(tmp_path, "Rigol DM3068 | Keysight\n")
    assert doc["lab_equipment"] == [{
        "name": "Rigol DM3068", "vendor": "Keysight",
        "purpose": "lab equipment (extracted)",
        "evidence": "input/docs/calibration.md",
    }]
    assert doc["no_lab_equipment_in_input"] is False
    # Equipment alone does not assert that calibration is present.
    assert doc["no_lab_calibration_in_input"] is True
    assert doc["lab_calibration_present"] is False


@pytest.mark.parametrize("clause", [
    "TRIM_OSC isn't required.\n",
    "TRIM_OSC N/A.\n",
    "TRIM_OSC is N/A.\n",
    "TRIM_OSC are N/A.\n",
    "TRIM_OSC: N/A.\n",
])
def test_postposed_negative_forms_are_not_targets(tmp_path, clause):
    doc = _emit(tmp_path, clause)
    assert doc["calibration_targets"] == []
    assert doc["no_lab_calibration_in_input"] is True


@pytest.mark.parametrize("is_spi", [False, True])
def test_generic_overlay_does_not_invent_calibration_or_baud_implementation(
        tmp_path, is_spi):
    path = tmp_path / "L13_LAB_CALIBRATION.json"
    path.write_text(json.dumps({"calibration_steps": [{"action": "Measure frequency"}],
                                "notes": "Input calls for a frequency measurement."}))
    spi.apply_spi_synth(tmp_path, is_spi, None)
    doc = json.loads(path.read_text())
    assert doc["lab_calibration_present"] is True
    assert doc["notes"] == "Input calls for a frequency measurement."
