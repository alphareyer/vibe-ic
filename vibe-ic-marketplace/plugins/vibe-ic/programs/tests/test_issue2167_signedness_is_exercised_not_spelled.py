"""#2167 — the COARSE `signedness` obligation must be discharged by BEHAVIOUR,
not by the literal word "signed".

#2152 closed the FINE `signed_operand` obligation, whose coverage token was an
English noun scraped from the prose. The COARSE `signedness` kind in
`spec_coverage_check` is the same defect one level up: its coverage token is the
literal spec WORD ("signed" / "unsigned" / "two's complement"), it is
RTL-corroborated by `_rtl_declares_signed` and therefore BLOCKS, and
`attribute_coverage` credited it by a whole-word lexical match in the testbench.

Measured on the pristine tree, one design INPUT and one RTL, two testbenches
differing ONLY in whether they happen to spell the word:

    reg [31:0] a, b;          (directed hex vectors)  -> covered=False -> BLOCKS
    reg signed [31:0] a, b;   (the same vectors)      -> covered=True  -> passes

So a directed-vector testbench that separates the signed from the unsigned
reading — SLT vs SLTU on 32'hFFFFFFFF, SRA on 32'h80000000 — was rejected while a
spelling bought acceptance. The gate was refusing for the wrong reason.

The contract this file pins:

  1. a testbench that DRIVES a literal whose signed and unsigned readings differ
     (negative, or MSB set at the operand's declared width) into a signal the RTL
     structurally treats as a signed operand, AND checks the DUT's response with
     an equality assertion, COVERS the signedness obligation — with the word
     absent from the testbench;
  2. the literal-word path is KEPT: `reg signed` in a testbench is real
     structural evidence, so the change is purely ADDITIVE and nothing that was
     covered before becomes uncovered;
  3. §4.05 no-leak, three ways — an MSB-clear stimulus, a stimulus with nothing
     asserted, and a narrow literal zero-extended into a wide signed operand all
     STILL GAP and still BLOCK;
  4. a magnitude COMPARISON is not a drive, and a 1-bit operand is not a
     signedness exercise.

chip-AGNOSTIC: generic signedness prose and generic Verilog; no design, vendor,
node or benchmark literal.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROG = Path(__file__).resolve().parents[1]
if str(_PROG) not in sys.path:
    sys.path.insert(0, str(_PROG))

import spec_coverage_check as COV            # noqa: E402


# ── the design INPUT: a prose sentence that mentions signedness ───────────────
SPEC = (
    "Implement an arithmetic unit with two 32-bit operands a and b, a 6-bit\n"
    "opcode aluc and a 32-bit result r. The module assigns the input operands\n"
    "to the signed wires. The set-less-than operation compares the operands as\n"
    "signed values and the set-less-than-unsigned operation compares them as\n"
    "unsigned values. The arithmetic-shift-right operation preserves the sign.\n"
)

# ── the authored RTL: it structurally declares its signed operands ────────────
RTL_SIGNED = """module dut_top(input [31:0] a, input [31:0] b, input [5:0] aluc,
                output [31:0] r);
  wire signed [31:0] sa = a;
  wire signed [31:0] sb = b;
  reg [31:0] res;
  always @(*) begin
    case (aluc)
      6'b100000: res = a + b;
      6'b101010: res = {31'b0, sa < sb};
      6'b101011: res = {31'b0, a < b};
      6'b000011: res = sa >>> b[4:0];
      default:   res = 32'b0;
    endcase
  end
  assign r = res;
endmodule
"""

# An RTL with no `signed` anywhere — the obligation is NOT corroborated there.
RTL_UNSIGNED = """module dut_top(input [31:0] a, input [31:0] b, input [5:0] aluc,
                output [31:0] r);
  reg [31:0] res;
  always @(*) begin
    case (aluc)
      6'b100000: res = a + b;
      default:   res = 32'b0;
    endcase
  end
  assign r = res;
endmodule
"""

_TB_HEAD = """`timescale 1ns/1ps
module tb;
  reg {sign}[31:0] a, b;
  reg [5:0] aluc;
  wire [31:0] r;
  integer errors = 0;
  dut_top dut(.a(a), .b(b), .aluc(aluc), .r(r));
  initial begin
"""
_TB_TAIL = """    if (errors == 0) $display("done");
    $finish;
  end
endmodule
"""

# (1) a faithful directed-vector signedness testbench. It NEVER spells the word:
#     the two comparisons only differ under the signed reading, and the
#     arithmetic shift only differs under it too.
TB_EXERCISES = _TB_HEAD.format(sign="") + """    aluc = 6'b101010; a = 32'hFFFFFFFF; b = 32'h00000001;
    #1 if (r !== 32'h00000001) errors = errors + 1;
    aluc = 6'b101011; a = 32'hFFFFFFFF; b = 32'h00000001;
    #1 if (r !== 32'h00000000) errors = errors + 1;
    aluc = 6'b000011; a = 32'h80000000; b = 32'h00000004;
    #1 if (r !== 32'hF8000000) errors = errors + 1;
""" + _TB_TAIL

# (2) the SAME testbench with the word present in the declaration.
TB_SPELLS_IT = _TB_HEAD.format(sign="signed ") + """    aluc = 6'b101010; a = 32'hFFFFFFFF; b = 32'h00000001;
    #1 if (r !== 32'h00000001) errors = errors + 1;
""" + _TB_TAIL

# (3) no-leak: every stimulus is MSB-CLEAR, so nothing distinguishes the signed
#     from the unsigned reading, even though the DUT result is asserted.
TB_MSB_CLEAR = _TB_HEAD.format(sign="") + """    aluc = 6'b100000; a = 32'h00000002; b = 32'h00000003;
    #1 if (r !== 32'h00000005) errors = errors + 1;
    aluc = 6'b101010; a = 32'h00000001; b = 32'h00000002;
    #1 if (r !== 32'h00000001) errors = errors + 1;
""" + _TB_TAIL

# (4) no-leak: an MSB-set stimulus with NOTHING asserted about the DUT.
TB_DRIVE_NO_CHECK = _TB_HEAD.format(sign="") + """    aluc = 6'b101010; a = 32'hFFFFFFFF; b = 32'h00000001;
    #1 $display("r=%h", r);
    aluc = 6'b000011; a = 32'h80000000; b = 32'h00000004;
    #1 $display("r=%h", r);
""" + _TB_TAIL

# (5) a testbench whose nets are named differently from the DUT ports and are
#     bound to them by named port connections.
TB_ALIASED = """`timescale 1ns/1ps
module tb;
  reg [31:0] op_lhs, op_rhs;
  reg [5:0] ctl;
  wire [31:0] result;
  integer errors = 0;
  dut_top dut(.a(op_lhs), .b(op_rhs), .aluc(ctl), .r(result));
  initial begin
    ctl = 6'b101010; op_lhs = 32'hFFFFFFFF; op_rhs = 32'h00000001;
    #1 if (result !== 32'h00000001) errors = errors + 1;
    $finish;
  end
endmodule
"""

# (6) a testbench that drives a NEGATIVE DECIMAL literal.
TB_NEGATIVE_DECIMAL = _TB_HEAD.format(sign="") + """    aluc = 6'b101010; a = -1; b = 32'h00000001;
    #1 if (r !== 32'h00000001) errors = errors + 1;
""" + _TB_TAIL

# (7) no-leak: a NARROW literal zero-extended into a WIDE signed operand. Its
#     own size has the MSB set, but at the operand's 32-bit width it does not,
#     so the signed and unsigned readings of the driven value are identical.
TB_NARROW_LITERAL = _TB_HEAD.format(sign="") + """    aluc = 6'b101010; a = 8'hFF; b = 8'h01;
    #1 if (r !== 32'h00000000) errors = errors + 1;
""" + _TB_TAIL

# (8) no-leak: a magnitude COMPARISON against an MSB-set literal is not a drive.
#     Three shapes: the ordinary `if (a <= LIT) stmt;`, the degenerate empty
#     statement `if (a <= LIT);`, and the one the trailing `;` alone CANNOT
#     separate from an assignment — a for-loop CONDITION, which really does end
#     in `;`. Only paren nesting tells them apart.
TB_COMPARISON_ONLY = _TB_HEAD.format(sign="") + """    integer k;
    aluc = 6'b100000; a = 32'h00000002; b = 32'h00000003;
    #1 if (a <= 32'hFFFFFFFF) errors = errors + 0;
    #1 if (b <= 32'h80000000);
    for (k = 0; a <= 32'hFFFFFFFF; k = k + 1) begin
      if (k > 1) k = 99;
    end
    #1 if (r !== 32'h00000005) errors = errors + 1;
""" + _TB_TAIL


def _signedness(rtl: str, tb: str) -> dict:
    """The single coarse `signedness` checklist row for this (spec, rtl, tb)."""
    rep = COV.run({"user_prompt": SPEC}, rtl, tb, None, True)
    rows = [it for it in rep["items"] if it["kind"] == "signedness"]
    assert len(rows) == 1, f"expected exactly one signedness row, got {rows}"
    return {"row": rows[0], "report": rep}


# ── 1. the inversion ─────────────────────────────────────────────────────────
def test_a_directed_signed_testbench_is_covered_without_spelling_the_word():
    r = _signedness(RTL_SIGNED, TB_EXERCISES)["row"]
    assert "signed" not in COV._SRC.strip_comments(TB_EXERCISES).lower(), (
        "the fixture must not contain the word, or the test proves nothing")
    assert r["covered"] is True, (
        f"a directed-vector signedness testbench must COVER the obligation "
        f"without spelling the word; note={r['coverage_note']!r}")


def test_the_faithful_testbench_no_longer_blocks_on_signedness():
    got = _signedness(RTL_SIGNED, TB_EXERCISES)
    blocking = [it["kind"] for it in got["report"]["items"]
                if it["covered"] is False and it.get("block_eligible", True)]
    assert "signedness" not in blocking, (
        f"signedness must not be a blocking gap for this testbench; "
        f"blocking kinds were {sorted(set(blocking))}")


def test_a_negative_decimal_literal_is_a_signedness_stimulus():
    r = _signedness(RTL_SIGNED, TB_NEGATIVE_DECIMAL)["row"]
    assert r["covered"] is True, (
        f"driving a negative literal into a signed operand and asserting the "
        f"DUT result exercises signedness; note={r['coverage_note']!r}")


def test_a_testbench_net_bound_to_a_signed_port_counts():
    r = _signedness(RTL_SIGNED, TB_ALIASED)["row"]
    assert r["covered"] is True, (
        f"a testbench net bound to a signed DUT port by a named connection "
        f"stands in for that port; note={r['coverage_note']!r}")


def test_the_drive_target_set_is_the_one_the_verdict_uses():
    """The alias hop is exposed, so a report can show the SAME set the gate
    judges on instead of rebuilding it by hand and disagreeing."""
    tb = COV._SRC.strip_comments(TB_ALIASED)
    targets, dut_tied = COV._signedness_drive_targets(
        tb, COV._rtl_signed_operand_widths(RTL_SIGNED),
        COV._rtl_port_name_set(RTL_SIGNED, TB_ALIASED))
    assert targets.get("op_lhs") == 32, (
        f"`.a(op_lhs)` must carry port `a`'s 32-bit width onto the TB net; "
        f"got {targets}")
    assert "result" in dut_tied, (
        f"`.r(result)` must make `result` a DUT-tied assertion operand; "
        f"got {sorted(dut_tied)}")


# ── 1b. the gate-level flip: BLOCKED -> not blocked, and BLOCKED -> BLOCKED ──
# The assertions above are at the ITEM level. This pair is at the REPORT level:
# a minimal design whose ONLY blocking gap is the signedness obligation, so
# `report["blocked"]` itself is the thing that moves — the prove-by-run that the
# gate really does stop the flow, and really does stop it for the RIGHT reason.
MIN_SPEC = (
    "The unit compares its two operands as signed values.\n"
    "Input ports:\n"
    "    a: a 8-bit input operand\n"
    "    b: a 8-bit input operand\n"
    "Output ports:\n"
    "    lt: a 1-bit output that is high when a is less than b\n"
)
MIN_RTL = """module cmp_top(input [7:0] a, input [7:0] b, output lt);
  wire signed [7:0] sa = a;
  wire signed [7:0] sb = b;
  assign lt = (sa < sb);
endmodule
"""
# 8'hFF is -1 signed and 255 unsigned, so `lt` differs between the two readings.
MIN_TB_EXERCISES = """`timescale 1ns/1ps
module tb;
  reg [7:0] a, b;
  wire lt;
  integer errors = 0;
  cmp_top dut(.a(a), .b(b), .lt(lt));
  initial begin
    a = 8'hFF; b = 8'h01; #1 if (lt !== 1'b1) errors = errors + 1;
    a = 8'h01; b = 8'h02; #1 if (lt !== 1'b1) errors = errors + 1;
    $finish;
  end
endmodule
"""
# the same TB with every stimulus MSB-CLEAR: nothing separates the readings.
MIN_TB_MSB_CLEAR = MIN_TB_EXERCISES.replace(
    "a = 8'hFF; b = 8'h01; #1 if (lt !== 1'b1) errors = errors + 1;",
    "a = 8'h03; b = 8'h01; #1 if (lt !== 1'b0) errors = errors + 1;")


def test_the_gate_stops_blocking_a_testbench_that_exercises_signedness():
    rep = COV.run({"user_prompt": MIN_SPEC}, MIN_RTL, MIN_TB_EXERCISES, None, True)
    blocking = [it["kind"] for it in rep["items"]
                if it["covered"] is False and it.get("block_eligible", True)]
    assert rep["blocked"] is False, (
        f"the only blocking gap on this design is the signedness obligation and "
        f"this testbench exercises it, so --strict must not block; still "
        f"blocking on {sorted(set(blocking))}")


def test_the_gate_STILL_blocks_a_testbench_that_does_not_exercise_it():
    """The other direction, at the same level: the gate must still stop the
    flow when the obligation really is uncovered. A check that cannot block is
    not a check."""
    rep = COV.run({"user_prompt": MIN_SPEC}, MIN_RTL, MIN_TB_MSB_CLEAR, None, True)
    blocking = [it["kind"] for it in rep["items"]
                if it["covered"] is False and it.get("block_eligible", True)]
    assert rep["blocked"] is True and "signedness" in blocking, (
        f"an MSB-clear testbench leaves the signedness obligation uncovered and "
        f"the gate must still BLOCK; blocked={rep['blocked']} "
        f"blocking={sorted(set(blocking))}")


# ── 2. the literal-word path is kept (green on BOTH sides, deliberately) ─────
def test_a_testbench_that_declares_reg_signed_is_still_covered():
    r = _signedness(RTL_SIGNED, TB_SPELLS_IT)["row"]
    assert r["covered"] is True, (
        "the change is ADDITIVE: a testbench that declares `reg signed` is real "
        "structural evidence and must stay covered")


# ── 3. §4.05 no-leak (green on BOTH sides, deliberately) ────────────────────
def test_an_msb_clear_testbench_still_gaps_and_still_blocks():
    got = _signedness(RTL_SIGNED, TB_MSB_CLEAR)
    r = got["row"]
    assert r["covered"] is False, (
        f"nothing in this testbench separates the signed from the unsigned "
        f"reading; note={r['coverage_note']!r}")
    assert r.get("block_eligible", True) is True, (
        "the RTL declares signed, so the uncovered obligation must still block")


def test_a_stimulus_with_nothing_asserted_is_not_coverage():
    r = _signedness(RTL_SIGNED, TB_DRIVE_NO_CHECK)["row"]
    assert r["covered"] is False, (
        f"wiggling an input without checking the DUT has not verified the "
        f"signed reading; note={r['coverage_note']!r}")


def test_a_narrow_literal_zero_extended_into_a_wide_operand_is_not_msb_set():
    r = _signedness(RTL_SIGNED, TB_NARROW_LITERAL)["row"]
    assert r["covered"] is False, (
        f"8'hFF driven into a 32-bit signed operand is +255 under BOTH "
        f"readings; note={r['coverage_note']!r}")


def test_a_magnitude_comparison_is_not_a_drive():
    r = _signedness(RTL_SIGNED, TB_COMPARISON_ONLY)["row"]
    assert r["covered"] is False, (
        f"an `if (...)` / `for (...;  x <= LIT; ...)` comparison is not a "
        f"stimulus; note={r['coverage_note']!r}")


def test_a_loop_condition_is_not_a_drive_even_though_it_ends_in_a_semicolon():
    """The one shape the trailing `;` cannot reject on its own."""
    assert COV._inside_parens("for (k = 0; a <= 32'hFFFFFFFF; k = k + 1)", 12), (
        "a for-loop CONDITION sits inside an unclosed `(` and must be rejected")
    assert not COV._inside_parens("  #1 a = 32'hFFFFFFFF;", 7), (
        "a statement-level drive must NOT be read as inside parens")
    assert not COV._inside_parens(
        "dut(.a(a), .b(b));\n  a = 32'hFFFFFFFF;", 22), (
        "a balanced instantiation before the drive must not shift the depth")


def test_an_unsigned_design_keeps_its_advisory_downgrade():
    """The corroboration branch is untouched: an RTL with no `signed` anywhere
    leaves the obligation ADVISORY, so it never blocks whatever the testbench."""
    r = _signedness(RTL_UNSIGNED, TB_MSB_CLEAR)["row"]
    assert r.get("block_eligible", True) is False, (
        "an RTL that declares no `signed` gives the prose mention no structural "
        "backing; the obligation must stay advisory")


# ── 4. the unit truth table of the readings-differ predicate ────────────────
def test_literal_readings_differ_truth_table():
    f = COV._literal_readings_differ
    cases = [
        # (literal, operand width, expected)
        ("32'hFFFFFFFF", 32, True),    # -1 signed, 4294967295 unsigned
        ("32'h80000000", 32, True),    # INT_MIN vs 2^31
        ("32'h7FFFFFFF", 32, False),   # same under both readings
        ("32'h00000001", 32, False),
        ("-1",           32, True),    # a negative literal is unambiguous
        ("-5",         None, True),    # ... even with no known width
        ("8'hFF",         8, True),
        ("8'hFF",        32, False),   # zero-extended: +255 under both
        ("32'hFFFFFFFF",  8, True),    # truncated to 8'hFF: MSB still set
        ("255",           8, True),    # unsized decimal, width from the RTL
        ("255",          32, False),
        ("255",        None, False),   # unsized with NO width: not judged
        ("1'b1",          1, False),   # a 1-bit operand is degenerate
        ("2'b10",         2, True),
        ("32'hxxxxxxxx", 32, False),   # x/z digits read as 0
    ]
    wrong = [(lit, w, exp, f(lit, w)) for lit, w, exp in cases
             if f(lit, w) is not exp]
    assert not wrong, f"readings-differ mismatches (lit, width, exp, got): {wrong}"


def test_declared_widths_are_read_from_the_rtl_and_never_guessed():
    w = COV._rtl_signed_operand_widths(RTL_SIGNED)
    assert w.get("sa") == 32 and w.get("a") == 32, w
    param = ("module m(input signed [WIDTH-1:0] x, output [7:0] y);\n"
             "endmodule\n")
    assert COV._rtl_signed_operand_widths(param).get("x") is None, (
        "a parameterised range must yield NO width rather than a guessed one")


# ── #2167 follow-up (lane cz2167b): the `_NOT_PROSE` claim, made falsifiable ──
#
# `prose_polarity_consulted_check` flagged `_rtl_declared_widths` as a prose
# extractor that writes a value into a record without asking whether the
# sentence DENIES it. The disposition filed for it is a `_NOT_PROSE` entry: the
# input is the Verilog DECLARATION grammar, in which there is no form that
# denies a declaration — you cannot write "a is NOT eight bits wide" in Verilog.
#
# That claim is only worth its place if it is CHECKED rather than asserted, and
# as first drafted it was FALSE. `_SRC.strip_comments` blanks comments only, so
# an English sentence inside a Verilog STRING reached the regex and minted the
# declaration it denies. The tests below are the falsifier for both halves: the
# grammar half (a declaration has no negation form, and absence is how the
# function reports what it cannot read) and the reach half (no sentence gets to
# the regex from ANY position it can physically occupy in a Verilog file).
#
# chip-AGNOSTIC: `_prose_polarity`'s own vocabulary and generic Verilog.

import _prose_polarity as _PP                # noqa: E402

#: `_prose_polarity`'s denial vocabulary, spelled out. Both tiers, including the
#: five CJK spellings. Each is asserted to BE a denial in prose below, so the
#: sweep cannot silently degrade into planting words that mean nothing.
_DENIAL_TOKENS = (
    "not", "no", "none", "without", "excluding", "excluded", "never", "non-",
    "非", "无", "無", "不", "否",
    "removed", "obsolete", "superseded", "n/a", "inapplicable", "deprecated",
    "no longer", "does not apply",
)

#: A DECLARATION-SHAPED payload: read as code it mints `plant_sig -> 8`.
_PAYLOAD = "input [7:0] plant_sig;"

_HOST_RTL = """module alu (
  input  wire signed [31:0] a,
  input  wire signed [31:0] b,
  output reg  signed [31:0] y
);
{planted}
  always @(*) y = a + b;
endmodule
"""


def _positions(sentence):
    """Every position a SENTENCE can physically occupy in a Verilog file."""
    return {
        "line_comment": "  // " + sentence,
        "block_comment": "  /* " + sentence + " */",
        "string_literal": '  initial $display("' + sentence + '");',
        # The two unterminated forms: the blanker leaves an unterminated opener
        # alone by design, so they are where a strip most plausibly leaks.
        "unterminated_block_comment": "  /* " + sentence,
        "unterminated_string": '  initial $display("' + sentence,
    }


def _prose_reaches(fn):
    """`[(token, position)]` for every denial sentence that MOVES `fn`'s answer.

    Empty is the claim. Non-empty is the #706/#711 defect: a value read out of a
    sentence and written into a record as a declaration.
    """
    base = fn(_HOST_RTL.format(planted=""))
    leaks = []
    for tok in _DENIAL_TOKENS:
        sentence = "this port is %s used: %s" % (tok, _PAYLOAD)
        assert _PP.NEGATION_RE.search(sentence), (
            "%r is not a denial in prose — the sweep would prove nothing" % tok)
        for label, planted in _positions(sentence).items():
            if fn(_HOST_RTL.format(planted=planted)) != base:
                leaks.append((tok, label))
    return leaks


def test_no_sentence_reaches_the_declared_width_regex_from_any_position():
    """The reach half of the `_NOT_PROSE` claim, over the whole vocabulary."""
    leaks = _prose_reaches(COV._rtl_declared_widths)
    assert leaks == [], (
        "an English sentence that DENIES a declaration minted it anyway, at: "
        "%r" % (leaks,))


def test_the_zero_carries_its_negative_control():
    """The sweep's zero is a statement about the GRAMMAR, not about a fixture
    that could not have moved: the IDENTICAL payload spliced in as CODE moves
    the published answer."""
    base = COV._rtl_declared_widths(_HOST_RTL.format(planted=""))
    as_code = COV._rtl_declared_widths(_HOST_RTL.format(planted="  " + _PAYLOAD))
    assert "plant_sig" not in base, base
    assert as_code.get("plant_sig") == 8, (
        "the payload does not move the answer even as CODE — the sweep above "
        "would then be vacuous: %r" % (as_code,))


def _comments_only_mutant(rtl_text):
    """MUTATION: the body as it stood before the string blanking landed."""
    if not rtl_text:
        return {}
    txt = COV._SRC.strip_comments(rtl_text)
    widths = {}
    for m in COV._RTL_DECL_WIDTH_RE.finditer(txt):
        rng, names = m.group(1), m.group(2)
        if rng:
            rm = COV._RANGE_LITERAL_RE.match(rng.replace(" ", ""))
            if not rm:
                continue
            w = abs(int(rm.group(1)) - int(rm.group(2))) + 1
        else:
            w = 1
        for nm in names.split(","):
            nm = nm.strip().lower()
            if nm and nm not in widths:
                widths[nm] = w
    return widths


def test_the_pre_fix_body_leaks_and_leaks_through_the_string_path():
    """A check that cannot fail is not a check. Run the SAME sweep against the
    body as it stood before this commit — comments stripped, strings intact —
    and it must find the leak, in the string positions and only there. This is
    the measurement that made the `_NOT_PROSE` entry honest: the claim was FALSE
    as first drafted, and this is the arm that says so."""
    leaks = _prose_reaches(_comments_only_mutant)
    assert leaks, "the reach check cannot fail — it proves nothing"
    positions = {p for _tok, p in leaks}
    assert positions == {"string_literal", "unterminated_string"}, (
        "the pre-fix leak is the string path, and only that path: %r" % positions)
    assert len(leaks) == 2 * len(_DENIAL_TOKENS), (
        "every token of the vocabulary leaks through a string: %d" % len(leaks))


def _cut_only_mutant(rtl_text):
    """MUTATION: the unterminated-string CUT without the blanker."""
    if not rtl_text:
        return {}
    txt = COV._SRC.strip_comments(rtl_text)
    c = txt.find('"')
    if c != -1:
        txt = txt[:c]
    widths = {}
    for m in COV._RTL_DECL_WIDTH_RE.finditer(txt):
        rng, names = m.group(1), m.group(2)
        if rng:
            rm = COV._RANGE_LITERAL_RE.match(rng.replace(" ", ""))
            if not rm:
                continue
            w = abs(int(rm.group(1)) - int(rm.group(2))) + 1
        else:
            w = 1
        for nm in names.split(","):
            nm = nm.strip().lower()
            if nm and nm not in widths:
                widths[nm] = w
    return widths


def test_the_blanker_and_the_cut_each_earn_their_place():
    """Neither half is decoration, and they fail in OPPOSITE directions.

      * the cut alone is sound but destructive — it truncates at the first `"`
        in the file, so every declaration after a `$display` is lost. MEASURED
        on this checkout's 31 *.v/*.sv files: the cut-alone body changes the
        answer on 17 of them and empties 3 outright.
      * the blanker alone leaks — an UNTERMINATED string is left as-is by
        design, which `test_no_sentence_reaches...` covers via MUT on the cut.
    """
    src = _HOST_RTL.format(
        planted='  initial $display("hello");\n  reg [15:0] after_the_string;')
    shipped = COV._rtl_declared_widths(src)
    assert shipped.get("after_the_string") == 16, (
        "a declaration after a terminated string must survive: %r" % (shipped,))
    assert _cut_only_mutant(src).get("after_the_string") is None, (
        "the cut alone would have kept it — then the blanker is decoration")


def test_a_range_the_grammar_leaves_open_yields_no_entry_and_never_a_default():
    """The grammar half. Verilog has no form that DENIES a declaration, so the
    only thing this function can fail to know is a width it cannot evaluate —
    and ABSENCE is how it reports that. A parameterised bound, an arithmetic
    bound, a non-literal bound and a malformed range each yield NO ENTRY; a
    guessed default (1, or the literal's own size) would be the value-out-of-
    nothing this register exists to refuse."""
    cases = {
        "parameterised": "module m(input signed [WIDTH-1:0] x); endmodule",
        "arithmetic": "module m(input signed [8*4-1:0] x); endmodule",
        "localparam_bound": "module m(input signed [N:0] x); endmodule",
        "backtick_define": "module m(input signed [`W-1:0] x); endmodule",
        "malformed": "module m(input signed [31 0] x); endmodule",
    }
    got = {k: COV._rtl_declared_widths(v).get("x") for k, v in cases.items()}
    assert all(v is None for v in got.values()), (
        "an unevaluable range must yield NO entry, never a guessed width: %r"
        % (got,))
    # ... and the same input still yields the widths it CAN evaluate, so the
    # refusal is scoped to the one bound rather than to the whole file.
    both = COV._rtl_declared_widths(
        "module m(input signed [WIDTH-1:0] x, output [7:0] y); endmodule")
    assert both.get("x") is None and both.get("y") == 8, both


def test_guessing_a_default_for_an_unevaluable_range_makes_that_check_go_red():
    """The other direction: a mutant that supplies 1 rather than declining is
    caught by the assertion above."""
    def _guessing_mutant(rtl_text):
        widths = dict(COV._rtl_declared_widths(rtl_text))
        for m in COV._RTL_DECL_WIDTH_RE.finditer(rtl_text or ""):
            for nm in (m.group(2) or "").split(","):
                nm = nm.strip().lower()
                if nm:
                    widths.setdefault(nm, 1)          # the guessed default
        return widths

    guessed = _guessing_mutant("module m(input signed [WIDTH-1:0] x); endmodule")
    assert guessed.get("x") == 1, guessed
    assert COV._rtl_declared_widths(
        "module m(input signed [WIDTH-1:0] x); endmodule").get("x") is None


def test_a_strip_that_raises_yields_no_entry_rather_than_the_raw_text(monkeypatch):
    """Could-not-read is not read-and-empty. If a strip raises, the function
    declines; falling back to the RAW text is the one direction that would
    publish declarations minted from comments and strings."""
    def _boom(_text):
        raise RuntimeError("strip failed")

    monkeypatch.setattr(COV._HDL, "strip_hdl_comments_and_strings", _boom)
    assert COV._rtl_declared_widths(_HOST_RTL.format(planted="")) == {}
