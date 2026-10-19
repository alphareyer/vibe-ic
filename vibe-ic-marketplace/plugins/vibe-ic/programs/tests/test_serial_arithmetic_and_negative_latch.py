"""Public generic issue3216 classifier and issue3209 log advice controls."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures/phase1_local/serial_arithmetic_and_negative_latch"
NEGATIVE = (FIXTURES / "no_latch.log").read_text().strip()
POSITIVE = "Warning: inferring a latch for signal state_next."


def classify(tmp_path, text):
    docs = tmp_path / "phase1/generated_docs"
    docs.mkdir(parents=True)
    (docs / "L1_DATASHEET.json").write_text(json.dumps({"description": text}))
    (docs / "L2_FRS.json").write_text("{}\n")
    result = subprocess.run([sys.executable, str(PROGRAMS / "ic_class_profile.py"), str(tmp_path)], text=True, capture_output=True)
    (tmp_path / "CLI_RESULT.json").write_text(json.dumps({"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}, indent=2))
    assert result.returncode == 0, result.stdout + result.stderr
    profile = json.loads(result.stdout)
    assert json.loads((tmp_path / "reports/ic_class.json").read_text()) == profile
    return profile


def test_internal_serial_arithmetic_is_not_a_serial_peripheral(tmp_path):
    profile = classify(tmp_path, (FIXTURES / "internal_serial_arithmetic.md").read_text())
    assert profile["ic_class"] == "crypto_accelerator"


@pytest.mark.parametrize("text", [
    "A UART peripheral provides a serial transmitter and receiver, shift registers and a baud rate generator. It can carry payload AES encryption with secret keys.",
    "An SPI serial peripheral acts as controller and target. It uses a shift register, clock generator, chip select and a data line. Optional payload AES encryption uses a block cipher.",
])
def test_real_serial_protocol_with_incidental_crypto_stays_serial(tmp_path, text):
    assert classify(tmp_path, text)["ic_class"] == "serial_peripheral_protocol"


def diagnose(tmp_path, text):
    log = tmp_path / "synth.log"
    log.write_text(text)
    result = subprocess.run([sys.executable, str(PROGRAMS / "synth_doctor.py"), str(log), "--json"], text=True, capture_output=True)
    (tmp_path / "CLI_RESULT.json").write_text(json.dumps({"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}, indent=2))
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_no_latch_inferred_is_clean_through_normal_cli(tmp_path):
    result = diagnose(tmp_path, NEGATIVE)
    assert result["verdict"] == "CLEAN"
    assert result["findings"] == []


@pytest.mark.parametrize("line", [POSITIVE, "Latch inferred for signal state_next from process control."])
def test_real_latch_keeps_the_existing_advice(tmp_path, line):
    result = diagnose(tmp_path, line)
    assert result["verdict"] == "DIAGNOSED"
    assert result["findings"][0]["matched_pattern"] == "LATCH_INFERENCE"
    assert result["findings"][0]["auto_fixable"] is True
    assert "default" in result["findings"][0]["canonical_fix"]


@pytest.mark.parametrize("separator", ["\n", "; "])
def test_negative_latch_message_does_not_hide_a_real_latch(tmp_path, separator):
    result = diagnose(tmp_path, NEGATIVE + separator + POSITIVE)
    assert result["verdict"] == "DIAGNOSED"
    assert result["count"] == 1
    finding = result["findings"][0]
    assert finding["matched_pattern"] == "LATCH_INFERENCE"
    assert POSITIVE in finding["evidence_line"]
    assert finding["line_no"] == (2 if separator == "\n" else 1)


def test_negative_latch_message_does_not_hide_a_real_unknown_error(tmp_path):
    result = diagnose(tmp_path, NEGATIVE + "\nERROR: entirely novel tool failure.")
    assert result["verdict"] == "MANUAL_REVIEW"
    assert result["findings"][0]["matched_pattern"] == "UNKNOWN"
    assert result["findings"][0]["line_no"] == 2
