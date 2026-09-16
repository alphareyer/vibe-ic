#!/usr/bin/env python3
"""report_belongs_to_project_check.py — a runner report must be ABOUT the
project it sits in.

THE DEFECT (vibe-ic#587)
========================
Runner reports record the project they were produced for::

    reports/phase3/analog_one_shot.json
      "project": "<another operator's home>/AI_IC_design/_c2_adc_run/proj"
      "verdict": "FAIL"

sitting inside a DIFFERENT project tree. The report was carried forward — copied
or inherited from another run — and it brought its verdict with it. Nothing
looked at the `project` field, so:

* the verdict is about someone else's design, and
* it contradicts every per-step gate that audits the same steps in the tree it
  now lives in.

A stale FAIL is the visible half. The dangerous half is a stale **PASS**: a
report attesting a clean run of a design that was never built here, in a
directory a reader takes as evidence about this one.

MEASURED before writing this, over `benchmark-data/`:

    *_one_shot.json found                364
    carrying a `project` field           303
    naming a DIFFERENT project tree       61   (20 %)

across sha256, edge_llm_matmul_accel, u_hawaii_adc and others, with both PASS
and FAIL verdicts among them. So this is not one stray file.

WHY AN EXISTING GATE DOES NOT COVER IT
======================================
`agent_report_presence_check` asks whether the final report EXISTS.
`agent_report_sha256_attestation_check` asks whether it carries a SHA256 table
for every canonical artefact. Both are about the report's CONTENT. Neither asks
the prior question — whether this report is about this project at all — and a
foreign report can satisfy both perfectly, because it was a complete, honest
report of a different run.

WHAT IS COMPARED
================
The `project` field, resolved, against the directory the report sits under —
the path above `reports/`. Both sides are `Path.resolve()`d, so a symlinked or
relative spelling of the same tree is not a finding.

Reports with NO `project` field are counted and reported as UNATTRIBUTED but do
not fail: a report that never claimed a project is not lying about one, and
promoting that to a failure would be a different (and much larger) change.

RELOCATION IS NOT LAUNDERING (`--relocated-from`)
=================================================
An INDEPENDENT AUDIT copies the run tree before reading it — `PUBLISHING.md`
requires an "independently re-derived" verdict before a cell may be published,
and this program's own umbrella ships `flow_compliance_check.py --read-only`
for exactly that, which audits `mkdtemp()/<project name>`. Every report in such
a copy claims the ORIGINAL path and sits in the new one, so ALL of them read as
foreign and a converged run turns red on its way to being published.

MEASURED, on a converged spm run (2026-09-16), same bytes, path alone varied::

    report_belongs_to_project_check <copy at a new path>   rc=1  4 foreign
    report_belongs_to_project_check <same bytes, own path> rc=0  0 foreign

and through the umbrella the copy arm took `Overall: PASS_WITH_WAIVERS` (rc 0)
to `Overall: FAIL` (rc 1), because step 36's refusal also voided four
downstream PASSes.

`--relocated-from ORIGINAL` lets the auditor state the provenance it has. It is
NOT a waiver, and it is deliberately unable to excuse the shape this gate
exists to catch:

* It excuses a **wholesale** relocation only, which takes TWO conditions:
  every foreign report must claim the SAME root and it must be the one named,
  AND no report may claim THIS tree. The second is the load-bearing one — a
  copy is written by the original run, so in a real relocation nothing can
  claim the new path. One report that does means the tree is live, and a
  foreign report beside it is an import, not a move.
* A **mixture** is never excused. If the foreign reports name two or more
  distinct roots, or any root other than the one named, NOTHING is excused: the
  verdict stays FAIL and the output names every foreign report, including the
  ones that would have matched. A carried-forward report sitting among the
  tree's own is precisely a mixture, so the laundering case in #587 — 61 of 303
  reports foreign, across many trees — cannot be turned green by this flag.
* Without the flag, behaviour is unchanged, byte for byte.

So the flag makes the gate say MORE than it did (it now separates "this tree
was moved" from "a report from elsewhere is mixed in"), never less.

`VIBEIC_AUDIT_RELOCATED_FROM` is honoured as a fallback so the umbrella's
`--read-only` copy can declare its own provenance without every flow row having
to learn a new argument; an explicit `--relocated-from` always wins.

Exit: 0 = every attributed report belongs here (or the whole tree was relocated
from the declared root), 1 = at least one is foreign, 2 = nothing could be
checked (no reports found — never a pass).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

#: Runner reports that record their project. The `*_one_shot.json` family is the
#: orchestrator's own output; a glob rather than a list so a new runner is
#: covered on arrival instead of being invisible until someone adds it.
REPORT_GLOB = "**/*_one_shot.json"


def _project_root_of(report: Path) -> Optional[Path]:
    """The project a report SITS IN — the directory above its `reports/`.

    Returns None when the report is not under a `reports/` directory, which
    means this program cannot say where it belongs; that is reported as
    undecidable rather than guessed.
    """
    parts = report.parts
    if "reports" not in parts:
        return None
    return Path(*parts[:parts.index("reports")])


def audit(project: Path,
          relocated_from: Optional[Path] = None,
          ) -> Tuple[List[Dict[str, object]], Dict[str, int]]:
    """(findings, stats) over every runner report under `project`.

    `relocated_from` names the path this tree was copied FROM. It excuses a
    foreign report only when the relocation is WHOLESALE — see the module
    docstring. `stats["relocated"]` counts what it excused and
    `stats["foreign_roots"]` is how many distinct foreign roots were seen, so a
    reader can tell "one tree moved" from "reports from several trees".
    """
    findings: List[Dict[str, object]] = []
    stats = {"reports": 0, "attributed": 0, "unattributed": 0,
             "undecidable": 0, "foreign": 0, "relocated": 0,
             "foreign_roots": 0}

    for rp in sorted(project.glob(REPORT_GLOB)):
        if not rp.is_file():
            continue
        stats["reports"] += 1
        try:
            doc = json.loads(rp.read_text(encoding="utf-8", errors="replace"))
        except Exception:                                    # noqa: BLE001
            stats["undecidable"] += 1
            continue
        if not isinstance(doc, dict):
            stats["undecidable"] += 1
            continue

        claimed = doc.get("project")
        if not claimed or not isinstance(claimed, str):
            stats["unattributed"] += 1
            continue
        stats["attributed"] += 1

        owner = _project_root_of(rp)
        if owner is None:
            stats["undecidable"] += 1
            continue

        try:
            same = Path(claimed).resolve() == owner.resolve()
        except OSError:
            stats["undecidable"] += 1
            continue

        if not same:
            findings.append({
                "report": str(rp),
                "claims_project": claimed,
                "sits_in": str(owner),
                "verdict": doc.get("verdict"),
                "claims_root": str(Path(claimed).resolve()),
            })

    # ── wholesale relocation vs mixture ──────────────────────────────────
    #
    # Counted AFTER the walk, because the question is about the POPULATION:
    # one report cannot tell you whether the tree moved or whether it was
    # carried in from somewhere. Only the set of roots can.
    roots = {str(f["claims_root"]) for f in findings}
    stats["foreign_roots"] = len(roots)
    declared = None
    if relocated_from is not None:
        try:
            declared = str(Path(relocated_from).resolve())
        except OSError:
            declared = None

    # A relocation is excused only when BOTH hold:
    #
    #   (1) every foreign report names the SAME root, and it is the declared
    #       one — one tree moved, not reports gathered from several; and
    #   (2) NO attributed report names THIS tree.
    #
    # (2) is the load-bearing half and the first draft of this fix did not have
    # it. A copy is written by the original run, so in a genuine wholesale
    # relocation nothing can claim the new path — every attributed report
    # predates the copy. The moment one report DOES claim this tree, the tree
    # is being written in place, and a foreign report sitting among its own is
    # the #587 import, not a move. Without (2) an attacker could excuse exactly
    # that by passing the foreign root as `--relocated-from`, which is the
    # laundering this gate exists to catch; the test
    # `test_the_original_587_shape_still_fails_with_the_flag` is that mutation.
    native = stats["attributed"] - len(findings)
    if declared is not None and roots == {declared} and native == 0:
        for f in findings:
            f["relocated_from"] = declared
        stats["relocated"] = len(findings)
        stats["foreign"] = 0
        return [], stats

    stats["foreign"] = len(findings)
    return findings, stats


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project", nargs="?", default=".")
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument(
        "--relocated-from", dest="relocated_from", default=None,
        help="the path this tree was COPIED FROM, for an independent audit "
             "that reads a copy. Excuses a WHOLESALE relocation only: every "
             "foreign report must claim this same root. A mixture of roots is "
             "never excused and still FAILs. Falls back to "
             "$VIBEIC_AUDIT_RELOCATED_FROM.")
    args = ap.parse_args(argv)

    root = Path(args.project).resolve()
    # Explicit flag wins; the env var is how the `--read-only` umbrella
    # declares the provenance of the copy it made.
    _reloc = args.relocated_from or os.environ.get(
        "VIBEIC_AUDIT_RELOCATED_FROM") or None
    findings, stats = audit(root, Path(_reloc) if _reloc else None)

    # DENOMINATOR FIRST, on its own line. "0 foreign reports" over a tree with
    # no reports in it is not the same fact as "0 foreign reports" over 303, and
    # a verdict line carrying the count would be comparing differently on every
    # tree (the lesson from waveform_artifact_hygiene_check in v1.9.0).
    print(f"examined {stats['reports']} runner report(s) under {str(root)!r}: "
          f"{stats['attributed']} attributed, {stats['unattributed']} "
          f"unattributed, {stats['undecidable']} undecidable")

    # The relocation is DISCLOSED, never silent: a reader of this output has to
    # be able to see that reports naming another path were accepted, and on
    # whose word. A flag whose effect is invisible is a waiver.
    if stats.get("relocated"):
        print(f"  RELOCATED  {stats['relocated']} report(s) claim "
              f"{_reloc!r} and this tree was declared a copy of it; the "
              f"relocation is WHOLESALE (1 foreign root), so they are not "
              f"foreign. A mixture of roots would not have been excused.")
    elif _reloc and stats.get("foreign_roots", 0) > 0:
        # Asked for, and refused. Say why, or the caller reads the FAIL as the
        # flag not having been passed.
        print(f"  NOT RELOCATED  --relocated-from {_reloc!r} does NOT excuse "
              f"these {stats['foreign_roots']} distinct foreign root(s): a "
              f"relocation is one tree moved, and this is a mixture. Nothing "
              f"was excused.")

    for f in findings:
        print(f"  FOREIGN  {f['report']}")
        print(f"      claims project : {f['claims_project']}")
        print(f"      sits in        : {f['sits_in']}")
        print(f"      carries verdict: {f['verdict']}")

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"findings": findings, "stats": stats,
                        "relocated_from": _reloc,
                        "verdict": "FAIL" if findings else "PASS"},
                       indent=2) + "\n", encoding="utf-8")

    if stats["reports"] == 0:
        print("VACUOUS_PASS — no runner report was found, so nothing was "
              "checked. This is not a clean result.", file=sys.stderr)
        return 2

    print(f"{'FAIL' if findings else 'PASS'} — "
          f"{len(findings)} report(s) belong to another project")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
