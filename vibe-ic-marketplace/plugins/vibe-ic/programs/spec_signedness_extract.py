#!/usr/bin/env python3
"""spec_signedness_extract.py — PROGRAM-FIRST per-SIGNAL signedness extractor
(chip-AGNOSTIC, §4.05 no-leak).

WHY THIS EXISTS
---------------
`spec_coverage_check.py` already carries a COARSE prose-heuristic checklist kind
for the signedness topic:

  * kind="signedness" — a single keyword hit on
        signed | unsigned | two's complement
    (one item per spec, NO named signal, NO per-operand binding, NO polarity).

That answers "is the topic MENTIONED?". It does NOT record the STRUCTURE the
author actually has to implement: WHICH specific signal/operand is signed, or
the explicit `signed` range declaration that ties a width to a NAMED port. A
verification FAILURE on an arithmetic design is — per the spec-coverage doctrine
— almost always one of OUR OWN extraction gaps (a signed operand we never read
out / never sign-extended / compared with the wrong relational operator), not
an unfixable floor: a `>` on two operands the spec called signed must be a
SIGNED comparison, and the hidden testbench exercises exactly that.

This module is the STRUCTURAL extension. It returns richer items keyed on a new
kind so a downstream consumer (or `spec_coverage_check`'s checklist) can demand
TB coverage of the *specific* signed operand rather than the topic in general:

    kind="signed_operand" — a NAMED signal/operand bound to a signedness
                            polarity (signed / unsigned), anchored to either a
                            Verilog `signed` range declaration tied to a name or
                            an explicit prose statement "<name> is signed".

EXTEND-NOT-DUPLICATE
--------------------
We do NOT re-emit the bare `signedness` keyword item — `spec_coverage_check`
still OWNS that coarse topic. We only emit the STRUCTURAL refinement it lacks: a
PER-SIGNAL signedness fact tied to a NAMED signal (or an explicit `signed` range
declaration). A consumer can union our items with spec_coverage_check's
checklist; a bare "signed arithmetic" with NO named signal yields NOTHING here
(that bare topic remains spec_coverage_check's coarse kind, not ours).

WHAT COUNTS (the §4.05 no-leak boundary)
  We emit ONLY when a NAMED signal is bound to a signedness polarity:
    * a Verilog `signed` range tied to a name — `signed [N:0] name`,
      `input signed [15:0] coeff`, or `name` declared with the `signed`
      keyword;
    * prose binding a name to signedness — "signed input X", "X is a signed
      value", "signed operand X", "treat X as signed", "two's complement X" /
      "X in two's complement".
  We do NOT emit for "unsigned" DEFAULTS — unsigned is the convention, so only a
  POSITIVE signed declaration is a fact worth carrying — EXCEPT when prose
  EXPLICITLY contrasts "X is unsigned" against a signed sibling: there the
  unsigned word is itself a deliberate, named polarity statement and MAY be
  emitted with the unsigned polarity captured. We stay conservative: an explicit
  signed/unsigned WORD must be tied to a NAME. A bare "signed arithmetic" /
  "perform a signed multiply" with NO named operand returns []. So does
  `extract("add two numbers")`.

  chip-AGNOSTIC: pure signedness grammar (the `signed` keyword shape, a
  signedness adjective bound to an identifier); NO chip / vendor / SKU /
  problem-id literal (enforced by `programs/source_chip_agnostic_check.py .`).

CONTRACT
  Each emitted dict is shaped to seed a `spec_coverage_check.ChecklistItem`:
    {
      "kind":            "signed_operand",
      "requirement":     human-readable testable requirement,
      "evidence":        the EXACT declaration / prose phrase it came from,
      "coverage_tokens": [the signal NAME, ...],   # always carries the name
      "provenance":      "STRUCTURAL",             # (default)
      "signal":          <the named signal>,
      "polarity":        "signed" | "unsigned",
      "block_eligible":  True,                     # (optional)
    }

  #2152 — an ADVISORY variant is emitted when the prose states a signedness
  requirement whose NOUN resolves to no declared signal in the doc. It carries
  the same kind, the phrase in "evidence", and:

      "coverage_tokens":    [],            # NEVER the prose noun
      "provenance":         "PROSE_HEURISTIC",
      "signal":             None,
      "block_eligible":     False,         # disclosed, never blocking
      "unresolved_operand": True,
      "unresolved_phrase":  <the noun the prose used>

  Making the English noun the coverage token forced a faithful testbench to
  write that noun into code to be accepted ("wires", "division"), which is
  indistinguishable — to the gate — from a real coverage improvement.
  `spec_coverage_check.run()` retargets the advisory item onto the operands the
  RTL actually treats as signed (a `wire signed [N:0] sa = a;` alias resolves to
  both `sa` and `a`), so a faithful TB is credited for what it really drives.

CLI
    python3 spec_signedness_extract.py <prompt.txt> [--json]
    cat prompt.txt | python3 spec_signedness_extract.py -
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Identifier / deny-set grammar (chip-AGNOSTIC, pure structural shape)
# ---------------------------------------------------------------------------
# A signal/operand NAME: a Verilog identifier (letter/underscore start). We do
# NOT require a particular case so din / coeff / acc_q / DATA_IN all qualify.
_IDENT = r"[A-Za-z_]\w*"
_IDENT_RE = re.compile(_IDENT)

# Tokens that look like an identifier in the `signed <NAME>` slot but are NOT a
# signal name — Verilog type/qualifier words and signedness words themselves, so
# `signed integer`, `signed logic`, `signed reg`, `signed value` (no following
# name) never mint a phantom signal. chip-AGNOSTIC (generic Verilog/English).
_NON_SIGNAL_WORDS = {
    "signed", "unsigned", "integer", "int", "logic", "reg", "wire", "bit",
    "byte", "shortint", "longint", "time", "real", "value", "values", "number",
    "numbers", "operand", "operands", "input", "inputs", "output", "outputs",
    "data", "result", "arithmetic", "comparison", "multiply", "multiplication",
    "addition", "subtraction", "type", "format", "representation", "vector",
    "and", "or", "the", "a", "an", "two", "twos", "complement",
}


def _is_signal_name(tok: str) -> bool:
    """A real signal-name token: identifier-shaped, >=1 char, NOT a Verilog
    type/qualifier or a signedness word. chip-AGNOSTIC — generic deny-set, no
    design literal. This is the §4.05 guard that keeps `signed value` /
    `signed integer` from minting a phantom operand."""
    if not tok:
        return False
    if not _IDENT_RE.fullmatch(tok):
        return False
    return tok.lower() not in _NON_SIGNAL_WORDS


# A REAL declaration head: a direction/type keyword that STARTS a declaration —
# at a line start, or after a `;` / `,` / `(` / a list bullet / a table pipe. The
# anchor is the whole point: without it the clause `input|output|...` matches any
# ENGLISH SENTENCE that merely contains a direction word earlier on the line
# ("the input operands to the signed wires", "8-bit input signal representing the
# dividend for division"), and the prose then CORROBORATES ITSELF (#2152).
_DECL_HEAD = (r"(?:^|[;,(\n])[ \t]*(?:[-*+\u2022>]|\|)?[ \t]*"
              r"(?:(?:input|output|inout)\b(?:[ \t]+(?:wire|reg|logic|var|bit))?"
              r"|(?:wire|reg|logic|var|bit|integer)\b)"
              r"(?:[ \t]+(?:signed|unsigned))?"
              r"(?:[ \t]*\[[^\]\n]*\])*"
              r"[ \t]*")
# the comma-separated identifier list a declaration head introduces
_DECL_NAMES = r"(" + _IDENT + r"(?:[ \t]*,[ \t]*" + _IDENT + r")*)"
# ... and the name list must END a declaration. A LINE THAT BEGINS WITH A
# DIRECTION WORD IS STILL ORDINARY ENGLISH HALF THE TIME — "Input signals are the
# signed signals of the datapath" would otherwise "declare" `signals` and
# reproduce #2152 with a different noun (measured). A real declaration stops at
# `,` `;` `)` `=` `[`, a markdown cell `|`, or the end of the line; English
# carries on into a verb.
_DECL_TAIL = r"[ \t]*(?=[,;)=\[|]|$)"
_DECL_RE = re.compile(_DECL_HEAD + _DECL_NAMES + _DECL_TAIL,
                      re.IGNORECASE | re.MULTILINE)


def _declared_names(text: str) -> set:
    """Every identifier introduced by a REAL declaration head in `text`.

    chip-AGNOSTIC: pure Verilog/port-table declaration grammar."""
    out = set()
    for m in _DECL_RE.finditer(text):
        for nm in m.group(1).split(","):
            nm = nm.strip()
            if nm and _IDENT_RE.fullmatch(nm):
                out.add(nm)
    return out


def _declared_signal(name: str, text: str) -> bool:
    """True iff `name` is corroborated as a REAL declared/used signal in the doc:
    a Verilog port/signal declaration whose DECLARED IDENTIFIER is `name`, a
    backtick `name`, or a bus-index `name[..`.

    §4.05 NO-LEAK: a deny-set alone cannot enumerate every English noun, so a
    PROSE signedness phrase ("unsigned integers", "the unsigned add result") would
    otherwise mint a phantom operand from a common word that merely follows
    signed/unsigned. Requiring the captured token to ALSO appear as a declared /
    bracket-indexed / backtick signal kills that leak: `add`/`integers` are not
    declared signals; `coeff`/`acc`/`din` are. A Verilog `signed [..] name`
    declaration is self-corroborating and bypasses this gate (it IS a decl).

    #2152: the declaration clause is ANCHORED at a declaration head. The
    unanchored form accepted any line on which a direction word appeared BEFORE
    the noun, so an English sentence corroborated itself — `wires` "declared" by
    "the input operands to the signed wires", `division` by "8-bit input signal
    representing the dividend for division". Both then became the blocking
    coverage token of a signedness obligation no faithful testbench can satisfy
    without writing the noun into code."""
    esc = re.escape(name)
    if re.search(r"`\s*" + esc + r"\s*`", text):          # backtick `name`
        return True
    if re.search(r"\b" + esc + r"\s*\[", text):           # bus index name[..]
        return True
    return name in _declared_names(text)                  # real declaration


# ---------------------------------------------------------------------------
# (A) Verilog `signed` RANGE DECLARATION tied to a NAMED signal.
# ---------------------------------------------------------------------------
# Forms (the `signed` keyword + an eventual identifier on the same declaration):
#   "signed [15:0] coeff"
#   "input signed [7:0] din"
#   "input wire signed [N-1:0] a"
#   "output reg signed [WIDTH-1:0] acc"
#   "logic signed [3:0] x"
# The range is OPTIONAL (a `signed` scalar `input signed s` is still a signed
# operand). We capture the FIRST identifier that follows `signed` (+ optional
# range + optional type/qualifier words), skipping type/qualifier deny-words.
# §4.05: the `signed` keyword MUST be present and bound to a real name.
# #2152: the `signed` keyword ALONE, followed by any word, is not a declaration —
# it is also ordinary English ("assigns the input operands to the signed wires").
# The unqualified form made the PROSE NOUN `wires` a self-corroborating "declared
# signal" and then the blocking coverage token of a signedness obligation. A
# self-corroborating declaration must carry a real Verilog marker, so we accept
# only two shapes:
#   (a) a PACKED RANGE sits between `signed` and the name — `signed [15:0] coeff`,
#       `input wire signed [N-1:0] a`. A range is unambiguous Verilog.
#   (b) NO range, but `signed` is introduced by a direction/type keyword AND the
#       name ends the declaration (`,` `;` `=` `)` `[` or end of line) —
#       `input signed s;`, `wire signed sa = a,`.
# Everything else falls through to the PROSE patterns below, which must
# corroborate the captured name against a REAL declaration (`_declared_signal`).
_SIGNED_DECL_RANGE_RE = re.compile(
    r"\bsigned\b"
    r"\s*\[[^\]\n]*\]"              # REQUIRED packed range [hi:lo]
    r"\s+"
    r"(" + _IDENT + r")",            # the candidate name immediately after
    re.IGNORECASE)
_SIGNED_DECL_SCALAR_RE = re.compile(
    r"\b(?:input|output|inout|wire|reg|logic|var|bit)\b"
    r"(?:\s+(?:wire|reg|logic|var|bit))?"
    r"\s+signed\s+"
    r"(" + _IDENT + r")"             # the candidate name
    r"\s*(?=[,;=)\[]|$)",            # ... ending a declaration
    re.IGNORECASE | re.MULTILINE)

# Also catch the `<qualifier> signed <range> <name>` where a type word sits
# BEFORE `signed` (input/output/wire/reg/logic signed ...). The name capture is
# the same as above (it sits after `signed`), so _SIGNED_DECL_RE already covers
# it — this comment documents the coverage; no extra pattern needed.

# A name declared signed with the keyword AFTER the name is rare in Verilog but
# appears in prose-y skeletons: "coeff is declared signed", "acc, signed,".
# Handled by the prose patterns below, not here.


# ---------------------------------------------------------------------------
# (B) PROSE binding a NAME to a signedness polarity.
# ---------------------------------------------------------------------------
# (B1) "signed <kind> <NAME>" / "signed <NAME>" — adjective BEFORE the name:
#        "signed input din", "signed operand acc", "a signed value coeff".
#      We allow up to two intervening type/role words (input/operand/value/...)
#      then capture the name; the intervening words are bounded by the deny-set
#      so we land on the real identifier.
_PROSE_ADJ_BEFORE_RE = re.compile(
    r"\b(signed|unsigned)\s+"
    r"(?:(?:input|output|operand|value|number|data|port|signal|reg|wire|"
    r"logic|bus|vector)\s+){0,2}"
    r"(" + _IDENT + r")\b",
    re.IGNORECASE)

# (B2) "<NAME> is (a/an) signed ..." / "<NAME> is unsigned" — the name BEFORE an
#      "is ... signed/unsigned" predicate:
#        "operand acc is treated as two's complement",
#        "din is a signed value", "X is unsigned".
#      Capture the LAST identifier before the `is ... signed/unsigned` so a role
#      word ("operand acc is signed") lands on `acc`, not `operand`.
_PROSE_IS_SIGNED_RE = re.compile(
    r"\b(" + _IDENT + r")\s+is\s+"
    r"(?:(?:a|an|the)\s+)?"
    r"(?:(?:value|number|signal|operand|input|output)\s+)?"
    r"(?:treated\s+as\s+|interpreted\s+as\s+|represented\s+(?:as|in)\s+)?"
    r"(?:(?:a|an|the)\s+)?"
    r"(signed|unsigned|two'?s\s+complement)\b",
    re.IGNORECASE)

# (B3) "treat <NAME> as signed" / "interpret <NAME> as two's complement":
_PROSE_TREAT_RE = re.compile(
    r"\b(?:treat|interpret|consider|regard)\s+"
    r"(?:(?:the|a|an)\s+)?"
    r"(?:(?:value|operand|input|output|signal|port)\s+)?"
    r"(" + _IDENT + r")\s+as\s+"
    r"(?:(?:a|an|the)\s+)?"
    r"(signed|unsigned|two'?s\s+complement)\b",
    re.IGNORECASE)

# (B4) "two's complement <NAME>" / "<NAME> in two's complement" — two's
#      complement is a signed representation; the NAMED operand it qualifies is a
#      signed operand.
_PROSE_TWOS_BEFORE_RE = re.compile(
    r"\btwo'?s\s+complement\s+"
    r"(?:(?:input|output|operand|value|number|signal)\s+){0,2}"
    r"(" + _IDENT + r")\b",
    re.IGNORECASE)
_PROSE_TWOS_AFTER_RE = re.compile(
    r"\b(" + _IDENT + r")\s+(?:is\s+|are\s+|,\s*)?"
    r"in\s+two'?s\s+complement\b",
    re.IGNORECASE)


def _norm_polarity(word: str) -> str:
    """Map a matched signedness word to a normalized polarity tag. Two's
    complement is a SIGNED representation. chip-AGNOSTIC."""
    w = word.strip().lower()
    if w.startswith("unsigned"):
        return "unsigned"
    return "signed"   # "signed" or "two's complement"


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------
# A POSITIVE `signed` statement somewhere in the doc — the "explicit contrast"
# the module doctrine carves out for an UNSIGNED polarity word. `\b` never fires
# inside "unsigned" (both neighbours are word characters), so this matches only a
# real signed sibling. chip-AGNOSTIC.
_POSITIVE_SIGNED_RE = re.compile(r"\bsigned\b|\btwo'?s\s+complement\b", re.I)


def _collect(text: str) -> List[Tuple[str, str, str, bool]]:
    """Return [(signal_name, polarity, evidence, resolved)] for every EXPLICIT
    per-signal signedness binding. De-duplicated by (signal, polarity); the FIRST
    evidence is kept. §4.05: only a NAMED signal bound to a signedness word is
    emitted — a bare topic mention with no name yields nothing here.

    #2152 — a PROSE noun that does NOT resolve to a real declared signal is no
    longer silently dropped: it is carried with resolved=False so `extract()` can
    DISCLOSE the signedness requirement as an ADVISORY item (never blocking, and
    never with the noun as a coverage token) instead of either (a) minting the
    noun as a blocking coverage token, or (b) losing the requirement entirely.
    An UNSIGNED unresolvable noun is disclosed only when the doc also carries a
    POSITIVE signed statement — an "unsigned" word with no signed sibling is the
    Verilog default, which this module deliberately does not carry as a fact."""
    found: Dict[Tuple[str, str], Tuple[str, bool]] = {}

    def _add(name: str, polarity: str, evidence: str,
             corroborate: bool = False) -> None:
        nm = name.strip().strip("`*")
        if not _is_signal_name(nm):
            return
        # §4.05: a PROSE-derived name must be corroborated as a real declared/used
        # signal (a Verilog `signed [..] name` decl is self-corroborating and
        # passes corroborate=False). An uncorroborated common-noun operand
        # ("unsigned integers", "the unsigned add", "the signed wires") is NOT a
        # signal: it is carried resolved=False and can only ever be advisory.
        resolved = (not corroborate) or _declared_signal(nm, text)
        if not resolved:
            if polarity == "unsigned" and not _POSITIVE_SIGNED_RE.search(text):
                return          # a bare unsigned default with no signed sibling
        key = (nm, polarity)
        if key not in found or (resolved and not found[key][1]):
            found[key] = (evidence.strip()[:140], resolved)

    # (A) Verilog `signed [range] name` declarations — always signed polarity
    #     (the keyword `signed` is, by definition, a positive signed assertion).
    #     Self-corroborating: the match IS a declaration.
    for _rx in (_SIGNED_DECL_RANGE_RE, _SIGNED_DECL_SCALAR_RE):
        for m in _rx.finditer(text):
            _add(m.group(1), "signed", m.group(0))

    # (B1..B4) PROSE bindings — corroborate the captured NAME against a real decl.
    # (B1) "signed/unsigned <role> <name>"
    for m in _PROSE_ADJ_BEFORE_RE.finditer(text):
        _add(m.group(2), _norm_polarity(m.group(1)), m.group(0), corroborate=True)

    # (B2) "<name> is ... signed/unsigned/two's complement"
    for m in _PROSE_IS_SIGNED_RE.finditer(text):
        _add(m.group(1), _norm_polarity(m.group(2)), m.group(0), corroborate=True)

    # (B3) "treat/interpret <name> as signed/unsigned/two's complement"
    for m in _PROSE_TREAT_RE.finditer(text):
        _add(m.group(1), _norm_polarity(m.group(2)), m.group(0), corroborate=True)

    # (B4) "two's complement <name>" / "<name> in two's complement"
    for m in _PROSE_TWOS_BEFORE_RE.finditer(text):
        _add(m.group(1), "signed", m.group(0), corroborate=True)
    for m in _PROSE_TWOS_AFTER_RE.finditer(text):
        _add(m.group(1), "signed", m.group(0), corroborate=True)

    # a name resolved under ANY polarity is a real signal: drop its unresolved
    # twins so one binding is never reported both ways.
    _resolved_names = {nm for (nm, _p), (_e, r) in found.items() if r}
    return [(nm, pol, ev, res)
            for (nm, pol), (ev, res) in found.items()
            if res or nm not in _resolved_names]


# ===========================================================================
# Public API
# ===========================================================================
def extract(prompt_text: str) -> List[dict]:
    """Extract structural PER-SIGNAL signedness facts from a CVDP-style prompt.

    Returns a list of dicts (one per explicit named-signal signedness binding).
    Each dict carries: kind, requirement, evidence, coverage_tokens (the signal
    NAME), provenance (default "STRUCTURAL"), signal, polarity, block_eligible.

    §4.05 no-leak: emits ONLY when a NAMED signal is bound to a signedness
    polarity (a `signed` range declaration tied to a name, or explicit prose
    "<name> is signed" / "treat <name> as signed" / "two's complement <name>").
    A bare "signed arithmetic" with NO named signal returns [] (that coarse
    topic is spec_coverage_check's kind="signedness", not ours). chip-AGNOSTIC.

    #2152 — a prose signedness statement whose noun resolves to NO declared
    signal is neither dropped nor minted as a coverage token: it is emitted as
    the ADVISORY variant documented in the module CONTRACT above (block_eligible
    False, coverage_tokens []). An UNSIGNED noun with no positive `signed`
    sibling in the doc still returns nothing — that is the Verilog default, not
    a fact this module carries.
    """
    if not prompt_text or not isinstance(prompt_text, str):
        return []

    items: List[dict] = []
    for name, polarity, evidence, resolved in _collect(prompt_text):
        if not resolved:
            # #2152 — the prose names no signal that exists in the design. The
            # signedness REQUIREMENT is real and is disclosed with its phrase,
            # but it carries NO coverage token: making the English noun the token
            # is what forced a faithful testbench to write the noun into code to
            # be accepted. ADVISORY (block_eligible=False) — never blocking.
            items.append({
                "kind": "signed_operand",
                "requirement": (
                    f"the spec states a {polarity} requirement in the phrase "
                    f"\"{evidence}\", but \"{name}\" is not a signal declared in "
                    f"the design: the operand cannot be resolved, so this "
                    f"obligation is ADVISORY — the TB must still exercise "
                    f"{polarity}-relevant stimulus, but no named operand can be "
                    f"attributed and the phrase noun is NOT a coverage token."),
                "evidence": evidence,
                "coverage_tokens": [],
                "provenance": "PROSE_HEURISTIC",
                "signal": None,
                "polarity": polarity,
                "block_eligible": False,
                "unresolved_operand": True,
                "unresolved_phrase": name,
            })
            continue
        req = (f"operand {name} is declared {polarity}; the design must treat "
               f"{name} as a {polarity} value (sign-extension / "
               f"{'signed' if polarity == 'signed' else 'unsigned'} "
               f"comparison / arithmetic) and the TB must exercise it with "
               f"{polarity}-relevant stimulus"
               + (" (negative / sign-boundary values)"
                  if polarity == "signed" else "") + ".")
        items.append({
            "kind": "signed_operand",
            "requirement": req,
            "evidence": evidence,
            "coverage_tokens": [name],
            "provenance": "STRUCTURAL",
            "signal": name,
            "polarity": polarity,
            "block_eligible": True,
        })
    return items


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="PROGRAM-FIRST per-signal signedness extractor "
                    "(named signed/unsigned operands). chip-AGNOSTIC, "
                    "§4.05 no-leak.")
    ap.add_argument("prompt", help="prompt file ('-' for stdin)")
    ap.add_argument("--json", action="store_true",
                    help="emit the raw checklist-item list as JSON")
    args = ap.parse_args(argv)

    try:
        if args.prompt == "-":
            text = sys.stdin.read()
        else:
            with open(args.prompt, "r", encoding="utf-8",
                      errors="replace") as fh:
                text = fh.read()
    except OSError as e:
        print("error: cannot read prompt: " + str(e), file=sys.stderr)
        return 2

    items = extract(text)
    if args.json:
        print(json.dumps(items, indent=2))
        return 0

    if not items:
        print("NO PER-SIGNAL SIGNEDNESS (no named signal bound to "
              "signed/unsigned) -> [] (no fabrication; bare 'signed "
              "arithmetic' is spec_coverage_check's coarse kind)")
        return 0

    resolved = [it for it in items if not it.get("unresolved_operand")]
    advisory = [it for it in items if it.get("unresolved_operand")]
    if resolved:
        print("SIGNED OPERANDS (" + str(len(resolved)) + "):")
        for it in resolved:
            print("  - " + str(it["signal"]) + " [" + it["polarity"] + "]   ["
                  + it["evidence"][:70] + "]")
    if advisory:
        print("ADVISORY — signedness stated, operand UNRESOLVED ("
              + str(len(advisory)) + "):")
        for it in advisory:
            print("  - phrase [" + it["evidence"][:70] + "] names \""
                  + str(it.get("unresolved_phrase")) + "\", which is not a "
                  "declared signal; non-blocking, no coverage token")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
