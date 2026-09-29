"""#2848: real elaboration of neutral port ranges, then real Icarus comparison.

The register and reference below are the complete public issue fixtures. Live
simulation requires Icarus; an absent tool is NOT_VERIFIED, never AGREE. Scratch
paths use mkdtemp because the image's default pytest user can contain a newline.
"""
from __future__ import annotations

import builtins
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import diff_verify_harness as dvh


SYMBOLIC = """module vector_register #(
    parameter integer WIDTH = 8
) (
    input wire clk,
    input wire rst_n,
    input wire [WIDTH-1:0] data_in,
    output reg [WIDTH-1:0] data_out
);
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) data_out <= 0;
        else data_out <= data_in;
    end
endmodule
"""
LITERAL = """module vector_register (
    input wire clk,
    input wire rst_n,
    input wire [7:0] data_in,
    output reg [7:0] data_out
);
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) data_out <= 0;
        else data_out <= data_in;
    end
endmodule
"""
REFERENCE = "def ref(seq):\n    return [0] + list(seq[:-1])\n"


@pytest.fixture
def elaborator():
    return pytest.importorskip("pyslang", reason="NOT_VERIFIED: pyslang absent")


@pytest.fixture
def scratch():
    with tempfile.TemporaryDirectory(prefix="dvh2848_") as d:
        yield Path(d)


@pytest.fixture
def live_tools(elaborator):
    missing = [n for n in ("iverilog", "vvp") if shutil.which(n) is None]
    if missing:
        pytest.skip(f"NOT_VERIFIED: missing {missing}")


def _files(scratch, code):
    rtl, ref = scratch / "register.sv", scratch / "ref.py"
    rtl.write_text(code)
    ref.write_text(REFERENCE)
    return rtl, ref


@pytest.mark.parametrize("code", [SYMBOLIC, LITERAL], ids=["symbolic", "literal"])
def test_public_fixture_resolves_actual_ports(elaborator, code):
    name, ports, error = dvh.parse_ports(code, "vector_register")
    observed = [(p.name, p.direction, p.width) for p in ports]
    assert observed == [("clk", "input", 1), ("rst_n", "input", 1),
                        ("data_in", "input", 8), ("data_out", "output", 8)]
    assert (name, error) == ("vector_register", "")


@pytest.mark.parametrize("default,bounds,width", [
    ("8'd8", "WIDTH-1:0", 8),
    ("(1 << 3)", "WIDTH-1:0", 8),
    ("$clog2(256)", "WIDTH-1:0", 8),
    ("8", "0:WIDTH-1", 8),
    ("8", "WIDTH+3:4", 8),
    ("8", "-WIDTH:-1", 8),
    ("8", "(WIDTH*2)-1:WIDTH", 8),
], ids=["sized", "shift", "clog2", "ascending", "offset", "negative", "arithmetic"])
def test_tool_resolves_constant_ranges(elaborator, default, bounds, width):
    code = SYMBOLIC.replace("WIDTH = 8", f"WIDTH = {default}")
    code = code.replace("WIDTH-1:0", bounds)
    name, ports, error = dvh.parse_ports(code, "vector_register")
    assert [(p.name, p.width) for p in ports[-2:]] == [
        ("data_in", width), ("data_out", width)]
    assert (name, error) == ("vector_register", "")


def test_elaborated_order_keeps_first_module_and_widest_tie_choice(elaborator):
    code = """module first #(parameter WIDTH=8)(input clk,
        input [WIDTH-1:0] z_in, a_in, input bit narrow,
        output [WIDTH-1:0] z_out, a_out);
        assign z_out = z_in; assign a_out = a_in;
        endmodule
        module wrapper(input x,output y); assign y=x; endmodule
    """
    name, ports, error = dvh.parse_ports(code, None)
    assert (name, error) == ("first", "")
    _, _, inputs, outputs = dvh._classify_ports(ports)
    assert [(p.name, p.width) for p in inputs] == [
        ("z_in", 8), ("a_in", 8), ("narrow", 1)]
    assert [(p.name, p.width) for p in outputs] == [("z_out", 8), ("a_out", 8)]
    assert max(inputs, key=lambda p: (p.width, -inputs.index(p))).name == "z_in"
    assert max(outputs, key=lambda p: (p.width, -outputs.index(p))).name == "z_out"
    name, ports, error = dvh.parse_ports(code, "wrapper")
    assert (name, error) == ("wrapper", "")
    assert [(p.name, p.width) for p in ports] == [("x", 1), ("y", 1)]


def test_nonansi_parameter_defaults(elaborator):
    code = """module dut(clk, data_in, data_out);
        parameter WIDTH=8;
        input clk;
        input [WIDTH-1:0] data_in;
        output reg [WIDTH-1:0] data_out;
        always @(posedge clk) data_out <= data_in;
        endmodule
    """
    name, ports, error = dvh.parse_ports(code, "dut")
    assert [(p.name, p.direction, p.width) for p in ports] == [
        ("clk", "input", 1), ("data_in", "input", 8), ("data_out", "output", 8)]
    assert (name, error) == ("dut", "")


@pytest.mark.parametrize("code", [
    SYMBOLIC.replace("WIDTH-1:0", "MISSING-1:0"),
    SYMBOLIC.replace("WIDTH = 8", "WIDTH = 'x"),
    SYMBOLIC.replace("WIDTH = 8", "WIDTH = 8/0"),
    SYMBOLIC.replace("[WIDTH-1:0] data_in", "[WIDTH-1:0] data_in [2]"),
], ids=["undeclared", "unknown", "division-zero", "unpacked"])
def test_unresolved_or_unsupported_ports_refuse_before_tb(elaborator, scratch, monkeypatch, code):
    rtl, ref = _files(scratch, code)
    events = []

    def no_reference(*args):
        events.append("reference")
        raise ValueError("reference reached despite unresolved ports")

    monkeypatch.setattr(dvh, "run_rtl_sequence", lambda *a: (None, "simulation reached"))
    monkeypatch.setattr(dvh, "load_reference", no_reference)
    report = dvh.diff_verify(rtl, ref, "vector_register", "all", 16, 0)
    assert report["verdict"] == "ERROR", report
    assert report["reason"].startswith("port parse failed: DIFF_PORT_"), report
    assert "driven_input" not in report and "sampled_output" not in report
    assert events == []


@pytest.mark.parametrize("require_tools,verdict,rc", [(False, "SKIP", 0), (True, "ERROR", 2)])
def test_missing_elaborator_is_not_verified(scratch, monkeypatch, capsys, require_tools, verdict, rc):
    rtl, ref = _files(scratch, SYMBOLIC)
    original = builtins.__import__

    def absent(name, *args, **kwargs):
        if name == "pyslang":
            raise ImportError("pyslang deliberately absent")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", absent)
    report = dvh.diff_verify(rtl, ref, "vector_register", "all", 16, 0, require_tools)
    assert (report["verdict"], report["tool_available"]) == (verdict, False)
    assert "DIFF_PORT_TOOL_UNAVAILABLE" in report["reason"]
    assert "NOT_VERIFIED" in report["reason"]
    args = ["--rtl", str(rtl), "--ref", str(ref), "--top", "vector_register"]
    assert dvh.main(args + (["--require-tools"] if require_tools else [])) == rc
    captured = capsys.readouterr()
    assert "AGREE" not in captured.out


@pytest.mark.parametrize("code", [SYMBOLIC, LITERAL], ids=["symbolic", "literal"])
def test_public_fixture_compiles_and_compares_all_22_sequences(live_tools, scratch, code):
    rtl, ref = _files(scratch, code)
    standalone = subprocess.run(["iverilog", "-g2012", "-s", "vector_register",
                                 "-o", str(scratch / "standalone.vvp"), str(rtl)],
                                capture_output=True, text=True)
    assert standalone.returncode == 0, standalone.stderr
    report_path = scratch / "report.json"
    result = subprocess.run([sys.executable, str(Path(dvh.__file__)),
                             "--rtl", str(rtl), "--ref", str(ref),
                             "--top", "vector_register", "--vectors",
                             "directed+random+boundary", "--cycles", "16",
                             "--require-tools", "--json", str(report_path)],
                            capture_output=True, text=True)
    report = json.loads(report_path.read_text())
    assert (result.returncode, report["verdict"], report["n_sequences"]) == (0, "AGREE", 22), report
    assert report["driven_input"] == {"name": "data_in", "width": 8}
    assert report["sampled_output"] == {"name": "data_out", "width": 8}
    assert result.stdout.strip() == "AGREE"


@pytest.mark.parametrize("rhs", ["data_in + 1'b1", "{data_in[0], data_in[7:1]}"],
                         ids=["arithmetic", "bit-order"])
def test_changed_datapath_disagrees(live_tools, scratch, rhs):
    rtl, ref = _files(scratch, SYMBOLIC.replace("data_out <= data_in;", f"data_out <= {rhs};"))
    report = dvh.diff_verify(rtl, ref, "vector_register", "all", 16, 0, True)
    assert report["verdict"] == "MISMATCH", report
    mismatch = report["first_mismatch"]
    assert mismatch["signal"] == "data_out"
    assert mismatch["rtl"] != mismatch["ref"]


def test_simulation_only_body_does_not_require_synthesis(live_tools, scratch):
    code = SYMBOLIC.replace("always @(posedge clk or negedge rst_n)",
                            "always @(posedge clk or negedge clk)")
    rtl, ref = _files(scratch, code)
    report = dvh.diff_verify(rtl, ref, "vector_register", "all", 16, 0, True)
    assert (report["verdict"], report["n_sequences"]) == ("AGREE", 22), report
