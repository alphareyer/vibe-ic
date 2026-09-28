"""A progressing harness tool is not an RTL failure; a stall is a tool error."""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import harness_exact_selfverify as H  # noqa: E402
import _progress_run as P  # noqa: E402


@pytest.mark.parametrize("gate", ["compile", "lint", "functional"])
def test_harness_long_calls_use_progress_supervision(monkeypatch, tmp_path, gate):
    rtl = tmp_path / "top.sv"
    rtl.write_text("module top; endmodule\n")
    tb = tmp_path / "tb.sv"
    tb.write_text("module tb; top dut(); initial $display(\"PASS\"); endmodule\n")
    monkeypatch.setattr(H._tool_route, "unavailable", lambda *args: None)
    monkeypatch.setattr(H._tool_route, "run", lambda *a, **k: pytest.fail("wall deadline used"))
    calls = []

    def progressing(argv, **kw):
        calls.append((argv, kw))
        return subprocess.CompletedProcess(argv, 0, "PASS\n" if argv[0] == "vvp" else "", "")

    monkeypatch.setattr(H._tool_route, "supervised_run", progressing)
    if gate == "compile":
        result = H.gate_a_standalone_compile(rtl, "top", tmp_path, True)
    elif gate == "lint":
        result = H.gate_b_verilator_lint(rtl, "top", tmp_path, True)
    else:
        result = H.gate_c_functional_tb(rtl, tb, tmp_path, True)
    assert result["verdict"] == "PASS", result
    assert calls and all("timeout" not in kw and kw["stall_looks"] >= 4 for _, kw in calls)


@pytest.mark.parametrize("gate", ["compile", "lint", "functional"])
def test_harness_stall_is_tool_error_not_design_block(monkeypatch, tmp_path, gate):
    rtl = tmp_path / "top.sv"
    rtl.write_text("module top; endmodule\n")
    tb = tmp_path / "tb.sv"
    tb.write_text("module tb; top dut(); endmodule\n")
    monkeypatch.setattr(H._tool_route, "unavailable", lambda *args: None)
    monkeypatch.setattr(H._tool_route, "run", lambda argv, **kw: (_ for _ in ()).throw(
        subprocess.TimeoutExpired(argv, kw["timeout"])))

    def stalled(argv, **kw):
        if gate == "functional" and argv[0] == "iverilog":
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise P.Stalled(argv, 4, 0.25, 1.0, {"output": True}, out="progress stopped")

    monkeypatch.setattr(H._tool_route, "supervised_run", stalled)
    if gate == "compile":
        result = H.gate_a_standalone_compile(rtl, "top", tmp_path, True)
    elif gate == "lint":
        result = H.gate_b_verilator_lint(rtl, "top", tmp_path, True)
    else:
        result = H.gate_c_functional_tb(rtl, tb, tmp_path, True)
    assert result["verdict"] == "ERROR", result
    assert "STALLED" in result["reason"] and "progress stopped" in result["reason"]
