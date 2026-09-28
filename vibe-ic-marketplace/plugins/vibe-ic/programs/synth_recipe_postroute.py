#!/usr/bin/env python3
"""Elect a PDK synthesis recipe from matched, signed-off post-route A/B rows.

The default recipe remains in the runner when this ledger has no qualifying
rows.  An empty ledger is NOT_MEASURED, never evidence that a variant won.
This program consumes normalized measurements; it does not parse tool prose.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

BASELINE = "fanout_buffer"
VARIANTS = {"abc_alt_buffer", "abc_no_buffer"}
IDENTITY = ("pdk", "design", "source_sha256", "liberty_sha256",
            "constraints_sha256", "image_digest", "fanout_cap")


def _valid(row: Mapping[str, Any]) -> bool:
    if any(not isinstance(row.get(k), str) or not row[k]
           for k in IDENTITY if k != "fanout_cap"):
        return False
    if (isinstance(row.get("fanout_cap"), bool)
            or not isinstance(row.get("fanout_cap"), int)
            or row["fanout_cap"] <= 0):
        return False
    if row.get("drc") != "PASS" or row.get("lvs") != "PASS":
        return False
    if row.get("max_fanout_violations") != 0:
        return False
    for key in ("area_um2", "postroute_slack_ns"):
        value = row.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if not math.isfinite(value):
            return False
    return row["area_um2"] > 0


def select(pdk: str, rows: Sequence[Mapping[str, Any]],
           *, fanout_cap: int = 0) -> dict[str, Any]:
    """Require two distinct matched designs with no sign-off regression.

    A variant must be no worse in BOTH post-route area and slack on each
    design, and strictly improve at least one metric. No full-adder mapping is
    elected here: a mapped multi-output cell needs its own working actuator,
    equivalence proof and measured post-route pair before it can be a variant.
    """
    valid = [r for r in rows if isinstance(r, Mapping) and _valid(r)
             and r.get("pdk") == pdk and r.get("fanout_cap") == fanout_cap]
    for variant in sorted(VARIANTS):
        pairs: dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]] = {}
        for candidate in (r for r in valid if r.get("recipe") == variant):
            matched = [r for r in valid if r.get("recipe") == BASELINE
                       and all(r[k] == candidate[k] for k in IDENTITY)]
            if len(matched) != 1:
                continue
            base = matched[0]
            if (candidate["area_um2"] > base["area_um2"] or
                    candidate["postroute_slack_ns"] < base["postroute_slack_ns"]):
                continue
            if (candidate["area_um2"] == base["area_um2"] and
                    candidate["postroute_slack_ns"] == base["postroute_slack_ns"]):
                continue
            pairs[candidate["design"]] = base, candidate
        if (len(pairs) >= 2 and
                len({candidate["source_sha256"] for _, candidate in pairs.values()}) >= 2):
            return {"verdict": "PASS", "recipe": variant,
                    "pdk": pdk, "designs": sorted(pairs),
                    "evidence": "matched_postroute_area_slack_drc_lvs_fanout"}
    return {"verdict": "NOT_MEASURED", "recipe": BASELINE,
            "pdk": pdk, "reason": "NO_TWO_MATCHED_POSTROUTE_AB_DESIGNS"}


def load(path: Path) -> tuple[list[dict[str, Any]], str]:
    """Read a versioned measurement ledger; malformed input is disclosed."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return [], f"LEDGER_UNREADABLE:{type(exc).__name__}"
    if not isinstance(data, dict) or data.get("schema") != "vibeic.synth.postroute.v1":
        return [], "LEDGER_SCHEMA_INVALID"
    rows = data.get("measurements")
    if not isinstance(rows, list):
        return [], "LEDGER_ROWS_INVALID"
    return rows, ""
