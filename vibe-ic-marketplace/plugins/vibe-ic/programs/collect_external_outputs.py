#!/usr/bin/env python3
"""collect_external_outputs.py — copy volatile external-path artifacts into the
project tree before the completion audit (#146 blocker-3).

`project_outputs_in_tree_check` FAILs when a canonical non-log file (RESULT.md /
waivers.json / reports/**/*.json|md / generated_docs/*.json) references a LIVE
artifact at a volatile external path (`/tmp`, `/var/tmp`, `/dev/shm`, `/run`) —
the guard against a tool writing a deliverable outside the project tree, which
the next /tmp-sweep destroys. Some PnR/GDS/EDA steps stage scratch under /tmp and
leave such references.

This late pass runs the SAME scanner the gate uses and, for every LIVE reference
(the file still exists at the volatile path), COPIES the artifact into a
canonical in-tree location (`collected_external/`, a TOP-LEVEL dir that is
deliberately OUTSIDE the gate's scan globs so neither the copied artifact nor the
provenance sidecar re-introduces a scanned path) and REWRITES the reference to
the project-RELATIVE in-tree path (a relative path is never volatile, so it is
location-independent — works even when the whole run lives under /tmp). The
original volatile path is kept in `collected_external/_provenance.json`.

§4.05 no-leak: a DANGLING reference (the file no longer exists) is NEVER copied
or rewritten — nothing is fabricated, and `project_outputs_in_tree_check` still
FAILs on it (a genuinely-lost deliverable is not masked). Log files (`*.log`) and
pinned-plugin-source paths are left untouched (the gate already treats them as
non-blocking). chip-AGNOSTIC — pure path structure, no chip/vendor literal.

Usage:
    python3 collect_external_outputs.py <project_dir> [--json <out>]

Exit: 0 always (best-effort producer-side collection; the audit gate is the
authoritative verdict).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import project_outputs_in_tree_check as _gate  # noqa: E402 — reuse the scanner

_PROGRAM = "collect_external_outputs"
# TOP-LEVEL (NOT under reports/) so the copied artifacts + the provenance sidecar
# fall OUTSIDE project_outputs_in_tree_check's scan globs — otherwise the sidecar,
# which records the original /tmp path, would itself be flagged (and re-collected).
_DEST_REL = Path("collected_external")


def _non_log_files(project: Path):
    seen = set()
    for pat in _gate._SCAN_GLOBS:
        for f in project.glob(pat):
            if not f.is_file() or f.name.endswith(".log"):
                continue
            if f in seen:
                continue
            seen.add(f)
            yield f


def is_external(path_str: str, project: Path) -> bool:
    """True only when `path_str` lies OUTSIDE `project`, judged on resolved real
    paths (symlinks followed), never on where either one sits.

    F16: `_PATH_RE` matches by PREFIX (`/tmp/...`), and a project that itself
    lives under /tmp makes every self-reference its reports carry match it. The
    collector took those for external outputs and copied the project into its
    own `collected_external/` -- MEASURED on a 4 KiB synthetic project under
    /tmp: ONE call left 340 files nested 169 levels deep, stopped only by
    ENAMETOOLONG, and reported `collected=1`. On spm each run grew ~245 GB.

    A path that CONTAINS the project is not external either: copying it would
    copy the collection directory into itself, which is the same recursion.
    """
    try:
        p = Path(path_str).resolve()
        root = project.resolve()
    except (OSError, ValueError, RuntimeError):
        return False                     # unresolvable — never copy it
    return not (p.is_relative_to(root) or root.is_relative_to(p))


def _live_external_paths(project: Path) -> Dict[str, List[Path]]:
    """{volatile_path -> [recording files]} for every LIVE (still-on-disk)
    volatile-path reference in a non-log canonical file that lies OUTSIDE the
    project (`is_external`). Skips pinned-plugin sources, in-project paths and
    dangling references."""
    out: Dict[str, List[Path]] = {}
    for f in _non_log_files(project):
        try:
            txt = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in _gate._PATH_RE.finditer(txt):
            p = m.group(1).rstrip(".,;:)")
            if _gate._pinned_plugin_root(p) is not None:
                continue                     # legit plugin source — never touch
            if not Path(p).exists():
                continue                     # DANGLING — leave it (gate FAILs it)
            if not is_external(p, project):
                continue                     # in-tree (or contains it) — F16
            out.setdefault(p, [])
            if f not in out[p]:
                out[p].append(f)
    return out


def _unique_dest(dest_dir: Path, orig: str) -> Path:
    """A collision-free in-tree destination for `orig`. Same basename when free;
    else a short original-path hash prefix so two `/tmp/a/x` and `/tmp/b/x` never
    overwrite each other."""
    base = Path(orig).name or "artifact"
    cand = dest_dir / base
    if not cand.exists():
        return cand
    h = hashlib.sha1(orig.encode("utf-8", "ignore")).hexdigest()[:8]
    return dest_dir / f"{h}_{base}"


def collect(project: Path) -> Tuple[int, List[Dict[str, str]]]:
    """Copy every live volatile-path artifact in-tree and rewrite its reference.
    Returns (count_copied, [{file, original, in_tree}])."""
    live = _live_external_paths(project)
    if not live:
        return 0, []
    dest_dir = project / _DEST_REL
    path_map: Dict[str, str] = {}                    # original -> in-tree rel
    collected: List[Dict[str, str]] = []
    for orig in sorted(live):
        src = Path(orig)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dst = _unique_dest(dest_dir, orig)
        try:
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)
        except (OSError, shutil.Error):
            continue                                  # unreadable — leave ref
        rel = dst.relative_to(project).as_posix()
        path_map[orig] = rel
        collected.append({"original": orig, "in_tree": rel})
    if not path_map:
        return 0, []
    # rewrite references: volatile absolute path -> project-relative in-tree path
    for f in _non_log_files(project):
        try:
            txt = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        new = txt
        for orig, rel in path_map.items():
            if orig in new:
                new = new.replace(orig, rel)
        if new != txt:
            try:
                f.write_text(new)
            except OSError:
                pass
    # provenance sidecar (in-tree path -> the original volatile path it came from)
    prov = {rel: orig for orig, rel in path_map.items()}
    try:
        (dest_dir / "_provenance.json").write_text(
            json.dumps({"_note": ("original volatile paths of artifacts copied "
                                  "in-tree by collect_external_outputs (#146)"),
                        "in_tree_to_original": prov}, indent=2) + "\n")
    except OSError:
        pass
    return len(path_map), collected


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("project_dir")
    p.add_argument("--json", default=None)
    args = p.parse_args(argv)
    project = Path(args.project_dir).resolve()
    if not project.is_dir():
        print(f"{_PROGRAM}: not a directory: {project}", file=sys.stderr)
        return 2
    n, collected = collect(project)
    res = {"program": _PROGRAM, "collected": n, "artifacts": collected}
    if args.json:
        Path(args.json).write_text(json.dumps(res, indent=2) + "\n")
    if n:
        print(f"{_PROGRAM}: copied {n} live external-storage artifact(s) "
              f"into {_DEST_REL}/ and rewrote their references")
    else:
        print(f"{_PROGRAM}: no live external-storage artifacts to collect")
    return 0


if __name__ == "__main__":
    sys.exit(main())
