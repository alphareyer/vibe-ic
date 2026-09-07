"""Unit tests for spec_self_consistency_check.py (pre-RTL spec self-consistency lint).

Anchored to two real benchmark misses this lint exists to catch — from the spec ALONE,
before any RTL exists:
  • VerilogEval-v2 Prob099: interface declares Y1,Y3 but the body says "Y2 and Y4"
    (garbled spec). `body-port-gap`.
  • CVDP arbiter: a spec asserting both synchronous and asynchronous reset.
    `reset-mode-contradiction`.
And the key NON-finding: Prob085-style "async areset + synchronous load/enable" must
NOT trip the reset contradiction (synchronous qualifies a control signal, not the reset).
"""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / 'spec_self_consistency_check.py'
assert SCRIPT.exists()


def run(tmp_path, spec_text, ext='.md', *extra):
    spec = tmp_path / f'spec{ext}'
    spec.write_text(spec_text)
    jf = tmp_path / 'out.json'
    res = subprocess.run(
        [sys.executable, str(SCRIPT), '--json', str(jf), *extra, str(spec)],
        capture_output=True, text=True)
    findings = json.loads(jf.read_text())['findings'] if jf.exists() else []
    return res, findings


def codes(findings):
    return {f['code'] for f in findings}


# ---- body-port-gap : the Prob099 garbled-spec signature, pre-RTL -----------
PROB099 = """\
Implement a module named TopModule with the following interface.
 - input  y (6 bits)
 - input  w
 - output Y1
 - output Y3
Consider the state machine shown below ... The module should implement the
next-state signals Y2 and Y4.
"""


def test_body_port_gap_fires_on_garbled_spec(tmp_path):
    res, f = run(tmp_path, PROB099)
    assert 'body-port-gap' in codes(f)
    # it is a WARN, so without --strict the gate still PASSes (exit 0)
    assert res.returncode == 0
    msg = next(x['message'] for x in f if x['code'] == 'body-port-gap')
    assert 'Y2' in msg and 'Y4' in msg


def test_body_port_gap_strict_fails(tmp_path):
    res, f = run(tmp_path, PROB099, '.md', '--strict')
    assert res.returncode == 1  # WARN fails under --strict


def test_body_port_gap_quiet_when_self_consistent(tmp_path):
    # numbered family Y1,Y2,Y3 all declared and only those referenced → no gap
    spec = """\
 - input  clk
 - output Y1
 - output Y2
 - output Y3
Drive Y1, Y2 and Y3 from the state vector.
"""
    res, f = run(tmp_path, spec)
    assert 'body-port-gap' not in codes(f)
    assert res.returncode == 0


# ---- reset-mode-contradiction : the CVDP lesson ----------------------------
def test_reset_mode_contradiction_fires(tmp_path):
    spec = """\
 - input clk
 - input reset
 - output q
The design uses a synchronous reset. Note elsewhere: the reset is asynchronous.
"""
    res, f = run(tmp_path, spec)
    assert 'reset-mode-contradiction' in codes(f)
    assert res.returncode == 1  # ERROR fails the gate


def test_reset_polarity_contradiction_fires(tmp_path):
    spec = """\
 - input clk
 - input reset
 - output q
An active-high reset is used here, but the reset is active-low per the table.
"""
    res, f = run(tmp_path, spec)
    assert 'reset-polarity-contradiction' in codes(f)


# ---- the critical NON-finding: Prob085 (async reset + synchronous load) ----
def test_async_reset_with_synchronous_load_is_not_a_contradiction(tmp_path):
    spec = """\
 - input  areset
 - input  load
asynchronous positive edge triggered areset, synchronous active high
signals load, and enable.
  (1) areset: Resets shift register to zero.
  (2) load: Loads shift register with data[3:0] instead of shifting.
"""
    res, f = run(tmp_path, spec)
    assert 'reset-mode-contradiction' not in codes(f)
    assert res.returncode == 0


# ---- duplicate declared port ----------------------------------------------
def test_duplicate_port(tmp_path):
    spec = """\
 - input  clk
 - output q
 - output q
"""
    res, f = run(tmp_path, spec)
    assert 'duplicate-port' in codes(f)


# ---- no-output-port : the Prob031 garbled-spec signature -------------------
def test_no_output_port_fires(tmp_path):
    # Prob031: a D flip-flop whose output `q` was mis-declared as `- input q`,
    # leaving the interface with three inputs and ZERO outputs.
    spec = """\
 - input clk
 - input d
 - input q
The module should implement a single D flip-flop.
"""
    res, f = run(tmp_path, spec)
    assert 'no-output-port' in codes(f)


def test_no_output_port_quiet_when_output_present(tmp_path):
    spec = """\
 - input clk
 - input d
 - output q
A D flip-flop.
"""
    res, f = run(tmp_path, spec)
    assert 'no-output-port' not in codes(f)


# ---- clean spec passes -----------------------------------------------------
def test_clean_spec_passes(tmp_path):
    spec = """\
Implement TopModule.
 - input  a
 - input  b
 - output out
out is the AND of a and b.
"""
    res, f = run(tmp_path, spec)
    assert f == []
    assert res.returncode == 0


# ---- no spec file -> exit 2 ------------------------------------------------
def test_missing_spec_exits_2(tmp_path):
    res = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path / 'nope.md')],
                         capture_output=True, text=True)
    assert res.returncode == 2


# ---- handshake-consume-undeclared : the RTLLM radix2_div signature ---------
# A valid/req spec whose behaviour gates on the RESULT being CONSUMED but that
# declares no ready/ack/consume input has a half-declared interface. Detected
# from the spec ALONE (behaviour prose vs declared interface), never the TB.
RADIX2_DIV = """\
Implement a simplified radix-2 divider on 8-bit integers.
Input ports:
    clk: Clock signal used for synchronous operation.
    rst: The reset signal.
    sign: signed (1) or unsigned (0).
    dividend: 8-bit dividend.
    divisor: 8-bit divisor.
    opn_valid: 1-bit indicates that a valid operation request is present.
Output ports:
    res_valid: 1-bit output signal indicating the result is valid and ready.
    result: 16-bit remainder in the upper 8 bits and the quotient in the lower 8.
Implementation:
    res_valid is managed based on the reset signal, the counter, and whether
    the result has been consumed.
"""


def test_handshake_consume_undeclared_fires_on_radix2_div(tmp_path):
    res, f = run(tmp_path, RADIX2_DIV)
    assert 'handshake-consume-undeclared' in codes(f)
    # WARN, so the gate still PASSes (exit 0) without --strict
    assert res.returncode == 0
    msg = next(x['message'] for x in f if x['code'] == 'handshake-consume-undeclared')
    assert 'consume' in msg.lower() or 'ready' in msg.lower()


def test_handshake_consume_undeclared_strict_fails(tmp_path):
    res, f = run(tmp_path, RADIX2_DIV, '.md', '--strict')
    assert res.returncode == 1


# The completeness proof: a harness-less Phase-1 doc of the SAME SHAPE (no TB
# anywhere) must get the SAME verdict — the rule is not adapter-trapped.
def test_handshake_consume_undeclared_fires_on_generic_multicycle_doc(tmp_path):
    spec = """\
Design a serial multiply-accumulate engine.
Input ports:
    clk: clock
    rst: reset
    a_valid: asserted when a new operand pair is presented
    a: 16-bit operand
    b: 16-bit operand
Output ports:
    result_valid: asserted when the accumulated result is available
    result: 32-bit accumulated result
Implementation:
    The engine iterates over 16 cycles. result_valid stays high until the
    result has been consumed, after which a new operation may begin.
"""
    res, f = run(tmp_path, spec)
    assert 'handshake-consume-undeclared' in codes(f)


# NEGATIVE 1: a free-running clock divider (multi-cycle, no handshake, no
# consumption) must NOT fire — the rule is not "any multi-cycle design".
def test_handshake_consume_free_running_divider_no_fire(tmp_path):
    spec = """\
Divide the input clock by 3 with a 50% duty cycle.
Input ports:
    clk: input clock
    rst: reset
Output ports:
    clk_out: output clock at one third the input frequency
Implementation:
    On each rising edge advance a modulo-3 counter and toggle clk_out.
"""
    res, f = run(tmp_path, spec)
    assert 'handshake-consume-undeclared' not in codes(f)


# NEGATIVE 2: a COMPLETE valid+ready handshake (ready input declared) must NOT
# fire — this is the mutation that proves the guard checks the interface, not
# merely the presence of the word "consumed".
def test_handshake_consume_full_handshake_no_fire(tmp_path):
    spec = """\
Design a divider with a complete handshake.
Input ports:
    clk: clock
    rst: reset
    opn_valid: request valid
    dividend: 8-bit
    divisor: 8-bit
    res_ready: downstream asserts this when it consumes the result
Output ports:
    res_valid: result valid
    result: 16-bit
Implementation:
    res_valid stays high until the result has been consumed (res_ready high).
"""
    res, f = run(tmp_path, spec)
    assert 'handshake-consume-undeclared' not in codes(f)


# NEGATIVE 3: a streaming valid interface with no consumption semantics must
# NOT fire — a fire-and-forget valid without back-pressure is legitimate.
def test_handshake_consume_streaming_no_fire(tmp_path):
    spec = """\
Design a streaming FIR filter.
Input ports:
    clk: clock
    rst: reset
    in_valid: a new sample is valid this cycle
    sample: 12-bit input sample
Output ports:
    out_valid: a filtered sample is valid this cycle
    y: 12-bit filtered output
Implementation:
    Each valid input sample produces one filtered output sample after latency.
"""
    res, f = run(tmp_path, spec)
    assert 'handshake-consume-undeclared' not in codes(f)


# ---- unconstrained-value-contradiction : the vibe-ic#2168 signature --------
# One Phase-1 L2 document stated "66 cycles after the accepted command" as
# observable FSM behaviour AND, in its own "not constrained by L2" list, declared
# the latency cycle count unconstrained. Both citations are in the SAME document,
# and nothing saw it: every other rule here compares the interface against the
# body, while this contradiction is between two PROSE sections.
#
# The fixture reproduces the real document's SHAPE (bilingual headings, the ❌
# bullet list) without carrying any design-specific content beyond the numbers
# that make the contradiction observable.
CONTRADICTED_LATENCY = """\
Implement a hash engine with a register interface.
 - input  clk
 - input  reset_n
 - output ready

## Control state machine (externally observable)

- On reset the state machine is in IDLE (ready = 1).
- The FSM transitions from BUSY to IDLE when the block computation completes,
  66 cycles after the accepted command (ready = 1).

## Not constrained by this layer

- The round function hierarchy is not specified.
- The latency cycle count is not specified (any number of cycles that produces
  the standard result is acceptable).
"""


def test_unconstrained_value_contradiction_fires(tmp_path):
    res, f = run(tmp_path, CONTRADICTED_LATENCY)
    assert 'unconstrained-value-contradiction' in codes(f)
    # WARN, so the gate still PASSes (exit 0) without --strict
    assert res.returncode == 0
    msg = next(x['message'] for x in f
               if x['code'] == 'unconstrained-value-contradiction')
    # the finding must name BOTH citations by line, or a reader cannot resolve it
    assert '66 cycles' in msg
    assert 'not constrained' in msg.lower()


def test_unconstrained_value_contradiction_strict_refuses(tmp_path):
    """The REFUSED direction the issue asks for: a document carrying the
    contradiction does not get a clean exit under --strict."""
    res, _ = run(tmp_path, CONTRADICTED_LATENCY, '.md', '--strict')
    assert res.returncode == 1


def test_unconstrained_value_contradiction_names_every_site(tmp_path):
    """The real document stated the number TWICE outside the disclaimer. Both
    sites must be named — resolving one and leaving the other still leaves a
    document that contradicts itself."""
    spec = CONTRADICTED_LATENCY.replace(
        "## Not constrained by this layer",
        "Software polls the status register until ready = 1 (66 cycles later).\n\n"
        "## Not constrained by this layer")
    _, f = run(tmp_path, spec)
    hits = [x for x in f if x['code'] == 'unconstrained-value-contradiction']
    assert len(hits) == 2, [x['message'] for x in hits]


# ACCEPTED direction 1: the value is stated in ONE place only — no disclaimer.
def test_unconstrained_value_no_disclaimer_is_accepted(tmp_path):
    spec = CONTRADICTED_LATENCY.split("## Not constrained by this layer")[0]
    res, f = run(tmp_path, spec)
    assert 'unconstrained-value-contradiction' not in codes(f)
    assert res.returncode == 0


# ACCEPTED direction 2: the disclaimer stands alone — the document never states
# an exact count. This is the single-declaration document the issue calls correct.
def test_unconstrained_declared_but_never_valued_is_accepted(tmp_path):
    spec = CONTRADICTED_LATENCY.replace(
        "  66 cycles after the accepted command (ready = 1).",
        "  the block computation completes (ready = 1).")
    res, f = run(tmp_path, spec)
    assert 'unconstrained-value-contradiction' not in codes(f)
    assert res.returncode == 0


# NEGATIVE 1 (hedged): "~66 cycles" already says the number does not bind.
def test_unconstrained_value_hedged_no_fire(tmp_path):
    spec = CONTRADICTED_LATENCY.replace("  66 cycles after", "  ~66 cycles after")
    _, f = run(tmp_path, spec)
    assert 'unconstrained-value-contradiction' not in codes(f)


# NEGATIVE 2 (bounded): a ceiling is not an exact value, and a disclaimer that
# grants a bound is consistent with the bound being stated elsewhere.
def test_unconstrained_value_bounded_no_fire(tmp_path):
    spec = CONTRADICTED_LATENCY.replace("  66 cycles after", "  at most 66 cycles after")
    _, f = run(tmp_path, spec)
    assert 'unconstrained-value-contradiction' not in codes(f)


# NEGATIVE 3 (illustrative): the number is labelled the reference's own figure.
def test_unconstrained_value_illustrative_no_fire(tmp_path):
    spec = CONTRADICTED_LATENCY.replace(
        "  66 cycles after the accepted command (ready = 1).",
        "  66 cycles after the accepted command in the reference (ready = 1).")
    _, f = run(tmp_path, spec)
    assert 'unconstrained-value-contradiction' not in codes(f)


# NEGATIVE 4 (design-freedom menu): a "choices you may take" list is the OPPOSITE
# shape — each option legitimately carries its own value. This is the mutation
# that proves the rule keys on a NON-CONSTRAINT clause, not on any bullet list.
def test_design_freedom_menu_is_not_a_disclaimer(tmp_path):
    spec = """\
 - input  clk
 - output ready

## Control state machine

- The FSM returns to IDLE 66 cycles after the accepted command.

## Design freedom

- Iterative single-cycle round: the cycle count is 66 per block, low area.
- Unrolled round: the cycle count drops to between 1 and 64, larger area.
"""
    _, f = run(tmp_path, spec)
    assert 'unconstrained-value-contradiction' not in codes(f)


# NEGATIVE 5 (citation): a document QUOTING another document's disclaimer by
# `name:line` is reporting it, not declaring it. Measured: this was the one and
# only false positive over the published corpus — a campaign report that quoted
# the very L2 bullet this rule exists to catch.
def test_quoted_disclaimer_with_line_citation_no_fire(tmp_path):
    spec = """\
 - input  clk
 - output ready

Post-mortem: the counter settled in 12 cycles from reset with no mismatch.

| `pipelining` | `FREE` | `L2_architecture:89` — "the latency cycle count is not specified" |
"""
    _, f = run(tmp_path, spec)
    assert 'unconstrained-value-contradiction' not in codes(f)


# The rule is language-shaped, not chip-shaped: the published corpus writes its
# disclaimer lists in Chinese, and the same document states the value in English.
def test_unconstrained_value_contradiction_zh_disclaimer(tmp_path):
    spec = """\
 - input  clk
 - output ready

- The FSM transitions from BUSY to IDLE 66 cycles after the accepted command.

## 不在 L2 約束的事

- ❌ 不指定 W memory 是 shift register / RAM
- ❌ 不指定 latency cycle 數
"""
    _, f = run(tmp_path, spec)
    assert 'unconstrained-value-contradiction' in codes(f)


# NEGATIVE 6 (unit named, not counted): a disclaimer that MENTIONS a unit noun
# without declaring HOW MANY ("reserved bit behaviour is not specified") does not
# put every bit-count in the document in conflict. This is the mutation that
# proves the join key is a COUNTED quantity, not any unit word: it was measured as
# a false positive on a real register-map document before the count marker was
# required.
def test_unit_mentioned_but_not_counted_no_fire(tmp_path):
    spec = """\
 - input  clk
 - output ready

- The digest register file holds 256 bits.

## Not constrained by this layer

- Reserved bit behaviour is not specified (reads may return 0 or a fixed value).
"""
    _, f = run(tmp_path, spec)
    assert 'unconstrained-value-contradiction' not in codes(f)


# NEGATIVE 7 (the disclaimer's own parenthetical): a non-constraint clause is
# allowed to explain itself with a number — "the latency cycle count is not
# specified (bit-serial is inherently 32 cycles per 32-bit operation)". The clause
# does not contradict itself, and a rule that read its own disclaimer back as a
# binding statement would fire on every self-explaining exclusion in the corpus.
def test_number_inside_the_disclaimer_itself_no_fire(tmp_path):
    spec = """\
 - input  clk
 - output ready

- The core is bit-serial and reports completion through ready.

## Not constrained by this layer

- The latency cycle count is not specified (bit-serial is inherently
  32 cycles per 32-bit operation, so no separate figure is needed).
"""
    _, f = run(tmp_path, spec)
    assert 'unconstrained-value-contradiction' not in codes(f)
