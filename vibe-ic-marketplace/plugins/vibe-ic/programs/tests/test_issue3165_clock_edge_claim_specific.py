#!/usr/bin/env python3
"""#3165 — clock edge owns a complete-state applicability route.

These deliberately use reset in data and enable plus dotted, post-flatten
state names.  They are not reset/value tests: the positive edge must close,
the opposite edge must refute, and a derived clock must remain refused.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import formal_structural_check as fsc  # noqa: E402
import formal_harness_gen as fhg  # noqa: E402
import formal_property_run as fpr  # noqa: E402
from not_verified_tier import skip_not_verified  # noqa: E402


HIER = '''module neutral_leaf(input wire clk, rst, clear, enable, data, output wire value);
reg forced;
reg held;
always @(posedge clk) begin
  if (enable) forced <= data & ~(rst & clear);
  if (enable & ~rst) held <= data;
end
assign value = forced ^ held;
endmodule
module neutral_clock(input wire clk, rst, clear, enable, data, output wire out);
neutral_leaf leaf(.clk(clk), .rst(rst), .clear(clear), .enable(enable), .data(data), .value(out));
endmodule
'''

DERIVED = HIER.replace(".clk(clk)", ".clk(clk_g)").replace(
    "neutral_leaf leaf", "wire clk_g = clk & enable;\nneutral_leaf leaf")
HARNESS_HIER = HIER.replace(
    "output wire out);\nneutral_leaf leaf(.clk(clk), .rst(rst), .clear(clear), .enable(enable), .data(data), .value(out));",
    "output reg out);\nwire leaf_value;\nneutral_leaf leaf(.clk(clk), .rst(rst), .clear(clear), .enable(enable), .data(data), .value(leaf_value));\nalways @(posedge clk) if (rst) out <= 1'b0; else out <= leaf_value;")
CLAIM = {"rule": "clock_edge", "signal": "clk", "value": "posedge"}
KLASS = {"clock": "clk", "resets": ["rst"]}


def _check(tmp_path: Path, rtl: str) -> dict:
    if shutil.which("yosys") is None:
        skip_not_verified(
            "yosys not on PATH, so #3165's native structural control cannot run",
            "tools/ci/run_suite_in_eda_image.sh -- programs/tests/test_issue3165_clock_edge_claim_specific.py")
    source = tmp_path / "neutral_clock.v"
    source.write_text(rtl)
    return fsc.check([source], "neutral_clock", [CLAIM], tmp_path / "native",
                     container=None, simdef="", klass=KLASS)


def test_hierarchical_reset_qualified_state_passes_only_clock_edge_route(tmp_path):
    result = _check(tmp_path, HIER)
    assert result["verdict"] == fsc.PASS
    assert result["claims"][0]["verdict"] == fsc.PASS
    assert result["claims"][0]["flops_clocked"] == 2
    assert result["applicability"]["inside"] is False
    assert any(row.startswith("C3 ") for row in result["applicability"]["failed_preconditions"])
    assert any(row.startswith("C6 ") for row in result["applicability"]["failed_preconditions"])
    assert result["clock_edge_applicability"] == {
        "class": "complete-state direct-clock observation", "inside": True,
        "failed_preconditions": []}
    raw = fsc.sync_state_facts(
        json.loads((tmp_path / "native" / "formal_structural_netlist_raw.json").read_text()),
        "neutral_clock", "clk")
    opt = fsc.sync_state_facts(
        json.loads((tmp_path / "native" / "formal_structural_netlist.json").read_text()),
        "neutral_clock", "clk")
    assert raw["ok"] and opt["ok"]
    assert sorted((r["name"], r["width"]) for r in raw["registers"]) == [
        ("leaf.forced", 1), ("leaf.held", 1)]
    assert sorted((r["name"], r["width"]) for r in opt["registers"]) == [
        ("leaf.forced", 1), ("leaf.held", 1)]
    assert raw["clock_edge"] == opt["clock_edge"] == "posedge"


def test_hierarchical_opposite_edge_is_refuted(tmp_path):
    result = _check(tmp_path, HIER.replace("posedge", "negedge"))
    assert result["verdict"] == fsc.REFUTED
    assert result["claims"][0]["verdict"] == fsc.REFUTED
    assert result["clock_edge_applicability"]["inside"] is True


def test_derived_clock_stays_named_not_discharged(tmp_path):
    result = _check(tmp_path, DERIVED)
    assert result["verdict"] == fsc.NOT_DISCHARGED
    assert result["claims"][0]["verdict"] == fsc.NOT_DISCHARGED
    assert result["clock_edge_applicability"]["inside"] is False
    assert "optimized-state census unavailable" in result["claims"][0]["finding"]


def test_harness_producer_and_structural_consumer_close_the_claim(tmp_path):
    if shutil.which("sby") is None:
        skip_not_verified(
            "sby not on PATH, so #3165's normal producer-to-consumer route cannot run",
            "tools/ci/run_suite_in_eda_image.sh -- programs/tests/test_issue3165_clock_edge_claim_specific.py")
    docs = tmp_path / "phase1/generated_docs"
    docs.mkdir(parents=True)
    (docs / "L3_PROTOCOL.json").write_text(json.dumps({"no_opcodes_in_input": True}))
    (docs / "L6_FSM.json").write_text(json.dumps({"no_fsm_in_input": True}))
    (docs / "L8_TIMING_WAVEFORM.json").write_text(json.dumps({
        "clock_and_reset_waveform": {"clocks": [{"name": "clk", "edge": "posedge"}],
                                     "resets": [{"name": "rst"}]}}))
    rtl_dir = tmp_path / "phase2/stage1/rtl"
    rtl_dir.mkdir(parents=True)
    rtl = rtl_dir / "neutral_clock.v"
    rtl.write_text(HARNESS_HIER)
    generated = fhg.generate(project=tmp_path, top="neutral_clock")
    assert generated["verdict"] == "EMITTED", generated
    contract = json.loads((tmp_path / "phase2/stage1/formal/property_contract.json").read_text())
    clock_obligation = next(o for o in contract["unresolved_obligations"]
                            if o["id"] == "L8.clock_and_reset_waveform.clocks.0.edge")
    assert "program_refused" not in clock_obligation
    result = fpr.run(tmp_path, harness=Path(generated["harness_path"]),
                     rtl=[Path(p) for p in generated["rtl_files"]],
                     top=generated["harness_module"], container=None, timeout=300)
    assert "L8.clock_and_reset_waveform.clocks.0.edge" in result[
        "program_discharged_obligations"]
