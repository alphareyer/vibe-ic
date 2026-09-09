#!/usr/bin/env python3
"""expert_decision_table.py — a decision table is DATED, and a stale one is refused.

THE GAP THIS CLOSES (vibe-ic#2150 item 6)
=========================================
A decision table says, per expectation, where a fact belongs: re-point to this
layer and this field, or withdraw it for this reason. It is authored by reading
ONE Phase-1 root and is then applied to ANOTHER, and nothing recorded which
root it was computed on.

MEASURED, applying vibe-ic#2132's 30-row table to the opentitan_aes artefact:

  * TWO of its OWNER cells name a leaf that artefact does not declare
    (`L9.io`, `L8/L9 reset_strategy`); the artefact declares `cpu_interrupts`,
    `resets` and `reset_domains`.
  * THREE name fields introduced by later work (#1982's
    `synthesis_area_budget`, #2118's `deliverable` / `macro_area_um`). The
    artefact was emitted by plugin v1.17.38; #2118 landed afterwards. Those
    fields COULD NOT exist in it.

None of that is a defect in the table. It is a table computed on a NEWER root
being applied to an OLDER artefact, and the only reason it surfaced at all is
that #2127's guard refused the paths. Without the guard the five would have
been applied silently and read as decisions about this design.

So a table carries a STAMP naming the root it was computed on, and this module
refuses a table the stamp says cannot describe the artefact in hand. The
refusal NAMES the mismatch: "stale" without saying stale against what is the
same unactionable verdict this whole track exists to replace.

WHAT "STALE" MEANS HERE, precisely, because a vague word would be applied
inconsistently:

  * `ROOT_MISMATCH`  — the table's `phase1_root_digest` differs from the
    artefact's. The table describes a DIFFERENT set of layer documents. This is
    a refusal, not a warning: every OWNER cell in it is a claim about fields
    that may not exist here.
  * `TABLE_OLDER`    — same root digest is unavailable, but the table's
    `plugin_version` is BELOW the artefact's. The table predates the emitter
    that wrote these layers, so it cannot name fields the emitter added.
  * `TABLE_NEWER`    — the table's `plugin_version` is ABOVE the artefact's.
    This is the #2132 case: the table names fields the artefact is too old to
    have. Also a refusal, and a DIFFERENT one, because the remedy differs —
    re-run Phase 1, rather than recompute the table.
  * `USABLE`         — the digests match.

The asymmetry is deliberate and is the measured case: an older artefact judged
by a newer table is exactly what happened, and calling it "stale" would point
the reader at recomputing the table when the artefact is what is behind.

chip-AGNOSTIC. §4.05: reads the design's own generated L-docs and the table.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import phase1_expert_parse_track as _t  # noqa: E402

PROGRAM = "expert_decision_table"
VERSION = "1.0.0"
SCHEMA = "vibeic.expert-decision-table.v1"

USABLE = "USABLE"
ROOT_MISMATCH = "ROOT_MISMATCH"
TABLE_OLDER = "TABLE_OLDER"
TABLE_NEWER = "TABLE_NEWER"
NOT_STAMPED = "NOT_STAMPED"
NO_ARTEFACT = "NO_ARTEFACT"

#: Every state in which a table must NOT be applied. An ALLOW-LIST of one is
#: deliberate: a new state added later inherits refusal, never acceptance.
APPLICABLE = frozenset({USABLE})


def phase1_root_digest(project: Path) -> Optional[str]:
    """A reproducible digest of the L-doc set this table would judge.

    Over NAMES AND CONTENT, both: a root with the same files and different
    content is a different root, and so is one with an extra layer. Sorted, so
    it does not depend on directory order. Returns None when there are no
    layers to digest — "I could not look" is not "I looked and it was empty".
    """
    files = _t.emitted_layer_files(project)
    if not files:
        return None
    h = hashlib.sha256()
    for f in sorted(files, key=lambda p: p.name):
        h.update(f.name.encode())
        h.update(b"\0")
        h.update(hashlib.sha256(f.read_bytes()).hexdigest().encode())
        h.update(b"\0")
    return h.hexdigest()


def _version_tuple(v: Any) -> Optional[Tuple[int, ...]]:
    try:
        return tuple(int(x) for x in str(v).strip().lstrip("v").split("."))
    except (AttributeError, ValueError):
        return None


def artefact_plugin_version(project: Path) -> Optional[str]:
    """The plugin version that EMITTED these layers, from the layers.

    Read from the documents rather than from the running plugin: the question
    is what wrote this artefact, and the running version answers a different
    one. The most common value across the layers wins, so one hand-staged file
    cannot re-date the whole root.
    """
    seen: Dict[str, int] = {}
    for f in _t.emitted_layer_files(project):
        try:
            blob = json.loads(f.read_text(errors="replace"))
        except (OSError, ValueError):
            continue
        gen = blob.get("_generator") if isinstance(blob, dict) else None
        v = gen.get("plugin_version") if isinstance(gen, dict) else None
        if isinstance(v, str) and v.strip():
            seen[v] = seen.get(v, 0) + 1
    return max(seen, key=seen.get) if seen else None


def stamp_for(project: Path) -> Dict[str, Any]:
    """The stamp a table computed on THIS project must carry."""
    return {
        "phase1_root_digest": phase1_root_digest(project),
        "plugin_version": artefact_plugin_version(project),
        "layer_count": len(_t.emitted_layer_files(project)),
    }


def applicability(project: Path, table: Any) -> Dict[str, Any]:
    """Can this table be applied to this artefact? Names the mismatch."""
    art = stamp_for(project)
    out: Dict[str, Any] = {"artefact_stamp": art, "table_stamp": None,
                           "state": NOT_STAMPED, "reason": ""}
    if art["phase1_root_digest"] is None:
        out["state"] = NO_ARTEFACT
        out["reason"] = ("the project has no readable L-doc set, so there is "
                         "nothing for a table to judge and nothing to date it "
                         "against. NOT a usable table and NOT an empty one")
        return out
    stamp = table.get("stamp") if isinstance(table, dict) else None
    if not isinstance(stamp, dict) or not stamp.get("phase1_root_digest"):
        out["reason"] = (
            "the table carries no `stamp.phase1_root_digest`, so the Phase-1 "
            "root it was computed on is unknown. It is REFUSED rather than "
            "applied hopefully: an undated table that happens to fit is "
            "indistinguishable from one that does not, and the difference is "
            "every OWNER cell in it")
        return out
    out["table_stamp"] = stamp
    if stamp["phase1_root_digest"] == art["phase1_root_digest"]:
        out["state"] = USABLE
        out["reason"] = "the table was computed on this exact L-doc set"
        return out

    tv = _version_tuple(stamp.get("plugin_version"))
    av = _version_tuple(art.get("plugin_version"))
    if tv is not None and av is not None and tv != av:
        newer = tv > av
        out["state"] = TABLE_NEWER if newer else TABLE_OLDER
        out["reason"] = (
            f"the table was computed on a Phase-1 root emitted by plugin "
            f"{stamp.get('plugin_version')}, and this artefact was emitted by "
            f"{art.get('plugin_version')} — the table is "
            f"{'NEWER' if newer else 'OLDER'} than the artefact it would "
            f"judge, so it "
            + ("names fields this artefact is too old to carry; re-run Phase 1 "
               "rather than recompute the table"
               if newer else
               "cannot name fields this artefact's emitter added; recompute "
               "the table on this root"))
        return out

    out["state"] = ROOT_MISMATCH
    out["reason"] = (
        f"the table was computed on Phase-1 root "
        f"{str(stamp['phase1_root_digest'])[:12]}… and this artefact is "
        f"{str(art['phase1_root_digest'])[:12]}…. Every OWNER cell in it is a "
        f"claim about a DIFFERENT set of layer documents")
    return out


def load(project: Path, path: Path) -> Dict[str, Any]:
    """Read a table and decide whether it may be applied. Never raises for a
    table that is merely unusable — an unusable table is a REPORTABLE state,
    not an exception the caller may swallow."""
    try:
        table = json.loads(Path(path).read_text(errors="replace"))
    except (OSError, ValueError) as exc:
        return {"state": NOT_STAMPED, "table": None, "path": str(path),
                "artefact_stamp": stamp_for(project), "table_stamp": None,
                "reason": (f"the decision table does not parse "
                           f"({exc.__class__.__name__}); unreadable content is "
                           f"not content")}
    verdict = applicability(project, table)
    verdict["table"] = table if verdict["state"] in APPLICABLE else None
    verdict["path"] = str(path)
    verdict["rows"] = len(table.get("rows") or {}) if isinstance(table, dict) else 0
    return verdict


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Stamp a decision table, or check one against a project.")
    ap.add_argument("project", type=Path)
    ap.add_argument("--stamp", action="store_true",
                    help="print the stamp a table for this project must carry")
    ap.add_argument("--check", type=Path, help="check this table")
    a = ap.parse_args(argv)
    if a.stamp:
        print(json.dumps({"schema": SCHEMA, "stamp": stamp_for(a.project)},
                         indent=2))
        return 0
    if not a.check:
        ap.error("pass --stamp or --check")
    v = load(a.project, a.check)
    print(f"{PROGRAM}: {v['state']} — {v['reason']}")
    return 0 if v["state"] in APPLICABLE else 1


if __name__ == "__main__":
    raise SystemExit(main())
