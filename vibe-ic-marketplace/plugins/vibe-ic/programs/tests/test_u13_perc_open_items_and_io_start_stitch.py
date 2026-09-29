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
