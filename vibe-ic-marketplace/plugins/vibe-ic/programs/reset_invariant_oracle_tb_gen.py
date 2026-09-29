#!/usr/bin/env python3
"""RESET-INVARIANT oracle TBs — the family beside the boot-latency one.

MEASURED on subservient x gf180mcuD as a DIE, FRONT DOOR, run r48 (lane
icsub2, host 8HD-4, tree 41d3b39b8, image 0.3.67 sha256:4e9f54ef): 1 of 10
declared L10 cases executed its own oracle. Four of the nine that did not
need a test program nobody delivers; three are conditional on ISA options the
design does not select. TWO need NOTHING DELIVERED AT ALL --

    reset_assert_sram
      stimulus "Reset assert 中 SRAM 內容保留"
    i_rst_glitch_instruction_fetch_race
      stimulus "i_rst glitch 不應導致 instruction fetch race"

-- because they are PROPERTIES over the design's own declared observables.
They fell to the substance-floor scaffold only because the flow owns an
emitter for one property family (`cpu_boot_latency_oracle_tb_gen`, the
"within N cycles of reset release" convention -- which is exactly why
`reset_n_cycle_instruction` is the single case that executed) and none for
this one. This module is that missing family.

WHAT IT GROUNDS, and what it deliberately does not. Both oracles are
NECESSARY CONDITIONS stated at the pins, never a re-description of the
sentence:

  RESET_ASSERT_HOLD  "state held across reset assert" is observed as: while
    reset is ASSERTED the design issues no WRITE on its storage interface.
    A write is the only pin-visible way stored content can change, so no
    write asserted across the whole assert window is a real, falsifiable
    necessary condition for "content preserved".

    FALSIFIABILITY, MEASURED rather than asserted. Both oracles PASS on the
    shipped RTL AND on its pre-R-0915-99 combinational-boundary predecessor,
    so neither is evidence about the registered-boundary change -- they are
    about the reset invariants, which both revisions honour. What proves they
    can fail is a MUTANT: a DUT whose reset branch drives the observed output
    to 1 makes each of them FAIL by name (`reset_assert_sram` at every cycle
    of the assert window; the glitch oracle inside its window).

  RESET_GLITCH_NO_RACE  "a reset glitch must not cause a fetch race" is
    observed as: during a SHORT reset pulse applied mid-operation, the bus
    activity output stays deasserted, and after release no observable output
    is left X/Z. A transaction that survives into -- or is launched inside --
    the reset window is the race the case names.

    It does NOT claim the converse (an unraced fetch is not proven correct by
    this TB), and it does not fabricate an instruction stream.

FAIL-CLOSED throughout: a case whose own text does not state the invariant,
or a DUT whose port surface offers nothing to observe it on, returns None and
falls through to the substance-floor scaffold, so a case nobody can verify
still fails the Step-4 gate honestly.

chip-AGNOSTIC: keyed on the case's own declared grammar and on structural
port-name vocabulary (`we`/`wen`/`write`/`wr`; `cyc`/`stb`/`req`/`valid`),
never on a case-name, chip, vendor or SKU literal.
"""
from __future__ import annotations

# --- sibling-import path (vibe-ic#2104) ------------------------------------
import os as _os                                                    # noqa: E402
import sys as _sys                                                  # noqa: E402

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
# ---------------------------------------------------------------------------

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── the case's own grammar ────────────────────────────────────────────────
#: reset is ASSERTED (held), as opposed to released — the boot-latency family
#: owns the release side and the two must not both claim a case.
_RE_RESET_ASSERT = re.compile(
    r"reset\s*(?:assert|中|期間|holds?)|(?:^|\s)rst\s*assert", re.IGNORECASE)
#: "content/state is preserved / retained / unchanged"
_RE_PRESERVED = re.compile(
    r"保留|保持|不變|preserv|retain|unchanged|intact", re.IGNORECASE)
#: a storage noun the invariant is about
_RE_STORAGE = re.compile(r"sram|ram|memory|記憶體|register\s*file|regfile",
                         re.IGNORECASE)
#: a short reset pulse / glitch
_RE_GLITCH = re.compile(r"glitch|毛刺|spike|短脈衝", re.IGNORECASE)
#: the hazard the case names
_RE_RACE = re.compile(r"race|競|hazard", re.IGNORECASE)
#: negation — the case says the hazard must NOT happen
_RE_MUST_NOT = re.compile(r"不應|不得|不能|must\s*not|should\s*not|no\s+",
                          re.IGNORECASE)

# ── structural port vocabulary (suffix match, never a chip literal) ───────
_WRITE_TOKENS = ("we", "wen", "write", "wr", "wstrb", "wr_en")
_WRITE_RES = [re.compile(rf"(?:^|_){t}(?:_o|_out)?$", re.IGNORECASE)
              for t in _WRITE_TOKENS]
_BUS_ACTIVITY_TOKENS = ("cyc", "stb", "strobe", "req", "valid")
_BUS_ACTIVITY_RES = [re.compile(rf"(?:^|_){t}(?:_o|_out)?$", re.IGNORECASE)
                     for t in _BUS_ACTIVITY_TOKENS]
#: Read-enables are bus activity, the mirror of `wen` above; see the same
#: vocabulary in `cpu_boot_latency_oracle_tb_gen` for the measurement. A
#: FALLBACK ONLY: a top with a handshake output keeps observing it.
_READ_ENABLE_TOKENS = ("ren", "rden", "rd_en", "read_en")
_READ_ENABLE_RES = [re.compile(rf"(?:^|_){t}(?:_o|_out)?$", re.IGNORECASE)
                    for t in _READ_ENABLE_TOKENS]
_CLOCK_RE = re.compile(r"(?:^|_)(?:clk|clock)(?:$|_)", re.IGNORECASE)
_RESET_RE = re.compile(r"(?:^|_)(?:rst|reset|resetn|rstn)(?:$|_)",
                       re.IGNORECASE)
_ACTIVE_LOW_RE = re.compile(r"(?:_n$|n$|_b$|(?:^|_)(?:rstn|resetn)(?:$|_))",
                            re.IGNORECASE)

FAMILY_HOLD = "RESET_ASSERT_HOLD"
FAMILY_GLITCH = "RESET_GLITCH_NO_RACE"

#: Cycles observed after the glitch is released. Every one is sampled, from
#: the FIRST edge after release (R-0929-X-QUALIFIED), not a single late look.
POST_RELEASE_CYCLES = 8

# ── R-0929-X-QUALIFIED: the qualifiers the DESIGN INPUT declares ──────────
#: A condition word: the sentence makes one signal's meaning conditional on
#: another's level ("wdata is sampled WHEN wen=1", "資料於 we 為 1 時有效").
_QUAL_COND_RE = re.compile(
    r"\bwhen(?:ever)?\b|\bwhile\b|\bif\b|\bqualified\s+by\b|\bsampled\b|"
    r"\bvalid\b|當|時|期間|有效|取樣", re.IGNORECASE)
#: VALIDITY semantics (R-0929-X-QUALIFIED): the clause says the output's value
#: is VALID / SAMPLED / QUALIFIED / CAPTURED at the qualifier's level. A bare
#: condition ("o_done is high when o_busy is low", "o_q cleared to 0 when
#: rst_n is low") is a behaviour or a reset value, not a validity qualifier.
#: `低有效` / `高電位有效` spell ACTIVE-LOW / -HIGH, not validity.
_QUAL_VALIDITY_RE = re.compile(
    r"\bvalid(?:ity)?\b|\bsampled\b|\bqualified\b|\bqualifies\b|"
    r"\bcaptured\b|\blatched\b|\bstrobed\b|(?<![低高位])有效|取樣|採樣|鎖存",
    re.IGNORECASE)
_ID_CH = r"A-Za-z0-9_$"
#: `<q> = 1`, `<q>==1'b1`, `<q> 為 0`, `<q> is 1`
_QUAL_BIT_TMPL = (r"(?<![{c}]){q}`?\s*(?:==?|為|是|is)\s*(?:1'b)?([01])(?![{c}'])")
#: `<q> high|asserted|拉高` / `<q> low|deasserted|拉低`
_QUAL_WORD_TMPL = (r"(?<![{c}]){q}`?\s*(?:is\s+|為)?\s*"
                   r"(asserted|deasserted|high|low|拉高|拉低|高|低)")
_QUAL_WORD_LEVEL = {"asserted": "1", "high": "1", "拉高": "1", "高": "1",
                    "deasserted": "0", "low": "0", "拉低": "0", "低": "0"}
#: What ends one statement in the L9 prose (sentences, table cells, lines).
_QUAL_SPLIT_RE = re.compile(r"[。;；|\n]|\.\s")


def _names(text: str, name: str) -> bool:
    return bool(re.search(rf"(?<![{_ID_CH}]){re.escape(name)}(?![{_ID_CH}])",
                          text))


def _qualifier_level(sentence: str, q: str) -> Optional[str]:
    """The ACTIVE level the sentence binds to `q`, or None."""
    qe = re.escape(q)
    m = re.search(_QUAL_BIT_TMPL.format(c=_ID_CH, q=qe), sentence)
    if m:
        return m.group(1)
    m = re.search(_QUAL_WORD_TMPL.format(c=_ID_CH, q=qe), sentence,
                  re.IGNORECASE)
    if m:
        return _QUAL_WORD_LEVEL[m.group(1).lower()]
    return None


def qualifiers_from_statements(
        statements: List[Tuple[Optional[str], str]],
        outputs: List[Tuple[str, str]],
        inputs: List[Tuple[str, str]],
        excluded: "Tuple[str, ...]" = (),
        data_outputs: "Optional[List[str]]" = None,
) -> Dict[str, List[Dict[str, str]]]:
    """{output: [{qualifier, active, evidence}]} from the design's own text.

    NOT CONSULTED BY THIS ORACLE (R-0929-X-QUALIFIED-4): `declared_output_
    qualifiers` reads only the D1-signed structured `qualified_by` field
    (`_qualified_by`). This text reader is kept as a pure function for
    callers that PROPOSE a field for review; its answer is never trusted
    as a declaration by itself.

    `statements` are (subject, text): a port-table row's description has its
    port as the implicit subject; a prose sentence has none and must name the
    qualified output itself. A qualifier is declared only when ONE statement
    names the output, names another 1-bit port, binds an active level to that
    port and carries a condition word -- and is not denied (`_prose_polarity`).
    Nothing is inferred from a port's NAME: an output the text never qualifies
    has no qualifier, and X on it after reset release is a FAIL.

    FULLSTACKTB final review (a false PASS): the clause must state VALIDITY
    (`_QUAL_VALIDITY_RE`), the qualified output must be a DATA output
    (`data_outputs`; by default, and always from `declared_output_qualifiers`,
    the multi-bit outputs), and the ports the testbench drives as CLOCK and RESET
    (`excluded`) are never a qualifier -- a reset-value sentence ("o_q cleared
    to 0 when rst_n is low") is the very invariant the X check tests, and read
    as a qualifier it exempted an UNRESET output after release.
    """
    import _prose_polarity as _pp
    one_bit = [n for n, w in (outputs + inputs)
               if not w and n not in excluded]
    out_names = [n for n, w in outputs if n not in excluded]
    if data_outputs is None:
        data_outputs = [n for n, w in outputs if w]
    found: Dict[str, List[Dict[str, str]]] = {}
    for subject, text in statements:
        for sent in _QUAL_SPLIT_RE.split(text or ""):
            sent = (sent or "").strip()
            if not sent or not _QUAL_COND_RE.search(sent) \
                    or not _QUAL_VALIDITY_RE.search(sent) \
                    or _pp.is_denied(sent):
                continue
            targets = [d for d in out_names if d in data_outputs
                       and (d == subject or _names(sent, d))]
            for d in targets:
                for q in one_bit:
                    if q == d or not _names(sent, q):
                        continue
                    level = _qualifier_level(sent, q)
                    if level is None:
                        continue
                    rows = found.setdefault(d, [])
                    if not any(r["qualifier"] == q for r in rows):
                        rows.append({"qualifier": q, "active": level,
                                     "evidence": sent[:200]})
    return found


def declared_output_qualifiers(
        project: Path, outputs: List[Tuple[str, str]],
        inputs: List[Tuple[str, str]],
) -> Dict[str, List[Dict[str, str]]]:
    """The qualifiers this oracle may honour: ONLY the D1-signed structured
    `qualified_by` field of the L9 port table (R-0929-X-QUALIFIED-4, via
    `_qualified_by.trusted_qualifiers`). This oracle never reads a port
    description: two versions that did produced false PASSes. The landed strict
    rules still bind: clock and reset never qualify, only a multi-bit output is
    qualified, and an unsigned or malformed field is ignored."""
    import _qualified_by as _qb
    excluded = tuple(n for n in (_pick_clock(inputs), _pick_reset(inputs)[0])
                     if n)
    found, _ignored = _qb.trusted_qualifiers(project, outputs, inputs,
                                             excluded=excluded)
    return found


def _text(case: dict) -> str:
    return f"{case.get('stimulus', '')} {case.get('expected', '')}"


def is_reset_hold_case(case: dict) -> bool:
    """True iff the case's OWN text says stored content survives reset assert."""
    t = _text(case)
    return bool(_RE_RESET_ASSERT.search(t) and _RE_PRESERVED.search(t)
                and _RE_STORAGE.search(t))


def is_reset_glitch_case(case: dict) -> bool:
    """True iff the case's OWN text says a reset glitch must not race."""
    t = _text(case)
    return bool(_RE_GLITCH.search(t) and _RE_RACE.search(t)
                and _RE_MUST_NOT.search(t))


def case_family(case: dict) -> Optional[str]:
    """Which invariant this case states, or None. A case that reads as BOTH is
    refused rather than guessed — an ambiguous grounding is not a grounding."""
    hold = is_reset_hold_case(case)
    glitch = is_reset_glitch_case(case)
    if hold and glitch:
        return None
    if hold:
        return FAMILY_HOLD
    if glitch:
        return FAMILY_GLITCH
    return None


def _pick(outputs: List[Tuple[str, str]], regexes) -> Optional[str]:
    for n, _w in outputs:
        if any(p.search(n) for p in regexes):
            return n
    return None


def _pick_clock(inputs: List[Tuple[str, str]]) -> Optional[str]:
    for n, w in inputs:
        if not w and _CLOCK_RE.search(n):
            return n
    return None


def _pick_reset(inputs: List[Tuple[str, str]]) -> Tuple[Optional[str], bool]:
    for n, w in inputs:
        if not w and _RESET_RE.search(n):
            return n, bool(_ACTIVE_LOW_RE.search(n))
    return None, False


def _vstr(text: str) -> str:
    """`text` safe inside a Verilog string literal passed to $display."""
    return (str(text).replace("\\", "/").replace('"', "'")
            .replace("%", " pct").replace("\n", " "))[:400]


def emit_case_oracle_from_ports(
    case: dict,
    dut: str,
    inputs: List[Tuple[str, str]],
    outputs: List[Tuple[str, str]],
    inouts: List[Tuple[str, str]],
    qualifiers: Optional[Dict[str, List[Dict[str, str]]]] = None,
) -> Optional[str]:
    """Core, project-I/O-free emitter. Returns TB text, or None (fail-closed).

    `qualifiers` is `declared_output_qualifiers`' answer. After the glitch is
    released every output is sampled on every one of `POST_RELEASE_CYCLES`
    cycles from the first edge (R-0929-X-QUALIFIED): an output with no declared
    qualifier must be a known 0/1 on each; one with declared qualifiers may be
    X/Z only on a cycle where EVERY qualifier is known and at its inactive
    level, and each such cycle is printed as an `X_EXEMPT` line."""
    qualifiers = qualifiers or {}
    name = case.get("name", "")
    family = case_family(case)
    if family is None:
        return None
    clk = _pick_clock(inputs)
    rst, rst_low = _pick_reset(inputs)
    if clk is None or rst is None:
        # No clock to sample on, or no reset to assert: this DUT surface does
        # not let the invariant be observed at all.
        return None
    observed = (_pick(outputs, _WRITE_RES) if family == FAMILY_HOLD
                else (_pick(outputs, _BUS_ACTIVITY_RES)
                      or _pick(outputs, _READ_ENABLE_RES)))
    if observed is None:
        return None

    asserted, released = ("0", "1") if rst_low else ("1", "0")
    L: List[str] = []
    L.append(f"// Auto-generated RESET-INVARIANT oracle TB for case={name}")
    L.append(f"// family={family} (reset_invariant_oracle_tb_gen)")
    L.append(f"// stimulus: {case.get('stimulus', '')}")
    L.append(f"// expected: {case.get('expected', '')}")
    L.append("//")
    if family == FAMILY_HOLD:
        L.append("// REAL ORACLE: the design's own declared text says stored")
        L.append("// content survives reset assert. The only pin-visible way")
        L.append(f"// content can change is a WRITE, so this TB holds '{rst}'")
        L.append(f"// asserted and FAILs if '{observed}' ever asserts inside")
        L.append("// that window. Necessary condition, stated at the pins; no")
        L.append("// memory model and no instruction semantics are invented.")
    else:
        L.append("// REAL ORACLE: the design's own declared text says a reset")
        L.append("// GLITCH must not cause a bus/fetch race. This TB runs the")
        L.append(f"// design, applies a ONE-CYCLE pulse on '{rst}', and FAILs if")
        L.append(f"// '{observed}' is asserted anywhere inside the pulse, or if")
        L.append("// any observable output is X/Z on any cycle after release")
        L.append("// (R-0929-X-QUALIFIED: X is allowed only on an output whose")
        L.append("// declared qualifier is known and inactive). It does NOT")
        L.append("// claim the converse: a race-free pulse is not a proof that")
        L.append("// the fetch which follows is correct.")
        for d, rows in sorted(qualifiers.items()):
            for r in rows:
                L.append(f"// qualifier (design input): '{d}' qualified by "
                         f"'{r['qualifier']}' active={r['active']} -- "
                         + r["evidence"].replace("\n", " ")[:120])
    L.append("`timescale 1ns/1ps")
    L.append(f"module {name};")
    for n, w in inputs:
        L.append(f"  reg {w + ' ' if w else ''}{n} = 0;")
    for n, w in outputs:
        L.append(f"  wire {w + ' ' if w else ''}{n};")
    for n, w in inouts:
        L.append(f"  wire {w + ' ' if w else ''}{n};")
    L.append("  integer errors = 0;")
    L.append("  integer _i;")
    L.append("")
    conns = ", ".join(f".{n}({n})" for n, _w in (inputs + outputs + inouts))
    L.append(f"  {dut} u_dut ({conns});")
    L.append("")
    L.append(f"  always #5 {clk} = ~{clk};")
    L.append("")
    L.append("  initial begin")
    if family == FAMILY_HOLD:
        L.append(f'    $display("[TB {name}] BEGIN — no write on \'{observed}\' '
                 f'while \'{rst}\' is asserted");')
        L.append(f"    {rst} = 1'b{asserted};")
        L.append("    for (_i = 0; _i < 32; _i = _i + 1) begin")
        # SAMPLE MID-CYCLE, NEVER ON THE EDGE. A registered output is
        # updated by a non-blocking assignment at the posedge, so a
        # check written at that same edge reads the PREVIOUS value and
        # a violation one cycle wide is invisible. MEASURED: the first
        # draft of the glitch oracle sampled on the edge and PASSED a
        # mutant that asserts the observed output throughout reset.
        L.append(f"      @(posedge {clk}); @(negedge {clk});")
        L.append(f"      if (({observed}) === 1'b1) begin")
        L.append("        errors = errors + 1;")
        L.append(f'        $display("[TB {name}] FAIL: \'{observed}\' asserted '
                 f'at reset-assert cycle %0d — stored content is not held", _i);')
        L.append("      end")
        L.append("    end")
        L.append(f"    {rst} = 1'b{released};")
    else:
        L.append(f'    $display("[TB {name}] BEGIN — no activity on '
                 f'\'{observed}\' inside a one-cycle \'{rst}\' glitch");')
        L.append(f"    {rst} = 1'b{asserted};")
        L.append("    repeat (4) @(posedge " + clk + ");")
        L.append(f"    {rst} = 1'b{released};")
        L.append("    repeat (8) @(posedge " + clk + ");")
        L.append("    // THE GLITCH. The pulse is held across ONE clock edge")
        L.append("    // and sampled mid-cycle while still asserted. That window")
        L.append("    // is not an arbitrary choice: on a synchronous design the")
        L.append("    // earliest the DUT can answer the pulse is the edge that")
        L.append("    // ends it, so a window that closes ON that edge can see")
        L.append("    // nothing, and one that stays open past the release flags")
        L.append("    // ordinary resumption as a race. MEASURED, both drafts:")
        L.append("    // the edge-sampled window PASSED a mutant that drives the")
        L.append("    // observed output throughout reset, and the widened one")
        L.append("    // FAILED the shipped design for restarting after release.")
        L.append(f"    {rst} = 1'b{asserted};")
        L.append(f"    @(posedge {clk});")
        L.append(f"    @(negedge {clk});")
        L.append(f"    if (({observed}) === 1'b1) begin")
        L.append("      errors = errors + 1;")
        L.append(f'      $display("[TB {name}] FAIL: \'{observed}\' asserted '
                 f'inside the reset glitch — a transaction races the reset");')
        L.append("    end")
        L.append(f"    {rst} = 1'b{released};")
        # R-0929-X-QUALIFIED — from the FIRST edge after release, every cycle.
        L.append(f"    for (_i = 0; _i < {POST_RELEASE_CYCLES}; _i = _i + 1) "
                 "begin")
        L.append(f"      @(posedge {clk}); @(negedge {clk});")
        for n, _w in outputs:
            rows = [r for r in qualifiers.get(n) or []
                    if r.get("active") in ("0", "1")]
            L.append(f"      if (^({n}) === 1'bx) begin")
            if rows:
                inactive = " && ".join(
                    f"({r['qualifier']} === 1'b{1 - int(r['active'])})"
                    for r in rows)
                shown = ", ".join(f"{r['qualifier']}=%b" for r in rows)
                args = ", ".join(r["qualifier"] for r in rows)
                L.append(f"        if ({inactive})")
                basis = _vstr("; ".join(r.get("evidence", "")
                                        for r in rows))
                L.append(f'          $display("[TB {name}] X_EXEMPT: cycle %0d '
                         f'after release: output \'{n}\' is X/Z while its '
                         f'declared qualifier(s) are inactive and known: '
                         f'{shown} -- basis: {basis}", _i, {args});')
                L.append("        else begin")
                L.append("          errors = errors + 1;")
                L.append(f'          $display("[TB {name}] FAIL: output '
                         f'\'{n}\' is X/Z at cycle %0d after the glitch was '
                         f'released while a declared qualifier is asserted or '
                         f'unknown: {shown}", _i, {args});')
                L.append("        end")
            else:
                L.append("        errors = errors + 1;")
                L.append(f'        $display("[TB {name}] FAIL: output \'{n}\' '
                         f'is X/Z at cycle %0d after the glitch was released '
                         f'(no qualifier declared for it in the design input)",'
                         f' _i);')
            L.append("      end")
        L.append("    end")
    L.append("    if (errors != 0) begin")
    L.append(f'      $display("[TB {name}] FAIL — %0d check(s) failed", errors);')
    L.append("      $fatal(1);")
    L.append("    end")
    L.append(f'    $display("[TB {name}] PASS — {family} holds");')
    L.append("    $finish;")
    L.append("  end")
    L.append("endmodule")
    return "\n".join(L) + "\n"


def _load_case(project: Path, case_name: str) -> Optional[dict]:
    f = Path(project) / "phase1" / "generated_docs" / "L10_TEST_CASES.json"
    try:
        doc = json.loads(f.read_text(errors="replace"))
    except Exception:
        return None
    for row in (doc.get("test_cases") or doc.get("cases") or []):
        if row.get("name") == case_name:
            return row
    return None


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project")
    ap.add_argument("--case", required=True)
    ap.add_argument("--top", default="chip_top")
    a = ap.parse_args(argv)
    case = _load_case(Path(a.project), a.case)
    if case is None:
        print(f"no declared L10 case named {a.case!r}", file=sys.stderr)
        return 2
    fam = case_family(case)
    print(f"case={a.case} family={fam or 'NOT_A_RESET_INVARIANT_CASE'}")
    return 0 if fam else 1


if __name__ == "__main__":
    raise SystemExit(main())
