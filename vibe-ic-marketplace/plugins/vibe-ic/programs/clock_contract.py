#!/usr/bin/env python3
"""clock_contract.py — one clock name declares exactly one period.

WHY THIS EXISTS
---------------
Phase 1 emits a clock contract into ``L8_RTL_CONSTANTS.json`` /
``L8_TIMING_WAVEFORM.json`` as ``clocks[]`` + ``clock_domains[]``.  Several
independent extraction strategies write into those two lists, and nothing
ever compared what they wrote.  A real converged run therefore shipped::

    clock_domains[0]  name=clk  freq_mhz=100.0   -> 10 ns
    clock_domains[1]  name=clk  freq_mhz=125.0   ->  8 ns

One clock name, two periods.  Every consumer then quietly picked one of the
two by list order (``sdc_gen._clock_mhz_from_l8_domains`` takes the first
primary record; the ``clocks[]`` seeder takes the first name match), so the
SDC the flow built was a coin-flip that no artifact recorded.
``sdc_validator_check`` is right to refuse to validate an SDC against that
document — but by then the contradiction is already in the deliverable.

This module is the PRODUCER-side contract, so the contradiction cannot reach
the document at all.  It answers one question: *does this document declare
one period per clock name?*  It resolves the one case where the answer is
recoverable by a stated rule, and REFUSES — loudly, in the document — for
every other case, rather than picking a number.

THE STATED RULE: a record that BORROWED its name never outranks the record
that OWNS it.
------------------------------------------------------------------------
Not every duplicate name is a disagreement about a clock.  In the run above
the 125 MHz record never claimed to be ``clk``'s period.  It came from
``doc_freq_mention_keyword_window``, a heuristic that harvests bare frequency
literals appearing near clock vocabulary.  Such a literal has no name of its
own, so the extractor asks ``_v1_6_328_canonical_clock_port_name()`` for the
project's canonical clock port and staples that name on — which is exactly
what ``derived_from == name`` (self-referential, carrying no derivation
information) and ``role == "extracted_from_doc_freq_mention"`` record.  The
100 MHz record, by contrast, OWNS the name: it was classified ``primary`` and
carries file/line evidence for the literal it read.

So the two records are not two claims about one clock; one is a claim, the
other is a naming accident.  Folding the borrowed record into the owner is
therefore not a guess — it undoes an incorrect name assignment.  The borrowed
record's numbers are NOT discarded: they are preserved on the owner under
``alternate_frequency_mentions[]``, where a reviewer can still see them and
no consumer will mistake them for the contract.

THE SECOND STATED RULE: the run's PDK decides which records are even
IN SCOPE — and it is not this module's rule, it is the landed one.
------------------------------------------------------------------------
``_post_emit_reference_clock_config`` lifts the clock a vendor reference flow
used for ITS library and stamps that library identity on the record
(``pdk_scoped_target``).  Such a record says "when built for target X this
design was clocked at P"; the design's own constraint file, carrying no
scope, says "this design is clocked at Q" for whatever target is being built.
Two answers to two different questions are not a contradiction, and refusing
the document because both are present makes a run impossible the moment its
input ships a reference flow for another library — MEASURED on a real run
(2026-09-15): phase 1 halted at 18.8 s on a project that is fully and
consistently specified.

The contract that decides this is ``_l8_clock_scope`` (vibe-ic#2244), which
``sdc_gen`` and ``l8_clock_period_actionability_check`` already delegate to.
This module is the THIRD consumer and delegates to the SAME function rather
than restating the rule, so all three answer identically: a scoped record
naming the run's PDK wins; the design's unscoped record governs when none
does; only-foreign-scoped REFUSES as cross-target timing reuse.  Only the
records that contract SELECTS take part in the one-name-one-period test.
Nothing is dropped — every record stays in the document, and the consumers
select from it by the run's own target.

WHERE IT REFUSES
----------------
When two records that both OWN the name pin different periods, there is no
principled winner and this module does not invent one.  It leaves both
records standing, stamps ``clock_contract_conflicts[]`` on the document
naming both, and returns the conflict so the producer can fail the run.  An
explicit conflict record beats a silently-picked period.

Chip-AGNOSTIC: schema keys and structural provenance markers only; no
chip-class or design-name literal participates in any decision.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Sibling import, resolvable HOWEVER this file is loaded: a by-path load
# (`spec_from_file_location`, how test_issue2104_programs_load_by_path measures
# every shipped program) does not put this directory on sys.path, and a bare
# import then raises ModuleNotFoundError. Same shim every other consumer of the
# contract carries (lander-added at landing; the G arm caught it).
import os as _os
import sys as _sys
if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _l8_clock_scope as _scope  # noqa: E402

#: L8 containers that carry clock records.  ``clocks`` is the canonical list;
#: ``clock_domains`` is what most extraction strategies populate.  A name at
#: 10 ns in one and 8 ns in the other is contradictory wherever it sits, so
#: both are one namespace for this check — the same containers, and the same
#: precedence, that ``sdc_validator_check`` reads.
CLOCK_CONTAINERS: Tuple[str, ...] = ("clocks", "clock_domains")

#: Relative tolerance for "are these the same period?".  Deliberately equal to
#: ``sdc_validator_check._PERIOD_REL_TOL`` so the producer and the consumer
#: cannot disagree about what counts as a contradiction.
PERIOD_REL_TOL = 0.01

#: Key stamped on the document for every conflict this module REFUSED.
CONFLICT_KEY = "clock_contract_conflicts"

#: Key on an owning record holding the folded borrowed-name observations.
MENTIONS_KEY = "alternate_frequency_mentions"

#: ``role`` values that mark a record whose name was supplied by the
#: canonical-clock-port lookup rather than read out of the document.
_BORROWED_ROLES = frozenset({"extracted_from_doc_freq_mention"})

#: Fields worth preserving when a borrowed record is folded into its owner.
_MENTION_FIELDS = (
    "freq_mhz", "freq_hz", "period_ns",
    "low_mhz", "high_mhz", "freq_low_mhz", "freq_high_mhz",
    "role", "source", "evidence", "extraction_strategy",
)


def entry_period_ns(entry: Any) -> Optional[float]:
    """Period in ns for one clock record, or ``None`` when it pins none.

    Field-name variance across IC classes is tolerated rather than treated as
    a schema error: ``period_ns`` wins, then ``freq_mhz``, then ``freq_hz``.
    Range-only records (``low_mhz`` / ``freq_high_mhz`` and friends) do not
    pin a period and must not have one synthesised for them.  Same precedence
    as ``sdc_validator_check._entry_period_ns``.
    """
    if not isinstance(entry, dict):
        return None
    for key, to_ns in (("period_ns", lambda v: v),
                       ("freq_mhz", lambda v: 1000.0 / v),
                       ("freq_hz", lambda v: 1e9 / v)):
        v = entry.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        if float(v) <= 0:
            continue
        return round(float(to_ns(float(v))), 6)
    return None


def entry_is_derived(entry: Any) -> bool:
    """True for a generated/derived clock.

    A derived clock legitimately runs at a different period from its parent
    and is constrained by ``create_generated_clock``, so it is not part of the
    one-name-one-period contract.  A self-referential ``derived_from`` equal
    to the record's own name carries no derivation information and is NOT
    treated as derived — that shape is the borrowed-name marker below.
    """
    if not isinstance(entry, dict):
        return False
    kind = str(entry.get("domain_kind") or "").lower()
    role = str(entry.get("role") or "").lower()
    if "derived" in kind or "generated" in kind:
        return True
    if role in ("derived", "generated", "generated_clock"):
        return True
    parent = entry.get("derived_from")
    if isinstance(parent, str) and parent and parent != entry.get("name"):
        return True
    return False


def entry_owns_name(entry: Any) -> bool:
    """False when this record's ``name`` was stapled on rather than read.

    Two structural markers, both written by the extractor itself:

    * ``derived_from == name`` — self-referential, i.e. the canonical
      clock-port lookup supplied both fields from the same string;
    * ``role`` in :data:`_BORROWED_ROLES` — the record is a bare frequency
      literal harvested from prose, which has no name of its own.

    Only consulted inside a group of records that already share a name, so a
    borrowed record that collides with nothing is never demoted.
    """
    if not isinstance(entry, dict):
        return False
    name = entry.get("name")
    parent = entry.get("derived_from")
    if isinstance(parent, str) and isinstance(name, str) and parent == name:
        return False
    if str(entry.get("role") or "").lower() in _BORROWED_ROLES:
        return False
    return True


def periods_agree(a: float, b: float) -> bool:
    """True when two periods are the same number to within tolerance."""
    return abs(a - b) <= max(PERIOD_REL_TOL * max(abs(a), abs(b)), 1e-9)


def distinct_periods(values: List[float]) -> List[float]:
    """Collapse a period list under :func:`periods_agree`."""
    out: List[float] = []
    for v in sorted(values):
        if not any(periods_agree(v, k) for k in out):
            out.append(v)
    return out


def owning_entry_with_period(entries: Any,
                             name: Any) -> Optional[Dict[str, Any]]:
    """The record that already OWNS ``name`` and pins a period, if any.

    Used by extractors BEFORE they emit: an extractor about to name a
    harvested frequency after the canonical clock port asks this first, and
    when it gets an answer it records an alternate mention instead of
    emitting a second, contradictory record.
    """
    if not isinstance(entries, list) or not isinstance(name, str):
        return None
    for e in entries:
        if not isinstance(e, dict):
            continue
        if e.get("name") != name:
            continue
        if entry_is_derived(e) or not entry_owns_name(e):
            continue
        if entry_period_ns(e) is None:
            continue
        return e
    return None


def record_alternate_mention(owner: Dict[str, Any],
                             mention: Dict[str, Any]) -> None:
    """Preserve a frequency observation on the record that owns the name.

    The observation is evidence, not contract: no consumer reads
    ``alternate_frequency_mentions[]`` as a period, and this module never
    counts it toward the one-name-one-period test.  Duplicate observations
    (same period, same source) collapse.
    """
    if not isinstance(owner, dict) or not isinstance(mention, dict):
        return
    rec = {k: mention[k] for k in _MENTION_FIELDS
           if mention.get(k) is not None}
    if not rec:
        return
    bucket = owner.get(MENTIONS_KEY)
    if not isinstance(bucket, list):
        bucket = []
        owner[MENTIONS_KEY] = bucket
    for prev in bucket:
        if isinstance(prev, dict) and prev == rec:
            return
    bucket.append(rec)


def _describe(entry: Dict[str, Any], container: str) -> Dict[str, Any]:
    """One conflicting record, rendered for the conflict report."""
    out: Dict[str, Any] = {
        "container": container,
        "name": entry.get("name"),
        "period_ns": entry_period_ns(entry),
        "owns_name": entry_owns_name(entry),
    }
    declared = sorted(_scope.scope_values(entry))
    if declared:
        out["scope"] = declared
    for k in ("role", "domain_kind", "extraction_strategy", "source",
              "derived_from"):
        v = entry.get(k)
        if v is not None:
            out[k] = v
    ev = entry.get("evidence")
    if isinstance(ev, dict):
        out["evidence"] = ev
    return out


def _scope_partition(members: List[Tuple[str, Dict[str, Any]]],
                     project: Optional[Path]
                     ) -> Tuple[Optional[List[Tuple[str, Dict[str, Any]]]],
                                Optional[str]]:
    """Which of `members` this RUN's target puts in scope, per vibe-ic#2244.

    Returns ``(members_in_scope, refusal_detail)``.  Exactly one is not None.
    A refusal detail is the landed contract's own sentence, so the three
    consumers cannot drift apart in what they say.
    """
    target, source = (_scope.run_pdk_target(project)
                      if project is not None else (None, None))
    res = _scope.select([e for _, e in members], target, source)
    if res.status == _scope.NO_SCOPED_RECORDS:
        return members, None
    if not res.selected:
        return None, res.detail
    keep = {id(r) for r in res.records}
    return [(c, e) for c, e in members if id(e) in keep], None


def enforce(doc: Any, project: Optional[Path] = None
            ) -> List[Dict[str, Any]]:
    """Make ``doc``'s clock contract self-consistent, or refuse.

    Mutates ``doc`` in place:

    * every borrowed-name record that contradicted the record owning the name
      is REMOVED from its container and folded onto the owner under
      ``alternate_frequency_mentions[]``;
    * every contradiction that survives that — i.e. records that both own the
      name and pin different periods — is left standing and reported in
      ``doc["clock_contract_conflicts"]``.

    Returns the surviving conflicts.  An empty list means the document
    declares exactly one period per clock name.  A non-empty list means the
    producer must FAIL: the document says two things about one clock and this
    module will not choose between them.
    """
    if not isinstance(doc, dict):
        return []

    # (container, entry) for every named, non-derived clock record.
    groups: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
    for container in CLOCK_CONTAINERS:
        entries = doc.get(container)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            if entry_is_derived(entry):
                continue
            groups.setdefault(name.strip(), []).append((container, entry))

    conflicts: List[Dict[str, Any]] = []
    drop: List[Tuple[str, int]] = []          # (container, id(entry))

    for name in sorted(groups):
        members = groups[name]
        periods = [p for p in (entry_period_ns(e) for _, e in members)
                   if p is not None]
        if len(distinct_periods(periods)) <= 1:
            # Already one period: no scope question can arise, and asking one
            # could only invent a refusal where there is no disagreement.
            continue

        # Only now — with a real disagreement on the table — ask the landed
        # contract which records this run's target even puts in scope.
        in_scope, refusal = _scope_partition(members, project)
        if refusal is not None:
            conflicts.append({
                "clock": name,
                "scopes_seen": sorted({v for _, e in members
                                       for v in _scope.scope_values(e)}),
                "periods_ns": distinct_periods(periods),
                "resolution": "refused",
                "reason": refusal,
                "records": [_describe(e, c) for c, e in members],
            })
            continue
        members = in_scope or []
        periods = [p for p in (entry_period_ns(e) for _, e in members)
                   if p is not None]
        if len(distinct_periods(periods)) <= 1:
            continue

        owners = [(c, e) for c, e in members
                  if entry_owns_name(e) and entry_period_ns(e) is not None]
        owner_periods = distinct_periods(
            [entry_period_ns(e) for _, e in owners])          # type: ignore

        if owners and len(owner_periods) == 1:
            # RECONCILE by the stated rule — borrowed never outranks owner.
            canonical = owner_periods[0]
            for container, entry in members:
                if any(entry is o for _, o in owners):
                    continue
                period = entry_period_ns(entry)
                if period is None or periods_agree(period, canonical):
                    continue
                # Prefer an owner in the same container, else the first.
                target = next((o for c, o in owners if c == container),
                              owners[0][1])
                record_alternate_mention(target, entry)
                drop.append((container, id(entry)))
            continue

        # REFUSE — two records that both own the name, or no owner at all.
        conflicts.append({
            "clock": name,
            "periods_ns": distinct_periods(periods),
            "resolution": "refused",
            "reason": (
                "two clock records own the name and pin different periods"
                if len(owner_periods) > 1 else
                "no clock record owns the name, so no record outranks "
                "another"),
            "records": [_describe(e, c) for c, e in members],
        })

    if drop:
        dropped = {(c, i) for c, i in drop}
        for container in CLOCK_CONTAINERS:
            entries = doc.get(container)
            if not isinstance(entries, list):
                continue
            doc[container] = [
                e for e in entries
                if not (isinstance(e, dict)
                        and (container, id(e)) in dropped)
            ]

    if conflicts:
        doc[CONFLICT_KEY] = conflicts
    elif isinstance(doc.get(CONFLICT_KEY), list):
        # Idempotent: a previously-recorded conflict that no longer exists
        # must not linger and fail a clean document forever.
        doc.pop(CONFLICT_KEY, None)
    return conflicts


def conflict_messages(doc: Any, where: str = "") -> List[str]:
    """Human-readable one-liners for the conflicts recorded on ``doc``."""
    out: List[str] = []
    recorded = doc.get(CONFLICT_KEY) if isinstance(doc, dict) else None
    for c in recorded if isinstance(recorded, list) else []:
        if not isinstance(c, dict):
            continue
        periods = c.get("periods_ns") or []
        strategies = sorted({
            str(r.get("extraction_strategy"))
            for r in (c.get("records") or [])
            if isinstance(r, dict) and r.get("extraction_strategy")
        })
        out.append(
            f"{where or 'L8'}: clock {c.get('clock')!r} is declared with "
            f"{len(periods)} conflicting periods ("
            + ", ".join(f"{float(p):g} ns" for p in periods
                        if isinstance(p, (int, float)))
            + ") by records that each own the name"
            + (f" [{', '.join(strategies)}]" if strategies else "")
            + " — REFUSED rather than picking one; fix the extraction that "
              "produced the second period"
        )
    return out
