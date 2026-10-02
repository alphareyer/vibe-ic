"""Conservative positional-port driver resolution for ``rtl_hygiene_lint``.

The parser is shared with ``module_port_audit``.  These fixtures are neutral,
compile-shaped RTL and exercise the actual linter consumer: only a resolved
same-file child output/inout formal may credit a parent actual as driven.
"""

from pathlib import Path
import shutil
import subprocess
import sys

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import rtl_hygiene_lint as lint  # noqa: E402


def _undriven(source: str):
    findings = lint.rule_undriven_and_unread(
        lint.strip_comments(source), "fixture.sv")
    return sorted(f.symbol for f in findings if f.rule == "undriven-wire")


ANSI = """
module leaf #(parameter W = 8) (
    input logic [W-1:0] data_i,
    output logic [W-1:0] data_o
);
    assign data_o = data_i;
endmodule
"""


def test_parameterized_ansi_output_actual_is_a_driver():
    source = ANSI + """
module top(input logic [7:0] source, output logic [7:0] result);
    logic [7:0] routed;
    leaf #(.W(8)) u_leaf(source, routed);
    assign result = routed;
endmodule
"""
    assert _undriven(source) == []


def test_non_ansi_header_order_beats_declaration_order():
    source = """
module leaf(data_i, data_o);
    output data_o;
    input data_i;
    assign data_o = data_i;
endmodule
module top(input source, output result);
    wire routed;
    leaf u_leaf(source, routed);
    assign result = routed;
endmodule
"""
    assert _undriven(source) == []


def test_empty_positional_actual_keeps_later_output_position():
    source = ANSI + """
module top(input logic [7:0] source, output logic [7:0] result);
    logic [7:0] routed;
    leaf u_leaf(, routed);
    assign result = routed;
endmodule
"""
    assert _undriven(source) == []


def test_concatenated_output_actuals_are_each_driven():
    source = """
module leaf(input data_i, output [1:0] data_o);
    assign data_o = {data_i, data_i};
endmodule
module top(input source, output [1:0] result);
    wire hi, lo;
    leaf u_leaf(source, {hi, lo});
    assign result = {hi, lo};
endmodule
"""
    assert _undriven(source) == []


def test_positional_input_actual_does_not_count_as_a_driver():
    source = """
module leaf(input data_i, output data_o);
    assign data_o = data_i;
endmodule
module top(input source, output result);
    wire routed;
    leaf u_leaf(routed, result);
    assign result = routed;
endmodule
"""
    assert _undriven(source) == ["routed"]


def test_true_floating_wire_remains_an_error():
    source = """
module top(input source, output result);
    wire floating;
    assign result = source;
endmodule
"""
    assert _undriven(source) == ["floating"]


@pytest.mark.parametrize("connection", [
    "missing u(source, routed);",
    "leaf u(source, routed, extra);",
    "leaf u(source, routed[);",
    "leaf u(source, routed, .unexpected(extra));",
])
def test_unresolved_unsupported_and_out_of_range_forms_stay_conservative(connection):
    source = """
module leaf(input data_i, output data_o);
    assign data_o = data_i;
endmodule
module top(input source, output result);
    wire routed;
    wire extra;
    %s
    assign result = routed;
endmodule
""" % connection
    symbols = _undriven(source)
    assert "extra" in symbols or "routed" in symbols


def test_named_port_baseline_remains_clean():
    source = """
module leaf(input data_i, output data_o);
    assign data_o = data_i;
endmodule
module top(input source, output result);
    wire routed;
    leaf u_leaf(.data_i(source), .data_o(routed));
    assign result = routed;
endmodule
"""
    assert _undriven(source) == []


@pytest.mark.skipif(shutil.which("iverilog") is None or shutil.which("vvp") is None,
                    reason="native Icarus is supplied by the EDA image")
@pytest.mark.parametrize("style", ["positional", "named"])
def test_positional_and_named_wiring_simulate(style, tmp_path):
    connection = ("leaf u_leaf(source, routed);" if style == "positional"
                  else "leaf u_leaf(.data_i(source), .data_o(routed));")
    design = f"""
module leaf(input wire data_i, output wire data_o);
    assign data_o = data_i;
endmodule
module top(input wire source, output wire result);
    wire routed;
    {connection}
    assign result = routed;
endmodule
"""
    tb = """
module tb;
    reg source;
    wire result;
    top dut(source, result);
    initial begin
        source = 1'b0;
        #1 if (result !== 1'b0) $fatal(1, "zero did not propagate");
        source = 1'b1;
        #1 if (result !== 1'b1) $fatal(1, "one did not propagate");
        $finish;
    end
endmodule
"""
    design_path = tmp_path / f"{style}.sv"
    tb_path = tmp_path / f"{style}_tb.sv"
    out_path = tmp_path / f"{style}.vvp"
    design_path.write_text(design)
    tb_path.write_text(tb)
    compile_result = subprocess.run(
        ["iverilog", "-g2012", "-s", "tb", "-o", str(out_path),
         str(design_path), str(tb_path)], capture_output=True, text=True)
    assert compile_result.returncode == 0, compile_result.stderr
    run_result = subprocess.run(["vvp", str(out_path)],
                                capture_output=True, text=True)
    assert run_result.returncode == 0, run_result.stdout + run_result.stderr
