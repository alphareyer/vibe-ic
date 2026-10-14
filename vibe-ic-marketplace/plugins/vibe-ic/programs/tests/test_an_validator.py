"""Unit tests for an_validator.py — application-note (AN) scorer 0-80."""
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "an_validator.py"
assert SCRIPT.exists(), f"Script not found: {SCRIPT}"
sys.path.insert(0, str(SCRIPT.parent))
import an_validator as an  # noqa: E402


GOOD_AN = """\
# Generic Sensor Application Note

## Overview
This application note describes how to design a complete sensing subsystem
around the device, including supply decoupling, the host interface, and the
firmware driver. It targets battery-powered instrumentation where low standby
current and ratiometric accuracy are both required at the same time.

The guidance below has been validated on a two-layer reference board and is
intended to be copied directly into a production design with minimal change,
so that a first-pass layout meets EMI and accuracy targets out of the box.

## Typical Application Circuit
```
 VDD --+--[10 kohm]--+-- SDAT --- host
       |             |
      ===0.1 uF      |
       |            host
      GND
```
The pull-up resistor and 0.1 uF decoupling capacitor are required.

## External Component Selection
| Ref | Value | Purpose |
|-----|-------|---------|
| R1  | 10 kohm | SDAT pull-up |
| C1  | 0.1 uF  | Supply decoupling |
| C2  | 1 uF    | Bulk reservoir |

## PCB Layout
- Keep the decoupling capacitor within 2 mm of the supply pin.
- Use a solid ground plane under the device.
- Route the analog input away from the digital clock.
```
[ device ]==C1== GND plane
```

## Firmware Example
```c
i2c_write(0x01, 0x8483);   // configure
uint16_t v = i2c_read(0x00); // read conversion
```
The firmware writes the CONFIG register then reads the CONV register.

## Design Calculations
The full-scale voltage is computed as:
VFS = VREF / GAIN
The LSB size is:
LSB = VFS / 32768
The pull-up value satisfies:
Rpull = (VDD - VOL) / IOL = 3.3 / 0.003 = 1100 ohm

## FAQ
- Q: What supply range is supported? The device runs from 1.8 V to 5.5 V.
- Q: Can I use a slower I2C clock? Yes, down to DC.
- Q: Is an external reference needed? No, an internal reference is provided.
- Q: How do I lower power? Use single-shot mode and power down between reads.
- Q: What is the conversion time? About 1 ms at the default data rate.

## Competitive Comparison
| Feature | This part | Competitor A | Competitor B |
|---------|-----------|--------------|--------------|
| Resolution | 16-bit | 12-bit | 16-bit |
| Iq | 150 uA | 300 uA | 200 uA |
| Interface | I2C | SPI | I2C |
"""

BAD_AN = """\
# Notes

## Overview
short.

Some text but nothing structured.
"""


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content)
    return p


def test_good_appnote_passes():
    result = an.score_appnote_text(GOOD_AN, "good.md")
    assert result.verdict == "PASS", result.to_dict()
    assert result.score >= an.THRESHOLD
    assert result.score <= an.MAX_SCORE


def test_good_appnote_full_marks_on_key_criteria():
    result = an.score_appnote_text(GOOD_AN)
    by_idx = {c.index: c for c in result.breakdown}
    assert by_idx[7].score == 10, by_idx[7].note   # FAQ >=5 Q&A
    assert by_idx[8].score == 10, by_idx[8].note   # comparison >=3 products


def test_bad_appnote_fails():
    result = an.score_appnote_text(BAD_AN, "bad.md")
    assert result.verdict == "FAIL"
    assert result.score < an.THRESHOLD


def test_empty_is_missing():
    result = an.score_appnote_text("", "empty.md")
    assert result.verdict == "MISSING"
    assert result.score == 0


def test_project_dir_autolocate(tmp_path):
    _write(tmp_path, "05_appnote.md", GOOD_AN)
    result = an.score_appnote_path(tmp_path)
    assert result.verdict == "PASS"
    assert "05_appnote.md" in result.source


def test_deterministic():
    a = an.score_appnote_text(GOOD_AN).score
    b = an.score_appnote_text(GOOD_AN).score
    assert a == b


def test_cli_exit_codes(tmp_path):
    good = _write(tmp_path, "g.md", GOOD_AN)
    bad = _write(tmp_path, "b.md", BAD_AN)
    assert an.main([str(good), "--json"]) == 0
    assert an.main([str(bad), "--json"]) == 1
    assert an.main([str(tmp_path / "missing.md")]) == 2


def test_layer_input_is_not_an_appnote_even_with_scoring_content(tmp_path):
    _write(tmp_path, "L5_arbitrary_record.md",
           GOOD_AN.replace("# Generic Sensor Application Note", "# Original Layer Record", 1))
    result = an.score_appnote_path(tmp_path)
    assert result.verdict == "MISSING", result.to_dict()
    assert result.score == 0
    assert result.source == str(tmp_path)


def test_layer_title_declares_appnote_role(tmp_path):
    path = _write(tmp_path, "L5_reference.md", GOOD_AN)
    result = an.score_appnote_path(tmp_path)
    assert result.verdict == "PASS", result.to_dict()
    assert result.source == str(path)


def test_role_filename_is_case_insensitive(tmp_path):
    path = _write(tmp_path, "DEVICE_APPLICATION_NOTE.MD", GOOD_AN)
    result = an.score_appnote_path(tmp_path)
    assert result.verdict == "PASS", result.to_dict()
    assert result.source == str(path)


def test_incomplete_role_keeps_fail_precedence_over_complete_fallback(tmp_path):
    poor = _write(tmp_path, "05_appnote.md", BAD_AN)
    _write(tmp_path, "L5_complete.md", GOOD_AN)
    result = an.score_appnote_path(tmp_path)
    assert result.verdict == "FAIL", result.to_dict()
    assert result.score == 0
    assert result.source == str(poor)


def test_malformed_role_document_is_fail(tmp_path):
    path = _write(tmp_path, "05_appnote.md", "#\nnot a complete document\n")
    result = an.score_appnote_path(tmp_path)
    assert result.verdict == "FAIL", result.to_dict()
    assert result.score == 0
    assert result.source == str(path)


def test_empty_role_document_is_missing(tmp_path):
    path = _write(tmp_path, "05_appnote.md", " \n")
    result = an.score_appnote_path(tmp_path)
    assert result.verdict == "MISSING", result.to_dict()
    assert result.source == str(path)


def test_explicit_layer_input_is_still_scored(tmp_path):
    path = _write(tmp_path, "L5_arbitrary_record.md", BAD_AN)
    result = an.score_appnote_path(path)
    assert result.verdict == "FAIL", result.to_dict()
    assert result.score == 0
    assert result.source == str(path)


def test_incomplete_layer_title_is_scored_without_quality_filter(tmp_path):
    path = _write(tmp_path, "L5_reference.md", "# Device Application Note\n\n## Overview\n"
                  "This role document is deliberately incomplete but must still be scored.\n")
    result = an.score_appnote_path(tmp_path)
    assert result.verdict == "FAIL", result.to_dict()
    assert result.score == 0
    assert result.source == str(path)


# Exact neutral input supplied by the independent fenced-title counterexample.
FENCED_AN_INPUT = r"""Original layer input metadata. The following Markdown is a quoted document example, not this file's document title.

```markdown
# Neutral Controller Application Note

This is an independently authored structural scoring fixture, not a board or firmware qualification.

## Overview
This example integrates a byte request controller into a small polling application. The software submits a request only when the controller indicates that it is ready, then observes status to distinguish acceptance from an idle cycle. The sample clock and reset remain explicit board connections so the interface can be understood without assuming an implicit power-on state.

The application uses separate control and status register accesses. Keeping those accesses distinct makes the demonstration's assumptions visible: a status read has no side effect, reset returns the controller to idle, and a request is sampled only at an active clock edge. Component values and board choices below are illustrative fixture information rather than measured device recommendations.

## Typical Application Circuit
An illustrative supply bypass is 0.1 uF and the example bias is 10 kohm.
```text
Supply --+--> Controller --> READY
         C1       ^
         |        CLK
Return --+-----------------------
```

## External Component Selection
| Component | Value | Purpose |
|---|---|---|
| C1 | 0.1 uF | Supply bypass |
| R1 | 10 kohm | Illustrative bias |

## PCB Layout
- Keep the supply return path next to the bypass connection.
- Route the clock away from the example status polling return path.
```text
Supply --- C1 --- Device
Return ---+------ Device
```

## Firmware Example
The example operations are illustrative pseudocode and are not executed.
```c
write_reg(0x00, mask);
unsigned status = read_reg(0x04);
```

## Design Calculations
T = 1 / 50000000 = 20 ns
I = 1.8 / 10000 = 0.18 mA
E = 0.5 * 0.0000001 * 1.8 * 1.8 = 0.000000162 J

## FAQ
Q: When is a request accepted?
A: When the documented active clock edge samples an allowed request.
Q: Does a status read change controller state?
A: No; the read reports the acceptance flag without a side effect.
Q: What does reset do?
A: It clears the illustrative controller state to idle.
Q: May requests overlap?
A: This demonstration waits for READY before submitting another byte.
Q: Are these component values measured?
A: No; the values are structural-fixture examples only.

## Competitive Comparison
The labels compare illustrative integration choices, not commercial products.
| Choice | Transfer | Status access |
|---|---|---|
| Mode A | Polling | Register |
| Mode B | Interrupt | Register |
| Mode C | Queued | Dedicated flag |

```
"""


def test_fenced_example_is_not_a_document_title(tmp_path):
    import hashlib
    import json
    import os
    import subprocess

    fixture = FENCED_AN_INPUT.encode("utf-8")
    assert hashlib.sha256(fixture).hexdigest() == "0c5b8c41b1dd479c3461c59c104d35827aaebe71166d9fb885714de4de3caf57"
    path = tmp_path / "L5_arbitrary_record.md"
    path.write_bytes(fixture)
    command = [sys.executable, "-B", str(SCRIPT), str(tmp_path), "--json"]
    process = subprocess.run(command, capture_output=True, text=True)
    result = json.loads(process.stdout)
    observation = {"id": "AN-fenced-example-auto", "argv": command,
                   "rc": process.returncode, "result": result,
                   "stderr": process.stderr, "input_sha256": "0c5b8c41b1dd479c3461c59c104d35827aaebe71166d9fb885714de4de3caf57"}
    if os.environ.get("DOCUMENT_ROLE_OBSERVATIONS"):
        with Path(os.environ["DOCUMENT_ROLE_OBSERVATIONS"]).open("a") as stream:
            stream.write(json.dumps(observation) + "\n")
    assert result["verdict"] == "MISSING", observation
    assert process.returncode == 2, observation
    assert result["score"] == 0, observation
    assert result["source"] == str(tmp_path), observation
