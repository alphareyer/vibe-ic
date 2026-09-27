#!/usr/bin/env python3
"""FX_D13: a reused IP's width/size parameters come from the DESIGN DOCUMENTS.

MEASURED on subservient x gf180mcuD (fxport runB2, v1.25.64): the documents
state ``memsize = 1024`` (L3 Design Parameters, L8 ``override: true``) and a
10-bit ``o_sram_addr``; the staged vendor top declares ``memsize = 512``,
``aw = $clog2(memsize)`` and was synthesised as staged (yosys: ``Parameter
\\memsize = 512``), so the netlist's ``o_sram_waddr`` is 9 bits. Nothing in
phase 2 carried the documents' value to the IP.

The rule pinned here: a parameter that sets a port width of the reused top is
DERIVED from the documents, checked against the IP's own math, and written
into the staged top's header default; two documents that disagree, or a
document the IP math contradicts, REFUSE with both values and sources; with
no stated value, only a value the documents ALLOW may be chosen, and an AI
backup choice is verified the same way.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
PROGRAMS = TESTS.parent
sys.path.insert(0, str(PROGRAMS))

import reused_ip_param_derive as D  # noqa: E402
from _staged_top_module import EXTRACTION_STRATEGY as STAGED  # noqa: E402

RUNNER = PROGRAMS / "design_one_shot_runner.py"

TOP_V = """`default_nettype none
module widget
  #(//Memory parameters
    parameter memsize  = 512,
    parameter aw       = $clog2(memsize),
    //Enable a feature
    parameter WITH_X = 0)
  (
   input wire           i_clk,

   //memory interface
   output wire [aw-1:0] o_mem_waddr,
   output wire [7:0]    o_mem_wdata,
   output wire [aw-1:0] o_mem_raddr,
   input wire [7:0]     i_mem_rdata,

   //External I/O
   output wire          o_gpio);
endmodule
"""


def _port(name, direction, width, staged, doc=True):
    p = {"name": name, "direction": direction, "width": width,
         "msb": width - 1, "lsb": 0, "declared_by_staged_top": staged}
    if staged and not doc:
        p["extraction_strategy"] = STAGED          # the harvest alone
    elif staged:
        p["extraction_strategy"] = "doc_table+" + STAGED
    else:
        p["extraction_strategy"] = "doc_table"
    return p


def _l9(addr_width=10, parameters=None):
    return {"top_module": "widget",
            "top_ports": [_port("i_clk", "input", 1, True),
                          _port("o_mem_addr", "output", addr_width, False),
                          _port("o_mem_waddr", "output", 9, True, doc=False),
                          _port("o_mem_raddr", "output", 9, True, doc=False),
                          _port("o_gpio", "output", 1, True)],
            "parameters": parameters or []}


def _param(name, value, *, override=False, allowed=None, source="L3.md"):
    e = {"name": name, "source": source, "extraction_strategy": "doc_table"}
    if override:
        e.update(value=str(value), default=None, override=True)
    else:
        e.update(default=None if value is None else str(value))
    if allowed:
        e["type"] = " / ".join(str(v) for v in allowed) + " (bytes)"
    return e


PAIRS = [{"l9": ["o_mem_addr"], "rtl": ["o_mem_waddr", "o_mem_raddr"]}]


def _project(tmp_path, *, l8=None, l9=None, pairs=PAIRS, reused=True,
             top_v=TOP_V):
    p = tmp_path / "proj"
    docs = p / "phase1/generated_docs"
    docs.mkdir(parents=True)
    (docs / "L8_RTL_CONSTANTS.json").write_text(json.dumps(
        {"parameters": l8 if l8 is not None
         else [_param("memsize", 1024, override=True)]}))
    (docs / "L9_INTEGRATION_SPEC.json").write_text(json.dumps(
        l9 if l9 is not None else _l9()))
    rtl = p / "phase2/stage1/rtl"
    rtl.mkdir(parents=True)
    (rtl / "widget.v").write_text(top_v)
    (rtl / "SOURCE_MANIFEST.json").write_text(json.dumps(
        {"reused_ip": reused, "renamed_interfaces": pairs}))
    return p


def _header_default(p, name):
    return dict(D.header_parameters(
        (p / "phase2/stage1/rtl/widget.v").read_text(), "widget"))[name]


# --------------------------------------------------------------------------- #
# the measured shape
# --------------------------------------------------------------------------- #
def test_the_documents_memsize_reaches_the_staged_top(tmp_path):
    p = _project(tmp_path)
    assert D.main([str(p), "--apply"]) == 0
    rec = json.loads((p / D.REPORT_REL).read_text())
    assert rec["verdict"] == "PASS"
    assert rec["overrides"] == {"memsize": 1024}
    assert rec["resolved"]["aw"] == 10
    assert {w["port"] for w in rec["width_evidence"]} == {
        "o_mem_waddr", "o_mem_raddr"}          # the `//` line lost no port
    assert _header_default(p, "memsize") == "1024"
    assert _header_default(p, "aw") == "$clog2(memsize)"   # derived: follows
    side = json.loads(D.sidecar_path(p, "widget").read_text())
    assert side["applied"] == {"memsize": "1024"}
    assert side["original"] == {"memsize": "512"}
    # idempotent: a second run finds nothing left to override
    before = (p / "phase2/stage1/rtl/widget.v").read_text()
    assert D.main([str(p), "--apply"]) == 0
    assert (p / "phase2/stage1/rtl/widget.v").read_text() == before
    assert json.loads((p / D.REPORT_REL).read_text())["overrides"] == {}


def test_a_feature_switch_a_document_states_is_not_applied(tmp_path):
    """Only a parameter that sets a port width is in scope."""
    p = _project(tmp_path, l8=[_param("memsize", 1024, override=True),
                               _param("WITH_X", 1, override=True)])
    assert D.main([str(p), "--apply"]) == 0
    rec = json.loads((p / D.REPORT_REL).read_text())
    assert rec["overrides"] == {"memsize": 1024}
    assert rec["parameters"]["WITH_X"]["decided_by"] == "out_of_scope"
    assert _header_default(p, "WITH_X") == "0"


def test_the_harvested_rtl_width_is_not_document_evidence(tmp_path):
    """A staged-only L9 entry carries the IP DEFAULT's width (9); it must not
    contradict the documents' 1024."""
    p = _project(tmp_path, pairs=[])
    rec = D.derive(p)
    assert rec["verdict"] == "PASS" and rec["overrides"] == {"memsize": 1024}
    assert rec["width_evidence"] == []


# --------------------------------------------------------------------------- #
# contradictions refuse, naming both values and their sources
# --------------------------------------------------------------------------- #
def test_two_documents_that_disagree_refuse(tmp_path):
    l9 = _l9(parameters=[_param("memsize", 512, source="L9.md")])
    p = _project(tmp_path, l9=l9)
    assert D.main([str(p), "--apply"]) == 1
    rec = json.loads((p / D.REPORT_REL).read_text())
    assert rec["verdict"] == "REFUSE"
    f = rec["findings"][0]
    assert f["rule"] == "DOC_PARAMETER_CONTRADICTION"
    assert "1024" in f["message"] and "512" in f["message"]
    assert "L8_RTL_CONSTANTS" in f["message"] and "L9_INTEGRATION_SPEC" in f["message"]
    assert _header_default(p, "memsize") == "512"          # nothing applied


def test_a_document_width_the_ip_math_contradicts_refuses(tmp_path):
    p = _project(tmp_path, l9=_l9(addr_width=9))           # 1024 -> aw 10
    rec = D.derive(p)
    assert rec["verdict"] == "REFUSE"
    msgs = [f["message"] for f in rec["findings"]
            if f["rule"] == "DOC_IP_PARAMETER_CONTRADICTION"]
    assert msgs and "10 bit(s)" in msgs[0] and "o_mem_addr is 9" in msgs[0]


def test_a_stated_derived_parameter_must_equal_the_ip_math(tmp_path):
    p = _project(tmp_path, l8=[_param("memsize", 1024, override=True),
                               _param("aw", 11, override=True)])
    rec = D.derive(p)
    assert rec["verdict"] == "REFUSE"
    assert any(f["rule"] == "DOC_IP_PARAMETER_CONTRADICTION"
               and f["parameter"] == "aw" for f in rec["findings"])


# --------------------------------------------------------------------------- #
# no stated value: only what the documents allow, and a verified AI choice
# --------------------------------------------------------------------------- #
def test_the_width_picks_the_one_allowed_value(tmp_path):
    p = _project(tmp_path, l8=[_param("memsize", None,
                                      allowed=[256, 512, 1024, 2048])])
    rec = D.derive(p)
    assert rec["verdict"] == "PASS", rec
    assert rec["parameters"]["memsize"]["decided_by"] == "document_widths"
    assert rec["overrides"] == {"memsize": 1024}


def test_several_allowed_values_leave_it_to_a_verified_ai_choice(tmp_path):
    p = _project(tmp_path, l8=[_param("memsize", None,
                                      allowed=[600, 1000, 1024, 4096])])
    rec = D.derive(p)
    assert rec["verdict"] == "UNRESOLVED"
    assert rec["undecided"] == {"memsize": [600, 1000, 1024]}
    ok = D.derive(p, {"memsize": 1000})
    assert ok["verdict"] == "PASS"
    assert ok["parameters"]["memsize"]["decided_by"] == "ai_choice_verified"
    assert D.derive(p, {"memsize": 4096})["findings"][0]["rule"] == \
        "CHOICE_CONTRADICTS_DOCUMENT_WIDTHS"
    assert D.derive(p, {"memsize": 999})["findings"][0]["rule"] == \
        "CHOICE_NOT_ALLOWED_BY_DOCUMENTS"


def test_nothing_stated_keeps_the_ip_default(tmp_path):
    p = _project(tmp_path, l8=[], pairs=[])
    rec = D.derive(p)
    assert rec["verdict"] == "PASS" and rec["overrides"] == {}
    assert rec["parameters"]["memsize"]["decided_by"] == "ip_default"


def test_an_unreadable_top_is_not_measured(tmp_path):
    p = _project(tmp_path, top_v="module other; endmodule\n")
    assert D.main([str(p)]) == 2


# --------------------------------------------------------------------------- #
# the phase-2 runner applies it after staging, before anything elaborates
# --------------------------------------------------------------------------- #
def _main_plan_appends(src):
    main = next(f for f in ast.parse(src).body
                if isinstance(f, ast.FunctionDef) and f.name == "main")
    calls = [n for n in ast.walk(main)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "append" and isinstance(n.func.value, ast.Name)
             and n.func.value.id == "plan" and n.args
             and isinstance(n.args[0], ast.Call)]
    return [ast.unparse(c.args[0].func)
            for c in sorted(calls, key=lambda c: (c.lineno, c.col_offset))]


def test_the_runner_applies_it_after_staging_before_step1s_gate():
    order = _main_plan_appends(RUNNER.read_text())
    assert order.count("step_reused_ip_parameters") == 1, order
    at = order.index("step_reused_ip_parameters")
    for before in ("step_reused_ip_consume", "step_leaf_typo_aliases",
                   "step_reset_clock_variant_aliases"):
        assert order.index(before) < at, before
    assert at < order.index("step_catalog_synth_safe_params")


@pytest.mark.parametrize("kw,status", [
    ({}, "PASS"),
    ({"reused": False}, "NOT_APPLICABLE"),
    ({"l9": _l9(addr_width=9)}, "FAIL"),
])
def test_the_runner_step(tmp_path, kw, status):
    import design_one_shot_runner as R
    p = _project(tmp_path, **kw)
    sr = R.step_reused_ip_parameters(p)
    assert sr.status == status, sr.detail
    expect = "1024" if status == "PASS" else "512"
    assert _header_default(p, "memsize") == expect


def test_the_runner_step_hands_an_undecided_value_to_the_ai_backup(tmp_path):
    import design_one_shot_runner as R
    p = _project(tmp_path, l8=[_param("memsize", None,
                                      allowed=[600, 1000, 1024])])
    sr = R.step_reused_ip_parameters(p)
    assert sr.status == "FAIL"
    assert sr.extras["fallback_skill"] == "catalog-glue-author"
    assert "--choose NAME=VALUE" in sr.extras["command"]
    assert _header_default(p, "memsize") == "512"


# --------------------------------------------------------------------------- #
# 15.5ic: a pad per bit of the IMPLEMENTED port, not of the harvest's width
# --------------------------------------------------------------------------- #
PAD_DOC = """---
layer: L3
---

# L3 — External Interface

The I/O cell library is delegated to the PDK i/o pad defaults.

## Physical Pad Placement

| Pad side | signals |
|---|---|
| **North (N)** | memory waddr bus |
| **South (S)** | `rst` |
| **East (E)** | `clk` |
| **West (W)** | status pin(s) |
"""

PAD_NETLIST = """module core(clk, rst, o_memory_waddr, o_status);
  input clk;
  input rst;
  output [9:0] o_memory_waddr;
  output o_status;
endmodule
"""


def _pad_project(tmp_path, waddr_entry):
    import test_io_pad_chip_top_gen as IO
    spec = {"top_module": "core", "top_ports": [
        _port("clk", "input", 1, True), _port("rst", "input", 1, True),
        _port("o_status", "output", 1, True), waddr_entry]}
    for p in spec["top_ports"]:
        if p["width"] == 1:
            p.pop("msb"), p.pop("lsb")
    proj = IO._project(tmp_path, doc=PAD_DOC, spec=spec)
    synth = proj / "phase2/stage2/synth"
    synth.mkdir(parents=True)
    (synth / "core_synth.v").write_text(PAD_NETLIST)
    res = IO._run(PROGRAMS / "io_pad_chip_top_gen.py", proj,
                  IO._pdk(tmp_path / "pdk"))
    return IO, proj, res, res.stdout + res.stderr


def test_the_pads_follow_the_netlist_width_the_parameter_set(tmp_path):
    """L9's harvested `o_memory_waddr` is 9 bits (the IP default); the core
    built with the documents' memsize is 10. Every core bit gets a pad."""
    IO, proj, res, out = _pad_project(
        tmp_path, _port("o_memory_waddr", "output", 9, True, doc=False))
    assert res.returncode == 0, out
    rec = IO._record(proj)
    north = rec["derived_answers"]["pad_order_by_side"]["north"]
    assert len(north) == 10 and "u_pad_o_memory_waddr_9" in north
    assert rec["port_widths_from_netlist"] == [{
        "name": "o_memory_waddr", "l9_width": 9, "netlist_width": 10,
        "netlist": str(proj / "phase2/stage2/synth/core_synth.v"),
        "reason": "the staged-top harvest evaluated this width at the IP's "
                  "default parameters"}]


def test_a_document_width_the_netlist_contradicts_is_refused(tmp_path):
    IO, proj, res, out = _pad_project(
        tmp_path, _port("o_memory_waddr", "output", 9, False))
    assert res.returncode == 1, out
    assert "PORT_WIDTH_CONTRADICTS_DOCUMENT" in out
    assert "declare 9 bit(s)" in out and "declares 10" in out
    assert not (proj / "phase3/stage3/pnr/chip_top_io.v").exists()


# --------------------------------------------------------------------------- #
# the _NOT_PROSE claims (prose_polarity_consulted_check) are falsifiable
# --------------------------------------------------------------------------- #
def test_not_prose_an_unreadable_expression_invents_no_width(tmp_path):
    """`_NOT_PROSE["reused_ip_param_derive::derive"]`."""
    top = TOP_V.replace("$clog2(memsize)", "vendor_log(memsize)")
    rec = D.derive(_project(tmp_path, top_v=top))
    assert rec["resolved"]["aw"] is None
    assert not any(f["rule"] == "DOC_IP_PARAMETER_CONTRADICTION"
                   for f in rec["findings"])


def test_not_prose_apply_never_inserts_a_parameter(tmp_path):
    """`_NOT_PROSE["reused_ip_param_derive::apply_overrides"]`."""
    p = _project(tmp_path)
    before = (p / "phase2/stage1/rtl/widget.v").read_text()
    rec = dict(D.derive(p), overrides={"DEPTH": 64})
    with pytest.raises(ValueError, match="no default for parameter DEPTH"):
        D.apply_overrides(p, rec)
    assert (p / "phase2/stage1/rtl/widget.v").read_text() == before


def test_a_non_literal_netlist_range_changes_no_width(tmp_path, monkeypatch):
    """The reconciliation reads the netlist through D2's one reader,
    `_implemented_core_ports`, whose non-literal range is width None: no
    width is invented from it."""
    import io_pad_chip_top_gen as G
    import phase3_one_shot_runner as R
    p = tmp_path / "p"
    (p / "phase1/generated_docs").mkdir(parents=True)
    (p / "phase1/generated_docs/L9_INTEGRATION_SPEC.json").write_text(
        json.dumps({"top_module": "core"}))
    nl = p / "core_synth.v"
    nl.write_text("module core(o_w);\n  output [W-1:0] o_w;\nendmodule\n")
    monkeypatch.setattr(R, "pnr_input_netlist", lambda proj, c: (nl, "t", False))
    port = _port("o_w", "output", 9, True, doc=False)
    out, notes = G._reconcile_port_widths(p, [port])
    assert out == [port] and notes == []
