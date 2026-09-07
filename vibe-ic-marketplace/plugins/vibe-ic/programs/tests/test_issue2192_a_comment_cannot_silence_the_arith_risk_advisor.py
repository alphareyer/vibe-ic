"""vibe-ic#2192 — a COMMENT may not silence `arith_ss_corner_risk_check`.

THE DEFECT, measured on the frozen base e2b3c08170b5 (tree 75d478b34c17,
v1.19.43) before the fix. The mitigation lookup ran over the RAW module text,
comments included, on the premise that a comment naming a carry-save /
carry-select / prefix / pipelined strategy is evidence the code implements it.
It is not. One SHA-256 round module written as the naive chained ripple
`t1 = h + Sigma1(e) + Ch + K[t] + W[t]`, in three arms whose CODE is byte
identical and whose only difference is one comment:

    arm A  a comment reading `... carry-save reduced for the same ...`   0 findings
    arm B  that ONE word changed to `restructured`                       6 findings, all HIGH
    arm C  arm B + this program's OWN printed recommendation, as a
           `// TODO: consider a carry-save / carry-select /
           parallel-prefix adder or pipelining.`                         0 findings

Arm C is the sharp end: the remedy the program prints, written down where the
work is, deleted the evidence that the work was owed. And nothing disclosed it
— arm A's headline was byte-identical to that of a module with no wide adders
in it at all, so a SILENCED module and a CLEAN module were the same record.

THE FIX. The marker is matched against the module name and the
comment-STRIPPED body, so it means an IDENTIFIER IN THE CODE — the only thing
a structural heuristic can stand behind. The intended path is untouched:
`wire [31:0] csa_s, csa_c;` still reads clean with no exemption and with no
comment helping it (`test_a_genuine_carry_save_rewrite_is_still_quiet_with_no_comments`).
And a module the marker does silence is now DISCLOSED rather than dropped in
silence.

EVERY FIXTURE HERE IS BIDIRECTIONAL BY CONSTRUCTION: each pair differs in a
comment and in no byte of code, so a test that passes for the wrong reason
would have to pass on both halves of a pair at once.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / 'arith_ss_corner_risk_check.py'
assert SCRIPT.exists()


# The naive datapath, shared by every arm below. `%s` is the ONLY thing that
# varies, and it is always a comment.
NAIVE = """
module sha256_round(input clk, input rst_n,
    input [31:0] a_i, b_i, c_i, d_i, e_i, f_i, g_i, h_i,
    input [31:0] kt, wt,
    output reg [31:0] a_o, e_o);
  wire [31:0] s1 = {e_i[5:0],e_i[31:6]} ^ {e_i[10:0],e_i[31:11]};
  wire [31:0] s0 = {a_i[1:0],a_i[31:2]} ^ {a_i[12:0],a_i[31:13]};
  wire [31:0] ch = (e_i & f_i) ^ (~e_i & g_i);
  wire [31:0] maj = (a_i & b_i) ^ (a_i & c_i) ^ (b_i & c_i);
  wire [31:0] t1 = h_i + s1 + ch + kt + wt;
  wire [31:0] t2 = s0 + maj;
%s
  always @(posedge clk) begin
    if (!rst_n) begin a_o <= 32'd0; e_o <= 32'd0; end
    else begin
      a_o <= t1 + t2;
      e_o <= d_i + t1;
    end
  end
endmodule
"""

#: The exact sentence every finding's own `message` ends with.
TOOL_RECOMMENDATION = ('// TODO: consider a carry-save / carry-select / '
                       'parallel-prefix adder or pipelining.')


def run(tmp_path, sv, name='dut.v'):
    f = tmp_path / name
    f.write_text(sv)
    jf = tmp_path / (name + '.json')
    res = subprocess.run(
        [sys.executable, str(SCRIPT), '--json', str(jf), str(f)],
        capture_output=True, text=True)
    if not jf.exists():
        return res, []
    report = json.loads(jf.read_text())
    # THE ARTEFACT IS A REPORT OBJECT, not a bare list (vibe-ic#2178, which
    # landed after this file and had to change the container so
    # `flow_compliance_check._command_json_report` — `data if isinstance(data,
    # dict) else None` — could read it at all). Only the extraction moved; not
    # one assertion in this file was touched, and the rows are the same rows.
    #
    # The shape is ASSERTED rather than defaulted on purpose. Half the tests
    # below assert `rows == []`, so a helper that quietly fell back to `[]`
    # when the key went missing would make them pass for the wrong reason —
    # which is precisely the failure mode this file's own docstring is about.
    assert isinstance(report, dict) and 'findings' in report, (
        'the --json artefact must be a report object carrying `findings`; got '
        f'{type(report).__name__}')
    return res, report['findings']


def _key(rows):
    """Identity of a row set that a file path cannot disturb."""
    return sorted((r['symbol'], r['risk'], r['width'], r['depth'])
                  for r in rows)


# --------------------------------------------------------------------------
# Direction 1 — a comment that says the right thing over code that does NOT.
# --------------------------------------------------------------------------

def test_a_mitigation_named_only_in_a_comment_does_not_silence_the_module(tmp_path):
    """Arm A. The comment describes a DIFFERENT variant of the same generator
    and silenced the whole module by accident."""
    claimed = NAIVE % ('  // t1 = h + Sigma1(e) + Ch + K[t] + W[t], carry-save\n'
                       '  // reduced for the same schedule in the wide variant.')
    res, rows = run(tmp_path, claimed)
    assert res.returncode == 0                     # advisory, unchanged
    assert rows, 'a naive ripple chain must be reported however its comments read'
    assert all(r['risk'] == 'HIGH' for r in rows)
    assert any(r['symbol'] == 't1' and r['depth'] >= 3 for r in rows)


def test_the_same_code_with_the_marker_word_removed_gives_the_same_rows(tmp_path):
    """Arms A and B, side by side. Identical code, one comment word apart:
    the row sets must be EQUAL. This is the pair that made the defect visible
    and it is the pair that proves the repair — not the count of either half."""
    claimed = NAIVE % ('  // t1 = h + Sigma1(e) + Ch + K[t] + W[t], carry-save\n'
                       '  // reduced for the same schedule in the wide variant.')
    plain = NAIVE % ('  // t1 = h + Sigma1(e) + Ch + K[t] + W[t], restructured\n'
                     '  // for the same schedule in the wide variant.')
    _, rows_claimed = run(tmp_path, claimed, 'claimed.v')
    _, rows_plain = run(tmp_path, plain, 'plain.v')
    assert _key(rows_claimed) == _key(rows_plain)
    assert rows_plain, 'the control arm must itself be non-empty'


def test_the_programs_own_recommendation_pasted_back_does_not_silence_it(tmp_path):
    """Arm C. An author acting on the advice in the most natural way — writing
    it down where the work is — must not delete the evidence that it is owed."""
    res, rows = run(tmp_path, NAIVE % ('  ' + TOOL_RECOMMENDATION))
    assert res.returncode == 0
    assert rows, 'the tool\'s own remedy, as a TODO, silenced the tool'
    assert all(r['risk'] == 'HIGH' for r in rows)


def test_a_bare_marker_word_in_a_comment_does_not_silence_it(tmp_path):
    """`// dsp` — one word, no code behind it."""
    _, rows = run(tmp_path, NAIVE % '  // dsp')
    assert rows
    _, block = run(tmp_path, NAIVE % '  /* wallace tree, see the design note */',
                   'blockcomment.v')
    assert block, 'a /* */ comment must not silence it either'


# --------------------------------------------------------------------------
# Direction 2 — the intended path. A marker in the CODE still silences, and
# does so without any comment helping it.
# --------------------------------------------------------------------------

GENUINE_NO_COMMENTS = """
module sha256_round_csa(input clk, input rst_n,
    input [31:0] h_i, kt, wt, d_i, e_i, f_i, g_i,
    output reg [31:0] a_o, e_o);
  wire [31:0] s1 = {e_i[5:0],e_i[31:6]} ^ {e_i[10:0],e_i[31:11]};
  wire [31:0] ch = (e_i & f_i) ^ (~e_i & g_i);
  wire [31:0] csa_s = h_i ^ s1 ^ ch;
  wire [31:0] csa_c = ((h_i & s1) | (h_i & ch) | (s1 & ch)) << 1;
  always @(posedge clk) begin
    if (!rst_n) begin a_o <= 32'd0; e_o <= 32'd0; end
    else begin a_o <= csa_s ^ csa_c; e_o <= d_i ^ csa_s; end
  end
endmodule
"""


def test_a_genuine_carry_save_rewrite_is_still_quiet_with_no_comments(tmp_path):
    """The path this advisory exists to leave alone. Real identifiers, ZERO
    comments: it must read clean with no exemption added anywhere."""
    assert '//' not in GENUINE_NO_COMMENTS and '/*' not in GENUINE_NO_COMMENTS
    res, rows = run(tmp_path, GENUINE_NO_COMMENTS)
    assert res.returncode == 0
    assert rows == []


def test_a_marker_in_the_code_still_withholds_the_rows(tmp_path):
    """A wide ripple chain in a module whose code carries `csa_`: the rows are
    still withheld, exactly as before. The fix narrows WHERE the marker is
    read, and does not change what a real marker does."""
    marked = NAIVE.replace('module sha256_round(', 'module csa_sha256_round(') % ''
    _, rows = run(tmp_path, marked)
    assert rows == []


# --------------------------------------------------------------------------
# Direction 3 — silence is not a verdict. A withheld module and a clean
# module may not render the same.
# --------------------------------------------------------------------------

def _stdout(tmp_path, sv, name):
    (tmp_path / name).write_text(sv)
    return subprocess.run([sys.executable, str(SCRIPT), str(tmp_path / name)],
                          capture_output=True, text=True).stdout


def test_a_suppressed_module_does_not_render_as_a_clean_one(tmp_path):
    withheld = _stdout(tmp_path,
                       NAIVE.replace('module sha256_round(',
                                     'module csa_sha256_round(') % '',
                       'withheld.v')
    clean = _stdout(tmp_path, GENUINE_NO_COMMENTS, 'clean.v')
    wl = [l for l in withheld.splitlines() if l.startswith('arith_ss')]
    cl = [l for l in clean.splitlines() if l.startswith('arith_ss')]
    # both are `findings: 0` — that is the point; the headlines must still differ
    assert 'findings: 0' in wl[0] and 'findings: 0' in cl[0]
    assert wl[0] != cl[0], 'a withheld module rendered exactly like a clean one'
    assert 'suppressed' in wl[0] and 'suppressed' not in cl[0]
    assert re.search(r"\[SUPPRESSED\] mitigation-marker: module "
                     r"'csa_sha256_round': 4 risk row\(s\) withheld", withheld)
    assert '[SUPPRESSED]' not in clean


def test_the_suppression_line_names_the_marker_that_did_it(tmp_path):
    out = _stdout(tmp_path,
                  NAIVE.replace('module sha256_round(',
                                'module wallace_sha256_round(') % '',
                  'named.v')
    assert "mitigation marker 'wallace'" in out


# --------------------------------------------------------------------------
# The unit the whole repair turns on, asserted directly.
# --------------------------------------------------------------------------

def test_mitigation_marker_reads_the_code_and_not_the_prose():
    sys.path.insert(0, str(SCRIPT.parent))
    import arith_ss_corner_risk_check as mod
    # a comment-stripped body is what this function is contracted to receive
    assert mod.mitigation_marker('m', 'wire [31:0] csa_s;') == 'csa'
    assert mod.mitigation_marker('csa_top', 'wire [31:0] q;') == 'csa'
    assert mod.mitigation_marker('m', 'wire [31:0] q;') is None
    # and the raw text that used to decide it is not consulted anywhere
    raw = 'module m; // carry-save\n wire [31:0] q; endmodule'
    assert mod.mitigation_marker('m', mod.strip_comments(raw)) is None
