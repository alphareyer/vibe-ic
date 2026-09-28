#!/usr/bin/env python3
"""l10_coverage_goal_classify.py — a coverage GOAL is not a functional vector.

NOT A GATE. A schema / producer imported by `cpu_functional_oracle_waiver_check`
(the Step-4 functional-evidence gate); it declares no `ENFORCEMENT:` intent
because it is wired into no flow clause, like its siblings
`stated_vector_bus_oracle_gen` and `known_answer_vector_tb_gen`.

WHY THIS EXISTS — the measurement, not a theory.

MEASURED, sha256 x sky130A, FRONT DOOR, run23 on main 751bed176:

    step4_functional_evidence FAIL: only 6 of 11 declared L10 case(s)
    EXECUTED their own oracle. Not executed:
      single_block_nist_appa_abcdbcde_mnopqr [NOT_EXECUTED],
      random_message_functional_equivalence_vs_nist_go [NOT_EXECUTED],
      message_length [NOT_EXECUTED], protocol [NOT_EXECUTED],
      mode_switch [NOT_EXECUTED]

Four of those five are not vectors at all. Their expected half is an
ACCEPTANCE PERCENTAGE over a named SCOPE — the design's own L7 verification
plan states them that way, and Phase 1's L10 emitter already records the
finding in each row:

    "expected": "100% PASS"
    "coverage_scope": "1 byte / 55 bytes(single-block boundary)/ 56 bytes /
                       64 bytes / 119 bytes / 120 bytes / 1024 bytes"
    "classification_reason": "percent-PASS acceptance criterion over a
                              coverage scope"
    "kind": "coverage_goal"

Demanding that such a row "EXECUTE its own oracle" demands a thing that
cannot happen: there is no single stimulus and no single expected value to
compare. It is the same failure `_split_executable` already closed for
`verification_checklist` rows on opentitan_aes, one kind later.

THE ROW IS NEVER DROPPED AND NEVER MARKED EXECUTED. It moves to the population
that has an instrument for it — the run's own coverage arm — and gets a
verdict by NUMBER there:

    PASS          the scope binds to a dimension the run measured and the
                  achieved percentage meets the stated one
    FAIL          it binds and the achieved percentage does not meet it
    NOT_MEASURED  the scope names no dimension this flow measures; the scope
                  is quoted BY NAME in the reason, so the row is visible as
                  unmeasured rather than quietly satisfied

NOT_MEASURED IS NOT A PASS, and the Step-4 gate still refuses on it. What
changes is the ARITHMETIC: two populations, each with its own denominator,
so "6 of 11" stops mixing vectors with goals and starts reading "6 of 7
vectors executed; 0 of 4 coverage goals measured".

CLASSIFICATION IS STRUCTURAL, and cites the line it read. Two routes, both
from the input's own shape, never from prose interpretation:

  (1) the row DECLARES its kind (`kind`/`type` is a coverage token) — the
      citation is that field;
  (2) the row's expected half is an acceptance PERCENTAGE — the citation is
      the expected text itself.

A row whose expected half is a literal value is a vector and stays one, even
when its scope text happens to contain a number.

chip-AGNOSTIC: coverage vocabulary only (line / toggle / branch / statement /
condition, percentage, scope). No chip, vendor, node or SKU literal, and no
hard-coded percentage, dimension or case name.
"""
from __future__ import annotations

# --- sibling-import path (vibe-ic#2104) ------------------------------------
import os as _os                                                    # noqa: E402
import sys as _sys                                                  # noqa: E402

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
# ---------------------------------------------------------------------------

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: The two populations Step 4 must keep apart.
STATED_VECTOR = "STATED_VECTOR"
COVERAGE_GOAL = "COVERAGE_GOAL"
#: A coverage goal's verdict vocabulary. NOT_MEASURED is not a pass.
PASS = "PASS"
FAIL = "FAIL"
NOT_MEASURED = "NOT_MEASURED"

#: Kind tokens a row may DECLARE itself with. Open vocabulary: a design that
#: writes `coverage_target` means the same thing as one that writes
#: `coverage_goal`, and neither is a chip name.
_GOAL_KIND_TOKENS = frozenset({
    "coverage_goal", "coverage_goals", "coverage", "coverage_target",
    "coverage_item", "coverage_objective", "coverage_metric",
})
#: An acceptance PERCENTAGE: `100% PASS`, `>= 95 %`, `95.5%`.
_PCT_RE = re.compile(r"(?P<pct>\d{1,3}(?:\.\d+)?)\s*%")
#: Where a row may state its scope, in the order a reader should trust.
_SCOPE_KEYS = ("coverage_scope", "scope", "stimulus", "coverage_group")
#: Where a row may state its acceptance criterion.
_EXPECTED_KEYS = ("expected", "expected_result", "acceptance",
                  "acceptance_criterion")
#: The coverage DIMENSIONS this flow actually measures, and the words a
#: document uses for each. A scope that names none of them cannot be compared
#: against a number this run holds.
DIMENSION_WORDS: Dict[str, Tuple[str, ...]] = {
    "line": ("line", "lines", "statement", "statements", "code coverage"),
    "toggle": ("toggle", "toggles", "bit toggle", "signal toggle"),
    "branch": ("branch", "branches", "decision", "decisions", "condition",
               "conditions"),
    # R-0915-131. A design that states an acceptance percentage over its own
    # INSTRUCTION SET is naming a coverage dimension like any other; what was
    # missing was an INSTRUMENT, not a word. `instruction_coverage_measure`
    # is that instrument and publishes `totals.instruction`, so this entry and
    # that producer stand or fall together -- which is what
    # `set(DIMENSION_WORDS) == set(TOTALS)` in this module's deck pins. A
    # dimension listed here with nothing publishing a number for it would turn
    # a goal from NOT_MEASURED into a verdict over an empty population.
    "instruction": ("instruction", "instructions", "opcode", "opcodes",
                    "isa", "mnemonic", "mnemonics", "\u6307\u4ee4"),
}


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _first(case: dict, keys: Sequence[str]) -> Tuple[Optional[str], str]:
    """`(text, key)` for the first of `keys` the row actually states."""
    for k in keys:
        v = case.get(k)
        if v not in (None, "", [], {}):
            return _text(v), k
    return None, ""


def declared_kind(case: dict) -> Tuple[Optional[str], str]:
    """`(token, field)` when the row DECLARES its own kind, else `(None, "")`."""
    for k in ("kind", "type", "case_kind", "row_kind"):
        v = case.get(k)
        if v in (None, ""):
            continue
        return re.sub(r"[^a-z0-9]+", "_", _text(v).lower()).strip("_"), k
    return None, ""


def stated_acceptance_percentage(case: dict
                                 ) -> Tuple[Optional[float], str]:
    """`(percentage, citation)` when the expected half is an acceptance
    PERCENTAGE, else `(None, why)`.

    A percentage found anywhere but the EXPECTED half decides nothing: a
    stimulus that says "0-2KB, 100 samples" is not an acceptance criterion."""
    text, key = _first(case, _EXPECTED_KEYS)
    if text is None:
        return None, "the row states no expected value"
    m = _PCT_RE.search(text)
    if not m:
        return None, (f"the row's {key} {text[:48]!r} states no acceptance "
                      f"percentage")
    try:
        return float(m.group("pct")), f"{key}={text.strip()[:80]!r}"
    except ValueError:                                  # pragma: no cover
        return None, f"the row's {key} percentage is not a number"


def coverage_scope(case: dict) -> Tuple[Optional[str], str]:
    """`(scope_text, citation)` — the population the goal is stated over."""
    text, key = _first(case, _SCOPE_KEYS)
    if text is None:
        return None, "the row names no scope"
    return text, key


def classify(case: dict) -> Tuple[str, str]:
    """`(population, evidence)` for ONE declared L10 row.

    STRUCTURAL and CITED. The row's own declared kind wins; failing that, an
    acceptance percentage in the expected half makes it a goal. Everything
    else is a vector — including a row whose scope text contains numbers."""
    kind, field = declared_kind(case)
    if kind in _GOAL_KIND_TOKENS:
        return COVERAGE_GOAL, f"the row declares {field}={kind!r}"
    pct, why = stated_acceptance_percentage(case)
    if pct is not None:
        return COVERAGE_GOAL, (f"the row states an acceptance percentage "
                               f"({pct:g}%): {why}")
    if kind:
        return STATED_VECTOR, f"the row declares {field}={kind!r}; {why}"
    return STATED_VECTOR, why


def partition(cases: Sequence[dict]) -> Tuple[List[dict], List[dict]]:
    """`(vectors, goals)` over declared rows, declaration order preserved."""
    vectors: List[dict] = []
    goals: List[dict] = []
    for c in cases:
        if not isinstance(c, dict):
            continue
        (goals if classify(c)[0] == COVERAGE_GOAL else vectors).append(c)
    return vectors, goals


def bind_scope(scope_text: Optional[str]) -> Tuple[Optional[str], str]:
    """`(dimension, why)` — the measured dimension this scope names.

    A scope binds ONLY when it names a dimension the run's coverage arm
    actually reports. A scope that names a set of STIMULI ("1 byte / 55 bytes
    / …", "INIT during BUSY / NEXT without prior INIT") names no dimension, so
    nothing this run measured is a statement about it, and inventing one would
    be manufacturing the verdict."""
    if not scope_text:
        return None, "the row names no scope to bind"
    low = scope_text.lower()
    hits = [dim for dim, words in DIMENSION_WORDS.items()
            if any(w in low for w in words)]
    if not hits:
        return None, (f"the stated scope names no coverage dimension this "
                      f"run measures ({'/'.join(sorted(DIMENSION_WORDS))}): "
                      f"{scope_text.strip()[:96]!r}")
    if len(hits) > 1:
        return None, (f"the stated scope names {len(hits)} dimensions "
                      f"({', '.join(sorted(hits))}) — which one carries the "
                      f"acceptance percentage is not stated")
    return hits[0], f"the scope names the {hits[0]} dimension"


def achieved_percentage(totals: Any, dimension: str
                        ) -> Tuple[Optional[float], str]:
    """The percentage the run's coverage arm reports for `dimension`."""
    if not isinstance(totals, dict) or not totals:
        return None, "the run published no coverage totals"
    row = totals.get(dimension)
    if isinstance(row, dict) and row.get("pct") is not None:
        try:
            return float(row["pct"]), f"totals.{dimension}.pct"
        except (TypeError, ValueError):
            return None, f"totals.{dimension}.pct is not a number"
    flat = totals.get(f"{dimension}_pct")
    if flat is not None:
        try:
            return float(flat), f"totals.{dimension}_pct"
        except (TypeError, ValueError):
            return None, f"totals.{dimension}_pct is not a number"
    return None, f"the run's coverage totals carry no {dimension} figure"


#: The instrument kinds a goal can be measured by.
COVERAGE_DIMENSION = "coverage_dimension"
#: A PASS-RATE goal over SCENARIOS ("100% PASS" over a set of situations the
#: scope names). Its instrument is not the coverage arm but the L10 cases bound
#: to it that ran their own oracle.
SCENARIO_PASS_RATE = "scenario_pass_rate"
#: Where the L10 rows LINK a goal to the cases that cover it: on the goal, the
#: cases it names; on a case, the goals it names.
_GOAL_LINK_KEYS = ("covered_by", "bound_cases", "covering_cases")
_CASE_LINK_KEYS = ("covers", "covers_goals", "covers_goal")
_PASS_RATE_RE = re.compile(r"%\s*\(?\s*pass\b", re.I)


def is_pass_rate_goal(case: dict) -> bool:
    """True when the goal's expected half is a PASS-RATE (`N% PASS`)."""
    text, _key = _first(case, _EXPECTED_KEYS)
    return bool(text and _PASS_RATE_RE.search(text))


def _names(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if isinstance(v, str) and v.strip()]
    return []


def _generated_oracle_row(row: Any) -> bool:
    """True only when the executor's TB is provably our generated oracle.

    A scenario goal needs an authored/delivered oracle that reads its scope.
    The generic golden oracle does not, so a passing generated TB is evidence
    of arithmetic only, not of this goal.  An unreadable TB is not silently
    classified: the normal execution-state path will retain its evidence.
    """
    if not isinstance(row, dict):
        return False
    tb = row.get("tb_file")
    if not isinstance(tb, str) or not tb:
        return False
    try:
        import testbench_gen as _tb
        marker = _tb.ORACLE_GENERATED_MARKER
    except Exception:  # pragma: no cover - sibling is always shipped
        marker = "VIBEIC_TB_ORACLE: GENERATED by "
    try:
        return marker in open(tb, errors="replace").read()
    except OSError:
        return False


def bound_cases(goal: dict, rows: Sequence[dict],
                ran: Sequence[str], record: Optional[dict] = None
                ) -> Tuple[List[str], str]:
    """`(case names, how)` bound to a scenario goal, from what is DECLARED.

    Three links, none inferred from prose: the goal names its cases; a case
    names the goal; or the goal's own scenario oracle ran under the goal's
    own L10 name. A case is never bound because its words resemble the
    scope's."""
    name = _text(goal.get("name") or goal.get("id"))
    out: List[str] = []
    how: List[str] = []
    for k in _GOAL_LINK_KEYS:
        for c in _names(goal.get(k)):
            if c not in out:
                out.append(c)
                how.append(f"the goal's {k}")
    for r in rows or []:
        if not isinstance(r, dict) or r is goal:
            continue
        cname = _text(r.get("name") or r.get("id"))
        for k in _CASE_LINK_KEYS:
            if name in _names(r.get(k)) and cname and cname not in out:
                out.append(cname)
                how.append(f"{cname}'s {k}")
    # An executed own oracle is a binding even where links also exist: its
    # failure must not disappear from the denominator.  Generated golden TBs
    # are explicitly not scenario oracles; they never read the L10 scope.
    own = ((record or {}).get("rows") or {}).get(name)
    if name and name in set(ran) and not _generated_oracle_row(own):
        try:
            import _l10_execution as _l10x
            state, _why = _l10x.case_state(name, record or {})
        except Exception:  # pragma: no cover
            state = None
        if state in (PASS, FAIL) and name not in out:
            out.append(name)
            how.append("the goal's own scenario oracle")
    return out, "; ".join(sorted(set(how)))


def measure_scenario_goal(case: dict, stated: float, rows: Sequence[dict],
                          record: dict) -> dict:
    """The SCENARIO_PASS_RATE instrument for ONE goal (verdict fields only)."""
    try:
        import _l10_execution as _l10x
    except Exception as exc:                              # pragma: no cover
        return {"verdict": NOT_MEASURED,
                "why": f"the execution record cannot be read ({exc})"}
    name = _text(case.get("name") or case.get("id"))
    ran = list((record or {}).get("rows") or {})
    bound, how = bound_cases(case, rows, ran, record)
    out: Dict[str, Any] = {"instrument": SCENARIO_PASS_RATE,
                           "bound_cases": bound, "bound_by": how}
    if not bound:
        unavailable = (not (record or {}).get("available"))
        record_note = ("; execution record is unavailable: %s"
                       % ((record or {}).get("reason") or "unknown")
                       if unavailable else "")
        out.update(verdict=NOT_MEASURED, why=(
            f"case {name!r}: a pass-rate goal over scenarios, and no case is "
            f"bound to it -- no L10 row links a case to it (goal "
            f"{'/'.join(_GOAL_LINK_KEYS)} or case {'/'.join(_CASE_LINK_KEYS)})"
            f" and no authored/delivered oracle for the goal itself ran"
            f"{record_note}"))
        return out
    passed, failed, not_run = [], [], []
    for c in bound:
        state, why = _l10x.case_state(c, record or {})
        if state == _l10x.PASS:
            passed.append(c)
        elif state == _l10x.FAIL:
            failed.append(c)
        else:
            not_run.append(f"{c} ({why})")
    out.update(passed=passed, failed=failed)
    if not_run:
        # Unrun cases cannot rescue a target above the best possible rate.
        # Keep achieved_pct unset: the final rate has not been measured.
        maximum = 100.0 * (len(passed) + len(not_run)) / len(bound)
        out["maximum_possible_pct"] = maximum
        out["verdict"] = FAIL if maximum < stated else NOT_MEASURED
        out["why"] = (
            f"case {name!r}: {len(passed)} passed, {len(failed)} failed, and "
            f"{len(not_run)} of {len(bound)} bound case(s) did not run their "
            f"own oracle; at most {maximum:g}% can pass vs the {stated:g}% "
            f"stated; failed: {', '.join(failed) if failed else 'none'}; "
            f"not run: {'; '.join(not_run[:4])}")
        return out
    rate = 100.0 * len(passed) / len(bound)
    out["achieved_pct"] = rate
    out["achieved_source"] = (f"{len(passed)} of {len(bound)} bound case(s) "
                              f"passed their own oracle ({how})")
    out["verdict"] = PASS if rate >= stated else FAIL
    out["why"] = (f"case {name!r}: {len(passed)}/{len(bound)} bound case(s) "
                  f"passed ({rate:g}% vs the {stated:g}% stated)"
                  + (f"; failed: {', '.join(failed)}" if failed else ""))
    return out


def measure_goal(case: dict, totals: Any, rows: Optional[Sequence[dict]] = None,
                 record: Optional[dict] = None) -> dict:
    """The verdict row for ONE coverage goal — by the NUMBER, or by NAME.

    Never PASS without both halves: a stated percentage AND a measured one for
    the dimension the scope names."""
    name = _text(case.get("name") or case.get("id"))
    scope, scope_cite = coverage_scope(case)
    stated, stated_cite = stated_acceptance_percentage(case)
    out: Dict[str, Any] = {
        "case": name, "population": COVERAGE_GOAL,
        "scope": scope, "scope_source": scope_cite,
        "stated_pct": stated, "stated_source": stated_cite,
        "dimension": None, "achieved_pct": None, "achieved_source": None,
    }
    if stated is None:
        out["verdict"] = NOT_MEASURED
        out["why"] = (f"case {name!r}: it is a coverage goal with no stated "
                      f"acceptance percentage ({stated_cite})")
        return out
    dim, why = bind_scope(scope)
    if dim is None and record is not None and is_pass_rate_goal(case):
        # The scope names scenarios, not a coverage dimension: the scenario
        # pass-rate instrument measures it from the cases bound to it.
        out.update(measure_scenario_goal(case, stated, rows or [], record))
        return out
    if dim is None:
        out["verdict"] = NOT_MEASURED
        out["why"] = f"case {name!r}: {why}"
        return out
    out["instrument"] = COVERAGE_DIMENSION
    out["dimension"] = dim
    achieved, src = achieved_percentage(totals, dim)
    if achieved is None:
        out["verdict"] = NOT_MEASURED
        out["why"] = f"case {name!r}: {src}"
        return out
    out["achieved_pct"] = achieved
    out["achieved_source"] = src
    out["verdict"] = PASS if achieved >= stated else FAIL
    out["why"] = (f"case {name!r}: {dim} coverage {achieved:g}% vs the "
                  f"{stated:g}% the design states")
    return out


def measure_goals(cases: Sequence[dict], totals: Any,
                  all_rows: Optional[Sequence[dict]] = None,
                  record: Optional[dict] = None) -> dict:
    """The coverage-goal population and its OWN denominator.

    `all_rows` (every declared L10 row) and `record` (the L10 execution
    record) arm the scenario pass-rate instrument; without them a goal whose
    scope names no coverage dimension is NOT_MEASURED, as before."""
    rows = [measure_goal(c, totals, all_rows, record) for c in cases]
    passed = [r for r in rows if r["verdict"] == PASS]
    failed = [r for r in rows if r["verdict"] == FAIL]
    unmeasured = [r for r in rows if r["verdict"] == NOT_MEASURED]
    return {
        "declared_count": len(rows),
        "passed_count": len(passed),
        "failed_count": len(failed),
        "not_measured_count": len(unmeasured),
        "rows": rows,
        "asked_through": "l10_coverage_goal_classify.measure_goal",
    }


def coverage_goal_refusal(summary: dict) -> Optional[str]:
    """The sentence a run owes when a declared coverage goal is not PASS.

    NOT_MEASURED is not a pass and FAIL is not a pass; both are named, with
    the scope quoted, so no goal is satisfied by being un-instrumented."""
    bad = [r for r in summary.get("rows") or [] if r["verdict"] != PASS]
    if not bad:
        return None
    named = "; ".join(f"{r['case']} [{r['verdict']}] {r['why']}"
                      for r in bad[:4])
    more = len(bad) - 4
    return (f"{summary['passed_count']} of {summary['declared_count']} "
            f"declared coverage goal(s) met their stated percentage. A "
            f"coverage goal is not a functional vector and is never counted "
            f"as one, but it is not satisfied by having no instrument "
            f"either: {named}" + (f" (+{more} more)" if more > 0 else "")
            + f". Asked through {summary['asked_through']}")
