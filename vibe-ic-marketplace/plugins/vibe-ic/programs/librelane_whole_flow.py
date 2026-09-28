#!/usr/bin/env python3
"""librelane_whole_flow.py — the `--librelane` two-segment driver (llv1 W5).

WHY TWO SEGMENTS
================
vibe-ic's contract makes floorplan wait for the equivalence check (step 13)
and the synthesis handoff gate (step 14), and on the chip path three inputs of
the layout exist only once the synthesised netlist does: the die, the pad
wrapper and the SDC. So LibreLane runs twice:

  segment 1  `--to Checker.NetlistAssignStatements`: lint + synthesis + the
             synthesis checkers, on the CORE (DESIGN_NAME = the core).
  between    vibe-ic: steps 11-14, the die, the pad wrapper, the SDC.
  segment 2  `--from OpenROAD.CheckSDCFiles --with-initial-state`: everything
             from pre-layout STA to GDS and sign-off, on the LAYOUT top (the
             chip top on the chip path, the core otherwise).

`--from OpenROAD.Floorplan` would skip OpenROAD.STAPrePNR, which is step 10.

WHAT WAS MEASURED BEFORE THIS WAS WRITTEN (LLV1_W5_spike.md, 0.3.83)
===================================================================
  * Classic: two segments equal one plain invocation -- every metric but the
    router's wall-clock `*_s` keys, the DEF, netlists and LEF byte for byte,
    the GDS record for record except its timestamps.
  * Chip: `--with-initial-state` survives DESIGN_NAME core -> chip top. The
    handed netlist is the core netlist followed by the wrapper, byte for
    byte; the JSON header regenerated over it by the image's own
    Yosys.JsonHeader (`--only`) gives Odb.SetPowerConnections exactly the
    power connections of the one-invocation run's RTL-level header.
  * The handed state must carry segment 1's metrics, or the final state loses
    them (400 vs 493 metrics).
  * 0.3.83's Checker.DisconnectedPins is fatal where older images warned; a
    wrapper's output-only pads leave their core-facing output open by design.
    The masters to ignore come from the chip-top producer's record
    (`ignore_disconnected_masters`), never from a name.

WHEN THE TOOL FAILS
===================
An honest FAIL: `Refusal('LL_SEGMENT_FAILED', ...)` naming the segment, the
last step the run's own log started, its folder, the tool's error log, and the
next action. No fallback to the direct flow and no AI config repair (owner
ruling, COMMON.md).

Every docker run carries the memory ceiling (`_docker_memory`) and a deadline.
chip-AGNOSTIC / PDK-AGNOSTIC: no design, cell or PDK name is written here.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_PROGRAMS_DIR = str(Path(__file__).resolve().parent)
if _PROGRAMS_DIR not in sys.path:
    sys.path.insert(0, _PROGRAMS_DIR)

import _atomic_artefact  # noqa: E402
import _docker_memory as _dmem  # noqa: E402 — every `docker run` carries the ceiling
import _watchdog  # noqa: E402 — EDA completion is supervised by progress
import librelane_contract as _ll  # noqa: E402
from librelane_contract import Refusal, digest  # noqa: E402

SEGMENT1_LAST = "Checker.NetlistAssignStatements"
SEGMENT2_FIRST = "OpenROAD.CheckSDCFiles"
JSON_HEADER_STEP = "Yosys.JsonHeader"
WHOLE_REL = "phase3/librelane/whole"
HANDOFF_RECORD = "handoff.json"
CHIP_TOP_RECORD_REL = "reports/phase3/io_pad_chip_top.json"
#: Default deadline of one segment (seconds); a caller may pass its own.
DEFAULT_DEADLINE_S = 4 * 3600


def whole_dir(project: Path) -> Path:
    return Path(project) / WHOLE_REL


def librelane_argv(project: Path, image: str, config: Path, *, flow: str, tag: str,
                   pdk: str, pdk_root: Path, scl: Optional[str],
                   extra: Sequence[str] = (), home: Optional[Path] = None,
                   docker: str = "docker") -> List[str]:
    """The one invocation shape both segments use: the image's LibreLane CLI,
    the manual PDK root mounted read-only, the explicit PDK and cell library
    (the image's own PDK/STD_CELL_LIBRARY environment is never relied on),
    the design directory = the project (config paths are `dir::` there)."""
    project = Path(project).resolve()
    argv = [docker, "run", "--rm", *_dmem.docker_memory_flags(),
            "--network", "none", "--user", f"{os.getuid()}:{os.getgid()}",
            "-v", f"{project}:{project}", "-v", f"{Path(pdk_root).resolve()}:/pdk:ro"]
    if home is not None:
        argv += ["-e", f"HOME={Path(home).resolve()}"]
    argv += ["--entrypoint", "python3", image, "-m", "librelane", "--manual-pdk",
             "--pdk-root", "/pdk", "--pdk", pdk]
    if scl:
        argv += ["--scl", scl]
    argv += ["--flow", flow, "--design-dir", str(project), "--run-tag", tag,
             *extra, str(Path(config).resolve())]
    return argv


class Uncalibrated(Refusal):
    """A reader of LibreLane's own logs that failed its calibration pair."""




def steps_from_log(text: str, tag: str) -> List[Tuple[str, str]]:
    """[(step id, folder name)] for every step a run logged starting.

    The grammar is read by `_tool_log_provenance.flow_log_steps` (W19's
    calibrated reader of LibreLane's flow.log); this only selects the run's
    own steps. The logged folder is relative to the CONTAINER's working
    directory, so only its last two parts mean anything here: the run tag and
    the step folder. A composite step logs its sub-steps one level deeper
    (`<tag>/NN-step/1-sub`); only the run directory's own children are the
    flow's steps."""
    import _tool_log_provenance as _tlp
    return [(step_id, parts[-1]) for step_id, parts in _tlp.flow_log_steps(text)
            if len(parts) >= 2 and parts[-2] == tag]


def started_steps(run_dir: Path) -> List[Tuple[str, Path]]:
    """[(step id, folder)] for every step the run started, from its own log.

    Keyed on the step ids the run wrote, never on ordinal folder names: the
    image's flow can gain or lose a step between releases."""
    run_dir = Path(run_dir)
    log = run_dir / "flow.log"
    if not log.is_file():
        return []
    return [(step_id, run_dir / name) for step_id, name in
            steps_from_log(log.read_text(errors="replace"), run_dir.name)]


def run_segment(project: Path, image: str, config: Path, *, name: str, flow: str,
                pdk: str, pdk_root: Path, scl: Optional[str], extra: Sequence[str],
                expected: Optional[Sequence[str]] = None,
                deadline_s: int = DEFAULT_DEADLINE_S, home: Optional[Path] = None,
                docker: str = "docker") -> Dict[str, Any]:
    """Run one LibreLane invocation; return its run directory, the steps it
    started and the final state. Any failure is `LL_SEGMENT_FAILED`, naming the
    tool's own report and the next action; a step list that is not the one
    `expected` is `LL_SEGMENT_STEPS_UNEXPECTED`."""
    base = whole_dir(project)
    base.mkdir(parents=True, exist_ok=True)
    tag = name
    run_dir = Path(project).resolve() / "runs" / tag
    argv = librelane_argv(project, image, config, flow=flow, tag=tag, pdk=pdk,
                          pdk_root=pdk_root, scl=scl, extra=extra, home=home,
                          docker=docker)
    _atomic_artefact.write_json(base / f"{name}.invocation.json",
                                {"argv": argv, "config": str(config),
                                 "config_sha256": digest(Path(config)),
                                 "deadline_s": deadline_s}, indent=2)
    done = _run_segment_process(argv, run_dir=run_dir, base=base,
                                name=name, budget_s=deadline_s, docker=docker)
    rc, out = done.returncode, (done.stdout or "") + (done.stderr or "")
    (base / f"{name}.console.log").write_text(out)
    steps = started_steps(run_dir)
    ids = [s for s, _ in steps]
    final = steps[-1][1] / "state_out.json" if steps else None
    findings = deferred_findings(run_dir)
    # LibreLane exits non-zero at the END of a run whose checkers found
    # something they defer (e.g. "6 Magic DRC errors found. - deferred"). That
    # run completed: every expected step ran and left its state. Its findings
    # are the tool's verdict, recorded and returned for vibe-ic's gates to
    # judge the result with; they are never dropped. Any other non-zero exit
    # (an aborted step, an undeferred error, a deadline) is a FAIL here.
    completed_with_findings = (
        rc != 0 and expected is not None and ids == list(expected)
        and final is not None and final.is_file() and findings is not None
        and len(findings) > 0)
    if rc != 0 and not completed_with_findings:
        last = steps[-1] if steps else ("<none>", run_dir)
        raise Refusal("LL_SEGMENT_FAILED",
                      f"{name}: LibreLane rc={rc}; last step started {last[0]} at "
                      f"{last[1]}; tool report {run_dir / 'error.log'} and "
                      f"{base / (name + '.console.log')}. Next action: read that "
                      "step's log, correct the declared input it names, and re-run; "
                      "or run the default flow (no --librelane).")
    if expected is not None and ids != list(expected):
        raise Refusal("LL_SEGMENT_STEPS_UNEXPECTED",
                      f"{name}: ran {ids}, expected {list(expected)}")
    if not steps:
        raise Refusal("LL_SEGMENT_FAILED", f"{name}: rc=0 but the run logged no step "
                      f"({run_dir / 'flow.log'})")
    if not final.is_file():
        raise Refusal("LL_SEGMENT_FAILED", f"{name}: no state_out.json at {final}")
    return {"run_dir": run_dir, "steps": steps, "state": final, "tool_rc": rc,
            "tool_findings": findings or [],
            "tool_verdict": "FINDINGS" if completed_with_findings else "CLEAN"}


def _run_segment_process(argv: List[str], *, run_dir: Path, base: Path,
                         name: str, budget_s: int, docker: str):
    """Supervise the exact Docker run by progress and reap only its CID.

    The budget is recorded, never used as a clock kill.  A stalled Docker
    client is stopped by its private CID before the supervised process group
    is reaped, so it cannot leave an orphaned EDA container running.
    """
    cidfile = base / f"{name}.cid"
    if cidfile.exists():
        cidfile.unlink()
    command = [*argv[:2], "--cidfile", str(cidfile), *argv[2:]]

    def _reap(proc, _reason):
        if cidfile.is_file():
            cid = cidfile.read_text().strip()
            if re.fullmatch(r"[0-9a-f]{64}", cid):
                subprocess.run([docker, "stop", "--time", "10", cid],
                               capture_output=True, text=True, timeout=30)
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    env = dict(os.environ)
    env.pop("VIBEIC_EDA_IMAGE_REPO", None)
    result = _watchdog.run_supervised(
        command, log_path=run_dir / "flow.log", stall_grace_s=1800,
        hard_ceiling_s=budget_s, poll_s=10, kill=_reap, env=env)
    _atomic_artefact.write_json(base / f"{name}.supervision.json",
                                {"outcome": result.outcome, "rc": result.rc,
                                 "elapsed_s": result.elapsed_s,
                                 "supervision": result.supervision}, indent=2)
    return type("SegmentProcess", (), {"returncode": result.rc,
                                         "stdout": result.out,
                                         "stderr": result.err})()


def deferred_findings(run_dir: Path) -> Optional[List[str]]:
    """The run's `error.log` lines when EVERY one is a deferred checker finding;
    None when the log holds anything else (an abort); [] when it is empty."""
    log = Path(run_dir) / "error.log"
    if not log.is_file():
        return []
    return deferred_lines(log.read_text(errors="replace"))


def deferred_lines(text: str) -> Optional[List[str]]:
    """The lines of a LibreLane `error.log` when EVERY one is a checker finding
    the flow deferred to its end ("... - deferred"); None when any line is
    something else; [] for an empty log."""
    import instrument_calibration as _calibration
    try:
        _calibration.assert_calibrated("librelane_whole_flow::deferred_lines")
    except _calibration.Uncalibrated as exc:
        raise Uncalibrated("LL_INSTRUMENT_UNCALIBRATED", str(exc)) from None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if all(ln.endswith("- deferred") for ln in lines):
        return lines
    return None


_PLANNED_STEPS_SCRIPT = r"""
import json, sys
from librelane.flows import Flow
from librelane.common import Filter
flow_name, config, pdk, scl, design_dir, first, last = sys.argv[1:]
cls = Flow.factory.get(flow_name)
kwargs = dict(config=config, pdk=pdk, pdk_root="/pdk", design_dir=design_dir)
if scl:
    kwargs["scl"] = scl
flow = cls(**kwargs)
ids = [s.id for s in flow.Steps]
if last == "__FLOW_END__":
    last = ids[-1]
gates = {}
for key, value in (flow.gating_config_vars or {}).items():
    targets = [key] if key in ids else list(Filter([key]).filter(ids))
    for target in targets:
        gates[target] = value
if first not in ids or last not in ids or ids.index(first) > ids.index(last):
    raise SystemExit("segment %s..%s is not in %s" % (first, last, flow_name))
out = []
for step in ids[ids.index(first):ids.index(last) + 1]:
    gated = [v for v in gates.get(step, []) if not flow.config[v]]
    out.append({"id": step, "gated_off_by": gated})
print(json.dumps(out))
"""


def planned_steps(project: Path, image: str, config: Path, *, flow: str, pdk: str,
                  pdk_root: Path, scl: Optional[str], first: str,
                  last: Optional[str],
                  docker: str = "docker", deadline_s: int = 600) -> Dict[str, Any]:
    """The steps `first..last` the image's own Flow will run for this config:
    its step order, minus every step whose gating variable the resolved config
    sets false (exactly `SequentialFlow.run`'s rule). `{"run": [...],
    "gated_off": {step: [vars]}}`; an unresolvable plan refuses by name."""
    project = Path(project).resolve()
    argv = [docker, "run", "--rm", *_dmem.docker_memory_flags(), "--network", "none",
            "-v", f"{project}:{project}", "-v", f"{Path(pdk_root).resolve()}:/pdk:ro",
            "--entrypoint", "python3", image, "-c", _PLANNED_STEPS_SCRIPT, flow,
            str(Path(config).resolve()), pdk, scl or "", str(project), first,
            last or "__FLOW_END__"]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=deadline_s)
    except subprocess.TimeoutExpired as exc:
        raise Refusal("LL_FLOW_PLAN_UNRESOLVED", f"deadline {deadline_s}s: {exc}") from None
    try:
        rows = json.loads(done.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        rows = None
    if done.returncode or not isinstance(rows, list):
        raise Refusal("LL_FLOW_PLAN_UNRESOLVED",
                      f"{flow} {first}..{last}: rc={done.returncode} {(done.stderr or '')[-800:]}")
    return {"run": [r["id"] for r in rows if not r["gated_off_by"]],
            "gated_off": {r["id"]: r["gated_off_by"] for r in rows if r["gated_off_by"]}}


def ignore_disconnected_masters(project: Path) -> Tuple[List[str], str]:
    """Pad masters whose core-facing outputs the wrapper leaves open by design.

    A master qualifies only when EVERY instance of it serves an output-direction
    port (the producer's `pad_instances[*].direction`). A master that also
    serves an input or inout port refuses (`LL_DISCONNECTED_MASTER_MIXED`):
    LibreLane can only ignore per module, and ignoring it would also hide a
    genuinely open input pad."""
    path = Path(project) / CHIP_TOP_RECORD_REL
    if not path.is_file():
        return [], f"{CHIP_TOP_RECORD_REL} absent: nothing ignored"
    record = json.loads(path.read_text())
    by_master: Dict[str, set] = {}
    for inst, row in (record.get("pad_instances") or {}).items():
        if isinstance(row, dict) and row.get("master") and row.get("port"):
            by_master.setdefault(row["master"], set()).add(row.get("direction"))
    output_only = sorted(m for m, d in by_master.items() if d == {"output"})
    mixed = sorted(m for m, d in by_master.items() if "output" in d and len(d) > 1)
    if mixed:
        raise Refusal("LL_DISCONNECTED_MASTER_MIXED",
                      f"{mixed} serve output and non-output ports ({CHIP_TOP_RECORD_REL})")
    return output_only, (f"{CHIP_TOP_RECORD_REL}.pad_instances[*] whose every "
                         "instance has direction output (core-facing outputs open "
                         "by design)")


def handoff(project: Path, segment1_state: Path, *, netlist: Path,
            wrapper: Optional[Path], json_header: Path, top: str) -> Dict[str, Any]:
    """Write segment 2's initial state and the record that binds it.

    The layout netlist is `netlist` (the core, or the scan netlist when DFT is
    declared) followed byte for byte by `wrapper` on the chip path. The state
    carries segment 1's metrics. The record names every input by sha256 so
    step 13 can prove it proved the netlist segment 2 consumed
    (`netlist_identity`)."""
    base = whole_dir(project) / "handoff"
    base.mkdir(parents=True, exist_ok=True)
    body = Path(netlist).read_bytes()
    if wrapper is not None:
        body += Path(wrapper).read_bytes()
    layout = base / f"{top}.nl.v"
    layout.write_bytes(body)
    seg1 = json.loads(Path(segment1_state).read_text())
    state = {"nl": str(layout.resolve()), "json_h": str(Path(json_header).resolve()),
             "metrics": seg1.get("metrics") or {}}
    state_path = base / "state_in.json"
    _atomic_artefact.write_json(state_path, state, indent=2)
    record = {"segment1_state": str(segment1_state),
              "segment1_state_sha256": digest(Path(segment1_state)),
              "netlist": str(netlist), "netlist_sha256": digest(Path(netlist)),
              "wrapper": str(wrapper) if wrapper else None,
              "wrapper_sha256": digest(Path(wrapper)) if wrapper else None,
              "layout_netlist": str(layout), "layout_netlist_sha256": digest(layout),
              "json_header": str(json_header), "json_header_sha256": digest(Path(json_header)),
              "state_in": str(state_path), "state_in_sha256": digest(state_path),
              "recipe": "layout_netlist = netlist ++ wrapper (bytes)"}
    _atomic_artefact.write_json(base / HANDOFF_RECORD, record, indent=2)
    return record


def netlist_identity(project: Path, proven_netlist: Path) -> Dict[str, Any]:
    """Is `proven_netlist` (what step 13 proved) the netlist segment 2 consumed?

    Recomputed from the files, never read from the record alone: the record's
    netlist must still hash the same, `proven_netlist` must be it, and the
    layout netlist on disk must still be netlist ++ wrapper."""
    rec_path = whole_dir(project) / "handoff" / HANDOFF_RECORD
    if not rec_path.is_file():
        return {"verdict": "NOT_MEASURED", "reason": f"{rec_path} absent"}
    rec = json.loads(rec_path.read_text())
    proven = digest(Path(proven_netlist))
    problems = []
    if proven != rec["netlist_sha256"]:
        problems.append(f"step 13 proved {proven}, segment 2 was handed {rec['netlist_sha256']}")
    layout = Path(rec["layout_netlist"])
    expect = Path(rec["netlist"]).read_bytes() + (
        Path(rec["wrapper"]).read_bytes() if rec.get("wrapper") else b"")
    if hashlib.sha256(expect).hexdigest() != digest(layout):
        problems.append(f"{layout} is not netlist ++ wrapper any more")
    if digest(layout) != rec["layout_netlist_sha256"]:
        problems.append(f"{layout} changed after the handoff")
    return {"verdict": "FAIL" if problems else "PASS", "problems": problems,
            "proven_sha256": proven, "consumed_sha256": rec["layout_netlist_sha256"]}


def import_completed_segments(project: Path, segment1: Path, segment2: Path,
                              *, importer=None) -> Dict[str, Any]:
    """Atomically import the two completed external runs through W6.

    This is intentionally a thin call-through: W6 owns the run-log checks,
    manifest schema, copy journal and per-view provenance.  The driver only
    supplies the ordered, already-completed segment directories.
    """
    if importer is None:
        import librelane_import as _importer
        importer = _importer.import_segments
    return importer(Path(project), [(Path(segment1), SEGMENT1_LAST),
                                     (Path(segment2), None)])


# ── segment configs, from the contract's emitters ──────────────────────────

def _write_config(out: Path, config: dict, sources: dict) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    _atomic_artefact.write_json(out, config, indent=2)
    _atomic_artefact.write_json(out.with_suffix(".provenance.json"), sources, indent=2)
    return out


def segment1_config(project: Path, pdk: str, out: Path, *, top: str,
                    rtl_files: Sequence[Path], defines: Sequence[str], use_slang: bool,
                    scl: Optional[str] = None, synth_liberty: Optional[str] = None,
                    top_source: str = "caller (the resolved synthesis top)") -> Path:
    """Segment 1 synthesises the CORE: `emit_synthesis_config` (the chip read,
    its defines, the FSM table step 13 needs) with DESIGN_NAME = the core."""
    config = _ll.emit_synthesis_config(project, pdk, out, list(rtl_files), list(defines),
                                       use_slang, std_cell_library=scl,
                                       synth_liberty=synth_liberty, top=top)
    sources = json.loads(out.with_suffix(".provenance.json").read_text())
    _ll._set(config, sources, "DESIGN_NAME", top, top_source)
    return _write_config(out, config, sources)


def segment2_config(project: Path, pdk: str, out: Path, *, layout_netlist: Path,
                    sdc: Optional[Path], sdc_source: str,
                    pdn_cfg: Optional[Path] = None) -> Path:
    """Segment 2 implements the LAYOUT top over the handed netlist.

    `emit_config` (declared inputs only), the die by flow
    (`librelane_contract.apply_flow_die`), the chip top as DESIGN_NAME on the
    chip path (`_apply_layout_top`, D7), VERILOG_FILES = the handed layout
    netlist (the only file Yosys.JsonHeader reads there), the SDC step 7
    declared, the pad-connect PDN script, and the output-only pad masters the
    disconnected-pin checker must ignore."""
    flow = _ll.librelane_flow(project)[0]
    config = _ll.emit_config(project, pdk, out)
    sources = json.loads(out.with_suffix(".provenance.json").read_text())
    _ll.apply_flow_die(project, config, sources,
                       ["OpenROAD.Floorplan", "OpenROAD.PadRing"], flow)
    _ll._apply_layout_top(project, config, sources)
    _ll._set(config, sources, "VERILOG_FILES", [str(Path(layout_netlist).resolve())],
             f"{WHOLE_REL}/handoff/{HANDOFF_RECORD}.layout_netlist")
    if sdc is not None:
        for key in ("PNR_SDC_FILE", "SIGNOFF_SDC_FILE"):
            _ll._set(config, sources, key, str(Path(sdc).resolve()), sdc_source)
    else:
        for key in ("PNR_SDC_FILE", "SIGNOFF_SDC_FILE"):
            config.pop(key, None)
            sources[key] = f"ABSENT: {sdc_source}"
    if pdn_cfg is not None:
        _ll._set(config, sources, "PDN_CFG", str(Path(pdn_cfg).resolve()),
                 "librelane_contract.emit_pdn_cfg (the image's pdn_cfg.tcl + the "
                 "PDK registry's pad-facing connects)")
    masters, why = ignore_disconnected_masters(project)
    if masters:
        _ll._set(config, sources, "IGNORE_DISCONNECTED_MODULES", masters, why)
    return _write_config(out, config, sources)


# ── the two segments ───────────────────────────────────────────────────────

def run_two_segments(project: Path, image: str, *, pdk: str, pdk_root: Path,
                     scl: Optional[str], segment1: Path, between,
                     segment2_kwargs: Dict[str, Any], first_step: str,
                     last_step: Optional[str] = None,
                     deadline_s: int = DEFAULT_DEADLINE_S,
                     docker: str = "docker", importer=None) -> Dict[str, Any]:
    """Segment 1, vibe-ic's between-segments work, segment 2.

    `between(project, segment1_state) -> dict` is the runner's: steps 11-14 and
    `step_prepnr` (W7a). It returns `netlist` (what step 13 proved: the core,
    or the scan netlist when DFT is declared), `wrapper` (the chip top's
    Verilog, or None), `top` (the layout top) and `sdc` / `sdc_source`. A
    `between` that raises stops the run there; nothing after it runs.
    """
    flow = _ll.librelane_flow(project)[0]
    base = whole_dir(project)
    common = dict(flow=flow, pdk=pdk, pdk_root=pdk_root, scl=scl)
    plan1 = planned_steps(project, image, segment1, first=first_step,
                          last=SEGMENT1_LAST, docker=docker, **common)
    s1 = run_segment(project, image, segment1, name="segment1",
                     extra=["--to", SEGMENT1_LAST], expected=plan1["run"],
                     deadline_s=deadline_s, docker=docker, **common)
    handed = between(project, s1["state"])
    state1 = json.loads(Path(s1["state"]).read_text())
    seg2 = segment2_config(project, pdk, base / "segment2.json",
                           layout_netlist=base / "handoff" / f"{handed['top']}.nl.v",
                           sdc=handed.get("sdc"), sdc_source=handed.get("sdc_source", ""),
                           **segment2_kwargs)
    if handed.get("wrapper") is not None:
        # The wrapper changes the netlist the header describes: regenerate it
        # with the image's own Yosys.JsonHeader over the layout netlist.
        layout = base / "handoff" / f"{handed['top']}.nl.v"
        layout.parent.mkdir(parents=True, exist_ok=True)
        layout.write_bytes(Path(handed["netlist"]).read_bytes()
                           + Path(handed["wrapper"]).read_bytes())
        jh = run_segment(project, image, seg2, name="json_header",
                         extra=["--only", JSON_HEADER_STEP], expected=[JSON_HEADER_STEP],
                         deadline_s=deadline_s, docker=docker, **common)
        json_header = Path(json.loads(Path(jh["state"]).read_text())["json_h"])
    else:
        json_header = Path(state1["json_h"])
    rec = handoff(project, s1["state"], netlist=Path(handed["netlist"]),
                  wrapper=handed.get("wrapper"), json_header=json_header,
                  top=handed["top"])
    plan2 = planned_steps(project, image, seg2, first=SEGMENT2_FIRST, last=last_step,
                          docker=docker, **common)
    s2 = run_segment(project, image, seg2, name="segment2",
                     extra=["--from", SEGMENT2_FIRST, "--with-initial-state", rec["state_in"]],
                     expected=plan2["run"], deadline_s=deadline_s, docker=docker, **common)
    imported = import_completed_segments(project, s1["run_dir"], s2["run_dir"],
                                         importer=importer)
    summary = {"flow": flow, "image": image,
               "segment1": {"steps": [s for s, _ in s1["steps"]], "state": str(s1["state"]),
                            "tool_verdict": s1["tool_verdict"]},
               "segment2": {"steps": [s for s, _ in s2["steps"]], "state": str(s2["state"]),
                            "tool_verdict": s2["tool_verdict"],
                            "tool_findings": s2["tool_findings"],
                            "gated_off": plan2["gated_off"]},
               "handoff": rec,
               "import": imported,
               "netlist_identity": netlist_identity(project, Path(handed["netlist"]))}
    _atomic_artefact.write_json(base / "whole_flow.json", summary, indent=2)
    return summary
