#!/usr/bin/env python3
"""l24_signoff_requirements_extract.py — R-0915-38.

WHAT THIS EXTRACTS
==================
L24_SIGNOFF's REQUIREMENTS, from the design's own input documents: which
sign-off checks the input says must be run, what it says they must achieve,
at which corners, and WHERE IT SAYS SO (document + line + the line itself).

WHY REQUIREMENTS AND NOT STATUSES
---------------------------------
L24's field template is `drc_status`, `lvs_status`, `sta_status`,
`ir_drop_status`, `antenna_status`, `tapeout_gates[]`. Those are OUTCOMES,
produced in phase 3. Phase 1 runs before DRC, LVS and STA exist, so a phase-1
extractor that filled them would be publishing a certificate for a check that
has not run — and `l24_signoff_evidence_backed_check` is built precisely to
refuse that: it treats every `*_status` / `status` / `verdict` / `result` key
at any depth as a CLAIM requiring an in-project evidence path plus the value
read back. Filling them here would turn the layer from inert to FAILING.

So nothing this module writes uses those key names. The requirement rows live
in `signoff_requirements`, keyed `check` / `requirement` / `corners` /
`threshold` / `citation`, and the outcome fields stay null for phase 3 to
answer. The two halves then meet in the gate: extracted requirement on one
side, the run's own report on the other.

WHAT IS NEVER INVENTED
----------------------
A check the input does not mention is emitted as a row with
`stated: false, requirement: null` AND the scan that looked for it — the
documents read and the terms searched. An absent requirement is reported as a
searched-and-not-found, never as a silent omission and never as a default.
A check the input DOES mention but for which no requirement word is
recognisable keeps `requirement: null` with `stated: true` and the citation,
so a reader sees the sentence and can judge it; the extractor does not guess
what "DRC" alone was supposed to demand.

CORNERS ARE ATTRIBUTED CONSERVATIVELY. A corner token counts as a check's
corner only when it appears inside that check's own clause. MEASURED on spm, a
line reads "TT corner / 10 ns ... sign-off (DRC/LVS/Antenna 全 clean)" — the
corner is stated BEFORE the checks, and extending the clause backwards to
catch it would drag the PREVIOUS check's requirement word in with it ("LVS
clean、STA met" would make STA `clean`, which is the bug this clause logic
exists to fix). So the corner is not attributed, `all_citations` points a
reader at every line that named the check, and the extractor under-claims
rather than inventing a requirement the input did not state for that check.

chip-AGNOSTIC: no design, PDK, vendor, node or signal name appears here. The
check vocabulary is L24's own — its `extraction_hints` name "DRC / LVS / STA /
antenna / IR-drop status per gate" and "the tapeout gate list" — plus EM and
the tapeout precheck named in the ruling.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["SIGNOFF_CHECKS", "extract_signoff_requirements",
           "report_tokens_for"]

#: check -> (the word-bounded spellings that NAME it in an input document,
#:           the tokens that name its REPORT in a run tree).
#: Order is the emission order of the rows. The report tokens are what
#: `l24_signoff_evidence_backed_check` matches against report filenames and
#: their `program` / `gate` fields, so the two halves of R-0915-38 — what the
#: input requires and what the run measured — are bound by ONE table.
SIGNOFF_CHECKS: Tuple[Tuple[str, Tuple[str, ...], Tuple[str, ...]], ...] = (
    ("DRC", ("DRC", "design rule check"), ("drc",)),
    ("LVS", ("LVS", "layout versus schematic", "layout vs schematic"),
     ("lvs",)),
    ("antenna", ("antenna",), ("antenna",)),
    ("STA", ("STA", "static timing analysis", "timing sign-off",
             "timing signoff"), ("sta", "timing")),
    ("IR_drop", ("IR-drop", "IR drop", "IR_drop", "voltage drop"),
     ("ir_drop", "ir")),
    ("EM", ("EM", "electromigration", "electro-migration"),
     ("em", "electromigration")),
    ("tapeout_precheck", ("tapeout precheck", "tape-out precheck",
                          "tapeout pre-check", "tapeout checklist",
                          "tape-out checklist"),
     ("tapeout_precheck", "tapeout_checklist")),
)


def report_tokens_for(check: str) -> Tuple[str, ...]:
    """The run-tree report tokens for one check name, or ()."""
    for name, _spellings, tokens in SIGNOFF_CHECKS:
        if name == check:
            return tokens
    return ()

#: The requirement a line STATES, as the input's own word. Ordered: the first
#: match wins, so the specific numeric forms are tried before the bare words.
_REQUIREMENT_PATTERNS: Tuple[Tuple[str, str], ...] = (
    ("zero_violations", r"(?<![a-z0-9])(?:0|zero)\s*(?:violation|error|drc|"
                        r"vio)\w*"),
    ("percent_clean", r"100\s*%\s*clean"),
    ("clean", r"(?<![a-z0-9])clean(?![a-z0-9])"),
    ("met", r"(?<![a-z0-9])met(?![a-z0-9])"),
    ("all_zero", r"(?<![a-z0-9])(?:全\s*0|all\s+zero)(?![a-z0-9])"),
    ("pass", r"(?<![a-z0-9])pass(?:ed)?(?![a-z0-9])"),
)

#: A corner named on the line. Uppercase process-corner tokens only; these are
#: PDK-independent names, not a node or vendor.
_CORNER_RE = re.compile(r"(?<![A-Za-z0-9])(SS|TT|FF|SF|FS)(?![A-Za-z0-9])")

#: A stated numeric threshold with a unit.
_THRESHOLD_RE = re.compile(
    r"(?<![A-Za-z0-9])(\d+(?:\.\d+)?)\s*(ns|ps|mV|V|%|uW|mW|mA|uA)"
    r"(?![A-Za-z0-9])", re.IGNORECASE)

_DOC_SUFFIXES = (".md", ".markdown", ".txt", ".rst", ".adoc", ".asciidoc")


def _word_bounded(term: str) -> re.Pattern:
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(term)
                      + r"(?![A-Za-z0-9])", re.IGNORECASE)


def _input_documents(project: Path) -> List[Path]:
    docs = project / "input" / "docs"
    if not docs.is_dir():
        return []
    return sorted(p for p in docs.rglob("*")
                  if p.is_file() and p.suffix.lower() in _DOC_SUFFIXES)


def _clause_for(line: str, match: re.Match) -> str:
    """The span of the line that belongs to THIS check's mention.

    MEASURED, and the reason this function exists: an input line reading
    "DRC clean, LVS clean, STA met" states THREE different requirements, and a
    reader that scans the whole line hands the first one it finds to all
    three — so STA came back `clean`, which the input never says. The clause
    runs from this check's token to the next check's token on the same line,
    or to the end of it.
    """
    end = len(line)
    for _check, spellings, _tokens in SIGNOFF_CHECKS:
        for spelling in spellings:
            for other in _word_bounded(spelling).finditer(line):
                if match.start() < other.start() < end:
                    end = other.start()
    return line[match.start():end]


def _requirement_of(clause: str) -> Optional[str]:
    low = clause.lower()
    for name, pattern in _REQUIREMENT_PATTERNS:
        if re.search(pattern, low, re.IGNORECASE):
            return name
    return None


def _threshold_of(line: str) -> Optional[Dict[str, Any]]:
    m = _THRESHOLD_RE.search(line)
    if not m:
        return None
    return {"value": float(m.group(1)), "unit": m.group(2)}


def extract_signoff_requirements(project: Path) -> Optional[Dict[str, Any]]:
    """L24's requirement rows plus the scan that produced them, or None.

    Returns None — leaving the caller to emit whatever it would have emitted
    — when the question cannot be answered honestly: no project, or no
    readable input document. An empty corpus is a scan with no denominator.
    """
    if project is None:
        return None
    project = Path(project)
    docs = _input_documents(project)
    if not docs:
        return None

    scanned: List[str] = []
    lines_by_doc: List[Tuple[str, List[str]]] = []
    for doc in docs:
        try:
            text = doc.read_text(encoding="utf-8", errors="replace")
        except OSError:
            # Unreadable is not absent; refuse the whole extraction.
            return None
        try:
            rel = doc.relative_to(project).as_posix()
        except ValueError:
            rel = doc.name
        scanned.append(rel)
        lines_by_doc.append((rel, text.splitlines()))

    rows: List[Dict[str, Any]] = []
    stated_count = 0
    for check, spellings, _report_tokens in SIGNOFF_CHECKS:
        patterns = [_word_bounded(s) for s in spellings]
        hits: List[Dict[str, Any]] = []
        for rel, lines in lines_by_doc:
            for lineno, line in enumerate(lines, start=1):
                match = next((m for m in (p.search(line) for p in patterns)
                              if m), None)
                if match is None:
                    continue
                clause = _clause_for(line, match)
                hits.append({
                    "document": rel,
                    "line": lineno,
                    # Bounded so one pathological line cannot dominate the
                    # layer; the citation is a pointer, not a copy.
                    "text": line.strip()[:400],
                    "clause": clause.strip()[:200],
                    "requirement": _requirement_of(clause),
                    # Corners are read from the CLAUSE too, for the same
                    # reason: a corner named beside a different check on the
                    # same line is not this check's corner.
                    "corners": sorted(set(_CORNER_RE.findall(clause))),
                    "threshold": _threshold_of(clause),
                })
        if not hits:
            rows.append({
                "check": check,
                "stated": False,
                "requirement": None,
                "corners": [],
                "threshold": None,
                "citation": None,
                "searched": {"spellings": list(spellings),
                             "documents": list(scanned)},
            })
            continue
        stated_count += 1
        # The citation is the first hit that actually STATES a requirement;
        # failing that, the first hit at all — so a reader always lands on the
        # most informative sentence the input offers.
        best = next((h for h in hits if h["requirement"]), hits[0])
        rows.append({
            "check": check,
            "stated": True,
            "requirement": best["requirement"],
            "corners": sorted({c for h in hits for c in h["corners"]}),
            "threshold": best["threshold"],
            "citation": {"document": best["document"], "line": best["line"],
                         "text": best["text"], "clause": best["clause"]},
            "occurrences": len(hits),
            "all_citations": [{"document": h["document"], "line": h["line"]}
                              for h in hits[:20]],
        })

    return {
        "signoff_requirements": rows,
        "signoff_requirements_scan": {
            "documents_scanned": scanned,
            "documents_scanned_count": len(scanned),
            "checks_searched": [c for c, _s, _r in SIGNOFF_CHECKS],
            "checks_stated": stated_count,
            "extracted_by": ("l24_signoff_requirements_extract"
                             ".extract_signoff_requirements"),
        },
    }
