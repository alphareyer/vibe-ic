#!/usr/bin/env python3
"""_impl_flow.py — the implementation-flow mode record (`--librelane`, W0).

WHAT THIS IS
============
One project is implemented by ONE flow. ``impl`` names it:

    vibe-ic    the default: vibe-ic's own producers, per step (and the
               per-step LibreLane arms the switch file already selects).
    librelane  ``--librelane``: LibreLane runs synthesis (segment 1) and
               implementation through GDS (segment 2); vibe-ic runs steps
               11-14, the die, the pad wrapper and the SDC between them, and
               every vibe-ic gate judges the result.
    orfs       RESERVED. The internal name of a later OpenROAD-flow-scripts
               mode. It is not ``openroad``, because ``openroad`` already names
               vibe-ic's direct OpenROAD deck. v1 refuses it by name.

The mode is resolved ONCE, at the front door, and every child runner reads the
same answer from the project instead of re-deciding it.

THE DEFAULT WRITES NOTHING
==========================
When the mode is ``vibe-ic`` no record is written, and resolving it reads
nothing but the (absent) record. A project that never saw a flag is
byte-for-byte what it was before this module existed; an absent record MEANS
``vibe-ic``. `dispatch_config_entry` follows the same rule, so the admission
identity of a default run does not move.

AN IN-PLACE SWITCH IS REFUSED (orchestrator decision 20)
========================================================
A project implemented by one flow cannot be re-implemented by another in
place: the step cache, the provenance ledger and the reports would mix two
flows' artefacts under one set of names. A different mode needs a fresh project
clone. Three things can show that a project already has a mode:

  * the record itself (a non-default mode was resolved here before);
  * the canonical-run admission ledger: a project that has admitted a
    Phase-2/Phase-3 span has spent compute in the mode that span's
    ``dispatch_config`` names, and an absent ``impl`` there is ``vibe-ic``;
  * default-flow OUTPUT (`default_output_evidence`): provenance rows for
    phase2/phase3 outputs that no external flow is attributed with, and
    step-cache producer sidecars. The phase-3 window path admits into a
    temporary copy that is deleted, so its run leaves no ledger row; its
    output stays. A hand-copied tree with neither is not seen.

ONCE A RECORD EXISTS, IT IS THE ANSWER. History is consulted only when a
NON-default mode is requested and no record exists yet -- i.e. exactly when
a record would be created, by `resolve` or by `write_record`. Rows written
after the record (a flagged admission whose ``dispatch_config`` lacks
``impl``) can therefore never lock a project out of its own mode, and the
default path never reads the ledger at all. (A default request against a
non-default project is caught by the record.)

Every refusal raises `ImplRefusal` with a stable ``reason_class``:

    IMPL_UNKNOWN            not a mode name (``openroad`` included, see above)
    IMPL_NOT_YET_SUPPORTED  a reserved mode (``orfs``) that v1 does not run
    IMPL_MODE_CONFLICT      the request disagrees with the project's mode
    IMPL_RECORD_UNREADABLE  the record or the ledger cannot be read, so the
                            project's mode cannot be proven
    IMPL_IMAGE_CHANGED      the recorded mode was implemented on another
                            image than this dispatch resolved

WHERE IT LIVES
==============
``<project>/.vibeic-state/impl-mode-v1.json``. ``.vibeic-state`` is runtime
bookkeeping that `design_input_digest` excludes: the mode is a dispatch choice,
not a design input. The admission identity carries it through
``dispatch_config`` (W2), which is where a mode change must reopen admission.

The record also reserves the two facts later items fill in:
``tool_defaults`` (W9: what the tool assumed where the design declares nothing,
per question, never written into the tapeout declaration) and ``image`` (the
image resolved at dispatch, decision 24).

chip-AGNOSTIC / PDK-AGNOSTIC: no design, cell or PDK name is read or written.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

_PROGRAMS_DIR = str(Path(__file__).resolve().parent)
if _PROGRAMS_DIR not in sys.path:
    sys.path.insert(0, _PROGRAMS_DIR)

import _atomic_artefact  # noqa: E402

IMPL_DEFAULT = "vibe-ic"
IMPL_LIBRELANE = "librelane"
IMPL_ORFS = "orfs"

#: Every mode name this module recognises, supported or reserved.
IMPLS = (IMPL_DEFAULT, IMPL_LIBRELANE, IMPL_ORFS)
#: The modes v1 runs. `orfs` is recognised so it can be refused BY NAME.
SUPPORTED_IMPLS = frozenset({IMPL_DEFAULT, IMPL_LIBRELANE})
#: The command-line flag that selects each non-default mode.
FLAG_FOR = {IMPL_LIBRELANE: "--librelane", IMPL_ORFS: "--orfs"}

SCHEMA = "vibe-ic/impl-mode/1"
STATE_DIR = ".vibeic-state"
RECORD_NAME = "impl-mode-v1.json"
#: Must agree with canonical_run_admission.STATE_DIR / LEDGER_NAME. Spelled
#: here rather than imported so the default path imports no admission code.
ADMISSION_LEDGER = "canonical-run-admission-v1.jsonl"

IMPL_UNKNOWN = "IMPL_UNKNOWN"
IMPL_NOT_YET_SUPPORTED = "IMPL_NOT_YET_SUPPORTED"
IMPL_MODE_CONFLICT = "IMPL_MODE_CONFLICT"
IMPL_RECORD_UNREADABLE = "IMPL_RECORD_UNREADABLE"
IMPL_IMAGE_CHANGED = "IMPL_IMAGE_CHANGED"
REASON_CLASSES = (IMPL_UNKNOWN, IMPL_NOT_YET_SUPPORTED, IMPL_MODE_CONFLICT,
                  IMPL_RECORD_UNREADABLE, IMPL_IMAGE_CHANGED)

#: The step-cache sidecars a vibe-ic producer stamps beside what it wrote
#: (phase3_one_shot_runner._PRODUCER_SIDECAR, _step_identity.SIDECAR).
PRODUCER_SIDECARS = ("producer_identity.json", "step_identity.json")
#: Flow output under these trees is implementation work; Phase 1 is not.
FLOW_OUTPUT_ROOTS = ("phase2", "phase3")

NOT_CAPTURED = "NOT CAPTURED:"
_IMAGE_NOT_GIVEN = (f"{NOT_CAPTURED} the runner that created this record "
                    "resolved no image; the first dispatch that names one "
                    "fills it")

_REQUIRED_KEYS = ("schema", "impl", "flag", "resolved_at", "resolved_by",
                  "tool_defaults", "image")


class ImplRefusal(Exception):
    """A named refusal. ``str()`` starts with the reason class."""

    def __init__(self, reason_class: str, detail: str) -> None:
        super().__init__(f"{reason_class}: {detail}")
        self.reason_class = reason_class
        self.detail = detail


def record_path(project: Path) -> Path:
    return Path(project) / STATE_DIR / RECORD_NAME


def normalise(value: Optional[str]) -> str:
    """The mode a requested value names. None/empty is the default.

    Refuses anything outside the vocabulary, and names the reserved flag when
    the request is the name that already means vibe-ic's direct deck.
    """
    if value is None or str(value).strip() == "":
        return IMPL_DEFAULT
    v = str(value).strip().lower()
    if v in IMPLS:
        return v
    if v == "openroad":
        raise ImplRefusal(
            IMPL_UNKNOWN,
            "'openroad' is not a flow mode: it already names vibe-ic's direct "
            f"OpenROAD deck. The OpenROAD-flow-scripts mode is reserved as "
            f"{FLAG_FOR[IMPL_ORFS]} (internal '{IMPL_ORFS}') and is not yet "
            "supported.")
    raise ImplRefusal(IMPL_UNKNOWN,
                      f"{value!r} is not a flow mode; known: {', '.join(IMPLS)}")


def require_supported(impl: str) -> str:
    """Refuse a recognised mode v1 does not run, naming its flag."""
    if impl not in SUPPORTED_IMPLS:
        raise ImplRefusal(
            IMPL_NOT_YET_SUPPORTED,
            f"{FLAG_FOR.get(impl, impl)} (mode '{impl}') is reserved and not yet "
            f"supported; v1 runs the default flow or {FLAG_FOR[IMPL_LIBRELANE]}.")
    return impl


def validate_record(obj: Any) -> List[str]:
    """Problems with a record object; empty when it is well formed."""
    if not isinstance(obj, dict):
        return ["record is not a JSON object"]
    problems = [f"missing key '{k}'" for k in _REQUIRED_KEYS if k not in obj]
    if obj.get("schema") != SCHEMA:
        problems.append(f"schema is {obj.get('schema')!r}, expected {SCHEMA!r}")
    impl = obj.get("impl")
    if impl not in IMPLS:
        problems.append(f"impl {impl!r} is not a mode")
    elif impl == IMPL_DEFAULT:
        # The default is represented by ABSENCE. A written default record
        # would be a second spelling of the same fact.
        problems.append("impl 'vibe-ic' is never recorded; its record is absent")
    elif obj.get("flag") != FLAG_FOR[impl]:
        problems.append(f"flag {obj.get('flag')!r} does not select {impl!r}")
    if not isinstance(obj.get("tool_defaults"), dict):
        problems.append("tool_defaults is not an object")
    else:
        for q, ans in obj["tool_defaults"].items():
            if not (isinstance(ans, dict) and "value" in ans
                    and isinstance(ans.get("source"), str) and ans["source"]):
                problems.append(f"tool_defaults[{q!r}] needs a value and a "
                                "non-empty source")
    if obj.get("image") is not None and not isinstance(obj.get("image"), str):
        problems.append("image is neither null nor a string")
    # #312/#365: an unknown image is None WITH its reason, never a bare null.
    if obj.get("image") is None and not str(
            obj.get("image_capture") or "").startswith(NOT_CAPTURED):
        problems.append("image is null without an image_capture "
                        f"'{NOT_CAPTURED} <reason>'")
    for k in ("resolved_at", "resolved_by"):
        if k in obj and not (isinstance(obj[k], str) and obj[k]):
            problems.append(f"{k} is not a non-empty string")
    return problems


def read_record(project: Path) -> Optional[Dict[str, Any]]:
    """The project's record, or None when absent (the default mode).

    A record that exists and cannot be read or validated is a refusal, never
    the default: guessing ``vibe-ic`` from a damaged record is exactly the
    in-place switch this record exists to refuse.
    """
    path = record_path(project)
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImplRefusal(IMPL_RECORD_UNREADABLE, f"{path}: {exc}") from exc
    problems = validate_record(obj)
    if problems:
        raise ImplRefusal(IMPL_RECORD_UNREADABLE,
                          f"{path}: " + "; ".join(problems))
    return obj


def recorded_impl(project: Path) -> str:
    """The project's mode: the record's, or the default when there is none."""
    rec = read_record(project)
    return rec["impl"] if rec else IMPL_DEFAULT


def admitted_impls(project: Path) -> Set[str]:
    """The modes the project's admitted canonical spans ran in.

    Read from each admission-ledger row's ``identity.dispatch_config``; a row
    without ``impl`` ran in the default mode. Empty when nothing was admitted.
    """
    ledger = Path(project) / STATE_DIR / ADMISSION_LEDGER
    if not ledger.exists():
        return set()
    modes: Set[str] = set()
    try:
        lines = ledger.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ImplRefusal(IMPL_RECORD_UNREADABLE, f"{ledger}: {exc}") from exc
    for n, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
            cfg = row["identity"]["dispatch_config"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ImplRefusal(
                IMPL_RECORD_UNREADABLE,
                f"{ledger} line {n} names no dispatch_config ({exc!r}), so the "
                "mode it ran in cannot be read") from exc
        modes.add((cfg or {}).get("impl", IMPL_DEFAULT)
                  if isinstance(cfg, dict) else IMPL_DEFAULT)
    return modes


def default_output_evidence(project: Path) -> List[str]:
    """Flow output this project already holds that no external flow made.

    The admission ledger does not see every run: the phase-3 window path
    admits into a temporary copy that is deleted, and a run that never passed
    admission leaves no row. Its OUTPUT stays, so this names it:

      * a ``provenance.jsonl`` row declaring an output under phase2/ or
        phase3/ that no external flow is attributed with (``attributed_to``
        is what `_external_flow_manifest.to_provenance_entry` writes);
      * a step-cache producer sidecar anywhere under phase2/ or phase3/.

    Phase-1 output is not implementation work and is not counted. What this
    still cannot see: a run that wrote no provenance row and no sidecar (a
    hand-copied tree). Returns at most a few examples; empty means none.
    """
    project = Path(project)
    found: List[str] = []
    prov = project / "provenance.jsonl"
    if prov.is_file():
        try:
            lines = prov.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            raise ImplRefusal(IMPL_RECORD_UNREADABLE, f"{prov}: {exc}") from exc
        for raw in lines:
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or row.get("attributed_to"):
                continue
            outs = row.get("outputs") or {}
            hit = next((o for o in (outs if isinstance(outs, dict) else ())
                        if str(o).split("/", 1)[0] in FLOW_OUTPUT_ROOTS), None)
            if hit:
                found.append(f"provenance.jsonl: {row.get('tool')} -> {hit}")
                break
    for root in FLOW_OUTPUT_ROOTS:
        base = project / root
        if not base.is_dir():
            continue
        for name in PRODUCER_SIDECARS:
            hit = next(iter(sorted(base.rglob(name))), None)
            if hit is not None:
                found.append(f"step-cache sidecar {hit.relative_to(project)}")
    return found


def _refuse_prior_history(project: Path, impl: str) -> None:
    """Refuse a NEW non-default record on a project that already ran."""
    ran = admitted_impls(project) - {impl}
    if ran:
        raise ImplRefusal(
            IMPL_MODE_CONFLICT,
            f"this invocation asks for '{impl}' but the project already "
            f"admitted canonical spans in {sorted(ran)} "
            f"({Path(project) / STATE_DIR / ADMISSION_LEDGER}). A mode is "
            "never switched in place; run a fresh project clone.")
    out = default_output_evidence(project)
    if out:
        raise ImplRefusal(
            IMPL_MODE_CONFLICT,
            f"this invocation asks for '{impl}' but the project already holds "
            f"default-flow output ({'; '.join(out)}). A mode is never "
            "switched in place; run a fresh project clone.")


def resolve(project: Path, requested: Optional[str]) -> str:
    """The mode this invocation runs in, or a named refusal.

    ``requested`` is what the invocation asked for: a mode name, or None when
    no flag was given (the default). A child runner passes what its own argv
    says; a missing flag is a request for the default and conflicts with a
    non-default project exactly like a wrong flag does.
    """
    impl = require_supported(normalise(requested))
    rec = read_record(project)
    have = rec["impl"] if rec else IMPL_DEFAULT
    # No record + a non-default request is a project whose mode is not yet
    # resolved; its history is the admission ledger, asked below.
    if (rec is not None or impl == IMPL_DEFAULT) and have != impl:
        raise ImplRefusal(
            IMPL_MODE_CONFLICT,
            f"this invocation asks for '{impl}' "
            f"({FLAG_FOR.get(impl, 'no flag')}) but the project is implemented "
            f"by '{have}' (record {record_path(project)}). A mode is never "
            "switched in place; run a fresh project clone in the other mode.")
    # A record that names this mode is the answer: it was written before any
    # flagged span ran, so history recorded after it cannot overrule it (a
    # flagged admission row that lacks `impl` would otherwise read as
    # vibe-ic and lock the project out of both modes). History is asked only
    # when there is no record yet, i.e. when a record would be created.
    if impl != IMPL_DEFAULT and rec is None:
        _refuse_prior_history(project, impl)
    return impl


def write_record(project: Path, impl: str, *, resolved_by: str,
                 image: Optional[str] = None) -> Optional[Path]:
    """Record a resolved mode. Writes NOTHING for the default; returns the path.

    Resolved once: an existing record for the same mode is kept as it is (its
    ``resolved_at`` and ``tool_defaults`` belong to the first resolution), and
    a record for a different mode is refused.
    """
    impl = require_supported(normalise(impl))
    if impl == IMPL_DEFAULT:
        have = recorded_impl(project)
        if have != IMPL_DEFAULT:
            raise ImplRefusal(IMPL_MODE_CONFLICT,
                              f"the project is implemented by '{have}'")
        return None
    existing = read_record(project)
    path = record_path(project)
    if existing is not None:
        if existing["impl"] != impl:
            raise ImplRefusal(
                IMPL_MODE_CONFLICT,
                f"the project is implemented by '{existing['impl']}'; "
                f"refusing to record '{impl}' over it")
        if image is not None and existing.get("image") is None:
            # Decision 24: the image the dispatch resolved. A record created
            # before any runner named one is FILLED once, and says so; the
            # reason it was missing is kept, never overwritten.
            filled = dict(existing)
            filled["image"] = image
            filled["image_capture"] = (
                f"FILLED by {resolved_by} at "
                f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}; "
                f"was {existing.get('image_capture')}")
            _atomic_artefact.write_json(path, filled, indent=2)
            return path
        if image is not None and image != existing.get("image"):
            # A second dispatch on ANOTHER image would mix two images'
            # artefacts under one record; it is never absorbed.
            raise ImplRefusal(
                IMPL_IMAGE_CHANGED,
                f"the project was implemented by '{impl}' on image "
                f"{existing.get('image')!r}; this dispatch resolved {image!r}. "
                "Run a fresh project clone on the new image.")
        return path
    # Creating the record is the in-place-switch decision (decision 20).
    _refuse_prior_history(project, impl)
    rec = {
        "schema": SCHEMA,
        "impl": impl,
        "flag": FLAG_FOR[impl],
        "resolved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "resolved_by": str(resolved_by),
        "tool_defaults": {},
        "image": image,
    }
    if image is None:
        rec["image_capture"] = _IMAGE_NOT_GIVEN
    problems = validate_record(rec)
    if problems:  # pragma: no cover - the constructor above is the schema
        raise ImplRefusal(IMPL_RECORD_UNREADABLE, "; ".join(problems))
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_artefact.write_json(path, rec, indent=2)
    return path


def dispatch_config_entry(impl: str) -> Dict[str, str]:
    """What a runner adds to its admission ``dispatch_config`` for ``impl``.

    Empty for the default, so a default run's admission identity is unchanged.
    """
    impl = normalise(impl)
    return {} if impl == IMPL_DEFAULT else {"impl": impl}
