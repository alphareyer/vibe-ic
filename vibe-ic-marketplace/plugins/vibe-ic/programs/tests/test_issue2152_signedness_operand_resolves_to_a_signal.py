"""#2152 — a PROSE NOUN must never be the blocking coverage token of a signedness
obligation.

`spec_signedness_extract` derived a `signed_operand` obligation whose
`coverage_tokens` was the English noun of the prose sentence rather than an
operand name: "the input operands to the signed wires" -> ["wires"], "unsigned
division" -> ["division"]. Because `attribute_coverage` credits a normal item by
whole-word lexical match in comment-stripped testbench code, the obligation could
only be satisfied by writing that noun INTO THE TESTBENCH. A faithful signed
testbench — directed vectors that separate the signed from the unsigned reading —
was rejected, while a testbench that merely contained the noun was accepted. The
gate was teaching testbenches to contain nouns.

Two prose surfaces let the noun through, and BOTH are exercised here:

  (A) the `signed <name>` declaration pattern accepted ordinary English —
      `signed wires` in "assigns the input operands to the signed wires" was read
      as a Verilog declaration and was therefore SELF-corroborating;
  (B) `_declared_signal`, the corroboration guard for prose-derived names,
      matched any line on which a direction word appeared BEFORE the noun, so the
      sentence corroborated itself: `division` was "declared" by "8-bit input
      signal representing the dividend for division".

The contract this file pins (the owner's ladder, #2152):

  1. a prose noun that IS a real declared signal / DUT port resolves to that
     signal and the obligation keeps its blocking power;
  2. a signedness statement about a named port/operand resolves to that operand;
  3. an UNRESOLVABLE noun leaves the obligation ADVISORY — disclosed with its
     phrase, never blocking, and never carrying the noun as a coverage token.
     Where the RTL structurally names its signed operands, the advisory item is
     attributed on THOSE (a `wire signed [31:0] sa = a;` alias resolves to both
     `sa` and `a`), so a faithful testbench is credited for what it really drives.

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
import spec_signedness_extract as SIGN       # noqa: E402


# ── the two prose surfaces, in their general shape ─────────────────────────────
# (A) an adjective+noun phrase that is ordinary English, not a declaration.
SPEC_ADJ_NOUN = (
    "The module assigns the input operands to the signed wires and the output "
    "result (r) to the lower 32 bits of the register (res[31:0]).\n"
)
# (B) a signedness word whose noun is corroborated only by a direction word
#     appearing EARLIER IN THE SAME SENTENCE.
SPEC_SELF_CORROBORATING = (
    "The design supports both signed and unsigned division operations.\n"
    "    dividend: 8-bit input signal representing the dividend for division.\n"
    "    divisor: 8-bit input signal representing the divisor for division.\n"
)
_NOUNS = {"wires", "division"}

# A design whose signed operands are declared as aliases of its ports — the shape
# the obligation must be attributed against.
RTL_SIGNED_ALIASES = """module dut_top(input [31:0] a, input [31:0] b,
                    input [4:0] op, output [31:0] r);
  wire signed [31:0] sa = a;
  wire signed [31:0] sb = b;
  reg [32:0] res;
  always @(*) begin
    case (op)
      5'd0: res = sa + sb;
      5'd1: res = {31'b0, sa < sb};
      5'd2: res = {31'b0, a < b};
      5'd3: res = sa >>> b[4:0];
      default: res = 33'bz;
    endcase
  end
  assign r = res[31:0];
endmodule
"""

# A faithful signedness testbench: directed vectors that DIFFER between the
# signed and the unsigned reading. It never writes the word "wires".
TB_FAITHFUL_SIGNED = """module tb;
  reg [31:0] a, b; reg [4:0] op; wire [31:0] r;
  dut_top dut(.a(a), .b(b), .op(op), .r(r));
  initial begin
    a = 32'hFFFFFFFF; b = 32'h00000001; op = 5'd1; #1;
    if (r !== 32'd1) $fatal(1, "signed compare");
    op = 5'd2; #1;
    if (r !== 32'd0) $fatal(1, "unsigned compare");
    a = 32'h80000000; b = 32'd1; op = 5'd3; #1;
    if (r !== 32'hC0000000) $fatal(1, "arithmetic shift");
    a = 32'h7FFFFFFF; b = 32'd1; op = 5'd0; #1;
    $finish;
  end
endmodule
"""

# A testbench that contains the PROSE NOUN in non-comment code and nothing the
# design calls an operand. It exercises no signedness at all.
TB_NOUN_STUFFED = """module tb;
  reg [31:0] wires; reg [4:0] ctl; wire [31:0] out;
  dut_top dut(wires, wires, ctl, out);
  initial begin
    wires = 0; ctl = 0; #1; wires = 1; #1; wires = 2; #1;
    wires = wires + 1; #1; wires = wires + 1; #1; $finish;
  end
endmodule
"""


def _signed_operand_rows(report):
    return [i for i in report["items"] if i["kind"] == "signed_operand"]


def _run(spec, rtl, tb):
    return COV.run({"user_prompt": spec}, rtl, tb, None, True)


# ── (B) the prose must not corroborate itself ────────────────────────────────
def test_a_direction_word_earlier_in_the_sentence_is_not_a_declaration():
    # the exact self-corroboration that made `division` a "declared signal"
    assert SIGN._declared_signal("division", SPEC_SELF_CORROBORATING) is False
    assert SIGN._declared_signal("wires", SPEC_ADJ_NOUN) is False
    # a REAL declaration of the same shape still resolves, in every spelling
    for decl, name in (("- input signed [15:0] coeff", "coeff"),
                       ("  output reg [31:0] acc;", "acc"),
                       ("input wire [7:0] din,", "din"),
                       ("| logic [3:0] cnt |", "cnt"),
                       ("module m(input signed s, output y);", "s")):
        assert SIGN._declared_signal(name, decl) is True, decl


def test_a_line_that_begins_with_a_direction_word_is_still_prose():
    # anchoring the declaration clause at a declaration head is NOT enough on its
    # own: half the English lines in a design document begin with a direction
    # word. "Input signals are the signed signals of the datapath" would declare
    # `signals` and reproduce this issue with a different noun. A real
    # declaration ENDS at `,` `;` `)` `=` `[`, a markdown cell `|`, or the line
    # end; English carries on into a verb.
    for prose, noun in (
            ("Input signals are the signed signals of the datapath.\n", "signals"),
            ("Input quantities are signed quantities.\n", "quantities"),
            ("Output ports carry the signed magnitudes.\n", "magnitudes")):
        assert SIGN._declared_signal(noun, prose) is False, prose
        for it in SIGN.extract(prose):
            assert noun not in it["coverage_tokens"], it
            assert it["block_eligible"] is False, it
    # a comma-separated declaration list still resolves EVERY name it introduces
    assert SIGN._declared_signal("b", "  input  [31:0] a, b;") is True
    assert SIGN._declared_signal("sa", "wire signed [31:0] sa = a;") is True


# ── (A) ordinary English is not a `signed` declaration ───────────────────────
def test_english_adjective_noun_is_not_a_signed_declaration():
    items = SIGN.extract(SPEC_ADJ_NOUN)
    for it in items:
        assert it["signal"] != "wires", it
        assert "wires" not in it["coverage_tokens"], it
    # a real signed declaration, with or without a packed range, still resolves
    ranged = SIGN.extract("- input signed [15:0] coeff\n")
    assert [i["signal"] for i in ranged] == ["coeff"], ranged
    scalar = SIGN.extract("module m(input signed s, output y);\n")
    assert [i["signal"] for i in scalar] == ["s"], scalar


# ── the ladder, rung 3: unresolvable -> advisory, never the noun ─────────────
def test_prose_noun_is_never_a_blocking_coverage_token():
    for spec in (SPEC_ADJ_NOUN, SPEC_SELF_CORROBORATING):
        for it in SIGN.extract(spec):
            assert not (_NOUNS & set(it["coverage_tokens"])), it
            if it.get("unresolved_operand"):
                assert it["block_eligible"] is False, it
                assert it["coverage_tokens"] == [], it


def test_the_unresolvable_obligation_is_disclosed_not_dropped():
    # membership, not counts: the phrase that carried the requirement is still
    # reported — the fix must not make a stated requirement disappear.
    phrases = {it["evidence"] for it in SIGN.extract(SPEC_ADJ_NOUN)}
    assert "signed wires" in phrases, phrases
    phrases = {it["evidence"] for it in SIGN.extract(SPEC_SELF_CORROBORATING)}
    assert "unsigned division" in phrases, phrases


def test_advisory_survives_the_provenance_loop():
    # the provenance loop re-computes block_eligible for every prose-heuristic
    # kind; without a signed_operand branch it would RE-PROMOTE the advisory item
    # to blocking (UNKNOWN is no-leak biased to "keep the block").
    rep = _run(SPEC_ADJ_NOUN, RTL_SIGNED_ALIASES, TB_FAITHFUL_SIGNED)
    rows = _signed_operand_rows(rep)
    assert rows, "the obligation disappeared entirely"
    assert all(r["block_eligible"] is False for r in rows), rows


# ── the two testbenches, in opposite directions ─────────────────────────────
def test_faithful_signed_tb_attributes_on_a_real_operand_without_the_noun():
    rep = _run(SPEC_ADJ_NOUN, RTL_SIGNED_ALIASES, TB_FAITHFUL_SIGNED)
    rows = _signed_operand_rows(rep)
    assert rows, "the obligation disappeared entirely"
    for r in rows:
        # attributed on the operands the RTL really treats as signed …
        assert set(r["coverage_tokens"]) == {"a", "b", "sa", "sb"}, r
        assert r["covered"] is True, r
        # … and the testbench never contains the prose noun
        assert "wires" not in TB_FAITHFUL_SIGNED
    assert not [i for i in rep["items"]
                if i["kind"] == "signed_operand" and i["block_eligible"]
                and i["covered"] is False]


def test_a_testbench_that_only_contains_the_noun_earns_nothing():
    rep = _run(SPEC_ADJ_NOUN, RTL_SIGNED_ALIASES, TB_NOUN_STUFFED)
    rows = _signed_operand_rows(rep)
    assert rows, "the obligation disappeared entirely"
    for r in rows:
        assert r["covered"] is False, r
        assert "wires" not in r["coverage_tokens"], r


# ── the requirement is still ENFORCED where it resolves (rungs 1 and 2) ──────
_SPEC_NAMED_OPERAND = """## Ports
- input signed [15:0] coeff
- input wire [15:0] din
- output reg [31:0] acc
The signed [15:0] coeff multiplies din. operand acc is treated as two's complement.
"""
_RTL_NAMED = """module dut_top(input signed [15:0] coeff, input [15:0] din,
                output reg [31:0] acc);
  always @(*) acc = coeff * din;
endmodule
"""
_TB_IGNORES_THE_OPERAND = """module tb;
  reg [15:0] q; wire [31:0] z;
  dut_top dut(q, q, z);
  initial begin q = 0; #1; q = 1; #1; $finish; end
endmodule
"""


def test_a_resolved_named_operand_still_blocks_when_uncovered():
    rep = _run(_SPEC_NAMED_OPERAND, _RTL_NAMED, _TB_IGNORES_THE_OPERAND)
    rows = _signed_operand_rows(rep)
    # a RESOLVED operand keeps exactly its own name as the coverage token
    named = {t for r in rows for t in r["coverage_tokens"]}
    assert {"coeff", "acc"} <= named, rows
    blocking = {t for r in rows if r["block_eligible"] and r["covered"] is False
                for t in r["coverage_tokens"]}
    assert {"coeff", "acc"} <= blocking, rows


def test_a_resolved_named_operand_is_covered_when_the_tb_drives_it():
    tb = """module tb;
  reg signed [15:0] coeff; reg [15:0] din; wire [31:0] acc;
  dut_top dut(coeff, din, acc);
  initial begin coeff = -1; din = 3; #1; coeff = 16'sh8000; #1; $finish; end
endmodule
"""
    rep = _run(_SPEC_NAMED_OPERAND, _RTL_NAMED, tb)
    rows = {t: r for r in _signed_operand_rows(rep)
            for t in r["coverage_tokens"]}
    assert rows["coeff"]["covered"] is True, rows["coeff"]
    assert rows["acc"]["covered"] is True, rows["acc"]


# ── §4.05 no-leak: an unsigned default with no signed sibling stays nothing ──
def test_unsigned_default_with_no_signed_sibling_yields_nothing():
    assert SIGN.extract("# adder — unsigned add\n"
                        "Computes modular arithmetic of unsigned integers.\n"
                        "## Ports\n- input wire [7:0] a\n"
                        "- output wire [8:0] y\n") == []
    assert SIGN.extract("Perform signed arithmetic.") == []
    assert SIGN.extract("Add two numbers.") == []
