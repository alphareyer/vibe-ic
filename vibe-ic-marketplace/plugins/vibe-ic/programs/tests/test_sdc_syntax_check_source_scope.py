"""Step-7 SDC scope and OpenSTA receipt controls.

The syntax arm audits the design's staged/declared SDC population.  LibreLane
intermediate decks are tool output and are judged by the tool receipt instead;
they must not become design failures merely because they have an ``.sdc``
suffix.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
PROGRAMS = TESTS.parent
sys.path.insert(0, str(PROGRAMS))

import sdc_syntax_check as S  # noqa: E402


GOOD = (
    "create_clock -name clk -period 10 [get_ports clk]\n"
    "set_input_delay 2 -clock clk [get_ports din]\n"
    "set_output_delay 2 -clock clk [get_ports dout]\n"
)


def _write(project: Path, rel: str, text: str) -> Path:
    path = project / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _dual_project(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    _write(project, "phase3/librelane_switch.json",
           json.dumps({"steps": {"8": "dual"}}))
    deck = _write(project, "phase3/stage3/pnr/constraint.sdc", GOOD)
    _write(project, "phase2/stage2/constraints/top.asic.sdc", GOOD)
    return project, deck


def _receipt(project: Path, deck: Path, *, sdc: str | None = None) -> Path:
    gate = project / "phase3/librelane/prelayout/gates/step8_sdc_opensta.json"
    gate.parent.mkdir(parents=True, exist_ok=True)
    target = Path(sdc) if sdc is not None else deck
    digest = (hashlib.sha256(target.read_bytes()).hexdigest()
              if target.is_file() else "stale-receipt")
    gate.write_text(json.dumps({
        "sdc": str(target), "sdc_sha256": digest,
        "verdict": "PASS", "findings": [],
    }))
    return gate


def test_tool_generated_abc_sdc_is_outside_the_design_scope(tmp_path):
    project = tmp_path / "project"
    _write(project, "phase3/librelane_switch.json",
           json.dumps({"steps": {"8": "direct"}}))
    _write(project, "phase2/stage2/constraints/top.asic.sdc", GOOD)
    abc = _write(project, "phase3/librelane/02-yosys-synthesis/synthesis.abc.sdc", GOOD)

    before = S.audit(str(project))
    abc.write_text("# ABC intermediate\nset_wire_load_mode top\n")
    result = S.audit(str(project))

    assert before.passed is True, before.findings
    assert result.passed is True, result.findings
    assert result.summary["files_checked"] == 1
    assert not any(f.file.endswith("synthesis.abc.sdc") for f in result.findings)


def test_real_step7_sdc_failure_survives_intermediate_exclusion(tmp_path):
    project = tmp_path / "project"
    _write(project, "phase3/librelane_switch.json",
           json.dumps({"steps": {"8": "direct"}}))
    source = _write(project, "phase2/stage2/constraints/top.asic.sdc", GOOD)
    _write(project, "phase3/librelane/02-yosys-synthesis/synthesis.abc.sdc",
           "# ABC intermediate\nset_wire_load_mode top\n")

    assert S.audit(str(project)).passed is True
    source.write_text("set_input_delay 2 -clock clk [get_ports din]\n")
    result = S.audit(str(project))
    errors = [f.rule for f in result.findings if f.severity == "ERROR"]

    assert result.passed is False
    assert "NO_CREATE_CLOCK" in errors
    assert "NO_TIMING_CONSTRAINT" not in errors
    assert all(not f.file.endswith("synthesis.abc.sdc") for f in result.findings)


def test_dual_mode_keeps_stale_opensta_receipt_blocking(tmp_path):
    project, deck = _dual_project(tmp_path)
    _write(project, "phase3/librelane/02-yosys-synthesis/synthesis.abc.sdc",
           "# ABC intermediate\nset_wire_load_mode top\n")
    _receipt(project, deck, sdc="/old/run/project/phase3/stage3/pnr/constraint.sdc")

    result = S.audit(str(project))
    errors = [f.rule for f in result.findings if f.severity == "ERROR"]

    assert result.passed is False
    assert "OPENSTA_GATE_STALE" in errors
    assert "NO_CREATE_CLOCK" not in errors
    assert "NO_TIMING_CONSTRAINT" not in errors


def test_dual_mode_rejects_receipt_for_another_existing_project(tmp_path):
    project, deck = _dual_project(tmp_path)
    old_project = tmp_path / "old-run"
    old_deck = _write(old_project, "phase3/stage3/pnr/constraint.sdc", GOOD)
    _receipt(project, deck, sdc=str(old_deck))

    result = S.audit(str(project))

    assert result.passed is False
    assert any(f.rule == "OPENSTA_GATE_STALE" for f in result.findings)


def test_dual_mode_accepts_a_current_opensta_receipt(tmp_path):
    project, deck = _dual_project(tmp_path)
    _receipt(project, deck)

    result = S.audit(str(project))

    assert result.passed is True, result.findings
    assert result.summary["files_checked"] == 1
