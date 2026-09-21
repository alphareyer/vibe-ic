#!/usr/bin/env python3
"""_structural_absence.py — a checker whose SUBJECT CLASS is absent has DECIDED.

NOT A GATE. A shared vocabulary imported by the structural-RTL checkers and by
`_flow_reason_taxonomy`, so the shape of the claim is stated ONCE and cannot
drift between the checker that makes it and the umbrella that reads it.

WHY THIS EXISTS — R-0915-119, and the measurement behind it.

MEASURED, sha256 x sky130A, FRONT DOOR, run24 on main 8337cc81f:
`final_audit Overall: NOT_MEASURED (strict=True)` <- Step P0
`partial_population` <- EIGHT INCOMPLETE checkers, seven of which exit rc=2
saying the design has no such structure at all:

    arbiter_starvation_check              no arbitration patterns found
    bram_pdob_combinational_check         no inferred BRAM / megafunction wrappers
    cross_module_1cycle_handshake_check   no cross-module 1-cycle pulse races
    frame_end_detection_check             no rx_* modules found
    tx_abort_during_transmission_check    examined nothing (no_tx_modules)
    break_handler_safety_check            examined nothing (no_break_signals)
    l4_regmap_enumerated_values_typed_check  no multi-bit enum-eligible fields

All seven were recorded `reason_class=EXECUTION_ERROR`, which says the program
errored — and none of them did. A design with no arbiter has been ANSWERED
about arbiters.

`_flow_reason_taxonomy` already recognised these sentences
(`_SUBJECT_ABSENT_RE`) and deliberately refused to act on them, for a reason
this module keeps rather than overturns:

    A DESIGN_DECLARED_NA must carry the fact that says the design has no such
    thing ... I had made the prose its own basis, which is the very laundering
    the docstring refuses; the sentence was a better clue than the old
    default, and a clue is not a declaration.

So the claim is not made by the sentence. It is made by the CHECKER, out of
its own ENUMERATION, and it carries that enumeration with it.

THE TWO GUARDS, BOTH REQUIRED (R-0915-119):

  (i)  POSITIVELY ESTABLISHED. The checker must say WHAT POPULATION it
       enumerated and that the enumeration came back empty — the module list,
       the signal list, the field list it actually walked. A caught exception,
       an unreadable input or an empty glob is NOT this class: a checker that
       could not read its inputs has established nothing and stays
       EXECUTION_ERROR. `absence()` refuses to build a claim without a named
       population and a scanned count.

  (ii) FOUND-BUT-EXAMINED-NOTHING IS NOT THIS CLASS. A checker that found its
       subject and examined none of it has a zero denominator, which is a
       different fact and stays INCOMPLETE. `absence()` refuses when
       `found` is non-zero.

IT IS NEVER A PASS. `NOT_APPLICABLE_BY_STRUCTURE` is a third state, published
under its own name beside PASS and INCOMPLETE. The umbrella counts it as
DECIDED — the same way R-0915-102(1) counts a design-declared N/A — and strict
mode still refuses on any true incomplete.

chip-AGNOSTIC: structural vocabulary only (population, scanned, found). No
chip, vendor, node, SKU or register spelling takes part.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence

#: The class name. One spelling, imported by everything that says it.
NOT_APPLICABLE_BY_STRUCTURE = "NOT_APPLICABLE_BY_STRUCTURE"
#: Where the evidence rides in a checker's report / summary.
EVIDENCE_KEY = "structural_absence"
#: An enumeration has to have walked SOMETHING. A claim over a population the
#: checker never opened is the empty-glob case guard (i) refuses.
MIN_SCANNED = 1


def absence(population: str, scanned: int, found: int = 0,
            names: Optional[Sequence[str]] = None,
            detail: str = "") -> Dict[str, Any]:
    """The evidence record for "this design has no such subject".

    `population` — what was enumerated, in the checker's own words ("modules
    in phase2/stage1/rtl", "fields declared by L4.registers[]").
    `scanned`    — how many members of that population the checker walked.
    `found`      — how many were of the subject class. MUST be 0.

    Raises `ValueError` rather than returning a weak claim: a checker that
    cannot say what it enumerated has not established an absence, and letting
    it publish one anyway is exactly the laundering this class exists to
    avoid."""
    if not str(population or "").strip():
        raise ValueError("a structural absence must name the population it "
                         "enumerated")
    if not isinstance(scanned, int) or isinstance(scanned, bool) or \
            scanned < MIN_SCANNED:
        raise ValueError(f"a structural absence must have walked at least "
                         f"{MIN_SCANNED} member(s) of {population!r}; got "
                         f"scanned={scanned!r}. An empty or unreadable input "
                         f"establishes nothing and stays EXECUTION_ERROR")
    if not isinstance(found, int) or isinstance(found, bool) or found != 0:
        raise ValueError(f"found={found!r}: the subject IS present, so this "
                         f"is a zero denominator, not a structural absence")
    rec: Dict[str, Any] = {
        "population": str(population),
        "scanned": int(scanned),
        "found": 0,
    }
    if names is not None:
        rec["scanned_names"] = [str(n) for n in names]
    if detail:
        rec["detail"] = str(detail)
    return rec


def is_valid(evidence: Any) -> bool:
    """Does this record actually establish a structural absence?

    The umbrella's side of guard (i): a class token with no enumeration behind
    it is not believed, whoever wrote it."""
    if not isinstance(evidence, Mapping):
        return False
    if not str(evidence.get("population") or "").strip():
        return False
    scanned = evidence.get("scanned")
    found = evidence.get("found")
    if not isinstance(scanned, int) or isinstance(scanned, bool):
        return False
    if scanned < MIN_SCANNED:
        return False
    if not isinstance(found, int) or isinstance(found, bool) or found != 0:
        return False
    return True


def evidence_of(report: Any) -> Optional[Dict[str, Any]]:
    """The evidence record inside a checker's report, top level or summary."""
    if not isinstance(report, Mapping):
        return None
    for holder in (report, report.get("summary")):
        if isinstance(holder, Mapping):
            ev = holder.get(EVIDENCE_KEY)
            if is_valid(ev):
                return dict(ev)
    return None


def sentence(evidence: Mapping[str, Any], subject: str) -> str:
    """The one line a checker prints, so every one of them says it the same
    way and a reader can tell it from an error at a glance."""
    ev = dict(evidence)
    tail = f" — {ev['detail']}" if ev.get("detail") else ""
    return (f"[{NOT_APPLICABLE_BY_STRUCTURE}] {subject}: enumerated "
            f"{ev['scanned']} {ev['population']} and found 0 — this design "
            f"has no such subject, so the question is ANSWERED, not "
            f"unmeasured{tail}")


def attach(summary: Dict[str, Any], evidence: Dict[str, Any]) -> Dict[str, Any]:
    """Put the class and its evidence where every reader already looks."""
    summary["reason_class"] = NOT_APPLICABLE_BY_STRUCTURE
    summary[EVIDENCE_KEY] = evidence
    return summary
