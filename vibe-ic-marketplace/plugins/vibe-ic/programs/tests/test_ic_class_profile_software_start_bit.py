"""Public L1/L2 software control bit through the normal profile CLI."""
import json
from pathlib import Path
import subprocess
import sys


def test_software_start_bit_does_not_prove_an_external_serial_wire(tmp_path):
    docs = tmp_path / "phase1/generated_docs"
    docs.mkdir(parents=True)
    (docs / "L1_DATASHEET.json").write_text(json.dumps({
        "description": "The accelerator implements the AES algorithm for block cipher encryption with a key schedule.",
    }))
    (docs / "L2_FRS.json").write_text(json.dumps({
        "functional_requirements": "The serial-parallel multiplier processes shifted internal state. Software sets the START bit to initiate the operation.",
    }))
    program = Path(__file__).resolve().parents[1] / "ic_class_profile.py"
    result = subprocess.run([sys.executable, str(program), str(tmp_path)],
                            text=True, capture_output=True)
    (tmp_path / "CLI_RESULT.json").write_text(json.dumps({
        "returncode": result.returncode, "stdout": result.stdout,
        "stderr": result.stderr,
    }, indent=2))
    assert result.returncode == 0, result.stdout + result.stderr
    profile = json.loads(result.stdout)
    assert json.loads((tmp_path / "reports/ic_class.json").read_text()) == profile
    assert profile["ic_class"] == "crypto_accelerator", profile


def test_start_and_stop_bits_in_actual_frames_stay_serial(tmp_path):
    docs = tmp_path / "phase1/generated_docs"
    docs.mkdir(parents=True)
    (docs / "L1_DATASHEET.json").write_text(json.dumps({
        "description": "A transmitter and receiver use shift registers for frames with a start bit,\n eight data bits and a stop bit.",
    }))
    (docs / "L2_FRS.json").write_text("{}\n")
    program = Path(__file__).resolve().parents[1] / "ic_class_profile.py"
    result = subprocess.run([sys.executable, str(program), str(tmp_path)],
                            text=True, capture_output=True)
    (tmp_path / "CLI_RESULT.json").write_text(json.dumps({
        "returncode": result.returncode, "stdout": result.stdout,
        "stderr": result.stderr,
    }, indent=2))
    assert result.returncode == 0, result.stdout + result.stderr
    profile = json.loads(result.stdout)
    assert json.loads((tmp_path / "reports/ic_class.json").read_text()) == profile
    assert profile["ic_class"] == "serial_peripheral_protocol", profile
