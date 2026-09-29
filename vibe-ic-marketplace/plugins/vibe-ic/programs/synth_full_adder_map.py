#!/usr/bin/env python3
"""Derive a Yosys $fa techmap from a Liberty cell's proven truth table.

The map is an optional synthesis actuator.  It is never chosen without the
post-route recipe ledger, and a cell name alone is never evidence of function.
"""
from __future__ import annotations

import os
import math
import re
import sys
from itertools import product
from typing import Optional

if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lec_post_layout_check import (
    _LIB_CELL_RE, _LibertyFn, _brace_block, _parse_liberty_pins)

_VERILOG_ID = re.compile(r"[A-Za-z_][A-Za-z_0-9$]*\Z")


def discover(liberty: str) -> Optional[tuple[str, tuple[str, str, str], str, str]]:
    """Return (cell, inputs, carry, sum), or None if no proven lowest-area cell.

    Each candidate must expose exactly three scalar inputs and two scalar
    outputs.  All eight input combinations must match majority and XOR3.
    Unsupported Liberty function syntax is rejected, never partially parsed.
    """
    areas = {}
    for cell_match in _LIB_CELL_RE.finditer(liberty):
        body, _ = _brace_block(liberty, cell_match.end())
        area_match = re.search(r"\barea\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*;", body)
        if area_match:
            area = float(area_match.group(1))
            if math.isfinite(area) and area > 0:
                areas[cell_match.group(1)] = area
    matches = []
    for cell, pins in _parse_liberty_pins(liberty).items():
        ins = pins["inputs"]
        outs = pins["outputs"]
        if (len(ins) != 3 or len(outs) != 2 or
                not all(_VERILOG_ID.fullmatch(p) for p in [cell, *ins, *outs])):
            continue
        functions = {}
        try:
            for name, expression in outs.items():
                if not isinstance(expression, str):
                    raise ValueError("output function absent")
                tokens = _LibertyFn._TOK.findall(expression)
                if "".join(tokens) != re.sub(r"\s+", "", expression):
                    raise ValueError("unsupported Liberty syntax")
                functions[name] = _LibertyFn(expression)
            tables = {name: [] for name in outs}
            for values in product((False, True), repeat=3):
                env = dict(zip(ins, values))
                for name, function in functions.items():
                    tables[name].append(function(env))
        except (KeyError, ValueError, IndexError, RecursionError):
            continue
        carry = tuple(sum(bits) >= 2 for bits in product((False, True), repeat=3))
        summation = tuple(sum(bits) % 2 == 1 for bits in product((False, True), repeat=3))
        carry_pins = [name for name, truth in tables.items() if tuple(truth) == carry]
        sum_pins = [name for name, truth in tables.items() if tuple(truth) == summation]
        if len(carry_pins) == 1 and len(sum_pins) == 1 and cell in areas:
            matches.append((areas[cell], cell, tuple(ins), carry_pins[0],
                            sum_pins[0]))
    if not matches:
        return None
    matches.sort()
    return matches[0][1:] if len(matches) == 1 or matches[0][0] < matches[1][0] else None


def verilog_map(spec: tuple[str, tuple[str, str, str], str, str]) -> str:
    """Map Yosys $fa X=carry and Y=sum for every WIDTH bit."""
    cell, inputs, carry, summation = spec
    return ("(* techmap_celltype = \"$fa\" *)\n"
            "module _vibeic_full_adder_map #(parameter WIDTH = 1) "
            "(input [WIDTH-1:0] A, B, C, output [WIDTH-1:0] X, Y);\n"
            "  genvar i;\n  generate for (i = 0; i < WIDTH; i = i + 1) "
            "begin: bits\n"
            f"    {cell} mapped (.{inputs[0]}(A[i]), .{inputs[1]}(B[i]), "
            f".{inputs[2]}(C[i]), .{carry}(X[i]), .{summation}(Y[i]));\n"
            "  end endgenerate\nendmodule\n")
