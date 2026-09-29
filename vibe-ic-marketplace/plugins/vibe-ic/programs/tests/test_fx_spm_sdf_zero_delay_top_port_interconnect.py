#!/usr/bin/env python3
"""FX_SPM_GATES_2 (1): a zero-delay INTERCONNECT to a top port is legal SDF.

MEASURED on the same-RTL spm x gf180mcuD run (lane cmpb, N5; vibeic-eda 0.3.83,
OpenSTA 3.1.0 `write_sdf`): step 29's `sdf_gate_sim` ran the run's 5 executed
L10 cases against the routed netlist, 5/5 PASS, and still FAILED: "5 SDF ERROR
record(s) no class explains". Every one is `spm.sdf:290: Could not find
intermodpath!` on `(INTERCONNECT _416_/Q p (0.000:0.000:0.000))`: the netlist
drives top output `p` through `assign p = pr;`, and the delay is exactly 0.
An independent Icarus 14.0 probe under `sdf_top_port_bus/` confirms the
alias case, a vector-bit top-port case, and direct scalar annotation.

The fixtures are that run's own bytes, copied verbatim into
`tests/fixtures/sdf_top_port/` (a TEST fixture of a design under test, so not
under `programs/calibration/`, whose samples must never come from one):
  * `spm.sdf` = `phase3/stage3/sim_postlayout/spm.sdf`
    (sha256 34701f988daa3c48...),
  * `spm_pnr.v` = `phase3/stage3/pnr/spm_pnr.v` (the netlist the gate
    simulated; sha256 13a1aa2382b19840...),
  * `case_3.sdf_errors.log` = lines 916-918 of `case_3.stdout.log` (the
    `vvp -sdf-info` transcript), with the run directory prefix made
    project-relative.
The negative guards are the same real SDF: a zero-delay record whose sink is
a CELL pin, and the same top-port record with a non-zero delay, stay
unexplained. The class needs an actual Icarus error, a top-port sink, and a
complete zero-delay record.
"""
from __future__ import annotations

import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
PROGRAMS = TESTS.parent
sys.path.insert(0, str(PROGRAMS))

import sdf_gate_sim as SG  # noqa: E402

FIX = TESTS / "fixtures" / "sdf_top_port"
SDF = (FIX / "spm.sdf").read_text()
NETLIST = (FIX / "spm_pnr.v").read_text()
TRANSCRIPT = (FIX / "case_3.sdf_errors.log").read_text()
CLASS = "ZERO_DELAY_TOP_PORT_INTERCONNECT"
BUS = TESTS / "fixtures" / "sdf_top_port_bus"
BUS_SDF = (BUS / "top.sdf").read_text()
BUS_ALIAS = (BUS / "top.v").read_text()
BUS_DIRECT = (BUS / "top_direct.v").read_text()
BUS_ALIAS_LOG = (BUS / "alias.log").read_text()
BUS_DIRECT_LOG = (BUS / "direct.log").read_text()


def _classify(transcript=TRANSCRIPT, sdf=SDF):
    return SG.classify_sdf_errors(transcript, compile_log="", sdf_text=sdf,
                                  explainer=SG.SdfErrorExplainer(NETLIST, {}))


def test_the_real_record_is_the_fixture_it_claims_to_be():
    lines = SDF.splitlines()
    assert lines[289].strip() == "(INTERCONNECT _416_/Q p (0.000:0.000:0.000))"
    assert "output p;" in NETLIST
    assert TRANSCRIPT.count("SDF ERROR") == 1


def test_a_zero_delay_interconnect_to_a_top_port_is_explained():
    """RED on main: `unexplained 1`, the step-29 FAIL."""
    got = _classify()
    assert got["by_class"] == {CLASS: 1}, got
    assert got["unexplained"] == 0
    assert CLASS in SG.SDF_ERROR_CLASSES
    line = SG._sdf_error_class_line({"sdf_errors_by_class": got["by_class"],
                                     "sdf_errors_unexplained": 0})
    assert f"{CLASS} 1 (owner sdf:legal, legal SDF, no fix owed)" in line


def test_a_zero_delay_record_whose_sink_is_a_cell_pin_stays_unexplained():
    """Line 285 of the same SDF: `(INTERCONNECT _411_/ZN _456_/D (0:0:0))`,
    zero delay but the sink is an instance pin -- not this class."""
    lines = SDF.splitlines()
    assert "_456_/D" in lines[284]
    got = _classify(TRANSCRIPT.replace("spm.sdf:290: Could not",
                                       "spm.sdf:285: Could not"))
    assert got["by_class"] == {} and got["unexplained"] == 1


def test_a_nonzero_delay_to_a_top_port_stays_unexplained():
    """A real delay dropped is not legal-and-harmless."""
    sdf = SDF.replace("(INTERCONNECT _416_/Q p (0.000:0.000:0.000))",
                      "(INTERCONNECT _416_/Q p (0.000:0.012:0.020))")
    got = _classify(sdf=sdf)
    assert got["by_class"] == {} and got["unexplained"] == 1


def test_multiple_records_on_one_error_line_are_not_mixed_or_guessed():
    """The error gives a line number, not the identity of a record on it."""
    original = "(INTERCONNECT _416_/Q p (0.000:0.000:0.000))"
    mixed = ("(INTERCONNECT _416_/Q p (0.000:0.012:0.020)) "
             "(INTERCONNECT _417_/Q other (0.000:0.000:0.000))")
    got = _classify(sdf=SDF.replace(original, mixed))
    assert got["by_class"] == {} and got["unexplained"] == 1, got
    assert not SG._all_delays_zero(mixed)

    both_zero = (original + " " +
                 "(INTERCONNECT _417_/Q other (0.000:0.000:0.000))")
    got = _classify(sdf=SDF.replace(original, both_zero))
    assert got["by_class"] == {} and got["unexplained"] == 1, got


def test_an_incomplete_record_is_not_read_as_zero():
    assert not SG._all_delays_zero("(INTERCONNECT a p ()")
    assert not SG._all_delays_zero("(INTERCONNECT a p ( : : ))")
    assert not SG._all_delays_zero("(INTERCONNECT a p (0.000:0.000:0.000)")
    assert SG._all_delays_zero("(INTERCONNECT a p (0.000:0.000:0.000) "
                               "(0:0:0))")


def test_real_icarus_bus_bit_and_alias_transcript_is_explained():
    """Icarus 14.0 devel, 0.3.83 image, -ginterconnect -gspecify.

    Both records came from vvp -sdf-info on the committed netlist and SDF.
    q[1] is a declared vector bit; p is a scalar assign alias.
    """
    assert "SDF ERROR: top.sdf:15: Could not find intermodpath!" in BUS_ALIAS_LOG
    assert "SDF ERROR: top.sdf:16: Could not find intermodpath!" in BUS_ALIAS_LOG
    got = SG.classify_sdf_errors(
        BUS_ALIAS_LOG, compile_log="", sdf_text=BUS_SDF,
        explainer=SG.SdfErrorExplainer(BUS_ALIAS, {}))
    assert got["by_class"] == {CLASS: 2} and got["unexplained"] == 0, got


def test_real_icarus_direct_scalar_annotates_but_vector_bit_errors():
    assert "SDF ERROR: top.sdf:15: Could not find intermodpath!" in BUS_DIRECT_LOG
    assert "SDF ERROR: top.sdf:16:" not in BUS_DIRECT_LOG
    assert "Created a vpiInterModPath" in BUS_DIRECT_LOG
    got = SG.classify_sdf_errors(
        BUS_DIRECT_LOG, compile_log="", sdf_text=BUS_SDF,
        explainer=SG.SdfErrorExplainer(BUS_DIRECT, {}))
    assert got["by_class"] == {CLASS: 1} and got["unexplained"] == 0, got


def test_bus_bit_requires_a_declared_vector_and_zero_delay():
    bus_only = "SDF ERROR: top.sdf:15: Could not find intermodpath!\n"
    explainer = SG.SdfErrorExplainer(BUS_ALIAS, {})
    assert explainer.is_top_port("q[1]", "/")
    assert explainer.is_top_port(r"q\[1\]", "/")
    assert explainer.endpoint_dir("q[1]", "/") == "output"
    assert not explainer.is_top_port("p[0]", "/")
    nonzero = BUS_SDF.replace(
        "(INTERCONNECT u1/Z q[1] (0.000:0.000:0.000))",
        "(INTERCONNECT u1/Z q[1] (0.000:0.010:0.020))")
    got = SG.classify_sdf_errors(
        bus_only, compile_log="", sdf_text=nonzero,
        explainer=explainer)
    assert got["by_class"] == {} and got["unexplained"] == 1, got
    scalar_q = BUS_ALIAS.replace("output [1:0] q", "output q")
    got = SG.classify_sdf_errors(
        bus_only, compile_log="", sdf_text=BUS_SDF,
        explainer=SG.SdfErrorExplainer(scalar_q, {}))
    assert got["by_class"] == {} and got["unexplained"] == 1, got


def test_published_cause_names_measured_alias_and_vector_cases():
    why = SG.SDF_ERROR_CLASSES[CLASS]["why"]
    assert "assign" in why and "vector bit" in why
    assert "direct scalar" in why
