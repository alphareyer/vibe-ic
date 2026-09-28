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

import json
import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

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


def emit_case_oracle_from_ports(
    case: dict,
    dut: str,
    inputs: List[Tuple[str, str]],
    outputs: List[Tuple[str, str]],
    inouts: List[Tuple[str, str]],
) -> Optional[str]:
    """Core, project-I/O-free emitter. Returns TB text, or None (fail-closed)."""
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
        L.append("// any observable output is left X/Z after release. It does")
        L.append("// NOT claim the converse: a race-free pulse is not a proof")
        L.append("// that the fetch which follows is correct.")
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
        L.append("    repeat (8) @(posedge " + clk + ");")
        for n, _w in outputs:
            L.append(f"    if (^({n}) === 1'bx) begin")
            L.append("      errors = errors + 1;")
            L.append(f'      $display("[TB {name}] FAIL: output \'{n}\' is X/Z '
                     f'after the glitch was released");')
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
