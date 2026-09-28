#!/usr/bin/env python3
"""What a stream-out READ, recorded by the step that streamed it.

Step 37.3 (`gds_xor_check`) compares the shipped GDS with a fresh stream of the
same routed DEF. That reference is only a reference if it is streamed from the
SAME inputs: the same engine and recipe, the same LEF set, the same cell and
macro GDS, the same layer map and (for Magic) the same rcfile. A reference
missing one library is a confident wrong answer — 776,403 phantom differences on
run21 — so the consumer must never assemble the set from other records or from
a naming convention.

MEASURED on lane fxspm1's integration run (spm x gf180mcuD, 2026-09-28): the
consumer assembled the set from `technology_units.json`, the pad-ring IO record
and a registry glob. A core design has no IO pad ring, so that assembly was
empty on every such design and step 37.3 answered NOT_MEASURED ("the run records
no LEF set for its stream-out") for a GDS the flow had just streamed itself.

So the stream-out step writes this record, beside the GDS it wrote and keyed on
that output's name (the same function also streams the DRC re-stream's
`<top>.magic_merged.gds`, which must not overwrite it). Every input carries the
path the tool was handed, the project-relative path when it lies inside the
project (so a COPY of a run re-anchors without guessing), and its sha256. The
consumer reads it and refuses, by name, when it is absent or incomplete.

chip-AGNOSTIC: paths and digests only; no PDK, cell or design literal.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _atomic_artefact import write_json as _atomic_write_json  # noqa: E402

SCHEMA = "vibeic.stream_inputs/1"
#: The recipe environment keys that name input FILES, `;`-joined where a key
#: may carry several (the recipes split them on `;`).
FILE_KEYS = ("LEFS", "CELL_GDS", "MACRO_GDS", "LEFDEF_MAP")
ENGINES = ("magic", "klayout")


def record_path(gds_out: Path) -> Path:
    """`<dir>/<stem>.stream_inputs.json` beside the GDS the stream wrote."""
    gds_out = Path(gds_out)
    return gds_out.with_name(f"{gds_out.stem}.stream_inputs.json")


def split_paths(value: str) -> List[str]:
    """The `;`-joined list a recipe reads, empty entries dropped."""
    return [p for p in (value or "").split(";") if p.strip()]


def _entry(path: str, project_c: str, hashes: Mapping[str, str]) -> Dict[str, Any]:
    rel = None
    prefix = project_c.rstrip("/") + "/"
    if project_c and path.startswith(prefix):
        rel = path[len(prefix):]
    return {"path": path, "project_rel": rel, "sha256": hashes.get(path)}


def build(*, engine: str, output: Path, top: str, project: Path,
          project_c: str, recipe: Path, def_file: Path,
          files: Mapping[str, str], scalars: Mapping[str, str],
          rcfile: Optional[str], hashes: Mapping[str, str]) -> Dict[str, Any]:
    """The record for one stream-out invocation.

    `files` is the recipe environment exactly as the tool received it
    (container-side, `;`-joined); `project_c` is the project root as the tool
    saw it, so an input under it is recorded project-relative as well.
    `hashes` maps each tool-side path to its sha256; a path missing from it is
    recorded with `sha256: null`, which the consumer refuses.
    """
    if engine not in ENGINES:
        raise ValueError(f"unknown stream-out engine {engine!r}")
    project = Path(project)
    inputs = {k: [_entry(p, project_c, hashes) for p in split_paths(files.get(k, ""))]
              for k in FILE_KEYS}
    rec: Dict[str, Any] = {
        "schema": SCHEMA,
        "program": "phase3_one_shot_runner",
        "engine": engine,
        "output": Path(output).name,
        "top": top,
        "recipe": {"project_rel": _rel(recipe, project),
                   "sha256": _file_sha256(recipe)},
        "def": {"project_rel": _rel(def_file, project),
                "sha256": _file_sha256(def_file)},
        "inputs": inputs,
        "scalars": {k: str(v) for k, v in scalars.items()},
        "rcfile": (_entry(rcfile, project_c, hashes) if rcfile else None),
    }
    rec["unhashed"] = [e["path"] for e in all_entries(rec) if not e["sha256"]]
    return rec


def write(gds_out: Path, record: Dict[str, Any]) -> Path:
    return _atomic_write_json(record_path(gds_out), record)


def read(path: Path) -> Tuple[Optional[Dict[str, Any]], str]:
    """(record, "") or (None, why). A record that is present but not this
    schema, or names an unknown engine, is NOT a record."""
    path = Path(path)
    if not path.is_file():
        return None, "absent"
    try:
        rec = json.loads(path.read_text(errors="replace"))
    except (OSError, ValueError) as exc:
        return None, f"unreadable ({type(exc).__name__})"
    if not isinstance(rec, dict) or rec.get("schema") != SCHEMA:
        return None, f"not a {SCHEMA} record"
    if rec.get("engine") not in ENGINES:
        return None, f"names no known engine ({rec.get('engine')!r})"
    return rec, ""


def all_entries(rec: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Every input entry the record names, the rcfile included."""
    out: List[Dict[str, Any]] = []
    for k in FILE_KEYS:
        out.extend((rec.get("inputs") or {}).get(k) or [])
    if rec.get("rcfile"):
        out.append(rec["rcfile"])
    return out


def _rel(path: Path, project: Path) -> Optional[str]:
    try:
        return Path(path).resolve().relative_to(Path(project).resolve()).as_posix()
    except ValueError:
        return None


def _file_sha256(path: Path) -> Optional[str]:
    import hashlib
    try:
        h = hashlib.sha256()
        with Path(path).open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def iter_env(rec: Mapping[str, Any], resolve) -> Iterable[Tuple[str, str]]:
    """(key, `;`-joined value) for the recipe environment, each entry passed
    through `resolve(entry) -> tool-side path` (the consumer's translation)."""
    for k in FILE_KEYS:
        entries = (rec.get("inputs") or {}).get(k) or []
        yield k, ";".join(resolve(e) for e in entries)
    for k, v in (rec.get("scalars") or {}).items():
        yield k, str(v)
