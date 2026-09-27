#!/usr/bin/env python3
"""N9 — the shipped routed netlist declares each supply port exactly once.

MEASURED (subservient x gf180mcuD HARDMACRO, image 0.3.83, OpenROAD
26Q3-3002, 2026-09-28): `phase3/stage3/pnr/subservient_pnr.v` named `VDD, VSS`
five times in its module header and declared `inout VDD;` / `inout VSS;` five
times. iverilog: "'VDD' has already been declared in this scope" (rc 8);
yosys: "Duplicate module port `\\VDD'" (rc 1); OpenSTA read it silently, so
the flow shipped it and a judge found it.

ROOT CAUSE, reproduced in the image on a checkpoint of that run: every
`pdngen` creates a temporary BTerm per supply net that has none and destroys
it again when it gets no BPin. The STA top port the creation callback made is
never removed (dbSta.cc inDbBTermDestroy: "sta::NetworkEdit does not support
port removal"), so the next `pdngen` adds a SECOND port of the same name.
Counted: 1, 2, 3 ports after 1, 2, 3 `pdngen` calls; 0 after
`add_global_connection` / `global_connect` / `set_voltage_domain`; 0 from a
fresh process reading the saved ODB (the database holds no such BTerm). The
flow's PDN EM pre-sweep runs the PDN block once per candidate: 5 on
subservient -> 5 copies, 1 on spm -> 1 copy.

chip-, PDK- and vendor-AGNOSTIC: synthetic netlists only.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROG))

import phase3_one_shot_runner as R  # noqa: E402
import _netlist_port_decls as N  # noqa: E402


def _netlist(copies: int = 5, vss_dir: str = "inout", top: str = "dut") -> str:
    head = ",\n    ".join(["o_q", "i_clk", "i_d"] + ["VDD", "VSS"] * copies)
    decls = " output o_q;\n input i_clk;\n input [7:0] i_d;\n"
    for i in range(copies):
        decls += " inout VDD;\n" + f" {vss_dir if i else 'inout'} VSS;\n"
    return (f"module {top} ({head});\n{decls}\n wire _0_;\n"
            " cell_x u0 (.A(i_clk), .Z(o_q));\nendmodule\n")


# ── the structural rule ───────────────────────────────────────────────────

def test_the_measured_shape_is_found_and_reduced_to_one_declaration_each():
    """RED on main (the module does not exist)."""
    text = _netlist(5)
    status, found = N.problems(text, "dut")
    assert status == "PARSED"
    assert "port `VDD` named 5x in the module header" in found
    assert "port `VSS` declared 5x (inout, inout, inout, inout, inout)" in found
    new, rec = N.dedupe(text, "dut")
    assert rec["status"] == "DEDUPED"
    assert N.problems(new, "dut") == ("PARSED", [])
    assert new.count("inout VDD;") == 1 and new.count("inout VSS;") == 1
    header = new.split(");", 1)[0]
    assert header.count("VDD") == 1 and header.count("VSS") == 1
    # nothing else in the module moved
    assert new.split(");", 1)[1].replace(" inout VDD;\n", "").replace(
        " inout VSS;\n", "") == text.split(");", 1)[1].replace(
        " inout VDD;\n", "").replace(" inout VSS;\n", "")


def test_a_clean_netlist_is_left_byte_for_byte():
    text = _netlist(1)
    new, rec = N.dedupe(text, "dut")
    assert rec["status"] == "CLEAN" and new == text


def test_a_conflicting_repeat_is_refused_never_merged():
    """Same name, different direction: which one is right is not this
    program's to guess. The text is left unchanged and the check reports it."""
    text = _netlist(3, vss_dir="input")
    new, rec = N.dedupe(text, "dut")
    assert rec["status"] == "CONFLICT" and "VSS" in rec["why"] and new == text
    assert N.problems(new, "dut")[1]


def test_an_ansi_header_is_unparsed_not_judged():
    text = "module dut (input a, output b, inout VDD);\nendmodule\n"
    assert N.problems(text, "dut")[0] == "UNPARSED"
    assert N.dedupe(text, "dut")[1]["status"] == "UNPARSED"


def test_a_header_port_without_a_declaration_is_a_finding():
    text = "module dut (a, b);\n input a;\nendmodule\n"
    assert N.problems(text, "dut") == (
        "PARSED", ["header port `b` has no direction declaration"])


# ── the flow: the pre-stream gate freezes only a legal netlist ────────────

def _stage(tmp_path: Path, text: str) -> Path:
    pnr = tmp_path / "phase3" / "stage3" / "pnr"
    pnr.mkdir(parents=True)
    (pnr / "dut_pnr.v").write_text(text)
    return pnr / "dut_pnr.v"


def test_the_prestream_gate_dedupes_exact_repeats_before_the_digest(tmp_path):
    """RED on main: the gate freezes the netlist as written. Drives the real
    gate; with no DEF it then stops at its own input check, which is fine --
    the netlist it would have digested is already the corrected one."""
    nl = _stage(tmp_path, _netlist(5))
    res = R.step_prestream_gate(tmp_path, "dut", None, "")
    assert "NETLIST_PORT_DECL_INVALID" not in str(res.detail), res.detail
    assert N.problems(nl.read_text(), "dut") == ("PARSED", [])
    rec = json.loads((tmp_path / "reports/phase3/netlist_port_decls.json").read_text())
    row = [r for r in rec["rows"] if r["stage"] == "prestream"][0]
    assert row["status"] == "DEDUPED" and row["findings"] == []
    assert row["sha256_before"] != row["sha256_after"]


def test_the_prestream_gate_refuses_a_conflicting_declaration_by_name(tmp_path):
    """RED on main: main digests and freezes it."""
    _stage(tmp_path, _netlist(3, vss_dir="input"))
    res = R.step_prestream_gate(tmp_path, "dut", None, "")
    assert res.status == "FAIL", res
    assert "NETLIST_PORT_DECL_INVALID" in str(res.detail)
    assert "VSS" in str(res.detail)


def test_the_pnr_step_dedupes_where_the_netlist_is_first_written():
    """The hook sits in step_pnr after the routed DEF is confirmed, so every
    consumer between PnR and the pre-stream gate reads a legal netlist."""
    src = (PROG / "phase3_one_shot_runner.py").read_text()
    tree = ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "step_pnr")
    calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call)
             and getattr(c.func, "id", None) == "_dedupe_shipped_netlist_ports"]
    assert calls and any(isinstance(a, ast.Constant) and a.value == "pnr"
                         for c in calls for a in c.args)


def test_the_helper_records_an_absent_netlist_without_raising(tmp_path):
    row = R._dedupe_shipped_netlist_ports(tmp_path, "dut", "pnr")
    assert row["status"] == "ABSENT"
