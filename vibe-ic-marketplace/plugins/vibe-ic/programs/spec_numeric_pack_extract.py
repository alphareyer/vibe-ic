#!/usr/bin/env python3
"""spec_numeric_pack_extract.py — PROGRAM-FIRST numeric-semantics + packing
extractor (chip-AGNOSTIC, §4.05 no-leak).

WHY THIS EXISTS
---------------
`spec_coverage_check.py` already carries COARSE prose-heuristic checklist kinds
for the numeric/packing family:

  * kind="overflow"   — a single keyword hit on
        overflow|underflow|saturat*|wrap-around|rounding|truncat*|clip*
    (one item per spec, no mode, no tie-break, no flag).
  * kind="byte_order" — a single keyword hit on
        little/big-endian | msb/lsb-first | byte/bit order | byte packing
    (one item, no in/out width, no ratio).
  * kind="signedness" — signed | unsigned | two's complement.
  * a TB-side ceil/floor idiom oracle (`_tb_exercises_rounding`).

Those answer "is the topic MENTIONED?". They do NOT record the STRUCTURE the
author actually has to implement: WHICH rounding mode, the tie-break rule, the
inexact/cout/r_up status flags, or a concrete input->output WIDTH RATIO
(32->8 downscale, 8->16 upscale, a concat of N inputs), nor the byte-enable
(tkeep/tstrb) semantics.

This module is the STRUCTURAL extension. It returns richer items keyed on new
kinds so a downstream consumer (or `spec_coverage_check`'s checklist) can demand
TB coverage of the *specific* mode / ratio rather than the topic in general:

    kind="rounding_mode"  — a NAMED rounding mode + tie-break + status flags
    kind="saturation"     — an explicit saturate/clamp-on-overflow requirement
    kind="width_convert"  — a stated in_width -> out_width (ratio) / concat / pick
    kind="byte_order"     — endian / byte-order + (optional) byte-enable width

EXTEND-NOT-DUPLICATE
--------------------
We REUSE spec_coverage_check's kind names where they already exist
(`byte_order`) and ADD the three new ones (`rounding_mode`, `saturation`,
`width_convert`). We do NOT re-emit a bare `overflow`/`signedness` keyword item
(spec_coverage_check still owns those); we only emit the STRUCTURAL refinement
it lacks. A consumer can union our items with spec_coverage_check's checklist.

§4.05 NO-LEAK CONTRACT
----------------------
Every emitted item is anchored to an EXPLICIT statement in the prompt:
  * a rounding_mode item requires a NAMED mode (round-to-nearest-even / RTZ /
    ceil / floor / truncate / round-half-up / round-away-from-zero / a 3'bXXX
    mode legend) — a bare "compute the result" yields NOTHING.
  * a width_convert item requires a STATED numeric width pair (NN-bit -> MM-bit,
    "downscale from 16 to 8", "select bits [hi:lo]", "concatenate N M-bit") —
    an unstated ratio yields NOTHING.
  * a byte_order item requires an explicit endian / byte-order / byte-enable
    token.
`extract("perform the computation")` returns `[]`.

chip-AGNOSTIC: pure numeric/idiom grammar; NO chip / vendor / SKU literal
(enforced by `programs/source_chip_agnostic_check.py .`).

Enforcement: this extractor supplies blocking structural obligations to the
strict coverage / AI-review consumers. An elaboration-only width helper is
not a runtime rounding mode; real arithmetic modes remain blocking there.

API
    extract(prompt_text: str) -> list[dict]
        each dict: {kind, requirement, evidence, ...structured fields}

CLI
    python3 spec_numeric_pack_extract.py PROMPT.txt
    cat PROMPT.txt | python3 spec_numeric_pack_extract.py -
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


import json
import re
import sys
from typing import Dict, List, Optional, Tuple

from _prose_polarity import (DENIAL_RETIRED_RE, is_denied,
                             sentence_scope)

# ---------------------------------------------------------------------------
# Comment / fence stripping is NOT done here: a CVDP prompt's status-flag and
# mode definitions frequently live in the markdown prose AND in the embedded
# code skeleton (localparam RNE = 3'b000), and both are legitimate explicit
# statements of intent. We DO de-duplicate so a mode stated in both prose and
# skeleton is reported once.
# ---------------------------------------------------------------------------


# ===========================================================================
# (1) ROUNDING / SATURATION SEMANTICS
# ===========================================================================

# A canonical rounding-mode vocabulary. Each entry maps an EXPLICIT spec phrase
# to a normalized mode tag. The phrase regexes are anchored on whole multi-word
# idioms so an incidental bare "round" (e.g. "around the loop") never fires —
# §4.05 no-leak: only a NAMED mode is emitted.
_MODE_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    # round-to-nearest-even / banker's rounding / convergent / round-half-even
    ("round_to_nearest_even", re.compile(
        r"\b(round(?:ed|ing|s)?[ \-]?(?:to[ \-]?)?nearest[ \-,]*even|"
        r"nearest[ \-,]*even|round[ \-]?half[ \-]?(?:to[ \-]?)?even|"
        r"banker'?s?[ \-]?round\w*|convergent[ \-]?round\w*|"
        r"\bRNE\b|gaussian[ \-]?round\w*)", re.I)),
    # round to nearest, ties away from zero / round-half-up / nearest max-mag
    ("round_nearest_ties_away", re.compile(
        r"\b(round[ \-]?half[ \-]?up|round(?:ed|ing|s)?[ \-]?(?:to[ \-]?)?"
        r"nearest[ \-,]*(?:max\w*[ \-]?magnitude|ties?[ \-]?away|away)|"
        r"nearest[ \-,]*max\w*[ \-]?magnitude|\bRMM\b)", re.I)),
    # round toward zero / arithmetic truncation / chop.  Bare ``truncation``
    # is also a lint diagnosis for accidental width loss, not a named rounding
    # policy.  Require rounding/fractional/toward-zero context for that word.
    ("round_toward_zero", re.compile(
        r"\b(round(?:ed|ing|s)?[ \-]?(?:toward|towards|to)[ \-]?zero|"
        r"\bRTZ\b|(?<=rounding\s)truncat\w*|"
        r"truncat\w*\s+(?:the\s+)?(?:fractional(?:\s+part)?|fraction|"
        r"decimal\s+part)|truncat\w*(?:\s+rounding)?\s+"
        r"(?:toward|towards|to)\s+zero|\bchop\b)", re.I)),
    # ceiling / round toward +inf / round up
    ("round_ceiling", re.compile(
        r"\b(ceil\w*|round(?:ed|ing|s)?[ \-]?(?:toward|towards|to)?[ \-]?"
        r"(?:positive[ \-]?infinity|\+?\s*inf\w*|up\b)|\bRUP\b)", re.I)),
    # floor / round toward -inf / round down.  A bare ``floor\w*`` is not a
    # rounding mode: phase-1 documents routinely contain ``floorplan_hints``
    # and building-control prompts contain ordinary floors.  Require either
    # explicit rounding context or a floor operation applied to a numeric
    # result.
    ("round_floor", re.compile(
        r"\b(floor[ \-]?(?:round\w*|mode|behavior|function|operation)|"
        r"(?:rounding[ \-]?mode(?:\s+is|:)?|use)\s+floor\b|floor\s*\(|"
        r"(?:floor(?:ed|ing)\s+(?:the\s+)?|floor\s+the\s+)"
        r"(?:value|result|quotient|output|number|operand)\b|"
        r"round(?:ed|ing|s)?[ \-]?(?:toward|towards|to)?[ \-]?"
        r"(?:negative[ \-]?infinity|-\s*inf\w*|down\b)|\bRDN\b)", re.I)),
    # round away from zero (sign-independent magnitude bump)
    ("round_away_from_zero", re.compile(
        r"\b(round(?:ed|ing|s)?[ \-]?away[ \-]?from[ \-]?zero|"
        r"away[ \-]?from[ \-]?zero)", re.I)),
]

# A tie-break statement: "ties resolved by ...", "tie break(ing)", "on a tie".
_TIEBREAK_RE = re.compile(
    r"\b(tie[ \-]?break\w*|ties?\s+(?:are\s+)?(?:resolved|broken|rounded|"
    r"handled)\b[^.;\n]*|on\s+a\s+tie\b[^.;\n]*|in\s+case\s+of\s+a\s+tie"
    r"[^.;\n]*)", re.I)

# Status flags the design must EMIT for a rounding op, when stated:
#   inexact / precision loss, carry-out / overflow flag, round-up direction.
_FLAG_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("inexact", re.compile(r"\b(inexact|precision[ \-]?loss)\b", re.I)),
    ("cout", re.compile(r"\b(cout|carry[ \-]?out)\b", re.I)),
    ("r_up", re.compile(r"\b(r_up|round(?:ed|ing)?[ \-]?up\s+occurred|"
                        r"rounding\s+direction|round[ \-]?up\s+flag)\b", re.I)),
]

# Saturation / clamp on overflow: an ACTIVE region-handling requirement (distinct
# from a rounding mode). "saturate on overflow", "clamp to max", "clip the value".
# §4.05 no-leak: the verb must be ACTIVE (saturate/clamp/clip), not a "sized to
# prevent overflow" width-sizing note (mirrors spec_coverage_check's #760 guard).
_SATURATE_RE = re.compile(
    r"\b(saturat\w*|clamp\w*|clip\w*)\b", re.I)
# Preventive width-sizing context (NOT a behavioral saturate requirement).
_PREVENTIVE_RE = re.compile(
    r"\b(prevent\w*|avoid\w*|sized\s+to|wide\s+enough|big\s+enough|"
    r"large\s+enough|to\s+ensure\s+no|guard\w*\s+against|without\b)", re.I)


def _clauses(text: str) -> List[str]:
    """Split into clauses on sentence / list / line boundaries for clause-scoped
    guards. Keeps each idiom in its own local context."""
    return [c for c in re.split(r"[.;\n]|\s-\s|•|·|—", text) if c.strip()]


def _detect_rounding_modes(text: str) -> List[Tuple[str, str]]:
    """Return [(mode_tag, evidence_phrase)] for every EXPLICITLY-named rounding
    mode. De-duplicated by mode_tag (first evidence kept). §4.05: a mode is
    emitted ONLY on a named idiom hit — silence yields []."""
    found: Dict[str, str] = {}
    for tag, rx in _MODE_PATTERNS:
        m = _first_not_retired(rx, text, skip_width_helper=tag == "round_ceiling")
        if m and tag not in found:
            found[tag] = m.group(0).strip()
    return list(found.items())


def _detect_tiebreak(text: str) -> Optional[str]:
    m = _TIEBREAK_RE.search(text)
    return m.group(0).strip()[:120] if m else None


def _first_live(rx, text: str):
    """The first match of `rx` in `text` that is NOT denied, or None.

    ONE HELPER FOR THE WHOLE FILE (vibe-ic#712). The width pairs were guarded
    first and their siblings were not, so one spec was read by two rules:

        "Little-endian packing is no longer used; the packing is big-endian."
            -> "Little-endian"   (the retired order: data arrives scrambled)
        "Round-half-up is no longer used; rounding truncates."
            -> BOTH modes, the retired one included

    Byte order and rounding are what the arithmetic IS. A denied match does not
    END the search, or a spec that retires one convention and states another
    yields nothing.
    """
    for m in rx.finditer(text):
        lo, hi = sentence_scope(text, m.start(), m.end(),
                                extra_breaks=_LINE_END_BREAKS)
        if is_denied(text[lo:hi]):
            continue
        return m
    return None


def _is_log2_width_helper(text: str, match) -> bool:
    """Recognize only a described integer helper used solely for dimensions.

    A bare 'ceiling of log2' can describe real arithmetic, so that phrase alone
    is insufficient. Require the immediately following function declaration,
    at least one packed-dimension use, and no runtime call. A later, genuine
    ceiling requirement must still be searched after this occurrence.
    """
    start = text.rfind("\n", 0, match.start()) + 1
    end = text.find("\n", match.end())
    if end < 0:
        return False
    if not re.fullmatch(
            r"\s*(?://\s*)?Function\s+to\s+(?:calculate|compute)\s+the\s+"
            r"ceiling\s+of\s+log2\s*", text[start:end], re.I):
        return False
    declaration = re.match(
        r"\s*function\s+(?:automatic\s+)?(?:integer|int)\s+"
        r"(?P<name>[A-Za-z_]\w*)\s*[;(]", text[end:], re.I)
    if not declaration:
        return False
    name = re.escape(declaration.group("name"))
    dimensions = [m.span() for m in re.finditer(r"\[[^\[\]\n]*\]", text)]
    calls = list(re.finditer(rf"\b{name}\s*\(", text))
    in_dimension = lambda pos: any(lo <= pos < hi for lo, hi in dimensions)
    if not any(in_dimension(call.start()) for call in calls):
        return False
    for call in calls:
        if in_dimension(call.start()) or end <= call.start() < end + declaration.end():
            continue
        # A prose reference saying how a width is computed is also a
        # dimension use; an assignment remains a runtime call, even if its
        # destination happens to be named 'width'.
        lo = text.rfind("\n", 0, call.start()) + 1
        hi = text.find("\n", call.end())
        line = text[lo:hi if hi >= 0 else len(text)]
        if (re.search(r"\b(?:width|bits|dimension)\b", line, re.I)
                and not re.search(r"\bassign\b|<=|(?<![<>=!])=(?!=)", line)):
            continue
        return False
    return True


def _first_not_retired(rx, text: str, *, skip_width_helper: bool = False):
    """Like `_first_live`, but RETIREMENT only -- not every negation.

    A rounding-mode NAME describes itself, and its description legitimately
    contains negative words. Verbatim from a real prompt:

        "RTZ: Truncate the fractional part without rounding up."

    `is_denied` reads "without" and drops a correctly stated mode -- measured,
    it broke `test_named_rounding_mode_round_to_nearest_even_verbatim`, which
    quotes that line from the corpus. So here only a RETIREMENT denies:
    "no longer used", not any "without" or "no".

    WHAT THIS MISSES, said plainly: "The design does not use round-half-up" is
    not caught, because the vocabulary that catches it is the one that
    false-refuses the line above. Between a false refusal on real corpus text
    and a missed denial, this takes the missed denial -- and says so, rather
    than trading a visible regression for an invisible improvement.
    """
    for m in rx.finditer(text):
        if skip_width_helper and _is_log2_width_helper(text, m):
            continue
        lo, hi = sentence_scope(text, m.start(), m.end(),
                                extra_breaks=_LINE_END_BREAKS)
        if DENIAL_RETIRED_RE.search(text[lo:hi]):
            continue
        return m
    return None


def _detect_flags(text: str) -> List[str]:
    flags: List[str] = []
    for tag, rx in _FLAG_PATTERNS:
        if _first_live(rx, text):
            flags.append(tag)
    return flags


def _detect_saturation(text: str) -> Optional[str]:
    """An explicit saturate/clamp/clip-on-overflow requirement, with the #760
    preventive-width guard: a clause that only describes PREVENTING overflow by
    sizing (no active verb in-clause) is NOT a behavioral saturation
    requirement. Returns the evidence phrase or None."""
    for clause in _clauses(text):
        # THE CLAUSE IS THE RECORD here -- this loop already splits on them --
        # so a clause that RETIRES the requirement is not one that states it.
        # "Saturation on overflow is no longer used" was returned as the
        # evidence FOR saturation.
        if is_denied(clause):
            continue
        m = _SATURATE_RE.search(clause)
        if not m:
            continue
        # If the clause is purely preventive width-sizing AND the only matched
        # token is a noun-ish "clip"/"clamp" with no overflow/region object,
        # still accept — saturate/clamp/clip ARE active verbs (unlike the bare
        # "overflow" noun guarded in spec_coverage_check). The preventive guard
        # only suppresses when the clause says the design AVOIDS the condition
        # by sizing AND there is no region behaviour stated; here the verb
        # itself IS the region behaviour, so we keep it unless the clause is a
        # bare "sized to prevent ... " with the verb absent from the actionable
        # part. We bias no-leak: keep on any active saturate/clamp/clip verb,
        # drop only when the clause is dominated by preventive sizing language
        # with no object.
        if _PREVENTIVE_RE.search(clause) and not re.search(
                r"\b(overflow|underflow|max\w*|min\w*|range|boundary|limit|"
                r"value|result|output)\b", clause, re.I):
            continue
        return clause.strip()[:140]
    return None


# ===========================================================================
# (2) PACKING / WIDTH CONVERSION
# ===========================================================================

# An explicit input->output WIDTH RATIO. Several stated forms:
#   "from a higher 16-bit width to a smaller width of 8-bits"
#   "from a smaller 24-bit width to a larger width of 32-bits"
#   "32 -> 8", "32-to-8", "32:8 downscale"
#   "downscale/upscale ... NN ... to ... MM"
# Captured: (in_width, out_width).
#: A SENTENCE THAT ENDS AT A LINE END IS STILL A SENTENCE. The shared
#: vocabulary breaks on ". " and on a blank line, but a specification wraps its
#: paragraphs and writes ".\n" -- so without these the scope of a match reached
#: backwards over the full stop into the previous sentence, and a DENIAL there
#: refused a live pair standing beside it. Measured on
#:
#:     The path from 8-bit to 16-bit is no longer supported.
#:     Data is packed from 8-bit to 32-bit words.
#:
#: which returned NOTHING once polarity was asked: the false refusal, which is
#: the failure the other direction of this trade produces.
#:
#: `"\n"` alone would be wrong here. A spec wraps mid-sentence, and breaking on
#: every newline would miss a denial written across two lines -- an under-reach
#: that publishes a denied value, which is the failure being fixed.
_LINE_END_BREAKS = (".\n", "!\n", "?\n")

_WIDTH_PAIR_PATTERNS: List["re.Pattern[str]"] = [
    # "<adj> NN-bit width to <adj> width of MM-bit[s]" (axis up/down-scale form)
    re.compile(
        r"\b(\d{1,4})[ \-]?bit\s+width\s+to\s+(?:a\s+)?(?:smaller|larger|"
        r"narrower|wider|higher|lower)?\s*width\s+of\s+(\d{1,4})[ \-]?bit", re.I),
    # "from NN ... to ... MM bit[s]" / "NN-bit ... to ... MM-bit"
    re.compile(
        r"\bfrom\s+(?:a\s+)?(?:higher|lower|smaller|larger|wider|narrower)?\s*"
        r"(\d{1,4})[ \-]?bit\w*\s+(?:width\s+)?to\s+(?:a\s+)?(?:smaller|larger|"
        r"narrower|wider|higher|lower)?\s*(?:width\s+of\s+)?(\d{1,4})[ \-]?bit",
        re.I),
    # bare arrow / colon ratio "NN -> MM", "NN-to-MM", "NN:MM" near width/scale
    re.compile(
        r"\b(\d{1,4})[ \-]?bit\w*\s*(?:->|→|=>|:|\bto\b|-to-)\s*(\d{1,4})"
        r"[ \-]?bit", re.I),
]

# "downscale" / "upscale" / "resize" / "upsizer" / "downsizer" direction word.
_SCALE_DIR_RE = re.compile(
    r"\b(downscal\w*|downsiz\w*|upscal\w*|upsiz\w*|resiz\w*|narrow\w*|widen\w*)\b",
    re.I)

# A bit-slice PICK "bits [HI:LO]" / "select ... bits [HI:LO]" of a wider value,
# e.g. "48 bits long ... select only the middle 18 bits which is bits [26:9]".
_BITSLICE_RE = re.compile(
    r"\b(?:select\w*|pick\w*|take\w*|use\w*|middle|truncat\w*)?[^.;\n]{0,40}?"
    r"\bbits?\s*\[\s*(\d{1,4})\s*:\s*(\d{1,4})\s*\]", re.I)

# A "NN bits long" source-width statement (the wide intermediate).
_WIDE_SRC_RE = re.compile(r"\b(\d{1,4})\s*bits?\s+(?:long|wide|total)\b", re.I)

# Concatenation of N M-bit inputs into a K-bit bus, e.g.
#   "Concatenates six 5-bit input vectors into a single 30-bit bus"
#   "concatenation of 3 8-bit words -> 24-bit"
_CONCAT_NUM_WORD = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "sixteen": 16,
}
_CONCAT_RE = re.compile(
    r"\bconcat\w*[^.;\n]*?\b(\d+|two|three|four|five|six|seven|eight|nine|"
    r"ten|eleven|twelve|sixteen)\s+(\d{1,4})[ \-]?bit", re.I)
_CONCAT_INTO_RE = re.compile(
    r"\binto\s+(?:a\s+)?(?:single\s+)?(\d{1,4})[ \-]?bit", re.I)

# A "split into N M-bit" form (the dual of concat), e.g.
#   "splits it into four 8-bit output vectors"
_SPLIT_RE = re.compile(
    r"\bsplit\w*[^.;\n]*?\b(\d+|two|three|four|five|six|seven|eight|nine|"
    r"ten|eleven|twelve|sixteen)\s+(\d{1,4})[ \-]?bit", re.I)

# Byte-enable / strobe / select semantics: tkeep / tstrb / sel / byte-enable.
_BYTE_ENABLE_RE = re.compile(
    r"\b(tkeep|tstrb|byte[ \-]?enable\w*|byte[ \-]?select\w*|"
    r"\bsel(?:_[a-z]\w*)?\b|byte[ \-]?strobe\w*|lane[ \-]?enable\w*)\b", re.I)

# Endian / byte-order tokens (reuse spec_coverage_check's vocabulary; we ADD the
# byte-enable width as a structured field it lacks).
_BYTEORDER_RE = re.compile(
    r"\b(little[ \-]?endian|big[ \-]?endian|msb[ \-]?first|lsb[ \-]?first|"
    r"byte[ \-]?order|bit[ \-]?order|byte[ \-]?packing|byte[ \-]?swap\w*|"
    r"endian\w*\s+conversion|endian\w*)\b", re.I)

# A "[HI:0]" port-width token to size a named byte-enable signal (e.g. sel_i[3:0]
# => 4 byte lanes). Used only to enrich a byte_order/byte-enable item.
_PORTWIDTH_AFTER_RE = re.compile(r"\[\s*(\d{1,4})\s*:\s*0\s*\]")


def _coerce_count(tok: str) -> Optional[int]:
    tok = tok.strip().lower()
    if tok.isdigit():
        return int(tok)
    return _CONCAT_NUM_WORD.get(tok)


def _gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return a


def _ratio_str(a: int, b: int) -> str:
    if a <= 0 or b <= 0:
        return f"{a}:{b}"
    g = _gcd(a, b)
    return f"{a // g}:{b // g}"


def _detect_width_pairs(text: str) -> List[Tuple[int, int, str]]:
    """Return [(in_width, out_width, evidence)] for every EXPLICIT in->out width
    statement. De-duplicated by (in,out). §4.05: only a stated numeric pair.

    POLARITY IS ASKED (vibe-ic#712). A specification states a retired width as
    readily as a live one, and this reader published both:

        "The path from 8-bit to 16-bit is no longer supported."  -> (8, 16)
        "The block does not pack from 8-bit to 16-bit."          -> (8, 16)

    Both were returned as EXPLICIT stated pairs, which is a denied value
    published as a declaration -- the defect #712 exists to answer, and the one
    the polarity baseline names: it is how a design gets hard-sized onto
    another chip's die while citing its own document as the authority.

    The sentence around each match is asked, and a denied pair is dropped rather
    than published. Dropped and not merely down-ranked, because a caller that
    receives it cannot tell it from a stated one."""
    found: Dict[Tuple[int, int], str] = {}
    for rx in _WIDTH_PAIR_PATTERNS:
        for m in rx.finditer(text):
            lo, hi = sentence_scope(text, m.start(), m.end(),
                                    extra_breaks=_LINE_END_BREAKS)
            if is_denied(text[lo:hi]):
                continue
            try:
                iw, ow = int(m.group(1)), int(m.group(2))
            except (ValueError, IndexError):
                continue
            if iw <= 0 or ow <= 0 or iw == ow:
                continue
            key = (iw, ow)
            if key not in found:
                found[key] = m.group(0).strip()[:120]
    return [(iw, ow, ev) for (iw, ow), ev in found.items()]


def _detect_bitslice_pick(text: str) -> Optional[Dict[str, object]]:
    """A "select bits [HI:LO] of an NN-bit value" width-pick (e.g. 48-bit
    intermediate, take bits [26:9] => 18-bit). Returns a structured pick dict or
    None. §4.05: requires the explicit [HI:LO] slice."""
    sm = _BITSLICE_RE.search(text)
    if not sm:
        return None
    hi, lo = int(sm.group(1)), int(sm.group(2))
    if hi < lo:
        hi, lo = lo, hi
    picked = hi - lo + 1
    src = None
    wm = _WIDE_SRC_RE.search(text)
    if wm:
        src = int(wm.group(1))
    return {
        "hi": hi, "lo": lo, "picked_width": picked, "source_width": src,
        "evidence": sm.group(0).strip()[:120],
    }


def _detect_concat(text: str) -> Optional[Dict[str, object]]:
    """A "concatenate N M-bit ... into a K-bit bus" packing. Returns a structured
    concat dict or None. §4.05: requires the explicit N + M-bit count."""
    cm = _CONCAT_RE.search(text)
    if not cm:
        return None
    n = _coerce_count(cm.group(1))
    member_w = int(cm.group(2))
    if not n or member_w <= 0:
        return None
    total = None
    im = _CONCAT_INTO_RE.search(text, cm.end() - 1)
    if im:
        total = int(im.group(1))
    return {
        "n_inputs": n, "member_width": member_w,
        "computed_total": n * member_w, "stated_total": total,
        "evidence": cm.group(0).strip()[:120],
    }


def _detect_split(text: str) -> Optional[Dict[str, object]]:
    """A "split into N M-bit" unpacking (dual of concat)."""
    sm = _SPLIT_RE.search(text)
    if not sm:
        return None
    n = _coerce_count(sm.group(1))
    member_w = int(sm.group(2))
    if not n or member_w <= 0:
        return None
    return {
        "n_outputs": n, "member_width": member_w,
        "computed_total": n * member_w,
        "evidence": sm.group(0).strip()[:120],
    }


def _detect_byte_enable_width(text: str) -> Optional[int]:
    """If a byte-enable/strobe signal is named with a [HI:0] width, return the
    lane count (HI+1). Else None."""
    bm = _BYTE_ENABLE_RE.search(text)
    if not bm:
        return None
    tail = text[bm.end():bm.end() + 16]
    wm = _PORTWIDTH_AFTER_RE.search(tail)
    if wm:
        return int(wm.group(1)) + 1
    return None


def _detect_byte_order(text: str) -> Optional[str]:
    m = _first_live(_BYTEORDER_RE, text)
    return m.group(0).strip() if m else None


# ===========================================================================
# Public API
# ===========================================================================
# ===========================================================================
# (3) CONDITIONAL BIT MAPPINGS
# ===========================================================================
#
# WHY THIS SECTION EXISTS (#2215)
# -------------------------------
# The width family above answers "how many bits in, how many out". It does not
# answer WHICH bit becomes WHICH, under WHAT condition, and whether a bit may
# be transformed at all -- and that is the part an author has to implement.
# MEASURED on the shared extractor at v1.20.7, two public inputs that state
# DIFFERENT contracts:
#
#   "When active is one, result[11] is sample[11] XOR flip. When active is
#    zero, result[11] equals sample[11]. Bits result[10:0] always equal
#    sample[10:0]. If extend is one, bits result[19:12] repeat result[11];
#    otherwise those upper bits are zero."
#   "Preserve all twelve source bits. Do not invert any source bit. The upper
#    eight result bits are zero."
#
# both returned ONE identical item -- `width_convert 12->20` -- and the
# coverage reader emitted only that kind. No condition, no bit relation, no
# extension source and no preservation prohibition survived into the
# deterministic handoff, so the two contracts were indistinguishable
# downstream while differing in every bit they describe.
#
# §4.05 NO-LEAK, UNCHANGED. Every field below is read off an EXPLICIT clause
# and carries the clause as `evidence`. A prompt that states no mapping emits
# nothing: an unspecified format block must not acquire a guessed one, which
# is why there is no default transform, no default condition polarity and no
# inference from a signal's NAME.

# `name[hi:lo]` or `name[i]`. Bare `name` is deliberately NOT a reference: a
# mapping this module reports must say which bits it is about.
_BIT_REF = r"([A-Za-z_]\w*)\s*\[\s*(\d{1,4})\s*(?::\s*(\d{1,4})\s*)?\]"
_BIT_REF_RE = re.compile(_BIT_REF)

# The truth words a public input actually uses for a one-bit control. Kept
# closed: a word this does not list is not silently read as a polarity.
_TRUE_WORDS = ("one", "1", "high", "set", "asserted", "true")
_FALSE_WORDS = ("zero", "0", "low", "clear", "cleared", "deasserted", "false")

_CONDITION_RE = re.compile(
    r"\b(?:when|if)\s+([A-Za-z_]\w*)\s+is\s+"
    r"(" + "|".join(_TRUE_WORDS + _FALSE_WORDS) + r")\b", re.I)
# A guard is PRESENT here whether or not the truth word is one this grammar
# knows. The difference matters: a clause whose guard cannot be read must
# yield NOTHING, because recording its mapping without the guard turns a
# conditional contract into an unconditional one -- a different contract, and
# the §4.05 leak this section exists to avoid.
_GUARD_PRESENT_RE = re.compile(r"\b(?:when|if)\s+[A-Za-z_]\w*\s+is\b", re.I)
# A bitwise operator with no operand after it: the statement is incomplete,
# and reading it as the plain identity it syntactically resembles would
# record a mapping the input never states.
_DANGLING_OP_RE = re.compile(
    r"^\s*(?:XOR|AND|OR|\^|&|\|)\s*$", re.I)

# `<target> is|equals|becomes <source>` optionally transformed by a bitwise op
# with a named operand. `XOR` is the only transform with an operand; the rest
# of the vocabulary is closed for the same reason as the truth words.
_MAP_RE = re.compile(
    r"(?P<target>" + _BIT_REF + r")\s*"
    # An adverb between the reference and the relation word is common and
    # carries the UNCONDITIONAL claim ("always"), so it must not stop the
    # match -- it was the reason `Bits result[10:0] always equal sample[10:0]`
    # produced nothing while its two conditional siblings parsed.
    r"(?P<adverb>always|unconditionally|in all cases)?\s*"
    r"(?:is|are|equals?|equal to|becomes?)\s+"
    r"(?P<invert>(?:the\s+)?(?:inverse|complement|inversion)\s+of\s+|~|!)?\s*"
    r"(?P<source>" + _BIT_REF + r")"
    r"(?:\s*(?P<op>XOR|AND|OR|\^|&|\|)\s*(?P<operand>[A-Za-z_]\w*))?",
    re.I)

# Sign/bit extension: the upper slice REPEATS a named bit.
_REPLICATE_RE = re.compile(
    r"(?:bits?\s+)?(?P<target>" + _BIT_REF + r")\s*"
    r"(?:repeats?|replicates?|are copies of|is a copy of|"
    r"are sign[- ]extended from|extends? the sign of)\s+"
    r"(?P<source>" + _BIT_REF + r")", re.I)

# Zero fill, in the two shapes the prose actually uses: an explicit slice, or
# "those upper bits"/"the upper N result bits" naming a count.
_ZERO_SLICE_RE = re.compile(
    r"(?:bits?\s+)?(?P<target>" + _BIT_REF + r")\s*"
    r"(?:is|are)\s+(?:all\s+)?(?:zero|zeros|zeroed|0)\b", re.I)
_ZERO_UPPER_RE = re.compile(
    r"\b(?:those|the)\s+upper\s+"
    r"(?:(?P<count>\d{1,4}|" + "|".join(_CONCAT_NUM_WORD) + r")\s+)?"
    r"(?:\w+\s+)?bits?\s+"
    r"(?:is|are)\s+(?:all\s+)?(?:zero|zeros|zeroed|0)\b", re.I)

# Preservation, and its prohibition. Both are explicit REQUIREMENTS -- the
# prohibition especially: "do not invert" is not an absent statement, it is a
# stated constraint, and dropping it is how a preserve-only contract became
# indistinguishable from one that inverts a bit.
_PRESERVE_RE = re.compile(
    r"\bpreserve[sd]?\s+(?:all\s+)?(?:the\s+)?"
    r"(?P<count>\d{1,4}|" + "|".join(_CONCAT_NUM_WORD) + r")?\s*"
    r"(?:source|input|payload|data)?\s*bits?\b", re.I)
_NO_INVERT_RE = re.compile(
    r"\b(?:do not|don't|must not|never|shall not)\s+"
    r"(?:invert|complement|negate|flip)\b[^.;\n]*", re.I)


def _ref_from(groups: Tuple[str, str, Optional[str]]) -> Dict[str, object]:
    signal, first, second = groups
    hi = int(first)
    lo = int(second) if second is not None else hi
    if lo > hi:
        hi, lo = lo, hi
    return {"signal": signal, "hi": hi, "lo": lo, "width": hi - lo + 1}


def _ref_at(text: str, start: int) -> Optional[Dict[str, object]]:
    m = _BIT_REF_RE.match(text, start)
    return _ref_from(m.groups()) if m else None


def _condition_for(clause: str) -> Optional[Dict[str, object]]:
    """The `when/if <ctrl> is <truth word>` guard of one clause, or None.

    None means only "no guard I can read here". It does NOT mean the clause is
    unconditional -- `_detect_bit_mappings` asks `_GUARD_PRESENT_RE` next, and
    a clause that HAS a guard whose truth word is outside the closed
    vocabulary yields no mapping at all. Splitting the two questions is what
    keeps an unreadable guard from silently becoming an unconditional
    contract.
    """
    m = _CONDITION_RE.search(clause)
    if m is None:
        return None
    word = m.group(2).lower()
    return {"signal": m.group(1),
            "value": 1 if word in _TRUE_WORDS else 0,
            "evidence": m.group(0).strip()}


_OTHERWISE_RE = re.compile(r"^\s*(?:otherwise|else)\b", re.I)


def _negate(condition: Optional[Dict[str, object]]) -> Optional[Dict[str, object]]:
    """The complementary branch of a one-bit condition, or None.

    None when there is nothing to complement, and the caller then emits NO
    mapping for that clause. An `otherwise` arm recorded without its condition
    would be an UNCONDITIONAL mapping -- a different contract from the one the
    public input states, and the §4.05 leak this whole section exists to
    avoid. Silence is the safe answer; a guessed guard is not.
    """
    if condition is None:
        return None
    return {"signal": condition["signal"],
            "value": 0 if condition["value"] else 1,
            "evidence": f"otherwise-arm of: {condition['evidence']}"}


def _detect_bit_mappings(text: str) -> List[dict]:
    """Every EXPLICIT per-bit / per-slice mapping, with its condition.

    One item per stated clause, in source order, de-duplicated on the exact
    (target, source, transform, condition) tuple so a contract repeated in
    prose and in an embedded skeleton is reported once.

    An `otherwise`/`else` clause inherits the COMPLEMENT of the condition its
    neighbour stated. When there is no such neighbour the clause yields
    nothing at all, because an else-arm with no guard is not the contract the
    input describes.
    """
    items: List[dict] = []
    seen: set = set()
    previous_condition: Optional[Dict[str, object]] = None
    previous_targets: List[Dict[str, object]] = []
    for clause in _clauses(text):
        stripped = clause.strip()
        if not stripped:
            continue
        condition = _condition_for(stripped)
        otherwise_arm = False
        if condition is None and _OTHERWISE_RE.match(stripped):
            condition = _negate(previous_condition)
            if condition is None:
                continue
            otherwise_arm = True
        elif condition is None and _GUARD_PRESENT_RE.search(stripped):
            continue          # a guard this grammar cannot read -- see above
        elif condition is not None:
            previous_condition = condition
        clause_targets: List[Dict[str, object]] = []
        for m in _MAP_RE.finditer(stripped):
            target = _ref_at(stripped, m.start("target"))
            source = _ref_at(stripped, m.start("source"))
            if target is None or source is None:
                continue
            if m.group("op"):
                transform = {"^": "xor", "xor": "xor", "&": "and",
                             "and": "and", "|": "or", "or": "or"}[
                                 m.group("op").lower()]
                operand = m.group("operand")
            elif m.group("invert"):
                transform, operand = "invert", None
            elif _DANGLING_OP_RE.match(stripped[m.end():]):
                continue      # `X is Y XOR` names no operand -- incomplete
            else:
                transform, operand = "identity", None
            items.append(_mapping_item(
                target, source, transform, operand, condition, stripped))
            clause_targets.append(target)
        for m in _REPLICATE_RE.finditer(stripped):
            target = _ref_at(stripped, m.start("target"))
            source = _ref_at(stripped, m.start("source"))
            if target is None or source is None:
                continue
            items.append(_mapping_item(
                target, source, "replicate", None, condition, stripped))
            clause_targets.append(target)
        for m in _ZERO_SLICE_RE.finditer(stripped):
            target = _ref_at(stripped, m.start("target"))
            if target is None:
                continue
            items.append(_mapping_item(
                target, {"literal": 0}, "zero", None, condition, stripped))
            clause_targets.append(target)
        zero_upper = _ZERO_UPPER_RE.search(stripped)
        if zero_upper is not None:
            count = zero_upper.group("count")
            target = {"signal": None, "hi": None, "lo": None,
                      # `_coerce_count`, not int(): the prose writes "the upper
                      # eight result bits" as often as "the upper 8".
                      "width": _coerce_count(count) if count else None,
                      "position": "upper"}
            # "OTHERWISE THOSE UPPER BITS ARE ZERO" NAMES THE SLICE ITS OWN
            # OTHER BRANCH NAMED. `those` is anaphoric, and the antecedent is
            # decidable from the clause structure rather than guessed: an
            # else-arm complements exactly one preceding branch, so when that
            # branch stated exactly ONE explicit target slice, `those bits`
            # are those bits. Resolved only under all three conditions -- an
            # else-arm, no explicit count of its own, and exactly one
            # antecedent -- because a reference with two candidate antecedents
            # is ambiguous and an ambiguous mapping must stay unanchored.
            if (otherwise_arm and target["width"] is None
                    and len(previous_targets) == 1):
                antecedent = previous_targets[0]
                if antecedent.get("signal") is not None:
                    target = dict(antecedent)
            items.append(_mapping_item(
                target, {"literal": 0}, "zero", None, condition, stripped))
            clause_targets.append(target)
        if not otherwise_arm:
            previous_targets = [t for t in clause_targets
                                if t.get("signal") is not None]
    deduped: List[dict] = []
    for item in items:
        key = json.dumps(
            [item["target"], item["source"], item["transform"],
             item.get("operand"), item.get("condition")], sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _describe(ref: Dict[str, object]) -> str:
    if "literal" in ref:
        return str(ref["literal"])
    if ref.get("signal") is None:
        width = ref.get("width")
        return f"the upper {width} result bits" if width else "the upper bits"
    if ref["hi"] == ref["lo"]:
        return f"{ref['signal']}[{ref['hi']}]"
    return f"{ref['signal']}[{ref['hi']}:{ref['lo']}]"


def _mapping_item(target, source, transform, operand, condition,
                  evidence: str) -> dict:
    requirement = f"{_describe(target)} = {_describe(source)}"
    if transform == "xor":
        requirement += f" XOR {operand}"
    elif transform == "invert":
        requirement = f"{_describe(target)} = ~{_describe(source)}"
    elif transform == "replicate":
        requirement = f"{_describe(target)} = replicate({_describe(source)})"
    if condition is not None:
        requirement += (f" when {condition['signal']} == "
                        f"{condition['value']}")
    item = {
        "kind": "bit_mapping",
        "requirement": requirement,
        "evidence": evidence[:240],
        "target": target,
        "source": source,
        "transform": transform,
        "condition": condition,
        "coverage_tokens": [t for t in (
            target.get("signal"), source.get("signal") if isinstance(source, dict)
            else None, operand,
            condition["signal"] if condition else None) if t],
    }
    if operand:
        item["operand"] = operand
    return item


def _detect_preservation(text: str) -> List[dict]:
    """Explicit preserve / do-not-transform rules.

    A prohibition is a REQUIREMENT, not an absence. "Do not invert any source
    bit" is the clause that makes a preserve-only contract different from one
    that conditionally inverts a bit, and it is exactly what was being dropped.
    """
    items: List[dict] = []
    preserve = _PRESERVE_RE.search(text)
    if preserve is not None:
        raw = preserve.group("count")
        count = _coerce_count(raw) if raw else None
        items.append({
            "kind": "bit_preserve",
            "requirement": ("preserve all source bits"
                            + (f" ({count} bits)" if count else "")),
            "evidence": preserve.group(0).strip()[:240],
            "preserved_width": count,
            "prohibited_transforms": [],
            "coverage_tokens": ["preserve"],
        })
    for m in _NO_INVERT_RE.finditer(text):
        items.append({
            "kind": "bit_preserve",
            "requirement": "no source bit may be inverted",
            "evidence": m.group(0).strip()[:240],
            "preserved_width": None,
            "prohibited_transforms": ["invert"],
            "coverage_tokens": ["invert"],
        })
    return items


def extract(prompt_text: str) -> List[dict]:
    """Extract structural numeric-semantics + packing/width checklist items from
    a CVDP-style prompt. Returns a list of dicts (one per explicit fact).

    §4.05 no-leak: emits ONLY when a mode / ratio / order is EXPLICITLY stated;
    returns [] on silence. chip-AGNOSTIC.

    Item kinds (extends spec_coverage_check.ChecklistItem.kind):
      rounding_mode | saturation | width_convert | byte_order
      bit_mapping | bit_preserve
    """
    if not prompt_text or not isinstance(prompt_text, str):
        return []
    text = prompt_text
    items: List[dict] = []

    # --- (1a) Named rounding modes (+ shared tie-break + status flags) ---
    modes = _detect_rounding_modes(text)
    if modes:
        tiebreak = _detect_tiebreak(text)
        flags = _detect_flags(text)
        for tag, ev in modes:
            req = f"rounding mode: {tag.replace('_', ' ')}"
            item = {
                "kind": "rounding_mode",
                "requirement": req,
                "evidence": ev,
                "mode": tag,
                "coverage_tokens": [tag, "round"],
            }
            # A tie-break rule is only semantically meaningful for a
            # round-to-NEAREST mode (a midpoint can only occur there). Attaching
            # it to RTZ/ceil/floor/away-from-zero would be a leak — those modes
            # have a deterministic non-midpoint rule. So tie_break is recorded
            # ONLY on the nearest-* modes. §4.05 no-leak.
            if tiebreak and tag in (
                    "round_to_nearest_even", "round_nearest_ties_away"):
                item["tie_break"] = tiebreak
            if flags:
                item["status_flags"] = flags
            items.append(item)

    # --- (1a-bis) Explicit per-bit / per-slice mappings and their conditions ---
    items.extend(_detect_bit_mappings(text))
    items.extend(_detect_preservation(text))

    # --- (1b) Saturation / clamp on overflow ---
    sat = _detect_saturation(text)
    if sat:
        items.append({
            "kind": "saturation",
            "requirement": "saturate / clamp on overflow",
            "evidence": sat,
            "coverage_tokens": ["saturat", "clamp", "clip"],
        })

    # --- (2a) Stated input->output width ratio(s) ---
    scale_dir = None
    sd = _SCALE_DIR_RE.search(text)
    if sd:
        scale_dir = sd.group(0).lower()
    for iw, ow, ev in _detect_width_pairs(text):
        direction = "downscale" if iw > ow else "upscale"
        items.append({
            "kind": "width_convert",
            "requirement": (f"width conversion {iw}->{ow} "
                            f"({direction}, ratio {_ratio_str(iw, ow)})"),
            "evidence": ev,
            "in_width": iw,
            "out_width": ow,
            "ratio": _ratio_str(iw, ow),
            "direction": scale_dir or direction,
            "coverage_tokens": [str(iw), str(ow)],
        })

    # --- (2b) Bit-slice width pick (wide intermediate -> select [hi:lo]) ---
    pick = _detect_bitslice_pick(text)
    if pick:
        picked = pick["picked_width"]
        src = pick["source_width"]
        ratio = _ratio_str(src, picked) if src else None  # type: ignore[arg-type]
        req = (f"width pick: bits [{pick['hi']}:{pick['lo']}] "
               f"-> {picked}-bit")
        if src:
            req += f" of {src}-bit intermediate (ratio {ratio})"
        item = {
            "kind": "width_convert",
            "requirement": req,
            "evidence": pick["evidence"],
            "in_width": src,
            "out_width": picked,
            "slice_hi": pick["hi"],
            "slice_lo": pick["lo"],
            "coverage_tokens": [str(pick["hi"]), str(pick["lo"])],
        }
        if ratio:
            item["ratio"] = ratio
        items.append(item)

    # --- (2c) Concatenation packing (N M-bit -> K-bit) ---
    concat = _detect_concat(text)
    if concat:
        n = concat["n_inputs"]
        mw = concat["member_width"]
        total = concat["stated_total"] or concat["computed_total"]
        items.append({
            "kind": "width_convert",
            "requirement": (f"pack: concat of {n} x {mw}-bit "
                            f"-> {total}-bit bus"),
            "evidence": concat["evidence"],
            "n_inputs": n,
            "member_width": mw,
            "out_width": total,
            "coverage_tokens": [str(n), str(mw), str(total)],
        })

    # --- (2d) Split unpacking (K-bit -> N M-bit) ---
    split = _detect_split(text)
    if split:
        n = split["n_outputs"]
        mw = split["member_width"]
        total = split["computed_total"]
        items.append({
            "kind": "width_convert",
            "requirement": (f"unpack: split {total}-bit "
                            f"-> {n} x {mw}-bit"),
            "evidence": split["evidence"],
            "n_outputs": n,
            "member_width": mw,
            "in_width": total,
            "coverage_tokens": [str(n), str(mw), str(total)],
        })

    # --- (2e) Byte-order / endian (+ byte-enable lane width) ---
    bo = _detect_byte_order(text)
    if bo:
        be_lanes = _detect_byte_enable_width(text)
        item = {
            "kind": "byte_order",
            "requirement": f"byte order / packing: {bo}",
            "evidence": bo,
            "byte_order": bo.lower().replace(" ", "-"),
            "coverage_tokens": [bo.split()[0].lower()],
        }
        if be_lanes:
            item["byte_enable_lanes"] = be_lanes
            item["requirement"] += f" ({be_lanes} byte lanes)"
        items.append(item)

    return items


# ===========================================================================
# CLI
# ===========================================================================
def _read_arg(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        sys.stderr.write(
            "usage: spec_numeric_pack_extract.py PROMPT.txt | -\n"
            "       (reads prompt text, prints extracted numeric/pack items "
            "as JSON)\n")
        return 2
    try:
        text = _read_arg(argv[0])
    except OSError as exc:
        sys.stderr.write(f"error: cannot read {argv[0]!r}: {exc}\n")
        return 2
    items = extract(text)
    json.dump({"count": len(items), "items": items}, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
