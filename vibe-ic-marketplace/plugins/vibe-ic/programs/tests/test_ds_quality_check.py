"""Unit tests for ds_quality_check.py — datasheet (L1) quality scorer 0-100."""
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "ds_quality_check.py"
assert SCRIPT.exists(), f"Script not found: {SCRIPT}"
sys.path.insert(0, str(SCRIPT.parent))
import ds_quality_check as ds  # noqa: E402


# A "good" datasheet hitting all 10 criteria at full marks.
GOOD_DS = """\
# Generic Sensor Datasheet

## Features
- 16-bit resolution
- I2C interface up to 400 kHz
- Low power 1.2 uA standby
- Wide supply 1.8 V to 5.5 V
- Internal temperature sensor
- Programmable interrupt

## Description
This device is a high-precision analog front end intended for portable
instrumentation. It integrates a programmable gain amplifier, a delta-sigma
converter and a digital interface in a compact package.

It supports single-shot and continuous conversion modes, and includes an
internal reference for ratiometric measurements without an external part.

## Pin Configuration
| Pin | Name | Type | Description |
|-----|------|------|-------------|
| 1   | VDD  | PWR  | Supply |
| 2   | GNDP | PWR  | Ground |
| 3   | SDAT | I/O  | Serial data |
| 4   | SCLK | IN   | Serial clock |

## Absolute Maximum Ratings
| Parameter | Min | Max | Unit |
|-----------|-----|-----|------|
| Supply voltage | -0.3 | 6.0 | V |
| Input voltage | -0.3 | 6.0 | V |
| Storage temp | -65 | 150 | C |
| Junction temp | - | 150 | C |
| ESD HBM | - | 2000 | V |

## Recommended Operating Conditions
| Parameter | Min | Typ | Max | Unit |
|-----------|-----|-----|-----|------|
| Supply | 1.8 | 3.3 | 5.5 | V |
| Temp | -40 | 25 | 85 | C |

## Electrical Characteristics
DC characteristics measured at 3.3 V.
| Param | Min | Typ | Max | Unit |
|-------|-----|-----|-----|------|
| Iq | - | 150 | 250 | uA |

AC / switching characteristics:
| Param | Min | Typ | Max | Unit |
|-------|-----|-----|-----|------|
| fSCLK | - | - | 400 | kHz |

## Timing Diagrams
```
SCLK  __|‾‾|__|‾‾|__
SDAT  ‾‾‾‾|________|‾‾
       <-tSU-><-tHD->
```

## Block Diagram
```
   +--------+      +-------+      +--------+
   |  PGA   |----->| ADC   |----->|  I2C   |
   +--------+      +-------+      +--------+
        ^              ^              |
        |              |              v
     analog in      ref              SDAT
```

## Detailed Description and Register Map
The conversion engine runs from an internal oscillator. The configuration
register selects gain, data rate and mux channel. Reading the conversion
register returns the most-recent result in two's-complement form. The device
powers up in a known reset state with all registers cleared, ensuring a
deterministic startup behaviour for the host firmware to build upon safely.

| Addr | Register | Bits | Description |
|------|----------|------|-------------|
| 0x00 | CONV     | 16   | Conversion result |
| 0x01 | CONFIG   | 16   | Configuration |

## Application Information
```
 VDD --+--[10 kohm]--+-- SDAT
       |             |
      ===0.1 uF     host
       |
      GND
```
Use a 10 kohm pull-up on SDAT and a 0.1 uF decoupling capacitor close to VDD.
"""

# A "bad" datasheet: only a title and a sparse feature list — most sections
# absent. Should score well below threshold.
BAD_DS = """\
# Sketch

## Features
- one thing

Some loose text without proper sections.
"""


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content)
    return p


def test_good_datasheet_passes():
    result = ds.score_datasheet_text(GOOD_DS, "good.md")
    assert result.verdict == "PASS", result.to_dict()
    assert result.score >= ds.THRESHOLD
    assert result.score <= ds.MAX_SCORE


def test_good_datasheet_full_marks_on_key_criteria():
    result = ds.score_datasheet_text(GOOD_DS)
    by_idx = {c.index: c for c in result.breakdown}
    # Features (>=5 bullets) and Pin config (>=3 cols) must be full marks.
    assert by_idx[1].score == 10, by_idx[1].note
    assert by_idx[3].score == 10, by_idx[3].note
    assert by_idx[5].score == 10, by_idx[5].note   # min/typ/max ROC


def test_bad_datasheet_fails():
    result = ds.score_datasheet_text(BAD_DS, "bad.md")
    assert result.verdict == "FAIL"
    assert result.score < ds.THRESHOLD


def test_empty_is_missing_not_false_fail():
    result = ds.score_datasheet_text("", "empty.md")
    assert result.verdict == "MISSING"
    assert result.score == 0


def test_missing_file_degrades_gracefully(tmp_path):
    result = ds.score_datasheet_path(tmp_path / "nope.md")
    assert result.verdict == "MISSING"


def test_project_dir_autolocate(tmp_path):
    _write(tmp_path, "04_datasheet.md", GOOD_DS)
    result = ds.score_datasheet_path(tmp_path)
    assert result.verdict == "PASS"
    assert "04_datasheet.md" in result.source


def test_deterministic():
    a = ds.score_datasheet_text(GOOD_DS).score
    b = ds.score_datasheet_text(GOOD_DS).score
    assert a == b


def test_cli_exit_codes(tmp_path, capsys):
    good = _write(tmp_path, "g.md", GOOD_DS)
    bad = _write(tmp_path, "b.md", BAD_DS)
    assert ds.main([str(good), "--json"]) == 0
    assert ds.main([str(bad), "--json"]) == 1
    assert ds.main([str(tmp_path / "missing.md")]) == 2


def test_layer_input_is_not_a_datasheet_even_with_scoring_content(tmp_path):
    # The layer number and quality of its body do not establish document role.
    _write(tmp_path, "L1_arbitrary_record.md",
           GOOD_DS.replace("# Generic Sensor Datasheet", "# Original Layer Record", 1))
    result = ds.score_datasheet_path(tmp_path)
    assert result.verdict == "MISSING", result.to_dict()
    assert result.score == 0
    assert result.source == str(tmp_path)


def test_layer_title_declares_datasheet_role(tmp_path):
    path = _write(tmp_path, "L1_reference.md", GOOD_DS)
    result = ds.score_datasheet_path(tmp_path)
    assert result.verdict == "PASS", result.to_dict()
    assert result.source == str(path)


def test_role_filename_is_case_insensitive(tmp_path):
    path = _write(tmp_path, "DEVICE_DATASHEET.MD", GOOD_DS)
    result = ds.score_datasheet_path(tmp_path)
    assert result.verdict == "PASS", result.to_dict()
    assert result.source == str(path)


def test_incomplete_role_keeps_fail_precedence_over_complete_fallback(tmp_path):
    poor = _write(tmp_path, "04_datasheet.md", BAD_DS)
    _write(tmp_path, "L1_complete.md", GOOD_DS)
    result = ds.score_datasheet_path(tmp_path)
    assert result.verdict == "FAIL", result.to_dict()
    assert result.score == 2
    assert result.source == str(poor)


def test_malformed_role_document_is_fail(tmp_path):
    path = _write(tmp_path, "04_datasheet.md", "#\nnot a complete document\n")
    result = ds.score_datasheet_path(tmp_path)
    assert result.verdict == "FAIL", result.to_dict()
    assert result.score == 0
    assert result.source == str(path)


def test_empty_role_document_is_missing(tmp_path):
    path = _write(tmp_path, "04_datasheet.md", " \n")
    result = ds.score_datasheet_path(tmp_path)
    assert result.verdict == "MISSING", result.to_dict()
    assert result.source == str(path)


def test_explicit_layer_input_is_still_scored(tmp_path):
    path = _write(tmp_path, "L1_arbitrary_record.md", BAD_DS)
    result = ds.score_datasheet_path(path)
    assert result.verdict == "FAIL", result.to_dict()
    assert result.score == 2
    assert result.source == str(path)


def test_current_in_repo_integration_fixture_is_not_a_layer_document(tmp_path):
    from _hostpaths import require_repo

    artifact = require_repo("vibe-ic-marketplace", "plugins", "vibe-ic", "_shared",
                            "integration_fixtures", "datasheet-gen.md")
    _write(tmp_path, "L1_record.md", artifact.read_text(encoding="utf-8"))
    result = ds.score_datasheet_path(tmp_path)
    assert result.verdict == "MISSING", result.to_dict()
    assert result.source == str(tmp_path)


def test_incomplete_layer_title_is_scored_without_quality_filter(tmp_path):
    path = _write(tmp_path, "L1_reference.md", "# Device Datasheet\n\n## Description\n"
                  "This role document is deliberately incomplete but must still be scored.\n")
    result = ds.score_datasheet_path(tmp_path)
    assert result.verdict == "FAIL", result.to_dict()
    assert result.score == 5
    assert result.source == str(path)


# Exact neutral input supplied by the independent fenced-title counterexample.
FENCED_DS_INPUT = r"""Original layer input metadata. The following Markdown is a quoted document example, not this file's document title.

```markdown
# Neutral Controller Datasheet

This is an independently authored structural scoring fixture, not measured hardware data.

## Features
- Byte-wide request data
- Clocked ready indication
- Active-high reset input
- Readable status register
- Configurable request mask

## Description
The neutral controller samples a byte request when the ready indication permits it. A subsequent status observation distinguishes an accepted request from an idle bus cycle, and reset returns the request controller to idle state.

A small status register describes the most recent accepted request. Software may poll this value after writing the control register; the clock and reset pins remain mandatory independently of whether the application reads the status interface.

## Pin Configuration
| Pin | Direction | Description |
|---|---|---|
| CLK | Input | Clock samples the request |
| RESET | Input | Active-high reset |
| READY | Output | Request acceptance indication |

## Absolute Maximum Ratings
The following are illustrative metadata-fixture values, not qualified electrical limits.
| Parameter | Value | Unit |
|---|---|---|
| Supply maximum | 2.2 | V |
| Input maximum | 2.2 | V |
| Input minimum | -0.3 | V |
| Storage minimum | -40 | C |
| Storage maximum | 125 | C |

## Recommended Operating Conditions
| Parameter | Min | Typ | Max | Unit |
|---|---|---|---|---|
| Supply | 1.62 | 1.8 | 1.98 | V |

## Electrical Characteristics
### DC
| Parameter | Value | Unit |
|---|---|---|
| Input leakage | 1 | uA |
### AC
| Parameter | Value | Unit |
|---|---|---|
| Clock period | 20 | ns |

## Timing Diagrams
```text
CLK    __/--\__/--\__
REQ    ----valid-------
READY  ______/------__
```

## Block Diagram
```text
Request --> Controller --> Status
Clock   --> Controller
Reset   --> Controller
```

## Detailed Description and Register Map
The request controller accepts one byte transaction on an active clock edge when READY is asserted. Writing CONTROL while idle selects the mask used by the following transaction. STATUS records whether an acceptance occurred, and a read has no side effect. Reset clears both state and the acceptance flag. Polling STATUS while a request remains active does not authorize a second request; software must wait for READY before launching another byte.

| Address | Register | Description |
|---|---|---|
| 0x00 | CONTROL | Request mask |
| 0x04 | STATUS | Acceptance flag |

## Application Information
Place a 0.1 uF capacitor at the supply pins and use a 10 kohm illustrative bias resistor. These values only exercise document structure.
```text
Supply --+--> Controller
         C1
         |
Return --+-------------
```

```
"""


def test_fenced_example_is_not_a_document_title(tmp_path):
    import hashlib
    import json
    import os
    import subprocess

    fixture = FENCED_DS_INPUT.encode("utf-8")
    assert hashlib.sha256(fixture).hexdigest() == "7c1ce4c72ce2815c73924d58ae42d0e26c57393a33389c4f3436748145a0e6fd"
    path = tmp_path / "L1_arbitrary_record.md"
    path.write_bytes(fixture)
    command = [sys.executable, "-B", str(SCRIPT), str(tmp_path), "--json"]
    process = subprocess.run(command, capture_output=True, text=True)
    result = json.loads(process.stdout)
    observation = {"id": "DS-fenced-example-auto", "argv": command,
                   "rc": process.returncode, "result": result,
                   "stderr": process.stderr, "input_sha256": "7c1ce4c72ce2815c73924d58ae42d0e26c57393a33389c4f3436748145a0e6fd"}
    if os.environ.get("DOCUMENT_ROLE_OBSERVATIONS"):
        with Path(os.environ["DOCUMENT_ROLE_OBSERVATIONS"]).open("a") as stream:
            stream.write(json.dumps(observation) + "\n")
    assert result["verdict"] == "MISSING", observation
    assert process.returncode == 2, observation
    assert result["score"] == 0, observation
    assert result["source"] == str(tmp_path), observation
