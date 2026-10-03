"""Focused regression tests for an explicitly absent L4 register map.

The absence wording is the real input shape supplied for this repair: the
document is marked not applicable, states that no software-visible registers
exist, and says no register file is required.  These tests keep that evidence
separate from an empty or contradictory document and from an OTP-positive
document.
"""
from __future__ import annotations

import json
from pathlib import Path

from programs.phase1_one_shot_runner import gen_l4_regmap
import spi_protocol_synth


_GEN_DIR = Path("phase1") / "generated_docs"

_EXPLICIT_ABSENCE = """\
---
layer: L5
status: not-applicable
---

# Register Map

This datapath has no SW-visible chip registers.
There is no control, status, configuration, or interrupt register.
No register file is required.
"""

_ACTUAL_L6_OTP_NEGATIVE = """\
---
layer: L6
ic: spm
status: not-applicable
---

# L6 — Calibration / Lab Procedures

## 適用性 — N/A

`spm` 為純數位設計,**無 analog/mixed-signal 內容**,因此:

- 無 trimming
- 無 OTP-based calibration
- 無 lab measurement procedure
- 無 analog bias adjustment
- 無溫度補償

→ 不需 Plugin 產生 calibration controller、OTP interface、analog trim DAC 等。
"""


def _read_l4(project: Path) -> dict:
    return json.loads((project / _GEN_DIR / "L4_REGMAP.json").read_text())


def _seed(project: Path, l4: dict) -> None:
    path = project / _GEN_DIR
    path.mkdir(parents=True, exist_ok=True)
    (path / "L4_REGMAP.json").write_text(json.dumps(l4), encoding="utf-8")


def test_actual_explicit_absence_sets_typed_no_register_flag(tmp_path: Path) -> None:
    gen_l4_regmap(tmp_path, {"L5_register_map.md": _EXPLICIT_ABSENCE})
    l4 = _read_l4(tmp_path)
    assert l4["registers"] == []
    assert l4["no_registers_in_input"] is True
    assert l4["register_map_present"] is False
    assert l4["otp_layout"] is None
    assert l4["no_otp_layout_in_input"] is True


def test_contradictory_register_declaration_stays_unknown(tmp_path: Path) -> None:
    contradictory = _EXPLICIT_ABSENCE + """

| Address | Name | Access | Description |
|---|---|---|---|
| 0x00 | CTRL | R/W | control register |
"""
    gen_l4_regmap(tmp_path, {"L5_register_map.md": contradictory})
    l4 = _read_l4(tmp_path)
    assert l4["registers"]
    assert l4["no_registers_in_input"] is False
    assert l4.get("register_map_present") is not False


def test_otp_positive_control_keeps_declared_geometry(tmp_path: Path) -> None:
    otp = """\
# Register Map

OTP layout: 128-byte one-time-programmable fuse array with lock bits.
"""
    gen_l4_regmap(tmp_path, {"otp_layout.md": otp})
    l4 = _read_l4(tmp_path)
    assert l4["otp_layout"] is not None
    assert l4["no_otp_layout_in_input"] is False
    assert l4["otp_layout"]["depth_bytes"] == 128
    assert l4["otp_layout"]["width_bits"] == 8


def test_otp_declaration_after_unrelated_negative_clause_is_positive(
        tmp_path: Path) -> None:
    """A negative clause must not erase a later real OTP declaration."""
    cases = (
        "There is no reset pin; an OTP bank stores trim data.\n",
        "No OTP-based calibration is used; an OTP bank stores the device "
        "identifier.\n",
    )
    for index, text in enumerate(cases):
        project = tmp_path / f"case_{index}"
        gen_l4_regmap(project, {f"otp_clause_{index}.md": text})
        l4 = _read_l4(project)
        assert l4["no_otp_layout_in_input"] is False
        assert l4["otp_layout"] is not None


def test_actual_l6_negative_otp_interface_does_not_create_layout(
        tmp_path: Path) -> None:
    """The actual SPM L6 wording names OTP only to reject its interface."""
    gen_l4_regmap(tmp_path, {"L6_calibration.md": _ACTUAL_L6_OTP_NEGATIVE})
    l4 = _read_l4(tmp_path)
    assert l4["otp_layout"] is None
    assert l4["no_otp_layout_in_input"] is True


def test_universal_overlay_does_not_add_register_defaults_to_explicit_absence(
        tmp_path: Path) -> None:
    _seed(tmp_path, {
        "registers": [],
        "register_map_present": False,
        "no_registers_in_input": True,
        "otp_layout": None,
        "no_otp_layout_in_input": True,
    })
    spi_protocol_synth._apply_universal(tmp_path / _GEN_DIR, False)
    l4 = _read_l4(tmp_path)
    assert l4["register_map_present"] is False
    assert "base_address" not in l4
    assert "notes" not in l4


def test_universal_overlay_keeps_supported_register_map_defaults(
        tmp_path: Path) -> None:
    _seed(tmp_path, {
        "registers": [{"name": "CTRL", "address": "0x00"}],
        "register_map_present": True,
    })
    spi_protocol_synth._apply_universal(tmp_path / _GEN_DIR, False)
    l4 = _read_l4(tmp_path)
    assert l4["register_map_present"] is True
    assert l4["base_address"]
    assert l4["notes"]
