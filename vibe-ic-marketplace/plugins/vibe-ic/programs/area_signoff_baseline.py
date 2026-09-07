#!/usr/bin/env python3
"""area_signoff_baseline.py — resolve the standard-cell AREA a design signs off
against FOR THE TECHNOLOGY THIS RUN BUILDS AGAINST, or refuse by name.

CHIP_AGNOSTIC: strict — no process, vendor or PDK name anywhere in this file,
DOCSTRING INCLUDED. This is STRICTER than the repo-wide
`source_chip_agnostic_check`, which permits open-PDK names; that gate's PASS is
not this file's verdict.
`test_issue2136_area_signoff_baseline::test_the_program_names_no_process_or_vendor_token`
is, and it reads the WHOLE file. The rule is DECLARED HERE and not only in that
test because an author editing this file has to be able to see it — measured:
the ban shipped asserted-but-undeclared and
`test_chip_agnostic_strictness_is_declared_not_hidden` reddened on exactly that,
which is the rule working.

THE DEFECT THIS CLOSES (measured, vibe-ic#2136)
==============================================
An L7 verification plan states a baseline it MEASURED, and then turns that
measurement into an absolute sign-off row::

    ### 7.4.1 Baseline (top=..., <library-A>, TT corner)
    | stdcell area | **12,775 um^2** |

    ### 7.4.2 Sign-off Acceptance Range
    | stdcell area | (0, baseline x 1.3] = <= 16,608 um^2 | gate |

The baseline row is ATTRIBUTED — it names the standard-cell library it was
measured on.  The sign-off row is not: it carries only the product.  A reader
of the sign-off row alone sees an absolute area ceiling that looks like a
property of the DESIGN, and it is a property of ONE TECHNOLOGY.

Measured consequence: a run of the same design on a different, physically
larger open technology mapped to 57,024.71 um^2 (2180 cells).  Against the row
that is a 3.4x overshoot; against the row's own denominator it is not a
comparison at all, because that denominator was never measured on the library
this run used.  The lane declined to report pass/fail and said so, which is the
correct human answer and exactly the answer a program has to be able to give.

This is the same shape as ``database_unit_um`` (#2085) and as the clock period
(#2091): a TECHNOLOGY fact stated once, for one technology, and then read as if
it were universal.  `clock_target_provenance` is the model followed here — the
value never travels without the tier that produced it.

WHAT THIS PROGRAM DOES
======================
It reads the design INPUT for (a) a declared standard-cell area baseline and
its attribution, and (b) the acceptance rule applied to it, and answers ONE
question: *is there a valid denominator for the technology this run resolved?*

THE LADDER, highest authority first::

    declared_pdk_table              a technology-keyed area table row matching
                                    this run's library / PDK
    declared_baseline_technology    a baseline row whose own attribution names
                                    this run's technology family
    derived_reference_netlist       a reference-netlist area MEASURED in this
                                    run's own cell library, x the declared ratio
    not_determined                  NAMED REFUSAL                <-- never PASS

``NOT_DETERMINED`` IS THE LOAD-BEARING STATE.  It is not a soft PASS and it is
not a FAIL: a design measured against a denominator from another technology has
not been judged at all, and publishing either verdict would be a false
statement about the design.  Every refusal carries `reason`, the run's
technology, and the attribution text the document actually carried, so the
refusal is actionable rather than merely negative.

WHY THE BASELINE'S ATTRIBUTION GOVERNS THE THRESHOLD
====================================================
The absolute in the acceptance row is a PRODUCT of the baseline and a ratio the
same document states.  It inherits the baseline's attribution and has none of
its own.  So a threshold is usable exactly when the baseline behind it is, and
a document that states an absolute with no attributable baseline anywhere
declares no denominator at all.

HONESTY BOUNDARY (Sec 4.05)
===========================
Only the design INPUT is read.  Nothing is defaulted, interpolated, rounded or
scaled from a neighbouring technology.  Two baseline rows that match the run
and DISAGREE are AMBIGUOUS and resolve to nothing — picking one by document
order is how a cross-technology number gets published as a specification.  A
denied row is not a declaration (vibe-ic#712), consulted through the repo's one
negation vocabulary.

HOW THE STRICT DECLARATION ABOVE IS HONOURED: the run's technology names are
supplied by the CALLER from what the run resolved, and every other name comes
from the design's own document.  Two names belong to the same technology family
when one's leading family token is a prefix of the other's — a naming
convention, not a name.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _atomic_artefact import write_text as atomic_write_text  # noqa: E402
from _prose_polarity import is_denied as _is_denied  # noqa: E402
# The repo's ONE spelling of "how a markdown table row is split", and of "does
# this row's key name the technology the caller resolved". A second copy of
# those facts is a second answer to them (measured elsewhere in this repo), so
# they are imported rather than restated.
from declared_clock_period import (  # noqa: E402
    _cells, _clean_key, _is_separator, match_rows)

#: The tier names. `NOT_DETERMINED_TIER` is a verdict, not an absence.
DECLARED_PDK_TABLE_TIER = "declared_pdk_table"
DECLARED_BASELINE_TIER = "declared_baseline_technology"
DERIVED_REFERENCE_TIER = "derived_reference_netlist"
NOT_DETERMINED_TIER = "not_determined"

#: The three verdicts a caller may publish. Nothing else is a verdict.
PASS = "PASS"
FAIL = "FAIL"
NOT_DETERMINED = "NOT_DETERMINED"

#: The disclosure a consumer MUST carry when the denominator is not this run's.
#: One constant so an emitter and a checker cannot drift apart.
NOT_DETERMINED_DISCLOSURE = (
    "standard-cell area was NOT judged: no area baseline is declared for the "
    "technology this run built against")

# A metric cell naming the STANDARD-CELL area. `die area` and `core area` are
# deliberately NOT matched: they are different quantities with different
# ceilings, and `area_total_vs_budget_check` already owns the die comparison.
_CELL_AREA_METRIC_RE = re.compile(
    r"(?:std\s*[-_]?\s*cell|standard[\s_-]*cell|cell)\s*area"
    r"|標準(?:單元|元件|儲存格)?\s*面積"
    r"|單元\s*面積",
    re.IGNORECASE)
# The unit this program will compare in. A figure in any other unit is READ and
# REFUSED, never converted on a guess.
_UM2 = r"(?:µm|μm|um|micron)\s*(?:\^?2|²|\*\*2)"
_AREA_VALUE_RE = re.compile(
    r"(\d[\d,\s]*(?:\.\d+)?)\s*(" + _UM2 + r")", re.IGNORECASE)
# `baseline x 1.3` in the spellings a document actually writes it in. The
# multiplier must be a NUMBER: `baseline x [0.5, 2.0]` is a two-sided range on
# a different (informational) metric and is not a ceiling.
_RATIO_RE = re.compile(
    r"baseline\s*[×xX\*]\s*(\d+(?:\.\d+)?)"
    r"|基準\s*[×xX\*]\s*(\d+(?:\.\d+)?)")
# An upper bound: a comparator immediately in front of an area figure.
_UPPER_RE = re.compile(
    r"(?:≤|<=|≦|<|不超過|至多)\s*\*{0,2}\s*(\d[\d,\s]*(?:\.\d+)?)\s*("
    + _UM2 + r")", re.IGNORECASE)
# A header cell that names an area column, for the technology-keyed table tier.
_AREA_HDR_RE = re.compile(r"area|面積", re.IGNORECASE)
# A header cell that names the technology key column. Same vocabulary as the
# period table's key column, for the same reason: it is how these documents
# spell "which technology this row is about".
_KEY_HDR_RE = re.compile(
    r"librar|library|pdk|std[\s_-]*cell|cell\s*lib|技術|製程", re.IGNORECASE)
#: A markdown heading line, which is where a baseline table states its scope.
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")
#: How far back to look for an attribution when the document has no heading.
_ATTRIBUTION_FALLBACK_LINES = 12
#: Shortest family token that may be matched by prefix. Two-and-three-character
#: prefixes collide across unrelated families; four does not, measured against
#: every technology name this repo's registry carries.
_MIN_FAMILY_LEN = 4


# ── technology-name identity, by convention rather than by name ─────────────
def family(name: str) -> str:
    """The leading family token of a technology name, lowercased.

    A standard-cell library is conventionally spelled ``<family>_<...>`` and a
    PDK ``<family><variant>``, so the family token is the run of characters up
    to the first separator. No name is recognised here — only a shape.
    """
    t = str(name or "").strip().lower().strip("`*")
    t = re.split(r"[_\-. /]", t, 1)[0]
    return re.sub(r"[^a-z0-9]", "", t)


def same_family(a: str, b: str) -> bool:
    """True when two technology names belong to one family.

    A PDK name and a library name from the same family differ by a variant
    suffix on one side or the other, so the test is PREFIX in either direction
    over the family tokens — never equality, which would make a library and its
    own PDK read as different technologies.
    """
    fa, fb = family(a), family(b)
    if len(fa) < _MIN_FAMILY_LEN or len(fb) < _MIN_FAMILY_LEN:
        return False
    return fa.startswith(fb) or fb.startswith(fa)


def _text_names_family(text: str, candidates: Sequence[str]) -> Optional[str]:
    """The first token in ``text`` belonging to a candidate's family, or None."""
    for tok in re.findall(r"[A-Za-z][A-Za-z0-9_\-.]{2,}", text or ""):
        for cand in candidates:
            if same_family(tok, cand):
                return tok
    return None


# ── numbers ────────────────────────────────────────────────────────────────
def _num(raw: str) -> Optional[float]:
    """A figure written with thousands separators, or None."""
    try:
        v = float(re.sub(r"[,\s]", "", str(raw)))
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


# ── reading the design's own area statements ───────────────────────────────
def _attribution(lines: Sequence[str], row_idx: int) -> Tuple[str, int]:
    """The scope a table row's value is stated under, and the line it starts on.

    A baseline table states WHAT IT MEASURED in its heading or in the caption
    between the heading and the table — never inside the value row, which is
    why the row alone is not enough to attribute the number.
    """
    for i in range(row_idx, -1, -1):
        if _HEADING_RE.match(lines[i]):
            return "\n".join(lines[i:row_idx + 1]), i + 1
    lo = max(0, row_idx - _ATTRIBUTION_FALLBACK_LINES)
    return "\n".join(lines[lo:row_idx + 1]), lo + 1


def parse_area_statements(text: str, source: str = "") -> Dict[str, object]:
    """Every standard-cell-area BASELINE, CEILING and RATIO stated in ``text``.

    A row is classified by WHAT ITS CELLS SAY, not by the section number it
    sits under, so a document that numbers its sections differently is read
    identically:

      * a cell carrying ``baseline x <n>``            -> a ratio;
      * a cell carrying a comparator in front of an
        area figure                                   -> a ceiling;
      * a cell carrying a bare area figure and neither
        of the above                                  -> a measured baseline.
    """
    out: Dict[str, object] = {"baselines": [], "ceilings": [], "ratios": [],
                              "unreadable_units": []}
    lines = text.splitlines()
    for j, line in enumerate(lines):
        cells = _cells(line)
        if not cells or _is_separator(cells):
            continue
        if not _CELL_AREA_METRIC_RE.search(cells[0] or ""):
            continue
        # vibe-ic#712 — a RETIRED row is not a declaration. The row is the
        # record, so the row is the scope: a denial one row up must not retire
        # the row below it.
        if _is_denied(line):
            continue
        attribution, attribution_line = _attribution(lines, j)
        base = {"source": source, "line": j + 1, "row": line.strip()[:300],
                "attribution": attribution.strip()[:600],
                "attribution_line": attribution_line}
        for cell in cells[1:]:
            rm = _RATIO_RE.search(cell)
            if rm:
                val = _num(rm.group(1) or rm.group(2))
                if val:
                    out["ratios"].append(dict(base, ratio=val))
            um = _UPPER_RE.search(cell)
            if um:
                val = _num(um.group(1))
                if val:
                    out["ceilings"].append(dict(base, value_um2=val))
                continue
            if rm:
                continue
            am = _AREA_VALUE_RE.search(cell)
            if am:
                val = _num(am.group(1))
                if val:
                    out["baselines"].append(dict(base, value_um2=val))
    # THE ACCEPTANCE RATIO IS AS OFTEN STATED IN PROSE AS IN A ROW. The
    # measured document writes it in the paragraph above the table ("area /
    # power within baseline x 1.3") as well as inside the row, and a keyed
    # table states it only in prose. A ratio read here is subject to the same
    # two rules as one read from a row: a denied line declares nothing, and two
    # DIFFERENT ratios anywhere in the document refuse rather than vote — so a
    # prose line about a different area quantity cannot quietly displace the
    # right one, it can only make the document ambiguous.
    for j, line in enumerate(lines):
        if _cells(line):
            continue                      # rows are handled above
        if not re.search(r"area|面積", line, re.IGNORECASE):
            continue
        if _is_denied(line):
            continue
        rm = _RATIO_RE.search(line)
        if not rm:
            continue
        val = _num(rm.group(1) or rm.group(2))
        if val:
            attribution, attribution_line = _attribution(lines, j)
            out["ratios"].append({
                "source": source, "line": j + 1, "row": line.strip()[:300],
                "attribution": attribution.strip()[:600],
                "attribution_line": attribution_line, "ratio": val})
    return out


def parse_area_key_tables(text: str, source: str = "") -> List[Dict[str, object]]:
    """Rows of every table that keys an AREA by technology.

    The portable way to state a per-technology baseline is a keyed table, the
    same shape `declared_clock_period` reads for the clock period. A table
    qualifies when one header cell names an area and another names a
    library/PDK key.
    """
    rows: List[Dict[str, object]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        hdr = _cells(lines[i])
        if hdr is None or i + 1 >= len(lines):
            i += 1
            continue
        sep = _cells(lines[i + 1])
        if sep is None or not _is_separator(sep):
            i += 1
            continue
        area_col = next((n for n, c in enumerate(hdr)
                         if _AREA_HDR_RE.search(c or "")), None)
        key_col = next((n for n, c in enumerate(hdr)
                        if _KEY_HDR_RE.search(c or "")), None)
        if area_col is None or key_col is None or area_col == key_col:
            i += 1
            continue
        j = i + 2
        while j < len(lines):
            row = _cells(lines[j])
            if row is None:
                break
            if _is_separator(row):
                j += 1
                continue
            if max(area_col, key_col) < len(row) and not _is_denied(lines[j]):
                key = _clean_key(row[key_col])
                cell = row[area_col] or ""
                um = _UPPER_RE.search(cell) or _AREA_VALUE_RE.search(cell)
                val = _num(um.group(1)) if um else None
                if key and val:
                    rows.append({"key": key, "value_um2": val,
                                 "source": source, "line": j + 1,
                                 "row": lines[j].strip()[:300],
                                 "ceiling": bool(_UPPER_RE.search(cell))})
            j += 1
        i = j
    return rows


# ── the resolver ───────────────────────────────────────────────────────────
def _blank_report(candidates: Sequence[str]) -> Dict[str, object]:
    return {
        "threshold_um2": None,
        "baseline_um2": None,
        "ratio": None,
        "tier": NOT_DETERMINED_TIER,
        "determined": False,
        "reason": "",
        "candidates": [c for c in candidates if c],
        "matched_name": None,
        "source": None,
        "line": None,
        "row": "",
        "baseline_attribution": "",
        "baseline_source": None,
        "baseline_line": None,
        "baseline_row": "",
        "attributions_seen": [],
        "signoff_row_found": False,
        "derived_um2": None,
        "note": "",
        "disclosure": "",
        "would_have_stated": "",
    }


def _refuse(rep: Dict[str, object], reason: str, note: str) -> Dict[str, object]:
    rep["tier"] = NOT_DETERMINED_TIER
    rep["determined"] = False
    rep["reason"] = reason
    rep["note"] = note
    rep["disclosure"] = NOT_DETERMINED_DISCLOSURE
    rep["would_have_stated"] = (
        "a standard-cell area baseline attributed to "
        f"'{(rep['candidates'] or ['the target technology'])[0]}' — either a "
        "technology-keyed area table row naming it, or a baseline table whose "
        "own heading names it — would make this a specification for this run")
    return rep


def resolve_texts(items: Sequence[Tuple[str, str]],
                  candidates: Sequence[str],
                  *,
                  reference_area_um2: Optional[float] = None,
                  reference_area_cite: str = "") -> Dict[str, object]:
    """Resolve the area threshold for ``candidates`` over ``(name, text)`` docs.

    ``candidates`` are the technology names THIS RUN resolved (its std-cell
    library, its PDK, or both). An empty list is not a licence to fall back to
    whatever the document states first — it means the run's technology is not
    known here, and the answer is a refusal.
    """
    cands = [c.strip() for c in candidates if c and str(c).strip()]
    rep = _blank_report(cands)

    stmts: Dict[str, List[Dict[str, object]]] = {
        "baselines": [], "ceilings": [], "ratios": []}
    keyed: List[Dict[str, object]] = []
    for name, text in items:
        if not isinstance(text, str) or not text:
            continue
        got = parse_area_statements(text, source=name)
        for k in stmts:
            stmts[k].extend(got[k])          # type: ignore[arg-type]
        keyed.extend(parse_area_key_tables(text, source=name))

    rep["signoff_row_found"] = bool(
        stmts["baselines"] or stmts["ceilings"] or keyed)
    rep["attributions_seen"] = sorted({
        str(b["attribution"]).splitlines()[0].strip()
        for b in stmts["baselines"]})

    if not cands:
        return _refuse(
            rep, "run_technology_not_supplied",
            "the technology this run builds against was not supplied, so no "
            "baseline can be attributed to it; a threshold resolved without "
            "one is a threshold from whichever technology the document "
            "happened to state first")

    if not rep["signoff_row_found"]:
        return _refuse(rep, "no_area_signoff_stated",
                       "the design input states no standard-cell area "
                       "baseline and no standard-cell area ceiling")

    # ── tier 1: a technology-keyed area table row ──────────────────────────
    hits = match_rows(keyed, cands)
    if hits:
        values = sorted({round(float(h["value_um2"]), 6) for h in hits})
        if len(values) > 1:
            return _refuse(
                rep, "declared_table_ambiguous",
                f"{len(hits)} technology-keyed area row(s) match {cands} and "
                f"they declare DIFFERENT areas {values} um^2; refusing to pick "
                "one. Rows: " + "; ".join(
                    f"{h['source']}:{h['line']} {h['row']}" for h in hits))
        best = sorted(hits, key=lambda h: (str(h["key"]).count("*"),
                                           -len(str(h["key"]))))[0]
        rep.update({"tier": DECLARED_PDK_TABLE_TIER, "determined": True,
                    "matched_name": best["key"], "source": best["source"],
                    "line": best["line"], "row": best["row"]})
        if best["ceiling"]:
            rep["threshold_um2"] = float(best["value_um2"])
        else:
            rep["baseline_um2"] = float(best["value_um2"])
        ratio = _one_ratio(stmts["ratios"])
        if ratio is not None:
            rep["ratio"] = ratio
        if rep["threshold_um2"] is None:
            if ratio is None:
                return _refuse(
                    rep, "no_acceptance_rule",
                    f"a baseline of {rep['baseline_um2']:g} um^2 is declared "
                    f"for '{best['key']}' at {best['source']}:{best['line']}, "
                    "but the document states no acceptance rule (no ceiling "
                    "and no 'baseline x <n>' multiplier) to turn it into one")
            rep["threshold_um2"] = round(float(rep["baseline_um2"]) * ratio, 6)
            rep["derived_um2"] = rep["threshold_um2"]
        rep["note"] = (
            f"the design declares {rep['threshold_um2']:g} um^2 for "
            f"'{best['key']}' (matched {cands}) at "
            f"{best['source']}:{best['line']} — {best['row']}")
        return rep

    # ── tier 2: a baseline row attributed to this run's technology ─────────
    mine = [b for b in stmts["baselines"]
            if _text_names_family(str(b["attribution"]), cands)]
    if mine:
        values = sorted({round(float(b["value_um2"]), 6) for b in mine})
        if len(values) > 1:
            return _refuse(
                rep, "declared_baseline_ambiguous",
                f"{len(mine)} standard-cell area baseline(s) are attributed to "
                f"{cands} and they DISAGREE {values} um^2; refusing to pick "
                "one. Rows: " + "; ".join(
                    f"{b['source']}:{b['line']} {b['row']}" for b in mine))
        best = mine[0]
        rep.update({
            "tier": DECLARED_BASELINE_TIER, "determined": True,
            "baseline_um2": float(best["value_um2"]),
            "matched_name": _text_names_family(str(best["attribution"]), cands),
            "source": best["source"], "line": best["line"], "row": best["row"],
            # The ceiling's citation overwrites `source`/`line` below when the
            # document states one, so the BASELINE's own citation is kept in
            # its own fields. Provenance that survives only in `note` is
            # provenance a consumer has to parse English to reach.
            "baseline_source": best["source"], "baseline_line": best["line"],
            "baseline_row": best["row"],
            "baseline_attribution": best["attribution"]})
        ratio = _one_ratio(stmts["ratios"])
        rep["ratio"] = ratio
        if ratio is not None:
            rep["derived_um2"] = round(float(best["value_um2"]) * ratio, 6)
        # The document's OWN stated ceiling is the design's number and wins over
        # the product, which can differ from it by the document's rounding. Both
        # travel, so a reader can see the rounding rather than guess at it.
        ceilings = sorted({round(float(c["value_um2"]), 6)
                           for c in stmts["ceilings"]})
        if len(ceilings) > 1:
            return _refuse(
                rep, "declared_ceiling_ambiguous",
                f"the document states {len(ceilings)} DIFFERENT standard-cell "
                f"area ceilings {ceilings} um^2; refusing to pick one")
        if ceilings:
            cite = stmts["ceilings"][0]
            rep["threshold_um2"] = ceilings[0]
            rep["note"] = (
                f"the design states a standard-cell area ceiling of "
                f"{ceilings[0]:g} um^2 at {cite['source']}:{cite['line']}, "
                f"from a baseline of {rep['baseline_um2']:g} um^2 attributed to "
                f"'{rep['matched_name']}' at {best['source']}:{best['line']}"
                + (f" (ratio {ratio:g}, product {rep['derived_um2']:g} um^2)"
                   if ratio is not None else ""))
            rep["source"] = cite["source"]
            rep["line"] = cite["line"]
            rep["row"] = cite["row"]
            return rep
        if ratio is None:
            return _refuse(
                rep, "no_acceptance_rule",
                f"a baseline of {rep['baseline_um2']:g} um^2 is attributed to "
                f"'{rep['matched_name']}' at {best['source']}:{best['line']}, "
                "but the document states no acceptance rule (no ceiling and no "
                "'baseline x <n>' multiplier) to turn it into one")
        rep["threshold_um2"] = rep["derived_um2"]
        rep["note"] = (
            f"the design declares a baseline of {rep['baseline_um2']:g} um^2 "
            f"for '{rep['matched_name']}' at {best['source']}:{best['line']} "
            f"and an acceptance ratio of {ratio:g}, giving "
            f"{rep['threshold_um2']:g} um^2")
        return rep

    # ── tier 3: measured in this run's own library ─────────────────────────
    if reference_area_um2:
        ratio = _one_ratio(stmts["ratios"])
        if ratio is None:
            return _refuse(
                rep, "no_acceptance_rule",
                f"a reference-netlist area of {float(reference_area_um2):g} "
                "um^2 was measured in this run's own cell library, but the "
                "document states no acceptance rule (no 'baseline x <n>' "
                "multiplier) to turn it into a threshold")
        rep.update({
            "tier": DERIVED_REFERENCE_TIER, "determined": True,
            "baseline_um2": float(reference_area_um2), "ratio": ratio,
            "matched_name": cands[0],
            "threshold_um2": round(float(reference_area_um2) * ratio, 6),
            "source": reference_area_cite or None, "row": "",
            "note": ""})
        rep["derived_um2"] = rep["threshold_um2"]
        rep["note"] = (
            f"DERIVED for '{cands[0]}': the reference netlist measures "
            f"{float(reference_area_um2):g} um^2 in this run's own cell "
            f"library ({reference_area_cite or 'caller-supplied'}) and the "
            f"design's acceptance ratio is {ratio:g}, giving "
            f"{rep['threshold_um2']:g} um^2")
        return rep

    # ── the refusal this issue exists for ─────────────────────────────────
    seen = rep["attributions_seen"] or ["(the baseline row states no scope)"]
    return _refuse(
        rep, "baseline_not_attributed_to_this_technology",
        f"the design states a standard-cell area sign-off, but no baseline is "
        f"attributed to {cands}. What the document DOES attribute its baseline "
        f"to: {seen}. A ceiling derived from another technology's baseline is "
        "not a ceiling for this run, and reporting it as PASS or as FAIL would "
        "both be false statements about this design.")


def _one_ratio(ratios: Sequence[Dict[str, object]]) -> Optional[float]:
    """The single acceptance ratio the document states, or None.

    Two different ratios is a refusal, not a vote — the same rule the rest of
    this module follows.
    """
    vals = sorted({round(float(r["ratio"]), 6) for r in ratios})
    return vals[0] if len(vals) == 1 else None


def resolve(docs: Sequence[Path], candidates: Sequence[str],
            **kw) -> Dict[str, object]:
    """:func:`resolve_texts` over files on disk."""
    items: List[Tuple[str, str]] = []
    for d in docs:
        try:
            items.append((str(d), Path(d).read_text(errors="ignore")))
        except OSError:
            continue
    return resolve_texts(items, candidates, **kw)


# ── the verdict a consumer may publish ─────────────────────────────────────
def verdict(rep: Dict[str, object],
            measured_um2: Optional[float]) -> Dict[str, object]:
    """PASS / FAIL / NOT_DETERMINED for a measured standard-cell area.

    NOT_DETERMINED whenever the denominator is not this run's, whatever the
    measured figure is. A cross-technology FAIL is as wrong as a
    cross-technology PASS and this function will emit neither.
    """
    out = {"verdict": NOT_DETERMINED,
           "measured_um2": (float(measured_um2)
                            if measured_um2 is not None else None),
           "threshold_um2": rep.get("threshold_um2"),
           "tier": rep.get("tier"),
           "reason": rep.get("reason") or "",
           "note": rep.get("note") or ""}
    if not rep.get("determined"):
        out["reason"] = rep.get("reason") or "not_determined"
        out["note"] = rep.get("note") or NOT_DETERMINED_DISCLOSURE
        return out
    if measured_um2 is None:
        out["reason"] = "no_measured_area"
        out["note"] = ("a threshold was resolved but this run published no "
                       "standard-cell area to compare against it")
        return out
    thr = float(rep["threshold_um2"])
    out["verdict"] = PASS if float(measured_um2) <= thr else FAIL
    out["reason"] = ""
    out["note"] = (f"{float(measured_um2):g} um^2 measured against "
                   f"{thr:g} um^2 resolved via '{rep.get('tier')}'"
                   + (f" ({rep.get('source')}"
                      + (f":{rep.get('line')}" if rep.get("line") else "")
                      + ")" if rep.get("source") else ""))
    return out


# ── what the run built against, as far as the project itself says ──────────
def run_technology(project: Path) -> Dict[str, object]:
    """The technology names THIS project declares, and whether they are one.

    A design that names two technology families has not chosen one, and a
    baseline resolved against the first of them would be a baseline for a run
    that may not be happening. That is `ambiguous`, and it refuses.
    """
    out: Dict[str, object] = {"candidates": [], "ambiguous": False,
                              "source": None, "note": ""}
    names: List[str] = []
    src = None
    l19 = project / "phase1" / "generated_docs" / "L19_CONSTRAINTS_PDK.json"
    try:
        fields = json.loads(l19.read_text(errors="replace")).get("fields") or {}
    except Exception:
        fields = {}
    if isinstance(fields, dict):
        for key in ("std_cell_library", "pdk_target"):
            v = fields.get(key)
            if isinstance(v, str) and v.strip():
                names.append(v.strip())
                src = src or f"{l19}::fields.{key}"
        alts = fields.get("pdk_target_alternates")
        if isinstance(alts, list):
            for v in alts:
                if isinstance(v, str) and v.strip():
                    names.append(v.strip())
                    src = src or f"{l19}::fields.pdk_target_alternates"
    if not names:
        cfg = project / "config.json"
        try:
            data = json.loads(cfg.read_text(errors="replace"))
        except Exception:
            data = {}
        if isinstance(data, dict):
            for key in ("STD_CELL_LIBRARY", "PDK"):
                v = data.get(key)
                if isinstance(v, str) and v.strip():
                    names.append(v.strip())
                    src = src or f"{cfg}::{key}"
    if not names:
        for key in ("STD_CELL_LIBRARY", "PDK"):
            v = os.environ.get(key, "")
            if v.strip():
                names.append(v.strip())
                src = src or f"env::{key}"
    uniq: List[str] = []
    for n in names:
        if n not in uniq:
            uniq.append(n)
    # CLUSTER BY FAMILY, NOT BY TOKEN EQUALITY. A project that states BOTH its
    # std-cell library and its PDK — the ordinary, well-declared case — states
    # two DIFFERENT tokens for ONE technology, and comparing the tokens for
    # equality read that as two families and refused. Measured on this file's
    # own resolver before the fix: library + its own PDK came back
    # `ambiguous: True`, which would have turned every properly-declared
    # project into NOT_DETERMINED and made the whole fix inert on the case it
    # exists for. `same_family` is the repo's one answer to "are these the same
    # technology" and it is the answer here too.
    clusters: List[List[str]] = []
    for n in uniq:
        for c in clusters:
            if same_family(n, c[0]) or family(n) == family(c[0]):
                c.append(n)
                break
        else:
            clusters.append([n])
    out["source"] = src
    if len(clusters) > 1:
        out["ambiguous"] = True
        out["note"] = (f"the project names {len(clusters)} technology families "
                       f"{[c[0] for c in clusters]} and has not chosen one, so "
                       "no run technology is determined here")
        return out
    out["candidates"] = uniq
    out["note"] = (f"resolved from {src}" if src else
                   "the project states no technology")
    return out


#: Where the L7 emitter publishes what it used, project-relative.
L7_FIELD = "area_signoff_baseline"


def for_l7(project: Path, docs: Sequence[Tuple[str, str]]) -> Optional[Dict]:
    """The block the L7 emitter writes, or None when the design states nothing.

    Returning None for a design that declares no standard-cell area sign-off is
    deliberate: the L7 documents of every such design stay byte-identical, so
    this disclosure adds a field exactly where there is something to disclose.
    """
    tech = run_technology(project)
    rep = resolve_texts(docs, [] if tech["ambiguous"] else tech["candidates"])
    if not rep["signoff_row_found"]:
        return None
    if tech["ambiguous"]:
        rep["reason"] = "run_technology_ambiguous"
        rep["note"] = tech["note"]
    rep["run_technology"] = tech
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Resolve the standard-cell area threshold the design "
                    "declares FOR THIS RUN's technology, or refuse by name.")
    ap.add_argument("--docs-dir", help="the design's input/docs directory")
    ap.add_argument("--doc", action="append", default=[],
                    help="an explicit doc to read (repeatable)")
    ap.add_argument("--library", default="",
                    help="the std-cell library this run builds against")
    ap.add_argument("--pdk", default="", help="the PDK this run resolved")
    ap.add_argument("--reference-area-um2", type=float, default=None,
                    help="a reference-netlist area MEASURED in this run's own "
                         "cell library")
    ap.add_argument("--reference-area-cite", default="")
    ap.add_argument("--measured-area-um2", type=float, default=None,
                    help="this run's synthesised standard-cell area")
    ap.add_argument("--json", help="write the structured report here")
    args = ap.parse_args(argv)

    docs = [Path(d) for d in args.doc]
    if args.docs_dir:
        d = Path(args.docs_dir)
        if d.is_dir():
            docs.extend(sorted(p for p in d.rglob("*")
                               if p.is_file()
                               and p.suffix.lower() in (".md", ".txt")))
    rep = resolve(docs, [c for c in (args.library, args.pdk) if c],
                  reference_area_um2=args.reference_area_um2,
                  reference_area_cite=args.reference_area_cite)
    rep["library"] = args.library
    rep["pdk"] = args.pdk
    rep["verdict"] = verdict(rep, args.measured_area_um2)
    if args.json:
        atomic_write_text(Path(args.json), json.dumps(rep, indent=2) + "\n")
    print(json.dumps(rep, indent=2, ensure_ascii=False))
    # 0 = a threshold was resolved (and, if measured, met);
    # 1 = resolved and the measured area exceeds it;
    # 2 = NOT_DETERMINED — no valid denominator for this run's technology.
    if not rep["determined"]:
        return 2
    return 1 if rep["verdict"]["verdict"] == FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
