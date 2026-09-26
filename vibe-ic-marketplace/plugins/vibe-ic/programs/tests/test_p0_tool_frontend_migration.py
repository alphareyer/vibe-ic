"""Tool front ends own generic P0 shape; design-intent gates remain separate."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import flow_compliance_check as flow  # noqa: E402
import flow_step_output_content_check as content  # noqa: E402
import p0_tool_frontend_check as frontend  # noqa: E402
import ic_class_profile as classes  # noqa: E402


#: THE RETIREMENT RECORD for the P0 regex gates this front end replaced
#: (1a27f263d). It lives in this TEST file on purpose: `gate_is_wired_check`
#: credits a gate name written as a table literal in a program that can spawn
#: as a dispatch, so the same record in `p0_tool_frontend_check.py` would read
#: as the retired gate's caller. `tests/` is not a wiring source.
#:
#: Each row names the Verilator code that now blocks the retired gate's only
#: ERROR-severity finding, and a line of REAL tool output carrying it
#: (vibeic-eda 0.3.79, sha256:93d88e9e…, Verilator 5.053) on the retired
#: gate's own known-bad shape. MEASURED on that image with `check()`, over the
#: retired checker's own test fixtures: 4 known-bad bit-selects refused as
#: SELRANGE, 4 known-good passed, and a parameter-width select the regex could
#: not see (`reg [W-1:0] idx` indexed `[6:0]`, W=5) refused too.
#:
#: `program`: `deleted` is the retirement of a gate file, because
#: `gate_is_wired_check` counts every `programs/*_check|_audit.py` stem and a
#: retired gate left there reads as a gate nothing invokes. `library` means
#: other programs still import the file for its parser, which keeps it wired.
RETIRED_REGEX_GATES = {
    "bitwidth_consistency_check": {
        "blocking_code": "SELRANGE",
        "retired_finding": "bitselect-out-of-range: a [hi:lo] select outside "
                           "the signal's declared range",
        "tool_line": "%Warning-SELRANGE: top.v:7:40: Selection index out of "
                     "range: 6:0 outside 4:0",
        "program": "deleted",
    },
    "module_port_audit": {
        "blocking_code": "PINNOTFOUND",
        "retired_finding": "MISMATCH: a named connection to a port the "
                           "instantiated module does not declare (its "
                           "WIDTH_MISMATCH and UNCONNECTED were WARN, and stay "
                           "visible as WIDTH*/PINMISSING diagnostics)",
        "tool_line": "%Error-PINNOTFOUND: top.v:7:25: Pin not found: "
                     "'sys_clk_5m'",
        "program": "library",
    },
}


def _project(tmp_path):
    rtl = tmp_path / "phase2/stage1/rtl"
    rtl.mkdir(parents=True)
    (rtl / "top.sv").write_text("module top(input logic a); endmodule\n")
    docs = tmp_path / "phase1/generated_docs"
    docs.mkdir(parents=True)
    (docs / "L9_INTEGRATION_SPEC.json").write_text(json.dumps({"top_module": "top"}))
    return tmp_path


def test_tool_codes_replace_only_covered_regex_gates(monkeypatch, tmp_path):
    project = _project(tmp_path)
    calls = []

    def fake(tool, args, root, image):
        calls.append((tool, args))
        if tool == "verilator":
            return subprocess.CompletedProcess([], 0, "", "%Warning-SELRANGE: top.sv:1:1: out of range\n")
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(frontend, "_invoke", fake)
    result = frontend.check(project, "test-image")
    assert not result["passed"]
    assert "Verilator Warning-SELRANGE" in result["findings"]
    assert [name for name, _ in calls] == ["yosys", "verilator"]
    assert "hierarchy -check -top top" in calls[0][1][-1]
    assert "p0_tool_frontend_check" not in flow._STRUCTURAL_RTL_GATES
    assert "bitwidth_consistency_check" not in flow._STRUCTURAL_RTL_GATES
    assert "module_port_audit" not in flow._STRUCTURAL_RTL_GATES
    # Current released Verilator accepts this SV extension without a code.
    assert "function_void_with_output_check" in flow._STRUCTURAL_RTL_GATES


def test_no_frontend_execution_cannot_pass(monkeypatch, tmp_path):
    project = _project(tmp_path)
    monkeypatch.setattr(frontend, "_invoke", lambda *args: (_ for _ in ()).throw(FileNotFoundError("missing")))
    result = frontend.check(project, "test-image")
    assert not result["passed"]
    assert "unavailable" in result["findings"][0]


def test_unconnected_and_width_warnings_remain_visible_without_new_block(monkeypatch, tmp_path):
    project = _project(tmp_path)

    def fake(tool, args, root, image):
        text = ("%Warning-PINMISSING: top.sv:1:1: missing pin\n"
                "%Warning-WIDTHEXPAND: top.sv:1:1: width mismatch\n") if tool == "verilator" else ""
        return subprocess.CompletedProcess([], 0, "", text)

    monkeypatch.setattr(frontend, "_invoke", fake)
    result = frontend.check(project, "test-image")
    assert result["passed"]
    assert [row["code"] for row in result["tools"]["Verilator.Lint"]["diagnostics"]] == [
        "PINMISSING", "WIDTHEXPAND"]


def test_rtl_content_delegates_parser_and_retains_source_presence(monkeypatch, tmp_path):
    project = _project(tmp_path)
    rtl = project / "phase2/stage1/rtl/top.sv"
    rtl.write_text("module top; // endmodule appears only in a comment\n")
    calls = []
    monkeypatch.setattr(frontend, "check", lambda *args: (calls.append(args) or
                                                    {"passed": True, "findings": []}))
    assert content.check(project, "rtl") == []
    assert calls
    rtl.write_text("")
    assert content.check(project, "rtl")


def test_bus_intent_uses_the_declared_class(monkeypatch, tmp_path):
    monkeypatch.setattr(classes, "detect_ic_class", lambda project: {"ic_class": "test_class"})
    monkeypatch.setattr(classes, "class_verification_flags", lambda name: {
        "registry_matched": True, "half_duplex_bus": False,
        "command_protocol_applicable": True, "analog_applicable": True,
        "verification_track": "generic_full_stack"})
    skipped = flow._class_skipped_gates(tmp_path)
    assert "self_rx_mask_check" in skipped
    assert "half_duplex_bus=false" in skipped["self_rx_mask_check"]
    assert "crc_completeness_check" not in skipped
    monkeypatch.setattr(classes, "class_verification_flags", lambda name: {
        "registry_matched": True, "half_duplex_bus": True,
        "command_protocol_applicable": True, "analog_applicable": True,
        "verification_track": "aid_protocol"})
    assert "self_rx_mask_check" not in flow._class_skipped_gates(tmp_path)


def test_command_intent_needs_class_and_l3_l4_declarations(monkeypatch, tmp_path):
    monkeypatch.setattr(classes, "detect_ic_class", lambda project: {"ic_class": "test_class"})
    monkeypatch.setattr(classes, "class_verification_flags", lambda name: {
        "registry_matched": True, "half_duplex_bus": True,
        "command_protocol_applicable": False, "analog_applicable": True,
        "verification_track": "generic_full_stack"})
    assert "cmd_arg_range_validation_check" not in flow._class_skipped_gates(tmp_path)
    monkeypatch.setattr(flow, "_ldocs_record_no_opcodes", lambda project: True)
    assert "cmd_arg_range_validation_check" in flow._class_skipped_gates(tmp_path)


@pytest.mark.parametrize("gate", sorted(RETIRED_REGEX_GATES))
def test_a_retired_gate_is_replaced_by_a_blocking_tool_code(gate, monkeypatch, tmp_path):
    """The retirement is true only while the replacement still refuses the
    retired gate's finding: the code blocks, the real tool line carrying it
    fails `check()`, and the clause that runs `check()` is still in P0."""
    rec = RETIRED_REGEX_GATES[gate]
    assert rec["blocking_code"] in frontend.BLOCKING_CODES, gate
    project = _project(tmp_path)

    def fake(tool, args, root, image):
        text = rec["tool_line"] + "\n" if tool == "verilator" else ""
        return subprocess.CompletedProcess([], 0, "", text)

    monkeypatch.setattr(frontend, "_invoke", fake)
    result = frontend.check(project, "test-image")
    assert not result["passed"], gate
    assert any(f.endswith("-" + rec["blocking_code"]) for f in result["findings"])
    flow_yaml = (PROGRAMS.parent / "flow" / "phase1_phase2_phase3.yaml").read_text()
    assert 'program_exit_zero: "flow_step_output_content_check . --mode rtl"' in flow_yaml


@pytest.mark.parametrize("gate", sorted(RETIRED_REGEX_GATES))
def test_a_retired_gate_leaves_the_umbrella_and_its_registers(gate):
    """Out of the P0 umbrella, and so out of the register of gates the
    umbrella carries but cannot drive (vibe-ic#559)."""
    assert gate not in flow._STRUCTURAL_RTL_GATES
    assert gate not in flow._UNDRIVABLE_BY_STRUCTURAL_UMBRELLA


@pytest.mark.parametrize("gate", sorted(RETIRED_REGEX_GATES))
def test_a_retired_gate_file_is_deleted_or_still_a_library(gate):
    """A deleted gate cannot sit in `gate_is_wired_check`'s population as a
    gate nothing invokes; a library one must still exist for its importers."""
    path = PROGRAMS / f"{gate}.py"
    program = RETIRED_REGEX_GATES[gate]["program"]
    assert program in ("deleted", "library"), program
    assert path.exists() is (program == "library"), path
