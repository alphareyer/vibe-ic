#!/usr/bin/env python3
"""formal_structural_check — the structural answers, through its OWN entry points.

Step 5 reaches this program through `formal_property_run`; these arms drive it
directly (`check()` and the CLI `main()`), both ways, on generic RTL:

  sync reset, posedge                 -> clock_edge / reset_port / reset_sync PASS
  async reset declared "synchronous"  -> reset_sync REFUTED
  negedge flop declared posedge       -> clock_edge REFUTED
  a reset port that reaches no flop   -> reset_port not PASS

Needs yosys on PATH (the vibeic-eda image, as CI and falsref run it); without
it the arms FAIL with the reason, never skip. chip-AGNOSTIC.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import formal_structural_check as fsc  # noqa: E402

SYNC = """module m(input clk, input rst, input [3:0] d, output reg [3:0] q);
  always @(posedge clk) if (rst) q <= 4'd0; else q <= d;
endmodule
"""
ASYNC = """module m(input clk, input rst, input [3:0] d, output reg [3:0] q);
  always @(posedge clk or posedge rst) if (rst) q <= 4'd0; else q <= d;
endmodule
"""
NEG = SYNC.replace("posedge clk", "negedge clk")
DEAD_RST = """module m(input clk, input rst, input [3:0] d, output reg [3:0] q);
  always @(posedge clk) q <= d;
endmodule
"""
CLAIMS = [{"rule": "clock_edge", "signal": "clk", "value": "posedge"},
          {"rule": "reset_port", "signal": "rst", "value": ""},
          {"rule": "reset_sync", "signal": "rst", "value": "synchronous"}]


def _yosys():
    assert shutil.which("yosys"), "yosys not on PATH — run inside the vibeic-eda image"


def _check(tmp: Path, rtl: str) -> dict:
    _yosys()
    f = tmp / "m.v"
    f.write_text(rtl)
    rec = fsc.check([f], "m", CLAIMS, tmp)
    return {c["rule"]: c["verdict"] for c in rec["claims"]} | {"_": rec["verdict"]}


def test_a_sync_posedge_design_passes_every_structural_claim(tmp_path):
    v = _check(tmp_path, SYNC)
    assert v == {"clock_edge": fsc.PASS, "reset_port": fsc.PASS,
                 "reset_sync": fsc.PASS, "_": fsc.PASS}, v


def test_an_async_reset_refutes_synchronous(tmp_path):
    v = _check(tmp_path, ASYNC)
    assert v["reset_sync"] == fsc.REFUTED and v["_"] == fsc.REFUTED, v


def test_a_negedge_flop_refutes_posedge(tmp_path):
    v = _check(tmp_path, NEG)
    assert v["clock_edge"] == fsc.REFUTED, v


def test_a_reset_that_reaches_no_flop_is_not_a_reset_port(tmp_path):
    v = _check(tmp_path, DEAD_RST)
    assert v["reset_port"] != fsc.PASS and v["_"] != fsc.PASS, v


def test_the_cli_both_ways(tmp_path, capsys):
    _yosys()
    for rtl, rc in ((SYNC, 0), (ASYNC, 1)):
        d = tmp_path / str(rc)
        d.mkdir()
        (d / "m.v").write_text(rtl)
        out = d / "rec.json"
        got = fsc.main(["--rtl", str(d / "m.v"), "--top", "m",
                        "--claim", "reset_sync:rst:synchronous",
                        "--work-dir", str(d), "--json", str(out)])
        capsys.readouterr()
        assert got == rc
        assert json.loads(out.read_text())["verdict"] == (fsc.PASS if rc == 0 else fsc.REFUTED)
