"""r34: a top module's own parameter defaults resolve its port widths.

MEASURED on subservient x gf180mcuD (a DIE whose top is the core):
`output wire [AW-1:0] o_sram_addr` with `parameter integer AW = 10` made
slot_pad_budget answer UNDECIDED ("parameterised and no value was supplied"),
although a top has no parent to override AW and synthesis builds 10 bits.

Both directions: integer-literal defaults resolve; an expression default, a
parameter of another module, and a caller-supplied --param all keep the rule
that nothing is guessed (the caller's value wins; unresolved stays None).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import slot_pad_budget_check as S  # noqa: E402

TOP = """module top #(
    parameter integer MEMSIZE = 1024,
    parameter integer AW = 10,
    parameter [31:0] RESET_PC = 32'h0000_0000,
    parameter W = 8'd4,
    parameter DEPTH = $clog2(MEMSIZE)
) (
    input wire i_clk,
    output wire [AW-1:0] o_addr,
    output wire [W-1:0] o_w,
    output wire [DEPTH-1:0] o_depth
);
endmodule
module other #(parameter integer ZZ = 3) (output wire [ZZ-1:0] o_zz);
endmodule
"""


def _width(ports, name):
    return next(p["width"] for p in ports if p["name"] == name)


def test_integer_literal_defaults_of_the_top_are_read():
    d = S.top_parameter_defaults(TOP, "top")
    assert d["AW"] == 10 and d["MEMSIZE"] == 1024 and d["W"] == 4
    ports = S.parse_top_ports(TOP, "top", d)
    assert _width(ports, "o_addr") == 10 and _width(ports, "o_w") == 4


def test_an_expression_default_stays_unresolved():
    d = S.top_parameter_defaults(TOP, "top")
    assert "DEPTH" not in d
    assert _width(S.parse_top_ports(TOP, "top", d), "o_depth") is None


def test_another_modules_parameters_are_not_read():
    assert "ZZ" not in S.top_parameter_defaults(TOP, "top")
    assert S.top_parameter_defaults(TOP, "absent") == {}


def test_without_defaults_the_width_is_still_unresolved():
    assert _width(S.parse_top_ports(TOP, "top", None), "o_addr") is None


def test_a_supplied_param_wins_over_the_default(tmp_path):
    rtl = tmp_path / "top.v"
    rtl.write_text(TOP)
    out = tmp_path / "r.json"
    S.main([str(tmp_path), "--rtl", str(rtl), "--top", "top",
            "--param", "AW=12", "--json", str(out)])
    import json
    rep = json.loads(out.read_text())
    used = rep["params_from_top_module_defaults"]
    assert "AW" not in used and used["MEMSIZE"] == 1024
