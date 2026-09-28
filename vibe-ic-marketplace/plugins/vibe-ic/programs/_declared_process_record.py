#!/usr/bin/env python3
"""`reports/phase3/sta/declared_process_sta.json`: one row per (declared PVT
corner, check) of the post-route declared-corner sweep, written every time.

Each row says what is TRUE of that row, independently of the others:

  * MEASURED  -- the native report carries a finite slack for it; the slack,
                 the files it was timed on and their sha256 are recorded. A
                 sweep that is later REFUSED as a whole (an incomplete
                 annotation census, a missing sibling measurement) does not
                 un-measure it: the row stays MEASURED with `promoted: false`,
                 and the refusal is recorded beside it at the top level.
  * NOT_MEASURED -- with THIS row's reason: no section for it, no finite
                 slack in its section, or the sweep never ran (and why).

Review wave 5 (N5, integrity MAJOR): the first version wrote every row
NOT_MEASURED with the sweep-level reason on any refusal, erasing slacks --
including negative ones -- that the run had actually measured.

chip-agnostic: corner names and file paths come from the caller.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

SCHEMA = "vibe-ic/declared-process-sta/2"
RECORD_REL = "reports/phase3/sta/declared_process_sta.json"
ROLES = ("setup", "hold")


def build(required: Iterable[str], *, status: str, reason: Optional[str],
          report: Optional[str],
          values: Optional[Mapping[Tuple[str, str], float]] = None,
          row_reasons: Optional[Mapping[Tuple[str, str], str]] = None,
          census: Optional[Mapping[str, str]] = None,
          sources: Optional[Mapping[str, Mapping[str, Any]]] = None,
          promoted: bool = False,
          attempt_report: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """The record. `values` / `row_reasons` are keyed `(CORNER, 'SETUP'|'HOLD')`.

    `attempt_report` (review wave 7): when the sweep was NOT promoted, the
    `.attempt-*` report its MEASURED rows were read from -- path, sha256, the
    population beside it and any failed invocation's own log -- so every
    slack in a refused record can be traced to the bytes it came from.
    `report` stays the promoted basis only."""
    values = values or {}
    row_reasons = row_reasons or {}
    rows: Dict[str, Dict[str, Any]] = {}
    for c in required:
        rows[c] = {}
        for role in ROLES:
            key = (c, role.upper())
            v = values.get(key)
            measured = v is not None and math.isfinite(v)
            row: Dict[str, Any] = {
                "status": "MEASURED" if measured else "NOT_MEASURED",
                "wns_ns": v if measured else None,
                "promoted": bool(promoted and measured),
                "reason": None if measured else (row_reasons.get(key) or reason),
            }
            if census and c in census:
                row["annotation_census"] = census[c]
            row.update(dict((sources or {}).get(c) or {}))
            rows[c][role] = row
    out = {"schema": SCHEMA, "status": status, "reason": reason,
           "required_corners": list(required), "report": report,
           "corners": rows}
    if attempt_report is not None:
        out["attempt_report"] = dict(attempt_report)
    return out


def write(project: Path, record: Mapping[str, Any]) -> Path:
    import _atomic_artefact as _aa  # noqa: PLC0415
    path = Path(project) / RECORD_REL
    _aa.write_json(path, dict(record))
    return path
