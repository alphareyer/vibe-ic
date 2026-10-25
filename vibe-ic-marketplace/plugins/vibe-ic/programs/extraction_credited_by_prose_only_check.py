#!/usr/bin/env python3
"""extraction_credited_by_prose_only_check.py — how much of the Phase-1
coverage number is earned by a field that merely COPIED the input?

ENFORCEMENT: advisory

The line above is a DECLARATION, in the anchored form `flow_gate_enforcement_
audit.declared_intent` reads. This runs, prints, and cannot deny its step a PASS
tier; the reason is measured below and repeated at its flow clause.

WHAT THE COVERAGE NUMBER MEANS TODAY
====================================
`phase1/extraction_patterns.json` says so in its own header: "this file is the
coverage denominator". A literal is credited when its verbatim string appears
anywhere in the `generated_docs/L*.json` payload.

Several L fields carry the input PROSE, unchanged. `L2.frs_sections` holds
requirement sentences as written. `L8.auto_discovered_literals` holds every
literal the harvester saw, by construction. `extraction_evidence` quotes the
matched substring. A literal sitting in one of those has not been extracted into
anything — it has been copied — yet it satisfies the credit test.

MEASURED, on a 4-channel PWM controller written as a plain-English spec:

    denominator                                35
    reported coverage                          100.0%
    credited ONLY by a verbatim-prose field     8
    structured-only coverage                   27/35 = 77.1%

and the eight are worth reading:

    0xDEADBEEF  0xCAFEBABE  0xFEEDFACE  0xBAADF00D   invented trim constants,
                                                     structured nowhere
    25 MHz                                           the SYSTEM CLOCK
    98 kHz      760 Hz      3.3 V

`25 MHz` is the one that matters. The same run emitted `L8.clock_mhz = 10.0` —
the SPI clock, from the same sentence — so the coverage metric certified as
extracted the very number the extractor got wrong. A metric cannot be a check on
extraction while a verbatim copy of the input satisfies it.

WHY THIS REPORTS AND DOES NOT REFUSE
====================================
The honest structured-only figure is lower than the published one on every
project, by construction — so refusing on it would redden every tree at once
over debt this program did not create. What it CAN do is make the gap a number
somebody reads, which is the difference between a known cost and an invisible
one. Promote it to blocking when the published figure and this one agree.

Chip-AGNOSTIC: the predicate is a field-name classification and a substring
test. No protocol, chip class or layer vocabulary participates.

An explicitly empty, well-formed declared literal census is NOT_APPLICABLE,
with the opened census file and its hash as evidence. This says nothing about
source completeness. Missing or unreadable census/L-doc inputs still refuse;
positive prose-only findings keep the advisory policy described above.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _atomic_artefact as _aa  # noqa: E402  (vibe-ic#1082)
import _structural_absence as _sa  # noqa: E402

#: Fields whose CONTENT is a copy of the input rather than a structured reading
#: of it. Named individually, never by a pattern: a field earns a place here by
#: being demonstrably a verbatim carrier, and the list is short enough to read.
PROSE_CARRIER_FIELDS = frozenset({
    "frs_sections",                          # requirement sentences, as written
    "auto_discovered_literals",              # every literal, by construction
    "extraction_evidence",                   # quotes the matched substring
    "electrical_specs_unextracted_mentions",  # says so in its own name
    "notes",
    "description",
    "extraction_strategy",
    "_generator",
    "_comment",
})


def _unique_census_fields(pairs):
    fields = {}
    for name, value in pairs:
        if name in fields:
            raise ValueError(f"duplicate declared literal-census field {name!r}")
        fields[name] = value
    return fields


def _denominator(project: Path) -> Tuple[List[str], Dict]:
    """The literals `extraction_patterns.json` declares as the denominator."""
    f = project / "phase1" / "extraction_patterns.json"
    raw = f.read_bytes()
    doc = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_census_fields)
    if not isinstance(doc, dict):
        raise ValueError(f"{f}: the declared literal census must be an object")
    out: List[str] = []
    groups = []
    for key, rows in doc.items():
        if key.startswith("_"):
            continue
        if not isinstance(rows, list):
            raise ValueError(f"{f}: public census scope {key!r} must be a list")
        groups.append(key)
        for row in rows:
            lit = row.get("literal") if isinstance(row, dict) else row
            if not isinstance(lit, str) or not lit.strip():
                raise ValueError(f"{f}: invalid literal in census scope {key!r}")
            out.append(lit)
    return out, {
        "path": f.relative_to(project).as_posix(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "declared_source_groups": sorted(groups),
        "declared_literal_count": len(out),
    }


def _haystacks(project: Path) -> Tuple[str, str]:
    """(structured, prose) — the L-doc payload split by field classification."""
    gd = project / "phase1" / "generated_docs"
    structured: List[str] = []
    prose: List[str] = []
    files = sorted(gd.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"{gd}: no generated document JSON inputs")
    for f in files:
        doc = json.loads(f.read_bytes().decode("utf-8"))
        if not isinstance(doc, dict):
            raise ValueError(f"{f}: generated document must be an object")
        for key, value in doc.items():
            blob = json.dumps(value).lower()
            (prose if key in PROSE_CARRIER_FIELDS else structured).append(blob)
    return "\n".join(structured), "\n".join(prose)


def audit(project: Path) -> Dict:
    lits, scope = _denominator(project)
    structured, prose = _haystacks(project)
    prose_only, uncredited, structured_ok = [], [], []
    for lit in lits:
        low = lit.lower()
        in_s, in_p = low in structured, low in prose
        if in_s:
            structured_ok.append(lit)
        elif in_p:
            prose_only.append(lit)
        else:
            uncredited.append(lit)
    total = len(lits)
    report = {
        "denominator": total,
        "structured": len(structured_ok),
        "prose_only": prose_only,
        "uncredited": uncredited,
        "structured_pct": (round(100.0 * len(structured_ok) / total, 1)
                           if total else None),
        "prose_carrier_fields": sorted(PROSE_CARRIER_FIELDS),
        "census_scope": scope,
    }
    if not total:
        # This file explicitly declares the census audited here. Opening and
        # validating it establishes an empty CENSUS, never source completeness.
        # A missing/invalid file or unreadable L-doc cannot reach this branch.
        evidence = _sa.absence(
            "declared literal-census file(s)", scanned=1, found=0,
            names=[scope["path"]],
            detail=("the validated declared census has no literal members; "
                    "this is not a source-completeness verdict, and other "
                    "D1 source-coverage and expert checks remain required"))
        report.update({"verdict": "NOT_APPLICABLE",
                       "reason": evidence["detail"]})
        _sa.attach(report, evidence)
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=("how much of the Phase-1 coverage number is earned by a "
                     "field that copied the input rather than read it"))
    ap.add_argument("project", nargs="?", default=".")
    ap.add_argument("--json", default=None, help="write the audit as JSON")
    args = ap.parse_args(argv)

    project = Path(args.project)
    if not project.is_dir():
        print(f"ERROR: not a directory: {project}", file=sys.stderr)
        return 2

    try:
        rep = audit(project)
    except (OSError, ValueError) as exc:
        rep = {"verdict": "NOT_MEASURED",
               "reason_class": ("BLOCKED_BY_UPSTREAM" if isinstance(exc, FileNotFoundError)
                                else "EXECUTION_ERROR"),
               "reason": str(exc)}

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        # vibe-ic#1082: the declared destination appears under its final name
        # only once complete, so a reader never resolves a half-written report.
        _aa.write_text(out, json.dumps(rep, indent=2) + "\n")

    if rep.get("verdict") == "NOT_MEASURED":
        print("[CANNOT CHECK] extraction_credited_by_prose_only_check: "
              + rep["reason"])
        return 2
    n = rep["denominator"]
    if not n:
        print("NOT_APPLICABLE: " + _sa.sentence(
            rep[_sa.EVIDENCE_KEY], "extraction_credited_by_prose_only_check"))
        return 2

    for lit in rep["prose_only"]:
        print(f"  [PROSE-ONLY] {lit!r} appears only in a field that carries the "
              f"input verbatim; it was copied, not extracted")
    for lit in rep["uncredited"]:
        print(f"  [UNCREDITED] {lit!r} is in the denominator and in no L doc "
              f"at all")
    print(f"extraction_credited_by_prose_only_check: {rep['structured']}/"
          f"{n} = {rep['structured_pct']}% of the coverage denominator is "
          f"credited by a STRUCTURED field; {len(rep['prose_only'])} literal(s) "
          f"are credited only by a verbatim-prose carrier "
          f"({', '.join(sorted(PROSE_CARRIER_FIELDS))}). "
          f"ADVISORY — reported, never blocking.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
