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
            problems = validate_record(filled)
            if problems:
                raise ImplRefusal(IMPL_RECORD_UNREADABLE,
                                  "refusing to fill the image: "
                                  + "; ".join(problems))
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


# ─────────────────────────────────────────────────────────────────────────────
# W1: THE COMMAND LINE — flags, the knob map, the child argv, the gate
# ─────────────────────────────────────────────────────────────────────────────
#
# Every runner that can be invoked on its own (the front door and each phase
# runner it delegates to) takes the same two flags and calls `gate` once,
# before its project lock, with its REAL parser. The gate is read-only.
#
# `--librelane` selects the mode. `--orfs` is accepted by the parser only so it
# is refused BY NAME (IMPL_NOT_YET_SUPPORTED) instead of as an unknown option.
#
# A child runner is told the mode by `child_argv(project)`, which reads the
# project's record: the default has no record, so a default child argv is
# exactly what it was. A child whose flag (or missing flag) disagrees with the
# record refuses IMPL_MODE_CONFLICT — so a spawn site that forgets to forward
# the flag fails closed, never runs the default flow on a flagged project.
#
# NOT YET WIRED. No runner consumes a non-default mode yet (the consumer mode
# is W7b; admission and the step cache key on the mode in W2). Until a runner
# is listed in WIRED_RUNNERS, `gate` refuses a non-default mode with
# IMPL_NOT_YET_WIRED after every other check, and writes nothing — a flagged
# run must never quietly run the default flow under a flagged name.

HONOURED = "honoured"   # the knob keeps its meaning: vibe-ic still runs it
MAPPED = "mapped"       # the knob becomes a tool config variable (named)
REFUSED = "refused"     # the knob has no meaning under the flag: refused

IMPL_KNOB_UNSUPPORTED = "IMPL_KNOB_UNSUPPORTED"
IMPL_NOT_YET_WIRED = "IMPL_NOT_YET_WIRED"
#: The refusals the command-line gate adds to REASON_CLASSES.
CLI_REASON_CLASSES = (IMPL_KNOB_UNSUPPORTED, IMPL_NOT_YET_WIRED)

#: The parser destinations the flags themselves own (never knobs).
FLAG_DESTS = frozenset({"librelane", "orfs"})

#: Runners whose consumer mode has landed. Empty in W1; W7b adds to it.
WIRED_RUNNERS: frozenset = frozenset()

_WINDOW = ("the external flow runs one span per segment; mapping a window "
           "onto it is W7b, until then a window is refused under the flag")
_P1 = "Phase 1 runs unchanged under the flag"
_P2 = "Phase 2 steps 1-8 run unchanged under the flag"
_ANALOG = ("the analog track is out of v1 and is refused as a whole under the "
           "flag (W24); its knobs keep their meaning for the default flow")
_UI = "operator interface only; no step reads it"
_IMG = "the image is resolved at dispatch and recorded (decision 24)"
_PDK = "vibe-ic resolves the PDK; v1 admits gf180mcuD only (W24)"
_DIE = ("DIE_AREA: a die the USER or the design declared is passed to the "
        "tool (0.5ic/W9); 'auto' is vibe-ic's auto-die between the segments")
_UTIL = ("FP_CORE_UTIL (Classic) / the auto-die's utilisation (Chip), when the "
         "USER or the design declared it; a parser default is not a "
         "declaration")
#: PRECONDITION FOR W5/W7b (wave-3 review, W1): the MAPPED rows cannot be
#: implemented from a child runner's own view of "differs from my default".
#: The parents forward THEIR defaults explicitly: the front door passes
#: `--util` (0.4) and `--die-um` (auto) to phase3 on every run, and phase23
#: passes `--util 0.4` and `--die-um 1500x1500`, while phase3's own defaults
#: are 0.30 and auto. So at phase3 an undeclared value looks explicit. Before a
#: MAPPED knob reaches a tool config, the parents must forward it only when the
#: user set it, or tell the child its provenance; until then no MAPPED knob is
#: applied (every flagged run is IMPL_NOT_YET_WIRED).
MAPPED_PRECONDITION = ("parents forward their own --util/--die-um defaults "
                       "explicitly; a MAPPED knob needs its provenance from "
                       "the parent before it may reach a tool config")

#: Every option of every gated runner, by parser destination, with what it
#: means under `--librelane`. `test_every_knob_has_a_disposition` reads the
#: real parsers, so a new option without a row here is red.
KNOBS: Dict[str, Dict[str, tuple]] = {
    "vibe_ic_one_shot_runner": {
        "project": (HONOURED, "the project"),
        "top_name": (HONOURED, "vibe-ic resolves the top; the tool's "
                     "DESIGN_NAME follows it"),
        "container": (HONOURED, _IMG),
        "require_image": (HONOURED, _IMG),
        "max_rtl_repair_retries": (HONOURED, _P2),
        "lec_max_completed_rungs": (HONOURED, "step 13 stays vibe-ic LEC "
                                    "(decision 11b)"),
        "skip_hardware": (HONOURED, _P2),
        "entry_step": (REFUSED, _WINDOW),
        "exit_step": (REFUSED, _WINDOW),
        "skip_phase1": (HONOURED, _P1),
        "skip_analog": (HONOURED, _ANALOG),
        "skip_phase3": (HONOURED, "stops before the tool runs"),
        "die_um": (MAPPED, _DIE),
        "util": (MAPPED, _UTIL),
        "pdk": (HONOURED, _PDK),
        "allow_pdk_target_mismatch": (HONOURED, _PDK),
        "allow_oss_pdk_fallback": (HONOURED, _PDK),
        "ic_name": (HONOURED, "the design identity"),
        "dashboard": (HONOURED, _UI),
        "dashboard_port": (HONOURED, _UI),
        "dashboard_host": (HONOURED, _UI),
        "dashboard_full": (HONOURED, _UI),
    },
    "phase1_one_shot_runner": {
        "project": (HONOURED, "the project"),
        "ic_name": (HONOURED, _P1),
        "mode": (HONOURED, _P1),
        "second_track_only": (HONOURED, _P1),
    },
    "design_one_shot_runner": {
        "project": (HONOURED, "the project"),
        "skip_hardware": (HONOURED, _P2),
        "skip_analog": (HONOURED, _ANALOG),
        "entry_step": (REFUSED, _WINDOW),
        "exit_step": (REFUSED, _WINDOW),
        "max_rtl_repair_retries": (HONOURED, _P2),
        "lec_max_completed_rungs": (HONOURED, "step 13 stays vibe-ic LEC "
                                    "(decision 11b)"),
        "top_name": (HONOURED, "vibe-ic resolves the top"),
        "container": (HONOURED, _IMG),
        "skip_phase3": (HONOURED, "stops before the tool runs"),
        "dry_run": (HONOURED, _P2),
        "force_rtl_regen": (HONOURED, _P2),
        "refresh_only": (HONOURED, "whole-flow roll-ups only; no step runs"),
    },
    "analog_one_shot_runner": {
        "project": (HONOURED, "the project"),
        "container": (HONOURED, _ANALOG),
        "allow_deterministic_stubs": (HONOURED, _ANALOG),
        "blocks": (HONOURED, _ANALOG),
        "pdk": (HONOURED, _ANALOG),
    },
    "phase23_one_shot_runner": {
        "project": (HONOURED, "the project"),
        "top_name": (HONOURED, "vibe-ic resolves the top"),
        "container": (HONOURED, _IMG),
        "max_rtl_repair_retries": (HONOURED, _P2),
        "skip_hardware": (HONOURED, _P2),
        "force_rtl_regen": (HONOURED, _P2),
        "skip_phase2": (HONOURED, "Phase 3 alone, on the staged RTL"),
        "skip_phase3": (HONOURED, "stops before the tool runs"),
        "die_um": (MAPPED, _DIE),
        "util": (MAPPED, _UTIL),
        "pdk": (HONOURED, _PDK),
        "allow_oss_pdk_fallback": (HONOURED, _PDK),
        "detect_stable": (HONOURED, "reads the runner's own verdict streak"),
    },
    "phase3_one_shot_runner": {
        "project": (HONOURED, "the project"),
        "top_name": (HONOURED, "vibe-ic resolves the top"),
        "ic_name": (HONOURED, "the design identity"),
        "container": (HONOURED, _IMG),
        "die_um": (MAPPED, _DIE),
        "util": (MAPPED, _UTIL),
        "pdk": (HONOURED, _PDK),
        "allow_oss_pdk_fallback": (HONOURED, _PDK),
        "allow_pdk_target_mismatch": (HONOURED, _PDK),
        "spare_density": (MAPPED, "the kept Vibeic.InsertSpareCells step's "
                          "density (decision 12)"),
        "force_step": (REFUSED, "the kinds name vibe-ic's cached producers; "
                       "the tool's segments are re-run whole (W7b)"),
        "entry_step": (REFUSED, _WINDOW),
        "exit_step": (REFUSED, _WINDOW),
        "diagnostic_continue": (HONOURED, "retains diagnostic reports; under "
                                "the flag the pre-stream gate only measures"),
    },
}


def add_cli_flags(parser) -> None:
    """Add `--librelane` and the reserved `--orfs` to a runner's parser."""
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--librelane", action="store_true",
                   help="implement the project with LibreLane (v1: "
                        "gf180mcuD, digital, no macros). Resolved once per "
                        "project; a different mode needs a fresh clone.")
    g.add_argument("--orfs", action="store_true",
                   help="RESERVED for an OpenROAD-flow-scripts mode; refused "
                        "as not yet supported.")


def requested_from_args(args) -> Optional[str]:
    """The mode a parsed argv asks for; None means no flag (the default)."""
    if getattr(args, "orfs", False):
        return IMPL_ORFS
    if getattr(args, "librelane", False):
        return IMPL_LIBRELANE
    return None


def refuse_knobs(runner: str, args, parser) -> None:
    """Refuse, by name, every REFUSED knob the invocation actually set.

    "Set" is "differs from the parser's own default": a default is not a
    request, and the runner's parser is the only authority on its default.
    """
    table = KNOBS[runner]
    set_refused = []
    for dest, (disposition, why) in sorted(table.items()):
        if disposition != REFUSED:
            continue
        if getattr(args, dest, None) != parser.get_default(dest):
            set_refused.append(f"--{dest.replace('_', '-')} ({why})")
    if set_refused:
        raise ImplRefusal(
            IMPL_KNOB_UNSUPPORTED,
            f"{runner} under {FLAG_FOR[IMPL_LIBRELANE]}: "
            + "; ".join(set_refused))


def gate(project: Path, args, *, runner: str, parser) -> str:
    """Resolve this invocation's mode or refuse by name. Writes nothing.

    Order: the mode itself (unknown / reserved / in-place switch), then the
    knobs the mode cannot honour, then whether this runner consumes the mode.
    """
    if runner not in KNOBS:
        raise KeyError(f"{runner} has no knob table")
    impl = resolve(project, requested_from_args(args))
    if impl == IMPL_DEFAULT:
        return impl
    refuse_knobs(runner, args, parser)
    if runner not in WIRED_RUNNERS:
        raise ImplRefusal(
            IMPL_NOT_YET_WIRED,
            f"{runner} does not implement {FLAG_FOR[impl]} yet (the consumer "
            "mode lands in W7b; admission and the step cache key on the mode "
            "in W2). Nothing was run or recorded. Remedy: run the default "
            "flow (no flag).")
    return impl


def record_after_lock(project: Path, args, *, runner: str) -> str:
    """Record the mode `gate` resolved, under the runner's project lock.

    The default writes nothing. A non-default mode is only reachable here
    through `gate` (which refuses it until the runner is wired), and is
    recorded once: every later runner reads it through `child_argv`, the
    admission `dispatch_config` and the step cache.
    """
    impl = require_supported(normalise(requested_from_args(args)))
    write_record(project, impl, resolved_by=runner)
    return impl


def child_argv(project: Path) -> List[str]:
    """The flag a spawned runner must carry for this project: [] by default."""
    impl = recorded_impl(project)
    return [] if impl == IMPL_DEFAULT else [FLAG_FOR[impl]]


def gate_or_exit(project: Path, args, *, runner: str, parser) -> Optional[int]:
    """`gate` for a runner's main(): the exit code to return on a refusal
    (2, with the reason on stderr), or None to proceed."""
    try:
        gate(project, args, runner=runner, parser=parser)
    except ImplRefusal as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    return None


# ─────────────────────────────────────────────────────────────────────────────
# W9: 0.5ic UNDER A FLAG — the declaration stays the owner's
# ─────────────────────────────────────────────────────────────────────────────
#
# Decision 6: the tape-out declaration is vibe-ic's and owner-answered,
# "NOT_DETERMINED, never a default". Under a non-default mode no ANSWER the run
# derives, and nothing the tool assumes, is written into it. The only
# declaration write under a flag is the PDK-measured database unit, identical
# to the default flow (orchestrator ruling, 2026-09-28):
# `publish_database_unit_declaration` merges vibe-ic's own measurement of the
# PDK into the TECHNOLOGY section -- not a tool default and not an owner
# answer, and byte-identical with or without the flag -- so decision 6
# ("LibreLane's PDK defaults never go into the declaration") holds. Where the design
# declares nothing, the value the flow APPLIED (the run's own die from the
# auto-die, a LibreLane PDK default, ...) is recorded here, per question, as
# {value, source, recorded_by} in the mode record's `tool_defaults`, beside the
# config provenance that already names each value's source. A consumer that
# needs the value asks `applied_answer`: the declaration first, then (under the
# flag only) this record, else NOT_DETERMINED with the reason.

IMPL_APPLIED_CONFLICT = "IMPL_APPLIED_CONFLICT"
IMPL_DECLARATION_UNREADABLE = "IMPL_DECLARATION_UNREADABLE"

#: Config keys that are NOT 0.5ic questions but whose applied value is still
#: a tool default worth recording (decision 6). Recorded under `config:<KEY>`,
#: so nothing mistakes them for an answer the declaration could give.
CONFIG_ONLY_KEYS = ("FP_CORE_UTIL",)
CONFIG_PREFIX = "config:"


def config_question(key: str, deliverable: Any) -> Optional[str]:
    """The 0.5ic question a resolved config key answers for THIS delivery.

    The size rectangle is `macro_area_um` on a HARDMACRO and `die_area_um` on
    a DIE (vibe-ic#2118); the core and the sizing are DIE-only questions; an
    undeclared deliverable owns neither name, so the rectangle is not
    recorded under a guess.
    """
    import _tapeout_declaration as TD  # noqa: PLC0415
    if key == "DESIGN_NAME":
        return "top_cell"
    if key == "DIE_AREA":
        return {TD.DELIVERABLE_DIE: "die_area_um",
                TD.DELIVERABLE_HARDMACRO: "macro_area_um"}.get(deliverable)
    if key in ("CORE_AREA", "FP_SIZING") and deliverable == TD.DELIVERABLE_DIE:
        return {"CORE_AREA": "core_area_um", "FP_SIZING": "fp_sizing"}[key]
    if key in CONFIG_ONLY_KEYS:
        return CONFIG_PREFIX + key
    return None


def _declaration(project: Path) -> Optional[Dict[str, Any]]:
    """The declaration, None when absent; an unreadable one is REFUSED -- an
    unread declaration is not an empty one, and reading it as NOT_DETERMINED
    would let an applied value sit over an answer the owner may have given."""
    import _tapeout_declaration as TD  # noqa: PLC0415
    path = Path(project) / TD.DECLARATION_REL
    if not path.is_file():
        return None
    doc, err = TD.load(path)
    if err is not None or not isinstance(doc, dict):
        raise ImplRefusal(IMPL_DECLARATION_UNREADABLE,
                          f"{path}: {err or 'not a JSON object'}")
    return doc


def _declared(project: Path, question: str) -> Any:
    import _tapeout_declaration as TD  # noqa: PLC0415
    doc = _declaration(project)
    return TD.NOT_DETERMINED if doc is None else TD.answer(doc, question)


def _withheld(project: Path, question: str) -> bool:
    """A declared rectangle `declaration_config` does not emit as the truth:
    a die whose `fp_sizing` is `relative` was DERIVED from a utilisation, and
    a question the declared deliverable does not owe is not its answer."""
    import _tapeout_declaration as TD  # noqa: PLC0415
    if question.startswith(CONFIG_PREFIX):
        return False
    if (question in ("die_area_um", "core_area_um")
            and _declared(project, "fp_sizing") == "relative"):
        return True
    q = TD.question(question)
    return q is not None and not TD.applicable(
        q, _declared(project, "deliverable"))


def _answered(project: Path, question: str) -> bool:
    import _tapeout_declaration as TD  # noqa: PLC0415
    if question.startswith(CONFIG_PREFIX):
        return False
    return (TD.is_answered(_declared(project, question))
            and not _withheld(project, question))


def record_applied(project: Path, answers: Dict[str, Dict[str, Any]], *,
                   recorded_by: str,
                   outrank: Optional[Dict[str, str]] = None) -> List[str]:
    """Record applied answers in the mode record; returns the questions written.

    ``answers`` maps question -> {"value", "source"}. Only under a recorded
    non-default mode (the default has no record, and writes nothing). A
    question the declaration answers is not recorded -- the owner's answer
    outranks anything applied -- unless ``outrank`` names it with the reason
    the publisher already decided it (the physical top a wrapper supersedes).
    A declared answer `declaration_config` withholds is not an answer here
    either. A recorded value is kept; a DIFFERENT one is IMPL_APPLIED_CONFLICT,
    and the caller must turn that refusal into a verdict.
    """
    rec = read_record(project)
    if rec is None:            # the default has no record: it writes nothing
        return []
    outrank = dict(outrank or {})
    table = dict(rec["tool_defaults"])
    written: List[str] = []
    for question, ans in sorted(answers.items()):
        if not (isinstance(ans, dict) and "value" in ans
                and isinstance(ans.get("source"), str) and ans["source"].strip()):
            raise ImplRefusal(IMPL_RECORD_UNREADABLE,
                              f"applied {question!r} needs a value and a source")
        source = ans["source"]
        if _answered(project, question):
            if question not in outrank:
                continue
            source += f" (outranks the declared answer: {outrank[question]})"
        old = table.get(question)
        if old is not None:
            if old.get("value") != ans["value"]:
                raise ImplRefusal(
                    IMPL_APPLIED_CONFLICT,
                    f"{question}: recorded {old.get('value')!r} ({old.get('source')}) "
                    f"vs {ans['value']!r} ({ans['source']})")
            continue
        table[question] = {"value": ans["value"], "source": source,
                           "recorded_by": str(recorded_by)}
        written.append(question)
    if written:
        new = dict(rec, tool_defaults=table)
        problems = validate_record(new)
        if problems:  # pragma: no cover - the entries above are the schema
            raise ImplRefusal(IMPL_RECORD_UNREADABLE, "; ".join(problems))
        _atomic_artefact.write_json(record_path(project), new, indent=2)
    return written


def record_applied_config(project: Path, config: Dict[str, Any],
                          sources: Dict[str, str], *, recorded_by: str,
                          resolved_config: Optional[Path] = None) -> List[str]:
    """Record the values a resolved tool config applies, per question.

    A key vibe-ic emitted carries the config provenance's source; one the
    declaration gave is the owner's answer and is skipped. A key LibreLane
    SUPPLIED itself has no emitted source: it is cited as the tool's resolved
    default, by the resolved config's path and sha256 (``resolved_config``);
    without that file there is nothing to cite and the key is not recorded.
    A key the tool left unset (None) was not applied and is not recorded.
    """
    import _tapeout_declaration as TD  # noqa: PLC0415
    decl = TD.DECLARATION_REL.removesuffix(".json")
    deliverable = _declared(project, "deliverable")
    cite = None
    if resolved_config is not None and Path(resolved_config).is_file():
        import hashlib  # noqa: PLC0415
        digest = hashlib.sha256(Path(resolved_config).read_bytes()).hexdigest()
        cite = (f"librelane resolved default ({resolved_config} "
                f"sha256:{digest})")
    answers = {}
    for key in sorted(config):
        value = config[key]
        question = config_question(key, deliverable)
        if question is None or value is None:
            continue
        source = str(sources.get(key) or "")
        if source.startswith(decl):
            continue
        source = source or cite or ""
        if not source:
            continue
        answers[question] = {"value": value, "source": source}
    return record_applied(project, answers, recorded_by=recorded_by)


def recorded_applied(project: Path, question: str) -> Optional[tuple]:
    """(value, source) from the mode record ONLY, or None. For a consumer that
    has already applied its own rule to the declaration and needs just what
    this flow applied (librelane_step37's finishing core)."""
    rec = read_record(project)
    if rec is None:
        return None
    ans = rec["tool_defaults"].get(question)
    if ans is None:
        return None
    return ans["value"], (f"{record_path(project)} tool_defaults.{question}: "
                          f"{ans['source']}")


def applied_answer(project: Path, question: str) -> tuple:
    """(value, source) for a 0.5ic question: the declaration's answer (unless
    `declaration_config` withholds it); else, under a recorded non-default
    mode, the value the flow applied; else (NOT_DETERMINED, why). The default
    flow never reads the record; an unreadable declaration is refused."""
    import _tapeout_declaration as TD  # noqa: PLC0415
    if _answered(project, question):
        return (_declared(project, question),
                f"{TD.DECLARATION_REL}.answers.{question}")
    got = recorded_applied(project, question)
    if got is not None:
        return got
    rec = read_record(project)
    if rec is not None:        # a record always names a non-default mode
        return TD.NOT_DETERMINED, (f"not declared, and the {rec['impl']} run "
                                   f"recorded no applied {question}")
    return TD.NOT_DETERMINED, "not declared"
