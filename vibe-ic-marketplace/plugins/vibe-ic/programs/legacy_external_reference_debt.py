#!/usr/bin/env python3
"""legacy_external_reference_debt.py — decide, record and MEASURE the legacy
external-reference debt that #2158's blocking gate made visible (#2165).

WHAT THE DEBT IS
----------------
`project_outputs_in_tree_check` blocks on a reference, in a project's own
declaration files, to a path that will not exist after the run. #2158 replaced
that gate's hard list of four directories with a population DERIVED from the run
root, and the census over 6847 published run roots on one host found 659 roots
over 219 distinct paths that had always satisfied the gate's own definition and
had never been visible: a published report citing a copy of ITS OWN run root
that is gone — e.g. `d9corpus/ic/ibex` citing
`/home/<your-user>/AI_IC_design/_bench6_v100_r1/ibex/phase2/.../results.json`.

The PRODUCER defect that created them is fixed in the same change, so no NEW run
can produce one. What is left is a legacy corpus, and the ruling on #2158 was
explicit that it is recorded as DEBT with the census beside it rather than
absorbed — and that THE GATE'S RULE DOES NOT CHANGE. This program is the other
half of that ruling: it decides what each root's terminal state is, records the
ARCHIVED ones honestly, and measures what remains.

WHAT THIS PROGRAM WILL NOT DO, AND WHY IT IS SAID HERE
------------------------------------------------------
It never edits a citing file, never deletes a reference, never rewrites one to a
path that did not produce the artefact, and never writes a waiver. An annotation
is a RECORD, not an exemption: a root that is annotated STILL FAILS the gate, by
design, and `test_annotating_does_not_make_the_gate_pass` pins exactly that. A
tool that made the count go down by writing waivers would be softening the gate
through its back door, which is the shape the #2158 ruling refused.

THE THREE MODES
---------------
    --classify --census <json> [--json OUT]
        Per root and per FAMILY: RE_RUN, ARCHIVED, or SPLIT.
    --annotate <project_dir>
        For an ARCHIVED root: record every unresolvable reference, verbatim,
        with the reason, in a top-level `unresolvable_external_references.json`.
    --sweep --census <json> [--json OUT]
        Re-measure with the GATE ITSELF and report the remaining blocking count
        BY FAMILY, so the debt is measured rather than remembered.

FAMILY — DERIVED, NOT LISTED
----------------------------
A family is WHERE THE VANISHED COPY OF THIS RUN ROOT LIVED: the parent of the
relocated run root inside the reference. Every path in this population carries
the run root's own directory name as a component — that IS what #2158's
predicate tests — so the run root's relocated position is read straight off the
path, and its parent is the directory the whole family was produced under.

    project `ibex`, reference
    /home/<your-user>/AI_IC_design/_bench6_v100_r1/ibex/phase2/.../results.json
        relocated run root  .../_bench6_v100_r1/ibex
        family              /home/<your-user>/AI_IC_design/_bench6_v100_r1

THE FIRST DERIVATION WAS TOO FINE AND THE DATA SAID SO. "The first ancestor that
does not exist on this host" was implemented first and run over the real census:
109 families for 700 roots, because a still-present corpus with a vanished
subdirectory yields one family per subdirectory —
`…/benchmark-data/ic/edge_llm_accel/phase2/stage2` and
`…/edge_llm_accel/phase3/stage4` came back as two families of the same run. A
family that fine is a re-spelling of the reference, not a group to decide about.
There is still no list of families in this file; only a different derivation.

THE DECISION — DERIVED, AND ARGUED
----------------------------------
A root is RE_RUN when re-running the flow on the FIXED producer would overwrite
the citation, which needs two things and is checked for both:

  (1) every file citing an unresolvable path is one the flow REGENERATES — i.e.
      it lives under `reports/`. A citation in `RESULT.md`, `waivers.json` or
      `phase1/generated_docs/` is authored, not regenerated, so a re-run is not
      guaranteed to replace it and the claim "re-running repairs this" would be
      an assumption rather than a finding;
  (2) the root still carries `phase1/generated_docs/` — its design INPUT. With
      the input gone there is nothing to re-run FROM.

Otherwise the root is ARCHIVED: it cannot be repaired by re-running, so the
honest terminal state is a recorded, unresolvable reference.

A FAMILY is RE_RUN when all its roots are, ARCHIVED when none are, and SPLIT
when its roots disagree. SPLIT is reported as itself and never forced to one
side: a family decision that only applies to half its members is not a decision,
and rounding it would put the number this program exists to measure back out of
reach.

Exit codes:
    0  the mode ran and reported.
    2  NOT_MEASURED — the census could not be read, the project is not a
       directory, or (for --sweep) the gate's #2158 predicate is not importable
       from this checkout. Never a default, and never silent: the mode names
       what it could not read.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _atomic_artefact import write_json  # noqa: E402 — vibe-ic#1082

_PROGRAM = "legacy_external_reference_debt"

# The record lives at the TOP LEVEL, deliberately OUTSIDE
# `project_outputs_in_tree_check._SCAN_GLOBS`. It quotes the unresolvable paths
# verbatim — that is its whole point — and a record placed under `reports/`
# would be scanned as one more declaration citing them. `collect_external_
# outputs.py` puts `collected_external/` outside the globs for the same reason.
ANNOTATION_REL = "unresolvable_external_references.json"
ANNOTATION_SCHEMA = 1

RE_RUN = "RE_RUN"
ARCHIVED = "ARCHIVED"
SPLIT = "SPLIT"

# A citation the flow REGENERATES. `project_outputs_in_tree_check` scans
# RESULT.md, waivers.json, reports/**, reports/*.log and
# phase1/generated_docs/*.json; only the `reports/` ones are rewritten by a run.
_REGENERATED_PREFIX = "reports/"

# The design INPUT a re-run would start from.
_INPUT_REL = Path("phase1") / "generated_docs"


def _gate_module():
    """The gate, or None when this checkout predates #2158.

    Imported lazily and reported as an absence rather than defaulted: a sweep
    that cannot ask the gate has not measured a zero, it has not measured.
    """
    try:
        import project_outputs_in_tree_check as gate  # noqa: WPS433
    except Exception:
        return None
    return gate if hasattr(gate, "_derived_ephemeral") else None


def family_of(path_str: str, project_name: str) -> str:
    """Where the vanished copy of this run root lived.

    `project_name` appears as a component of every path in this population
    (#2158's predicate requires it). The SHALLOWEST such component is the
    relocated run root; its parent is the family. Falls back to the first
    ancestor that does not exist on this host when the name is absent, which
    can only happen for a reference this program was handed from outside the
    gate's population.
    """
    parts = Path(path_str).parts
    for i in range(1, len(parts)):
        if parts[i] == project_name:
            return str(Path(*parts[:i])) if i > 1 else parts[0]
    for i in range(2, len(parts) + 1):
        candidate = Path(*parts[:i])
        if not candidate.exists():
            return str(candidate)
    return str(Path(path_str).parent)


def classify_root(project: Path, references: Dict[str, List[str]]) -> Dict[str, Any]:
    """Decide one root. `references` is {unresolvable path -> [citing rel file]}."""
    citing: List[str] = sorted(
        {f for files in references.values() for f in files})
    authored = [f for f in citing if not f.startswith(_REGENERATED_PREFIX)]
    has_input = (project / _INPUT_REL).is_dir()
    if authored:
        decision, why = ARCHIVED, (
            f"{len(authored)} citation(s) live in a file the flow does not "
            f"regenerate ({', '.join(authored[:3])}"
            f"{', …' if len(authored) > 3 else ''}), so a re-run is not "
            f"guaranteed to replace them")
    elif not has_input:
        decision, why = ARCHIVED, (
            f"the design input {_INPUT_REL}/ is absent, so there is nothing to "
            f"re-run from")
    else:
        decision, why = RE_RUN, (
            f"every citation is under {_REGENERATED_PREFIX} and the design "
            f"input {_INPUT_REL}/ is present, so a re-run on the fixed "
            f"producer overwrites them")
    return {
        "project": str(project),
        "decision": decision,
        "reason": why,
        "families": sorted({family_of(p, project.name)
                             for p in references}),
        "reference_count": len(references),
        "citing_files": citing,
        "input_present": has_input,
    }


def decide_families(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Per family: RE_RUN / ARCHIVED / SPLIT, plus its membership."""
    fams: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        for fam in row["families"]:
            e = fams.setdefault(fam, {"family": fam, "roots": [],
                                      "decisions": {}})
            e["roots"].append(row["project"])
            e["decisions"][row["decision"]] = \
                e["decisions"].get(row["decision"], 0) + 1
    for e in fams.values():
        e["roots"] = sorted(set(e["roots"]))
        kinds = set(e["decisions"])
        e["decision"] = (kinds.pop() if len(kinds) == 1 else SPLIT)
        e["root_count"] = len(e["roots"])
    return fams


def annotate(project: Path, references: Dict[str, List[str]]) -> Dict[str, Any]:
    """Record every unresolvable reference for an ARCHIVED root.

    APPEND-ONLY AND IDEMPOTENT. An existing record's entries are merged by path
    and never dropped, so re-running cannot silently shrink the record — the
    thing a debt ledger must not do. The path is stored exactly as it was cited;
    nothing is rewritten to a location that did not produce the artefact, and no
    citing file is touched.
    """
    out = project / ANNOTATION_REL
    existing: Dict[str, Any] = {}
    if out.is_file():
        try:
            existing = json.loads(out.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    prior = {e["path"]: e for e in existing.get("references", [])
             if isinstance(e, dict) and "path" in e}

    for path_str, files in references.items():
        fam = family_of(path_str, project.name)
        entry = prior.get(path_str, {"path": path_str})
        entry["status"] = "UNRESOLVABLE"
        entry["family"] = fam
        entry["cited_in"] = sorted(set(entry.get("cited_in", [])) | set(files))
        entry["reason"] = (
            f"{fam} does not exist on this host. The run that produced this "
            f"artefact wrote it there and the location is gone, so the artefact "
            f"cannot be recovered and the reference cannot be resolved. It is "
            f"preserved verbatim because it is the only surviving record of "
            f"where the artefact was produced.")
        prior[path_str] = entry

    record = {
        "program": _PROGRAM,
        "schema": ANNOTATION_SCHEMA,
        "issue": 2165,
        "project": str(project),
        "recorded_utc": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "note": (
            "A RECORD, NOT A WAIVER. This file states that the references below "
            "are unresolvable and why. It does not exempt them: "
            "project_outputs_in_tree_check still FAILS this project, which is "
            "the intended outcome — the #2158 ruling recorded these as debt "
            "rather than absorbing them, and a tool that made the count go "
            "down by writing waivers would be softening the gate."),
        "references": [prior[k] for k in sorted(prior)],
    }
    write_json(out, record)               # atomic: never a half-written ledger
    return record


# ── census I/O ──────────────────────────────────────────────────────────────

def load_census(path: Path) -> Optional[Dict[str, Dict[str, List[str]]]]:
    """{root -> {path -> [citing rel file]}} from a census JSON, or None.

    Accepts the shipped census shape (`new_paths`: {path: [root, …]}), in which
    the citing file is not recorded; the citing files are then read back from
    the root itself by `references_of`.
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None
    out: Dict[str, Dict[str, List[str]]] = {}
    if isinstance(raw.get("roots"), dict):
        for root, refs in raw["roots"].items():
            out[root] = {p: sorted(files) for p, files in refs.items()}
        return out
    if isinstance(raw.get("new_paths"), dict):
        # THE POPULATION IS THE ROOTS WHOSE VERDICT MOVED, when the census
        # records them. `new_paths` also names roots that already failed on the
        # volatile-prefix class, so taking it wholesale silently inflates the
        # debt: over the shipped #2158 census that is 700 roots where 659
        # moved. The debt this issue records is the 659 the gate newly blocks.
        moved = {r for r, _old, _new in raw.get("moved", [])}
        for p, roots in raw["new_paths"].items():
            for root in roots:
                if moved and root not in moved:
                    continue
                out.setdefault(root, {})[p] = []
        return out
    return None


def references_of(project: Path, gate) -> Dict[str, List[str]]:
    """{unresolvable path -> [citing rel file]}, asked of the GATE's own rule.

    The rule is never re-implemented here. `_derived_ephemeral`, `_PATH_RE`,
    `_inside_project` and `_pinned_plugin_root` are the gate's; this walks the
    same globs and records WHICH file cited what, which the gate's exit code
    cannot carry.
    """
    refs: Dict[str, List[str]] = {}
    seen = set()
    for pat in gate._SCAN_GLOBS:
        for f in sorted(project.glob(pat)):
            if not f.is_file() or f.name.endswith(".log"):
                continue
            try:
                txt = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            rel = str(f.relative_to(project))
            for m in gate._PATH_RE.finditer(txt):
                seen.add(m.group(1).rstrip(".,;:)"))
            for m in gate._ANY_ABS_PATH_RE.finditer(txt):
                p = m.group(1).rstrip(".,;:)")
                if p in seen:
                    continue
                if gate._inside_project(p, project):
                    continue
                if gate._pinned_plugin_root(p) is not None:
                    continue
                if not gate._derived_ephemeral(p, project):
                    continue
                refs.setdefault(p, [])
                if rel not in refs[p]:
                    refs[p].append(rel)
    return refs


# ── modes ───────────────────────────────────────────────────────────────────

def _rows_from_census(census: Dict[str, Dict[str, List[str]]],
                      gate) -> List[Dict[str, Any]]:
    rows = []
    for root in sorted(census):
        project = Path(root)
        refs = census[root]
        if gate is not None and project.is_dir():
            live = references_of(project, gate)
            if live:
                refs = live
        if not any(refs.values()):
            refs = {p: (files or ["<citing file not recorded by the census>"])
                    for p, files in refs.items()}
        rows.append(classify_root(project, refs))
    return rows


def mode_classify(census_path: Path, out_json: Optional[Path]) -> int:
    census = load_census(census_path)
    if census is None:
        print(f"[NOT_MEASURED] {_PROGRAM}: could not read a census from "
              f"{census_path} — no root was classified. This is not an empty "
              f"census; it is an unread one.", file=sys.stderr)
        return 2
    rows = _rows_from_census(census, _gate_module())
    fams = decide_families(rows)
    by = {}
    for r in rows:
        by[r["decision"]] = by.get(r["decision"], 0) + 1
    print(f"[CLASSIFY] {_PROGRAM}: {len(rows)} root(s) over {len(fams)} "
          f"family(ies) — " + ", ".join(
              f"{k} {by[k]}" for k in sorted(by)))
    for fam in sorted(fams, key=lambda k: (-fams[k]["root_count"], k)):
        e = fams[fam]
        print(f"  {e['decision']:8} {e['root_count']:5d} root(s)  {fam}")
    res = {"program": _PROGRAM, "issue": 2165, "roots": rows,
           "families": [fams[k] for k in sorted(fams)]}
    if out_json:
        write_json(out_json, res)
    return 0


def mode_annotate(project: Path) -> int:
    if not project.is_dir():
        print(f"[NOT_MEASURED] {_PROGRAM}: {project} is not a directory",
              file=sys.stderr)
        return 2
    gate = _gate_module()
    if gate is None:
        print(f"[NOT_MEASURED] {_PROGRAM}: the #2158 predicate "
              f"`_derived_ephemeral` is not importable from this checkout, so "
              f"the references to record cannot be derived. Nothing written.",
              file=sys.stderr)
        return 2
    refs = references_of(project, gate)
    if not refs:
        print(f"[SKIP] {_PROGRAM}: {project} cites no unresolvable external "
              f"reference — nothing to record, and nothing written.")
        return 0
    record = annotate(project, refs)
    print(f"[RECORDED] {_PROGRAM}: {len(record['references'])} unresolvable "
          f"reference(s) recorded in {ANNOTATION_REL} — a record, NOT a "
          f"waiver: project_outputs_in_tree_check still FAILS this project.")
    return 0


def mode_sweep(census_path: Path, out_json: Optional[Path]) -> int:
    census = load_census(census_path)
    if census is None:
        print(f"[NOT_MEASURED] {_PROGRAM}: could not read a census from "
              f"{census_path}", file=sys.stderr)
        return 2
    gate = _gate_module()
    if gate is None:
        print(f"[NOT_MEASURED] {_PROGRAM}: this checkout has no #2158 "
              f"`_derived_ephemeral`, so the remaining debt cannot be "
              f"re-measured. The sweep reports nothing rather than reporting "
              f"a zero it did not measure.", file=sys.stderr)
        return 2
    rows = _rows_from_census(census, gate)
    fams = decide_families(rows)
    per_fam: Dict[str, Dict[str, int]] = {}
    unreadable: List[str] = []
    for row in rows:
        project = Path(row["project"])
        if not project.is_dir():
            unreadable.append(row["project"])
            continue
        still = bool(references_of(project, gate))
        annotated = (project / ANNOTATION_REL).is_file()
        for fam in row["families"]:
            e = per_fam.setdefault(fam, {"roots": 0, "still_blocking": 0,
                                         "annotated": 0})
            e["roots"] += 1
            e["still_blocking"] += int(still)
            e["annotated"] += int(annotated)
    total = sum(e["still_blocking"] for e in per_fam.values())
    # A ROOT IN TWO FAMILIES IS COUNTED IN BOTH ROWS, so the column sums to
    # family-root PAIRS and not to roots. Both numbers are printed because
    # either alone reads as the other: over the shipped census 246 of 659 roots
    # cite more than one vanished producer.
    distinct_blocking = len({r["project"] for r in rows
                             if Path(r["project"]).is_dir()
                             and references_of(Path(r["project"]), gate)})
    print(f"[SWEEP] {_PROGRAM}: remaining blocking roots by family "
          f"(annotated roots are COUNTED — a record is not a waiver):")
    for fam in sorted(per_fam, key=lambda k: (-per_fam[k]["still_blocking"], k)):
        e = per_fam[fam]
        d = fams[fam]["decision"] if fam in fams else "?"
        print(f"  {d:8} {e['still_blocking']:5d}/{e['roots']:<5d} blocking "
              f"({e['annotated']} annotated)  {fam}")
    print(f"[SWEEP] {_PROGRAM}: {total} family-root pair(s) over "
          f"{len(per_fam)} family(ies) — {distinct_blocking} DISTINCT root(s) "
          f"still blocking.")
    if unreadable:
        print(f"[NOT_MEASURED] {_PROGRAM}: {len(unreadable)} root(s) from the "
              f"census are not directories on this host and were NOT counted "
              f"either way: {', '.join(unreadable[:5])}"
              f"{' …' if len(unreadable) > 5 else ''}")
    res = {"program": _PROGRAM, "issue": 2165,
           "still_blocking_family_roots": total,
           "still_blocking_distinct_roots": distinct_blocking,
           "families": {k: dict(v, decision=fams[k]["decision"])
                        for k, v in per_fam.items()},
           "not_measured": unreadable}
    if out_json:
        write_json(out_json, res)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--classify", action="store_true")
    g.add_argument("--annotate", metavar="PROJECT_DIR")
    g.add_argument("--sweep", action="store_true")
    ap.add_argument("--census", metavar="JSON")
    ap.add_argument("--json", metavar="OUT")
    args = ap.parse_args(argv)
    out = Path(args.json) if args.json else None
    if args.annotate:
        return mode_annotate(Path(args.annotate).resolve())
    if not args.census:
        ap.error("--classify / --sweep require --census")
    census = Path(args.census)
    return (mode_classify(census, out) if args.classify
            else mode_sweep(census, out))


if __name__ == "__main__":
    sys.exit(main())
