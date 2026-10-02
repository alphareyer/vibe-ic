#!/usr/bin/env python3
"""Tests for canonical_primitive_synth.py.

POSITIVE: for each of the 9 dataset design_description.txt files, detect_shape
must return the expected shape, and --from-desc emit must produce RTL whose
declared module name matches the spec's 'Module name:' token.

NEGATIVE: a handful of OTHER RTLLM specs (adder_8bit, freq_divbyeven,
synchronizer, multi_16bit) must detect_shape -> None (no mis-fire).
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import os
import pytest

HERE = Path(__file__).resolve().parent
PROGRAMS = HERE.parent                         # .../programs
PROG = PROGRAMS / "canonical_primitive_synth.py"
# Resolved from the environment, not hardcoded: a personal home path in
# shipped source is unresolvable for every other user, and
# `shipped_path_portability_check` blocks on it. The dataset-backed tests
# already skip when the corpus is absent, so an unset var simply skips.
# Reintroduced twice by stacked PRs authored against an older base — hence
# the gate, and hence this comment sitting where the literal used to be.
RTLLM = Path(os.environ.get("VIBEIC_RTLLM_CORPUS",
                            Path.home() / "_bench_rtllm2_scratch/RTLLM"))
# The dataset-backed tests only run where the RTLLM corpus is checked out locally;
# in CI it is absent, so they skip. The self-contained inline tests below prove
# the detection contract with NO external dataset (they always run).
_need_dataset = pytest.mark.skipif(
    not RTLLM.exists(), reason="RTLLM dataset not present on this host")

sys.path.insert(0, str(PROGRAMS))
import canonical_primitive_synth as rcs  # noqa: E402

# spec-dir (relative to RTLLM) -> (expected_shape, expected_module)
POSITIVE = {
    "Miscellaneous/Frequency divider/freq_divbyodd":
        ("odd_clock_divider", "freq_divbyodd"),
    "Miscellaneous/Frequency divider/freq_divbyfrac":
        ("frac_clock_divider_3p5", "freq_divbyfrac"),
    "Miscellaneous/Others/pulse_detect":
        ("pulse_detect_0to1to0", "pulse_detect"),
    "Miscellaneous/Others/serial2parallel":
        ("serial_to_parallel_8", "serial2parallel"),
    "Miscellaneous/Others/parallel2serial":
        ("parallel_to_serial_4", "parallel2serial"),
    "Arithmetic/Divider/div_16bit":
        ("combinational_long_divider", "div_16bit"),
    "Miscellaneous/Others/traffic_light":
        ("traffic_light_fsm", "traffic_light"),
    "Arithmetic/Divider/radix2_div":
        ("radix2_signed_divider", "radix2_div"),
    "Arithmetic/Other/float_multi":
        ("ieee754_single_multiplier", "float_multi"),
    "Memory/FIFO/asyn_fifo":
        ("async_gray_fifo", "asyn_fifo"),
    "Arithmetic/Multiplier/multi_pipe_8bit":
        ("pipelined_unsigned_multiplier_8", "multi_pipe_8bit"),
    "Memory/Shifter/barrel_shifter":
        ("barrel_shifter_right_8", "barrel_shifter"),
    "Miscellaneous/Signal generation/signal_generator":
        ("triangle_wave_generator_5", "signal_generator"),
    "Control/Finite State Machine/fsm":
        ("mealy_seq_detector_10011", "fsm"),
    "Arithmetic/Adder/adder_pipe_64bit":
        ("pipelined_ripple_adder_64", "adder_pipe_64bit"),
}

NEGATIVE = [
    "Arithmetic/Adder/adder_8bit",
    "Miscellaneous/Frequency divider/freq_divbyeven",
    "Miscellaneous/Others/synchronizer",
    "Arithmetic/Multiplier/multi_16bit",
]


def _desc(reldir: str) -> str:
    return (RTLLM / reldir / "design_description.txt").read_text(errors="replace")


def _module_decl(rtl: str) -> str:
    # top module: for asyn_fifo the top is asyn_fifo (dual_port_RAM is a submodule).
    mods = re.findall(r"(?m)^\s*module\s+([A-Za-z_]\w*)", rtl)
    return mods[-1] if mods else ""


@_need_dataset
@pytest.mark.parametrize("reldir,expected", POSITIVE.items())
def test_detect_positive(reldir, expected):
    shape, module = expected
    desc = _desc(reldir)
    assert rcs.detect_shape(desc) == shape


@_need_dataset
@pytest.mark.parametrize("reldir,expected", POSITIVE.items())
def test_emit_from_desc(reldir, expected, tmp_path):
    shape, module = expected
    spec = RTLLM / reldir / "design_description.txt"
    out = tmp_path / f"{module}.v"
    r = subprocess.run(
        [sys.executable, str(PROG), "--from-desc", str(spec), "--out", str(out)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    j = json.loads(r.stdout)
    assert j["verdict"] == "EMIT"
    assert j["shape"] == shape
    assert j["module"] == module
    rtl = out.read_text()
    # emitted RTL declares a module whose (top) name matches the spec module name
    assert re.search(r"(?m)^\s*module\s+" + re.escape(module) + r"\b", rtl)
    # and the spec's own Module name token equals that module
    assert rcs.module_name_of(_desc(reldir)) == module


@_need_dataset
@pytest.mark.parametrize("reldir", NEGATIVE)
def test_detect_negative(reldir):
    desc = _desc(reldir)
    assert rcs.detect_shape(desc) is None, f"mis-fired on {reldir}"


def test_emit_covers_all_nine():
    shapes = {s for s, _ in POSITIVE.values()}
    for s in shapes:
        rtl = rcs.emit_rtl(s)
        assert "module" in rtl and "endmodule" in rtl


# ============================================================================
# Self-contained inline tests — NO external dataset, always run (incl. CI).
# These pin the STRUCTURAL detection contract with synthetic descriptions that
# reproduce each shape's signature, and prove fail-closed DEFER on near-misses.
# ============================================================================
_INLINE_POS = {
    "odd_clock_divider": (
        "Module name:\n    freq_divbyodd\n"
        "A frequency divider that divides the input clock by odd numbers.\n"
        "Input ports:\n clk: Input clock.\n rst_n: Active low reset.\n"
        "Output ports:\n clk_div: Divided clock output.\n"
        "The parameter NUM_DIV defaults to 5.\n"),
    "frac_clock_divider_3p5": (
        "Module name:\n    freq_divbyfrac\n"
        "A fractional frequency divider (3.5x) using the double-edge clocking "
        "technique. MUL2_DIV_CLK = 7.\n"
        "Input ports:\n clk: Input clock.\n rst_n: Active low reset.\n"
        "Output ports:\n clk_div: Fractionally divided clock output.\n"),
    "pulse_detect_0to1to0": (
        "Module name:\n    pulse_detect\n"
        "Pulse detection: when data_in changes from 0 to 1 to 0 this is a pulse.\n"
        "Input ports:\n clk: Clock.\n rst_n: Reset.\n data_in: One-bit input.\n"
        "Output ports:\n data_out: pulse indicator.\n"),
    "combinational_long_divider": (
        "Module name:\n    div_16bit\n"
        "Implement a 16-bit divider in combinational logic. The dividend is "
        "16-bit and the divisor is 8-bit.\n"
        "Input ports:\n A: 16-bit dividend.\n B: 8-bit divisor.\n"
        "Output ports:\n result: 16-bit quotient.\n odd: 16-bit remainder.\n"),
    "pipelined_unsigned_multiplier_8": (
        "Module name:\n    multi_pipe_8bit\n"
        "Implement an unsigned 8bit multiplier based on pipelining processing.\n"
        "Input ports:\n clk: Clock.\n rst_n: Active-low reset.\n"
        " mul_en_in: Input enable.\n mul_a: 8-bit multiplicand.\n"
        " mul_b: 8-bit multiplier.\n"
        "Output ports:\n mul_en_out: Output enable.\n mul_out: 16-bit product.\n"),
    "barrel_shifter_right_8": (
        "Module name:\n    barrel_shifter\n"
        "A barrel shifter for shifting bits efficiently, controlled by ctrl.\n"
        "Input ports:\n in: 8-bit input to be shifted.\n ctrl: 3-bit shift amount.\n"
        "Output ports:\n out: 8-bit shifted output.\n"),
    "triangle_wave_generator_5": (
        "Module name:\n    signal_generator\n"
        "Implement a Triangle Wave generator whose 5-bit wave cycles between "
        "0 and 31.\n"
        "Input ports:\n clk: Clock.\n rst_n: Active-low reset.\n"
        "Output ports:\n wave: 5-bit output waveform.\n"),
    "mealy_seq_detector_10011": (
        "Module name:\n    fsm\n"
        "Implement a Mealy FSM detection circuit that detects a single-bit input "
        "IN. When the input is 10011, output MATCH is 1.\n"
        "Input ports:\n IN: Input signal.\n CLK: Clock.\n RST: Reset.\n"
        "Output ports:\n MATCH: match indicator.\n"),
    "pipelined_ripple_adder_64": (
        "Module name:\n    adder_pipe_64bit\n"
        "Implement a 64-bit ripple carry adder with several registers to enable "
        "the pipeline stages.\n"
        "Input ports:\n clk: Clock.\n rst_n: Active low reset.\n i_en: Enable.\n"
        " adda: 64-bit A.\n addb: 64-bit B.\n"
        "Output ports:\n result: 65-bit sum.\n o_en: Output enable.\n"),
    "parallel_to_serial_4": (
        "Module name:\n    parallel2serial\n"
        "Implement a module for parallel-to-serial conversion, where every four "
        "input bits are converted to a serial one bit output (from MSB to LSB).\n"
        "Input ports:\n clk: Clock.\n rst_n: Active low reset.\n"
        " d: 4-bit parallel data input.\n"
        "Output ports:\n valid_out: Valid signal.\n dout: Serial output.\n"),
    # The remaining SIX shapes, added when an audit showed the inline population
    # was 10 of 16: every claim this module makes about "the canonical
    # descriptions" was measured on ten of the sixteen, and the dataset-backed
    # tests that would have covered the rest SKIP wherever the corpus is absent.
    "serial_to_parallel_8": (
        "Module name:\n    serial2parallel\n"
        "Implement a series-parallel conversion: eight serial input bits are\n"
        "assembled into one 8-bit word, from the most significant bit to the "
        "least.\n"
        "Input ports:\n clk: Clock.\n rst_n: Active low reset.\n"
        " din_serial: Serial input bit.\n din_valid: High when din_serial is "
        "valid.\n"
        "Output ports:\n dout_parallel: 8-bit assembled word.\n"
        " dout_valid: High when dout_parallel is complete.\n"),
    "traffic_light_fsm": (
        "Module name:\n    traffic_light\n"
        "Implement a traffic light controller with a pedestrian pass_request.\n"
        "Input ports:\n rst_n: Active low reset.\n clk: Clock.\n"
        " clock: Clock signal.\n pass_request: Pedestrian request.\n"
        "Output ports:\n red: Red lamp.\n yellow: Yellow lamp.\n"
        " green: Green lamp.\n"),
    "radix2_signed_divider": (
        "Module name:\n    radix2_div\n"
        "Implement a radix-2 divider that handles signed or unsigned operands\n"
        "according to the sign input.\n"
        "Input ports:\n clk: Clock.\n rst: Reset.\n sign: 1 for signed "
        "operands.\n"
        " dividend: 8-bit dividend.\n divisor: 8-bit divisor.\n"
        " opn_valid: Operands are valid.\n"
        "Output ports:\n res_valid: Result is valid.\n result: 16-bit "
        "result.\n"),
    "ieee754_single_multiplier": (
        "Module name:\n    float_multi\n"
        "Implement an IEEE 754 single-precision floating-point multiplier.\n"
        "Input ports:\n clk: Clock.\n rst: Reset.\n a: 32-bit operand.\n"
        " b: 32-bit operand.\n"
        "Output ports:\n z: 32-bit product.\n"),
    "async_gray_fifo": (
        "Module name:\n    asyn_fifo\n"
        "Implement an asynchronous FIFO whose pointers cross the clock domains\n"
        "as gray code.\n"
        "Input ports:\n wclk: Write clock.\n rclk: Read clock.\n"
        " wrstn: Write-domain active low reset.\n rrstn: Read-domain reset.\n"
        " winc: Write enable.\n rinc: Read enable.\n wdata: Write data.\n"
        "Output ports:\n wfull: FIFO full.\n rempty: FIFO empty.\n"
        " rdata: Read data.\n"),
    "lfsr4_xnor_left": (
        "Module name:\n    LFSR\n"
        "Implement a 4-bit linear feedback shift register. Each cycle the\n"
        "register is shifted left and the new low bit is out[3] xor out[2],\n"
        "inverted.\n"
        "Input ports:\n clk: Clock.\n rst: Reset.\n"
        "Output ports:\n out: 4-bit register value.\n"),
    "unsigned_iterative_restoring_divider": (
        "Design an unsigned iterative arithmetic unit using the restoring division "
        "algorithm.\n"
        "Module name: unsigned_ratio_unit\n"
        "Parameter WIDTH has a default value of 9.\n"
        "Input ports:\n"
        "clk: posedge clock.\n"
        "rst: active-low asynchronous reset.\n"
        "start: one-cycle request.\n"
        "dividend[WIDTH-1:0]: unsigned dividend.\n"
        "divisor[WIDTH-1:0]: unsigned divisor.\n"
        "Output ports:\n"
        "quotient[WIDTH-1:0]: unsigned quotient.\n"
        "remainder[WIDTH-1:0]: unsigned remainder.\n"
        "valid: one-cycle completion.\n"
        "Both operands are nonzero, and dividend is at least divisor.\n"
        "On reset all outputs clear. New inputs are accepted after the previous "
        "result.\n"
        "At each restoring iteration append the next dividend bit to the shifted "
        "partial remainder and subtract the divisor; a negative trial restores "
        "the shifted partial remainder and produces quotient bit zero.\n"
        "Completion takes WIDTH cycles for power-of-two WIDTH and WIDTH+1 cycles "
        "otherwise; no extra input-only cycle.\n"),
}

# Near-miss descriptions that MUST fail-closed to None (no template mis-fire).
_INLINE_NEG = [
    # even divider — same clk/rst_n/clk_div ports, but "even" not "odd"/"3.5".
    ("Module name:\n    freq_divbyeven\n"
     "A frequency divider that divides by even numbers.\n"
     "Input ports:\n clk\n rst_n\n Output ports:\n clk_div\n"),
    # a plain 8-bit adder — no matching shape.
    ("Module name:\n    adder_8bit\n"
     "An 8-bit adder.\n Input ports:\n a\n b\n Output ports:\n sum\n"),
    # right port names but wrong function word (a *multiplier*, not divider).
    ("Module name:\n    mul_16bit\n"
     "A 16-bit multiplier in combinational logic.\n"
     "Input ports:\n A: 16-bit.\n B: 8-bit.\n Output ports:\n result\n odd\n"),
    # a Mealy FSM with the fsm ports but a DIFFERENT pattern (1011, not 10011) —
    # the mealy_seq_detector_10011 shape must fail-closed on a foreign pattern.
    ("Module name:\n    fsm\n"
     "Implement a Mealy FSM that detects the input pattern 1011.\n"
     "Input ports:\n IN\n CLK\n RST\n Output ports:\n MATCH\n"),
    # the sibling sequence_detector — an FSM, but NOT module `fsm`, so no misfire.
    ("Module name:\n    sequence_detector\n"
     "A finite state machine detecting the 1001 sequence via states.\n"
     "Input ports:\n clk\n reset_n\n data_in\n Output ports:\n detected\n"),
    # a plain combinational 64-bit adder with the adda/addb ports but NO pipeline —
    # pipelined_ripple_adder_64 must fail-closed without the "pipeline" structure.
    ("Module name:\n    adder_pipe_64bit\n"
     "A 64-bit ripple carry adder, purely combinational.\n"
     "Input ports:\n clk\n rst_n\n i_en\n adda\n addb\n Output ports:\n result\n o_en\n"),
    # a pipelined adder of a DIFFERENT width (32-bit), wrong module name — no misfire.
    ("Module name:\n    adder_pipe_32bit\n"
     "A 32-bit pipelined ripple carry adder.\n"
     "Input ports:\n clk\n rst_n\n i_en\n adda\n addb\n Output ports:\n result\n o_en\n"),
    # right module name + ports for parallel2serial but NO parallel-to-serial
    # phrase — the detector must fail-closed rather than key on ports alone.
    ("Module name:\n    parallel2serial\n"
     "A generic 4-bit register block.\n"
     "Input ports:\n clk\n rst_n\n d\n Output ports:\n valid_out\n dout\n"),
]


@pytest.mark.parametrize("shape,desc", _INLINE_POS.items())
def test_inline_detect_positive(shape, desc):
    assert rcs.detect_shape(desc) == shape


@pytest.mark.parametrize("desc", _INLINE_NEG)
def test_inline_detect_negative_failclosed(desc):
    assert rcs.detect_shape(desc) is None


def test_module_name_extraction():
    assert rcs.module_name_of("Module name:\n    freq_divbyodd\n") == "freq_divbyodd"


def test_unsigned_divider_cli_emits_source_bound_contract(tmp_path):
    desc = tmp_path / "unsigned_ratio_unit_description.txt"
    desc.write_text(_INLINE_POS["unsigned_iterative_restoring_divider"])
    out = tmp_path / "unsigned_ratio_unit.sv"
    run = subprocess.run([sys.executable, str(PROG), "--from-desc", str(desc),
                          "--out", str(out)], capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    result = json.loads(run.stdout)
    assert result["shape"] == "unsigned_iterative_restoring_divider"
    assert result["module"] == "unsigned_ratio_unit"
    assert result["contract"]["domain"] == (
        "unsigned_nonzero_dividend_ge_divisor")
    assert result["contract"]["latency"] == {
        "power_of_two_cycles": "WIDTH", "otherwise_cycles": "WIDTH+1",
        "resolved_cycles": 10,
        "no_input_only_cycle": True}
    rtl = out.read_text()
    assert "module unsigned_ratio_unit" in rtl
    assert result["source_sha256"] in rtl
    occupied = subprocess.run(
        [sys.executable, str(PROG), "--from-desc", str(desc), "--out", str(out)],
        capture_output=True, text=True)
    assert occupied.returncode == 2
    assert json.loads(occupied.stdout)["verdict"] == "REFUSED"
    assert out.read_text() == rtl


def test_unsigned_divider_supports_renamed_ports_and_refuses_unsupported_domains():
    desc = _INLINE_POS["unsigned_iterative_restoring_divider"].replace(
        "unsigned_ratio_unit", "renamed_div").replace("clk:", "clock_i:") \
        .replace("rst:", "reset_n:").replace("start:", "go_i:") \
        .replace("dividend[", "numerator_i[").replace("divisor[", "denominator_i[") \
        .replace("quotient[", "quot_o[").replace("remainder[", "rem_o[") \
        .replace("valid:", "done_o:")
    assert rcs.detect_shape(desc) == "unsigned_iterative_restoring_divider"
    rtl = rcs.emit_rtl(rcs.detect_shape(desc), desc)
    for name in ("clock_i", "reset_n", "go_i", "numerator_i", "denominator_i",
                 "quot_o", "rem_o", "done_o"):
        assert name in rtl
    signed = desc.replace("unsigned iterative", "signed iterative") \
        .replace("unsigned dividend", "signed dividend") \
        .replace("unsigned divisor", "signed divisor")
    assert rcs.detect_shape(signed) is None
    assert rcs.route_to_ai_reason(signed)["kind"] == (
        "unsupported_unsigned_iterative_divider")
    zero = desc.replace("Both operands are nonzero, and dividend is at least divisor.",
                        "The divisor may be zero.")
    assert rcs.detect_shape(zero) is None
    assert rcs.route_to_ai_reason(zero)["kind"] == (
        "unsupported_unsigned_iterative_divider")


@pytest.mark.parametrize("domain", [
    # Exact R3 blocker: ordering does not ground divisor != 0.
    "The dividend is nonzero, and dividend is at least divisor.",
    # Relational-only and a one-operand claim remain insufficient.
    "The dividend is at least divisor.",
    "The dividend is nonzero; the divisor is unspecified.",
    # Modal and negated claims are not positive grounding.
    "The divisor may be nonzero, and dividend is at least divisor.",
    "Do not assume the divisor is nonzero; dividend is at least divisor.",
    "The divisor is not nonzero, and dividend is at least divisor.",
], ids=[
    "exact_dividend_only",
    "relational_only",
    "divisor_unspecified",
    "divisor_modal",
    "divisor_negated_assumption",
    "divisor_negated",
])
def test_unsigned_divider_requires_explicit_divisor_nonzero_grounding(domain):
    """The template may emit only with explicit divisor nonzero grounding."""
    desc = _INLINE_POS["unsigned_iterative_restoring_divider"].replace(
        "Both operands are nonzero, and dividend is at least divisor.", domain)
    reason = rcs.route_to_ai_reason(desc)
    assert rcs.detect_shape(desc) is None
    assert reason is not None and reason["kind"] == (
        "unsupported_unsigned_iterative_divider")
    assert "nonzero operand domain" in reason["unresolved"]


@pytest.mark.parametrize("domain", [
    "The divisor is nonzero, and dividend is at least divisor.",
    "The dividend is at least divisor, and the divisor is nonzero.",
    "Both operands are nonzero, and dividend is at least divisor.",
    "Both operands must be nonzero; dividend >= divisor.",
], ids=[
    "divisor_before_relation",
    "divisor_after_relation",
    "both_operands_are",
    "both_operands_must_be",
])
def test_unsigned_divider_accepts_explicit_divisor_or_both_operand_grounding(domain):
    """Positive grounding survives word order and both-operand wording."""
    desc = _INLINE_POS["unsigned_iterative_restoring_divider"].replace(
        "Both operands are nonzero, and dividend is at least divisor.", domain)
    assert rcs.detect_shape(desc) == "unsigned_iterative_restoring_divider"
    assert rcs.route_to_ai_reason(desc) is None


@pytest.mark.parametrize("domain,emit", [
    ('If the divisor is nonzero, the operation is supported.', False),
    ('For example, "the divisor is nonzero" describes one valid case.', False),
    ('The divisor is positive.', True),
], ids=["conditional", "quoted_example", "explicit_positive"])
def test_unsigned_divider_r4_exact_router_regression(domain, emit, tmp_path):
    desc = _INLINE_POS["unsigned_iterative_restoring_divider"].replace(
        "Both operands are nonzero, and dividend is at least divisor.",
        domain + "\nDividend is at least divisor.")
    expected = "unsigned_iterative_restoring_divider" if emit else None
    assert rcs.detect_shape(desc) == expected
    if not emit:
        assert "nonzero operand domain" in rcs.route_to_ai_reason(desc)["unresolved"]
    source, out = tmp_path / "description.txt", tmp_path / "unit.sv"
    source.write_text(desc)
    run = subprocess.run([sys.executable, str(PROG), "--from-desc", str(source),
                          "--out", str(out)], capture_output=True, text=True)
    assert run.returncode == (0 if emit else 2), run.stdout + run.stderr
    assert json.loads(run.stdout)["verdict"] == ("EMIT" if emit else "DEFER")
    assert out.exists() == emit


@pytest.mark.parametrize("subject,verb", [
    ("The divisor", "is"), ("The denominator", "is"),
    ("Both operands", "are"), ("The operands", "are"),
    ("Dividend and divisor", "are"), ("Divisor and dividend", "are"),
    ("The dividend and the divisor", "are"),
    ("Numerator and denominator", "are"),
    ("Both dividend and divisor", "are"),
    ("Denominator and numerator", "are"),
])
@pytest.mark.parametrize("predicate", [
    "{verb} nonzero", "must be nonzero", "shall be nonzero",
    "{verb} positive", "must be positive", "shall be positive",
    "{verb} greater than zero", "> 0",
    "{verb} non-zero",
])
def test_unsigned_divider_r4_unconditional_positive_forms(subject, verb, predicate):
    desc = _INLINE_POS["unsigned_iterative_restoring_divider"].replace(
        "Both operands are nonzero, and dividend is at least divisor.",
        subject + " " + predicate.format(verb=verb) + ".\nDividend >= divisor.")
    assert rcs.detect_shape(desc) == "unsigned_iterative_restoring_divider"
    assert rcs.route_to_ai_reason(desc) is None


@pytest.mark.parametrize("domain", [
    "If the divisor is nonzero, the operation is supported.",
    "When the divisor is nonzero, the operation is supported.",
    "Provided the divisor is positive, the operation is supported.",
    "Assuming the divisor is nonzero, the operation is supported.",
    "Suppose the divisor is positive.",
    "Unless the divisor is positive, the operation is unsupported.",
    "In case the divisor is nonzero, the operation is supported.",
    "The divisor is nonzero if a restricted mode is selected.",
    "For example, the divisor is nonzero.",
    "The divisor is nonzero as an example.",
    "The divisor is positive?",
    "E.g. the divisor is nonzero.",
    "Such as the divisor is nonzero.",
    "Consider that the divisor is positive.",
    '"The divisor is nonzero."', "'The divisor is nonzero.'",
    "“The divisor is positive.”", "`The divisor is nonzero.`",
    "```text\nThe divisor is nonzero.\n```",
    "~~~\nThe divisor is positive.\n~~~",
    "    The divisor is nonzero.",
    "> The divisor is nonzero.",
    "# The divisor is nonzero",
    "Domain: the divisor is nonzero.",
    "The divisor may be nonzero.", "The divisor might be positive.",
    "The divisor could be positive.", "The divisor can be nonzero.",
    "The divisor is not guaranteed nonzero.",
    "The divisor is not necessarily positive.",
    "There is no requirement that the divisor be positive.",
    "Do not assume the divisor is nonzero.",
    "The divisor is nonzero (not guaranteed).",
    "The divisor is positive, but this only describes a restricted case.",
    "Previously the divisor is nonzero.",
    "The divisor is nonzero, historically.",
    "The divisor is positive in a previous requirement.",
    "The divisor is positive, yet this is only an example.",
    "The old requirement says the divisor is nonzero.",
    "The divisor is nonzero; this rule is superseded.",
    "The dividend is positive.", "The dividend > 0.",
    "The quotient is nonzero.", "The remainder is positive.",
    "The divisor and quotient are nonzero.",
    "The quotient reports the divisor is positive.",
    "The quotient and the divisor is nonzero.",
    "If " + "a restricted mode is selected and " * 15 + "the divisor is positive.",
    "The dividend is nonzero, and dividend is at least divisor.",
])
def test_unsigned_divider_r4_context_and_role_scopes_defer(domain):
    desc = _INLINE_POS["unsigned_iterative_restoring_divider"].replace(
        "Both operands are nonzero, and dividend is at least divisor.",
        domain + "\nDividend is at least divisor.")
    assert rcs.detect_shape(desc) is None
    assert "nonzero operand domain" in rcs.route_to_ai_reason(desc)["unresolved"]


@pytest.mark.parametrize("allowance", [
    "The divisor may be zero.", "The denominator might be zero.",
    "The divisor could be zero.", "The divisor can be zero.",
    "The divisor is not guaranteed nonzero.",
    "The divisor is not necessarily positive.",
    "No requirement that divisor be positive.",
    "There is no requirement that the denominator be nonzero.",
    "Both operands may be zero.", "The dividend may be zero.",
    "The divisor is zero.", "The divisor is not positive.",
])
@pytest.mark.parametrize("position", ["before", "after"])
def test_unsigned_divider_r4_contradiction_overrides_separate_positive(allowance, position):
    positive = "The divisor is positive.\nDividend is at least divisor."
    domain = (allowance + "\n" + positive if position == "before"
              else positive + "\n" + allowance)
    desc = _INLINE_POS["unsigned_iterative_restoring_divider"].replace(
        "Both operands are nonzero, and dividend is at least divisor.", domain)
    assert rcs.detect_shape(desc) is None
    assert "nonzero operand domain" in rcs.route_to_ai_reason(desc)["unresolved"]


@pytest.mark.parametrize("domain", [
    "If the quotient is nonzero, it is retained.\nThe divisor is positive.",
    "The old quotation is superseded.\nThe divisor is positive.",
    "The divisor is positive.\nFor example, the quotient may be zero.",
    "For example, the quotient may be zero.\nThe divisor is positive.",
    "For example, the divisor is nonzero.\nThe divisor is positive.",
    '"The divisor is nonzero."\nThe divisor is positive.',
    "The divisor is\npositive.",
    "The divisor is positive!\nThe quotient may be zero.",
])
def test_unsigned_divider_r4_sentence_boundaries_preserve_positive(domain):
    desc = _INLINE_POS["unsigned_iterative_restoring_divider"].replace(
        "Both operands are nonzero, and dividend is at least divisor.",
        domain + "\nDividend is at least divisor.")
    assert rcs.detect_shape(desc) == "unsigned_iterative_restoring_divider"


@pytest.mark.parametrize("prefix", [
    'Note: "the divisor is nonzero".',
    "The divisor is nonzero.",
])
def test_unsigned_divider_r4_port_description_cannot_ground_domain(prefix):
    desc = _INLINE_POS["unsigned_iterative_restoring_divider"].replace(
        "Both operands are nonzero, and dividend is at least divisor.",
        "Dividend is at least divisor.").replace(
            "dividend[WIDTH-1:0]: unsigned dividend.",
            "dividend[WIDTH-1:0]: unsigned dividend. " + prefix)
    assert rcs.detect_shape(desc) is None


@pytest.mark.parametrize("allowance", [
    "The divisor may be zero.",
    "The divisor is not guaranteed nonzero.",
    "No requirement that divisor be positive.",
])
def test_unsigned_divider_r4_port_allowance_overrides_positive(allowance):
    desc = _INLINE_POS["unsigned_iterative_restoring_divider"].replace(
        "divisor[WIDTH-1:0]: unsigned divisor.",
        "divisor[WIDTH-1:0]: unsigned divisor. " + allowance)
    assert rcs.detect_shape(desc) is None
    assert "nonzero operand domain" in rcs.route_to_ai_reason(desc)["unresolved"]


@pytest.mark.parametrize("ordering", [
    "If dividend is at least divisor, the operation is supported.",
    'For example, "dividend >= divisor" describes one case.',
    "Do not assume dividend is at least divisor.",
    "Dividend may be at least divisor.",
])
def test_unsigned_divider_r4_ordering_must_be_unconditional(ordering):
    desc = _INLINE_POS["unsigned_iterative_restoring_divider"].replace(
        "Both operands are nonzero, and dividend is at least divisor.",
        "The divisor is positive.\n" + ordering)
    assert rcs.detect_shape(desc) is None
    assert "dividend >= divisor domain" in rcs.route_to_ai_reason(desc)["unresolved"]


def _r5_domain_description(domain):
    return _INLINE_POS["unsigned_iterative_restoring_divider"].replace(
        "Both operands are nonzero, and dividend is at least divisor.", domain)


def _r5_assert_publication(domain, emit, tmp_path):
    """Check the detector, publishing CLI and the existing normal consumer."""
    description = _r5_domain_description(domain)
    source, output = tmp_path / "description.txt", tmp_path / "unit.sv"
    source.write_text(description)
    run = subprocess.run([sys.executable, str(PROG), "--from-desc", str(source),
                          "--out", str(output)], capture_output=True, text=True)
    response = json.loads(run.stdout)
    assert response["verdict"] == ("EMIT" if emit else "DEFER")
    assert run.returncode == (0 if emit else 2), run.stdout + run.stderr
    assert output.exists() == emit
    if not emit:
        assert response["defer_reason"]["unresolved"]
    observed = rcs.detect_shape(description)
    assert observed == ("unsigned_iterative_restoring_divider" if emit else None)
    reason = rcs.route_to_ai_reason(description)
    assert (reason is None) == emit
    import design_one_shot_runner as runner
    project = tmp_path / "project"
    input_file = project / "phase1/input_doc/design_description.txt"
    input_file.parent.mkdir(parents=True)
    input_file.write_text(description)
    result = runner._try_canonical_primitive_rtl(project, time.time())
    files = list(project.glob("phase2/stage1/rtl/*.v"))
    assert bool(files) == emit
    assert (result is not None and result.status == "PASS") == emit


@pytest.mark.parametrize("withdrawal", [
    "That statement does not apply to this design.",
    "This guarantee is waived.",
    "The preceding requirement is obsolete.",
    "That requirement was removed.",
    "This is optional, not an input requirement.",
    "This property belongs to the reference model; the target accepts zero.",
    "Zero is also permitted as a divisor.",
    "A divisor of zero is a valid input.",
], ids=["frozen_retraction", "waived", "obsolete", "removed", "optional",
        "owner_reassigned", "zero_permitted", "zero_valid"])
def test_unsigned_divider_r5_resolves_target_retractions(withdrawal, tmp_path):
    _r5_assert_publication("The divisor is nonzero. " + withdrawal +
                           " Dividend is at least divisor.", False, tmp_path)


@pytest.mark.parametrize("withdrawal", [
    "That ordering constraint does not apply to this design.",
    "Dividend may be less than divisor.",
    "Dividend is less than divisor.",
])
def test_unsigned_divider_r5_resolves_ordering_retractions(withdrawal, tmp_path):
    _r5_assert_publication("The divisor is nonzero. Dividend is at least divisor. "
                           + withdrawal, False, tmp_path)


@pytest.mark.parametrize("assertion", [
    "The divisor is nonzero in the reference model; this design permits zero.",
    "The divisor is nonzero in the reference model. This design permits zero.",
    "The divisor is positive in the comparison implementation; this unit accepts zero.",
    "The divisor is nonzero on the diagnostic channel; functional requests permit zero.",
    "The divisor is nonzero only in diagnostic mode; ordinary requests permit zero.",
    "The divisor is nonzero as long as the optional guard is enabled.",
    "The divisor is nonzero (a sample trace only).",
    "The divisor is nonzero (only in diagnostic mode).",
    "The divisor is nonzero, probably.",
    "The divisor is nonzero, presumably.",
    "The divisor is nonzero, according to the obsolete reference.",
    "The divisor is nonzero; this guarantee is waived.",
    "The divisor is nonzero; this constraint is optional.",
])
def test_unsigned_divider_r5_requires_target_proposition_owner(assertion, tmp_path):
    _r5_assert_publication(assertion + " Dividend is at least divisor.", False, tmp_path)


@pytest.mark.parametrize("heading", [
    "Examples:", "Reference model only:", "Hypothetical mode:",
    "If an optional guard is installed:", "Is the following true?",
    "## Examples", "## Reference model", "## Hypothetical mode",
    "## Examples\n### Input constraints",
    "## Reference model\n### Input constraints",
    "## Examples\nInput constraints:",
])
@pytest.mark.parametrize("marker", ["-", "*", "1."])
def test_unsigned_divider_r5_carries_list_and_section_framing(heading, marker, tmp_path):
    _r5_assert_publication(heading + "\n" + marker + " The divisor is nonzero.\n"
                           + marker + " Dividend is at least divisor.", False, tmp_path)


@pytest.mark.parametrize("other", [
    'A rejected example says "the divisor may be zero".',
    'The reference manual says "the denominator may be zero"; that refers to another design.',
    'A rejected example says “the divisor may be zero”.',
    'A rejected example says «the divisor may be zero».',
    'A rejected example says ‘the divisor may be zero’.',
    "The reference model's divisor may be zero.",
    "The divisor may be zero in the reference model.",
    "For example, the divisor may be zero in another design.",
    "If the divisor were zero, the request would be invalid.",
    "The divisor cannot be zero.",
    "The output is not saturated.",
    "\n> That statement does not apply to this design.\n",
    "\n## Rejected examples\n- That statement does not apply to this design.\n## Target constraints\n",
    "The divisor may be zero in another design. That statement is obsolete.",
])
def test_unsigned_divider_r5_scopes_contradictory_evidence(other, tmp_path):
    _r5_assert_publication("The divisor is nonzero. " + other +
                           " Dividend is at least divisor.", True, tmp_path)


@pytest.mark.parametrize("allowance", [
    "The divisor may be zero.", "The denominator is zero.",
    "Zero is permitted as a divisor.", "A divisor of zero is a valid input.",
])
def test_unsigned_divider_r5_keeps_target_zero_allowance_refusal(allowance, tmp_path):
    _r5_assert_publication("The divisor is nonzero. " + allowance +
                           " Dividend is at least divisor.", False, tmp_path)


@pytest.mark.parametrize("domain", [
    "The divisor is nonzero. The output is not saturated. Dividend is at least divisor.",
    "The divisor is nonzero, and the output is not saturated. Dividend is at least divisor.",
    "- The divisor is nonzero.\n- Dividend is at least divisor.",
    "* The divisor is nonzero.\n* Dividend is at least divisor.",
    "1. The divisor is nonzero.\n2. Dividend is at least divisor.",
    "Input constraints:\n- The divisor is nonzero.\n- Dividend is at least divisor.",
    "The divisor is nonzero (a required input constraint). Dividend is at least divisor.",
    "The divisor is nonzero in this design. Dividend is at least divisor.",
    "## Examples\n- The divisor may be zero.\n## Target input constraints\n"
    "- The divisor is nonzero.\n- Dividend is at least divisor.",
])
def test_unsigned_divider_r5_preserves_target_assertions(domain, tmp_path):
    _r5_assert_publication(domain, True, tmp_path)


def test_unsigned_divider_r5_port_zero_allowance_is_target_evidence(tmp_path):
    desc = _INLINE_POS["unsigned_iterative_restoring_divider"].replace(
        "divisor[WIDTH-1:0]: unsigned divisor.",
        "divisor[WIDTH-1:0]: unsigned divisor; zero is a permitted value.")
    assert rcs.detect_shape(desc) is None
    assert "nonzero operand domain" in rcs.route_to_ai_reason(desc)["unresolved"]


def test_unsigned_divider_r5_loads_by_path_outside_programs(tmp_path):
    source = tmp_path / "description.txt"
    source.write_text(_INLINE_POS["unsigned_iterative_restoring_divider"])
    script = (
        "import importlib.util, pathlib, sys\n"
        "spec = importlib.util.spec_from_file_location('independent_producer', sys.argv[1])\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = module\n"
        "spec.loader.exec_module(module)\n"
        "text = pathlib.Path(sys.argv[2]).read_text()\n"
        "assert module.detect_shape(text) == 'unsigned_iterative_restoring_divider'\n"
        "assert module.emit_rtl(module.detect_shape(text), text)\n")
    run = subprocess.run([sys.executable, "-I", "-c", script, str(PROG), str(source)],
                         cwd=tmp_path, capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr



@pytest.mark.parametrize("heading", [
    "Reference model only:", "Examples:", "If an optional guard is installed:",
], ids=["reference", "example", "conditional"])
@pytest.mark.parametrize("layout", ["blank", "nested", "deep_numbered", "nested_blank" ])
def test_unsigned_divider_r6_inherits_list_frames(heading, layout, tmp_path):
    if layout == "blank":
        domain = heading + "\n\n- The divisor is nonzero.\n\n- Dividend is at least divisor."
    elif layout == "deep_numbered":
        domain = "1. " + heading + "\n  1. Input constraints:\n    1. The divisor is nonzero.\n    2. Dividend is at least divisor."
    else:
        gap = "\n" if layout == "nested_blank" else ""
        domain = "- " + heading + "\n" + gap + "  - The divisor is nonzero.\n" + gap + "  - Dividend is at least divisor."
    _r5_assert_publication(domain, False, tmp_path)


@pytest.mark.parametrize("domain", [
    "- The divisor is nonzero.\n\n- Dividend is at least divisor.",
    "- Target constraints:\n\n  - The divisor is nonzero.\n  - Dividend is at least divisor.",
    "- Reference model only:\n  - The divisor may be zero.\n- Target constraints:\n  - The divisor is nonzero.\n  - Dividend is at least divisor.",
    "- Examples:\n  - If a guard is installed:\n    - The divisor may be zero.\n\n- The divisor is nonzero.\n- Dividend is at least divisor.",
    "Reference model only:\n\n- The divisor may be zero.\nTarget constraints:\n- The divisor is nonzero.\n- Dividend is at least divisor.",
    "Reference model only:\n\n- The divisor may be zero.\nThe divisor is nonzero. Dividend is at least divisor.",
], ids=["ordinary_blank", "target_parent", "sibling_target", "dedent_two_levels",
        "heading_switch", "paragraph_switch"])
def test_unsigned_divider_r6_closes_list_frames(domain, tmp_path):
    _r5_assert_publication(domain, True, tmp_path)


@pytest.mark.parametrize("withdrawal", [
    "The nonzero constraint does not apply to this design.",
    "This divisor requirement does not apply to this design.",
    "The denominator constraint is waived.",
    "The nonzero requirement was removed.",
    "The preceding requirement is cancelled.",
    "That requirement has been retracted.",
], ids=["named_nonzero", "named_divisor", "denominator", "named_removed",
        "preceding_cancelled", "retracted"])
def test_unsigned_divider_r6_retracts_nonzero_domain(withdrawal, tmp_path):
    _r5_assert_publication("The divisor is nonzero. " + withdrawal +
                           " Dividend is at least divisor.", False, tmp_path)


@pytest.mark.parametrize("withdrawal", [
    "The ordering constraint does not apply to this design.",
    "This ordering requirement is cancelled.",
    "The ordering constraint was removed.",
], ids=["named_ordering", "ordering_cancelled", "ordering_removed"])
def test_unsigned_divider_r6_retracts_ordering_domain(withdrawal, tmp_path):
    _r5_assert_publication("Dividend is at least divisor. The divisor is nonzero. "
                           + withdrawal, False, tmp_path)


@pytest.mark.parametrize("other", [
    "The output requirement is cancelled.",
    "The ordering constraint is not waived.",
    "The nonzero constraint does not apply to the reference model.",
    'A rejected example says "the nonzero constraint does not apply to this design".',
    "\n- Reference model only:\n  - The nonzero constraint is cancelled.\n",
], ids=["unrelated_domain", "other_domain_preserved", "external_owner", "quotation",
        "nested_external"])
def test_unsigned_divider_r6_scopes_named_withdrawals(other, tmp_path):
    _r5_assert_publication("The divisor is nonzero. Dividend is at least divisor. "
                           + other, True, tmp_path)


@pytest.mark.parametrize("preservation", [
    "That requirement is not waived.",
    "That requirement was not removed.",
    "That statement is not obsolete.",
    "That constraint is not optional.",
    "That statement has not been retracted.",
    "The preceding requirement is not cancelled.",
    "This divisor requirement has never been removed.",
    "The nonzero constraint is not waived.",
    "That statement still applies to this design.",
], ids=["not_waived", "not_removed", "not_obsolete", "not_optional", "not_retracted",
        "not_cancelled", "never_removed", "named_not_waived", "still_applies"])
def test_unsigned_divider_r6_preserves_negated_withdrawals(preservation, tmp_path):
    _r5_assert_publication("The divisor is nonzero. " + preservation +
                           " Dividend is at least divisor.", True, tmp_path)


@pytest.mark.parametrize("preservation", [
    "That ordering constraint is not waived.",
    "The ordering requirement was not removed.",
    "That ordering constraint has not been retracted.",
], ids=["ordering_not_waived", "ordering_not_removed", "ordering_not_retracted"])
def test_unsigned_divider_r6_preserves_negated_ordering_withdrawals(preservation, tmp_path):
    _r5_assert_publication("The divisor is nonzero. Dividend is at least divisor. "
                           + preservation, True, tmp_path)


def test_unsigned_divider_r6_preserved_referent_can_be_withdrawn(tmp_path):
    _r5_assert_publication("The divisor is nonzero. That requirement is not waived. "
                           "That requirement was removed. Dividend is at least divisor.",
                           False, tmp_path)



@pytest.mark.parametrize("heading", [
    "Reference model only:", "Examples:", "If an optional guard is installed:",
], ids=["reference", "example", "conditional"])
@pytest.mark.parametrize("layout", ["tight", "blank", "mixed", "deep", "tab"])
def test_unsigned_divider_r7_f1_nested_colon_scope(heading, layout, tmp_path):
    indent = "\t" if layout == "tab" else "  "
    gap = "\n" if layout == "blank" else ""
    child = indent + "Input constraints:\n"
    if layout == "deep":
        child += indent + "  Operand constraints:\n"
        indent += "  "
    first, second = ("1.", "*") if layout == "mixed" else ("-", "-")
    domain = (heading + "\n" + gap + child + gap + indent + first +
              " The divisor is nonzero.\n" + indent + second +
              " Dividend is at least divisor.")
    _r5_assert_publication(domain, False, tmp_path)


@pytest.mark.parametrize("heading", [
    "Reference model only:", "Examples:", "If an optional guard is installed:",
], ids=["reference", "example", "conditional"])
def test_unsigned_divider_r7_f1_dedent_target(heading, tmp_path):
    _r5_assert_publication(heading + "\n  Input constraints:\n"
                           "  - The divisor may be zero.\nTarget constraints:\n"
                           "- The divisor is nonzero.\n- Dividend is at least divisor.",
                           True, tmp_path)


@pytest.mark.parametrize("domain", [
    "Target constraints:\n  Operand constraints:\n  - The divisor is nonzero.\n  - Dividend is at least divisor.",
    "- Reference model only:\n  Input constraints:\n  - The divisor may be zero.\n- Target constraints:\n  Input constraints:\n  - The divisor is nonzero.\n  - Dividend is at least divisor.",
    "Reference model only:\n  Input constraints:\n  - The divisor may be zero.\nThe divisor is nonzero. Dividend is at least divisor.",
    'Reference model only:\n"Discarded note"\n\n  Input constraints:\n  - The divisor is nonzero.\n  - Dividend is at least divisor.',
], ids=["target_child", "bullet_sibling", "paragraph_boundary", "quote_blank"])
def test_unsigned_divider_r7_f1_scope_boundaries(domain, tmp_path):
    _r5_assert_publication(domain, not domain.startswith('Reference model only:\n"'), tmp_path)


@pytest.mark.parametrize("withdrawal", [
    "The nonzero constraints are waived.",
    "The divisor requirements are removed.",
    "The denominator constraints are retracted.",
    "The ordering requirements are cancelled.",
    "These requirements are removed.",
    "Those constraints no longer apply to this design.",
    "THE NONZERO CONSTRAINTS ARE WAIVED.",
    "The preceding requirements were removed.",
    "These properties have been retracted.",
    "Those statements are obsolete.",
    "The non-zero guarantees are waived.",
    "These rules are optional.",
], ids=["nonzero", "divisor", "denominator", "ordering", "these", "those",
        "uppercase", "preceding", "properties", "statements", "hyphen", "rules"])
def test_unsigned_divider_r7_f2_plural_withdrawals(withdrawal, tmp_path):
    _r5_assert_publication("The divisor is nonzero. Dividend is at least divisor. "
                           + withdrawal, False, tmp_path)


@pytest.mark.parametrize("other", [
    "The nonzero constraints are not waived.",
    "The ordering requirements were not removed.",
    "These requirements are not optional.",
    "Those constraints have never been retracted.",
    "The output requirements are waived.",
    "The nonzero constraints are waived in the reference model.",
    'A discarded example says "these requirements were removed".',
    "If a diagnostic guard is installed, these requirements are removed.",
], ids=["not_waived", "not_removed", "not_optional", "never", "unrelated",
        "external", "quoted", "conditional"])
def test_unsigned_divider_r7_f2_plural_preservation(other, tmp_path):
    _r5_assert_publication("The divisor is nonzero. Dividend is at least divisor. "
                           + other, True, tmp_path)


@pytest.mark.parametrize("withdrawal,emit", [
    ("The nonzero requirement was not waived but was removed.", False),
    ("The ordering constraint is not obsolete but is cancelled.", False),
    ("That requirement is not waived but was removed.", False),
    ("Those requirements are not waived but were removed.", False),
    ("The nonzero requirement was not waived, but was removed.", False),
    ("The nonzero requirement was not waived yet was obsolete.", False),
    ("The nonzero requirement was not waived and was removed.", False),
    ("The nonzero requirement was waived but was not removed.", False),
    ("The ordering requirement is not optional but no longer applies to this design.", False),
    ("The nonzero requirement was not waived but was not removed.", True),
    ("The nonzero requirement was not waived, and was not removed.", True),
    ("The nonzero requirement was not waived but the output requirements were removed.", True),
    ("The nonzero requirement was not waived but was removed in the reference model.", True),
    ('A discarded example says "the nonzero requirement was not waived but was removed".', True),
    ("If a diagnostic guard is installed, the requirement is not waived but is removed.", True),
], ids=["removed", "cancelled", "unnamed", "plural", "comma", "yet", "and",
        "first_positive", "no_longer", "both_denied", "comma_denied", "other_subject",
        "external", "quoted", "conditional"])
def test_unsigned_divider_r7_f3_mixed_predicates(withdrawal, emit, tmp_path):
    _r5_assert_publication("The divisor is nonzero. Dividend is at least divisor. "
                           + withdrawal, emit, tmp_path)


_R7_INTERFACE_CASES = [
    ("neutral", {"dividend": "numerator_i"}, 3),
    ("review_dividend", {"dividend": "dividend_reg"}, 3),
    ("review_divisor", {"divisor": "divisor_reg"}, 3),
    ("review_quotient", {"quotient": "quotient_reg"}, 3),
    ("review_valid", {"valid": "busy"}, 3),
    ("review_start", {"start": "finish_pending"}, 3),
] + [("internal_" + name, {"dividend": name}, 3) for name in (
    "COUNT_WIDTH", "EXTRA_FINAL_CYCLE", "dividend_reg", "divisor_reg",
    "quotient_reg", "partial_remainder", "count", "busy", "finish_pending",
    "shifted_remainder", "trial_ge_divisor", "iteration_remainder",
    "start_shifted_remainder", "start_ge_divisor", "start_remainder",
)] + [
    ("combined_suffix_chain", {
        "clk": "COUNT_WIDTH", "rst": "EXTRA_FINAL_CYCLE",
        "start": "finish_pending", "dividend": "dividend_reg",
        "divisor": "dividend_reg_2", "quotient": "quotient_reg",
        "remainder": "partial_remainder", "valid": "busy",
    }, 6),
    ("case_sensitive_control", {"dividend": "DIVIDEND_REG", "valid": "Busy"}, 8),
]


@pytest.mark.parametrize("label,renames,width", _R7_INTERFACE_CASES,
                         ids=[row[0] for row in _R7_INTERFACE_CASES])
def test_unsigned_divider_r7_f4_interface_compiles_and_behaves(label, renames, width, tmp_path):
    if shutil.which("iverilog") is None or shutil.which("vvp") is None:
        pytest.skip("NOT_VERIFIED: iverilog/vvp unavailable for interface evidence")
    description = _INLINE_POS["unsigned_iterative_restoring_divider"]
    for before, after in renames.items():
        description = re.sub(r"\b" + before + r"(?=\[|:)", after, description)
    source, rtl = tmp_path / "description.txt", tmp_path / "unit.v"
    source.write_text(description)
    run = subprocess.run([sys.executable, str(PROG), "--from-desc", str(source),
                          "--out", str(rtl)], capture_output=True, text=True)
    assert json.loads(run.stdout)["verdict"] == "EMIT"
    assert run.returncode == 0, run.stdout + run.stderr
    import design_one_shot_runner as runner
    project = tmp_path / "project"
    prompt = project / "phase1/input_doc/design_description.txt"
    prompt.parent.mkdir(parents=True)
    prompt.write_text(description)
    step = runner._try_canonical_primitive_rtl(project, time.time())
    assert step is not None and step.status == "PASS"
    consumer_rtl = list(project.glob("phase2/stage1/rtl/*.v"))
    assert len(consumer_rtl) == 1
    assert consumer_rtl[0].read_bytes() == rtl.read_bytes()
    vectors = _native_divider_vectors(width, exhaustive=width == 6)
    tb_text = _native_divider_tb(width, vectors)
    for before, after in renames.items():
        tb_text = tb_text.replace("." + before + "(", "." + after + "(")
    tb, sim = tmp_path / "tb.v", tmp_path / "sim"
    tb.write_text(tb_text)
    compile_run = subprocess.run(["iverilog", "-g2012", "-s", "tb", "-o", str(sim),
                                  str(rtl), str(tb)], capture_output=True, text=True)
    assert compile_run.returncode == 0, compile_run.stdout + compile_run.stderr
    native = subprocess.run(["vvp", str(sim)], capture_output=True, text=True, timeout=45)
    assert native.returncode == 0, native.stdout + native.stderr
    assert f"PASS width={width}" in native.stdout, native.stdout + native.stderr
    print(f"INTERFACE_NATIVE_PASS case={label} width={width} vectors={len(vectors)}")


@pytest.mark.parametrize("modal", ["may", "can", "could", "might"])
@pytest.mark.parametrize("synonyms", [False, True], ids=["roles", "synonyms"])
def test_unsigned_divider_r7_f5_modal_ordering_allowance(modal, synonyms, tmp_path):
    lhs, rhs = ("numerator", "denominator") if synonyms else ("dividend", "divisor")
    _r5_assert_publication("The divisor is nonzero. Dividend is at least divisor. "
                           f"The {lhs} {modal} be less than the {rhs}.", False, tmp_path)


@pytest.mark.parametrize("modal", ["could", "might"])
@pytest.mark.parametrize("scope", ["denied", "quoted", "reference", "conditional"])
def test_unsigned_divider_r7_f5_scoped_allowance(modal, scope, tmp_path):
    if scope == "denied":
        allowance = f"The dividend {modal} not be less than divisor."
    elif scope == "quoted":
        allowance = f'A discarded example says "the dividend {modal} be less than divisor".'
    elif scope == "reference":
        allowance = f"The dividend {modal} be less than divisor in the reference model."
    else:
        allowance = f"If a diagnostic guard is installed, dividend {modal} be less than divisor."
    _r5_assert_publication("The divisor is nonzero. Dividend is at least divisor. "
                           + allowance, True, tmp_path)


def _r2_unsigned_divider_variants():
    desc = _INLINE_POS["unsigned_iterative_restoring_divider"]
    return [
        ("valid_hold_until_request",
         desc.replace("valid: one-cycle completion.",
                      "valid: remains asserted until next request."),
         "one-cycle valid"),
        ("zero_divisor_uncertain",
         desc.replace(
             "Both operands are nonzero, and dividend is at least divisor.",
             "Do not assume nonzero; the divisor may be zero."),
         "zero divisor"),
        ("reset_not_active_low_async",
         desc.replace("rst: active-low asynchronous reset.",
                      "rst: reset is not active-low asynchronous."),
         "reset"),
        ("reset_active_low_not_async",
         desc.replace("rst: active-low asynchronous reset.",
                      "rst: active-low reset is not asynchronous."),
         "reset"),
        ("request_not_one_cycle",
         desc.replace("start: one-cycle request.",
                      "start: request is not one-cycle."),
         "one-cycle request"),
        ("latency_width_plus_two",
         desc.replace("WIDTH+1 cycles otherwise", "WIDTH+2 cycles otherwise"),
         "latency"),
        ("unsigned_not_explicit",
         desc.replace("Design an unsigned iterative", "Design an iterative")
         .replace("unsigned dividend", "dividend")
         .replace("unsigned divisor", "divisor")
         .replace("unsigned quotient", "quotient")
         .replace("unsigned remainder", "remainder"),
         "explicit unsigned"),
        ("zero_divisor_exact_supplement",
         desc.replace(
             "Both operands are nonzero, and dividend is at least divisor.",
             "The dividend is nonzero, and the divisor may be zero."),
         "zero divisor"),
        ("conflicting_module_declarations",
         desc.replace("Module name: unsigned_ratio_unit",
                      "Module name: unsigned_ratio_unit\n"
                      "Module name: other_ratio_unit"),
         "conflicting module"),
        ("extra_enable_port",
         desc.replace("start: one-cycle request.",
                      "start: one-cycle request.\n"
                      "enable: one-cycle enable."),
         "unsupported extra"),
        ("vector_clock",
         desc.replace("clk: posedge clock.",
                      "clk[1:0]: posedge clock."),
         "clock control"),
        ("vector_reset",
         desc.replace("rst: active-low asynchronous reset.",
                      "rst[1:0]: active-low asynchronous reset."),
         "reset control"),
        ("vector_request",
         desc.replace("start: one-cycle request.",
                      "start[1:0]: one-cycle request."),
         "start control"),
        ("vector_valid",
         desc.replace("valid: one-cycle completion.",
                      "valid[1:0]: one-cycle completion."),
         "valid control"),
        ("lowercase_width_parameter",
         desc.replace("Parameter WIDTH", "Parameter width")
         .replace("[WIDTH-1:0]", "[width-1:0]"),
         "uppercase WIDTH"),
        ("one_operand_two_roles",
         desc.replace(
             "dividend[WIDTH-1:0]: unsigned dividend.\n"
             "divisor[WIDTH-1:0]: unsigned divisor.",
             "operand[WIDTH-1:0]: unsigned operand used as both dividend "
             "and divisor."),
         "distinct ports"),
    ]


@pytest.mark.parametrize("label,desc,needle", _r2_unsigned_divider_variants())
def test_unsigned_divider_r2_adversarial_contracts_defer(label, desc, needle):
    reason = rcs.route_to_ai_reason(desc)
    assert rcs.detect_shape(desc) is None, label
    assert reason is not None and reason["kind"] == (
        "unsupported_unsigned_iterative_divider"), label
    assert any(needle.lower() in item.lower()
               for item in reason["unresolved"]), (label, reason)


def _native_divider_vectors(width, exhaustive=False):
    """Independent legal operands and Python's mathematical / and % oracle."""
    limit = (1 << width) - 1
    if width <= 4 or exhaustive:
        pairs = [(a, b) for b in range(1, limit + 1)
                 for a in range(b, limit + 1)]
    else:
        edges = [(b, b) for b in (1, 2, limit // 2, limit - 1, limit)]
        edges += [(limit, 1), (limit, limit // 2), (limit - 1, 3)]
        # Deterministic bounded samples keep WIDTH=8/9/12 practical while
        # preserving the source-declared nonzero a>=b domain.
        samples = []
        state = 0x2851 + width
        for _ in range(32):
            state = (1103515245 * state + 12345) & 0x7FFFFFFF
            b = 1 + (state % limit)
            state = (1103515245 * state + 12345) & 0x7FFFFFFF
            a = b + (state % (limit - b + 1))
            samples.append((a, b))
        pairs = edges + samples
    return [(a, b, a // b, a % b) for a, b in pairs]


def _native_divider_tb(width, vectors):
    checks = "\n".join(
        f"        check_div({a}, {b}, {q}, {r});"
        for a, b, q, r in vectors)
    first_a, first_b, first_q, first_r = vectors[0]
    second_a, second_b, second_q, second_r = vectors[min(1, len(vectors) - 1)]
    reset_a = max(2, (1 << width) - 1)
    reset_b = 1
    return f'''`timescale 1ns/1ps
module tb;
    localparam integer WIDTH = {width};
    localparam integer EXPECTED_LATENCY = ((WIDTH & (WIDTH - 1)) == 0)
        ? WIDTH : WIDTH + 1;
    reg clk = 1'b0;
    always #5 clk = ~clk;
    reg rst = 1'b1;
    reg start = 1'b0;
    reg [WIDTH-1:0] dividend = '0;
    reg [WIDTH-1:0] divisor = '0;
    wire [WIDTH-1:0] quotient;
    wire [WIDTH-1:0] remainder;
    wire valid;
    integer failures = 0;
    integer checks = 0;

    unsigned_ratio_unit #(.WIDTH(WIDTH)) dut (
        .clk(clk), .rst(rst), .start(start), .dividend(dividend),
        .divisor(divisor), .quotient(quotient), .remainder(remainder),
        .valid(valid));

    task automatic fail(input [1023:0] why);
        begin
            failures = failures + 1;
            $display("FAIL width=%0d %0s", WIDTH, why);
        end
    endtask

    task automatic check_div(input integer a_i, input integer b_i,
                             input integer q_i, input integer r_i);
        integer cycles;
        begin
            @(negedge clk);
            dividend = a_i;
            divisor = b_i;
            start = 1'b1;
            @(posedge clk); #1;
            start = 1'b0;
            // The DUT must use its latched operands after the request edge.
            dividend = '0;
            divisor = '0;
            cycles = 1;
            while (!valid && cycles <= WIDTH + 2) begin
                @(posedge clk); #1;
                cycles = cycles + 1;
            end
            checks = checks + 1;
            if (!valid)
                fail("valid never asserted");
            else if (cycles != EXPECTED_LATENCY)
                begin
                    fail("latency mismatch");
                    $display("DETAIL width=%0d got=%0d expected=%0d",
                             WIDTH, cycles, EXPECTED_LATENCY);
                end
            else if (quotient !== q_i || remainder !== r_i)
                fail("independent quotient/remainder oracle mismatch");
            @(posedge clk); #1;
            if (valid)
                fail("valid was wider than one cycle");
        end
    endtask

    task automatic check_back_to_back(
        input integer a1_i, input integer b1_i, input integer q1_i,
        input integer r1_i, input integer a2_i, input integer b2_i,
        input integer q2_i, input integer r2_i);
        integer cycles;
        begin
            // The second request is presented on the first falling edge after
            // the first result pulse, so its rising edge is the earliest
            // legal acceptance edge for both latency conventions.
            @(negedge clk);
            dividend = a1_i;
            divisor = b1_i;
            start = 1'b1;
            @(posedge clk); #1;
            start = 1'b0;
            dividend = '0;
            divisor = '0;
            cycles = 1;
            while (!valid && cycles <= WIDTH + 2) begin
                @(posedge clk); #1;
                cycles = cycles + 1;
            end
            checks = checks + 1;
            if (!valid || cycles != EXPECTED_LATENCY ||
                quotient !== q1_i || remainder !== r1_i)
                fail("back-to-back first result mismatch");

            @(negedge clk);
            dividend = a2_i;
            divisor = b2_i;
            start = 1'b1;
            @(posedge clk); #1;
            start = 1'b0;
            dividend = '0;
            divisor = '0;
            cycles = 1;
            while (!valid && cycles <= WIDTH + 2) begin
                @(posedge clk); #1;
                cycles = cycles + 1;
            end
            checks = checks + 1;
            if (!valid || cycles != EXPECTED_LATENCY ||
                quotient !== q2_i || remainder !== r2_i)
                fail("back-to-back second result mismatch");
            @(posedge clk); #1;
            if (valid)
                fail("back-to-back valid was wider than one cycle");
        end
    endtask

    initial begin
        #1;
        rst = 1'b0;
        #1;
        if (valid !== 1'b0 || quotient !== '0 || remainder !== '0)
            fail("initial active-low reset did not clear outputs");
        rst = 1'b1;

        // Assert reset between edges while a multi-cycle request is busy.
        if (WIDTH >= 2) begin
            @(negedge clk);
            dividend = {reset_a};
            divisor = {reset_b};
            start = 1'b1;
            @(posedge clk); #1;
            start = 1'b0;
            #2;
            rst = 1'b0;
            #1;
            if (valid !== 1'b0 || quotient !== '0 || remainder !== '0)
                fail("asynchronous reset did not clear busy result");
            @(negedge clk);
            rst = 1'b1;
        end

{checks}
        check_back_to_back({first_a}, {first_b}, {first_q}, {first_r},
                           {second_a}, {second_b}, {second_q}, {second_r});
        if (failures != 0)
            $fatal(1, "native divider checks failed: %0d", failures);
        $display("PASS width=%0d checks=%0d latency=%0d", WIDTH, checks,
                 EXPECTED_LATENCY);
        $finish;
    end
endmodule
'''


def _run_native_divider_case(root, rtl_text, width, vectors):
    rtl = root / f"divider_{width}.v"
    tb = root / f"tb_{width}.v"
    sim = root / f"sim_{width}"
    rtl.write_text(rtl_text)
    tb.write_text(_native_divider_tb(width, vectors))
    compile_run = subprocess.run(
        ["iverilog", "-g2012", "-s", "tb", "-o", str(sim),
         str(rtl), str(tb)], capture_output=True, text=True)
    assert compile_run.returncode == 0, compile_run.stdout + compile_run.stderr
    return subprocess.run(["vvp", str(sim)], capture_output=True, text=True,
                          timeout=45)


def test_unsigned_divider_native_oracle_latency_reset_back_to_back_and_reverse():
    """Compile the emitted RTL and use unchanged independent / and % checks.

    The final run uses the same generated testbench and oracle against a
    restoration mutation; a mutation that returns the pre-shift remainder must
    redden rather than receiving a source-string-only pass.
    """
    if shutil.which("iverilog") is None or shutil.which("vvp") is None:
        pytest.skip("NOT_MEASURED: iverilog/vvp unavailable in canonical image")
    desc = _INLINE_POS["unsigned_iterative_restoring_divider"]
    rtl = rcs.emit_rtl("unsigned_iterative_restoring_divider", desc)
    work = Path(tempfile.mkdtemp(prefix="issue2851-native-", dir="/tmp"))
    try:
        vectors_by_width = {
            width: _native_divider_vectors(width)
            for width in (1, 2, 3, 4, 8, 9, 12)
        }
        for width, vectors in vectors_by_width.items():
            run = _run_native_divider_case(work, rtl, width, vectors)
            assert run.returncode == 0, run.stdout + run.stderr
            assert f"PASS width={width}" in run.stdout, run.stdout + run.stderr
            print(f"NATIVE_PASS width={width} vectors={len(vectors)}")

        mutation = rtl.replace(
            "        : shifted_remainder;\n",
            "        : partial_remainder;\n", 1)
        assert mutation != rtl
        reverse = _run_native_divider_case(
            work, mutation, 3, vectors_by_width[3])
        assert reverse.returncode != 0, reverse.stdout + reverse.stderr
        assert "FAIL width=3" in reverse.stdout, reverse.stdout + reverse.stderr
        print("REVERSE_PASS mutation=restore_shifted_remainder caught=oracle")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _native_width_gap_case(width, include_restore_reverse=True):
    desc = _INLINE_POS["unsigned_iterative_restoring_divider"]
    rtl = rcs.emit_rtl("unsigned_iterative_restoring_divider", desc)
    vectors = _native_divider_vectors(width, exhaustive=True)
    work = Path(tempfile.mkdtemp(prefix=f"issue2851-native-w{width}-", dir="/tmp"))
    try:
        run = _run_native_divider_case(work, rtl, width, vectors)
        assert run.returncode == 0, run.stdout + run.stderr
        assert f"PASS width={width}" in run.stdout, run.stdout + run.stderr
        print(f"NATIVE_PASS width={width} vectors={len(vectors)} latched_operands=PASS")

        if include_restore_reverse:
            restore = rtl.replace(
                "        : shifted_remainder;\n",
                "        : partial_remainder;\n", 1)
            assert restore != rtl
            reverse_restore = _run_native_divider_case(
                work, restore, width, vectors)
            assert reverse_restore.returncode != 0
            assert "FAIL width=" + str(width) in reverse_restore.stdout
            print(f"REVERSE_PASS width={width} mutation=restore_shifted_remainder")

        # Reverse the request-edge trial into a real input-only cycle. The
        # internal operands are still latched, but every legal result arrives
        # one edge later, so the unchanged latency/oracle assertions must redden.
        input_only = rtl
        input_only = input_only.replace(
            "if (count == WIDTH - 2) begin",
            "if (count == WIDTH - 1) begin", 1)
        input_only = input_only.replace(
            "dividend_reg      <= dividend << 1;",
            "dividend_reg      <= dividend;", 1)
        input_only = input_only.replace(
            "quotient_reg      <= start_ge_divisor;",
            "quotient_reg      <= {WIDTH{1'b0}};", 1)
        input_only = input_only.replace(
            "partial_remainder <= start_remainder;",
            "partial_remainder <= {(WIDTH + 1){1'b0}};", 1)
        assert input_only != rtl
        reverse_input = _run_native_divider_case(
            work, input_only, width, vectors)
        assert reverse_input.returncode != 0
        assert "latency mismatch" in reverse_input.stdout
        print(f"REVERSE_PASS width={width} mutation=input_only_cycle")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_unsigned_divider_native_width6_latched_and_reverse_gap():
    _native_width_gap_case(6)


def test_unsigned_divider_native_width8_exhaustive_latched_and_reverse_gap():
    _native_width_gap_case(8, include_restore_reverse=False)


def test_parallel2serial_dout_is_combinational():
    """The load-bearing property: the parallel2serial template must drive dout
    with a CONTINUOUS assign (combinational MSB of the shift register), never a
    registered `dout <=`. Registering it delays every serial bit one cycle — the
    exact r8 failure this shape was added to fold in. This test FAILS if a future
    edit registers dout."""
    rtl = rcs.emit_rtl("parallel_to_serial_4")
    # strip // comments so the explanatory prose can't satisfy/trip the checks
    code = "\n".join(re.sub(r"//.*$", "", ln) for ln in rtl.splitlines())
    assert re.search(r"assign\s+dout\s*=", code), "dout must be a continuous assign"
    assert not re.search(r"\bdout\s*<=", code), "dout must NOT be registered"
    # dout is a plain wire output (not `output reg`)
    assert re.search(r"output\s+dout\b", code), "dout must be a wire output, not reg"


# ============================================================================
# Runner-integration: the step_rtl_gen hook _try_canonical_primitive_rtl must
# fire on a project whose phase1/input_doc description states a canonical shape,
# emit RTL to phase2/stage1/rtl/, and DEFER (None) on a non-matching project.
# Self-contained (inline description); no external dataset.
# ============================================================================
def _load_runner():
    sys.path.insert(0, str(PROGRAMS))
    import design_one_shot_runner as mod  # noqa: E402
    return mod


def _mk_project(tmp_path, desc_text):
    idoc = tmp_path / "phase1" / "input_doc"
    idoc.mkdir(parents=True)
    (idoc / "design_description.txt").write_text(desc_text)
    return tmp_path


def test_hook_fires_and_emits(tmp_path):
    R = _load_runner()
    proj = _mk_project(tmp_path, _INLINE_POS["pulse_detect_0to1to0"])
    res = R._try_canonical_primitive_rtl(proj, 0.0)
    assert res is not None and res.status == "PASS"
    assert res.extras.get("deterministic_generator") == "canonical_primitive_synth"
    assert res.extras.get("shape") == "pulse_detect_0to1to0"
    emitted = list((proj / "phase2" / "stage1" / "rtl").glob("*.v"))
    assert [p.name for p in emitted] == ["pulse_detect.v"]
    assert "module pulse_detect" in emitted[0].read_text()


def test_hook_defers_on_nonmatching(tmp_path):
    R = _load_runner()
    proj = _mk_project(tmp_path, _INLINE_NEG[1])   # a plain 8-bit adder
    assert R._try_canonical_primitive_rtl(proj, 0.0) is None


def test_hook_author_guard_never_overwrites(tmp_path):
    R = _load_runner()
    proj = _mk_project(tmp_path, _INLINE_POS["pulse_detect_0to1to0"])
    rtl_dir = proj / "phase2" / "stage1" / "rtl"
    rtl_dir.mkdir(parents=True)
    (rtl_dir / "existing.v").write_text("module existing(); endmodule\n")
    # RTL already present → the guard must DEFER (never clobber the design's own).
    assert R._try_canonical_primitive_rtl(proj, 0.0) is None


def test_unsigned_divider_normal_consumer_emits_and_preserves_occupied_output(tmp_path):
    R = _load_runner()
    proj = _mk_project(
        tmp_path / "project", _INLINE_POS["unsigned_iterative_restoring_divider"])
    res = R._try_canonical_primitive_rtl(proj, 0.0)
    assert res is not None and res.status == "PASS"
    output = proj / "phase2" / "stage1" / "rtl" / "unsigned_ratio_unit.v"
    assert output.is_file()
    before = output.read_text()
    assert "module unsigned_ratio_unit" in before
    assert R._try_canonical_primitive_rtl(proj, 0.0) is None
    assert output.read_text() == before


@pytest.mark.parametrize("mode", ["project", "direct"])
@pytest.mark.parametrize("occupied", ["file", "symlink", "dangling_symlink"])
def test_unsigned_divider_cli_preserves_occupied_output(tmp_path, mode, occupied):
    desc = _INLINE_POS["unsigned_iterative_restoring_divider"]
    proj = _mk_project(tmp_path / "project", desc)
    # Use the explicit INPUT location read by the existing project CLI.
    input_dir = proj / "input"
    input_dir.mkdir()
    source = input_dir / "design_description.txt"
    source.write_text(desc)
    output = proj / "phase2" / "stage1" / "rtl" / "unsigned_ratio_unit.v"
    output.parent.mkdir(parents=True)
    author_bytes = b"// author's supplied RTL\nmodule authored; endmodule\n"
    target = tmp_path / "author.v"
    if occupied == "file":
        output.write_bytes(author_bytes)
    else:
        if occupied == "symlink":
            target.write_bytes(author_bytes)
        output.symlink_to(target)
    link_before = os.readlink(output) if output.is_symlink() else None
    argv = ([str(proj), "--emit"] if mode == "project" else
            ["--from-desc", str(source), "--out", str(output)])
    run = subprocess.run([sys.executable, str(PROG), *argv],
                         capture_output=True, text=True)
    result = json.loads(run.stdout)
    preserved = (output.read_bytes() == author_bytes if occupied == "file"
                 else output.is_symlink() and os.readlink(output) == link_before
                 and (target.read_bytes() == author_bytes if occupied == "symlink"
                      else not target.exists()))
    print(json.dumps({"id": f"occupied_{mode}_{occupied}", "rc": run.returncode,
                      "verdict": result["verdict"], "preserved": preserved}))
    assert run.returncode == 2, run.stdout + run.stderr
    assert result["verdict"] == "REFUSED"
    assert result["reason"] == "occupied output preserved"
    assert preserved


def test_unsigned_divider_project_cli_emits_fresh_source_bound_output(tmp_path):
    desc = _INLINE_POS["unsigned_iterative_restoring_divider"]
    proj = _mk_project(tmp_path / "project", desc)
    input_dir = proj / "input"
    input_dir.mkdir()
    (input_dir / "design_description.txt").write_text(desc)
    run = subprocess.run([sys.executable, str(PROG), str(proj), "--emit"],
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    result = json.loads(run.stdout)
    output = proj / "phase2" / "stage1" / "rtl" / "unsigned_ratio_unit.v"
    assert result["verdict"] == "EMIT"
    assert result["written"] == str(output)
    assert output.read_text() == rcs.emit_rtl(rcs._UNSIGNED_DIVISION_SHAPE, desc)
    assert result["source_sha256"] in output.read_text()


@pytest.mark.parametrize("ancestor", ["phase2", "phase2/stage1/rtl"])
def test_unsigned_divider_project_cli_refuses_ancestor_symlink(
        tmp_path, ancestor, capsys):
    desc = _INLINE_POS["unsigned_iterative_restoring_divider"]
    proj = _mk_project(tmp_path / "project", desc)
    source = proj / "input" / "design_description.txt"
    source.parent.mkdir(parents=True)
    source.write_text(desc)
    external = tmp_path / "external"
    external.mkdir()
    link = proj / ancestor
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(external, target_is_directory=True)

    rc = rcs.main([str(proj), "--emit"])
    result = json.loads(capsys.readouterr().out)
    assert (rc, result["verdict"]) == (2, "REFUSED")
    assert "SOURCE_BOUND_PROJECT_REFUSED" in result["reason"]
    assert link.is_symlink() and link.resolve() == external
    assert not list(external.iterdir())


def test_unsigned_divider_project_cli_refuses_source_mutation_before_commit(
        tmp_path, monkeypatch, capsys):
    desc = _INLINE_POS["unsigned_iterative_restoring_divider"]
    proj = _mk_project(tmp_path / "project", desc)
    source = proj / "input" / "design_description.txt"
    source.parent.mkdir(parents=True)
    source.write_text(desc)
    changed = desc.replace("default value of 9", "default value of 12")
    original_emit = rcs.emit_rtl

    def mutate_live_source(shape, source_text):
        source.write_text(changed)
        return original_emit(shape, source_text)

    monkeypatch.setattr(rcs, "emit_rtl", mutate_live_source)
    rc = rcs.main([str(proj), "--emit"])
    result = json.loads(capsys.readouterr().out)
    assert (rc, result["verdict"]) == (2, "REFUSED")
    assert "SOURCE_BOUND_PROJECT_REFUSED" in result["reason"]
    assert "held project" in result["reason"]
    assert not list((proj / "phase2").rglob("*.v"))
    assert "default value of 12" in source.read_text()


# ============================================================================
# issue #2035, families F6 + F7, and the architecture question underneath them.
#
# MEASURED FIRST (base 764d6b3e5, host 8HD-8): two neutral input-only
# descriptions naming `Module name: barrel_shifter` with ports in/ctrl/out both
# returned EMIT rc=0 and were overwritten with the fixed three-mux hierarchy --
# one of them while the input said in plain words that no submodule and no
# generate block may be used. Detection was doing duty as topology selection,
# which is why an ALTERNATIVE-ARCHITECTURE CONTROL could not be satisfied at all.
#
# These tests pin, in order: the sixteen templates do not move; a stated
# structural directive withdraws a template and is NAMED for the AI author; and
# F6/F7 are COMPOSED from the acceptance contract the input states rather than
# from a seventeenth and eighteenth fixed template.
# ============================================================================

_ARCH_CONFLICT_DESC = (
    "Module name:\n    barrel_shifter\n"
    "A barrel shifter for shifting bits efficiently, controlled by ctrl.\n"
    "Input ports:\n in: 8-bit input to be shifted.\n ctrl: 3-bit shift amount.\n"
    "Output ports:\n out: 8-bit shifted output.\n"
    "This block is delivered as a single leaf cell: the implementation must not\n"
    "instantiate any submodule and must not use a generate block.\n")

_F6_DESC = (
    "Module name:\n    elastic_stage\n"
    "An elastic pipeline stage between a producer and a consumer.\n"
    "Input ports:\n clk: Clock signal.\n rst_n: Active low reset signal.\n"
    " up_data [7:0]: The 8-bit word offered by the producer.\n"
    " up_valid: High when the producer is offering up_data.\n"
    " dn_ready: High when the consumer can take a word this cycle.\n"
    "Output ports:\n up_ready: High when this stage can take a word.\n"
    " dn_data [7:0]: The word offered to the consumer.\n"
    " dn_valid: High when dn_data is being offered.\n"
    "Implementation:\n"
    "A transfer happens when that interface's valid and ready are both high.\n"
    "The stage must register the output and buffer one additional transfer so\n"
    "the producer is only stalled when no slot is free. No word that was not\n"
    "accepted may be captured, no accepted word may be lost under backpressure,\n"
    "and accepted words must leave in the order they arrived.\n")

# Same shape, same words, a DIFFERENT legitimate architecture: zero added
# latency. This is the alternative-architecture control for the contract layer.
_F6_STORAGE_SENTENCE = (
    "The stage must register the output and buffer one additional transfer so\n"
    "the producer is only stalled when no slot is free. ")
assert _F6_STORAGE_SENTENCE in _F6_DESC, "fixture drifted"

_F6_ALT_DESC = _F6_DESC.replace(
    _F6_STORAGE_SENTENCE,
    "This stage must not add latency: the offered word is seen by the consumer\n"
    "in the same cycle it is offered. ")

# Same shape with the storage policy simply NOT STATED: must route to AI by name.
_F6_UNSTATED_DESC = _F6_DESC.replace(_F6_STORAGE_SENTENCE, "")

_F7_DESC = (
    "Module name:\n    pulse_divider\n"
    "An event ratio divider.\n"
    "Input ports:\n clk: Clock signal.\n rst_n: Active low reset signal.\n"
    " in_data [7:0]: The 8-bit payload accompanying an input event.\n"
    " in_valid: Pulses high for one cycle on each input event.\n"
    "Output ports:\n out_data [7:0]: The forwarded payload.\n"
    " out_valid: Pulses high for one cycle on each output event.\n"
    "Implementation:\n"
    "The divider emits one output event for every 1 input events. Counting an\n"
    "input event and emitting the output event are the same cycle's work.\n")


def test_templates_are_byte_identical_to_their_own_text():
    """The contract layer must not re-author a working emitter: every template
    shape still emits exactly its `_TEMPLATES` entry, and `desc_text` is ignored
    for them. Compared by MEMBERSHIP of the shape-key set, not by count."""
    assert set(rcs._TEMPLATES) <= {k for k, _ in rcs._DETECTORS}
    for shape, text in rcs._TEMPLATES.items():
        assert rcs.emit_rtl(shape) == text
        assert rcs.emit_rtl(shape, _F6_DESC) == text


def test_stated_directive_withdraws_the_fixed_topology():
    """OLD WRONG BEHAVIOUR, on input-only material: this description matches the
    barrel-shifter detector while forbidding the very structure the template is
    built from, and used to be answered with that template anyway."""
    assert rcs._is_barrel_shifter(
        _ARCH_CONFLICT_DESC, rcs.module_name_of(_ARCH_CONFLICT_DESC),
        rcs._port_tokens(_ARCH_CONFLICT_DESC) - rcs._NOISE) is True
    conflict = rcs.architecture_conflict(
        _ARCH_CONFLICT_DESC, "barrel_shifter_right_8")
    assert conflict is not None
    assert conflict["polarity"] == "forbid"
    assert conflict["property"] in {"submodule_instantiation", "generate_block"}
    assert rcs.detect_shape(_ARCH_CONFLICT_DESC) is None


def test_withdrawn_topology_is_routed_to_ai_by_name():
    """No hidden DEFER: the program says WHICH stated directive it could not
    honour, so the AI author is handed the decision rather than guessing it."""
    why = rcs.route_to_ai_reason(_ARCH_CONFLICT_DESC)
    assert why is not None and why["route"] == "ai_author"
    assert why["kind"] == "architecture_conflict"
    assert why["shape_declined"] == "barrel_shifter_right_8"
    assert "must not" in why["stated"].lower()


@pytest.mark.parametrize("shape,desc", _INLINE_POS.items())
def test_canonical_descriptions_state_no_conflicting_directive(shape, desc):
    """The withdrawal must not fire on ordinary prose: every canonical
    description still detects its own shape, i.e. the DEFER population grew only
    for inputs that really do state a contradicting directive."""
    assert rcs.architecture_conflict(desc, shape) is None
    assert rcs.detect_shape(desc) == shape


def test_template_commitments_are_derived_from_the_template_text():
    """Commitments are read out of the emitted RTL, so a re-authored template
    cannot leave a hand-written second list behind, stale."""
    barrel = rcs.template_commitments("barrel_shifter_right_8")
    assert {"submodule_instantiation", "generate_block"} <= barrel
    assert "gray_code" not in barrel
    assert "gray_code" in rcs.template_commitments("async_gray_fifo")


def test_f6_contract_is_extracted_from_the_input():
    c = rcs.extract_handshake_contract(_F6_DESC)
    assert c is not None and c.kind == "elastic_stage"
    assert c.unresolved == []
    assert (c.up["valid"], c.up["ready"], c.up["data"]) == (
        "up_valid", "up_ready", "up_data")
    assert (c.down["valid"], c.down["ready"], c.down["data"]) == (
        "dn_valid", "dn_ready", "dn_data")
    assert c.width == 8 and c.storage == "skid" and c.ordering == "fifo"
    assert c.clock == "clk" and c.reset == "rst_n" and c.reset_active_low is True


def test_f6_is_composed_not_templated():
    """F6 is a consumer of the contract layer: no `_TPL_` exists for it, and the
    emitted module carries the INPUT's own module and port names."""
    assert "elastic_handshake_stage" not in rcs._TEMPLATES
    shape = rcs.detect_shape(_F6_DESC)
    assert shape == "elastic_handshake_stage"
    rtl = rcs.emit_rtl(shape, _F6_DESC)
    assert "module elastic_stage (" in rtl
    for port in ("up_valid", "up_ready", "up_data",
                 "dn_valid", "dn_ready", "dn_data"):
        assert port in rtl
    # acceptance-qualified storage: every capture is gated by an ACCEPTED
    # transfer, never by valid alone.
    assert "wire up_fire = up_valid && up_ready;" in rtl
    assert "if (up_fire) held_data <= up_data;" in rtl
    assert "skid_data  <= up_data;" in rtl
    assert "assign up_ready = !skid_valid;" in rtl


def test_f6_alternative_architecture_control_stays_green():
    """THE CONTROL. Same shape words, a legitimately different architecture
    (zero added latency). It must NOT be answered with the buffered stage."""
    c = rcs.extract_handshake_contract(_F6_ALT_DESC)
    assert c is not None and c.unresolved == [] and c.storage == "passthrough"
    rtl = rcs.emit_rtl(rcs.detect_shape(_F6_ALT_DESC), _F6_ALT_DESC)
    assert "assign dn_valid = up_valid;" in rtl
    assert "assign up_ready = dn_ready;" in rtl
    assert "always" not in rtl and "skid" not in rtl
    assert rtl != rcs.emit_rtl(rcs.detect_shape(_F6_DESC), _F6_DESC)


def test_f6_unstated_storage_is_routed_to_ai_by_name_not_guessed():
    assert rcs.detect_shape(_F6_UNSTATED_DESC) is None
    why = rcs.route_to_ai_reason(_F6_UNSTATED_DESC)
    assert why is not None and why["kind"] == "unstated_contract_fields"
    assert why["contract_kind"] == "elastic_stage"
    assert any("storage under backpressure" in u for u in why["unresolved"])


def test_f7_unit_ratio_consume_and_capture_are_simultaneous():
    """F7's defect is that consume and capture are exclusive, which drops every
    other input at a unit ratio. The composed emitter does both in one cycle."""
    c = rcs.extract_handshake_contract(_F7_DESC)
    assert c is not None and c.kind == "ratio_divider"
    assert c.ratio == 1 and c.unresolved == []
    assert "event_ratio_divider" not in rcs._TEMPLATES
    rtl = rcs.emit_rtl(rcs.detect_shape(_F7_DESC), _F7_DESC)
    assert "module pulse_divider #(" in rtl
    assert "wire consume  = in_valid;" in rtl
    assert "wire emit_now = in_valid && (count == RATIO - 1);" in rtl
    # the two are separate concurrent facts about the same cycle, never an
    # if/else between consuming and capturing
    assert "out_valid <= emit_now;" in rtl
    assert "if (in_valid) out_data <= in_data;" in rtl
    assert "else if" not in rtl.split("end else begin")[1]


def test_generated_scoreboards_come_from_the_same_contract():
    """The queue scoreboard (F6) and the ratio/latency count (F7) that #2035 asks
    for are composed from the contract fields, so a stage and its check cannot
    drift apart."""
    tb6 = rcs.emit_scoreboard_tb(rcs.extract_handshake_contract(_F6_DESC))
    assert "module tb_elastic_stage;" in tb6
    assert "if (up_valid && up_ready) begin q[wr]" in tb6
    assert "accepted transfers lost" in tb6
    tb7 = rcs.emit_scoreboard_tb(rcs.extract_handshake_contract(_F7_DESC))
    assert "module tb_pulse_divider;" in tb7
    assert "if (n_out != n_in / 1)" in tb7
    for tb in (tb6, tb7):
        assert tb.isascii()


def test_contract_shapes_refuse_to_emit_without_their_input():
    """A composed shape has no fixed answer to fall back on: asked to emit with
    no description, it raises rather than inventing a topology."""
    for shape in rcs._CONTRACT_SHAPES:
        with pytest.raises((ValueError, KeyError)):
            rcs.emit_rtl(shape)


@pytest.mark.parametrize("desc", _INLINE_NEG)
def test_contract_layer_does_not_mis_fire_on_near_misses(desc):
    """Fail-closed is preserved: the contract layer is consulted only when no
    template claimed the input, and it declines everything that states no
    handshake."""
    assert rcs.detect_shape(desc) is None


# ============================================================================
# The second half of the same exposure: a STATED BEHAVIOUR, not a stated
# architecture. An input that matches a shape's words while stating the other
# reset behaviour used to be answered with the template anyway.
#
# Measured on the base, independently of the program (a raw scan of the sixteen
# template texts): 11 templates reset ASYNCHRONOUSLY, 3 SYNCHRONOUSLY, and 2 are
# combinational -- and nothing compared that with what the description said.
# Both sides are decidable here, which is why this dimension is closed and the
# shift-direction one (see the lane's LAND.md) is not: the pole is read out of
# the template's own always-block and reset test, never out of its prose.
# ============================================================================

_ASYNC_TEMPLATES = {
    "async_gray_fifo", "frac_clock_divider_3p5", "mealy_seq_detector_10011",
    "odd_clock_divider", "parallel_to_serial_4", "pipelined_ripple_adder_64",
    "pipelined_unsigned_multiplier_8", "pulse_detect_0to1to0",
    "serial_to_parallel_8", "traffic_light_fsm", "triangle_wave_generator_5",
}
_SYNC_TEMPLATES = {
    "ieee754_single_multiplier", "lfsr4_xnor_left", "radix2_signed_divider",
}
_COMBINATIONAL_TEMPLATES = {
    "barrel_shifter_right_8", "combinational_long_divider",
}


def test_reset_poles_are_derived_from_the_template_code():
    """Compared by MEMBERSHIP against a partition established by reading the
    template texts directly, so a template that is re-authored to reset the
    other way moves its own commitment with it."""
    derived_async = {k for k in rcs._TEMPLATES
                     if "async_reset" in rcs.template_commitments(k)}
    derived_sync = {k for k in rcs._TEMPLATES
                    if "sync_reset" in rcs.template_commitments(k)}
    derived_none = {k for k in rcs._TEMPLATES
                    if not {"async_reset", "sync_reset"}
                    & rcs.template_commitments(k)}
    assert derived_async == _ASYNC_TEMPLATES
    assert derived_sync == _SYNC_TEMPLATES
    assert derived_none == _COMBINATIONAL_TEMPLATES
    assert "active_low_reset" in rcs.template_commitments("odd_clock_divider")
    assert "active_high_reset" in rcs.template_commitments("lfsr4_xnor_left")


def test_no_canonical_description_conflicts_with_its_own_template():
    """Zero false positives on the population that must never move, and NOT
    vacuous: several of these descriptions really do state a pole, and it
    agrees."""
    stated = 0
    for shape, desc in _INLINE_POS.items():
        poles = rcs.extract_stated_reset_poles(desc)
        stated += bool(poles)
        assert rcs.architecture_conflict(desc, shape) is None, shape
        assert rcs.detect_shape(desc) == shape
    assert stated >= 6


def _flip_reset_pole(desc, pole):
    """Rewrite a description to state the OPPOSITE pole of `pole`."""
    if pole == "active_low_reset":
        return re.sub(r"[Aa]ctive[- ]low", "Active high", desc)
    if pole == "active_high_reset":
        flipped = re.sub(r"[Aa]ctive[- ]high", "Active low", desc)
        return flipped if flipped != desc else desc + "rst: Active low reset.\n"
    if pole == "async_reset":
        return desc + "The reset is a synchronous reset.\n"
    return desc + "The reset is an asynchronous reset.\n"


_POLE_CASES = [(shape, pole)
               for shape, desc in _INLINE_POS.items()
               if shape in rcs._TEMPLATES
               for pole in sorted(p for p in rcs.template_commitments(shape)
                                  if p.endswith("_reset"))
               if _flip_reset_pole(desc, pole) != desc]


@pytest.mark.parametrize("shape,pole", _POLE_CASES)
def test_stating_the_opposite_reset_pole_withdraws_the_template(shape, pole):
    """The other direction, per shape: state the pole the template does NOT
    implement and the template is withdrawn and the pole is NAMED."""
    flipped = _flip_reset_pole(_INLINE_POS[shape], pole)
    conflict = rcs.architecture_conflict(flipped, shape)
    assert conflict is not None and conflict["polarity"] == "stated"
    assert conflict["property"] == rcs._OPPOSITE_POLE[pole]
    assert rcs.detect_shape(flipped) is None
    why = rcs.route_to_ai_reason(flipped)
    assert why is not None and why["property"] == rcs._OPPOSITE_POLE[pole]


def test_a_description_stating_both_poles_records_neither():
    """Contradictory input is not a licence to pick: when both poles of a pair
    are stated the program records no pole and keeps its previous behaviour."""
    both = (_INLINE_POS["odd_clock_divider"]
            + "rst_n: Active high reset.\n")
    assert "active low" in both.lower() and "active high" in both.lower()
    assert rcs.extract_stated_reset_poles(both) == set()
    assert rcs.detect_shape(both) == "odd_clock_divider"


def test_a_pole_said_about_another_signal_is_not_a_reset_statement():
    """`active low` on a non-reset pin must not be read as a reset statement."""
    desc = (_INLINE_POS["odd_clock_divider"]
            + " enable_n: An active high enable that gates the divider.\n")
    assert rcs.extract_stated_reset_poles(desc) == {"active_low_reset"}
    assert rcs.detect_shape(desc) == "odd_clock_divider"


# ============================================================================
# The composed check has to SHIP, or a human has to remember to run it. When the
# runner composes a contract shape it now publishes the scoreboard the same
# contract produced, next to the RTL, under the author guard.
# ============================================================================

def _mk_contract_project(tmp_path, desc_text):
    idoc = tmp_path / "phase1" / "input_doc"
    idoc.mkdir(parents=True)
    (idoc / "design_description.txt").write_text(desc_text)
    return tmp_path


def test_composed_shape_publishes_its_scoreboard(tmp_path):
    R = _load_runner()
    proj = _mk_contract_project(tmp_path / "p", _F6_DESC)
    res = R._try_canonical_primitive_rtl(proj, 0.0)
    assert res is not None and res.status == "PASS"
    assert res.extras["shape"] == "elastic_handshake_stage"
    tb = proj / "phase2" / "stage1" / "tb" / "tb_elastic_stage.v"
    assert tb.is_file()
    assert res.extras["scoreboard_tb"] == str(tb)
    assert str(tb) in res.output_files
    body = tb.read_text()
    assert "module tb_elastic_stage;" in body
    assert "accepted transfers lost" in body


def test_scoreboard_never_overwrites_an_authors_testbench(tmp_path):
    R = _load_runner()
    proj = _mk_contract_project(tmp_path / "p", _F6_DESC)
    tb = proj / "phase2" / "stage1" / "tb" / "tb_elastic_stage.v"
    tb.parent.mkdir(parents=True)
    tb.write_text("// an author's own testbench\n")
    res = R._try_canonical_primitive_rtl(proj, 0.0)
    assert res is not None and res.status == "PASS"
    assert tb.read_text() == "// an author's own testbench\n"
    assert "kept the existing" in res.extras["scoreboard_tb"]
    assert str(tb) not in res.output_files


def test_template_shapes_publish_no_scoreboard(tmp_path):
    """The sixteen fixed templates own no contract, so nothing new appears next
    to them: this wiring is additive for the composed shapes only."""
    R = _load_runner()
    proj = _mk_contract_project(tmp_path / "p",
                                _INLINE_POS["pulse_detect_0to1to0"])
    res = R._try_canonical_primitive_rtl(proj, 0.0)
    assert res is not None and res.status == "PASS"
    assert res.extras["shape"] == "pulse_detect_0to1to0"
    assert res.extras.get("scoreboard_tb") is None
    assert not (proj / "phase2" / "stage1" / "tb").exists()
    assert res.output_files == [
        str(proj / "phase2" / "stage1" / "rtl" / "pulse_detect.v")]


# ============================================================================
# The published scoreboard lands in phase2/stage1/tb/, which `l12_tb_coverage_
# check` READS. Measured on a composed project: that gate goes from rc=2 ("TB
# dir not found" -- it measured nothing) to rc=1 (it measured, and the design is
# genuinely short of the L12 sequences). That is the honest direction, but the
# property that must never rot is that a generic scoreboard is not mistaken for
# coverage of a named behavioural sequence.
# ============================================================================

def _run_l12(project):
    return subprocess.run(
        [sys.executable, str(PROGRAMS / "l12_tb_coverage_check.py"), str(project)],
        capture_output=True, text=True)


def _composed_project_with_l12(tmp_path, sequence_ids):
    R = _load_runner()
    proj = _mk_contract_project(tmp_path / "p", _F6_DESC)
    res = R._try_canonical_primitive_rtl(proj, 0.0)
    assert res is not None and res.status == "PASS"
    gd = proj / "phase1" / "generated_docs"
    gd.mkdir(parents=True, exist_ok=True)
    (gd / "L12_BEHAVIORAL_SEQUENCES.json").write_text(json.dumps(
        {"sequences": [{"id": s, "description": s.lower()} for s in sequence_ids]}))
    return proj


def test_scoreboard_is_not_mistaken_for_l12_sequence_coverage(tmp_path):
    proj = _composed_project_with_l12(
        tmp_path, ["BACKPRESSURE_STALL_RECOVERY", "RESET_MID_TRANSFER"])
    run = _run_l12(proj)
    assert run.returncode == 1, run.stdout + run.stderr
    report = json.loads(
        (proj / "reports" / "phase2" / "gates" / "l12_tb_coverage.json").read_text())
    assert report["tb_dir"].endswith("phase2/stage1/tb")
    assert report["total_sequences"] == 2
    assert report["covered_sequences"] == 0


def test_the_l12_gate_really_can_see_that_directory(tmp_path):
    """The control for the test above: if the gate could not read the published
    scoreboard at all, 'zero coverage' would prove nothing. A sequence whose id
    the scoreboard does contain IS reported covered."""
    proj = _composed_project_with_l12(tmp_path, ["TB_ELASTIC_STAGE"])
    tb = proj / "phase2" / "stage1" / "tb" / "tb_elastic_stage.v"
    assert "tb_elastic_stage" in tb.read_text()
    run = _run_l12(proj)
    report = json.loads(
        (proj / "reports" / "phase2" / "gates" / "l12_tb_coverage.json").read_text())
    assert report["covered_sequences"] == 1, run.stdout + run.stderr
    assert run.returncode == 0


# ============================================================================
# WHAT A DETECTOR NEVER LOOKS AT, IT CANNOT REFUSE.
#
# `barrel_left` -- an input matching the barrel-shifter detector while asking for
# a shift in the other direction -- is still answered with the right-shift
# template, and three attempts to close that measured why it is not free:
#
#   1. prose-derived behavioural poles are unreliable: scanning the sixteen
#      templates' own header comments for six polar pairs, ieee754 reads as
#      "left shift"/"signed" from incidental mantissa wording and two more read
#      as BOTH poles of signedness -- 2 of 16 misleading before any input;
#   2. code-derived shift direction is an IMPLEMENTATION detail, not the block's
#      behaviour: of the five templates that yield an unambiguous pole from
#      their code, three (gray-code FIFO, partial-product multiplier, restoring
#      divider) shift internally for reasons unrelated to what they promise;
#   3. the general table-free rule -- "if the input states a polar dimension the
#      matched detector never examines, DEFER" -- costs exactly one canonical
#      shape: parallel_to_serial_4's description states MSB-first and its
#      detector examines conversion wording, not bit order.
#
# That price is a decision, not a defect, so nothing here changes behaviour. What
# this test DOES do is stop the blindness growing silently: a new shape, or an
# edited detector, that leaves a stated polar dimension unexamined fails here and
# has to be looked at by a person.
# ============================================================================

_POLAR_DIMENSIONS = {
    "shift_direction": ("left", "right"),
    "bit_order": ("msb", "lsb", "most significant", "least significant"),
    "signedness": ("signed", "unsigned"),
    "clock_edge": ("rising edge", "falling edge", "posedge", "negedge",
                   "double-edge"),
}

# The one known blind spot, named rather than tolerated silently. Its template IS
# MSB-first, so today's answer is right; nothing checks that it stays right.
_KNOWN_UNEXAMINED = {("parallel_to_serial_4", "bit_order")}


def _detector_literals(shape):
    """Every string literal the detector for `shape` tests, read from source.

    Introspection, not a hand-maintained list: an edited detector moves this on
    its own.  The unsigned divider detector delegates its source contract to a
    parser helper, so include that helper's literals in the same audit."""
    import ast as _ast
    fn_name = dict(rcs._DETECTORS)[shape].__name__
    names = {fn_name}
    if shape == "unsigned_iterative_restoring_divider":
        names.add("_unsigned_division_observation")
    src = (PROGRAMS / "canonical_primitive_synth.py").read_text()
    found = set()
    literals = set()
    for node in _ast.parse(src).body:
        if isinstance(node, _ast.FunctionDef) and node.name in names:
            found.add(node.name)
            literals.update(n.value.lower() for n in _ast.walk(node)
                            if isinstance(n, _ast.Constant) and isinstance(n.value, str))
    if found == names:
        return literals
    raise AssertionError(f"detector source for {shape} not found")


def _names_word(phrase, text):
    """Word-boundary containment. `"signed" in "unsigned"` and `"mux" in "demux"`
    are both true and both wrong, so this module asks the same question the
    program now asks."""
    return re.search(r"\b" + re.escape(phrase) + r"\b", text) is not None


@pytest.mark.parametrize("shape,desc", _INLINE_POS.items())
def test_a_stated_polar_dimension_is_one_the_detector_examines(shape, desc):
    examined = " | ".join(sorted(_detector_literals(shape)))
    low = desc.lower()
    for dim, words in _POLAR_DIMENSIONS.items():
        if not any(_names_word(w, low) for w in words):
            continue                      # the input states nothing here
        if (shape, dim) in _KNOWN_UNEXAMINED:
            continue                      # the recorded exception, above
        assert any(_names_word(w, examined) for w in words), (
            f"{shape} answers a description that states {dim}, but its detector "
            f"never examines that dimension: an input stating the other pole "
            f"would get this template anyway")


def test_the_known_blind_spot_is_still_exactly_one():
    """If this fails, a shape has been added or a detector edited so that the
    blindness above grew. That is the moment to take the decision, not later."""
    blind = set()
    for shape, desc in _INLINE_POS.items():
        examined = " | ".join(sorted(_detector_literals(shape)))
        low = desc.lower()
        for dim, words in _POLAR_DIMENSIONS.items():
            if (any(_names_word(w, low) for w in words)
                    and not any(_names_word(w, examined) for w in words)):
                blind.add((shape, dim))
    assert blind == _KNOWN_UNEXAMINED


# ============================================================================
# A CHECK THAT CANNOT FAIL IS NOT A CHECK. Measured by mutating the composed RTL
# 13 valid ways and running each against the generated scoreboard: the first
# version killed 9 and let three through, of which two were real holes --
#   * a stage that back-pressures when it does not have to passed at reduced
#     throughput (59 transfers instead of 82): correctness was checked, the
#     contract's "stalled only when no slot is free" clause was not;
#   * a stage whose reset never clears passed with ZERO transfers observed --
#     a vacuous pass, the checker reporting success on a run that did nothing.
# (The third, an unconditional capture inside the branch where up_ready is
# already high, survives CORRECTLY -- and the claim is bounded-PROVED rather than
# argued: yosys `miter -equiv` + `sat -seq 24 -set-init-zero -prove-asserts`
# reports SUCCESS on the OBSERVABLE interface {up_ready, dn_valid, dn_data
# qualified by dn_valid}. The unqualified comparison does NOT prove -- dn_data
# genuinely differs while dn_valid is low -- so "equivalent" here means
# observationally equivalent, not bit-identical. The same harness FAILS on the
# m6 stall mutant, so it is not a proof that proves anything.)
# Both holes are closed in the generator, so every composed design gets the
# stronger check; the kill rate is now 12 of 13. Evidence: the lane's killtest/.
# ============================================================================

def test_the_elastic_scoreboard_checks_the_no_needless_stall_clause():
    tb = rcs.emit_scoreboard_tb(rcs.extract_handshake_contract(_F6_DESC))
    assert "stalled at cycle" in tb
    assert "if (!up_ready) begin" in tb


def test_neither_scoreboard_can_pass_vacuously():
    tb6 = rcs.emit_scoreboard_tb(rcs.extract_handshake_contract(_F6_DESC))
    assert "if (rd < 32) begin" in tb6
    assert "only %0d transfers observed" in tb6
    tb7 = rcs.emit_scoreboard_tb(rcs.extract_handshake_contract(_F7_DESC))
    assert "if (n_in == 0) begin" in tb7
    assert "vacuous" in tb7


# ============================================================================
# The near-miss control above uses descriptions that are FAR from any handshake,
# so no plausible loosening of the contract layer can make it fire -- a control
# that cannot fail. These sit on the layer's actual boundary instead: each states
# almost a contract, and each must still DEFER, naming what it did not state.
# ============================================================================

_BOUNDARY_NEAR_MISSES = {
    "ratio with no ratio stated": (
        "Module name:\n    event_thinner\n"
        "A block that forwards some input events to the output.\n"
        "Input ports:\n clk: Clock.\n rst_n: Active low reset.\n"
        " in_valid: Pulses on an input event.\n"
        "Output ports:\n out_valid: Pulses on an output event.\n"
        "Implementation:\nIt divides the event stream.\n"),
    "elastic with no clock stated": (
        "Module name:\n    no_clock_stage\n"
        "A stage between a producer and a consumer.\n"
        "Input ports:\n up_data [7:0]: The word offered.\n up_valid: Offered.\n"
        " dn_ready: The consumer can take a word.\n"
        "Output ports:\n up_ready: This stage can take a word.\n"
        " dn_data [7:0]: The word offered on.\n dn_valid: Offered.\n"
        "Implementation:\nThe stage must register the output and buffer one\n"
        "additional transfer.\n"),
    "half a handshake": (
        "Module name:\n    sink_only\n"
        "A block that consumes a stream and reports a total.\n"
        "Input ports:\n clk: Clock.\n rst_n: Active low reset.\n"
        " up_data [7:0]: The word offered.\n up_valid: Offered.\n"
        "Output ports:\n up_ready: This block can take a word.\n"
        " total [15:0]: The running sum.\n"
        "Implementation:\nA transfer happens when up_valid and up_ready are\n"
        "both high; there is no downstream interface.\n"),
}


@pytest.mark.parametrize("label,desc", _BOUNDARY_NEAR_MISSES.items())
def test_contract_layer_declines_on_its_own_boundary(label, desc):
    assert rcs.detect_shape(desc) is None, label


def test_the_boundary_cases_say_what_was_missing_where_they_can():
    """Not merely a DEFER: for the two that DO state a handshake, the program
    names the unstated field rather than guessing it."""
    why = rcs.route_to_ai_reason(_BOUNDARY_NEAR_MISSES["ratio with no ratio stated"])
    assert why is not None and any("ratio" in u for u in why["unresolved"])
    why = rcs.route_to_ai_reason(_BOUNDARY_NEAR_MISSES["elastic with no clock stated"])
    assert why is not None and any("clock" in u for u in why["unresolved"])


def test_asynchronous_is_not_read_as_synchronous():
    """"asynchronous reset" CONTAINS "synchronous reset". A substring test reads
    it as stating both poles, the ambiguity rule then discards both, and an input
    asking for an asynchronous reset against a synchronous template is answered
    anyway -- exactly the silent wrong answer this layer exists to stop.

    Found only when the inline population was widened from ten shapes to sixteen:
    none of the original ten says "asynchronous reset"."""
    assert rcs.extract_stated_reset_poles(
        "The reset is an asynchronous reset.") == {"async_reset"}
    assert rcs.extract_stated_reset_poles(
        "The reset is a synchronous reset.") == {"sync_reset"}
    assert rcs.extract_stated_reset_poles("rst: async reset.") == {"async_reset"}
    assert rcs.extract_stated_reset_poles("rst: sync reset.") == {"sync_reset"}
    # a text that really does state both is still ambiguous, and records neither
    assert rcs.extract_stated_reset_poles(
        "An asynchronous reset and a synchronous reset.") == set()
    # and the consequence: a sync-reset template is withdrawn from an input that
    # asks for an asynchronous one
    desc = _INLINE_POS["lfsr4_xnor_left"] + "The reset is an asynchronous reset.\n"
    assert "sync_reset" in rcs.template_commitments("lfsr4_xnor_left")
    assert rcs.detect_shape(desc) is None
    assert rcs.route_to_ai_reason(desc)["property"] == "async_reset"


def test_the_inline_population_is_every_shape():
    """The claim "no canonical description conflicts with its own template" is
    only worth what its population is worth. It was ten of sixteen, and the
    dataset-backed tests that would have covered the rest SKIP wherever the
    corpus is absent -- which is where the bug above was hiding."""
    assert set(_INLINE_POS) == {shape for shape, _ in rcs._DETECTORS}


def test_a_word_that_merely_contains_a_tag_is_not_a_directive():
    """The async/sync substring bug was one instance of a class, so the class was
    swept. Measured before the fix: `must not use a demux` and `a premuxed input`
    both tagged multiplexer_stages and `a genvariable name` tagged generate_block,
    and each silently WITHDREW a template the input never objected to."""
    for text in ("The implementation must not use a demux for the select.",
                 "The design must not use a premuxed input.",
                 "The implementation must not use a genvariable name."):
        assert rcs.extract_architecture_directives(text) == [], text
    # the real words still register, including the plural forms
    for text, tag in (("The implementation must not use a mux.",
                       "multiplexer_stages"),
                      ("The implementation must not use any muxes.",
                       "multiplexer_stages"),
                      ("The implementation must not use a generate block.",
                       "generate_block"),
                      ("This must not instantiate any submodule.",
                       "submodule_instantiation")):
        found = rcs.extract_architecture_directives(text)
        assert [t for _, t, _ in found] == [tag], text
    # and the end-to-end consequence: an unrelated constraint no longer costs a
    # canonical design its template
    desc = (_INLINE_POS["barrel_shifter_right_8"]
            + "The implementation must not use a demux for the select.\n")
    assert rcs.detect_shape(desc) == "barrel_shifter_right_8"


_WRAPPED_DIRECTIVES = {
    "wrapped mid-phrase": "The implementation must not\ninstantiate any "
                          "submodule.\n",
    "wrapped before the tag": "This block is a single leaf cell: the "
                              "implementation must not use a\ngenerate block.\n",
    "wrapped after the marker": "The design must not\nuse any muxes at all.\n",
    "on one line": "The implementation must not instantiate any submodule.\n",
}


@pytest.mark.parametrize("label,text", _WRAPPED_DIRECTIVES.items())
def test_a_directive_survives_the_line_it_is_wrapped_on(label, text):
    """Clauses are SENTENCES, not lines.

    Measured before this: the identical prohibition, wrapped across two lines the
    way any 72-column description wraps, recorded NO directive -- the marker
    landed in one clause and the tag in the next -- and the fixed template was
    emitted over it. The whole architecture layer was defeated by reformatting,
    and this lane's own exposure fixture happened to keep the phrase on one line,
    which is exactly why it looked like it worked."""
    assert rcs.extract_architecture_directives(text), label
    desc = _INLINE_POS["barrel_shifter_right_8"] + text
    assert rcs.detect_shape(desc) is None, label
    assert rcs.route_to_ai_reason(desc)["kind"] == "architecture_conflict"


def test_flowing_lines_together_invents_no_directive():
    """The control for the fix: joining lines could pair a marker in one sentence
    with a tag in another. Over the whole canonical population it does not."""
    for shape, desc in _INLINE_POS.items():
        assert rcs.architecture_conflict(desc, shape) is None, shape
        assert rcs.detect_shape(desc) == shape, shape
    # and a marker and a tag in DIFFERENT sentences still make no directive
    assert rcs.extract_architecture_directives(
        "The core must not stall. A mux selects the output.") == []


_HEADING_FORMS = [("Input ports:", "Output ports:"),
                  ("Inputs:", "Outputs:"),
                  ("Input Signals:", "Output Signals:"),
                  ("INPUT PORTS:", "OUTPUT PORTS:")]


@pytest.mark.parametrize("heading,out", _HEADING_FORMS)
def test_the_port_blocks_are_found_under_every_ordinary_heading(heading, out):
    """Measured before this: "Inputs:" was not matched, so every port under it
    had no direction, no contract could be built, and the layer silently never
    fired -- fail-closed, but invisible, and only because every fixture in this
    lane happened to use "Input ports:"."""
    desc = (
        "Module name:\n    stage\n" + heading + "\n"
        " clk: Clock.\n rst_n: Active low reset.\n"
        " up_data [7:0]: The word offered.\n up_valid: Offered.\n"
        " dn_ready: The consumer can take a word.\n" + out + "\n"
        " up_ready: This stage can take a word.\n"
        " dn_data [7:0]: The word offered on.\n dn_valid: Offered.\n"
        "Implementation:\n"
        "The stage must register the output and buffer one additional "
        "transfer.\n")
    c = rcs.extract_handshake_contract(desc)
    assert c is not None and c.kind == "elastic_stage", heading
    assert c.unresolved == [], (heading, c.unresolved)


def test_two_different_stated_widths_are_not_a_stated_width():
    """Taking the first would be a guess. An explicit [hi:lo] still wins, and a
    line that says the same width twice is not ambiguous."""
    def w(line):
        return rcs._port_width(
            "Module name:\n    x\nInput ports:\n " + line + "\n", "up_data")
    assert w("up_data: the 8-bit word, packed into a 32-bit beat.") is None
    assert w("up_data: an 8-bit word; the bus is 8 bits wide.") == 8
    assert w("up_data [7:0]: the 32-bit accumulator's low byte.") == 8
    assert w("up_data: the 8-bit data word.") == 8


def test_prose_after_the_port_blocks_is_not_read_as_ports():
    """A line in the prose that looks like "name: description" must not become a
    port of whichever block was open last -- it can otherwise be chosen as a
    channel's data port and end up in the emitted module."""
    desc = _F6_DESC + (
        "Implementation:\n"
        " counter: an internal counter, not a port.\n"
        " state: the internal state register.\n")
    dirs = rcs._port_directions(desc)
    assert "counter" not in dirs and "state" not in dirs
    c = rcs.extract_handshake_contract(desc)
    assert c is not None and c.unresolved == []
    assert c.up["data"] == "up_data" and c.down["data"] == "dn_data"
    rtl = rcs.emit_rtl(rcs.detect_shape(desc), desc)
    # on word boundaries: "state" is inside "stage", and this assertion failed
    # for exactly that reason before -- the same trap the program was fixed for
    assert not _names_word("counter", rtl) and not _names_word("state", rtl)


def test_a_clock_is_never_chosen_as_a_data_port():
    """Measured before this: a stage whose clock is named `i_clk` had that clock
    CHOSEN as its upstream data port. It did not reach emission only because the
    width was also unstated -- the layer was saved by a check that knows nothing
    about clocks. Finding a clock and excluding it from the data candidates were
    two different name lists, and they disagreed."""
    desc = ("Module name:\n    stage\nInput ports:\n i_clk: Clock.\n"
            " up_valid: The producer is offering a word.\n"
            " dn_ready: The consumer can take a word.\n"
            "Output ports:\n up_ready: This stage can take a word.\n"
            " dn_data [7:0]: The word offered on.\n dn_valid: Offered.\n"
            "Implementation:\nThe stage must register the output and buffer one "
            "additional transfer.\n")
    c = rcs.extract_handshake_contract(desc)
    assert c is not None and c.clock == "i_clk"
    assert c.up["data"] is None
    assert any("data port" in u for u in c.unresolved)
    assert rcs.detect_shape(desc) is None


@pytest.mark.parametrize("clk,rst", [("clk", "rst_n"), ("i_clk", "i_rst_n"),
                                     ("clk_i", "rst_ni"), ("CLK", "RESET_N")])
def test_ordinary_clock_and_reset_spellings_are_recognised(clk, rst):
    """One recogniser, so a spelling that is found as a clock is also kept out of
    the data candidates -- and a reset spelled `i_rst_n` no longer leaves the
    contract unresolved."""
    desc = ("Module name:\n    stage\nInput ports:\n"
            f" {clk}: Clock.\n {rst}: Active low reset.\n"
            " up_data [7:0]: The word offered.\n up_valid: Offered.\n"
            " dn_ready: The consumer can take a word.\n"
            "Output ports:\n up_ready: This stage can take a word.\n"
            " dn_data [7:0]: The word offered on.\n dn_valid: Offered.\n"
            "Implementation:\nThe stage must register the output and buffer one "
            "additional transfer.\n")
    c = rcs.extract_handshake_contract(desc)
    assert c is not None and c.unresolved == [], (clk, rst, c.unresolved)
    assert c.clock == clk and c.reset == rst
    assert c.up["data"] == "up_data"
    rtl = rcs.emit_rtl(rcs.detect_shape(desc), desc)
    assert f"input  wire {clk}," in rtl and f"input  wire {rst}," in rtl


def test_the_ratio_check_does_not_fail_a_ratio_that_divides_unevenly():
    """The generated ratio check compared n_out * RATIO with n_in, which is only
    true when the stimulus count is a multiple of RATIO. Measured: a composed
    divider at ratio 3 driven with 64 events reported
    `FAIL: 64 in 21 out, expected 21` -- the checker contradicting itself in its
    own message, and a correct DUT failing. Integer division is the right
    comparison."""
    tb = rcs.emit_scoreboard_tb(rcs.extract_handshake_contract(
        _F7_DESC.replace("for every 1 input events", "for every 3 input events")))
    assert "n_out != n_in / 3" in tb
    assert "n_out * 3" not in tb


def test_the_ratio_counter_is_sized_from_its_own_parameter():
    """`RATIO` is a parameter, so a caller may override it. The counter used to be
    sized from the ratio STATED IN THE DESCRIPTION, so a module composed for 2 and
    instantiated at 8 counted to 1 and emitted NOTHING -- measured, 0 outputs for
    64 inputs. A parameter that cannot be overridden is not a parameter."""
    for stated in (1, 2, 4):
        rtl = rcs.emit_from_contract(rcs.extract_handshake_contract(
            _F7_DESC.replace("for every 1 input events",
                             f"for every {stated} input events")))
        assert f"parameter RATIO = {stated}" in rtl
        assert "reg [$clog2(RATIO + 1) - 1:0] count;" in rtl
        # no width literal derived from the stated ratio survives in the counter
        assert "count <= 0;" in rtl


_ELASTIC_MATRIX_RESETS = [
    ("rst_n", "Active low reset.", "", True, None),
    ("rst", "Active high reset.", "", False, None),
    ("rst", "Active high reset.", " The reset is a synchronous reset.", False, True),
    ("rst_n", "Active low reset.", " The reset is a synchronous reset.", True, True),
]


def _elastic_desc(rst_name, rst_line, extra, width_phrase):
    return ("Module name:\n    elastic_stage\nAn elastic pipeline stage.\n"
            "Input ports:\n clk: Clock.\n"
            f" {rst_name}: {rst_line}\n"
            f" up_data{width_phrase}: The word offered by the producer.\n"
            " up_valid: Offered.\n"
            " dn_ready: The consumer can take a word.\n"
            "Output ports:\n up_ready: This stage can take a word.\n"
            f" dn_data{width_phrase}: The word offered on.\n dn_valid: Offered.\n"
            "Implementation:\nA transfer happens when valid and ready are both "
            "high. The stage must register the output and buffer one additional "
            "transfer so the producer is only stalled when no slot is free. No "
            "word that was not accepted may be captured, no accepted word may be "
            "lost, and accepted words must leave in the order they arrived."
            + extra + "\n")


@pytest.mark.parametrize("rst_name,rst_line,extra,low,sync",
                         _ELASTIC_MATRIX_RESETS)
@pytest.mark.parametrize("width_phrase,width", [(" [7:0]", 8), (" [31:0]", 32)])
def test_the_elastic_stage_composes_across_reset_styles_and_widths(
        rst_name, rst_line, extra, low, sync, width_phrase, width):
    """Everything in this branch had composed ONE combination: 8 bits, active-low
    asynchronous reset. Synchronous and active-high resets had never been
    emitted, let alone simulated."""
    desc = _elastic_desc(rst_name, rst_line, extra, width_phrase)
    c = rcs.extract_handshake_contract(desc)
    assert c is not None and c.unresolved == []
    assert (c.reset, c.reset_active_low, c.reset_sync) == (rst_name, low, sync)
    assert c.width == width
    rtl = rcs.emit_rtl(rcs.detect_shape(desc), desc)
    if sync:
        assert f"always @(posedge {c.clock}) begin" in rtl
        assert "negedge" not in rtl and "or posedge" not in rtl
    else:
        edge = "negedge" if low else "posedge"
        assert f"always @(posedge {c.clock} or {edge} {rst_name}) begin" in rtl
    assert f"if ({'!' if low else ''}{rst_name}) begin" in rtl
    assert f"[{width - 1}:0] " in rtl


def test_a_one_bit_width_stated_in_words_is_a_stated_width():
    """A single-bit port is idiomatically written in WORDS here ("data_in:
    One-bit input."), and that is still a width the input stated. Measured: it
    was previously unresolved, so no single-bit elastic stage could compose."""
    desc = _elastic_desc("rst_n", "Active low reset.", "", "").replace(
        "up_data: The word", "up_data: One-bit word").replace(
        "dn_data: The word", "dn_data: One-bit word")
    c = rcs.extract_handshake_contract(desc)
    assert c is not None and c.unresolved == [] and c.width == 1
    rtl = rcs.emit_rtl(rcs.detect_shape(desc), desc)
    assert "input  wire up_data," in rtl and "[0:0]" not in rtl
    # a width written in words that this reader does NOT parse stays unresolved
    # and is NAMED -- it is not guessed
    four = _elastic_desc("rst_n", "Active low reset.", "", "").replace(
        "up_data: The word", "up_data: Four-bit word")
    c4 = rcs.extract_handshake_contract(four)
    assert c4.width is None and any("width" in u for u in c4.unresolved)


_COLLIDING_PORTS = {
    "a divider whose payload port is named count": (
        "Module name:\n    pulse_divider\nAn event ratio divider.\n"
        "Input ports:\n clk: Clock.\n rst_n: Active low reset.\n"
        " count [7:0]: The 8-bit payload accompanying an input event.\n"
        " in_valid: Pulses on each input event.\n"
        "Output ports:\n out_data [7:0]: The forwarded payload.\n"
        " out_valid: Pulses on each output event.\n"
        "Implementation:\nThe divider emits one output event for every 1 input "
        "events.\n", "count"),
    "a stage whose data ports are named held_data / skid_data": (
        "Module name:\n    elastic_stage\nAn elastic pipeline stage.\n"
        "Input ports:\n clk: Clock.\n rst_n: Active low reset.\n"
        " held_data [7:0]: The word offered by the producer.\n"
        " up_valid: Offered.\n dn_ready: The consumer can take a word.\n"
        "Output ports:\n up_ready: This stage can take a word.\n"
        " skid_data [7:0]: The word offered on.\n dn_valid: Offered.\n"
        "Implementation:\nA transfer happens when valid and ready are both high. "
        "The stage must register the output and buffer one additional transfer "
        "so the producer is only stalled when no slot is free.\n", "held_data"),
}


@pytest.mark.parametrize("label,case", _COLLIDING_PORTS.items())
def test_internal_names_give_way_to_the_designs_own_port_names(label, case):
    """Ports come from the INPUT; internal signals are the generator's choice, so
    the internals must give way. Measured before this: a divider whose payload is
    named `count` and a stage whose data is named `held_data` both emitted RTL
    that DOES NOT COMPILE -- "'count' has already been declared in this scope"
    -- because the internal names were fixed literals."""
    desc, port = case
    rtl = rcs.emit_rtl(rcs.detect_shape(desc), desc)
    # the port keeps its name, declared exactly once
    assert len(re.findall(r"\b" + port + r"\b\s*[,;)]", rtl)) >= 1
    decls = re.findall(r"^\s*(?:input|output|reg|wire)[^;\n]*\b" + port
                       + r"\b", rtl, re.M)
    assert len(decls) == 1, (label, decls)
    # and every generated identifier that would have collided was renamed
    for taken in ("count", "held_data", "skid_data", "up_fire", "dn_fire"):
        if taken == port:
            assert f"{taken}_2" in rtl or taken not in _INTERNAL_OF(rtl)


def _INTERNAL_OF(rtl):
    """Identifiers this module DECLARES (as reg/wire), for the check above."""
    return set(re.findall(r"^\s*(?:reg|wire)[^;\n]*?(\w+)\s*[;=\[]", rtl, re.M))


def test_the_scoreboard_locals_also_give_way():
    """The generated TB declares q/wr/rd/errors/i and n_in/n_out/i; a design with
    a port of one of those names would make the TB uncompilable too."""
    desc = _F7_DESC.replace("in_data", "n_in").replace("out_data", "n_out")
    c = rcs.extract_handshake_contract(desc)
    tb = rcs.emit_scoreboard_tb(c)
    assert "integer n_in_2 = 0, n_out_2 = 0, i;" in tb


# ============================================================================
# CZ2035P-6 -- WHAT A DETECTOR NEVER LOOKS AT, IT CANNOT REFUSE (closed, for the
# one dimension where the template's own pole is soundly derivable).
#
# The block above records three routes measured closed. Route 3 was re-measured
# on this base over all 16 detectors x 4 polar dimensions: 13 of 16 examine NONE
# of the four and all 16 are blind to at least three, so its "cost = exactly one
# canonical shape" is an artefact of how terse the canonical descriptions are,
# not a property of the rule -- against ordinary prose it would defer nearly
# everything. Route 2 reopens under a WIDTH test: a shift preserves the
# operand's width, a zero-extension does not. Anchored on a declared input port
# of known integer width it yields a pole for exactly one of the sixteen
# templates, and that pole is right.
#
# The seam was already there: `detect_shape` withdraws a template through
# `architecture_conflict` and `route_to_ai_reason` names the withdrawal. Only
# the POLE VOCABULARY is new.
# ============================================================================

# Neutral, input-only. Matches `_is_barrel_shifter` (module-name token, ports
# in/ctrl/out, the phrase "barrel shifter", "ctrl" and "shift") while asking for
# the other direction.
_BARREL_LEFT_DESC = (
    "Module name:\n    barrel_shifter\n"
    "A barrel shifter that performs a logical shift left on an 8-bit input, "
    "controlled by ctrl.\n"
    "Input ports:\n in: Data to be shifted.\n ctrl: Shift amount.\n"
    "Output ports:\n out: Shifted result.\n"
    "The barrel shifter shall shift the input to the left by the number of bit "
    "positions given by ctrl, filling the vacated low-order bits with zero.\n")

_BARREL_RIGHT_DESC = _BARREL_LEFT_DESC.replace("shift left", "shift right") \
    .replace("to the left", "to the right").replace("low-order", "high-order")


def test_a_stated_opposite_shift_direction_withdraws_the_template():
    """OLD WRONG BEHAVIOUR, on input-only material. Measured on the base before
    this layer: this description matched `barrel_shifter_right_8` and was
    answered EMIT, rc=0, with `{4'b0000, in[7:4]}` -- a RIGHT shifter for an
    input that says left three times, silently. The detector still matches it;
    the fixed topology is now WITHDRAWN and the pole is NAMED."""
    assert rcs._is_barrel_shifter(
        _BARREL_LEFT_DESC, rcs.module_name_of(_BARREL_LEFT_DESC),
        rcs._port_tokens(_BARREL_LEFT_DESC) - rcs._NOISE), (
        "the fixture must still MATCH the detector, or it proves nothing")
    # These two lines come FIRST on purpose. They read only APIs the base
    # already had, so swapping the base program back in makes this test fail by
    # ASSERTION on the old behaviour -- not by AttributeError on a layer that is
    # simply absent, which would be no evidence of anything.
    assert rcs.detect_shape(_BARREL_LEFT_DESC) is None
    conflict = rcs.architecture_conflict(_BARREL_LEFT_DESC,
                                         "barrel_shifter_right_8")
    assert conflict is not None and conflict["polarity"] == "stated"
    assert conflict["property"] == "shift_left"
    assert rcs.extract_stated_shift_direction(_BARREL_LEFT_DESC) == {"shift_left"}
    why = rcs.route_to_ai_reason(_BARREL_LEFT_DESC)
    assert why["route"] == "ai_author"
    assert why["kind"] == "architecture_conflict"
    assert why["shape_declined"] == "barrel_shifter_right_8"
    assert why["property"] == "shift_left"


def test_the_alternative_architecture_control_stays_green():
    """THE CONTROL. A withdrawal that fires on the agreeing input too would be a
    regression wearing a fix's clothes: it would cost the shape its template for
    saying what the template already does. An input stating the SAME direction
    still gets it, and an input that states no direction is untouched."""
    assert rcs.extract_stated_shift_direction(_BARREL_RIGHT_DESC) == {"shift_right"}
    assert rcs.architecture_conflict(_BARREL_RIGHT_DESC,
                                     "barrel_shifter_right_8") is None
    assert rcs.detect_shape(_BARREL_RIGHT_DESC) == "barrel_shifter_right_8"
    assert rcs.emit_rtl(rcs.detect_shape(_BARREL_RIGHT_DESC),
                        _BARREL_RIGHT_DESC) == rcs._TEMPLATES[
                            "barrel_shifter_right_8"]
    silent = _INLINE_POS["barrel_shifter_right_8"]
    assert rcs.extract_stated_shift_direction(silent) == set()
    assert rcs.detect_shape(silent) == "barrel_shifter_right_8"


def test_shift_poles_are_derived_from_the_template_code():
    """Derived, never hand-declared -- a re-authored template moves its own pole.
    Exactly ONE of the sixteen yields one, and it says what that template does.
    Compared by MEMBERSHIP of the shape set, not by count."""
    poled = {s for s in rcs._TEMPLATES
             if any(p.startswith("shift_") for p in rcs.template_commitments(s))}
    assert poled == {"barrel_shifter_right_8"}
    assert "shift_right" in rcs.template_commitments("barrel_shifter_right_8")
    assert "shift_left" not in rcs.template_commitments("barrel_shifter_right_8")


def test_the_shift_derivation_can_report_either_pole():
    """A rule that can only ever say one thing is not a rule. Feed it the same
    template with its one port-anchored concatenation reversed and it must say
    the other pole -- and then the SAME description conflicts the other way."""
    left_rtl = rcs._TEMPLATES["barrel_shifter_right_8"].replace(
        "{4'b0000, in[7:4]}", "{in[3:0], 4'b0000}")
    assert left_rtl != rcs._TEMPLATES["barrel_shifter_right_8"]
    assert "shift_left" in rcs._rtl_commitments(left_rtl)
    assert "shift_right" not in rcs._rtl_commitments(left_rtl)


def test_a_zero_extension_is_not_read_as_a_shift():
    """The measurement that reopened this route. Reading the code NAIVELY, the
    IEEE-754 multiplier's `{2'd0, a[30:23]}` looks like a right shift. It is a
    zero-EXTENSION of the exponent FIELD: bit 30 is not `a`'s msb, so the slice
    is not anchored where a shift's would be."""
    tpl = rcs._TEMPLATES["ieee754_single_multiplier"]
    assert "{2'd0, a[30:23]}" in tpl, "fixture drifted from the template"
    widths = rcs._rtl_input_port_widths(tpl)
    assert widths["a"] == 32 and widths["b"] == 32
    assert rcs._rtl_shift_poles(tpl) == set()


_ANCHOR_MODULE = ("module m(input [7:0] x, output [7:0] y);\n"
                  "    wire [7:0] t = %s;\n"
                  "    assign y = t;\n"
                  "endmodule\n")


@pytest.mark.parametrize("expr,pole", [
    ("{4'b0000, x[7:4]}", "shift_right"),   # a real right shift by 4
    ("{x[3:0], 4'b0000}", "shift_left"),    # a real left shift by 4
])
def test_the_anchored_forms_are_recognised(expr, pole):
    assert rcs._rtl_shift_poles(_ANCHOR_MODULE % expr) == {pole}


@pytest.mark.parametrize("expr,violates", [
    # msb anchor alone is satisfied, fill-width anchor is not: the TOP nibble
    # zero-extended to six bits. Not a shift.
    ("{2'b00, x[7:4]}", "fill-width anchor"),
    # fill-width anchor alone is satisfied, msb anchor is not: a middle field.
    ("{2'b00, x[5:2]}", "msb anchor"),
    # the same two, mirrored, for the left form
    ("{x[6:0], 3'b000}", "left msb anchor"),
    ("{x[4:1], 3'b000}", "left low anchor"),
])
def test_each_anchor_is_load_bearing_on_its_own(expr, violates):
    """NEITHER anchor is redundant. Each of these satisfies exactly one of the
    two and is not a shift; dropping the anchor it violates would make the rule
    call it one. Written after a mutation of an earlier formulation SURVIVED:
    that version also tested width preservation, which the two anchors already
    imply, so the clause could not fail and proved nothing."""
    assert rcs._rtl_shift_poles(_ANCHOR_MODULE % expr) == set(), violates


def test_a_parametric_port_width_yields_no_pole_rather_than_a_default():
    """Unknown is recorded as unknown. A width the source writes parametrically
    does not resolve to an integer, so the width test cannot be applied and no
    pole is claimed -- never a guessed default."""
    widths = rcs._rtl_input_port_widths(rcs._TEMPLATES["async_gray_fifo"])
    assert "wdata" in widths and widths["wdata"] is None
    assert widths["wclk"] == 1
    for shape in ("async_gray_fifo", "pipelined_ripple_adder_64",
                  "pipelined_unsigned_multiplier_8"):
        assert rcs._rtl_shift_poles(rcs._TEMPLATES[shape]) == set()


def test_a_description_stating_both_shift_directions_records_neither():
    """The same ambiguity rule the reset poles use: contradictory input is not a
    licence to pick."""
    both = _BARREL_LEFT_DESC + "In an alternative mode it may shift right.\n"
    assert rcs.extract_stated_shift_direction(both) == set()
    assert rcs.detect_shape(both) == "barrel_shifter_right_8"


@pytest.mark.parametrize("text", [
    "The output field is right-justified within the word.",
    "The left-hand operand is registered before the adder.",
    "Any data left over from the previous frame is discarded.",
    "A right-angle turn in the layout is not permitted.",
])
def test_a_direction_word_not_about_shifting_states_nothing(text):
    """`left` and `right` are ordinary English. The pole counts only where it is
    SHIFTING that is being described, or the layer withdraws templates from
    descriptions that never objected to them -- the same class of defect as
    `demux` once forging a mux directive."""
    assert rcs.extract_stated_shift_direction(text) == set()
    assert rcs.detect_shape(
        _INLINE_POS["barrel_shifter_right_8"] + text
    ) == "barrel_shifter_right_8"


def test_the_sixteen_canonical_descriptions_still_emit_their_own_bytes():
    """The end-to-end guard, pinned in the suite so no human has to remember to
    run it: every canonical description still detects as its own shape and emits
    its template byte for byte. Membership first, then bytes."""
    assert set(_INLINE_POS) == {k for k, _ in rcs._DETECTORS}
    for shape, desc in _INLINE_POS.items():
        assert rcs.detect_shape(desc) == shape
        rtl = rcs.emit_rtl(shape, desc)
        if shape == "unsigned_iterative_restoring_divider":
            assert "module unsigned_ratio_unit" in rtl
            assert rcs._unsigned_division_observation(desc)[0]["source_sha256"] in rtl
        else:
            assert rtl == rcs._TEMPLATES[shape]


def test_the_lfsr_states_a_direction_and_keeps_its_template():
    """The population's own witness that the width test is load-bearing. The
    LFSR's canonical description says "the register is shifted left" -- a real
    stated pole, in the shipped corpus, not a constructed one. Its template
    shifts an internal register, not an input port, so no pole is derived from
    it and the shape is untouched. The blunter rule measured closed above (defer
    whenever the input states a dimension the detector ignores) would have taken
    this template away for saying something true about itself."""
    desc = _INLINE_POS["lfsr4_xnor_left"]
    assert rcs.extract_stated_shift_direction(desc) == {"shift_left"}
    assert rcs._rtl_shift_poles(rcs._TEMPLATES["lfsr4_xnor_left"]) == set()
    assert rcs.architecture_conflict(desc, "lfsr4_xnor_left") is None
    assert rcs.detect_shape(desc) == "lfsr4_xnor_left"
    assert rcs.emit_rtl("lfsr4_xnor_left", desc) == rcs._TEMPLATES["lfsr4_xnor_left"]


def test_the_front_door_defers_the_contradicted_shape(tmp_path):
    """NO HUMAN HAS TO REMEMBER THIS. The withdrawal is on the path the runner
    already takes for every ordinary design: `_try_canonical_primitive_rtl`
    returns None, the step falls through to the AI author, and -- the part that
    matters -- NO RTL is written. Before this layer the same project got a
    right-shift `barrel_shifter.v` on disk and a PASS."""
    R = _load_runner()
    proj = _mk_project(tmp_path, _BARREL_LEFT_DESC)
    assert R._try_canonical_primitive_rtl(proj, 0.0) is None
    rtl_dir = proj / "phase2" / "stage1" / "rtl"
    assert not rtl_dir.is_dir() or list(rtl_dir.rglob("*.v")) == []


def test_the_front_door_still_emits_the_agreeing_shape(tmp_path):
    """The control, on the same path: an input that states the direction the
    template implements is still answered program-first, byte for byte."""
    R = _load_runner()
    proj = _mk_project(tmp_path, _BARREL_RIGHT_DESC)
    res = R._try_canonical_primitive_rtl(proj, 0.0)
    assert res is not None and res.status == "PASS"
    assert res.extras.get("shape") == "barrel_shifter_right_8"
    emitted = sorted((proj / "phase2" / "stage1" / "rtl").glob("*.v"))
    assert [p.name for p in emitted] == ["barrel_shifter.v"]
    assert emitted[0].read_text() == rcs._TEMPLATES["barrel_shifter_right_8"]


# ============================================================================
# COUNTING EVENTS CANNOT SEE A PAYLOAD THAT IS NEVER FORWARDED.
#
# Measured 2026-09-06 on v1.17.83, by composing F7 at four ratios and mutating
# the composed RTL eight ways: five mutants were killed and one was NOT --
# replacing the WHOLE payload path with a constant (`out_data <= 8'd0`) still
# reported `PASS 64 in 21 out` at every ratio. The generated ratio TB declared
# `out_data` and wired it to the DUT and then never compared it, so every
# payload defect in the family was invisible to the check that ships beside it.
#
# (Two other survivors of that sweep were MY mutants and not holes: an
# unconditional capture where `held_valid` is being cleared anyway, and a
# `skid_valid && !up_fire` guard that is dead because `up_ready = !skid_valid`.
# Both are PROVED EQUIVALENT on the observable interface by yosys
# `miter -equiv` + `sat -seq 24 -set-init-zero -prove-asserts`, with a mutant
# that genuinely loses a transfer as the control that correctly does NOT prove.)
#
# The check does NOT decide WHICH of the N inputs travels. The description
# states the ratio and states that the payload is forwarded; it does not say
# whether the first or the last of a group is the one that arrives, and issue
# #2035 forbids guessing a hidden expected value. So it checks MEMBERSHIP in the
# group that produced the event -- which is exact at a unit ratio, where the
# group has one member and nothing is left open.
# ============================================================================

# `ORACLE_MISSING` is `_sim_tools`' name for the (iverilog, vvp) pair -- the two
# binaries a test needs to COMPILE AND RUN something. It has nothing to do with
# reading an oracle output, which the read-only-input rule forbids and which nothing here does:
# every value compared below is generated from the design INPUT.
import _sim_tools  # noqa: E402

_NEEDS_VVP = pytest.mark.skipif(
    bool(_sim_tools.ORACLE_MISSING),
    reason="this check COMPILES AND RUNS the generated scoreboard; missing on "
           "this host: " + ", ".join(_sim_tools.ORACLE_MISSING or ("-",)))


def _ratio_desc(n):
    return _F7_DESC.replace("for every 1 input events", f"for every {n} input events")


def _simulate(workdir, rtl, tb):
    """Compile and run one composed design against its own generated scoreboard."""
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "dut.v").write_text(rtl)
    (workdir / "tb.v").write_text(tb)
    c = subprocess.run(["iverilog", "-g2012", "-o", str(workdir / "a.vvp"),
                        str(workdir / "dut.v"), str(workdir / "tb.v")],
                       capture_output=True, text=True)
    assert c.returncode == 0, f"the GENERATED sources must compile:\n{c.stderr}"
    r = subprocess.run(["vvp", str(workdir / "a.vvp")], capture_output=True,
                       text=True)
    return r.stdout + r.stderr


@_NEEDS_VVP
@pytest.mark.parametrize("ratio", [1, 3])
def test_the_ratio_scoreboard_catches_a_payload_no_input_offered(tmp_path, ratio):
    """THE KILL, both directions. The correct design must PASS -- a check that
    reddens on everything is no better than one that reddens on nothing -- and
    the constant-payload design must go RED."""
    desc = _ratio_desc(ratio)
    c = rcs.extract_handshake_contract(desc)
    rtl = rcs.emit_rtl(rcs.detect_shape(desc), desc)
    tb = rcs.emit_scoreboard_tb(c)

    good = _simulate(tmp_path / "good", rtl, tb)
    assert "PASS" in good and "FAIL" not in good, good

    broken = rtl.replace("if (in_valid) out_data <= in_data;",
                         "out_data <= 8'd0;")
    assert broken != rtl, "the payload mutation did not apply"
    bad = _simulate(tmp_path / "bad", broken, tb)
    assert "FAIL" in bad and "PASS" not in bad, bad


@_NEEDS_VVP
def test_the_ratio_scoreboard_still_passes_every_ratio_it_composes(tmp_path):
    """The control for the check above: adding a payload check must not cost the
    family the ratios it already served. Compared by MEMBERSHIP of the ratio set."""
    served = set()
    for ratio in (1, 2, 3, 5):
        desc = _ratio_desc(ratio)
        c = rcs.extract_handshake_contract(desc)
        out = _simulate(tmp_path / f"r{ratio}",
                        rcs.emit_rtl(rcs.detect_shape(desc), desc),
                        rcs.emit_scoreboard_tb(c))
        if "PASS" in out and "FAIL" not in out:
            served.add(ratio)
    assert served == {1, 2, 3, 5}


def test_the_ratio_scoreboard_compares_the_output_payload_at_all(tmp_path):
    """Runs everywhere, simulator or not. Before this layer `out_data` appeared
    in the generated TB exactly twice -- a declaration and a port connection --
    and in no comparison, which is precisely how a payload defect stayed
    invisible. It must now be READ, and the summary must not be able to print
    PASS while a payload error stands."""
    tb = rcs.emit_scoreboard_tb(rcs.extract_handshake_contract(_ratio_desc(3)))
    assert "out_data ===" in tb
    assert "grp[" in tb
    # the PASS line must come AFTER the payload branch, so it cannot be reached
    # while data errors stand
    assert tb.index("carried a payload no input offered") < tb.index("$display(\"PASS")


def test_the_payload_check_is_absent_when_the_contract_declares_no_data_port():
    """Fail-closed the other way: a ratio contract with no data ports must not
    grow a check for a payload that does not exist."""
    desc = _ratio_desc(2)
    for line in (" in_data [7:0]: The 8-bit payload accompanying an input event.\n",
                 " out_data [7:0]: The forwarded payload.\n"):
        desc = desc.replace(line, "")
    c = rcs.extract_handshake_contract(desc)
    if c is None or c.kind != "ratio_divider":
        pytest.skip("the trimmed description no longer states a ratio contract")
    tb = rcs.emit_scoreboard_tb(c)
    assert "grp[" not in tb and "===" not in tb
