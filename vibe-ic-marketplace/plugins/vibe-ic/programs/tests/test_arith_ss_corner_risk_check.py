"""Unit tests for arith_ss_corner_risk_check.py.

The advisor predicts the slow-corner re-architecture that the spm and sha256
benchmark ICs needed. It must: fire on undocumented wide ripple chains, stay
quiet on documented carry-save / structured / narrow designs, and ignore
loop-counter / index-math / parameter false positives.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / 'arith_ss_corner_risk_check.py'
assert SCRIPT.exists()


def run(tmp_path, sv, *extra):
    f = tmp_path / 'dut.v'
    f.write_text(sv)
    jf = tmp_path / 'out.json'
    res = subprocess.run(
        [sys.executable, str(SCRIPT), '--json', str(jf), *extra, str(f)],
        capture_output=True, text=True)
    report = json.loads(jf.read_text()) if jf.exists() else {}
    # The `--json` artefact is a REPORT OBJECT (vibe-ic#2178), not a bare list:
    # `flow_compliance_check._command_json_report` returns `data if
    # isinstance(data, dict) else None`, so a list was invisible to the flow's
    # own advisory recorder. `report_of` below pins that shape directly.
    return res, report.get('findings', [])


def run_report(tmp_path, sv, *extra):
    """The same invocation, returning the whole report object."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    f = tmp_path / 'dut.v'
    f.write_text(sv)
    jf = tmp_path / 'out.json'
    res = subprocess.run(
        [sys.executable, str(SCRIPT), '--json', str(jf), *extra, str(f)],
        capture_output=True, text=True)
    return res, json.loads(jf.read_text())


WIDE_RIPPLE = """
module acc(input clk, input rst_n, input [31:0] a, input [31:0] b,
           output reg [31:0] sum);
  always @(posedge clk) if(!rst_n) sum <= 0; else sum <= a + b + sum;
endmodule
"""


def test_wide_ripple_is_high_and_advisory(tmp_path):
    res, f = run(tmp_path, WIDE_RIPPLE)
    assert res.returncode == 0                       # advisory default
    assert any(x['risk'] == 'HIGH' for x in f)
    # advisory mode must not print the FAIL token (MCP PASS contract)
    assert 'FAIL' not in subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path / 'dut.v')],
        capture_output=True, text=True).stdout


def test_strict_does_not_fail_on_a_PREDICTED_high(tmp_path):
    """RB2-05 (#2063). `--strict` used to exit 1 here. The row it exited on
    says, in its own message, "Predicted from RTL STRUCTURE, not measured" —
    so rc=1 published a guess in the grammar of a slow-corner STA result. The
    row must still be produced, in full, with its risk tier intact; only the
    EXIT CODE stops claiming it was measured."""
    res, f = run(tmp_path, WIDE_RIPPLE, '--strict')
    assert res.returncode == 0, res.stdout
    high = [x for x in f if x['risk'] == 'HIGH']
    assert high, f                      # the prediction is NOT suppressed
    for x in high:
        assert x['measured'] is False
        assert x['basis'] == 'predicted-from-rtl-structure'
        assert x['severity'] == 'INFO'  # a prediction may not outrank a measurement
    assert 'PREDICTED from RTL structure' in res.stdout


def test_strict_DOES_fail_on_a_measured_high_row():
    """The other direction, so the rule is a rule and not a silencer: a row a
    MEASURING producer marks `measured=True` at HIGH risk is exactly what
    `--strict` exists to exit 1 on."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('_ass', SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPT.parent))
    # 3.10 dataclasses resolves the string annotations of a `from __future__
    # import annotations` module through sys.modules[cls.__module__].
    sys.modules['_ass'] = mod
    spec.loader.exec_module(mod)
    predicted = mod.Finding('f.v', 1, 'INFO', 'wide-ripple-add', 'sum',
                            'HIGH', 32, 3, 'msg')
    measured = mod.Finding('f.v', 1, 'WARN', 'wide-ripple-add', 'sum',
                           'HIGH', 32, 3, 'msg', measured=True,
                           basis='measured-slow-corner-sta')
    med_measured = mod.Finding('f.v', 1, 'INFO', 'ripple-add', 'x',
                               'MED', 16, 1, 'msg', measured=True,
                               basis='measured-slow-corner-sta')
    assert mod.strict_failing_rows([predicted]) == []
    assert mod.strict_failing_rows([med_measured]) == []
    assert mod.strict_failing_rows([predicted, measured]) == [measured]


def test_documented_carry_save_is_quiet(tmp_path):
    sv = """
module core(input clk, input rst_n, output reg [31:0] sum);
  // carry-save adder (3:2 compressor) tree — only one final CPA per result
  wire [31:0] csa_s, csa_c;
  always @(posedge clk) if(!rst_n) sum <= 0; else sum <= csa_s;
endmodule
"""
    res, f = run(tmp_path, sv)
    assert res.returncode == 0
    assert len(f) == 0


def test_for_loop_counter_ignored(tmp_path):
    sv = """
module m(input clk, output reg [31:0] mem [0:15]);
  integer j;
  always @(posedge clk) for (j=0;j<16;j=j+1) mem[j] <= 32'b0;
endmodule
"""
    _, f = run(tmp_path, sv)
    assert len(f) == 0


def test_index_math_ignored(tmp_path):
    sv = """
module m(input [2:0] addr, input [255:0] digest, output reg [31:0] q);
  always @(*) q = digest[(7-(addr[2:0]))*32 +: 32];
endmodule
"""
    _, f = run(tmp_path, sv)
    assert len(f) == 0


def test_parameter_default_ignored(tmp_path):
    sv = """
module m #(parameter [0:0] MDU = 0, parameter RESET = "MINI")
          (input clk, output reg q);
  always @(posedge clk) q <= 1'b0;
endmodule
"""
    _, f = run(tmp_path, sv)
    assert len(f) == 0


def test_narrow_add_not_flagged(tmp_path):
    sv = """
module m(input clk, input [7:0] a, input [7:0] b, output reg [8:0] y);
  always @(posedge clk) y <= a[7:0] + b[7:0];
endmodule
"""
    _, f = run(tmp_path, sv)
    assert len(f) == 0


def test_wide_multiply_is_high(tmp_path):
    sv = """
module m(input clk, input [17:0] a, input [17:0] b, output reg [35:0] p);
  always @(posedge clk) p <= a * b;
endmodule
"""
    _, f = run(tmp_path, sv)
    assert any(x['rule'] == 'wide-mult-comb' and x['risk'] == 'HIGH' for x in f)


# ─────────────────────────────────────────────────────────────────────────
# FALSE POSITIVES measured on REAL published RTL when this advisory was
# wired into the flow. Each fixture is the offending construct copied from
# the run that produced it, so the regression is pinned to the shape that
# actually occurred rather than to one an author imagined.
# ─────────────────────────────────────────────────────────────────────────

# A `<=` inside an assertion MACRO argument is a COMPARISON, and the macro
# call carries no terminating `;` — so `([^;]*);` swallowed every following
# line and reported a 168-bit "add/compare chain feeding 'LfsrDw'", for a
# PARAMETER, in an assertion, which is not a datapath at all.
ASSERT_MACRO_COMPARISON = """
module prim_lfsr #(parameter int LfsrDw = 32) (input clk_i, input rst_ni,
                   output logic [LfsrDw-1:0] state_o);
  logic [LfsrDw-1:0] coeffs;
  logic [LfsrDw-1:0] lfsr_q;
  `ASSERT_INIT(MaxLfsrWidth_A, LfsrDw <= $high(LFSR_COEFFS)+LUT_OFF)
  assign state_o = lfsr_q ^ coeffs;
endmodule
"""


def test_assertion_macro_comparison_is_not_an_assignment(tmp_path):
    res, f = run(tmp_path, ASSERT_MACRO_COMPARISON, '--strict')
    assert res.returncode == 0, res.stdout
    assert not any(x['symbol'] == 'LfsrDw' for x in f), f


# The same class in ordinary RTL: an `else if (addr <= (BASE + 8'd7))`
# comparison, not a registered assignment.
IF_COMPARISON = """
module regfile(input clk, input [7:0] address, output reg [31:0] read_data);
  always @(posedge clk)
    if (address >= 8'h10 && address <= (8'h10 + 8'd7))
      read_data <= 32'h0;
endmodule
"""


def test_if_condition_comparison_is_not_an_assignment(tmp_path):
    res, f = run(tmp_path, IF_COMPARISON, '--strict')
    assert res.returncode == 0, res.stdout
    assert not any(x['symbol'] == 'address' for x in f), f


# Arithmetic that only computes a SHIFT DISTANCE is at most log2(width)
# bits wide. A rotate helper was reported as a "32-bit add/compare chain".
SHIFT_AMOUNT_MATH = """
module hashcore(input clk, input [31:0] x, output reg [31:0] y);
  function [31:0] rotr; input [31:0] x; input [4:0] n;
      begin rotr = (x >> n) | (x << (6'd32 - n)); end
  endfunction
  always @(posedge clk) y <= rotr(x, 5'd7);
endmodule
"""


def test_shift_amount_math_is_not_a_carry_chain(tmp_path):
    res, f = run(tmp_path, SHIFT_AMOUNT_MATH, '--strict')
    assert res.returncode == 0, res.stdout
    assert not any(x['symbol'] == 'rotr' for x in f), f


def test_the_real_wide_adder_still_fires_under_strict(tmp_path):
    """The two repairs above must not silence the thing the gate is for.

    RB2-05 (#2063) moved the exit code, not the finding: what this test
    guards — that the real wide adder is still REPORTED, by rule name, under
    `--strict` — is asserted on the finding itself rather than on rc, which
    now says only whether anything was MEASURED."""
    res, f = run(tmp_path, WIDE_RIPPLE, '--strict')
    assert any(x['risk'] == 'HIGH' for x in f)
    assert 'wide-ripple-add' in res.stdout


def test_the_finding_does_not_claim_the_case_it_cannot_reproduce(tmp_path):
    """The message shipped on EVERY HIGH finding used to end "(This is the
    spm/sha256 re-architecture pattern.)" — while this program's own measured
    CORRECTION records that only the sha256 half reproduces: on the other
    design it finds nothing even with every mitigation marker stripped,
    because that datapath is a carry-save array with no `+`, `-` or `*` in it.
    A user-facing message must not carry a claim the program has retracted."""
    _res, f = run(tmp_path, WIDE_RIPPLE, '--strict')
    msgs = [x['message'] for x in f if x['risk'] == 'HIGH']
    assert msgs
    for m in msgs:
        assert 're-architecture pattern' not in m, m
        # and it still says what the finding IS, and that it is a prediction
        assert 'slow-corner (SS) timing risk' in m
        assert 'not measured' in m


if __name__ == '__main__':
    pytest.main([__file__, '-v'])


# ── vibe-ic#2178: THE VERDICT MUST CARRY THE COUNT ──────────────────────────
# MEASURED on two real authoring outputs of the same design (lane rbsha5 arms
# A2/A3, re-measured by lane cz2178 on 2026-09-07): 13 HIGH rows and 0 HIGH
# rows produced rc 0 and the single headline word `PASS` in BOTH. Every reader
# downstream — including the flow's own advisory recorder — saw byte-identical
# evidence for a design that was warned and one with nothing to warn about.

QUIET = """
module narrow(input clk, input [3:0] a, input [3:0] b, output reg [3:0] s);
  always @(posedge clk) s <= a + b;
endmodule
"""


def test_headline_and_verdict_move_with_the_high_count(tmp_path):
    hot, _ = run_report(tmp_path / 'hot', WIDE_RIPPLE)
    cold, _ = run_report(tmp_path / 'cold', QUIET)
    # the count is IN the headline, both ways. The field ORDER is the one
    # vibe-ic#2192 left on this line, not the reordering #2178's branch
    # carried: that branch was authored before #2192 landed a `supp_note`
    # clause onto the same print, and the count was already in the headline on
    # both sides, so the reorder was cosmetic and the line that landed first
    # is the contract. What #2178 adds here is the VERDICT WORD.
    assert '(1 HIGH, 0 MED;' in hot.stdout.splitlines()[0], hot.stdout
    assert '(0 HIGH, 0 MED;' in cold.stdout.splitlines()[0], cold.stdout
    # and the VERDICT WORD itself differs, which is what a reader that only
    # keeps the verdict (the compliance record) can see
    assert hot.stdout.splitlines()[0].split(':')[1].strip().startswith(
        'PASS-WITH-ADVISORIES')
    assert cold.stdout.splitlines()[0].split(':')[1].strip().startswith('PASS ')
    assert hot.stdout.splitlines()[0] != cold.stdout.splitlines()[0]
    # NEITHER may block. That is the ruling on #2178, not a preference.
    assert hot.returncode == 0 and cold.returncode == 0
    assert 'FAIL' not in hot.stdout and 'FAIL' not in cold.stdout


def test_json_is_a_report_object_that_names_the_rows(tmp_path):
    res, rep = run_report(tmp_path, WIDE_RIPPLE)
    assert isinstance(rep, dict)                      # NOT a bare list
    assert rep['verdict'] == 'PASS-WITH-ADVISORIES'
    assert rep['high'] == 1 and rep['med'] == 0
    assert rep['predicted'] == 1 and rep['measured'] == 0
    assert len(rep['findings']) == 1                  # the rows are NAMED
    assert rep['findings'][0]['risk'] == 'HIGH'
    assert rep['findings'][0]['symbol'] == 'sum'
    assert res.returncode == 0


def test_the_flow_recorder_reads_the_count_and_still_does_not_block(tmp_path):
    """END TO END through the recorder that actually consumes this gate.

    Not a grep: `_advisory_execution_record` is the function the
    `advisory_program_exit_zero` slot builds its record with. The NEGATIVE
    CONTROL is the point — the same rows written in the OLD bare-list shape are
    invisible to it and both arms record `PASS`, which is the defect #2178
    names.
    """
    fc = pytest.importorskip('flow_compliance_check')
    cmd = ('arith_ss_corner_risk_check --strict phase2/stage1/rtl '
           '--json reports/phase2/gates/arith_ss_corner_risk.json')
    proj = tmp_path / 'proj'
    dest = proj / 'reports/phase2/gates/arith_ss_corner_risk.json'
    dest.parent.mkdir(parents=True)

    def record_for(sv):
        _res, rep = run_report(tmp_path / f'w{abs(hash(sv))}', sv)
        dest.write_text(json.dumps(rep))
        return fc._advisory_execution_record(
            cmd, len(fc._GATE_LEDGER), True, '', proj), rep

    hot, hot_rep = record_for(WIDE_RIPPLE)
    cold, _ = record_for(QUIET)
    assert hot['verdict'] == 'PASS-WITH-ADVISORIES'
    assert cold['verdict'] == 'PASS'
    assert hot['verdict'] != cold['verdict']          # the record CARRIES it
    assert hot['exit_code'] == cold['exit_code'] == 0  # rc is unchanged
    assert hot['enforcement'] == 'NON_BLOCKING_ADVISORY'   # and never blocks

    # NEGATIVE CONTROL — the pre-#2178 shape, same rows, same rc.
    dest.write_text(json.dumps(hot_rep['findings']))
    stale = fc._advisory_execution_record(
        cmd, len(fc._GATE_LEDGER), True, '', proj)
    assert stale['verdict'] == 'PASS', (
        'the bare-list report must be invisible to the recorder — if this '
        'passes as PASS-WITH-ADVISORIES the control is not testing anything')


# ── the #2178/#2192 SEAM ────────────────────────────────────────────────────
# `analyse_text` is #2178's text-level entry point, added so the router and the
# phase-2 dispatch read THIS analyser instead of a second copy of the
# heuristic. It was first authored against the raw-body mitigation lookup that
# #2192 had already replaced on main. #2192 landed first and is the contract,
# so `analyse_text` reads the COMMENT-STRIPPED source like `lint_file` does.
# These two tests are what stops the older half creeping back in through the
# new entry point.

COMMENT_ONLY_MARKER = """
module wide_sum(input clk, input [31:0] a, input [31:0] b,
                output reg [31:0] sum);
  // carry-save reduced -- prose only; no identifier below says so.
  always @(posedge clk) sum <= a + b;
endmodule
"""

CODE_MARKER = """
module wide_sum(input clk, input [31:0] a, input [31:0] b,
                output reg [31:0] sum);
  wire [31:0] csa_s, csa_c;
  always @(posedge clk) sum <= a + b;
endmodule
"""


def test_analyse_text_is_not_silenced_by_a_comment(tmp_path):
    ass = pytest.importorskip('arith_ss_corner_risk_check')
    rows = [f for f in ass.analyse_text(COMMENT_ONLY_MARKER) if f.risk == 'HIGH']
    assert rows, ('a marker that appears only in a comment must not silence '
                  'the text entry point either (vibe-ic#2192)')
    # the other direction: a marker in CODE still silences, and says so
    supp = []
    coded = ass.analyse_text(CODE_MARKER, '<rtl>', suppressed=supp)
    assert coded == []
    assert len(supp) == 1 and supp[0].withheld >= 1
    assert supp[0].marker.lower().startswith('csa')


def test_the_report_object_discloses_what_a_marker_withheld(tmp_path):
    """#2192's rule holds in #2178's new machine-readable channel too.

    A module silenced by a marker and a module with no wide adders in it must
    not produce the same report. Without these two keys the object would be
    byte-identical for both, which is the exact shape #2178 exists to refuse.
    """
    _res, hidden = run_report(tmp_path / 'hidden', CODE_MARKER)
    _res2, clean = run_report(tmp_path / 'clean', QUIET)
    assert hidden['high'] == clean['high'] == 0
    assert hidden['suppressed_modules'] == 1
    assert hidden['suppressed_rows_withheld'] >= 1
    assert hidden['suppressed'][0]['module'] == 'wide_sum'
    assert clean['suppressed_modules'] == 0
    assert clean['suppressed'] == []
    assert hidden != clean
