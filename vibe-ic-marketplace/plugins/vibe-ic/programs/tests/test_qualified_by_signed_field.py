#!/usr/bin/env python3
"""R-0929-X-QUALIFIED-4 — the reset oracle reads a qualifier ONLY from the
D1-signed structured `qualified_by` field; it never parses a description.

Two oracle-side prose readers produced false PASSes (3, then 23 more in
review_wave58/XQROLES_review.json). The fact now lives on the L9 port row of a
data output as {port, active_level high|low, basis [{file, line, quote}]} and
is trusted only when (1) it is well formed, (2) every quotation is verbatim on
its line of a design-input file, (3) the D1 receipt is valid for the current
bytes AND the signed expectations carry exactly this fact, and (4) the strict
rules hold (multi-bit output, 1-bit qualifier, never clock or reset). Each
refusal is pinned here with its known positive; the iverilog arms show a
signed field exempts, an unsigned one does not, and a signed one never exempts
a cycle where the qualifier is asserted. chip-AGNOSTIC synthetic fixtures.
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _qualified_by as qb  # noqa: E402
import reset_invariant_oracle_tb_gen as riv  # noqa: E402
from _ai_judgement_fixture import sign  # noqa: E402
from _qualified_by_fixture import (INPUT_DOC, sign_field, stage_field,  # noqa: E402
                                   write_expectation)

_IN = [("clk", ""), ("rst_n", ""), ("d", "[7:0]")]
_OUT = [("o_data", "[7:0]"), ("o_we", ""), ("o_cyc", ""), ("o_flag", "")]
_EXCL = ("clk", "rst_n")


def _proj(tmp_path):
    p = tmp_path / "proj"
    (p / "phase1/generated_docs").mkdir(parents=True)
    (p / "phase1/generated_docs/L9_INTEGRATION_SPEC.json").write_text(
        json.dumps({"top_ports": [
            {"name": "o_data", "direction": "output", "width": 8,
             "description": "write data"},
            {"name": "o_we", "direction": "output", "width": 1,
             "description": "write enable"}]}))
    return p


def _trusted(p):
    return qb.trusted_qualifiers(p, _OUT, _IN, excluded=_EXCL)


def test_a_signed_field_is_trusted_and_cites_its_basis(tmp_path):
    p = _proj(tmp_path)
    sign_field(p, "o_data", "o_we", "high")
    found, ignored = _trusted(p)
    assert ignored == {}
    (row,) = found["o_data"]
    assert row["qualifier"] == "o_we" and row["active"] == "1"
    assert row["basis"] == "signed_field"
    assert f"{INPUT_DOC}:5" in row["evidence"] and "D1-signed" in row["evidence"]
    assert riv.declared_output_qualifiers(p, _OUT, _IN) == found


def test_active_low_is_the_declared_level(tmp_path):
    p = _proj(tmp_path)
    sign_field(p, "o_data", "o_we", "low")
    assert _trusted(p)[0]["o_data"][0]["active"] == "0"


def test_no_d1_receipt_means_ignored(tmp_path):
    p = _proj(tmp_path)
    stage_field(p, "o_data", "o_we", "high")
    write_expectation(p, "o_data", "o_we", "high")
    found, ignored = _trusted(p)
    assert found == {} and "not signed by the D1" in ignored["o_data"]


def test_a_stale_receipt_means_ignored(tmp_path):
    p = _proj(tmp_path)
    sign_field(p, "o_data", "o_we", "high")
    l9 = p / "phase1/generated_docs/L9_INTEGRATION_SPEC.json"
    doc = json.loads(l9.read_text())
    doc["top_ports"][0]["qualified_by"]["active_level"] = "low"   # edited after
    l9.write_text(json.dumps(doc))
    found, ignored = _trusted(p)
    assert found == {} and "not signed" in ignored["o_data"]


def test_a_receipt_without_the_matching_expectation_means_ignored(tmp_path):
    p = _proj(tmp_path)
    stage_field(p, "o_data", "o_we", "high")
    write_expectation(p, "o_data", "o_we", "low")      # the expert said low
    sign(p, "D1")
    found, ignored = _trusted(p)
    assert found == {} and "carry no 'qualified_by:o_data' fact" in \
        ignored["o_data"]
    q = _proj(tmp_path / "b")
    stage_field(q, "o_data", "o_we", "high")
    sign(q, "D1")                                        # no expectation at all
    assert _trusted(q)[0] == {}


@pytest.mark.parametrize("basis,why", [
    ([], "non-empty"),
    ([{"file": INPUT_DOC, "line": 5, "quote": "write enable"}], "is not on"),
    ([{"file": INPUT_DOC, "line": 99, "quote": "write data"}], "is not on"),
    ([{"file": "reports/golden.txt", "line": 1, "quote": "x"}],
     "not a design-input file"),
    ([{"file": INPUT_DOC, "line": "5", "quote": "write data"}], "integer line"),
])
def test_a_basis_must_quote_the_design_input_verbatim(tmp_path, basis, why):
    p = _proj(tmp_path)
    sign_field(p, "o_data", "o_we", "high", basis=basis)
    found, ignored = _trusted(p)
    assert found == {} and why in ignored["o_data"]


@pytest.mark.parametrize("output,port,level,width,why", [
    ("o_flag", "o_we", "high", 1, "not a multi-bit data output"),
    ("o_data", "d", "high", 8, "not a 1-bit port"),
    ("o_data", "rst_n", "high", 8, "not a 1-bit port"),
    ("o_data", "clk", "high", 8, "not a 1-bit port"),
    ("o_data", "o_data", "high", 8, "not a 1-bit port"),
    ("o_data", "o_we", "asserted", 8, "exactly 'high' or 'low'"),
])
def test_the_strict_rules_still_bind_a_signed_field(tmp_path, output, port,
                                                    level, width, why):
    p = _proj(tmp_path)
    sign_field(p, output, port, level, row_width=width)
    found, ignored = _trusted(p)
    assert output not in found and why in ignored[output]


def test_the_oracle_never_reads_a_description(tmp_path):
    """The XQROLES classes (low-active spellings, flags, cross-channel pairs,
    高態有效) are all prose: with no signed field, none of them exempts."""
    p = _proj(tmp_path)
    l9 = p / "phase1/generated_docs/L9_INTEGRATION_SPEC.json"
    l9.write_text(json.dumps({
        "top_ports": [
            {"name": "o_data", "direction": "output", "width": 8,
             "description": "write data, valid when o_we = 1"},
            {"name": "o_we", "direction": "output", "width": 1,
             "description": "write enable, low active"}],
        "integration": {"external_interface": [{
            "source": INPUT_DOC, "heading": "Write port",
            "evidence": ("| Sub-port | 描述 |\n|---|---|\n"
                         "| `o_data` | write data |\n"
                         "| `o_we` | write enable |\n")}]}}))
    assert riv.declared_output_qualifiers(p, _OUT, _IN) == {}


def test_the_proposal_quotes_one_table_and_decides_nothing(tmp_path):
    p = _proj(tmp_path)
    stage_field(p, "o_data", "o_we", "high")   # writes the design-input table
    l9 = p / "phase1/generated_docs/L9_INTEGRATION_SPEC.json"
    doc = json.loads(l9.read_text())
    for r in doc["top_ports"]:
        r.pop("qualified_by", None)
    l9.write_text(json.dumps(doc))
    before = l9.read_bytes()
    out = tmp_path / "pack"
    out.mkdir()
    path = qb.write_review_request(p, out)
    req = json.loads(path.read_text())
    assert l9.read_bytes() == before                     # writes no field
    (cand,) = req["candidates"]
    assert (cand["output"], cand["port"]) == ("o_data", "o_we")
    assert [b["line"] for b in cand["basis"]] == [5, 6]
    assert qb.basis_refusal(p, cand["basis"]) is None    # exact quotations
    assert req["checklist"] and req["signing"]["expectation"]["id"] == \
        "qualified_by:<output>"
    assert qb.trusted_qualifiers(p, _OUT, _IN)[0] == {}  # a proposal is inert


def test_the_d1_pack_carries_the_review_request():
    src = (PROGRAMS / "phase1_expert_parse_track.py").read_text()
    assert "write_review_request(project, out_dir)" in src


# ── iverilog: signed exempts, unsigned does not, asserted never ────────────
_CASE = {"name": "rst_glitch", "stimulus": "rst_n glitch 不應導致 bus race",
         "expected": "holds"}


def _simulate(p, tmp_path, we_value):
    path = os.pathsep.join(("/foss/tools/bin", os.environ.get("PATH", "")))
    for tool in ("iverilog", "vvp"):
        if shutil.which(tool, path=path) is None:
            pytest.fail(f"{tool}: command not found — this arm simulates the "
                        f"generated testbench and this host has no {tool}")
    import testbench_gen as tbg
    ports = ([("input", w, n) for n, w in _IN]
             + [("output", w, n) for n, w in _OUT])
    tb = tbg._emit_case_reset_invariant_oracle(p, _CASE, "dut", ports,
                                               tmp_path, {})
    assert tb is not None
    decl = ["input clk", "input rst_n", "input [7:0] d"] + [
        f"output reg {w} {n}".replace("  ", " ") for n, w in _OUT]
    (tmp_path / "dut.v").write_text(
        f"module dut({', '.join(decl)});\n  always @(posedge clk) if (!rst_n) "
        f"begin o_we <= {we_value}; o_cyc <= 0; o_flag <= 0; end\nendmodule\n")
    env = dict(os.environ, PATH=path)
    b = subprocess.run(["iverilog", "-g2012", "-s", _CASE["name"], "-o",
                        str(tmp_path / "s.vvp"), str(tmp_path / "dut.v"),
                        str(tb)], capture_output=True, text=True, env=env)
    assert b.returncode == 0, b.stderr
    r = subprocess.run(["vvp", "-n", str(tmp_path / "s.vvp")],
                       capture_output=True, text=True, env=env)
    fsf = importlib.import_module("full_stack_functional_tb")
    return fsf.score_transcript(_CASE["name"], r.returncode,
                                r.stdout + r.stderr)["state"], r.stdout


def test_a_signed_field_exempts_in_simulation(tmp_path):
    p = _proj(tmp_path)
    sign_field(p, "o_data", "o_we", "high")
    state, out = _simulate(p, tmp_path, 0)
    assert state == "passed", out
    assert out.count("X_EXEMPT") == 8 and "D1-signed" in out


def test_an_unsigned_field_does_not_exempt_in_simulation(tmp_path):
    p = _proj(tmp_path)
    stage_field(p, "o_data", "o_we", "high")
    write_expectation(p, "o_data", "o_we", "high")
    state, out = _simulate(p, tmp_path, 0)
    assert state == "failed" and "X_EXEMPT" not in out


def test_a_signed_field_never_exempts_an_asserted_qualifier(tmp_path):
    p = _proj(tmp_path)
    sign_field(p, "o_data", "o_we", "low")      # active low, driven 0 = asserted
    state, out = _simulate(p, tmp_path, 0)
    assert state == "failed" and "X_EXEMPT" not in out
    assert "while a declared qualifier is asserted or unknown" in out


def test_the_handoff_names_the_review_request(tmp_path):
    (tmp_path / "ic_expert_agent_handoff.json").write_text(
        json.dumps({"answer_contract": {}}))
    assert qb.point_handoff_at_review(tmp_path) is True
    doc = json.loads((tmp_path / "ic_expert_agent_handoff.json").read_text())
    assert doc["qualified_by_review"]["file"] == qb.REVIEW_REQUEST
    assert doc["answer_contract"] == {}                  # nothing else moved
    assert qb.point_handoff_at_review(tmp_path / "none") is False
