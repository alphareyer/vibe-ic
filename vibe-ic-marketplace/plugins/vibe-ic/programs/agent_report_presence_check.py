#!/usr/bin/env python3
"""
agent_report_presence_check.py — verify the project ships a structured
top-level AGENT_REPORT.md summarising what the run delivered.

Real-world inspiration: phase2+3_v10619-vendor/AGENT_REPORT.md carried a
clear five-section breakdown (Verdict, Acceptance evidence, Waivers
list, Discoveries, Iteration log) that made the run independently
reviewable. phase2+3_v10619 (other agent) shipped no such file, so
verifying its end-state required reading raw logs across the project
tree. v1.6.24 promotes the AGENT_REPORT.md discipline from convention
to gate so every project has one auditable report card.

Required structure:
  Top-level `AGENT_REPORT.md` exists, non-empty, and contains a heading
  for each of the five mandatory sections (case-insensitive
  substring match, since markdown heading levels and exact wording
  vary across runs):
      - Verdict
      - Acceptance evidence  (also accepts: "Acceptance" / "Deliverables")
      - Waivers list         (also accepts: "Waivers" / "Deferred")
      - Discoveries          (also accepts: "Backlog" / "Findings")
      - Iteration log        (also accepts: "Iterations" / "Wave log")

Project-level acceptance: file exists + all 5 section synonyms hit.

This gate is universal — every project type benefits from a final
report. NO VACUOUS_PASS — there is no "this IC class doesn't need a
report" exception.

Usage:
    python3 agent_report_presence_check.py <project_dir> [--json <out>]

Exit codes:
    0  PASS
    1  AGENT_REPORT.md missing OR sections missing
    2  argument or I/O error

chip-AGNOSTIC. Section synonyms come from the v10619-vendor template
plus common alternates; no chip / project-name hardcoded.
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
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional, Tuple
from _atomic_artefact import write_text as atomic_write_text  # vibe-ic#1082 (helper from PR #1094)


# Each tuple is (canonical_section_name, [synonym_substrings_lowercase]).
# A section is satisfied when ANY synonym appears as part of a markdown
# heading (`#`, `##`, `###`, ...). Synonyms are matched
# case-insensitively against the heading text only, so body text
# referring to "verdict" doesn't accidentally satisfy the section.
_REQUIRED_SECTIONS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("Verdict",
     ("verdict",)),
    ("Acceptance evidence",
     ("acceptance evidence", "acceptance", "deliverables")),
    ("Waivers list",
     ("waivers list", "waivers", "deferred")),
    ("Discoveries",
     ("discoveries", "backlog", "findings")),
    ("Iteration log",
     ("iteration log", "iterations", "wave log", "iteration")),
)
_RE_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


@dataclass
class SectionCheck:
    canonical_name: str
    matched_synonym: Optional[str] = None
    matched_heading: Optional[str] = None


def _extract_headings(text: str) -> List[str]:
    """Return all markdown heading texts (without the `#` prefix)."""
    return [m.group(2).strip().lower() for m in _RE_HEADING.finditer(text)]


def _check_sections(
        text: str,
        required: Optional[Tuple[Tuple[str, Tuple[str, ...]], ...]] = None
) -> Tuple[List[SectionCheck], List[str]]:
    """For each required section, find a heading whose text contains
    one of the synonyms. Returns (per-section results, overall missing).
    """
    headings = _extract_headings(text)
    results: List[SectionCheck] = []
    missing: List[str] = []
    for canonical, synonyms in (required or _REQUIRED_SECTIONS):
        sc = SectionCheck(canonical_name=canonical)
        for h in headings:
            for syn in synonyms:
                if syn in h:
                    sc.matched_synonym = syn
                    sc.matched_heading = h
                    break
            if sc.matched_synonym:
                break
        results.append(sc)
        if not sc.matched_synonym:
            missing.append(canonical)
    return results, missing


def _candidate_rel_paths() -> Tuple[str, ...]:
    """THE register of what a report card is, owned by the sibling gate.

    R-0915-51. This gate demanded `AGENT_REPORT.md`, and NOTHING in the
    shipped flow writes one: the card moved to `reports/final_summary.md` in
    v1.6.32 (`final_report_generate`, wired into all five runners) and this
    gate was never re-pointed. On the SPM verdict run that single row was the
    ONLY cause of the overall FAIL — it failed step 36 through
    `step_internal_fail_bubble_up_check` and voided steps 37, 37.4, 37.5ip
    and 38.

    The order is the sibling's: the generated card first, the hand-authored
    one as back-compat. Imported rather than re-typed so the two gates cannot
    disagree about what a report card is.
    """
    try:
        from agent_report_sha256_attestation_check import (
            _REPORT_CANDIDATE_REL_PATHS)
        return tuple(_REPORT_CANDIDATE_REL_PATHS)
    except ImportError:
        return ("reports/final_summary.md", "AGENT_REPORT.md")


def _sections_for(rel: str) -> Tuple[Tuple[str, Tuple[str, ...]], ...]:
    """The sections THAT card declares.

    A generated card is held to what its generator GUARANTEES, imported from
    `final_report_generate.MANDATORY_SECTIONS` so a renamed heading moves both
    sides at once. A hand-authored `AGENT_REPORT.md` keeps the five this gate
    has always demanded.

    NOBODY HAND-WRITES A VERDICT CARD. Accepting the generated card is what
    keeps it that way: the alternative is an agent typing a verdict into a
    file to satisfy a gate, which is the fabrication surface the
    anti-fabrication rules exist to close.
    """
    if Path(rel).name.lower() != "final_summary.md":
        return _REQUIRED_SECTIONS
    try:
        from final_report_generate import MANDATORY_SECTIONS
    except ImportError:
        return _REQUIRED_SECTIONS
    return tuple((name, tuple(syn)) for name, _heading, syn
                 in MANDATORY_SECTIONS)


def resolve_report(project: Path) -> Optional[Tuple[Path, str]]:
    """(path, project-relative spelling) of the card this run produced."""
    for rel in _candidate_rel_paths():
        p = project / rel
        if p.is_file():
            return p, rel
    return None


def audit(project: Path) -> Tuple[str, List[SectionCheck], List[str]]:
    resolved = resolve_report(project)
    if resolved is None:
        # STILL FAILS when there is no card at all — that is the whole point
        # of a presence check, and re-pointing it must not cost it.
        # Phrased so each candidate is named with "does not exist": two
        # shipped tests key on that wording, and the contract they pin --
        # "this run produced no report card at all" -- is unchanged by
        # re-pointing WHICH cards count. Widening the population must not
        # silently rewrite the message its consumers read.
        return "FAIL", [], [
            "no report card exists: "
            + " and ".join(f"{rel} does not exist"
                           for rel in _candidate_rel_paths())]
    report_path, rel = resolved
    text = report_path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        return "FAIL", [], [f"{rel} is empty"]
    sections, missing = _check_sections(text, _sections_for(rel))
    if missing:
        diagnostics = [f"{rel}: missing section: {s}" for s in missing]
        return "FAIL", sections, diagnostics
    return "PASS", sections, []


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Verify project ships a structured AGENT_REPORT.md.")
    ap.add_argument("project_dir")
    ap.add_argument("--json", help="write JSON report to this path")
    args = ap.parse_args(argv)

    project = Path(args.project_dir).resolve()
    if not project.is_dir():
        print(f"error: project dir not found: {project}", file=sys.stderr)
        return 2

    verdict, sections, diagnostics = audit(project)
    resolved = resolve_report(project)
    rel = resolved[1] if resolved else None
    report = {
        "gate": "agent_report_presence_check",
        "verdict": verdict,
        "project": str(project),
        # The card this run ACTUALLY produced, not a constant. `null` when
        # there is none, which is the FAIL above.
        "report_path": rel,
        "report_candidates": list(_candidate_rel_paths()),
        "required_sections": [c for c, _ in _sections_for(rel or "")],
        "section_results": [asdict(s) for s in sections],
        "diagnostics": diagnostics,
    }

    if args.json:
        out_path = Path(args.json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(out_path, json.dumps(report, indent=2) + "\n")

    if verdict == "PASS":
        # Name the card that was actually read and the count THAT card is
        # held to; the old line said "AGENT_REPORT.md ... 5 sections" whatever
        # it had resolved, which is how the mis-pointing stayed invisible.
        print(f"PASS: {rel} present with all "
              f"{len(report['required_sections'])} section(s) it declares")
        return 0
    print(f"FAIL: report card problems:", file=sys.stderr)
    for d in diagnostics:
        print(f"  {d}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
