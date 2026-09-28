#!/usr/bin/env python3
"""Bounded SAT search over the *unproven* cells of a Yosys equivalence miter.

This is a counterexample search, never an unbounded sequential proof. The
supervising LEC runner must use its stall watchdog for the second Yosys run.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

SETTING = Path(__file__).resolve().parent.parent / "flow/lec_counterexample_search.json"
METHOD = ("Yosys equiv_miter -trigger -cmp -undef; "
          "sat -enable_undef -set-def-inputs -prove trigger 0")
INITIAL_STATE_POLICY = ("Yosys -set-init-zero: unspecified gold/gate state "
                        "registers start at the same zero value; declared init "
                        "attributes are preserved. No declared reset constrains "
                        "that state, so it need not be reachable from reset")
#: The result word for a SAT model found on a STATEFUL miter. The model starts
#: from the zero state above, which need not correspond to a reachable state of
#: gold or gate: a gold up-counter reset to 0 and a gate down-counter reset to
#: 127 are equivalent from reset, yet the all-zero pair diverges in 4 cycles.
#: Such a trace is evidence to look at, never a decided mismatch.
CANDIDATE = "CANDIDATE_COUNTEREXAMPLE"
#: YOSYS'S OWN COMBINATIONAL CELL VOCABULARY, used for ONE positive fact: every
#: cell of the flattened search miter is stateless, so each SAT time step is an
#: independent evaluation and a model is a real mismatch. A cell type not in
#: this closed, tool-defined list (a flip-flop, `$mem*`, `$sr`, a Liberty cell,
#: a type a future yosys adds) makes the miter BOUNDED, whose model is only a
#: CANDIDATE. An omission can only cost the stricter verdict, never grant it.
#: `$scopeinfo` is the annotation cell `flatten` leaves; it carries no logic.
COMBINATIONAL_CELL_TYPES = frozenset("""
$_NOT_ $_BUF_ $_AND_ $_NAND_ $_OR_ $_NOR_ $_XOR_ $_XNOR_ $_ANDNOT_ $_ORNOT_
$_MUX_ $_NMUX_ $_MUX4_ $_MUX8_ $_MUX16_ $_AOI3_ $_OAI3_ $_AOI4_ $_OAI4_
$_TBUF_ $not $pos $neg $and $or $xor $xnor $reduce_and $reduce_or $reduce_xor
$reduce_xnor $reduce_bool $logic_not $logic_and $logic_or $shl $shr $sshl
$sshr $shift $shiftx $lt $le $eq $ne $eqx $nex $ge $gt $add $sub $mul $div
$mod $divfloor $modfloor $pow $mux $pmux $bmux $demux $bwmux $tribuf $lut
$sop $macc $macc_v2 $alu $lcu $fa $concat $slice $buf $equiv $scopeinfo
""".split())
_CELL = re.compile(r"^\s*cell\s+(\S+)\s+", re.M)
_PORT = r"^\s*wire\s+(?:\S+\s+)*?{kind}\s+\d+\s+\\?(\S+)\s*$"
_INPUT = re.compile(_PORT.format(kind="input"), re.M)
_OUTPUT = re.compile(_PORT.format(kind="output"), re.M)
_SUCCESS = "SAT proof finished - no model found: SUCCESS!"
_COUNTEREXAMPLE = "SAT proof finished - model found: FAIL!"
# One row of the model table `sat -show-inputs -show-outputs` prints:
# `<time|init> <signal> <dec> <hex> <bin>`. The time column is explicit, so an
# initial-state row can never shift a step, and the Bin column is the value at
# full width (Dec/Hex read `--` for wide or undefined values).
_TABLE_HEADER = re.compile(r"^\s*Time\s+Signal\s+Name\b")
_TABLE_RULE = re.compile(r"^\s*-+(?:\s+-+)*\s*$")
_TABLE_ROW = re.compile(r"^\s*(init|\d+)\s+\\?(\S+)\s+\S+\s+\S+\s+([01xXzZ]+)\s*$")


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
    # are not weakened or reclassified by this search. `-undef` builds a
    # compare in which an undefined GOLD bit is a don't-care; that compare is
    # only meaningful when SAT models undef, so `-enable_undef` goes with it.
    # MEASURED on the pinned image: gold `y=a&b` vs gate `y=a|b` gave NONE_FOUND
    # without it (the gold-0/gate-1 direction was masked) and a model with it.
    # `-set-def-inputs` keeps the primary inputs defined, so a model is a
    # concrete stimulus.
    return (f"read_rtlil {equiv_il}\n"
            "equiv_miter -trigger -cmp -undef lec_cex_miter\n"
            "hierarchy -top lec_cex_miter\n"
            "flatten\n"
            f"write_rtlil {flat_il}\n"
            f"sat -seq {bound} -timeout {timeout} -enable_undef -set-def-inputs "
            f"-set-init-zero -prove trigger 0 "
            f"-show-inputs -show-outputs -dump_json {trace_json}\n")


def _model_rows(log: str) -> List[tuple]:
    """(time, signal, bin) for every row of the model table SAT printed."""
    lines = (log or "").splitlines()
    start = next((i for i, line in enumerate(lines) if _COUNTEREXAMPLE in line), None)
    if start is None:
        return []
    header = next((i for i in range(start, len(lines))
                   if _TABLE_HEADER.match(lines[i])), None)
    if header is None:
        return []
    rows: List[tuple] = []
    for line in lines[header + 1:]:
        if _TABLE_RULE.match(line) or (not line.strip() and not rows):
            continue
        m = _TABLE_ROW.match(line)
        if not m:
            break
        rows.append((m.group(1), m.group(2), m.group(3).lower()))
    return rows


def _trace(log: str, flat_text: str) -> Tuple[List[dict], Dict[str, str]]:
    """Decode SAT's model into per-step stimulus and mismatched points.

    `equiv_miter -cmp` drives `cmp_<point>` with the EQUALITY of gold and gate
    (the trigger is the OR of the negated compares), so a point mismatches in a
    step iff its compare is 0. Point names are the compare name without the
    `cmp_` prefix, which for a bit of a multi-bit wire is `<wire>[<bit>]`.
    """
    rows = _model_rows(log)
    inputs = set(_INPUT.findall(flat_text))
    compares = {n for n in _OUTPUT.findall(flat_text) if n.startswith("cmp_")}
    steps = sorted({int(t) for t, _n, _v in rows if t != "init"})
    trace = [{"cycle": step,
              "inputs": {n: v for t, n, v in rows
                         if t == str(step) and n in inputs},
              "mismatched_points": sorted(n[len("cmp_"):] for t, n, v in rows
                                          if t == str(step) and n in compares
                                          and v == "0")}
             for step in steps]
    initial = {n: v for t, n, v in rows if t == "init"}
    return trace, initial


def search_completeness(flat_text: str) -> str:
    """COMPLETE only on positive evidence that the search miter holds no state."""
    cells = _CELL.findall(flat_text or "")
    return ("COMPLETE" if all(cell in COMBINATIONAL_CELL_TYPES for cell in cells)
            else "BOUNDED")


def interpret(log: str, flat_il: Path, trace_json: Path, *, bound: int,
              bound_source: str, point_names: List[str], tool_version: str,
              run_identity: str, reason: str = "") -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "method": METHOD, "bound_cycles": bound,
        "bound_source": bound_source, "point_names": sorted(set(point_names)),
        "tool": "yosys", "tool_version": tool_version,
        "run_identity": run_identity,
        "initial_state_policy": INITIAL_STATE_POLICY,
        "trace_json": str(trace_json),
    }
    try:
        flat = flat_il.read_text(encoding="utf-8")
    except OSError as exc:
        return {**base, "result": "NOT_RUN", "completeness": "UNKNOWN",
                "reason": reason or f"flattened SAT miter unavailable: {exc}"}
    completeness = search_completeness(flat)
    stateful = completeness != "COMPLETE"
    if _COUNTEREXAMPLE in log:
        trace, initial = _trace(log, flat)
        if not trace:
            return {**base, "result": "NOT_RUN", "completeness": completeness,
                    "reason": "SAT reported a model but printed no model table"}
        if not any(step["mismatched_points"] for step in trace):
            return {**base, "result": "NOT_RUN", "completeness": completeness,
                    "reason": ("SAT reported a model in which no compare "
                               "output is 0; the trace does not show a mismatch")}
        if stateful:
            return {**base, "result": CANDIDATE, "completeness": completeness,
                    "trace": trace, "initial_state": initial,
                    "reason": (f"SAT found a model within {bound} cycles of a "
                               "stateful miter, starting from the unconstrained "
                               "zero state; that state is not constrained by "
                               "any declared reset and need not be reachable, "
                               "so the trace is a candidate, not a decided "
                               "mismatch")}
        return {**base, "result": "COUNTEREXAMPLE", "completeness": completeness,
                "trace": trace,
                "reason": ("SAT found a model of the complete combinational "
                           "miter: the listed points differ for the listed "
                           "inputs")}
    if _SUCCESS in log:
        return {**base, "result": "NONE_FOUND", "completeness": completeness,
                "reason": ("no counterexample in the complete combinational SAT "
                           "problem" if not stateful else
                           f"no counterexample within {bound} cycles under the "
                           "recorded initial-state policy")}
    return {**base, "result": "NOT_RUN", "completeness": completeness,
            "reason": reason or "SAT did not finish with a decisive result"}


def decides_non_equivalence(search: Any) -> bool:
    """Only a model of a COMPLETE (stateless) miter decides NON_EQUIVALENT.

    A model of a stateful miter starts from a state no declared reset
    constrains, so it cannot decide a FAIL by itself, whatever word a record
    gives it."""
    return bool(
        isinstance(search, dict)
        and search.get("result") == "COUNTEREXAMPLE"
        and search.get("completeness") == "COMPLETE"
        and isinstance(search.get("trace"), list)
        and any(isinstance(step, dict) and step.get("mismatched_points")
                for step in search["trace"]))


def is_model_candidate(search: Any) -> bool:
    """A SAT model that does not decide: any model word on a non-COMPLETE miter."""
    return bool(isinstance(search, dict)
                and search.get("result") in ("COUNTEREXAMPLE", CANDIDATE)
                and not decides_non_equivalence(search))


def mismatched_points(search: Any) -> List[str]:
    """Every point the search trace shows differing, in name order."""
    trace = search.get("trace") if isinstance(search, dict) else None
    names = set()
    for step in trace if isinstance(trace, list) else []:
        if isinstance(step, dict) and isinstance(step.get("mismatched_points"), list):
            names.update(str(n) for n in step["mismatched_points"])
    return sorted(names)


def search_summary(search: Dict[str, Any]) -> str:
    """One sentence naming the search outcome, its bound and its reason."""
    outcome = search.get("result", "NOT_RUN")
    if outcome == "NOT_RUN":
        return f"counterexample search NOT RUN: {search.get('reason', 'no reason recorded')}"
    detail = (f"{search.get('method', 'unknown method')}, "
              f"K={search.get('bound_cycles', 'unknown')} cycles "
              f"({search.get('completeness', 'UNKNOWN')} miter): {outcome} "
              f"({search.get('reason', 'no reason recorded')})")
    trace = search.get("trace") if isinstance(search.get("trace"), list) else []
    first = next((step for step in trace if isinstance(step, dict)
                  and step.get("mismatched_points")), None)
    if outcome in ("COUNTEREXAMPLE", CANDIDATE) and first:
        detail += (f"; first mismatch at cycle {first.get('cycle')}: "
                   f"{first.get('mismatched_points')} for inputs "
                   f"{first.get('inputs')} (full trace in the record)")
    return detail


def point_names_error(names: Any) -> str:
    """Why a report's `unproven_point_names` field is malformed, or "".

    Absent (None) and an empty list both mean "no names recorded"; anything
    else must be a list of non-empty strings."""
    if names is None or names == []:
        return ""
    if not _valid_point_names(names):
        return ("unproven_point_names is malformed: expected a list of "
                f"non-empty strings, got {names!r}")
    return ""


def decide_residual(original_verdict: str, search: Dict[str, Any],
                    point_names: List[str], unproven_points: int,
                    miter_stateless: bool) -> Dict[str, Any]:
    """The producer's verdict for a proof that left `unproven_points` open.

    Shared by the pre-layout (lec_run) and post-layout (phase 3) producers so
    the two can never decide the same record differently."""
    summary = search_summary(search)
    if decides_non_equivalence(search):
        points = mismatched_points(search)
        return {"verdict": "NON_EQUIVALENT",
                "non_equivalent_points": len(points) or unproven_points,
                "explanation": (f"NON_EQUIVALENT: {len(points)} point(s) "
                                f"{points} differ in the complete "
                                f"combinational miter; {summary}.")}
    if str(original_verdict or "").upper() == "RUN_ERROR":
        # A tool/run error remains an error. FAIL and NON_EQUIVALENT are not
        # independent witnesses: the legacy equiv_status parser can assign
        # FAIL solely because a point remains unproven. Only the complete
        # counterexample above (or a separately proven reset replay) may
        # decide a residual mismatch.
        return {"verdict": original_verdict, "explanation": None}
    if not _valid_point_names(point_names) or len(point_names) != unproven_points:
        return {"verdict": "RUN_ERROR", "names_unbound": True,
                "explanation": (f"RUN_ERROR: equiv_status left {unproven_points} "
                                f"point(s) unproven but named {len(point_names or [])} "
                                f"({sorted(point_names or [])}); the residual cannot "
                                f"be bound to named points; {summary}.")}
    if complete_resolution_valid(search, point_names):
        return {"verdict": "PROVEN_EQUIVALENT",
                "explanation": (f"PROVEN_EQUIVALENT: the {unproven_points} point(s) "
                                f"equiv_status left open were discharged by a "
                                f"complete combinational SAT problem; {summary}.")}
    if miter_stateless:
        return {"verdict": "RUN_ERROR",
                "explanation": ("Combinational residual requires a complete SAT "
                                f"disposition; {summary}.")}
    return {"verdict": "NOT_PROVEN",
            "explanation": (f"NOT_PROVEN: {unproven_points} point(s) unproven "
                            f"{sorted(point_names)}; {summary}.")}


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
    if outcome == "COUNTEREXAMPLE" and search.get("completeness") != "COMPLETE":
        return ("COUNTEREXAMPLE is recorded on a miter that is not COMPLETE; a "
                "model of a stateful miter is a candidate, not a decided mismatch")
    if outcome not in ("NOT_RUN", "NONE_FOUND", CANDIDATE):
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
    elif outcome == CANDIDATE:
        if search.get("completeness") != "BOUNDED":
            return f"{CANDIDATE} is not recorded on a BOUNDED miter"
        if not search.get("tool_version") or str(search["tool_version"]).lower() == "unknown":
            return f"{CANDIDATE} has no Yosys version"
        if not isinstance(search.get("initial_state_policy"), str) or not search["initial_state_policy"].strip():
            return f"{CANDIDATE} has no initial-state policy"
        if not mismatched_points(search):
            return f"{CANDIDATE} has no trace step with a mismatched point"
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
