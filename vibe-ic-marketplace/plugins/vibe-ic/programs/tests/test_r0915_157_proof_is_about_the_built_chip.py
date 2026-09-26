#!/usr/bin/env python3
"""R-0915-157, round 9 — a program-closed proof must be about the chip phase 3 BUILDS.

Step 5 records the exact chip read it proved (`results["chip_read"]`: every
file with its sha256, the define decision with its verdict and the macro
inputs it saw, the top) through `_chip_synth_read` — the one definition that
phase-3 synthesis also reads with and resolves its top with
(`effective_top`). Phase-3 `step_synth` writes the read it BUILT
(`write_built_record`) and compares. Any difference makes Step 5's
program-closed obligations STALE: `formal_proof_evidence_check` refuses them
(NOT_DISCHARGED "proof read a different chip: <what>"), never PASS. A design
that declares an analog block whose A8 hard macro is not staged yet is
outside the class at Step 5 (its define decision cannot be known there).

Through the real programs (`formal_harness_gen.generate` ->
`formal_property_run.run` -> `formal_proof_evidence_check.audit`), on the
round-8 review's RTL: `ifdef SIMULATION` resets `acc`; the macro arm leaves it
unreset and instantiates a macro.

The engine arms need yosys + sby on PATH (the vibeic-eda image, as CI and
falsref run them); without them they FAIL with the reason, never skip.
chip-AGNOSTIC: generic fixtures written here.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _chip_synth_read as csr  # noqa: E402
import formal_proof_evidence_check as gate  # noqa: E402
from test_r0915_157_proof_reads_the_chip import _l8, _project, _step5, _closed  # noqa: E402

MACRO_RTL = """module m(input clk, input rst, input [7:0] d, output reg [7:0] q);
  reg [7:0] acc;
  always @(posedge clk) if (rst) q <= 8'd0; else q <= d ^ acc;
`ifdef SIMULATION
  always @(posedge clk) if (rst) acc <= 8'd0; else acc <= d;
`else
  always @(posedge clk) acc <= d;
  adc_core u_adc (.clk(clk));
`endif
endmodule
"""
LEF = "VERSION 5.8 ;\nMACRO adc_core\n  CLASS BLOCK ;\nEND adc_core\nEND LIBRARY\n"
LIB = "library (adc_core_lib) {\n  cell (adc_core) {\n    area : 1 ;\n  }\n}\n"
PLAIN_RTL = """module m(input clk, input rst, input [7:0] d, output reg [7:0] q);
  reg [7:0] a;
  always @(posedge clk) if (rst) begin a <= 8'd0; q <= 8'd0; end else begin a <= d; q <= a; end
endmodule
"""
ASIC = """module m_asic(input clk, input rst, input [7:0] d, output [7:0] q);
  reg [7:0] stuck;
  always @(posedge clk) stuck <= d;
  m core (.clk(clk), .rst(rst), .d(d ^ stuck), .q(q));
endmodule
"""


def _stage_a8_macro(project: Path, name: str = "adc_core") -> None:
    hm = project / "phase3/analog/hardmacro" / name
    hm.mkdir(parents=True, exist_ok=True)
    (hm / f"{name}.lef").write_text(LEF)
    (hm / f"{name}.lib").write_text(LIB)


def _declare_analog(project: Path, name: str = "adc_core") -> None:
    d = project / "phase3/analog"
    d.mkdir(parents=True, exist_ok=True)
    (d / "analog_block_list.json").write_text(json.dumps({"blocks": [{"name": name}]}))


def _refused_as_stale(rep: dict, what: str) -> None:
    assert rep["verdict"] != "PASS", rep["findings"]
    stale = rep.get("chip_read_stale") or []
    assert any(what in s for s in stale), stale
    assert any(f.startswith("CHIP_READ_STALE") and "proof read a different chip" in f
               for f in rep["findings"]), rep["findings"]


# ── the baseline: the same chip, recorded, PASS ─────────────────────────────
def test_a_proof_records_the_chip_read_it_proved_and_passes(tmp_path):
    proj = _project(tmp_path, MACRO_RTL, _l8())
    _, res, rep = _step5(proj)
    rec = res["chip_read"]
    assert rec["top"] == "m"
    assert rec["define"] == {"simulation": True, "verdict": "BEHAVIOURAL_NO_MACRO",
                             "macro_cells": [], "macro_inputs": []}
    assert [f["name"] for f in rec["files"]] == ["m.v"]
    assert len(rec["files"][0]["sha256"]) == 64
    assert rep["verdict"] == "PASS", rep["findings"]
    assert not rep.get("chip_read_stale")
    # phase-3 builds the same read: nothing differs, nothing stale
    files = csr.chip_rtl_files(proj / "phase2/stage1/rtl")
    assert csr.write_built_record(proj, files, [], "m") == []
    assert gate.audit(proj)["verdict"] == "PASS"


# ── HIGH: A8 stages the macro after Step 5 — the define decision flips ─────
def test_a_macro_staged_after_the_proof_makes_it_stale_not_pass(tmp_path):
    proj = _project(tmp_path, MACRO_RTL, _l8())
    _, res, rep = _step5(proj)
    assert rep["verdict"] == "PASS"
    _stage_a8_macro(proj)                    # A8, phase 3
    rep = gate.audit(proj)
    _refused_as_stale(rep, "define")
    ids = {r["id"] for r in rep["unresolved_obligations"]}
    assert set(res["program_discharged_obligations"]) <= ids
    assert all(r["status"] == "NOT_DISCHARGED" for r in rep["unresolved_obligations"]
               if r["id"] in set(res["program_discharged_obligations"]))


def test_phase3_synthesis_compares_its_built_read(tmp_path):
    # the macro reaches phase 3 through the PDK, not the project tree: only
    # the built record can see it
    proj = _project(tmp_path, MACRO_RTL, _l8())
    _step5(proj)
    pdk = tmp_path / "pdk_macros"
    pdk.mkdir()
    (pdk / "adc_core.lef").write_text(LEF)
    (pdk / "adc_core.lib").write_text(LIB)
    files = csr.chip_rtl_files(proj / "phase2/stage1/rtl")
    diffs = csr.write_built_record(proj, files, [pdk / "adc_core.lef",
                                                 pdk / "adc_core.lib"], "m")
    assert any(d.startswith("define") and "MACRO_INSTANTIATED" in d for d in diffs), diffs
    built = json.loads(csr.built_record_path(proj).read_text())
    assert built["define"]["simulation"] is False
    assert built["stale_against_step5"] == diffs
    _refused_as_stale(gate.audit(proj), "phase-3 synthesis — define")


def test_a_declared_analog_block_without_its_macro_is_outside_the_class(tmp_path):
    proj = _project(tmp_path, MACRO_RTL, _l8())
    _declare_analog(proj)
    gen, res, rep = _step5(proj)
    assert not _closed(res)
    assert rep["verdict"] != "PASS"
    refused = json.dumps(gen) + (proj / "phase2/stage1/formal/property_contract.json").read_text()
    assert "outside the class" in refused and "adc_core" in refused


def test_a_declared_analog_block_with_its_macro_staged_decides_with_it(tmp_path):
    proj = _project(tmp_path, MACRO_RTL, _l8())
    _declare_analog(proj)
    _stage_a8_macro(proj)
    _, res, rep = _step5(proj)
    # the chip's arm: acc is unreset — never a program PASS on "all zero"
    assert res.get("chip_read", {}).get("define", {}).get("simulation") in (False, None)
    assert "L8.clock_and_reset_waveform.resets.0.port_description" not in _closed(res)
    assert rep["verdict"] != "PASS"


# ── MEDIUM: phase 3 builds <top>_asic ───────────────────────────────────────
def test_the_asic_top_is_the_one_definition():
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        proj = _project(Path(t), PLAIN_RTL, _l8())
        assert csr.effective_top(proj, "m") == "m"
        (proj / "phase2/stage1/rtl/m_asic.sv").write_text(ASIC)
        assert csr.effective_top(proj, "m") == "m_asic"


def test_a_proof_of_top_when_phase3_builds_top_asic_is_refused(tmp_path):
    proj = _project(tmp_path, PLAIN_RTL, _l8())
    (proj / "phase2/stage1/rtl/m_asic.sv").write_text(ASIC)
    _, res, rep = _step5(proj)
    assert _closed(res)          # the proof of m itself holds ...
    # ... but it is not the chip: refused, never PASS
    _refused_as_stale(rep, "top: the proof proved 'm', the chip is built from 'm_asic'")


# ── the file set / contents ─────────────────────────────────────────────────
def test_an_rtl_edit_after_the_proof_makes_it_stale(tmp_path):
    proj = _project(tmp_path, PLAIN_RTL, _l8())
    _, res, rep = _step5(proj)
    assert rep["verdict"] == "PASS", rep["findings"]
    (proj / "phase2/stage1/rtl/m.v").write_text(PLAIN_RTL.replace("a <= d;", "a <= ~d;"))
    _refused_as_stale(gate.audit(proj), "sha256")


def test_a_program_closure_without_a_recorded_read_is_refused(tmp_path):
    proj = _project(tmp_path, PLAIN_RTL, _l8())
    _step5(proj)
    rp = proj / "phase2/stage1/formal/results.json"
    r = json.loads(rp.read_text())
    r.pop("chip_read")
    rp.write_text(json.dumps(r))
    _refused_as_stale(gate.audit(proj), "recorded no chip read")


def test_the_differences_are_named_both_ways():
    rec = {"top": "m", "files": [{"name": "m.v", "sha256": "a"}],
           "define": {"simulation": True, "verdict": "BEHAVIOURAL_NO_MACRO"}}
    assert csr.chip_read_differences(rec, dict(rec)) == []
    assert csr.chip_read_differences(rec, dict(rec, top="m_asic"))
    assert csr.chip_read_differences(rec, dict(rec, files=[{"name": "m.v", "sha256": "b"}]))
    assert csr.chip_read_differences(rec, dict(rec, files=[]))
    assert csr.chip_read_differences(
        rec, dict(rec, define={"simulation": False, "verdict": "MACRO_INSTANTIATED"}))
