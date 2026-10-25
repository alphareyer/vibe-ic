#!/usr/bin/env python3
"""ORGANIC #780 (#770 r7, R8C1) — spec_coverage_check.py worked_example FP.

ROOT CAUSE
----------
`worked_example` is a PROSE_HEURISTIC kind, but run()'s #770 provenance loop had
corroboration branches only for reset/latency/handshake/enum_set — and NONE for
worked_example. So a worked_example gap kept corr=UNKNOWN and
`is_block_eligible(PROSE_HEURISTIC, UNKNOWN)` returns True (no-leak bias), so the
gap hard-blocked correct RTL under --strict.

The blocking gaps were PHANTOM (structural artifacts), not real in->out vectors —
`_WORKED_EXAMPLE_RE` mis-read surrounding Verilog / per-digit notation:
  * (A) an identifier-suffix LHS:  "GRANT_2 = 3'b010"  -> phantom "2 -> 3"
  * (B) a sized-literal-width RHS: the "3" of "3'b010" is a bit-width, not data
  * (C) a binary-nibble=decimal gloss: "0101 = 5" — one per-digit step of ONE
        worked example bcd_in=0010_0101_0111 -> 257 (Process MSD/Middle/LSD),
        NOT an independent vector (bin 0101 == 5, != its decimal reading 101).

THE FIX (chip-AGNOSTIC, program-first)
--------------------------------------
extract_checklist now tags each worked_example with `we_structural_artifact`
(via `_worked_example_structural_artifact`, pure Verilog/notation grammar). run()
gains a worked_example provenance branch: an artifact match -> NO_CORROBORATION ->
ADVISORY (routed through the existing _provenance path, not silently dropped); a
genuine in->out example (artifact False) keeps UNKNOWN -> BLOCK.

§4.05 NO-LEAK (load-bearing half)
---------------------------------
A GENUINE worked example the TB never stimulates must STILL hard-BLOCK:
  * decimal  42 -> 137  (not an identifier suffix, not a width, not a self-gloss)
  * binary 0011 -> 1010 (bin 0011 == 3 != 1010 dec, so NOT a self-decode gloss)
Both stay we_structural_artifact False -> UNKNOWN -> BLOCK.

chip-AGNOSTIC: pure Verilog/notation grammar (identifier-suffix / sized-literal
apostrophe / binary-nibble self-value); no chip / vendor / SKU literal.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROGRAMS))
import spec_coverage_check as SC  # noqa: E402

_SPEC_COV = _PROGRAMS / "spec_coverage_check.py"


# ---------------------------------------------------------------------------
# Fixtures — the two round-8 AFFECTED shapes (synthetic, self-contained)
# ---------------------------------------------------------------------------
# (BCD) per-digit decomposition gloss of ONE example -> phantom 0111 -> 257 +
# 0010=2 / 0101=5 / 0111=7 self-decode glosses.
_BCD_SPEC = (
    "Binary-to-BCD converter (8-bit -> 3-digit BCD).\n"
    "Worked example: bcd_in = 0010_0101_0111 -> 257.\n"
    "Process MSD: 0010 = 2\n"
    "Process Middle: 0101 = 5\n"
    "Process LSD: 0111 = 7\n"
    "Inputs: clk, bin_in[7:0]. Output: bcd_out[11:0].\n")
_BCD_RTL = (
    "module binary_to_BCD(input clk, input [7:0] bin_in, output reg [11:0] bcd_out);\n"
    "  integer i; reg [19:0] shift;\n"
    "  always @(*) begin\n"
    "    shift = 0; shift[7:0] = bin_in;\n"
    "    for (i=0;i<8;i=i+1) begin\n"
    "      if (shift[11:8] >= 5) shift[11:8] = shift[11:8] + 3;\n"
    "      if (shift[15:12] >= 5) shift[15:12] = shift[15:12] + 3;\n"
    "      if (shift[19:16] >= 5) shift[19:16] = shift[19:16] + 3;\n"
    "      shift = shift << 1;\n"
    "    end\n"
    "    bcd_out = shift[19:8];\n"
    "  end\nendmodule\n")
_BCD_TB = (
    "module tb;\n  reg clk; reg [7:0] bin_in; wire [11:0] bcd_out;\n"
    "  binary_to_BCD dut(clk, bin_in, bcd_out);\n"
    "  initial begin clk=0; bin_in=8'd99; #10 bin_in=8'd0; #10"
    " $display(\"ALL TESTS PASSED\"); $finish; end\n"
    "  always #5 clk=~clk;\nendmodule\n")

# (ARB) state-encoding localparam legend GRANT_2 = 3'b010 -> phantom 2 -> 3
# (LHS '2' is the suffix of GRANT_2; RHS '3' is the width of 3'b010).
_ARB_SPEC = (
    "Bus arbiter, round-robin priority.\n"
    "State encoding:\n"
    "  localparam GRANT_2 = 3'b010;\n"
    "Inputs: clk, rst_n, req[3:0]. Output: grant[3:0].\n")
_ARB_RTL = (
    "module bus_arbiter(input clk, input rst_n, input [3:0] req,"
    " output reg [3:0] grant);\n"
    "  localparam GRANT_2 = 3'b010;\n"
    "  always @(posedge clk or negedge rst_n)\n"
    "    if (!rst_n) grant <= 4'b0000; else grant <= req;\nendmodule\n")
_ARB_TB = (
    "module tb;\n  reg clk, rst_n; reg [3:0] req; wire [3:0] grant;\n"
    "  bus_arbiter dut(clk, rst_n, req, grant);\n"
    "  initial begin clk=0; rst_n=0; #5 rst_n=1; req=4'b0001; #10 req=4'b1000;"
    " #10 $display(\"PASS: all checks ok\"); $finish; end\n"
    "  always #5 clk=~clk;\nendmodule\n")

# §4.05 NEGATIVE — GENUINE in->out worked examples the TB never stimulates.
_GEN_DEC_SPEC = (
    "Transform unit.\n"
    "Worked example: 42 -> 137.\n"
    "Inputs: x[7:0]. Output: y[7:0].\n")
_GEN_DEC_RTL = (
    "module xform(input [7:0] x, output reg [7:0] y);\n"
    "  always @(*) y = x + 95;\nendmodule\n")
_GEN_DEC_TB = (
    "module tb;\n  reg [7:0] x; wire [7:0] y;\n  xform dut(x, y);\n"
    "  initial begin x=8'd10; #10 x=8'd200; #10 $display(\"done\"); $finish; end\n"
    "endmodule\n")

_GEN_BIN_SPEC = (
    "Combinational mapper.\n"
    "Worked example: 0011 -> 1010.\n"
    "Inputs: a[3:0]. Output: b[3:0].\n")
_GEN_BIN_RTL = (
    "module mapper(input [3:0] a, output reg [3:0] b);\n"
    "  always @(*) b = a + 4'd7;\nendmodule\n")
_GEN_BIN_TB = (
    "module tb;\n  reg [3:0] a; wire [3:0] b;\n  mapper dut(a, b);\n"
    "  initial begin a=4'd1; #10 a=4'd5; #10 $display(\"done\"); $finish; end\n"
    "endmodule\n")


def _run(tmp_path, spec, rtl, tb, strict=True):
    (tmp_path / "spec.md").write_text(spec)
    (tmp_path / "rtl.sv").write_text(rtl)
    (tmp_path / "tb.sv").write_text(tb)
    cmd = [sys.executable, str(_SPEC_COV),
           "--spec", str(tmp_path / "spec.md"),
           "--rtl", str(tmp_path / "rtl.sv"),
           "--tb", str(tmp_path / "tb.sv")]
    if strict:
        cmd.append("--strict")
    return subprocess.run(cmd, capture_output=True, text=True)


def _we_items(spec, rtl, tb):
    rep = SC.run({"user_prompt": spec}, rtl, tb, None, True)
    return rep, [it for it in rep["items"] if it["kind"] == "worked_example"]


# ---------------------------------------------------------------------------
# Unit-level: the structural-artifact detector (pure grammar)
# ---------------------------------------------------------------------------
def _first_match(text):
    return next(SC._WORKED_EXAMPLE_RE.finditer(text))


def test_detector_identifier_suffix_lhs_is_artifact():
    # "GRANT_2 = 3'b010" — LHS '2' is the suffix of GRANT_2 (A) AND RHS '3' is a
    # sized-literal width (B). Either shape is enough.
    t = "localparam GRANT_2 = 3'b010;"
    assert SC._worked_example_structural_artifact(t, _first_match(t)) is True


def test_detector_sized_literal_width_rhs_is_artifact():
    # "STATE = 3'b001" — RHS '3' is immediately followed by an apostrophe.
    t = "code 8 = 3'b001"
    assert SC._worked_example_structural_artifact(t, _first_match(t)) is True


def test_detector_binary_nibble_self_gloss_is_artifact():
    # "0101 = 5" is a per-digit decomposition gloss ONLY when the larger grouped
    # source (`0010_0101_0111`) is present — bin 0101 == 5 != its decimal 101.
    # (ORGANIC #780 r2: the gloss now requires a grouped decomposition source.)
    t = "bcd_in = 0010_0101_0111 -> 257. Process Middle: 0101 = 5"
    gloss = [m for m in SC._WORKED_EXAMPLE_RE.finditer(t) if m.group(1) == "0101"]
    assert gloss and SC._worked_example_structural_artifact(t, gloss[0]) is True


def test_780r2_noleak_standalone_binary_decimal_vector_is_not_artifact():
    # ORGANIC #780 r2 Step-2.7 §4.05: a GENUINE standalone binary->decimal vector
    # (a real binary-to-BCD / decoder test) has NO grouped decomposition source,
    # so it must NOT be tagged a gloss — it keeps BLOCKING when the TB misses it.
    for t in ["0101 -> 5", "0010 -> 2", "0011 -> 3", "1010 -> 10"]:
        assert SC._worked_example_structural_artifact(t, _first_match(t)) is False, t


def test_detector_genuine_decimal_pair_is_not_artifact():
    t = "Worked example: 42 -> 137."
    assert SC._worked_example_structural_artifact(t, _first_match(t)) is False


def test_detector_genuine_binary_pair_is_not_artifact():
    # bin 0011 == 3 != 1010 dec, so this is a real vector, not a self-gloss.
    t = "Worked example: 0011 -> 1010."
    assert SC._worked_example_structural_artifact(t, _first_match(t)) is False


# ---------------------------------------------------------------------------
# POSITIVE — the two AFFECTED ids flip hard-BLOCK -> advisory (rc 1 -> 0)
# ---------------------------------------------------------------------------
def test_bcd_self_decode_gloss_no_longer_hard_blocks(tmp_path):
    r = _run(tmp_path, _BCD_SPEC, _BCD_RTL, _BCD_TB)
    assert r.returncode == 0, r.stdout + r.stderr
    rep, we = _we_items(_BCD_SPEC, _BCD_RTL, _BCD_TB)
    assert rep["blocked"] is False
    # every worked_example here is a structural artifact -> advisory, not block.
    assert we, "expected the per-digit glosses to be extracted as worked examples"
    for it in we:
        assert it["we_structural_artifact"] is True
        assert it["block_eligible"] is False
        assert it["advisory_note"]  # routed through _provenance, not dropped


def test_arb_state_encoding_legend_no_longer_hard_blocks(tmp_path):
    r = _run(tmp_path, _ARB_SPEC, _ARB_RTL, _ARB_TB)
    assert r.returncode == 0, r.stdout + r.stderr
    rep, we = _we_items(_ARB_SPEC, _ARB_RTL, _ARB_TB)
    assert rep["blocked"] is False
    assert we
    for it in we:
        assert it["we_structural_artifact"] is True
        assert it["block_eligible"] is False


# ---------------------------------------------------------------------------
# §4.05 NO-LEAK — GENUINE uncovered worked examples must STILL hard-BLOCK
# ---------------------------------------------------------------------------
def test_genuine_decimal_worked_example_still_hard_blocks(tmp_path):
    r = _run(tmp_path, _GEN_DEC_SPEC, _GEN_DEC_RTL, _GEN_DEC_TB)
    assert r.returncode == 1, r.stdout + r.stderr
    rep, we = _we_items(_GEN_DEC_SPEC, _GEN_DEC_RTL, _GEN_DEC_TB)
    assert rep["blocked"] is True
    # the genuine 42 -> 137 vector is present, NOT an artifact, and blocks.
    g = [it for it in we if "42" in it["requirement"] and "137" in it["requirement"]]
    assert g, [it["requirement"] for it in we]
    assert g[0]["we_structural_artifact"] is False
    assert g[0]["covered"] is False
    assert g[0]["block_eligible"] is True


def test_genuine_binary_worked_example_still_hard_blocks(tmp_path):
    r = _run(tmp_path, _GEN_BIN_SPEC, _GEN_BIN_RTL, _GEN_BIN_TB)
    assert r.returncode == 1, r.stdout + r.stderr
    rep, we = _we_items(_GEN_BIN_SPEC, _GEN_BIN_RTL, _GEN_BIN_TB)
    assert rep["blocked"] is True
    g = [it for it in we
         if "0011" in it["requirement"] and "1010" in it["requirement"]]
    assert g, [it["requirement"] for it in we]
    assert g[0]["we_structural_artifact"] is False
    assert g[0]["block_eligible"] is True


# ---------------------------------------------------------------------------
# Cross-station merge — an artifact tag at ANY station survives the merge
# ---------------------------------------------------------------------------
def test_artifact_tag_survives_cross_station_merge():
    # Same worked_example text at two stations; the artifact tag must persist
    # (the structural shape is identical), so the merged item stays advisory.
    stations = {"user_prompt": _BCD_SPEC, "l_docs": _BCD_SPEC}
    rep = SC.run(stations, _BCD_RTL, _BCD_TB, None, True)
    assert rep["blocked"] is False
    we = [it for it in rep["items"] if it["kind"] == "worked_example"]
    assert we
    assert all(it["we_structural_artifact"] for it in we)


def _width_example_spec(header, width=16, *, explicit_owner=True):
    pair = ("16'h1234", "16'hedcb") if width == 16 else ("8'h34", "8'hcb")
    example = (f"For example, a {width}-bit vector inverter\n"
               f"given the input {pair[0]} would output {pair[1]}.\n"
               if explicit_owner else
               f"For input {pair[0]}, the output is {pair[1]}.\n")
    return example + "Build the inverter with the following exact interface:\n" + header


@pytest.mark.parametrize("source_form", ["verilog", "named_prose"])
def test_separate_width_illustration_is_advisory_through_native_program_contract(tmp_path, source_form):
    import hashlib
    import benchmark_dispatch as bd
    from _hostpaths import require_repo
    rtl = require_repo("vibe-ic-marketplace", "plugins", "vibe-ic", "programs", "tests",
                       "fixtures", "librelane_lint_frontend", "plain.v").read_text()
    interface = ("Implement a module named plain with the following interface.\n"
                 " - input value_i (8 bits)\n - output value_o (8 bits)\n"
                 if source_form == "named_prose" else rtl)
    spec = _width_example_spec(interface)
    tb = '''module tb;
reg [7:0] value_i; wire [7:0] value_o; integer n;
plain u(.value_i(value_i), .value_o(value_o));
initial begin
  for (n=0; n<256; n=n+1) begin
    value_i=n; #1;
    if (value_o !== (~value_i & 8'hff)) $fatal(1, "inversion mismatch");
  end
  $display("all 256 native cases passed"); $finish;
end
endmodule
'''
    source, bench = tmp_path / "plain.v", tmp_path / "tb.sv"
    source.write_text(rtl)
    bench.write_text(tb)
    compiled = subprocess.run(["iverilog", "-g2012", "-s", "tb", "-o", str(tmp_path / "sim"),
                               str(source), str(bench)], capture_output=True, text=True)
    assert compiled.returncode == 0, compiled.stderr
    simulated = subprocess.run(["vvp", str(tmp_path / "sim")], capture_output=True, text=True)
    assert simulated.returncode == 0 and "all 256 native cases passed" in simulated.stdout
    report, examples = _we_items(spec, rtl, tb)
    assert examples
    for item in examples:
        assert item["block_eligible"] is False
    assert all(it["advisory_note"] for it in examples)
    assert all(it["block_eligible"] is True for it in report["items"] if it["kind"] == "port")
    candidate = {"rtl_paths": [str(source)]}
    contract = bd._program_review_obligation_contract(spec, candidate)
    assert not any(it["kind"] == "worked_example" for it in contract["obligations"])
    coverage = bd._program_review_coverage_result(spec, candidate, [{
        "path": str(bench), "sha256": hashlib.sha256(bench.read_bytes()).hexdigest()}])
    assert coverage["status"] == "PASS", coverage


@pytest.mark.parametrize("case", ["same_width", "no_separate_owner", "wrong_candidate_width",
                                  "wrong_candidate_name", "phantom_named_candidate",
                                  "unknown_source_width", "unknown_output_width",
                                  "missing_source_interface", "no_rtl"])
def test_real_or_unresolved_width_examples_remain_blocking(case):
    rtl = "module neutral(input [7:0] data_i, output [7:0] data_o); assign data_o=~data_i; endmodule\n"
    spec = _width_example_spec(rtl, 8 if case == "same_width" else 16,
                               explicit_owner=case != "no_separate_owner")
    if case == "wrong_candidate_width":
        rtl = rtl.replace("[7:0]", "[3:0]")
    elif case in {"wrong_candidate_name", "phantom_named_candidate"}:
        spec = _width_example_spec(
            "Implement a module named DesiredUnit.\n"
            " - input data_i (8 bits)\n - output data_o (8 bits)\n")
        if case == "phantom_named_candidate":
            rtl = rtl.replace("module neutral", "module named")
    elif case == "unknown_source_width":
        spec = spec.replace("[7:0]", "[WIDTH-1:0]")
    elif case == "unknown_output_width":
        spec = spec.replace("output [7:0]", "output [WIDTH-1:0]")
        rtl = rtl.replace("output [7:0]", "output")
    elif case == "missing_source_interface":
        spec = spec.split("Build the inverter", 1)[0] + "Build an eight-bit inverter.\n"
    elif case == "no_rtl":
        rtl = None
    _, examples = _we_items(spec, rtl, None)
    assert examples and all(it["block_eligible"] is True for it in examples)


@pytest.mark.parametrize("text,expected", [
    ("Implement a module named PacketUnit.", "PacketUnit"),
    ("Implement a module named `PacketUnit`.", "PacketUnit"),
    ("Implement a module Named PacketUnit.", "PacketUnit"),
    ("module named(input a,output y); endmodule", "named"),
    ("module named #(parameter W=8)(input [W-1:0] a,output y); endmodule", "named"),
    ("module named; input a; output y; endmodule", "named"),
    ('module named(input a,output y); initial $display("module named TopModule"); endmodule', "named"),
    ("// Implement a module named PhantomUnit.\n", None),
    ('"Implement a module named PhantomUnit."', None),
])
def test_module_name_reader_distinguishes_named_prose_from_hdl_and_phantoms(text, expected):
    assert SC._SRC.extract_spec_contract(text, confirm=False).module == expected


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
