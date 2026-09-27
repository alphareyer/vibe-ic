#!/usr/bin/env python3
"""librelane_import.py — import a LibreLane run into the canonical tree (llv1 W6).

Under ``--librelane`` the layout is LibreLane's. vibe-ic's gates still read the
canonical tree (``phase2/stage2/synth``, ``phase3/stage3/pnr``, ...), so every
file a gate reads must arrive there from the run that produced it, bound to it
by sha256, with a provenance row that says who produced it (W19).

WHAT IT KEYS ON
---------------
The run's OWN records, never folder ordinals:
  * ``flow.log`` lists every step LibreLane started, in order, by instance id
    (``_tool_log_provenance.flow_log_steps``, a calibrated reader);
  * each folder's ``config.json`` names the step class that wrote it; the two
    must agree (an instance id is the class id, or the class id plus ``-N``).
A rule names a step CLASS and takes its LAST completed run in flow order. A
step counts as completed only when the flow started another step after it.

WHAT IT WRITES
--------------
* The canonical files, COPIED (never symlinked; a symlink at a destination is
  replaced, not written through). State views go through
  ``librelane_contract.handoff_to_direct`` with a ``path_map`` from the path the
  run recorded to where the run now lives; a view recorded by an EARLIER step
  is refused, since the row would attribute it to the wrong step.
* One provenance row per imported step: a witnessed row
  (``_tool_log_provenance.witnessed_row``, decision 4a) when the step's own
  tool logs support one, else a #365 back-fill (``reconstructed: true``). Each
  row gets the artefact-derived measurement ``_runner_measurement`` states.
* ``phase3/stage3/pnr/openroad.log`` assembled from the OpenROAD step logs in
  flow order. Every inserted section is bracketed by a marker naming its source
  log and sha256; the assembled file is itself a back-fill row.
* ``phase3/librelane/import_manifest.json``: one row per imported file, in the
  field names of the W0 import-manifest schema (flow step, canonical path,
  tool-run path, sha256 on both sides, tool, tool step id, the source log that
  witnessed it, exit code, measurement), plus every rule whose step this run
  did not perform.

WHAT IT DOES NOT DO
-------------------
Reports whose grammar vibe-ic's own consumers were not measured against
(LibreLane's STA summaries, DRC/LVS reports, antenna reports) go under
``reports/phase3/librelane/<flow step>/``, never under a sign-off name: the
kept vibe-ic decks own those (decision 11g). It judges nothing: a step this
flow did not do is listed in ``not_performed`` for the verdict layer (W14).

chip-AGNOSTIC: no design, PDK, corner or cell literal. The top comes from the
run's own ``resolved.json``; corners come from the state.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _path_layout as _pl  # noqa: E402
import _runner_measurement  # noqa: E402
import _tool_log_provenance as _tlp  # noqa: E402
from _atomic_artefact import write_json, write_text  # noqa: E402
from librelane_contract import Refusal, _load, digest, handoff_to_direct  # noqa: E402

FLOW = "librelane"
MANIFEST_REL = "phase3/librelane/import_manifest.json"
RECEIPT_DIR_REL = "reports/phase3/librelane_import"
OPENROAD_LOG_MARK_BEGIN = "# >>> LIBRELANE"
OPENROAD_LOG_MARK_END = "# <<< LIBRELANE"
_PNR_STAGE_MARKER = "PNR_STAGE:"

#: Step class -> the PnR stage marker the log readers already key on. Only
#: classes whose work is the same stage are marked.
_STAGE_OF = {
    "OpenROAD.Floorplan": "floorplan",
    "OpenROAD.GlobalPlacement": "placement",
    "OpenROAD.CTS": "cts",
    "OpenROAD.ResizerTimingPostCTS": "hold_repair",
    "OpenROAD.GlobalRouting": "global_route",
    "OpenROAD.DetailedRouting": "detailed_route",
}

Dest = Callable[[Path, str, str], Path]


@dataclass(frozen=True)
class View:
    """A state view (``def``, ``spef:*``) the step itself wrote."""
    key: str
    dest: Dest            # (project, top, corner-or-"") -> path


@dataclass(frozen=True)
class Files:
    """Files the step wrote in its folder, by glob relative to it."""
    pattern: str
    dest: Dest            # (project, top, path-relative-to-step) -> path


@dataclass(frozen=True)
class Rule:
    flow_step: str
    step: str             # LibreLane step CLASS id
    sources: Tuple[Any, ...]


def _ll_reports(step: str) -> Dest:
    return lambda p, top, rel: _pl.reports_phase3_dir(p) / "librelane" / step / rel


def _corner_spef(p: Path, top: str, corner: str) -> Path:
    return _pl.extracted_dir(p) / "spef_corners" / f"{top}.{corner.strip('*_')}.spef"


IMPORT_RULES: Tuple[Rule, ...] = (
    Rule("9", "Yosys.Synthesis", (
        View("nl", lambda p, top, _: _pl.synth_dir(p) / "netlist.v"),
        Files("reports/stat.json", _ll_reports("9")),
        Files("reports/stat.rpt", _ll_reports("9")))),
    Rule("10", "OpenROAD.STAPrePNR", (
        Files("summary.rpt", _ll_reports("10")),
        Files("*/*.rpt", _ll_reports("10")))),
    # Floorplan + PDN: the state after the grid is generated.
    Rule("15", "OpenROAD.GeneratePDN", (
        View("def", lambda p, top, _: _pl.pnr_dir(p) / "floorplan.def"),
        View("odb", lambda p, top, _: _pl.pnr_dir(p) / "librelane_floorplan.odb"))),
    Rule("15.5ic", "OpenROAD.PadRing", (
        View("def", lambda p, top, _: _pl.pnr_dir(p) / "padring.def"),)),
    Rule("17", "OpenROAD.DetailedPlacement", (
        View("def", lambda p, top, _: _pl.pnr_dir(p) / "placed.def"),
        View("odb", lambda p, top, _: _pl.pnr_dir(p) / "librelane_placed.odb"))),
    Rule("19", "OpenROAD.CTS", (
        View("def", lambda p, top, _: _pl.pnr_dir(p) / "post_cts.def"),
        Files("cts.rpt", lambda p, top, _: _pl.cts_dir(p) / "clock_tree.rpt"))),
    Rule("20", "OpenROAD.ResizerTimingPostCTS", (
        View("def", lambda p, top, _: _pl.pnr_dir(p) / "post_hold.def"),
        View("odb", lambda p, top, _: _pl.pnr_dir(p) / "post_hold.odb"))),
    Rule("21", "OpenROAD.DetailedRouting", (
        View("def", lambda p, top, _: _pl.pnr_dir(p) / "routed.def"),
        View("odb", lambda p, top, _: _pl.pnr_dir(p) / "librelane_routed.odb"),
        # the router's own marker report, where the route arm hands it
        Files("*.drc", lambda p, top, _: _pl.pnr_dir(p) / "routed_router.drc.rpt"))),
    Rule("22", "OpenROAD.RCX", (
        View("spef:*", _corner_spef),
        View("spef:nom_*", lambda p, top, _: _pl.extracted_dir(p) / f"{top}.spef"))),
    Rule("23", "OpenROAD.STAPostPNR", (
        Files("summary.rpt", _ll_reports("23")),
        Files("*/*.rpt", _ll_reports("23")))),
    Rule("26", "OpenROAD.CheckAntennas", (
        Files("reports/*.rpt", _ll_reports("26")),)),
    Rule("31", "Magic.DRC", (Files("reports/*", _ll_reports("31")),)),
    Rule("31", "KLayout.DRC", (Files("reports/*", _ll_reports("31")),)),
    Rule("31", "Netgen.LVS", (Files("reports/*", _ll_reports("31")),)),
    Rule("34", "OpenROAD.FillInsertion", (
        View("def", lambda p, top, _: _pl.pnr_dir(p) / "filled.def"),)),
    Rule("37", "Magic.StreamOut", (
        View("gds", lambda p, top, _: _pl.gds_dir(p) / f"{top}.gds"),
        Files("magic-streamout.log",
              lambda p, top, _: _pl.pnr_dir(p) / "magic_stream_out.log"))),
    Rule("37", "KLayout.StreamOut", (
        View("klayout_gds",
             lambda p, top, _: _pl.pnr_dir(p) / f"{top}.klayout_stream_out.gds"),)),
    Rule("37.5ip", "Magic.WriteLEF", (
        View("lef", lambda p, top, _: _pl.gds_dir(p).parent / "hardmacro" / f"{top}.lef"),)),
)

_INSTANCE_SUFFIX = re.compile(r"-\d+$")


def _class_of(instance_id: str) -> str:
    return _INSTANCE_SUFFIX.sub("", instance_id)


@dataclass
class Ran:
    """One step the run started, located and cross-checked."""
    instance: str
    step: str             # class id from the folder's own config.json
    rel: str              # folder relative to the run directory
    folder: Path
    completed: bool


def run_index(run_dir: Path) -> List[Ran]:
    """Every step the run's own flow.log started, in order, cross-checked
    against the folder's config.json. Refuses on any disagreement."""
    flow_log = run_dir / "flow.log"
    if not flow_log.is_file():
        raise Refusal("LL_IMPORT_NO_FLOW_LOG", str(flow_log))
    started = _tlp.flow_log_steps(flow_log.read_text(errors="replace"))
    if not started:
        raise Refusal("LL_IMPORT_NO_STEPS", f"{flow_log} names no started step")
    out: List[Ran] = []
    for i, (instance, parts) in enumerate(started):
        if run_dir.name not in parts:
            raise Refusal("LL_IMPORT_FOREIGN_STEP",
                          f"{instance} ran at {'/'.join(parts)}, outside "
                          f"the run {run_dir.name}")
        k = len(parts) - 1 - parts[::-1].index(run_dir.name)
        rel = "/".join(parts[k + 1:])
        folder = run_dir / rel
        config = folder / "config.json"
        if not rel or not config.is_file():
            raise Refusal("LL_IMPORT_STEP_FOLDER_MISSING",
                          f"{instance}: {folder}")
        recorded = (_load(config).get("meta") or {}).get("step")
        if recorded != _class_of(instance):
            raise Refusal("LL_IMPORT_STEP_MISMATCH",
                          f"flow.log started {instance} in {rel}, whose "
                          f"config.json names {recorded!r}")
        out.append(Ran(instance, recorded, rel, folder,
                       completed=i + 1 < len(started)))
    return out


def _duration_ms(folder: Path) -> Optional[int]:
    rt = folder / "runtime.txt"
    try:
        h, m, s = rt.read_text().strip().split(":")
        return int(round((int(h) * 3600 + int(m) * 60 + float(s)) * 1000))
    except (OSError, ValueError):
        return None


def _copy(src: Path, dst: Path) -> None:
    """Copy bytes; a symlink at the destination is replaced, never followed."""
    if src.is_symlink():
        raise Refusal("LL_IMPORT_SOURCE_SYMLINK", str(src))
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".import.tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)
    if digest(src) != digest(dst):
        raise Refusal("LL_IMPORT_COPY_MISMATCH", f"{src} -> {dst}")


def _recorded_root(value: str, rel: str) -> Optional[str]:
    marker = "/" + rel.strip("/") + "/"
    return value[:value.index(marker)] if marker in value else None


def _state_views(state: Dict[str, Any], key: str) -> List[Tuple[str, str]]:
    """``(view, corner)`` pairs a View source names; ``spef:*`` expands."""
    base, _, corner = key.partition(":")
    value = state.get(base)
    if not corner:
        return [(base, "")] if isinstance(value, str) and value else []
    if not isinstance(value, dict):
        return []
    if corner == "*":
        return [(f"{base}:{c}", c) for c in sorted(value)]
    return [(key, corner)] if corner in value else []


def _source_log(ran: Ran, tool_file: Path) -> Optional[Path]:
    """The transcript of the process that wrote ``tool_file``: a log in the
    file's own directory (a corner subfolder has its own), else the step's
    main log, which LibreLane names after the step folder."""
    logs = _step_logs(ran.folder)
    here = [l for l in logs if l.parent == tool_file.parent]
    if len(here) == 1:
        return here[0]
    slug = re.sub(r"^\d+-", "", ran.folder.name) + ".log"
    main = [l for l in logs if l.parent == ran.folder and l.name == slug]
    if main:
        return main[0]
    top = [l for l in logs if l.parent == ran.folder]
    return top[0] if len(top) == 1 else None


def _file_row(project: Path, ran: Ran, tool_file: Path, dest: Path,
              view: Optional[str] = None) -> Dict[str, Any]:
    """One manifest row, in the field names of the W0 import-manifest schema
    (``_external_flow_manifest``, lane llf). ``measurement`` is the
    artefact-derived record, or None when nothing can be stated about this
    file — never a guessed one. The flow step and ``exit_code`` are filled in
    by the caller."""
    log = _source_log(ran, tool_file)
    rel = dest.relative_to(project).as_posix()
    row: Dict[str, Any] = {
        "canonical_path": rel,
        "tool_run_path": tool_file.relative_to(project).as_posix(),
        "canonical_sha256": "sha256:" + digest(dest),
        "tool_run_sha256": "sha256:" + digest(tool_file),
        "flow": FLOW,
        "tool": _tlp.underlying_tool(ran.instance),
        "tool_step_id": ran.instance,
        "source_log": log.relative_to(project).as_posix() if log else None,
        "source_log_sha256": ("sha256:" + digest(log)) if log else None,
        "measurement": _runner_measurement.derive(
            project, rel, _tlp.underlying_tool(ran.instance) or ""),
    }
    if view:
        row["view"] = view
    return row


def _import_step(project: Path, run_dir: Path, ran: Ran, rule: Rule, top: str,
                 receipts: Path) -> Tuple[Dict[str, Path], List[Dict[str, Any]]]:
    state_path = ran.folder / "state_out.json"
    state = _load(state_path) if state_path.is_file() else {}
    outputs: Dict[str, Path] = {}
    rows: List[Dict[str, Any]] = []
    receipt = receipts / f"{rule.flow_step}_{ran.instance}.json"
    for src in rule.sources:
        if isinstance(src, View):
            pairs = _state_views(state, src.key)
            if not pairs:
                raise Refusal("LL_IMPORT_VIEW_MISSING",
                              f"{ran.instance} state has no {src.key}")
            targets: Dict[str, Path] = {}
            path_map: Dict[str, str] = {}
            for view, corner in pairs:
                base, _, c = view.partition(":")
                value = state[base][c] if c else state[base]
                root = _recorded_root(value, f"{run_dir.name}/{ran.rel}")
                if root is None:
                    raise Refusal("LL_IMPORT_VIEW_NOT_OWN",
                                  f"{ran.instance} {view} = {value}: written by "
                                  "another step, not by this one")
                path_map[root + "/" + run_dir.name] = str(run_dir)
                targets[view] = src.dest(project, top, corner)
            if len(set(path_map)) != 1:
                raise Refusal("LL_IMPORT_VIEW_ROOTS", f"{ran.instance}: {path_map}")
            doc = handoff_to_direct(
                state_path, targets,
                receipt.with_name(f"{receipt.stem}_{src.key.replace(':', '_').replace('*', 'all')}.json"),
                path_map=path_map)
            for view, row in doc["views"].items():
                dest = Path(row["dest"])
                outputs[dest.relative_to(project).as_posix()] = \
                    Path(row["source"]).resolve()
                rows.append(_file_row(project, ran, Path(row["source"]).resolve(),
                                      dest, view=view))
        else:
            found = sorted(p for p in ran.folder.glob(src.pattern) if p.is_file())
            if not found:
                raise Refusal("LL_IMPORT_FILE_MISSING",
                              f"{ran.instance}: no {src.pattern} in {ran.rel}")
            for path in found:
                rel = path.relative_to(ran.folder).as_posix()
                dest = src.dest(project, top, rel)
                _copy(path, dest)
                outputs[dest.relative_to(project).as_posix()] = path
                rows.append(_file_row(project, ran, path, dest))
    files = [r for r in rows if "view" not in r]
    if files:
        write_json(receipt.with_name(f"{receipt.stem}_files.json"),
                   {"step": ran.instance, "folder": ran.rel, "files": files})
    return outputs, rows


def _step_logs(folder: Path) -> List[Path]:
    """The tool transcripts the step itself wrote (its folder and corner
    subfolders), excluding nested sub-steps, which have their own config."""
    logs = []
    for p in sorted(folder.rglob("*.log")):
        parent = p.parent
        nested = False
        while parent != folder:
            if (parent / "config.json").is_file():
                nested = True
                break
            parent = parent.parent
        if not nested and p.is_file() and not p.is_symlink():
            logs.append(p)
    return logs


def _provenance_row(project: Path, run_dir: Path, ran: Ran,
                    outputs: Dict[str, Path]) -> Dict[str, Any]:
    """``outputs`` maps each canonical path to the file the step wrote."""
    try:
        row = _tlp.witnessed_row(
            project, flow=FLOW, step_id=ran.instance, run_dir=run_dir,
            step_dir=ran.rel, outputs=outputs,
            duration_ms=_duration_ms(ran.folder))
    except _tlp.WitnessRefused as exc:
        # #365: what the runner could not see being written is a back-fill.
        row = {"tool": _tlp.underlying_tool(ran.instance) or FLOW,
               "command": f"librelane step {ran.instance}", "exit_code": 0,
               "duration_ms": None, "reconstructed": True,
               "timestamp": _dt.datetime.now(_dt.timezone.utc)
               .strftime("%Y-%m-%dT%H:%M:%SZ"),
               "outputs": {rel: "sha256:" + digest(project / rel)
                           for rel in outputs},
               "note": f"imported, not witnessed: {exc}"}
    return _runner_measurement.attach(project, row)


def _pnr_span(index: List[Ran]) -> List[Ran]:
    """The OpenROAD steps of the place-and-route session, in flow order.

    vibe-ic's ``openroad.log`` is the PnR session transcript (floorplan through
    routing), so the span runs from the first ``OpenROAD.Floorplan`` up to the
    first ``OpenROAD.RCX`` after it. The STA steps inside it are separate
    OpenSTA sessions with their own reports, and stay out of the transcript.
    """
    ids = [r.step for r in index]
    if "OpenROAD.Floorplan" not in ids:
        return []
    start = ids.index("OpenROAD.Floorplan")
    stop = next((i for i in range(start, len(ids)) if ids[i] == "OpenROAD.RCX"),
                len(ids))
    return [r for r in index[start:stop]
            if _tlp.underlying_tool(r.instance) == "openroad"
            and not r.step.startswith("OpenROAD.STA")]


def assemble_openroad_log(project: Path, run_dir: Path, index: List[Ran],
                          dest: Path) -> Optional[Dict[str, Any]]:
    """The PnR-span step transcripts in flow order, each bracketed by a marker
    that names its source log and sha256."""
    parts: List[str] = []
    cited: List[Dict[str, str]] = []
    for ran in _pnr_span(index):
        stage = _STAGE_OF.get(ran.step)
        logs = _step_logs(ran.folder)
        if stage and logs:
            parts.append(f"{_PNR_STAGE_MARKER} {stage}\n")
        for log in logs:
            rel, sha = log.relative_to(project).as_posix(), "sha256:" + digest(log)
            text = log.read_text(errors="replace")
            parts.append(f"{OPENROAD_LOG_MARK_BEGIN} {ran.instance} {rel} {sha}\n")
            parts.append(text if text.endswith("\n") else text + "\n")
            parts.append(f"{OPENROAD_LOG_MARK_END} {ran.instance}\n")
            cited.append({"path": rel, "sha256": sha})
    if not cited:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_symlink():
        dest.unlink()
    write_text(dest, "".join(parts))
    return {"path": dest.relative_to(project).as_posix(),
            "sha256": "sha256:" + digest(dest),
            "sources": cited}


def import_run(project: Path, run_dir: Path) -> Dict[str, Any]:
    """Import every rule's step from ``run_dir`` (inside ``project``)."""
    project = Path(project).resolve()
    run_dir = Path(run_dir).resolve()
    try:
        run_dir.relative_to(project)
    except ValueError:
        raise Refusal("LL_IMPORT_RUN_OUTSIDE_PROJECT", str(run_dir))
    resolved = run_dir / "resolved.json"
    top = _load(resolved).get("DESIGN_NAME") if resolved.is_file() else None
    if not isinstance(top, str) or not top:
        raise Refusal("LL_IMPORT_NO_DESIGN_NAME", str(resolved))
    index = run_index(run_dir)
    receipts = project / RECEIPT_DIR_REL
    manifest: Dict[str, Any] = {
        "schema": "vibe-ic/librelane-import/1", "flow": FLOW,
        "run_dir": run_dir.relative_to(project).as_posix(), "top": top,
        "flow_log_sha256": digest(run_dir / "flow.log"),
        "rows": [], "not_performed": []}
    prov: List[Dict[str, Any]] = []
    for rule in IMPORT_RULES:
        ran = [r for r in index if r.step == rule.step]
        if not ran:
            manifest["not_performed"].append({
                "flow_step": rule.flow_step, "tool_step": rule.step,
                "reason": f"the run's own flow.log never started {rule.step}"})
            continue
        last = ran[-1]
        if not last.completed:
            raise Refusal("LL_IMPORT_STEP_NOT_COMPLETED",
                          f"{last.instance} is the last step the run started")
        outputs, rows = _import_step(project, run_dir, last, rule, top, receipts)
        row = _provenance_row(project, run_dir, last, outputs)
        prov.append(row)
        kind = "witnessed" if row.get("reconstructed") is False else "reconstructed"
        for r in rows:
            r.update({"step_id": rule.flow_step, "exit_code": row["exit_code"],
                      "timestamp": row["timestamp"], "provenance": kind})
            manifest["rows"].append(r)
    log = assemble_openroad_log(project, run_dir, index,
                                _pl.pnr_dir(project) / "openroad.log")
    if log is not None:
        manifest["openroad_log"] = log
        prov.append(_runner_measurement.attach(project, {
            "tool": "openroad", "command": "librelane_import.assemble_openroad_log",
            "exit_code": 0, "duration_ms": None, "reconstructed": True,
            "timestamp": _dt.datetime.now(_dt.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "outputs": {log["path"]: log["sha256"]},
            "note": "assembled from LibreLane step logs; every section's "
                    "marker cites its source log and sha256"}))
    with (project / "provenance.jsonl").open("a") as fh:
        for row in prov:
            fh.write(json.dumps(row) + "\n")
    write_json(project / MANIFEST_REL, manifest)
    return manifest


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project", type=Path)
    ap.add_argument("run_dir", type=Path)
    a = ap.parse_args(argv)
    try:
        doc = import_run(a.project, a.run_dir)
    except Refusal as exc:
        print(f"REFUSED {exc.code}: {exc}", file=sys.stderr)
        return 1
    print(f"imported {len(doc['rows'])} file(s); "
          f"{len(doc['not_performed'])} rule(s) not performed by this run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
