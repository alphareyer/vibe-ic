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

WHERE THE MODE AND THE IMPORT COME FROM
=======================================
The mode is the W0 mode record (``_impl_flow.recorded_impl``); an absent record
is the default. The import is W0's one import manifest, read ONLY through
``_external_flow_manifest.load_manifest`` (validated against the disk) and
``segments_of``: the two-segment plan writes one manifest whose rows live in
``segments``, and a one-run manifest is one segment. A manifest that does not
validate supports no claim: every flag step is NOT_ATTRIBUTED, and the reason
says why.

chip-AGNOSTIC: flow-step ids only; no design, PDK or cell literal.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _external_flow_manifest as _M  # noqa: E402
import _impl_flow  # noqa: E402
import verdict as _V  # noqa: E402

IMPL_DEFAULT = "vibe-ic"
IMPL_LIBRELANE = "librelane"

DONE_BY_TOOL = "DONE_BY_TOOL"
MEASURED_BY_VIBEIC = "MEASURED_BY_VIBEIC"
VIBEIC = "VIBEIC"
NOT_PERFORMED = "NOT_PERFORMED"
#: Some of the step's tool steps ran and others did not (e.g. step 37:
#: KLayout.StreamOut imported, Magic.StreamOut not performed). Not red, never
#: PASS: the step is not done; the missing tool steps are named.
PARTIALLY_PERFORMED = "PARTIALLY_PERFORMED"
#: The default flow itself would not run the step for this design (its flow
#: YAML condition, e.g. `delivery_declares absent_when`): N/A, cited, never a
#: gap the flag caused.
NOT_APPLICABLE = "NOT_APPLICABLE"
#: The flag hands the step to the tool but no import of this run exists yet
#: (or the import has no rule that yields a file for it). No claim either way.
NOT_ATTRIBUTED = "NOT_ATTRIBUTED"

#: W0's import manifest, which W6 writes (librelane_import.MANIFEST_REL).
IMPORT_MANIFEST_REL = _M.MANIFEST_REL

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
    return _impl_flow.recorded_impl(Path(project))


def load_import_manifest(project: Path) -> Optional[Dict[str, Any]]:
    """W0's import manifest, validated against the disk; None when there is
    none. Raises ``_external_flow_manifest.ManifestError`` when it does not
    validate."""
    if not _M.manifest_path(project).is_file():
        return None
    return _M.load_manifest(Path(project))


def applicability(project: Path, step_id: str) -> Optional[Dict[str, str]]:
    """None when the DEFAULT flow would run ``step_id`` for this design; else
    why it would not, read by the default flow's own evaluator
    (`flow_compliance_check._check_condition` on the step's flow-YAML
    ``condition``) and citing the declaration that stood it down."""
    import _flow_yaml
    import flow_compliance_check as _F
    steps = {str(s.get("id")): s for s in
             (_flow_yaml.load(_F.DEFAULT_FLOW_DEF) or {}).get("steps") or []
             if isinstance(s, dict)}
    cond = (steps.get(str(step_id)) or {}).get("condition") or {}
    if not cond or _F._check_condition(Path(project), cond):
        return None
    dd = cond.get("delivery_declares")
    if isinstance(dd, dict) and _F._delivery_declares_absence(Path(project), dd):
        return {"declared_by": f"{dd.get('declaration')}#{dd.get('field')}",
                "reason": (f"the design's own declaration stands step "
                           f"{step_id} down (absent_when "
                           f"{dd.get('absent_when')}); the default flow "
                           "skips it by condition too")}
    return {"declared_by": f"flow condition of step {step_id}",
            "reason": (f"step {step_id}'s flow condition is not met for this "
                       "design; the default flow skips it by condition too")}


def _plugin_runs(project: Path, manifest: Dict[str, Any]
                 ) -> Dict[str, List[Dict[str, Any]]]:
    """Step class -> the runs of it each imported segment's OWN flow.log
    shows (`librelane_import.run_index`: flow.log cross-checked against each
    folder's config.json). Each flow.log must still be the one the import
    recorded; otherwise ValueError."""
    import librelane_import as _LI
    from librelane_contract import Refusal, digest
    out: Dict[str, List[Dict[str, Any]]] = {}
    for seg in _M.segments_of(manifest):
        run = Path(project) / str(seg.get("run_dir"))
        want = (seg.get("flow_status") or {}).get("flow_log_sha256")
        log = run / "flow.log"
        if not log.is_file() or (want and "sha256:" + digest(log) != want):
            raise ValueError(f"{seg.get('run_dir')}/flow.log is not the log "
                             "the import recorded")
        try:
            index = _LI.run_index(run)
        except Refusal as exc:
            raise ValueError(f"{seg.get('run_dir')}: {exc}") from None
        for ran in index:
            out.setdefault(ran.step, []).append(
                {"segment": seg.get("name"), "step_dir": ran.rel,
                 "completed": ran.completed})
    return out


def _gap(sid: str, impl: str, project: Optional[Path],
         record: Dict[str, Any]) -> Dict[str, Any]:
    """A step the flow did not (fully) perform: N/A when the default flow
    would not run it either, else FLOW_DOES_NOT_PERFORM with the remedy."""
    na = applicability(project, sid) if project is not None else None
    if na is not None:
        return {"state": NOT_APPLICABLE, "producer": impl,
                "verdict": _V.Verdict.NOT_APPLICABLE.value,
                "declared_by": na["declared_by"], "reason": na["reason"],
                **{k: v for k, v in record.items()
                   if k in ("tool_steps", "tool_steps_not_performed")}}
    return {"producer": impl, **record,
            "verdict": _V.Verdict.NOT_MEASURED.value,
            "reason_class": _V.ReasonClass.FLOW_DOES_NOT_PERFORM.value,
            "remedy": remedy(sid, impl)}


def step_producers(impl: str, manifest: Optional[Dict[str, Any]],
                   manifest_error: Optional[str] = None,
                   project: Optional[Path] = None
                   ) -> Dict[str, Dict[str, Any]]:
    """Per flow step: who produced it under ``impl``. ``{}`` for the default.

    ``manifest`` is a VALIDATED import manifest (``load_import_manifest``);
    its rows are read per segment (``segments_of``). ``manifest_error`` is why
    a manifest on disk did not validate (``manifest`` is then None, so no step
    is claimed); it becomes the NOT_ATTRIBUTED reason. ``project`` lets a gap
    be checked against the design's own applicability, and the plugin steps
    against the runs' own flow.logs; without it neither is claimed. Only the
    steps the flag changes are listed; every other step is vibe-ic's, as it is
    without the flag.
    """
    if impl == IMPL_DEFAULT:
        return {}
    if impl != IMPL_LIBRELANE:
        raise ValueError(f"no outcome mapping for impl {impl!r}")
    rows = [(seg.get("name"), r)
            for seg in (_M.segments_of(manifest) if manifest else [])
            for r in seg.get("rows") or [] if isinstance(r, dict)]
    missing: Dict[str, List[Dict[str, Any]]] = {}
    for n in (manifest or {}).get("not_performed") or []:
        if isinstance(n, dict):
            missing.setdefault(str(n.get("flow_step")), []).append(n)
    unclaimed = (f"the import manifest does not validate: {manifest_error}"
                 if manifest_error is not None else
                 "no import of this run yet" if manifest is None else None)
    out: Dict[str, Dict[str, Any]] = {}
    for sid in sorted(LIBRELANE_STEPS):
        mine = [(seg, r) for seg, r in rows if str(r.get("step_id")) == sid]
        gone = missing.get(sid, []) if manifest is not None else []
        if mine:
            kinds: Dict[str, int] = {}
            for _, r in mine:
                kinds[str(r.get("provenance"))] = kinds.get(
                    str(r.get("provenance")), 0) + 1
            done = {"tool_steps": sorted({str(r.get("tool_step_id"))
                                          for _, r in mine}),
                    "files": len(mine), "provenance": kinds}
            segs = sorted({seg for seg, _ in mine if seg is not None})
            if segs:                       # a segmented import: which run
                done["segments"] = segs
            if gone:
                # A sibling rule of this step was not performed: the step is
                # not done, and the missing tool steps are named.
                out[sid] = _gap(sid, impl, project, {
                    "state": PARTIALLY_PERFORMED, **done,
                    "tool_steps_not_performed": [str(n.get("tool_step"))
                                                 for n in gone],
                    "reason": "; ".join(str(n.get("reason") or "")
                                        for n in gone)})
            else:
                out[sid] = {"state": DONE_BY_TOOL, "producer": impl, **done}
        elif gone:
            out[sid] = _gap(sid, impl, project, {
                "state": NOT_PERFORMED,
                "tool_step": str(gone[0].get("tool_step")),
                "tool_steps_not_performed": [str(n.get("tool_step"))
                                             for n in gone],
                "reason": "; ".join(str(n.get("reason") or "")
                                    for n in gone)})
        else:
            out[sid] = {"state": NOT_ATTRIBUTED, "producer": impl,
                        "reason": unclaimed or
                        "the import names no file for this step"}
    for sid in sorted(VIBEIC_MEASURES):
        # The role is planned; the subject is claimed only once a validated
        # import of the tool's output exists.
        out[sid] = {"state": MEASURED_BY_VIBEIC, "producer": IMPL_DEFAULT}
        if manifest is not None:
            out[sid]["subject"] = f"{impl} output"
        else:
            out[sid]["role"] = "planned; no validated import of the output yet"
    # The vibe-ic plugin steps run INSIDE LibreLane (decision 12) are claimed
    # only from the runs' own flow.logs, never from the plan.
    runs: Dict[str, List[Dict[str, Any]]] = {}
    why: Optional[str] = unclaimed
    if why is None and project is None:
        why = "no project to read the runs' own flow.logs from"
    if why is None:
        try:
            runs = _plugin_runs(Path(project), manifest or {})
        except ValueError as exc:
            why = str(exc)
    for sid, step in sorted(VIBEIC_PLUGIN_STEPS.items()):
        seen = [r for r in runs.get(step, []) if r["completed"]]
        if why is not None:
            out[sid] = {"state": NOT_ATTRIBUTED, "producer": IMPL_DEFAULT,
                        "reason": why}
        elif seen:
            out[sid] = {"state": VIBEIC, "producer": IMPL_DEFAULT,
                        "disclosure": f"vibe-ic plugin step {step} run inside "
                                      f"the {impl} flow",
                        "evidence": seen}
        else:
            out[sid] = _gap(sid, impl, project, {
                "state": NOT_PERFORMED, "tool_step": step,
                "reason": (f"no imported run's own flow.log shows {step} "
                           "finishing (decision 12 keeps it as a vibe-ic "
                           f"plugin step inside the {impl} flow)")})
    return out


GAP_STATES = (NOT_PERFORMED, PARTIALLY_PERFORMED)


def not_performed_verdicts(producers: Dict[str, Dict[str, Any]],
                           names: Optional[Dict[str, str]] = None
                           ) -> List["_V.StepVerdict"]:
    """The step rows a runner publishes for steps the flow did not perform."""
    rows = []
    for sid, p in producers.items():
        if p.get("state") not in GAP_STATES:
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
    try:
        manifest, error = load_import_manifest(project), None
    except _M.ManifestError as exc:
        manifest, error = None, str(exc)
    return {"impl": impl,
            "step_producers": step_producers(impl, manifest, error, project)}


def demote_verdict(summary: Dict[str, Any]) -> None:
    """A run with a step the flow did not (fully) perform is never PASS: its
    headline becomes NOT_MEASURED(flow_does_not_perform), the way the
    runners demote a run awaiting a signed judgement. No-op by default."""
    gaps = sorted(sid for sid, p in (summary.get("step_producers") or {}).items()
                  if isinstance(p, dict) and p.get("state") in GAP_STATES)
    if gaps and summary.get("verdict") in ("PASS", "PASS_WITH_WAIVERS"):
        summary["verdict"] = _V.Verdict.NOT_MEASURED.value
        summary["reason_class"] = _V.ReasonClass.FLOW_DOES_NOT_PERFORM.value
        summary["verdict_note"] = (
            (str(summary.get("verdict_note")) + "; "
             if summary.get("verdict_note") else "")
            + f"the {summary.get('impl')} flow did not perform step(s) "
            f"{', '.join(gaps)}: run the default flow (no "
            f"--{summary.get('impl')}), or a flow that performs them")


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
                f"remedy — {p['remedy']}"
                + (f" (not performed: "
                   f"{', '.join(p['tool_steps_not_performed'])})"
                   if p.get("tool_steps_not_performed") else "")) \
            or (p.get("declared_by") and f"N/A — {p['declared_by']}") \
            or p.get("disclosure") \
            or ", ".join(p.get("tool_steps") or []) or p.get("reason") \
            or p.get("role") or ""
        md.append(f"| {sid} | {p.get('producer', '')} | {p.get('state', '')} "
                  f"| {note} |")
    md.append("")
    return md
