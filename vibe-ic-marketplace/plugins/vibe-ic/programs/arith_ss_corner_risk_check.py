#!/usr/bin/env python3
"""arith_ss_corner_risk_check.py — slow-corner arithmetic-architecture advisor.

Predicts a timing-closure re-architecture: a wide single-cycle ripple-carry
add / accumulate / compare chain closes at TT but blows the SS (slow-slow,
cold, low-V) corner, forcing a carry-save / carry-select / pipelined rebuild.
Today that is only discovered *after* a full multi-corner STA round-trip. This
surfaces the risk from RTL, before synthesis.

CORRECTION, measured when this was wired (it previously claimed BOTH the spm
and sha256 benchmark ICs as motivating cases): on sha256 it fires — 21 HIGH on
the published RTL, on the real 32-bit round adders. On spm it finds NOTHING,
and NOT because a mitigation marker silences it: stripping every marker from
the published spm RTL still yields 0 findings, because that datapath is an
XOR/AND carry-save array containing no `+`, `-` or `*` at all. The rebuild spm
needed is therefore outside what this heuristic can see. Only the sha256 half
of the original claim is demonstrable from the published corpus.

It is an ADVISORY lint (a risk predictor, not a proof): default exit is 0 and
findings are WARN/INFO. `--strict` makes a HIGH-risk finding fail (exit 1) so
it can act as a gate where desired.

ENFORCEMENT: advisory

HOW `--strict` IS USED IN THE FLOW, and why it is not a promotion
-----------------------------------------------------------------
Wired at Step 2 in the `advisory_program_exit_zero` slot, which RUNS the
program, RECORDS the verdict and returns True unconditionally. That slot judges
by EXIT CODE ONLY: rc=0 is recorded as the single word `ok`. Without `--strict`
this program always exits 0, so a run with ninety-odd HIGH findings and a run
with none would produce byte-identical evidence — the disclosure the wiring
exists for would be discarded at the moment of recording. `--strict` is
therefore the RECORDING channel here, not a tightening: rc=1 is the only way the
findings reach the compliance report, and the advisory slot still cannot fail
the step.

CORRECTED 2026-09-06 (RB2-05, #2063). That paragraph justified exiting 1 on
rows this analyser itself labels "Predicted from RTL STRUCTURE, not measured" —
i.e. it made a prediction wear the exit code of a measurement, because the
recording channel it wanted was rc. It is no longer true that rc is the only
channel: the flow's own step command already passes `--json`, every row is
written there with `measured` and `basis` fields, and the rows are printed in
full on stdout. `--strict` now exits 1 only on a MEASURED HIGH row (see
`strict_failing_rows`), so today, with no measuring producer wired, this
program exits 0 and says in its headline how many rows are predictions.

Heuristic, per module (chip-AGNOSTIC, purely structural):
  1. Build a width table from `reg/wire/logic [H:L] name` declarations.
  2. Scan every combinational arithmetic expression that feeds state —
     RHS of a non-blocking `<=` (a registered result) or of an `assign`.
  3. For each, estimate the carry-chain width = max width of the operands /
     LHS, and the add-chain depth = number of chained +/- operators.
  4. Risk tiers (when NO mitigation marker is present in the module):
       HIGH : width >= --high-width (default 32), OR
              (width >= --warn-width AND add-chain-depth >= 3)
       MED  : width >= --warn-width (default 16)
     `*` (multiply) at width >= --warn-width is HIGH unless a DSP / compressor
     marker is present.
  5. Mitigation markers (any → the chain is assumed already structured):
       csa, carry_save, carry_select, kogge, brent_kung, han_carlson,
       sklansky, prefix, pipelin, dsp, wallace, dadda, compressor, ladner.
     READ FROM THE CODE ONLY — the module name and the comment-STRIPPED body
     (vibe-ic#2192). A comment naming a strategy is prose about the problem,
     not evidence the code implements it, and this program's own printed
     recommendation pasted in as a `// TODO:` used to silence this program.
     A module the marker silences is DISCLOSED, never dropped in silence: the
     headline says how many modules were suppressed and how many rows were
     withheld, and each one prints a `[SUPPRESSED]` line naming the marker.

Recommendation emitted with every finding: replace the ripple chain with a
carry-save / carry-select adder, a parallel-prefix adder, or pipeline the add.

CLI (dual interface):
    python3 arith_ss_corner_risk_check.py <file.v|dir ...>
    python3 arith_ss_corner_risk_check.py --rtl-dir <dir> [--strict]

Exit codes:
    0 = PASS (no findings, or predictions only)
    1 = FAIL (a MEASURED HIGH-risk row + --strict; a PREDICTED row never
        exits 1 — RB2-05, see `strict_failing_rows`)
    2 = no RTL found / parse error
"""
from __future__ import annotations

# --- sibling-import path (vibe-ic#2104) ------------------------------------
# `programs/` is a flat directory whose modules import each other by BARE
# name. Python puts a file's own directory on `sys.path` only when that file
# is run as `__main__`; under `importlib.util.spec_from_file_location` — how
# the gates, the wiring audit and much of the suite load a program — it does
# not, so every bare sibling import below raises ModuleNotFoundError. Measured
# on the base tree: 454 of the 1385 top-level programs died that way. Restore
# the condition the file is written for. Idempotent, and the same shape the
# sibling programs that already carry it use.
import os as _os                                                    # noqa: E402
import sys as _sys                                                  # noqa: E402

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
# ---------------------------------------------------------------------------

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Tuple
from _atomic_artefact import write_text as atomic_write_text  # vibe-ic#1082 (helper from PR #1094)


def strip_comments(src: str) -> str:
    out, i, n = [], 0, len(src)
    while i < n:
        if src[i:i + 2] == '/*':
            end = src.find('*/', i + 2)
            if end == -1:
                break
            out.append(''.join('\n' if c == '\n' else ' ' for c in src[i:end + 2]))
            i = end + 2
        elif src[i:i + 2] == '//':
            end = src.find('\n', i)
            if end == -1:
                break
            out.append(' ' * (end - i))
            i = end
        else:
            out.append(src[i])
            i += 1
    return ''.join(out)


def collect_rtl_files(paths: List[str], rtl_dir: str | None) -> List[Path]:
    files: List[Path] = []
    roots = list(paths) + ([rtl_dir] if rtl_dir else [])
    for r in roots:
        p = Path(r)
        if p.is_dir():
            files += sorted(p.rglob('*.v')) + sorted(p.rglob('*.sv'))
        elif p.is_file():
            files.append(p)
    seen, out = set(), []
    for f in files:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


# Leading word-boundary only (no trailing \b) so the abbreviations also match
# as name prefixes — `csa_s`, `csa_c`, `cpa_q`, `compressor_tree`, etc.
#
# MATCHED AGAINST THE CODE, NEVER AGAINST A COMMENT (vibe-ic#2192). This scan
# used to run over the RAW module text on the premise that a comment naming
# the strategy is evidence the code implements it. A comment is not a
# structure, and this marker set contains words that appear in ordinary prose
# ABOUT the problem — a rejected alternative, a design note, a header
# describing a different variant of the same generator. MEASURED on the frozen
# base e2b3c08170b5: one SHA-256 round module written as the naive chained
# `t1 = h + Sigma1(e) + Ch + K[t] + W[t]` ripple, three arms differing in ONE
# COMMENT and in no byte of code — a comment reading `carry-save reduced` gave
# 0 findings, the same comment with that word changed to `restructured` gave 6
# HIGH, and adding this program's OWN printed recommendation as
# `// TODO: consider a carry-save / carry-select / parallel-prefix adder or
# pipelining.` returned it to 0. An author acting on the advice in the most
# natural way — recording it where the work is — deleted the evidence that the
# work was owed.
#
# So the marker now means AN IDENTIFIER IN THE CODE, which is the only thing a
# structural heuristic can stand behind: `wire [31:0] csa_s, csa_c;` still
# reads clean with no exemption, and no sentence anywhere silences anything.
MITIGATIONS = re.compile(
    r'\b(csa|cpa|carry[_-]?save|carry[_-]?select|carryselect|kogge|'
    r'brent[_-]?kung|han[_-]?carlson|sklansky|prefix[_-]?adder|prefix[_-]?add|'
    r'pipelin|dsp|wallace|dadda|compressor|ladner|fischer|3:2|3to2)', re.I)


def mitigation_marker(name: str, body: str) -> str | None:
    """The mitigation identifier that silences this module, or None.

    `body` MUST be the comment-stripped module body (vibe-ic#2192): a marker
    that is only ever spoken about in prose is not a mitigation. Returns the
    matched text so a suppression can be disclosed by NAME rather than as a
    bare boolean — a reader has to be able to see which word did it.
    """
    m = MITIGATIONS.search(name) or MITIGATIONS.search(body)
    return m.group(0) if m else None


@dataclass
class Suppression:
    """A module whose risk rows were WITHHELD by a mitigation marker.

    vibe-ic#2192. Silence is not a verdict. Before this record existed, a
    module that had been silenced and a module with no wide adders in it
    produced byte-identical output — `findings: 0 (0 HIGH, 0 MED …)` — so the
    one fact a reader needed (rows were withheld, and by which word) was the
    one fact the report did not carry. A module is listed here only when the
    marker actually withheld something: `withheld` is the number of rows the
    same analysis produces with the marker ignored, and a module that would
    have been clean anyway is not a suppression and is not listed.
    """
    file: str
    line: int
    module: str
    marker: str
    withheld: int


#: The basis word for a row derived from RTL structure with no timing run
#: behind it. RB2-05 (#2063).
_PREDICTED_BASIS = "predicted-from-rtl-structure"
_MEASURED_BASIS = "measured-slow-corner-sta"


@dataclass
class Finding:
    file: str
    line: int
    severity: str   # WARN (HIGH risk) / INFO (MED risk)
    rule: str
    symbol: str
    risk: str       # HIGH / MED
    width: int
    depth: int
    message: str
    #: RB2-05 (#2063). Is this row a MEASUREMENT of the design, or a PREDICTION
    #: about it? Every row this module produces today is a prediction: it is
    #: derived from RTL STRUCTURE alone, and the row's own message says so.
    #: The field is written on the row rather than inferred from its text so
    #: the distinction survives the JSON artefact and can never be recovered by
    #: grepping a sentence.
    measured: bool = False
    basis: str = _PREDICTED_BASIS


def _width_table(src: str) -> Dict[str, int]:
    """name -> bit width, from declarations with an explicit [H:L] range."""
    widths: Dict[str, int] = {}
    decl = re.compile(
        r'\b(?:reg|wire|logic|output|input|inout)\b'
        r'(?:\s+(?:reg|wire|logic))?(?:\s+(?:signed|unsigned))?\s*'
        r'\[\s*(\d+)\s*:\s*(\d+)\s*\]\s*'
        r'([A-Za-z_]\w*(?:\s*,\s*(?!(?:input|output|inout)\b)[A-Za-z_]\w*)*)')
    for m in decl.finditer(src):
        hi, lo = int(m.group(1)), int(m.group(2))
        w = abs(hi - lo) + 1
        for nm in re.split(r'\s*,\s*', m.group(3)):
            widths[nm] = max(widths.get(nm, 0), w)
    return widths


def _segment_modules(src: str) -> List[Tuple[str, str, int]]:
    out = []
    for m in re.finditer(r'\bmodule\s+(\w+)\b', src):
        end = re.search(r'\bendmodule\b', src[m.end():])
        body = src[m.end():m.end() + end.start()] if end else src[m.end():]
        out.append((m.group(1), body, src[:m.start()].count('\n') + 1))
    return out


# An assignment statement we care about: `lhs <= rhs ;`  or  `assign lhs = rhs ;`
_ASSIGN = re.compile(
    r'(?:assign\s+)?([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*(<=|=)(?!=)([^;]*);')
_ARITH = re.compile(r'[+\-](?![=>-])|\*(?!\*)')   # + - * but not ++ -> *= **
_FOR_HEADER = re.compile(r'\bfor\s*\([^)]*\)')
# a name optionally followed by a bracketed select, to read its effective width
_NAME_SEL = re.compile(r'([A-Za-z_]\w*)\s*(\[[^\]]*\])?')


def _strip_index_math(expr: str) -> str:
    """Remove bracketed sub-expressions so index/part-select math
    (`x[(a-b)*32 +: 32]`) is not mistaken for a datapath carry chain.
    Repeated to peel nested brackets."""
    prev = None
    while prev != expr:
        prev = expr
        expr = re.sub(r'\[[^\[\]]*\]', '', expr)
    return expr


#: A parenthesised SHIFT AMOUNT. Same principle as `_strip_index_math`: the
#: operand of `<<` / `>>` is a shift distance of at most log2(width) bits, so
#: arithmetic inside it is not a datapath carry chain.
#:
#: MEASURED FALSE POSITIVE this removes, on real published RTL — a rotate
#: helper written as
#:     function [31:0] rotr; input [31:0] x; input [4:0] n;
#:         begin rotr = (x >> n) | (x << (6'd32 - n)); end
#: was reported as a "32-bit add/compare chain feeding 'rotr'". The only `-` in
#: it is a 6-bit shift-amount subtraction; the datapath is two shifts and an OR.
_SHIFT_AMOUNT = re.compile(r'(<<<?|>>>?)\s*\(([^()]*)\)')


def _strip_shift_amount_math(expr: str) -> str:
    """Blank out arithmetic that only computes a shift distance."""
    prev = None
    while prev != expr:
        prev = expr
        expr = _SHIFT_AMOUNT.sub(r'\1 0', expr)
    return expr


def _paren_depths(text: str) -> List[int]:
    """Depth of `(` nesting at each character index."""
    depths = [0] * (len(text) + 1)
    d = 0
    for i, ch in enumerate(text):
        if ch == '(':
            d += 1
        depths[i] = d
        if ch == ')':
            d = max(0, d - 1)
    depths[len(text)] = d
    return depths


def _expr_carry_width(lhs: str, rhs: str, widths: Dict[str, int]) -> int:
    """Effective carry-chain width of `rhs`: widest operand used WHOLE, or the
    declared width of a `[H:L]` slice. A name used only as an index target
    (`mem[idx]`, `digest[ofs +: 32]`) contributes its SLICE width, not the
    full array/vector width."""
    w = widths.get(lhs, 0)
    for mm in _NAME_SEL.finditer(rhs):
        nm, brk = mm.group(1), mm.group(2)
        if brk:
            sm = re.match(r'\[\s*(\d+)\s*:\s*(\d+)\s*\]\s*$', brk)
            if sm:                       # explicit [H:L] slice → that width
                w = max(w, abs(int(sm.group(1)) - int(sm.group(2))) + 1)
            # single index `[i]` / part-select `[ofs +: n]` → element/part,
            # not a wide operand → contributes nothing.
        else:
            w = max(w, widths.get(nm, 0))
    return w


def analyse_module(name: str, body: str, base_line: int, path: str,
                   warn_w: int, high_w: int, raw_body: str = '',
                   suppressed: List['Suppression'] | None = None
                   ) -> List[Finding]:
    """Risk rows for one module.

    `body` is the COMMENT-STRIPPED module body and is what every decision here
    is taken on, the mitigation lookup included (vibe-ic#2192). `raw_body` is
    accepted for call-compatibility and deliberately reads NOTHING: it is the
    text that used to be able to silence this analysis.

    The mitigation marker is applied at the END, over the rows the analysis
    actually produced, rather than folded into each tier test. Same rows out —
    a suppressed module returned [] before and returns [] now — but the count
    of what was withheld is knowable, and is appended to `suppressed`.
    """
    widths = _width_table(body)
    marker = mitigation_marker(name, body)
    # for-loop induction (`for(i=0;i<N;i=i+1)`) is not a datapath adder.
    body = _FOR_HEADER.sub(' ', body)
    # The module header (`#(parameter ...) (ports);`) ends at the first ';';
    # parameter/port defaults there are not datapath and their `[^;]*` rhs can
    # over-run a comma-separated list. Skip everything before that terminator.
    header_end = body.find(';')
    if header_end < 0:
        header_end = 0
    depths = _paren_depths(body)
    findings: List[Finding] = []
    for m in _ASSIGN.finditer(body):
        if m.start() < header_end:
            continue
        # A non-blocking assignment and an `assign` are STATEMENTS: their
        # operator sits at paren depth 0. A `<=` at depth > 0 is a COMPARISON —
        # inside an assertion macro, a function call argument, an `if (…)`.
        #
        # MEASURED FALSE POSITIVE this removes, on real published RTL:
        #   `ASSERT_INIT(MaxLfsrWidth_A, LfsrDw <= $high(LFSR_COEFFS)+LUT_OFF)
        # has no terminating `;`, so `([^;]*);` swallowed every following line
        # until the next statement and reported a "168-bit add/compare chain
        # feeding 'LfsrDw'" — for a parameter, in an assertion, that is not a
        # datapath at all.
        if depths[m.start(2)] != 0:
            continue
        lhs, _op, rhs = m.group(1), m.group(2), m.group(3)
        if '"' in rhs or re.search(r'\b(?:parameter|localparam)\b', rhs):
            continue   # string-valued / over-ran into a parameter list
        # ignore index math and shift-distance math
        ops = _ARITH.findall(
            _strip_shift_amount_math(_strip_index_math(rhs)))
        if not ops:
            continue
        has_mul = '*' in ops
        add_depth = sum(1 for o in ops if o in '+-')
        width = _expr_carry_width(lhs, rhs, widths)
        if width == 0:
            continue  # width unknown — don't guess
        line = base_line + body[:m.start()].count('\n')

        risk = None
        if has_mul and width >= warn_w:
            risk = 'HIGH'
            kind = 'wide-mult-comb'
            why = f"{width}-bit combinational multiply"
        elif width >= high_w or (width >= warn_w and add_depth >= 3):
            risk = 'HIGH'
            kind = 'wide-ripple-add'
            why = (f"{width}-bit add/compare chain (depth {add_depth}) in a "
                   f"single-cycle path")
        elif width >= warn_w:
            risk = 'MED'
            kind = 'ripple-add'
            why = f"{width}-bit add/compare chain (depth {add_depth})"
        if not risk:
            continue
        # RB2-05 (#2063) — A PREDICTION IS NOT A MEASUREMENT, AND ITS
        # SEVERITY MAY NOT OUTRANK ONE. Every row this analyser emits is
        # structural: it has read declarations and operators, and no timing
        # tool has run. `risk` keeps the HIGH/MED tier (that is the
        # prediction's own strength and is what a reader sorts on); the
        # SEVERITY of a predicted row is INFO, because severity is what the
        # rest of the flow escalates on.
        sev = 'INFO'
        findings.append(Finding(
            path, line, sev, kind, lhs, risk, width, add_depth,
            # SAY ONLY WHAT IS DEMONSTRABLE. This sentence used to end
            # "(This is the spm/sha256 re-architecture pattern.)" and shipped
            # on all 85 HIGH findings the published corpus produces — while
            # the module docstring's own CORRECTION records that only the
            # sha256 half reproduces: on spm this heuristic finds NOTHING even
            # with every mitigation marker stripped, because that datapath is
            # a carry-save XOR/AND array with no `+`, `-` or `*` in it. A
            # user-facing message must not carry a claim the program's own
            # measurement has already retracted.
            f"module '{name}': {why} feeding '{lhs}' is a slow-corner (SS) "
            f"timing risk — consider a carry-save / carry-select / "
            f"parallel-prefix adder or pipelining. Predicted from RTL "
            f"STRUCTURE, not measured: confirm on a slow-corner STA run. "
            f"(Reproduced on this repo's corpus for a 32-bit round-adder "
            f"datapath; a carry-save array containing no +/-/* is invisible "
            f"to this heuristic.)"))
    if marker and findings:
        # WITHHELD, not clean. The rows are dropped exactly as they were
        # before; what is new is that the drop leaves a record (vibe-ic#2192).
        if suppressed is not None:
            suppressed.append(Suppression(path, base_line, name, marker,
                                          len(findings)))
        return []
    return findings


def analyse_text(raw: str, path: str = '<rtl>',
                 warn_w: int = 16, high_w: int = 32,
                 suppressed: List['Suppression'] | None = None
                 ) -> List[Finding]:
    """Every finding in one RTL SOURCE STRING.

    The text-level entry point, so a consumer that already holds the RTL — the
    phase-2 determinism dispatch and `gate_directed_rtl_repair`'s router
    (vibe-ic#2178) — reads THIS analyser rather than a second copy of its
    heuristic. `lint_file` is this function plus a read, which is what keeps
    the two callers from drifting apart.

    IT READS THE COMMENT-STRIPPED SOURCE AND NOTHING ELSE, and passes
    `suppressed` straight through, because that is what `lint_file` does since
    vibe-ic#2192 landed. This entry point was first authored against the older
    raw-body mitigation lookup; that half is dropped in favour of the rule that
    landed first, so a router reading this function cannot be silenced by a
    sentence the file-path caller is already immune to, and a module this
    function withholds rows from is disclosed to its caller rather than
    rendered as a clean one.
    """
    src = strip_comments(raw)
    out: List[Finding] = []
    for name, body, line in _segment_modules(src):
        out += analyse_module(name, body, line, path, warn_w, high_w,
                              suppressed=suppressed)
    return out


def lint_file(path: Path, warn_w: int, high_w: int,
              suppressed: List['Suppression'] | None = None) -> List[Finding]:
    return analyse_text(path.read_text(errors='replace'), str(path),
                        warn_w, high_w, suppressed)


def strict_failing_rows(findings: List[Finding]) -> List[Finding]:
    """The rows `--strict` may exit 1 on. RB2-05 (#2063).

    THE RULE: a row whose own label says it was PREDICTED, not measured, never
    sets rc=1. The gate's verdict is a function of MEASURED rows only.

    WHY. `--strict` returned 1 on three rows that were all severity WARN and
    all carried the sentence "Predicted from RTL STRUCTURE, not measured:
    confirm on a slow-corner STA run" (MEASURED on the subservient cell, lane
    rbsub2, 2026-09-06). A gate that FAILs on its own admitted guess makes the
    guess indistinguishable, to every downstream reader, from a slow-corner
    STA result — and on that cell the prediction happened to be right, which
    is the worst version: a right guess published in the grammar of a
    measurement teaches everyone to read the next wrong one the same way.

    WHAT IS NOT LOST. The rows are still emitted on stdout with their full
    text, and still written to the `--json` artefact the flow's own step
    command already requests, with `measured` and `basis` on every row. The
    docstring above claims rc=1 is "the only way the findings reach the
    compliance report"; that was true of the report PROSE and never of the
    JSON. Disclosure moves to the channel that can carry a prediction without
    dressing it as a verdict.

    A row a MEASURING producer marks `measured=True` at HIGH risk is a
    slow-corner violation and does set rc=1 under `--strict` — this function
    is where that adjudication lives, so the first such producer is judged by
    a rule that already exists rather than by whoever writes it.
    """
    return [f for f in findings if f.measured and f.risk == 'HIGH']


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Slow-corner arithmetic risk advisor.')
    ap.add_argument('paths', nargs='*', help='RTL files or directories')
    ap.add_argument('--rtl-dir', help='Directory of .v/.sv to scan recursively')
    ap.add_argument('--warn-width', type=int, default=16,
                    help='Width at/above which a ripple chain is MED risk')
    ap.add_argument('--high-width', type=int, default=32,
                    help='Width at/above which a ripple chain is HIGH risk')
    ap.add_argument('--strict', action='store_true',
                    help='Exit 1 if any MEASURED HIGH-risk finding (a '
                         'PREDICTED row never sets rc=1 — see '
                         'strict_failing_rows; default: advisory, exit 0)')
    ap.add_argument('--json', help='Write findings as JSON')
    args = ap.parse_args(argv)

    files = collect_rtl_files(args.paths, args.rtl_dir)
    if not files:
        print('arith_ss_corner_risk_check: no RTL files found', file=sys.stderr)
        return 2

    findings: List[Finding] = []
    suppressed: List[Suppression] = []
    for f in files:
        try:
            findings += lint_file(f, args.warn_width, args.high_width,
                                  suppressed=suppressed)
        except Exception as e:  # noqa: BLE001
            print(f'arith_ss_corner_risk_check: parse error in {f}: {e}',
                  file=sys.stderr)
            return 2

    high = [f for f in findings if f.risk == 'HIGH']
    med = [f for f in findings if f.risk == 'MED']
    predicted = [f for f in findings if not f.measured]
    strict_rows = strict_failing_rows(findings)
    fail = args.strict and bool(strict_rows)
    # THE VERDICT CARRIES THE COUNT (vibe-ic#2178). Two real authoring outputs
    # of the same design, one with 13 HIGH rows and one with 0, both produced
    # rc 0 and the single word `PASS`, so every downstream reader of this gate
    # — including the flow's own advisory recorder — saw byte-identical
    # evidence for a design that was warned and one that had nothing to warn
    # about. A detector returning the same verdict at thirteen findings and at
    # none is discarding the only signal it has.
    #
    # `PASS-WITH-ADVISORIES` is chosen from the vocabulary the recorder ALREADY
    # treats as non-blocking (`flow_compliance_check._ADVISORY_NONBLOCKING_
    # VERDICTS`), so the count reaches the record WITHOUT this gate gaining the
    # power to refuse. That is deliberate and is the ruling on #2178: a risk
    # signal that blocks becomes a waiver factory. rc is UNCHANGED in both
    # directions — see the `--strict` note above and #2063.
    # Advisory default never prints the token FAIL (keeps the MCP PASS contract).
    verdict = ('FAIL' if fail
               else 'PASS-WITH-ADVISORIES' if high
               else 'PASS')
    note = '' if fail else ' (advisory)'
    # vibe-ic#2192 — A SILENCED MODULE MAY NOT RENDER AS A CLEAN ONE. The
    # clause is appended only when something was actually withheld, so a run
    # over a corpus with no suppression prints exactly the headline it always
    # printed.
    withheld = sum(sp.withheld for sp in suppressed)
    supp_note = (f"; {len(suppressed)} module(s) suppressed by a mitigation "
                 f"marker in code, {withheld} row(s) withheld"
                 if suppressed else "")
    print(f"arith_ss_corner_risk_check: {verdict}{note} — findings: "
          f"{len(findings)} ({len(high)} HIGH, {len(med)} MED; "
          f"{len(predicted)} PREDICTED from RTL structure, "
          f"{len(findings) - len(predicted)} MEASURED){supp_note}")
    for fd in sorted(findings, key=lambda x: (x.file, x.line)):
        print(f"  {fd.file}:{fd.line}: [{fd.risk}] {fd.rule}: {fd.message}")
    for sp in sorted(suppressed, key=lambda x: (x.file, x.line)):
        print(f"  {sp.file}:{sp.line}: [SUPPRESSED] mitigation-marker: module "
              f"'{sp.module}': {sp.withheld} risk row(s) withheld because the "
              f"code carries the mitigation marker '{sp.marker}'. The marker "
              f"is read from the code only; a comment claiming a mitigation "
              f"no longer silences this analysis (vibe-ic#2192).")
    if args.json:
        # A REPORT OBJECT, not a bare list, and that is the whole repair on
        # this side. `flow_compliance_check._command_json_report` reads the
        # path this gate's own flow command already passes and then returns
        # `data if isinstance(data, dict) else None` — so a list was invisible
        # to the recorder by construction, and `_advisory_execution_record`
        # fell back to rc, which is identical in both directions. The rows are
        # unchanged and still carry `measured` / `basis` on every one.
        #
        # The suppression census travels WITH the counts, for the reason
        # vibe-ic#2192 gave about the headline: a silenced module may not
        # render as a clean one. That rule landed against stdout, and this
        # object is a second channel to the same reader — dropping the
        # disclosure here would reopen the hole in the machine-readable half
        # while stdout stayed honest. `suppressed_modules` is 0 and
        # `suppressed_rows_withheld` is 0 on any corpus with nothing withheld,
        # so a clean run's report gains two zeroes and no other change.
        outp = Path(args.json)
        outp.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(outp, json.dumps({
            'verdict': verdict,
            'high': len(high),
            'med': len(med),
            'predicted': len(predicted),
            'measured': len(findings) - len(predicted),
            'suppressed_modules': len(suppressed),
            'suppressed_rows_withheld': withheld,
            'suppressed': [asdict(sp) for sp in suppressed],
            'findings': [asdict(f) for f in findings],
        }, indent=2))
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())
