"""U13 — Steps 28 and 30 read PASS while measuring nothing.

Step 28. `perc_signoff_check` exited 0 with PASS_WITH_OPEN_ITEMS when the
PERC aggregate's only non-PASS categories were MANUAL_REVIEW / INCOMPLETE,
so step 28 read PASS on a die whose ESD presence and latch-up had never been
measured (spm tail, cx_spmic2_run: "MANUAL_REVIEW: ESD protection presence",
"MANUAL_REVIEW: Latch-up / well-tap"). An open item is an unmeasured
category: the gate now says NOT_MEASURED (rc 2, an `INCOMPLETE:` last line)
and the step reads NOT_MEASURED. A conclusive FAIL and an all-PASS aggregate
are unchanged (controls below).

Step 30. The correlation refused with "critical path not stitchable" on every
path that starts at a bond pad. `parse_sta_path` took the FIRST component of
a hierarchical pin as its instance (`u_core/fanout48/Z` -> `u_core`), and a
die top puts every core cell one level below the pad ring, so no core stage
resolved. Measured on the spm tail's own post-route report + routed netlist:
before, `resolve_path_stages` -> None; after, 12 of 19 combinational stages.
The first modelled stage is now chained through the pad cell's output net.

chip-AGNOSTIC fixtures; the flow step is the shipped yaml's step 28.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import flow_compliance_check as FCC  # noqa: E402
import perc_signoff_check as PERC  # noqa: E402
import spice_correlation_check as S  # noqa: E402

_FLOW = _PROGRAMS.parent / "flow" / "phase1_phase2_phase3.yaml"


def _step(sid: str) -> dict:
    doc = yaml.safe_load(_FLOW.read_text())
    return next(s for s in doc["steps"] if str(s.get("id")) == sid)


def _perc_project(root: Path, categories: list, verdict: str) -> Path:
    d = root / "reports" / "phase3"
    d.mkdir(parents=True)
    (d / "perc_equivalent.json").write_text(json.dumps(
        {"verdict": verdict, "categories": categories}))
    (d / "perc_equivalent.rpt").write_text(
        f"PERC equivalent\nOVERALL VERDICT: {verdict}\n"
        + "".join(f"{c['category']}\n" for c in categories))
    (d / "PERC_SIGNOFF_MEMO.md").write_text(
        f"# PERC memo\n\n**Overall verdict:** `{verdict}`\n\n"
        + "".join(f"- {c['category']}\n" for c in categories))
    # The runner invokes the gate as the producer of its declared record,
    # before the audit (flow_declared_producer_run); do the same.
    PERC.main([str(root), "--json",
               str(root / "reports/phase2/gates/perc_signoff.json")])
    return root


_OPEN = [
    {"category": "ESD protection presence", "status": "MANUAL_REVIEW"},
    {"category": "Latch-up / well-tap", "status": "MANUAL_REVIEW"},
    {"category": "Antenna", "status": "AUTOMATED", "result": "PASS"},
]


def test_step28_open_items_read_not_measured_not_pass(tmp_path):
    proj = _perc_project(tmp_path, _OPEN, "PERC_EQUIV_PASS")
    rep = json.loads((proj / "reports/phase2/gates/perc_signoff.json").read_text())
    assert rep["verdict"] == "NOT_MEASURED"
    assert rep["open_items"] == ["MANUAL_REVIEW: ESD protection presence",
                                 "MANUAL_REVIEW: Latch-up / well-tap"]
    row = FCC.check_step(proj, _step("28"), {})
    assert row.status == "NOT_MEASURED", (row.status, row.reasons)
    assert row.status != "PASS"


def test_step28_control_all_measured_still_passes(tmp_path):
    proj = _perc_project(tmp_path, [_OPEN[2]], "PERC_EQUIV_PASS")
    row = FCC.check_step(proj, _step("28"), {})
    assert row.status == "PASS", (row.status, row.reasons)


def test_step28_control_conclusive_fail_still_fails(tmp_path):
    cats = _OPEN + [{"category": "ESD discharge topology",
                     "status": "AUTOMATED", "result": "FAIL"}]
    proj = _perc_project(tmp_path, cats, "PERC_EQUIV_FAIL")
    row = FCC.check_step(proj, _step("28"), {})
    assert row.status == "FAIL", (row.status, row.reasons)


# ── Step 30: a path that starts at an input pad cell ───────────────────────
_REPORT = """\
Startpoint: rst (input port clocked by clk)
Endpoint: u_core/r0 (rising edge-triggered flip-flop clocked by clk)
Path Group: clk
Path Type: max

  Delay    Time   Description
---------------------------------------------------------
   0.00    0.00   clock clk (rise edge)
   4.80    4.80 ^ input external delay
   0.00    4.80 ^ rst (in)
   1.63    6.43 ^ u_pad_rst/Y (padlib__in)
   0.51    6.94 v u_core/g1/ZN (stdlib__nand2)
   0.40    7.34 ^ u_core/g2/ZN (stdlib__inv)
   0.00    7.34 ^ u_core/r0/D (stdlib__dff)
           7.34   data arrival time
"""

_NETLIST = """\
module top(rst, clk);
  padlib__in u_pad_rst (.PAD(rst), .Y(rst_core));
  stdlib__nand2 \\u_core/g1  (.A1(en), .A2(rst_core), .ZN(\\u_core/n1 ));
  stdlib__inv \\u_core/g2  (.I(\\u_core/n1 ), .ZN(\\u_core/n2 ));
  stdlib__dff \\u_core/r0  (.D(\\u_core/n2 ), .CLK(clk), .Q(q));
endmodule
"""

_STD = {"stdlib__nand2", "stdlib__inv", "stdlib__dff"}


def test_hierarchical_pin_names_its_own_instance():
    path = S.parse_sta_path(_REPORT)
    insts = [r["inst"] for r in path["rows"]]
    assert "u_core/g1" in insts and "u_core/g2" in insts, insts
    assert "rst" in insts                         # a port row keeps its name


def test_a_path_starting_at_a_pad_cell_is_stitchable(tmp_path):
    path = S.parse_sta_path(_REPORT)
    inst_map = S.parse_verilog_instances(_NETLIST)
    assert "u_core/g1" in inst_map
    got = S.resolve_path_stages(path, inst_map, {}, _STD, "", 12)
    assert got is not None, "critical path not stitchable"
    assert [s["inst"] for s in got["stages"]] == ["u_core/g1", "u_core/g2"]
    # Chained through the pad cell's output net: A2 carries rst_core. The
    # representative-pin fallback would have taken A1 (a different input).
    assert got["stages"][0]["toggle_pin"] == "A2"
    assert got["stages"][1]["toggle_pin"] == "I"


# ── Round 2 (review wave 58): R-0929-SPICE-COVERAGE ────────────────────────
def _chain_report(n: int) -> str:
    rows = "".join(f"   0.10    {6.5 + 0.1 * i:.2f} ^ u_core/b{i}/Z (stdlib__buf)\n"
                   for i in range(n))
    return ("Startpoint: rst (input port clocked by clk)\n"
            "Endpoint: u_core/r0 (rising edge-triggered flip-flop clocked by clk)\n"
            "Path Type: max\n\n  Delay    Time   Description\n"
            "   0.00    4.80 ^ rst (in)\n"
            "   1.63    6.43 ^ u_pad_rst/Y (padlib__in)\n" + rows +
            "   0.00    9.00 ^ u_core/r0/D (stdlib__dff)\n")


def _chain_netlist(n: int) -> str:
    lines = ["module top(rst, clk);",
             "  padlib__in u_pad_rst (.PAD(rst), .Y(n_m1));"]
    for i in range(n):
        src = "n_m1" if i == 0 else f"\\u_core/n{i - 1} "
        lines.append(f"  stdlib__buf \\u_core/b{i}  (.I({src}), .Z(\\u_core/n{i} ));")
    lines.append(f"  stdlib__dff \\u_core/r0  (.D(\\u_core/n{n - 1} ), .CLK(clk), .Q(q));")
    return "\n".join(lines + ["endmodule", ""])


def test_the_resolver_no_longer_truncates_a_long_path():
    """spm's pad-rooted critical path: an input pad then 20 core stages. The
    old fixed max_stages=12 kept 12 and dropped the path's only logic cells."""
    path = S.parse_sta_path(_chain_report(20))
    inst_map = S.parse_verilog_instances(_chain_netlist(20))
    std = {"stdlib__buf", "stdlib__dff"}
    got = S.resolve_path_stages(path, inst_map, {}, std, "")
    assert got["covered"] == 20
    capped = S.resolve_path_stages(path, inst_map, {}, std, "", 12)
    assert capped["covered"] == 12
    assert any(d.startswith("u_core/b12 (stdlib__buf)") for d in capped["dropped"])
    assert sum("max_stages" in d for d in capped["dropped"]) == 8


def test_the_pad_counts_as_a_stage_and_an_unmodelled_one_is_dropped():
    """TAIL_A final F1: the input pad has no model in the std-cell SPICE set;
    it must count (21 stages) and be named, never vanish from both counts."""
    path = S.parse_sta_path(_chain_report(20))
    inst_map = S.parse_verilog_instances(_chain_netlist(20))
    got = S.resolve_path_stages(path, inst_map, {},
                                {"stdlib__buf", "stdlib__dff"}, "")
    assert (got["covered"], got["total_comb"]) == (20, 21)
    assert got["dropped"] == [
        "u_pad_rst (padlib__in): no SPICE model in the cell SPICE set"]
    # chained through the unmodelled pad: stage 0 is driven by its Y net
    assert got["stages"][0]["toggle_pin"] == "I"
    # and the pad simulated too (its model loaded): 21 of 21, nothing dropped
    full = S.resolve_path_stages(path, inst_map, {},
                                 {"stdlib__buf", "stdlib__dff", "padlib__in"}, "")
    assert (full["covered"], full["total_comb"], full["dropped"]) == (21, 21, [])


def test_an_unmodelled_core_cell_is_counted_and_the_chain_continues():
    report = _chain_report(20).replace("u_core/b7/Z (stdlib__buf)",
                                       "u_core/b7/Z (stdlib__nor2)")
    netlist = _chain_netlist(20).replace("stdlib__buf \\u_core/b7 ",
                                         "stdlib__nor2 \\u_core/b7 ")
    got = S.resolve_path_stages(S.parse_sta_path(report),
                                S.parse_verilog_instances(netlist), {},
                                {"stdlib__buf", "stdlib__dff", "padlib__in"}, "")
    assert (got["covered"], got["total_comb"]) == (20, 21)
    assert got["dropped"][0].startswith("u_core/b7 (stdlib__nor2)")
    b8 = next(st for st in got["stages"] if st["inst"] == "u_core/b8")
    assert b8["toggle_pin"] == "I"          # found by net through b7


def test_the_reviewer_reproduction_pad_plus_20_does_not_pass(tmp_path):
    """The resolver's own counts, carried into a CORRELATED record, must read
    NOT_MEASURED (20 of 21), never 20/20 PASS."""
    got = S.resolve_path_stages(S.parse_sta_path(_chain_report(20)),
                                S.parse_verilog_instances(_chain_netlist(20)),
                                {}, {"stdlib__buf", "stdlib__dff"}, "")
    proj = _correlated_project(tmp_path, got["covered"], got["total_comb"],
                               got["dropped"])
    rc = S.main([str(proj), "--no-spice", "--json", str(tmp_path / "o.json")])
    rep = json.loads((tmp_path / "o.json").read_text())
    assert rc == 2, rep["summary"]
    assert "20 of 21" in rep["summary"]["reason"]
    assert "u_pad_rst (padlib__in)" in rep["summary"]["reason"]


def _correlated_project(root: Path, k: int, n: int, dropped=None) -> Path:
    (root / "phase3/stage3/extracted").mkdir(parents=True)
    (root / "phase3/stage3/extracted/top.spef").write_text("*SPEF\n")
    (root / "phase3/stage3/sta").mkdir(parents=True)
    (root / "phase3/stage3/sta/post_route_timing.rpt").write_text("slack (MET) 1.0\n")
    rec = {"verdict": "CORRELATED", "spice_path_delay_ns": 3.0,
           "liberty_spef_cone_delay_ns": 3.05, "pct_error": 1.6,
           "tolerance_pct": 5.0, "stages_correlated": k,
           "stages_total_combinational": n}
    if dropped is not None:
        rec["stages_dropped"] = dropped
    out = root / "reports/phase3/spice_correlation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"program": "spice_correlation_check",
                               "correlation": rec}))
    return root


def test_the_spm_12_of_20_shape_does_not_pass(tmp_path):
    dropped = [f"u_core/_{i}_ (cell)" for i in range(8)]
    proj = _correlated_project(tmp_path, 12, 20, dropped)
    rc = S.main([str(proj), "--no-spice", "--json", str(tmp_path / "o.json")])
    rep = json.loads((tmp_path / "o.json").read_text())
    assert rc == 2, rep["summary"]
    assert rep["summary"]["verdict"] == "NOT_MEASURED"
    assert "12 of 20" in rep["summary"]["reason"]
    assert "u_core/_0_ (cell)" in rep["summary"]["reason"]


def test_control_a_fully_covered_correlation_passes(tmp_path):
    proj = _correlated_project(tmp_path, 20, 20)
    assert S.main([str(proj), "--no-spice", "--json", str(tmp_path / "o.json")]) == 0
