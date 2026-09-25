"""Tool front ends own generic P0 shape; design-intent gates remain separate."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import flow_compliance_check as flow  # noqa: E402
import flow_step_output_content_check as content  # noqa: E402
import p0_tool_frontend_check as frontend  # noqa: E402
import ic_class_profile as classes  # noqa: E402


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
