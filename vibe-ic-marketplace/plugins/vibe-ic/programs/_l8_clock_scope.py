#!/usr/bin/env python3
"""_l8_clock_scope.py — the ONE answer to "which L8 clock record applies to
THIS run?" when L8's records are PDK-scoped.

WHY THIS MODULE EXISTS
======================
Two consumers asked that question, each with its own copy of the matcher, and
both copies asked the WRONG question in the SAME two ways.

MEASURED 2026-09-15 (lane icspm) on live main ``9320b02697c6``, running the
canonical front door on a design whose L1 declares TWO target processes::

    $ vibe_ic_one_shot_runner.py <project> --pdk gf180mcuD --ic-name spm
    FAIL  sdc_gen  rc=1 err=FAIL: L8_CLOCK_SCOPE_MISMATCH:
                                  no scoped L8 clock matches L19 target 'sky130'
    overall verdict : FAIL     halted at : phase2

Phase 1 had already answered correctly, twice:

  * ``L8_RTL_CONSTANTS.clock_domains[0]`` carried
    ``{"pdk_scoped_target": "gf180mcuD", "period_ns": 24.0}`` — stamped by
    ``phase1_doc_one_shot_runner`` straight from the CLI ``--pdk``
    (``d["pdk_scoped_target"] = _CLI_PDK``), off the design's own clock row for
    that process;
  * ``reports/phase1/submission_template_fetch.json`` carried
    ``{"pdk": "gf180mcuD", "pdk_source": "--pdk"}`` — flow step 0.5ic's record
    of what this run targets.

The consumers asked a THIRD place, ``L19.fields.pdk_target``, and refused.

D1 — THE DECLARATION IS NOT THE RUN
===================================
``L19.fields.pdk_target`` is the DESIGN's declaration: the first family its L1
names. ``phase1_doc_one_shot_runner`` says so where it writes the field's
companion: "a design may declare MORE THAN ONE target process, and
``pdk_target`` is one scalar … phase3's declared-vs-resolved guard then REFUSED
an entire run on <B> — a process the design names, in the same breath, on the
same row." That is why ``pdk_target_alternates`` exists, and why
``tapeout_precheck`` honours it. Neither clock consumer did. A design that
declares two processes and is run on its second one was refused by both.

D2 — TWO NAMING REGISTERS, COMPARED WITH ``==``
===============================================
L8 scopes to the PDK NAME the run was given (``gf180mcuD``). L19's
``pdk_target`` / ``pdk_target_alternates`` hold FAMILY names (``gf180mcu``).
The match was ``target.lower() in scope_values``, so ``'gf180mcu'`` did not
match ``'gf180mcud'`` — a refusal between two spellings of ONE process. D2 is
not a corollary of D1: handing the family name over still fails.
``programs/pdk_family_identity.py`` already exists to end exactly this class of
disagreement, and this module routes every comparison through it.

THE CONTRACT — four outcomes, and a scoped record is not the only kind
=====================================================================
Stated once here so the two consumers cannot drift apart again:

  1. ``SELECTED_SCOPED``   — a scoped record matches the run's PDK by name or
                             family. Use those records.
  2. ``SELECTED_UNSCOPED`` — no scoped record matches, but the design also
                             states an UNSCOPED record. A design-owned clock
                             that names no process applies to every process, so
                             it is used, and the summary says
                             ``unscoped_design_owned`` rather than passing
                             silently.
  3. ``MISMATCH``          — every record is scoped and all of them are
                             foreign. THE HONEST REFUSAL, preserved verbatim:
                             a backend must never close timing against a period
                             this design never asked for on this process.
  4. ``NOT_MEASURED``      — records are scoped and no run PDK is known. "I
                             could not tell" is not "they disagree", and the
                             two must not reach a reader as one verdict.

``NO_SCOPED_RECORDS`` is the fifth, non-verdict outcome: nothing is scoped, the
branch does not apply, and the caller proceeds exactly as it did before.

WHY THE RUN RECORD IS READ FROM ITS PRODUCER'S OWN CONSTANT
===========================================================
``submission_template_fetch.REPORT_REL`` names the file; this module imports
that constant rather than retyping the path, and a test asserts the two agree.
The fallback to the L19 declaration is kept for a project with no such record,
so nothing that passed before this module existed changes its answer.

chip-AGNOSTIC: no PDK, vendor, design, IC or directory-name literal. Every
process name is data read from the project.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence, Set, Tuple

__all__ = [
    "MISMATCH",
    "NOT_MEASURED",
    "NO_SCOPED_RECORDS",
    "Resolution",
    "SELECTED_SCOPED",
    "SELECTED_UNSCOPED",
    "SCOPE_KEYS",
    "UNSCOPED_SUMMARY_TOKEN",
    "matches_target",
    "run_pdk_target",
    "scope_values",
    "select",
]

#: The record keys that can carry a target identity. Same list in both
#: consumers before this module; stated once now.
SCOPE_KEYS: Tuple[str, ...] = ("pdk_scoped_target", "pdk_target",
                               "technology_scope")

SELECTED_SCOPED = "scoped_run_pdk"
SELECTED_UNSCOPED = "unscoped_design_owned"
MISMATCH = "L8_CLOCK_SCOPE_MISMATCH"
NOT_MEASURED = "L8_CLOCK_SCOPE_NOT_MEASURED"
NO_SCOPED_RECORDS = "no_scoped_records"

#: What a summary says when outcome 2 fired. The orchestrator's ruling names
#: this token; consumers must not spell their own.
UNSCOPED_SUMMARY_TOKEN = SELECTED_UNSCOPED

#: `submission_template_fetch` writes this; imported, never retyped. The
#: literal is the fallback for an environment where that module cannot be
#: imported, and `test_..._is_the_producers_own_constant` asserts they agree.
try:  # pragma: no cover - exercised by the drift test
    from submission_template_fetch import REPORT_REL as _RUN_PDK_REPORT_REL
except Exception:  # noqa: BLE001  # pragma: no cover
    _RUN_PDK_REPORT_REL = "reports/phase1/submission_template_fetch.json"

RUN_PDK_REPORT_REL = _RUN_PDK_REPORT_REL

#: Where the run's PDK came from. A caller that must not guess can refuse
#: `SOURCE_DECLARATION`, which is the DESIGN's word, not the run's.
SOURCE_RUN_RECORD = "run_record"
SOURCE_DECLARATION = "l19_declaration"


class Resolution:
    """The outcome, the records it selects, and what it was measured from."""

    __slots__ = ("status", "records", "target", "target_source", "detail")

    def __init__(self, status: str, records: Sequence[Any],
                 target: Optional[str], target_source: Optional[str],
                 detail: str) -> None:
        self.status = status
        self.records = list(records)
        self.target = target
        self.target_source = target_source
        self.detail = detail

    @property
    def selected(self) -> bool:
        """True iff records were chosen and the caller may proceed."""
        return self.status in (SELECTED_SCOPED, SELECTED_UNSCOPED,
                               NO_SCOPED_RECORDS)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return (f"Resolution({self.status!r}, n={len(self.records)}, "
                f"target={self.target!r}, source={self.target_source!r})")


def _read_json(path: Path) -> Optional[dict]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def scope_values(rec: Any) -> Set[str]:
    """The target identities a record declares, case-folded.

    Never invents one: a record that names no target returns the empty set and
    is an UNSCOPED record, which is a different thing from a foreign one.
    """
    values: Set[str] = set()
    if not isinstance(rec, dict):
        return values
    for key in SCOPE_KEYS:
        value = rec.get(key)
        if isinstance(value, str) and value.strip():
            values.add(value.strip().lower())
        elif isinstance(value, dict):
            values.update(v.strip().lower() for v in value.values()
                          if isinstance(v, str) and v.strip())
    return values


def matches_target(target: Optional[str], values: Set[str]) -> bool:
    """Does `target` denote the same process as any of `values`?

    THREE LADDER RUNGS, strongest first:

      1. the same spelling, case-folded;
      2. both names resolve in ``programs/pdk_registry.json`` and resolve to
         the SAME canonical family — the authoritative answer, and the one the
         real case needs (L8 scopes to the PDK name the run was given while
         L19 holds the family name);
      3. neither pair resolves, so fall back to spelling strength, and require
         at least ``MATCH_PREFIX``.

    WHY RUNG 3 IS NOT ``same_family``. ``pdk_family_identity.same_family`` is
    True at ``MATCH_TOKEN`` — a SHARED WORD. MEASURED while writing this
    module::

        same_family('process_family_c', 'process_family_a')  -> True
        match_strength('process_family_c', 'process_family_a') -> 1 (MATCH_TOKEN)

    i.e. two DIFFERENT processes that merely share the words "process" and
    "family" would have selected each other's clock, and
    ``test_backend_consumer_selects_scope_before_unscoped_scalar`` — which
    asserts a third target selects NOTHING — caught it. A token overlap is
    enough to suggest a family; it is not enough to hand a backend a period.
    ``MATCH_PREFIX`` is what the real register difference actually costs:
    ``'gf180mcu'`` is a prefix of ``'gf180mcud'``, ``'sky130'`` of
    ``'sky130a'``. Anything weaker is refused, and a refusal here is the
    honest answer.
    """
    if not target:
        return False
    want = target.strip().lower()
    if not want:
        return False
    if want in values:
        return True
    try:
        import pdk_family_identity as _fam  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return False
    try:
        want_canonical = _fam.canonical_family(target)
    except Exception:  # noqa: BLE001
        want_canonical = None
    for value in values:
        try:
            if want_canonical is not None:
                other = _fam.canonical_family(value)
                if other is not None:
                    if other == want_canonical:
                        return True
                    # Both are registry-known and they are DIFFERENT families.
                    # The registry has answered; spelling cannot overrule it.
                    continue
            if _fam.match_strength(target, value) >= _fam.MATCH_PREFIX:
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def run_pdk_target(project: Path,
                   generated_docs: Optional[Path] = None
                   ) -> Tuple[Optional[str], Optional[str]]:
    """``(the PDK this run targets, where that was measured)``.

    THE RUN FIRST. Flow step 0.5ic records the resolved PDK — and whether it
    came from ``--pdk`` or from the design — in its own report. That is a fact
    about THIS run.

    THE DECLARATION SECOND, unchanged from what both consumers did before, so a
    project that never ran step 0.5ic answers exactly as it used to.

    Neither — ``(None, None)``, and the caller reports NOT_MEASURED. We do not
    invent a target.
    """
    rec = _read_json(project / RUN_PDK_REPORT_REL)
    if rec is not None:
        value = rec.get("pdk")
        if isinstance(value, str) and value.strip():
            return value.strip(), SOURCE_RUN_RECORD
    docs = generated_docs or (project / "phase1" / "generated_docs")
    try:
        candidates = sorted(docs.glob("L19*.json"))
    except OSError:  # pragma: no cover - unreadable dir
        candidates = []
    for path in candidates:
        doc = _read_json(path) or {}
        fields = doc.get("fields")
        value = fields.get("pdk_target") if isinstance(fields, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip(), SOURCE_DECLARATION
    return None, None


def select(records: Sequence[Any], target: Optional[str],
           target_source: Optional[str] = None,
           scope_of: Optional[Callable[[Any], Set[str]]] = None
           ) -> Resolution:
    """Apply the four-outcome contract to `records`.

    `scope_of` lets a caller whose records are not plain dicts say how a scope
    is read off one; it defaults to :func:`scope_values`.
    """
    read_scope = scope_of or scope_values
    scoped: List[Any] = []
    unscoped: List[Any] = []
    for rec in records:
        (scoped if read_scope(rec) else unscoped).append(rec)

    if not scoped:
        return Resolution(NO_SCOPED_RECORDS, records, target, target_source,
                          "no L8 clock record declares a target scope")

    if not target:
        return Resolution(
            NOT_MEASURED, [], None, None,
            f"{len(scoped)} L8 clock record(s) are target-scoped and this run "
            "has no measured PDK target to select with")

    matched = [r for r in scoped if matches_target(target, read_scope(r))]
    if matched:
        return Resolution(
            SELECTED_SCOPED, matched, target, target_source,
            f"{len(matched)} of {len(scoped)} target-scoped L8 clock "
            f"record(s) name {target!r}")

    if unscoped:
        return Resolution(
            SELECTED_UNSCOPED, unscoped, target, target_source,
            f"no target-scoped L8 clock record names {target!r}; using the "
            f"{len(unscoped)} record(s) the design states without a target "
            "scope, which apply to every process")

    foreign = sorted({v for r in scoped for v in read_scope(r)})
    return Resolution(
        MISMATCH, [], target, target_source,
        f"no L8 clock record names {target!r}; every record is scoped to "
        f"another process ({', '.join(foreign)}). Refusing cross-target "
        "timing: the design states no clock period for the process this run "
        "targets")
