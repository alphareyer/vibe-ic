#!/usr/bin/env python3
"""_impl_outcomes.py — who produced each flow step under an external flow (llv1 W14).

WHY THIS EXISTS
===============
Under ``--librelane`` a step's canonical output may come from three places,
and a reader of the phase reports must be able to tell them apart:

  DONE_BY_TOOL        LibreLane performed the step; its outputs were imported
                      (``librelane_import``, W6), each a witnessed or #365 row.
  MEASURED_BY_VIBEIC  a vibe-ic program runs on LibreLane's output (the kept
                      gates: step-31 decks, IR/EM, SI, SDF/GLS, ...).
  VIBEIC              vibe-ic's own producer, unchanged by the flag.
  NOT_PERFORMED       the flag handed the step to LibreLane and THIS run of
                      LibreLane did not perform it (W6's ``not_performed``).

NOT_PERFORMED IS ITS OWN REASON, AND IT IS NEITHER RED NOR PASS
===============================================================
Owner decision 5 (2026-09-28): a gap caused by the flag gets a NEW reason
class. It is `verdict.ReasonClass.FLOW_DOES_NOT_PERFORM` on the step row
(verdict ``NOT_MEASURED``) and `_flow_reason_taxonomy.FLOW_DOES_NOT_PERFORM`
on a gate record, which is outside ``SKIP_ELIGIBLE``: the existing
``CAPABILITY_ABSENT`` would have read as a harmless skip. The reason names the
remedy: run the default flow (no flag), or a flow that performs the step.

A step whose tool run FAILED is not this class: decision 8 makes that an honest
``FAIL`` with the tool's report, decided by the driver, never here.

THE DEFAULT WRITES NOTHING
==========================
`report_fields` returns ``{}`` when the mode is ``vibe-ic``, so a default run's
phase reports and ``final_summary.md`` are byte-identical to before.

WHERE THE MODE COMES FROM
=========================
The W0 mode record (``_impl_flow.recorded_impl``). Until W0 lands on main that
module may be absent, and absent means the default, exactly as an absent record
does; `resolve_impl` says which it read.

chip-AGNOSTIC: flow-step ids only; no design, PDK or cell literal.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verdict as _V  # noqa: E402

IMPL_DEFAULT = "vibe-ic"
IMPL_LIBRELANE = "librelane"

DONE_BY_TOOL = "DONE_BY_TOOL"
MEASURED_BY_VIBEIC = "MEASURED_BY_VIBEIC"
VIBEIC = "VIBEIC"
NOT_PERFORMED = "NOT_PERFORMED"
#: The flag hands the step to the tool but no import of this run exists yet
#: (or the import has no rule that yields a file for it). No claim either way.
NOT_ATTRIBUTED = "NOT_ATTRIBUTED"

#: W6's manifest (librelane_import.MANIFEST_REL).
IMPORT_MANIFEST_REL = "phase3/librelane/import_manifest.json"

#: PLAN §2, the "Under --librelane" column, for the steps after RTL.
#: Steps LibreLane performs (segment 1: 9, 10, 14; segment 2: the rest).
LIBRELANE_STEPS = frozenset({
    "9", "10", "14", "15", "15.5ic", "17", "19", "20", "21", "22", "23",
    "26", "26.5ic", "33", "34", "37", "37.5ip"})
#: Steps a kept vibe-ic program performs ON LibreLane's output.
VIBEIC_MEASURES = frozenset({
    "12", "16", "24", "25", "27", "28", "29", "30", "31", "35", "36",
    "37.3", "37.4", "37.5ic", "DT2", "DT3", "38"})
#: vibe-ic's own plugin steps inserted INTO LibreLane's flow (orchestrator
#: decision 12), disclosed rather than attributed to LibreLane.
VIBEIC_PLUGIN_STEPS = {"18": "Vibeic.InsertSpareCells",
                       "32": "Vibeic.PostRouteRepair"}


def remedy(step_id: str, flow: str = IMPL_LIBRELANE) -> str:
    return (f"run the default flow (no --{flow}), or a flow that performs "
            f"step {step_id}")


def resolve_impl(project: Path) -> str:
    """The project's implementation flow, from the W0 record."""
    try:
        import _impl_flow
    except ImportError:            # W0 not landed: no record can exist
        return IMPL_DEFAULT
    return _impl_flow.recorded_impl(Path(project))


def load_import_manifest(project: Path) -> Optional[Dict[str, Any]]:
    path = Path(project) / IMPORT_MANIFEST_REL
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def step_producers(impl: str,
                   manifest: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Per flow step: who produced it under ``impl``. ``{}`` for the default.

    Only the steps the flag changes are listed; every other step is vibe-ic's,
    as it is without the flag.
    """
    if impl == IMPL_DEFAULT:
        return {}
    if impl != IMPL_LIBRELANE:
        raise ValueError(f"no outcome mapping for impl {impl!r}")
    rows = [r for r in (manifest or {}).get("rows") or []
            if isinstance(r, dict)]
    missing = {str(n.get("flow_step")): n for n in
               (manifest or {}).get("not_performed") or []
               if isinstance(n, dict)}
    out: Dict[str, Dict[str, Any]] = {}
    for sid in sorted(LIBRELANE_STEPS):
        mine = [r for r in rows if str(r.get("step_id")) == sid]
        if mine:
            kinds: Dict[str, int] = {}
            for r in mine:
                kinds[str(r.get("provenance"))] = kinds.get(
                    str(r.get("provenance")), 0) + 1
            out[sid] = {"state": DONE_BY_TOOL, "producer": impl,
                        "tool_steps": sorted({str(r.get("tool_step_id"))
                                              for r in mine}),
                        "files": len(mine), "provenance": kinds}
        elif sid in missing and manifest is not None:
            n = missing[sid]
            out[sid] = {"state": NOT_PERFORMED, "producer": impl,
                        "tool_step": n.get("tool_step"),
                        "verdict": _V.Verdict.NOT_MEASURED.value,
                        "reason_class": _V.ReasonClass.FLOW_DOES_NOT_PERFORM.value,
                        "reason": str(n.get("reason") or ""),
                        "remedy": remedy(sid, impl)}
        else:
            out[sid] = {"state": NOT_ATTRIBUTED, "producer": impl,
                        "reason": ("no import of this run yet"
                                   if manifest is None else
                                   "the import names no file for this step")}
    for sid in sorted(VIBEIC_MEASURES):
        out[sid] = {"state": MEASURED_BY_VIBEIC, "producer": IMPL_DEFAULT,
                    "subject": f"{impl} output"}
    for sid, step in sorted(VIBEIC_PLUGIN_STEPS.items()):
        out[sid] = {"state": VIBEIC, "producer": IMPL_DEFAULT,
                    "disclosure": f"vibe-ic plugin step {step} run inside "
                                  f"the {impl} flow"}
    return out


def not_performed_verdicts(producers: Dict[str, Dict[str, Any]],
                           names: Optional[Dict[str, str]] = None
                           ) -> List["_V.StepVerdict"]:
    """The step rows a runner publishes for steps the flow did not perform."""
    rows = []
    for sid, p in producers.items():
        if p.get("state") != NOT_PERFORMED:
            continue
        rows.append(_V.StepVerdict.not_measured(
            sid, (names or {}).get(sid, ""),
            reason_class=_V.ReasonClass.FLOW_DOES_NOT_PERFORM,
            reason=(f"{p.get('reason')}; remedy: {p.get('remedy')}"
                    .lstrip("; "))))
    return rows


def report_fields(project: Path, impl: Optional[str] = None) -> Dict[str, Any]:
    """What the phase one-shot reports add under a flag; ``{}`` by default."""
    impl = resolve_impl(project) if impl is None else impl
    if impl == IMPL_DEFAULT:
        return {}
    return {"impl": impl,
            "step_producers": step_producers(impl,
                                             load_import_manifest(project))}


def summary_lines(report: Optional[Dict[str, Any]]) -> List[str]:
    """The ``final_summary.md`` section, from a phase report. ``[]`` when the
    report carries no ``impl`` (every default run)."""
    if not isinstance(report, dict) or not report.get("impl"):
        return []
    impl = str(report["impl"])
    prod = report.get("step_producers") or {}
    md = ["## Implementation flow", "",
          f"- Flow: **{impl}** (not the default vibe-ic flow)", ""]
    md.append("| Step | Produced by | State | Note |")
    md.append("|---|---|---|---|")

    def _key(s: str):
        return [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in
                s.replace(".", " ").split()]
    for sid in sorted(prod, key=_key):
        p = prod[sid] or {}
        note = (p.get("remedy") and f"{p.get('reason_class')}: "
                f"remedy — {p['remedy']}") or p.get("disclosure") \
            or ", ".join(p.get("tool_steps") or []) or p.get("reason") or ""
        md.append(f"| {sid} | {p.get('producer', '')} | {p.get('state', '')} "
                  f"| {note} |")
    md.append("")
    return md
