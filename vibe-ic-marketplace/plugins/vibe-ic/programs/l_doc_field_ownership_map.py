#!/usr/bin/env python3
"""l_doc_field_ownership_map.py — WHICH LAYER DECLARES A FIELD OF THIS NAME.

VERDICT SEMANTICS: **REPORTS**. Exit 0 with the map; exit 2 when the project
has no readable L-doc directory (NOT_MEASURED, never an empty map presented as
an answer). Not a gate: it decides nothing, it answers a question.

THE QUESTION IT ANSWERS, AND WHY A PROGRAM HAD TO
------------------------------------------------------------------
"Should layer X carry this fact, or does another layer own it?" is the
question `skills/layer-contract-doctrine` exists for, and its own §2 says to
answer it by NAMING THE CONSUMER. Before you can name a consumer you need the
cheaper half: does any layer already declare a field for a fact of this name?
That half was answered by hand, per argument, every time — and re-derived
differently each time, which is exactly the failure the doctrine opens by
describing.

Measured on a published 28-layer Phase-1 root (vibe-ic#2132): of 30 expert
expectations filed as "the layer contract has no field for the fact",

  * 10 name a leaf that a layer DOES declare — `clocks` (L8, L9), `io` (L9),
    `endianness` (L1), `parameters` (L8, L9, L10), `architecture` (L1),
    `algorithm` (L10), `reset_strategy` (L8, L9). Those are re-pointable, not
    schema gaps;
  * every one of the 30 names a first segment (`integration.` on L9,
    `constraints.` on L19) that NO layer declares at any depth.

Both halves are facts about the corpus, both were cheap, and neither was
available without running this.

TWO BASES, REPORTED SEPARATELY, NEVER COLLAPSED
------------------------------------------------------------------
INSTANCE evidence — a layer carries the name as a KEY, at any depth, in some
emitted L-doc of this project. A name a layer carries only as a VALUE is NOT a
declaration: `alerts` inside a prose sentence is the design talking, and
admitting it would make every layer own every word.

PRODUCER evidence — some program under `programs/` WRITES a field of that name
into an L-doc. Derived by `ast` from the two shapes an emitter uses: a module
constant whose name ends `_KEY` bound to a string literal, and a literal
subscript assignment into a document (`fields["x"] = ...`, `doc["x"] = ...`).

BOTH ARE NEEDED, AND THIS IS THE MEASUREMENT THAT PROVES IT (vibe-ic#2132).
An expert expectation asked L19 for `constraints.area`. INSTANCE evidence
answers "declared by NO layer": no published L19 in the corpus carries an area
ceiling, because no design in it states one. On that answer alone the repair
looks like "add the field", and this lane began writing it.

PRODUCER evidence answers differently and correctly: `synthesis_area_budget`
already exists, in `_tapeout_declaration.py` (`SYNTHESIS_AREA_BUDGET_KEY`,
added by vibe-ic#1982) with the exact `{status: NOT_APPLICABLE, rationale}`
shape the expectation asked for, consumed by `area_total_vs_budget_check` and
carried into L19 by `l9_l19_contract_carrythrough`. The field was written and
then withdrawn on that evidence: a second producer for one fact is how two
layers come to disagree.

An INSTANCE-unowned name is therefore evidence of absence IN THE CORPUS READ,
never proof that no layer may own it. A caller that reduces either basis to a
boolean, or that reads one without the other, is misusing this report.

chip-AGNOSTIC: no design, PDK, vendor or benchmark literal. The map is derived
entirely from the documents the project itself emitted.

Usage:
    python3 l_doc_field_ownership_map.py <project_dir> [--json OUT]
                                         [--field-path integration.clocks ...]
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import _atomic_artefact as _aa  # noqa: E402

TOOL = "l_doc_field_ownership_map"

#: The same relative-path family every L-doc reader on this tree walks. A
#: layout one reader can see and another cannot is a disagreement manufactured
#: by path drift.
_L_DOC_DIRS = ("phase1/generated_docs", "generated_docs",
               "input/generated_docs")

_LAYER_RE = re.compile(r"^(l\d+)", re.IGNORECASE)


def _norm(name: str) -> str:
    """Compare field names the way an author spells them, not byte for byte.

    `inter_module_signals`, `interModuleSignals` and `Inter Module Signals`
    are one name. Nothing else is collapsed: two names that differ by a
    character that is not a separator are two names.
    """
    return re.sub(r"[^a-z0-9]+", "", str(name).lower())


def _layer_of(path: Path) -> str:
    m = _LAYER_RE.match(path.stem)
    return m.group(1).upper() if m else path.stem


def _declared_keys(node: Any, out: set) -> None:
    """Every dict KEY at any depth. Values are deliberately not collected."""
    if isinstance(node, dict):
        for k, v in node.items():
            out.add(str(k))
            _declared_keys(v, out)
    elif isinstance(node, list):
        for v in node:
            _declared_keys(v, out)


#: Document objects an emitter assigns a field into. A subscript assignment to
#: anything else is some other dictionary and is not a layer field.
_DOC_TARGETS = {"fields", "doc", "out", "layer", "l19", "l9", "payload"}


def _producer_names(programs_dir: Path) -> Dict[str, List[str]]:
    """`{field name: [producing program, ...]}`, derived with `ast`.

    NOT grep: a name inside a docstring, a comment or a log line is prose, and
    a producer map built from prose names every word anybody ever wrote down.
    Only two syntactic shapes count, and both are assignments:

        <NAME>_KEY = "field"        a module constant an emitter writes with
        fields["field"] = ...       a literal subscript into a document object

    A dynamic key (`fields[k] = ...`) is deliberately invisible. It cannot be
    resolved without running the program, and a guess here would be a
    fabricated declaration — the thing this whole module exists to refuse.
    """
    out: Dict[str, set] = {}

    def add(name: str, prog: str) -> None:
        if name and isinstance(name, str):
            out.setdefault(name, set()).add(prog)

    for path in sorted(programs_dir.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8",
                                            errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            value = node.value
            for target in node.targets:
                if (isinstance(target, ast.Name)
                        and target.id.endswith("_KEY")
                        and isinstance(value, ast.Constant)
                        and isinstance(value.value, str)):
                    add(value.value, path.name)
                elif (isinstance(target, ast.Subscript)
                      and isinstance(target.slice, ast.Constant)
                      and isinstance(target.slice.value, str)
                      and isinstance(target.value, ast.Name)
                      and target.value.id in _DOC_TARGETS):
                    add(target.slice.value, path.name)
    return {k: sorted(v) for k, v in out.items()}


def layer_declarations(project: Path) -> Dict[str, set]:
    """`{layer code: {declared field names}}` for every readable L-doc."""
    found: Dict[str, set] = {}
    for rel in _L_DOC_DIRS:
        d = project / rel
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.json")):
            try:
                blob = json.loads(p.read_text(encoding="utf-8",
                                              errors="replace"))
            except (OSError, ValueError):
                # Unreadable content is not content, and it is not an empty
                # layer either: it is named in `unreadable[]` by `build`.
                continue
            keys: set = set()
            _declared_keys(blob, keys)
            found.setdefault(_layer_of(p), set()).update(keys)
    return found


def owners_of(declarations: Dict[str, set], name: str) -> List[str]:
    """Every layer that declares a field of this name."""
    want = _norm(name)
    if not want:
        return []
    return sorted(L for L, keys in declarations.items()
                  if any(_norm(k) == want for k in keys))


def resolve_field_path(declarations: Dict[str, set], field_path: str,
                       producers: Optional[Dict[str, List[str]]] = None
                       ) -> Dict[str, Any]:
    """Per-segment ownership for one dotted field path.

    Reported per SEGMENT, never collapsed to one verdict: a path whose first
    segment is owned nowhere and whose leaf is owned by three layers is a
    different situation from one owned nowhere throughout, and a single
    boolean cannot tell a reader which repair to make.
    """
    prod = producers or {}

    def related(name: str) -> Dict[str, List[str]]:
        """Names that CONTAIN this segment as a whole word. A LEAD, not a
        verdict: `area` leads to `synthesis_area_budget`, which is the field
        an area expectation actually wants and which exact-name ownership
        cannot find, because the two names are not the same name. Reported
        separately from ownership for exactly that reason — folding it in
        would make every `area` question resolve to every area-ish field,
        which is a false CLEAN wearing an answer's clothes."""
        want = re.escape(str(name).lower())
        pat = re.compile(r"(?:^|[^a-z0-9])" + want + r"(?:[^a-z0-9]|$)")
        lay = sorted({f"{L}.{k}" for L, keys in declarations.items()
                      for k in keys if pat.search(k.lower()) and
                      _norm(k) != _norm(name)})
        pr = sorted({k for k in prod
                     if pat.search(k.lower()) and _norm(k) != _norm(name)})
        return {"in_layers": lay[:20], "written_by_programs": pr[:20]}

    def written_by(name: str) -> List[str]:
        want = _norm(name)
        hits: set = set()
        for field, progs in prod.items():
            if _norm(field) == want:
                hits.update(progs)
        return sorted(hits)

    segments = [s for s in str(field_path or "").split(".") if s]
    per = [{"segment": s,
            "layers_declaring": owners_of(declarations, s),
            "written_by": written_by(s),
            "related_names_LEAD_ONLY": related(s)}
           for s in segments]
    leaf = per[-1] if per else None
    return {
        "field_path": field_path,
        "segments": per,
        "leaf_layers_declaring": leaf["layers_declaring"] if leaf else [],
        "leaf_written_by": leaf["written_by"] if leaf else [],
        # Unowned on BOTH bases. A segment owned on either one is not a
        # schema gap, and conflating them is the error this file documents.
        "segments_unowned_on_both_bases": [
            p["segment"] for p in per
            if not p["layers_declaring"] and not p["written_by"]],
    }


def build(project: Path, field_paths: Optional[List[str]] = None
          ) -> Dict[str, Any]:
    unreadable: List[str] = []
    dirs_seen: List[str] = []
    for rel in _L_DOC_DIRS:
        d = project / rel
        if not d.is_dir():
            continue
        dirs_seen.append(rel)
        for p in sorted(d.glob("*.json")):
            try:
                json.loads(p.read_text(encoding="utf-8", errors="replace"))
            except (OSError, ValueError) as exc:
                unreadable.append(f"{p.name}: {exc.__class__.__name__}")

    declarations = layer_declarations(project)
    producers = _producer_names(_HERE)
    if not declarations:
        return {"tool": TOOL, "status": "NOT_MEASURED",
                "reason": ("no readable L-doc was found under "
                           f"{list(_L_DOC_DIRS)} — this is 'could not read "
                           "it', never 'read it and no layer declares "
                           "anything'"),
                "l_doc_dirs_present": dirs_seen,
                "unreadable": unreadable}

    rep: Dict[str, Any] = {
        "tool": TOOL,
        "status": "OK",
        "basis": (
            "TWO independent bases, reported separately and never collapsed. "
            "INSTANCE: a layer declares a name when some emitted L-doc of "
            "that layer carries it as a KEY (a VALUE is not a declaration). "
            "PRODUCER: some program writes a field of that name into a "
            "document, derived with `ast` (prose and dynamic keys are not "
            "producers). Neither is a schema file: a name unowned on the "
            "INSTANCE basis is absent from the documents READ, and one "
            "unowned on both is a lead to check `related_names_LEAD_ONLY`, "
            "not a proof that no layer may own the fact."),
        "l_doc_dirs_present": dirs_seen,
        "unreadable": unreadable,
        "layers": sorted(declarations),
        "declared_field_count": {L: len(k) for L, k in
                                 sorted(declarations.items())},
    }
    rep["producer_field_count"] = len(producers)
    if field_paths:
        rep["resolved"] = [resolve_field_path(declarations, fp, producers)
                           for fp in field_paths]
    return rep


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog=TOOL, description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project_dir", type=Path)
    ap.add_argument("--json", default=None)
    ap.add_argument("--field-path", action="append", default=None,
                    help="a dotted field path to resolve; repeatable")
    args = ap.parse_args(argv)

    project = args.project_dir.resolve()
    if not project.is_dir():
        print(f"ERROR: not a directory: {project}", file=sys.stderr)
        return 2

    rep = build(project, args.field_path)
    if args.json:
        _aa.write_json(args.json, rep)

    if rep["status"] != "OK":
        print(f"{TOOL}: NOT_MEASURED — {rep['reason']}")
        return 2

    print(f"{TOOL}: {len(rep['layers'])} layer(s) read: "
          f"{', '.join(rep['layers'])}")
    for res in rep.get("resolved", []):
        for seg in res["segments"]:
            lay = (", ".join(seg["layers_declaring"])
                   if seg["layers_declaring"] else "no layer read")
            prod = (", ".join(seg["written_by"])
                    if seg["written_by"] else "no program")
            print(f"  {res['field_path']} :: {seg['segment']} -> "
                  f"declared by {lay}; written by {prod}")
            rel = seg["related_names_LEAD_ONLY"]
            near = rel["written_by_programs"][:10]
            if not seg["layers_declaring"] and not seg["written_by"] and near:
                print(f"      LEAD (not ownership): related field name(s) "
                      f"{', '.join(near)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
