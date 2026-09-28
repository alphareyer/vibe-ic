#!/usr/bin/env python3
"""Bounded SAT search over the *unproven* cells of a Yosys equivalence miter.

This is a counterexample search, never an unbounded sequential proof. The
supervising LEC runner must use its stall watchdog for the second Yosys run.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

SETTING = Path(__file__).resolve().parent.parent / "flow/lec_counterexample_search.json"
METHOD = "Yosys equiv_miter -trigger -cmp -undef; sat -prove trigger 0"
INITIAL_STATE_POLICY = ("Yosys -set-init-zero: unspecified gold/gate state "
                        "registers start at the same zero value; declared init "
                        "attributes are preserved")
_CELL = re.compile(r"^\s*cell\s+(\S+)\s+", re.M)
_INPUT = re.compile(r"^\s*wire\s+(?:width\s+\d+\s+)?input\s+\d+\s+\\?(\S+)", re.M)
_STATE = re.compile(r"(?:^\$|^\$_)(?:[A-Z]*DFF|[A-Z]*LATCH|DFFE|SDFF|ADFF|DLATCH|ALDFF|FF)", re.I)
_SUCCESS = "SAT proof finished - no model found: SUCCESS!"
_COUNTEREXAMPLE = "SAT proof finished - model found: FAIL!"


def setting() -> Dict[str, Any]:
    """The bound is declared in a file, never silently defaulted in code."""
    doc = json.loads(SETTING.read_text(encoding="utf-8"))
    if int(doc["bound_cycles"]) <= 0 or int(doc["sat_timeout_seconds"]) <= 0:
        raise ValueError("LEC counterexample search setting must be positive")
    return doc


def script(equiv_il: str, flat_il: str, trace_json: str, bound: int,
           timeout: int) -> str:
    if bound <= 0 or timeout <= 0:
        raise ValueError("SAT bound and timeout must be positive")
    # equiv_miter selects the remaining $equiv cells; already-proven points
    # are not weakened or reclassified by this search.
    return (f"read_rtlil {equiv_il}\n"
            "equiv_miter -trigger -cmp -undef lec_cex_miter\n"
            "hierarchy -top lec_cex_miter\n"
            "flatten\n"
            f"write_rtlil {flat_il}\n"
            f"sat -seq {bound} -timeout {timeout} -set-init-zero -prove trigger 0 "
            f"-show-inputs -show-outputs -dump_json {trace_json}\n")


def _trace(path: Path, flat_text: str) -> List[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    waves = {str(s.get("name")): str(s.get("wave"))
             for s in raw.get("signal", []) if isinstance(s, dict)}
    inputs = set(_INPUT.findall(flat_text))
    length = max((len(v) for v in waves.values()), default=0)
    return [{"cycle": i + 1,
             "inputs": {n: waves[n][i] for n in sorted(inputs)
                        if n in waves and i < len(waves[n])},
             "mismatched_points": [n[4:] for n, v in sorted(waves.items())
                                   if n.startswith("cmp_") and i < len(v)
                                   and v[i] == "1"]}
            for i in range(length)]


def interpret(log: str, flat_il: Path, trace_json: Path, *, bound: int,
              bound_source: str, point_names: List[str], tool_version: str,
              run_identity: str, reason: str = "") -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "method": METHOD, "bound_cycles": bound,
        "bound_source": bound_source, "point_names": sorted(set(point_names)),
        "tool": "yosys", "tool_version": tool_version,
        "run_identity": run_identity,
        "initial_state_policy": INITIAL_STATE_POLICY,
    }
    try:
        flat = flat_il.read_text(encoding="utf-8")
    except OSError as exc:
        return {**base, "result": "NOT_RUN", "completeness": "UNKNOWN",
                "reason": reason or f"flattened SAT miter unavailable: {exc}"}
    cells = _CELL.findall(flat)
    stateful = any(_STATE.match(cell) for cell in cells)
    completeness = "BOUNDED" if stateful else "COMPLETE"
    if _COUNTEREXAMPLE in log:
        try:
            trace = _trace(trace_json, flat)
        except (OSError, ValueError, TypeError) as exc:
            return {**base, "result": "NOT_RUN", "completeness": completeness,
                    "reason": f"SAT reported a model but its trace is unavailable: {exc}"}
        if not trace:
            return {**base, "result": "NOT_RUN", "completeness": completeness,
                    "reason": "SAT reported a model with an empty trace"}
        return {**base, "result": "COUNTEREXAMPLE", "completeness": completeness,
                "trace": trace}
    if _SUCCESS in log:
        return {**base, "result": "NONE_FOUND", "completeness": completeness,
                "reason": ("no counterexample in the complete combinational SAT "
                           "problem" if not stateful else
                           f"no counterexample within {bound} cycles under the "
                           "recorded initial-state policy")}
    return {**base, "result": "NOT_RUN", "completeness": completeness,
            "reason": reason or "SAT did not finish with a decisive result"}


def not_run(reason: str, point_names: List[str], *, run_identity: str = "") -> Dict[str, Any]:
    try:
        cfg = setting()
        bound, source = cfg["bound_cycles"], cfg["source"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        bound, source = None, f"setting unavailable: {exc}"
    return {"result": "NOT_RUN", "reason": reason, "method": METHOD,
            "bound_cycles": bound, "bound_source": source,
            "completeness": "UNKNOWN", "point_names": sorted(set(point_names)),
            "tool": "yosys", "tool_version": "unknown",
            "run_identity": run_identity,
            "initial_state_policy": INITIAL_STATE_POLICY}


def _valid_point_names(names: Any) -> bool:
    return (isinstance(names, list) and bool(names)
            and all(isinstance(name, str) and bool(name.strip())
                    for name in names))


def residual_search_evidence_error(search: Dict[str, Any],
                                   point_names: List[str]) -> str:
    """Name missing provenance before a residual is classified NOT_PROVEN."""
    outcome = search.get("result")
    if outcome not in ("NOT_RUN", "NONE_FOUND"):
        return "counterexample search has no recognized residual outcome"
    reason = search.get("reason")
    if (not isinstance(reason, str) or not reason.strip()
            or reason.strip().lower() in
            ("unknown", "not run", "no counterexample found")):
        return f"{outcome} has no concrete producer reason"
    if not isinstance(search.get("method"), str) or not search["method"].strip():
        return f"{outcome} has no search method"
    bound = search.get("bound_cycles")
    if isinstance(bound, bool) or not isinstance(bound, int) or bound <= 0:
        return f"{outcome} has no positive search bound"
    if not isinstance(search.get("bound_source"), str) or not search["bound_source"].strip():
        return f"{outcome} has no bound source"
    if not isinstance(search.get("run_identity"), str) or not search["run_identity"].strip():
        return f"{outcome} has no run identity"
    if search.get("tool") != "yosys":
        return f"{outcome} has no Yosys tool identity"
    search_names = search.get("point_names")
    if not _valid_point_names(search_names):
        return f"{outcome} has invalid point_names: expected non-empty strings in a list"
    if not _valid_point_names(point_names) or sorted(search_names) != sorted(point_names):
        return f"{outcome} is not bound to the named residual points"
    if outcome == "NOT_RUN":
        if search.get("completeness") not in ("UNKNOWN", "BOUNDED", "COMPLETE"):
            return "NOT_RUN has no miter completeness record"
        if not isinstance(search.get("tool_version"), str) or not search["tool_version"].strip():
            return "NOT_RUN has no tool version field"
        if not isinstance(search.get("initial_state_policy"), str) or not search["initial_state_policy"].strip():
            return "NOT_RUN has no initial-state policy"
    else:
        if search.get("completeness") not in ("BOUNDED", "COMPLETE"):
            return "NONE_FOUND has no SAT completeness record"
        if not search.get("tool_version") or str(search["tool_version"]).lower() == "unknown":
            return "NONE_FOUND has no Yosys version"
    return ""


def complete_resolution_valid(search: Dict[str, Any], point_names: List[str]) -> bool:
    """A residual can be upgraded to PASS only by a bound, named SAT result."""
    return bool(
        search.get("result") == "NONE_FOUND"
        and search.get("completeness") == "COMPLETE"
        and isinstance(search.get("reason"), str)
        and search["reason"].strip()
        and search.get("method") and search.get("tool_version")
        and str(search.get("tool_version")).lower() != "unknown"
        and search.get("tool") == "yosys"
        and isinstance(search.get("bound_cycles"), int)
        and search["bound_cycles"] > 0
        and search.get("bound_source")
        and search.get("run_identity")
        and _valid_point_names(point_names)
        and _valid_point_names(search.get("point_names"))
        and sorted(search["point_names"]) == sorted(point_names)
    )
