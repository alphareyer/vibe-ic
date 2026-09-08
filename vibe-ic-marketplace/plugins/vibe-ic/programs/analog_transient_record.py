#!/usr/bin/env python3
"""analog_transient_record.py — how long a block's transient record has to be.

CHIP_AGNOSTIC: strict-logic

WHAT WAS BROKEN
===============
A2's topology testbench IR derived its whole transient span from ONE input —
the conversion window the block's own counter defines — and from nothing else:

    tstop_ns = window_clocks * 2000 / fclk

At `window_clocks` 256 and `fclk` 1 MHz that is 512 converter samples, which is
exactly right for the metric the deck itself measures (bitstream density over
the second window) and is 27x too short for the metric the block is GRADED on.
Neither `enob`, nor `sndr`, nor `osr` appeared anywhere in that derivation, so
there was no path by which a spec the block is held to could lengthen the
record: `analog_resolution_stimulus` refused all nine corner decks with
`record_too_short_for_an_in_band_tone`, 512 available against 13824 required,
and no ENOB target the design could have written would ever have moved the 512.

That is a producer sizing its own record from its own measurement while the
graded spec set has no say — a limit fixed by one input while the thing it
bounds scales with another.

WHAT THIS MODULE DOES
=====================
It derives the record from EVERY declared constraint that bears on it, and it
REFUSES when it cannot — it never silently returns the shorter one.

An entry declares its constraints as DATA, one row per constraint:

    "record_constraints": [
      {"name": "conversion_windows",
       "clocks_expr": "window_clocks * 2",
       "why": "..."},
      {"name": "coherent_in_band_tone",
       "applies_when_spec_declares_any": ["enob", "sndr", ...],
       "needs_spec_bound": ["osr"],
       "clocks_rule": "coherent_in_band_tone",
       "why": "..."},
    ]

`derive` evaluates every APPLICABLE row and returns the LONGEST, naming which
row bound it. A row is applicable when it declares no trigger, or when the
block's bound spec declares one of the rows it triggers on. Rows are lower
bounds on one shared quantity, so the record that satisfies all of them is the
maximum — and reporting WHICH one bound it is the difference between a number a
reader can act on and a number they have to re-derive.

`clocks_rule` names an arithmetic that is not expressible as an expression over
the environment. There is exactly one, and it does not restate its own
threshold: `coherent_in_band_tone` calls
`analog_resolution_stimulus.coherent_record_samples`, the same function the
producer's own refusal is computed from, so the record this sizes and the
record that producer accepts cannot drift apart. A restatement here is the
defect this module exists to remove, one file further along.

IT REFUSES BY NAME RATHER THAN SHORTEN
======================================
Every refusal below is a case where the constraints do not close. None of them
falls back to a shorter record, because a record that is quietly too short is
what the flow already had and what nobody could see:

  record_constraints_absent      the entry declares no constraint at all, so
                                 there is nothing to derive a record from
  record_input_unbound           an APPLICABLE row needs a spec row the design
                                 does not bind — with no declared signal band
                                 there is no record length that puts a tone
                                 inside it, so this is unsatisfiable and not
                                 merely unknown
  record_constraint_unresolvable an applicable row's expression or rule cannot
                                 be evaluated in this block's environment
  record_constraint_not_a_length its value is not a positive finite number
  record_constant_not_consumed   the entry derives a record and then does not
                                 USE it — its transient never names
                                 `record_clocks`, so the deck would run the
                                 length it always ran and this whole derivation
                                 would be a number in a JSON file. That is
                                 vibe-ic#2200 exactly, and it is refused rather
                                 than emitted.

`unbound_inputs` is the same first check, split out so an emitter can raise it
at ADMISSION time — before a topology reaches disk — alongside its other
`requires_*` refusals.

chip-AGNOSTIC. No design, block, PDK, node or vendor literal appears here; the
constraint rows are the caller's data and the one arithmetic is imported.
"""
from __future__ import annotations

# --- sibling-import path (vibe-ic#2104) ------------------------------------
import os as _os                                                    # noqa: E402
import sys as _sys                                                  # noqa: E402

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
# ---------------------------------------------------------------------------

import ast as _ast
import math
from typing import Any, Callable, Dict, List, Optional, Sequence

from analog_resolution_stimulus import coherent_record_samples

PRODUCER = "analog_transient_record"

#: The name the derived length is published under, in the IR `constants`, and
#: therefore the name an entry's transient expression has to use. One spelling,
#: so `record_constant_not_consumed` can check for it rather than guess.
RECORD_CONSTANT = "record_clocks"

#: The entry key the constraint rows live under.
CONSTRAINTS_KEY = "record_constraints"

_ALLOWED_NODES = (
    _ast.Expression, _ast.BinOp, _ast.UnaryOp, _ast.Constant, _ast.Name,
    _ast.Load, _ast.Add, _ast.Sub, _ast.Mult, _ast.Div, _ast.Pow,
    _ast.USub, _ast.UAdd, _ast.Mod, _ast.FloorDiv,
)


class RecordNotDerivable(ValueError):
    """This entry's transient record cannot be derived from what it declares.

    Carries the per-row refusals so the honest-gap artefact names each one.
    Deliberately an exception and not a fallback: the caller that catches this
    writes a gap and emits no topology, which is the whole point — the record
    being quietly wrong is the failure mode, not the record being absent."""

    def __init__(self, refusals: Sequence[Dict[str, Any]]) -> None:
        super().__init__("; ".join(str(r.get("detail") or r.get("requirement"))
                                   for r in refusals))
        self.refusals = [dict(r) for r in refusals]


# ── the one arithmetic that is not an expression ───────────────────────────
def _coherent_in_band_tone(env: Dict[str, float],
                           spec_values: Dict[str, float]) -> float:
    """The record a coherent in-band tone needs, in converter samples.

    Imported, never restated: `coherent_record_samples` is the function the
    stimulus producer's own `record_too_short_for_an_in_band_tone` refusal is
    computed from, and a second copy of it here is how the two would come to
    disagree. One sample is one clock for a converter clocked at `fclk`, which
    is the unit every other row is in."""
    return float(coherent_record_samples(spec_values["osr"])["samples"])


#: Every `clocks_rule` an entry may name. A rule the registry does not carry is
#: a refusal, never a skipped row.
RULES: Dict[str, Callable[[Dict[str, float], Dict[str, float]], float]] = {
    "coherent_in_band_tone": _coherent_in_band_tone,
}


def _safe_eval(expr: str, env: Dict[str, float]) -> float:
    """Arithmetic over named values only. No calls, no attributes, no
    subscripts — a constraint row is data read off a library, not code."""
    tree = _ast.parse(str(expr), mode="eval")
    for node in _ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ValueError(f"disallowed expression node "
                             f"{type(node).__name__} in {expr!r}")
        if isinstance(node, _ast.Name) and node.id not in env:
            raise KeyError(node.id)
    return float(eval(compile(tree, "<record-expr>", "eval"),  # noqa: S307
                      {"__builtins__": {}}, dict(env)))


# ── which rows bear on this block ──────────────────────────────────────────
def constraint_rows(entry: Any) -> List[Dict[str, Any]]:
    """The declared rows, or [] for an entry that declares none."""
    rows = (entry or {}).get(CONSTRAINTS_KEY) if isinstance(entry, dict) else None
    return [r for r in (rows or []) if isinstance(r, dict)]


def applies(row: Dict[str, Any], spec_values: Dict[str, float]) -> bool:
    """True when this row bears on a block with these bound spec rows.

    A row with no trigger always applies — that is the constraint the deck's
    own measurement imposes and it is there for every block. A row WITH a
    trigger applies exactly when the design declares one of the things it
    names, which is what makes the graded spec set an input to the record
    length instead of a bystander."""
    trigger = row.get("applies_when_spec_declares_any")
    if not trigger:
        return True
    return any(n in (spec_values or {}) for n in trigger)


def unbound_inputs(entry: Any,
                   spec_values: Dict[str, float]) -> List[Dict[str, Any]]:
    """Refusals for every APPLICABLE row whose declared spec inputs are not
    bound. Empty when the record can be derived from what the design binds.

    Separate from `derive` so an emitter can raise it at admission time, next
    to its other requirement refusals, before anything reaches disk."""
    out: List[Dict[str, Any]] = []
    for row in constraint_rows(entry):
        if not applies(row, spec_values):
            continue
        for name in (row.get("needs_spec_bound") or []):
            if name not in (spec_values or {}):
                out.append({
                    "requirement": "record_input_unbound",
                    "field": name,
                    "constraint": row.get("name"),
                    "why": row.get("why"),
                    "detail": (
                        f"the transient record is bounded by "
                        f"`{row.get('name')}`, which this declaration triggers "
                        f"and which is derived from `{name}` — and the A1 spec "
                        f"artefact binds no numeric value for `{name}`. There "
                        f"is no record length that satisfies a constraint "
                        f"whose own input is absent, so this is refused rather "
                        f"than sized to the constraints that are left"),
                })
    return out


# ── the derivation ─────────────────────────────────────────────────────────
def derive(entry: Any, env: Dict[str, float],
           spec_values: Dict[str, float],
           consumer_expr: Optional[str] = None) -> Dict[str, Any]:
    """The record length, in clocks, and the whole derivation that produced it.

    `env` is the expression environment the entry's own constants and knobs
    resolve in; `spec_values` is what the design BINDS. `consumer_expr` is the
    transient expression that will USE the result — passed so the derivation
    can refuse an entry that derives a record and then does not spend it.

    Raises `RecordNotDerivable` in every case where the constraints do not
    close. It never returns the shorter of two answers.
    """
    refusals: List[Dict[str, Any]] = []
    rows = constraint_rows(entry)
    if not rows:
        raise RecordNotDerivable([{
            "requirement": "record_constraints_absent",
            "detail": (f"this entry declares no `{CONSTRAINTS_KEY}`, so there "
                       f"is nothing its transient record can be derived FROM. "
                       f"A record sized by one hard-coded expression is the "
                       f"defect, not the baseline")}])

    refusals.extend(unbound_inputs(entry, spec_values))

    record: List[Dict[str, Any]] = []
    for row in rows:
        name = row.get("name")
        applied = applies(row, spec_values)
        item: Dict[str, Any] = {"constraint": name, "applies": applied,
                                "why": row.get("why")}
        if not applied:
            item["not_applicable_because"] = (
                "this declaration binds none of "
                + ", ".join(f"`{n}`" for n in
                            (row.get("applies_when_spec_declares_any") or [])))
            record.append(item)
            continue
        if any(r.get("constraint") == name for r in refusals):
            record.append(item)
            continue
        expr = row.get("clocks_expr")
        rule = row.get("clocks_rule")
        value: Optional[float] = None
        try:
            if expr is not None:
                item["clocks_expr"] = expr
                value = _safe_eval(expr, env)
            elif rule is not None:
                item["clocks_rule"] = rule
                if rule not in RULES:
                    raise KeyError(rule)
                value = RULES[rule](env, spec_values)
            else:
                raise ValueError("row declares neither `clocks_expr` nor "
                                 "`clocks_rule`")
        except KeyError as exc:
            missing = str(exc.args[0] if exc.args else exc)
            refusals.append({
                "requirement": "record_constraint_unresolvable",
                "constraint": name, "missing": missing,
                "why": row.get("why"),
                "detail": (f"the record constraint `{name}` was NOT EVALUATED: "
                           f"this block's environment binds no value for "
                           f"`{missing}`. That is not the same statement as "
                           f"the constraint having been evaluated and found "
                           f"shorter than another one")})
            record.append(item)
            continue
        except Exception as exc:                                # noqa: BLE001
            refusals.append({
                "requirement": "record_constraint_unresolvable",
                "constraint": name, "missing": f"{type(exc).__name__}: {exc}",
                "why": row.get("why"),
                "detail": (f"the record constraint `{name}` could not be "
                           f"evaluated: {type(exc).__name__}: {exc}")})
            record.append(item)
            continue
        if not (math.isfinite(value) and value > 0):
            refusals.append({
                "requirement": "record_constraint_not_a_length",
                "constraint": name, "value": value,
                "why": row.get("why"),
                "detail": (f"the record constraint `{name}` evaluates to "
                           f"{value!r}, which is not a length. A record is a "
                           f"positive finite number of clocks")})
            record.append(item)
            continue
        item["clocks"] = value
        record.append(item)

    if refusals:
        raise RecordNotDerivable(refusals)

    binding = max((i for i in record if "clocks" in i),
                  key=lambda i: i["clocks"])
    clocks = float(binding["clocks"])

    # THE DERIVATION HAS TO REACH THE DECK. An entry that computes a record and
    # then runs a transient expressed in something else has changed nothing —
    # which is precisely the state vibe-ic#2200 reports, one file earlier. So
    # the connection is CHECKED, not assumed, and its absence is a refusal.
    if consumer_expr is not None:
        try:
            names = {n.id for n in _ast.walk(_ast.parse(str(consumer_expr),
                                                        mode="eval"))
                     if isinstance(n, _ast.Name)}
        except SyntaxError:
            names = set()
        if RECORD_CONSTANT not in names:
            raise RecordNotDerivable([{
                "requirement": "record_constant_not_consumed",
                "constraint": binding["constraint"],
                "consumer_expr": consumer_expr,
                "detail": (
                    f"this entry derives a transient record of {clocks:g} "
                    f"clocks, bound by `{binding['constraint']}`, and its "
                    f"transient is written {consumer_expr!r} — which does not "
                    f"name `{RECORD_CONSTANT}`. The deck would run the length "
                    f"it always ran and the derivation would be a number in a "
                    f"JSON file that nothing spends")}])

    return {
        "producer": PRODUCER,
        RECORD_CONSTANT: clocks,
        "binding_constraint": binding["constraint"],
        "constraints": record,
        "consumer_expr": consumer_expr,
    }
