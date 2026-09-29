"""Observed clock ports from the real helper and its actual caller regexes."""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import _hdl_code_text  # noqa: E402


def _real_clock_scan():
    source = PROGRAMS / "design_one_shot_runner.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    helper = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and node.name == "_cdc_top_clock_ports")
    names = {"_CDC_MODULE_RE", "_INPUT_PORT_RE", "_CDC_RST_RE"}
    patterns = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id in names
                        for target in node.targets)]
    assert len(patterns) == len(names)
    namespace = {"re": re, "Path": Path, "Dict": Dict, "List": List,
                 "Optional": Optional, "Tuple": Tuple,
                 "_hdl_code_text": _hdl_code_text}
    exec(compile(ast.Module(body=patterns + [helper], type_ignores=[]),
                 str(source), "exec", dont_inherit=True), namespace)
    return (namespace["_cdc_top_clock_ports"], namespace["_INPUT_PORT_RE"],
            namespace["_CDC_RST_RE"])


def _clock_ports(tmp_path, noncode):
    scan, input_regex, reset_regex = _real_clock_scan()
    rtl = tmp_path / "ports.sv"
    rtl.write_text("""module probe_top(
    input logic clk_real,
    input logic clock_aux,
    input logic reset_clock,
    input logic data,
    output logic clock_output
);
NONCODE
endmodule
module probe_child(input logic clk_child); endmodule
""".replace("NONCODE", noncode), encoding="utf-8")
    return scan([rtl], "probe_top", tmp_path, input_regex, reset_regex)


@pytest.mark.parametrize("noncode", [
    "// input clk_phantom is only a comment",
    "/* input clk_phantom\n   is only a comment */",
    'initial $display("input clk_phantom");',
], ids=["line_comment", "block_comment", "quoted_string"])
def test_real_clock_scan_ignores_noncode_declarations(tmp_path, noncode):
    ports, scope = _clock_ports(tmp_path, noncode)
    assert sorted(ports) == ["clk_real", "clock_aux"]
    assert scope == "top module 'probe_top' (resolved via --top-name)"


def test_real_clock_scan_keeps_real_top_ports_and_reset_filtering(tmp_path):
    ports, scope = _clock_ports(tmp_path, "")
    assert sorted(ports) == ["clk_real", "clock_aux"]
    assert scope == "top module 'probe_top' (resolved via --top-name)"
