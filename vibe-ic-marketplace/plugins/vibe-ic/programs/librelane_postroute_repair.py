#!/usr/bin/env python3
"""librelane_postroute_repair.py — step 32 (post-route repair) on LibreLane (T102).

Selected by `phase3/librelane_switch.json` (`"32": "librelane"`) through
`librelane_contract.selected_mode`. With `direct` (switch absent, or naming
other steps) `phase3_one_shot_runner` runs `step_signoff_spef_repair` and the
DRV wire-length escalation exactly as before and nothing here is called.

WHAT RUNS INSTEAD
=================
The routed database is bridged into a LibreLane State
(`librelane_contract.state_from_direct`) and the repair is the plugin step
`Vibeic.PostRouteRepair` (`librelane_plugins/librelane_plugin_vibeic/
postroute_repair.py`): fork `estimate_parasitics -detailed_routing` on the
router's own extracted wires, `repair_design`, `repair_timing -setup`, then
`-hold`, legalization of the changed cells only, supply connection, ECO
`detailed_route -nets` of the touched nets, re-verify. One run of it is one
CANDIDATE. Every candidate, and the input, is measured the same way:
`OpenROAD.RCX` then `OpenROAD.STAPostPNR` (every STA corner) in the sign-off
scene (the deck's SDC plus the sign-off STA's flat-OCV derate).

WHO DECIDES
===========
`_ppa/closure.py`. The actuator `timing.repair_setup` (config/
ppa_actuator_registry.yaml) is EXECUTABLE: its wrapper is this program's
`actuate` subcommand, its parameters (setup/hold margin, buffer budgets,
repair_tns) are a declared ladder, and the domains it re-measures —
`timing.setup`, `timing.hold`, `timing.drv`, `antenna.violations` — are this
program's `measure` subcommand over the ADOPTED candidate. The input route is
measured on the same instruments: the plugin step in census-only mode
(`VIBEIC_PRR_CENSUS_ONLY`, `check_antennas`), then RCX + STAPostPNR. The controller
promotes a candidate only on a measured improvement of its objective with no
regression in any re-measured domain, and rolls back otherwise. T98's finding
(a DRV repair that took SS setup from +3.33 to +0.14 because it was the first
candidate that passed DRV) cannot recur: setup is re-measured on every
candidate and a setup regression rolls the candidate back.

THE IMPLEMENTATION ROOT IS A POINTER
====================================
The closure snapshots and restores its implementation root, so the root holds
exactly two small files: `context.json` (how to run a candidate: project,
image, PDK mount, resolved configs) and `current.json` (the adopted State and
its measurement, bound by sha256). Candidate States live in LibreLane's own
step directories, which are never overwritten, so a rollback is keeping the
previous pointer: nothing is re-read or re-routed to undo a candidate. The
ledger of every candidate (adopted or not, and why) lives outside the root.

HARD REFUSALS, BEFORE A CANDIDATE CAN BE ADOPTED
================================================
* supply ownership (F24): `pg_supply_pin_ownership_check` on the candidate's
  own DEF. A proven off-supply pin keeps the pointer where it was;
* antenna, by the step-26 instrument (`OpenROAD.CheckAntennas`
  `antenna__violating__nets`): a candidate with more violating nets than the
  state it was built from, or an uncounted one, keeps the pointer where it
  was (lane mig99 measured the direct SDR candidate adding 4 on spm);
* the repair step's own `check_placement` (it fails the step);
* router DRC: the fork's scoped `detailed_route -nets` refuses a route with
  more whole-design violations than it was given (DRT-0712), which fails the
  candidate's step; the ledger records it as TOOL_REFUSED.

chip-AGNOSTIC: no design, PDK, corner, cell or net literal. Every value comes
from the resolved LibreLane config, the direct deck's own files or the
registry's declared ladder.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _atomic_artefact import write_json  # noqa: E402
import _docker_memory as _dmem  # noqa: E402 — every `docker run` carries the ceiling

STEP = "32"
REPAIR_STEP = "Vibeic.PostRouteRepair"
#: Every candidate, and the input, is measured by the same three steps: the
#: step-26 router-model antenna instrument (`OpenROAD.CheckAntennas`, whose
#: `antenna__violating__nets` `librelane_ir_antenna` judges), then RCX, then
#: STAPostPNR at every corner.
MEASURE_STEPS = ("OpenROAD.CheckAntennas", "OpenROAD.RCX", "OpenROAD.STAPostPNR")
ANTENNA_METRICS = ("antenna__violating__nets", "antenna__violating__pins")
ARM_REL = "phase3/tool_arms/32"
IMPL_DIR = "impl"
CONTEXT = "context.json"
CURRENT = "current.json"
LEDGER = "candidates.json"
REPORT_REL = "reports/phase3/librelane_postroute_repair.json"
#: The controllers this step runs, in order: setup, then hold (hold after
#: setup), then design rules. Each is declared in the actuator registry.
CONTROLLERS = ("postroute.repair_setup", "postroute.repair_hold",
               "postroute.repair_drv")

#: The actuator's parameters -> the plugin step's variables.
PARAM_VARS = {
    "setup_margin_ns": "VIBEIC_PRR_SETUP_MARGIN",
    "hold_margin_ns": "VIBEIC_PRR_HOLD_MARGIN",
    "setup_max_buffer_pct": "VIBEIC_PRR_SETUP_MAX_BUFFER_PCT",
    "hold_max_buffer_pct": "VIBEIC_PRR_HOLD_MAX_BUFFER_PCT",
    "repair_tns_pct": "VIBEIC_PRR_SETUP_REPAIR_TNS_PCT",
}

#: `measure` exits with this when the adopted state cannot answer; the
#: registry declares it UNDETERMINED, so it is NOT_MEASURED, never a number.
RC_UNDETERMINED = 3


def mode(project: Path) -> str:
    """The contract switch for step 32."""
    import librelane_contract as _ll
    return _ll.selected_mode(project, STEP)


def refusal(selected: str, route_mode: str = "direct") -> Optional[str]:
    """Why a step-32 selection cannot run, or None. `dual` needs step 21 on
    LibreLane: its second arm is the pre-detailed-route repair
    (RepairDesignPostGRT + ResizerTimingPostGRT) inside step 21's chain."""
    if selected == "dual" and route_mode == "direct":
        return ("LL_PRR_DUAL_NEEDS_LL21: step 32's second tool path is the "
                "pre-detailed-route repair inside step 21's LibreLane chain; "
                "select 21 `librelane` or `dual`, or 32 `librelane`")
    return None


# --- the fork capability -------------------------------------------------------

#: Our own markers around the two probe answers (not a tool grammar).
_PROBE_REAL = "VIBEIC_PRR_PROBE_REAL:"
_PROBE_CTRL = "VIBEIC_PRR_PROBE_CTRL:"
_REAL_FLAG = "-detailed_routing"
_CTRL_FLAG = "-vibeic_probe_unknown_control_flag"


def flag_accepted_vs_control(transcript: str) -> Optional[bool]:
    """Did `estimate_parasitics` treat the real flag differently from a flag
    we know is invalid? True/False, or None when either answer is absent.

    No diagnostic wording is read: an OpenROAD that does not know the flag
    answers the real flag exactly as it answers the bogus one (the flag name
    aside), whatever that answer says."""
    answers: Dict[str, str] = {}
    for line in transcript.splitlines():
        for key in (_PROBE_REAL, _PROBE_CTRL):
            at = line.find(key)
            if at >= 0:
                answers[key] = line[at + len(key):].strip()
    if len(answers) != 2:
        return None
    return (answers[_PROBE_REAL]
            != answers[_PROBE_CTRL].replace(_CTRL_FLAG, _REAL_FLAG))


def fork_capability(image: str, docker: str = "docker") -> Dict[str, Any]:
    """Probe the image's OpenROAD for the fork's `-detailed_routing` flag."""
    tcl = (f"if {{[catch {{estimate_parasitics {_REAL_FLAG}}} e]}} "
           f"{{ puts \"{_PROBE_REAL} $e\" }} else {{ puts \"{_PROBE_REAL} <accepted>\" }}\n"
           f"if {{[catch {{estimate_parasitics {_CTRL_FLAG}}} e]}} "
           f"{{ puts \"{_PROBE_CTRL} $e\" }} else {{ puts \"{_PROBE_CTRL} <accepted>\" }}\n")
    cmd = [docker, "run", "--rm", "--network", "none", *_dmem.docker_memory_flags(),
           "-i", image, "--skip", "bash", "-c",
           "openroad -no_init -no_splash -exit /dev/stdin 2>&1"]
    try:
        done = subprocess.run(cmd, input=tcl, capture_output=True, text=True,
                              timeout=300)
        transcript = (done.stdout or "") + "\n" + (done.stderr or "")
    except (OSError, subprocess.SubprocessError) as exc:
        return {"capable": None, "image": image, "reason": f"probe could not run: {exc}"}
    capable = flag_accepted_vs_control(transcript)
    return {"capable": capable, "image": image,
            "transcript_tail": transcript.strip()[-600:]}


# --- measurement ---------------------------------------------------------------

def _load(path: Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text())


def summarize(metrics: Dict[str, Any], corners: Sequence[str]) -> Dict[str, Any]:
    """Worst slack and design-rule counts over EVERY declared STA corner, from
    `OpenROAD.STAPostPNR`'s own per-corner metrics. A corner the step did not
    report leaves the summary unmeasured (`None`), never optimistic."""
    def per_corner(prefix: str) -> Dict[str, Any]:
        return {c: metrics.get(f"{prefix}__corner:{c}") for c in corners}

    setup = per_corner("timing__setup__ws")
    hold = per_corner("timing__hold__ws")
    drv = {kind: per_corner(f"design__max_{kind}_violation__count")
           for kind in ("slew", "cap", "fanout")}
    missing = sorted({c for c in corners
                      if not isinstance(setup[c], (int, float))
                      or not isinstance(hold[c], (int, float))
                      or any(not isinstance(drv[k][c], int) for k in drv)})
    out: Dict[str, Any] = {"corners": list(corners), "setup_ws": setup,
                           "hold_ws": hold, "drv": drv, "unmeasured_corners": missing}
    if missing or not corners:
        out.update(setup_ws_min=None, hold_ws_min=None, drv_count=None)
    else:
        out["setup_ws_min"] = min(float(v) for v in setup.values())
        out["hold_ws_min"] = min(float(v) for v in hold.values())
        out["drv_count"] = sum(int(v) for k in drv for v in drv[k].values())
    return out


def _candidate(ctx: Dict[str, Any], config: Path, state: Path,
               lane: str) -> Tuple[Path, Dict[str, Any]]:
    """The repair step (a candidate, or the census) then RCX + STAPostPNR:
    the repair step's folder and the measurement of what it handed on."""
    import librelane_contract as _ll
    project = Path(ctx["project"])
    folders = _ll.run_chain(
        project, ctx["image"],
        [(REPAIR_STEP, config, state)]
        + [(step, Path(ctx["configs"][step]), state) for step in MEASURE_STEPS],
        mounts=[(Path(h), g) for h, g in ctx["mounts"]], lane=lane)
    sta_state = folders[-1] / "state_out.json"
    summary = summarize(_load(sta_state).get("metrics") or {}, ctx["corners"])
    antenna = antenna_census(folders[1])
    return folders[0], {"sta_state": str(sta_state),
                        "sta_state_sha256": _ll.digest(sta_state), **summary,
                        "antenna_nets": antenna["antenna__violating__nets"],
                        "antenna_pins": antenna["antenna__violating__pins"],
                        "antenna_state": str(folders[1] / "state_out.json")}


def antenna_census(folder: Path) -> Dict[str, Optional[int]]:
    """`OpenROAD.CheckAntennas`' own counts from its step folder (the step-26
    router-model instrument); a count the step did not write is None."""
    metrics = _load(folder / "state_out.json").get("metrics") or {}
    return {k: (metrics.get(k) if isinstance(metrics.get(k), int)
                and not isinstance(metrics.get(k), bool) else None)
            for k in ANTENNA_METRICS}


DOMAIN_VALUE = {
    "setup": lambda cur: cur["measurement"].get("setup_ws_min"),
    "hold": lambda cur: cur["measurement"].get("hold_ws_min"),
    "drv": lambda cur: cur["measurement"].get("drv_count"),
    "antenna": lambda cur: cur["measurement"].get("antenna_nets"),
}


def measure(impl: Path, domain: str, json_out: Path) -> int:
    """The closure's measurement: one number about the ADOPTED candidate."""
    import librelane_contract as _ll
    try:
        cur = _load(impl / CURRENT)
        sta = Path(cur["measurement"]["sta_state"])
        if _ll.digest(sta) != cur["measurement"]["sta_state_sha256"]:
            print(f"{sta}: not the state {CURRENT} adopted", file=sys.stderr)
            return RC_UNDETERMINED
    except (OSError, ValueError, KeyError) as exc:
        print(f"no adopted candidate to measure: {exc}", file=sys.stderr)
        return RC_UNDETERMINED
    value = DOMAIN_VALUE[domain](cur)
    if value is None:
        print(f"{domain}: the adopted candidate does not carry it", file=sys.stderr)
        return RC_UNDETERMINED
    doc = {"domain": domain, "value": value, "candidate": cur.get("candidate"),
           "sta_state": str(sta)}
    try:
        floor = (_load(impl / CONTEXT).get("floors") or {}).get(domain)
    except (OSError, ValueError):
        floor = None
    if floor:
        doc["floor"], doc["floor_source"] = floor
    write_json(json_out, doc)
    return 0


# --- the actuator ---------------------------------------------------------------

def _ledger_path(impl: Path) -> Path:
    return impl.parent / LEDGER


def _ledger_append(impl: Path, row: Dict[str, Any]) -> None:
    path = _ledger_path(impl)
    rows = _load(path)["candidates"] if path.is_file() else []
    rows.append(row)
    write_json(path, {"candidates": rows})


def _host_path(ctx: Dict[str, Any], value: str) -> Path:
    """A config path as the host sees it (the PDK is mounted at a guest path)."""
    for host, guest in ctx["mounts"]:
        if value.startswith(guest.rstrip("/") + "/"):
            return Path(host) / value[len(guest.rstrip("/")) + 1:]
    return Path(value)


def supply_ownership(ctx: Dict[str, Any], def_path: Path) -> Dict[str, Any]:
    """F24: every supply pin of every instance of the candidate DEF on a
    declared supply net of its kind (`pg_supply_pin_ownership_check`)."""
    import pg_supply_pin_ownership_check as _pgo
    cfg = _load(Path(ctx["configs"][REPAIR_STEP]))
    lefs = [_host_path(ctx, str(p)) for key in ("CELL_LEFS", "PAD_LEFS",
                                                  "MACRO_LEFS", "EXTRA_LEFS")
            for p in (cfg.get(key) or [])]
    try:
        return _pgo.judge_files(def_path, [p for p in lefs if p.is_file()])
    except Exception as exc:  # noqa: BLE001 — recorded, never read as clean
        return {"gate": _pgo.GATE, "verdict": "NOT_MEASURED",
                "code": _pgo.UNMEASURED_CODE, "reason": f"{type(exc).__name__}: {exc}"}


def actuate(impl: Path, params: Dict[str, Any]) -> int:
    """One candidate from the ADOPTED state, measured; adopted only as far as
    the pointer goes — the closure decides whether it stays."""
    import librelane_contract as _ll
    ctx = _load(impl / CONTEXT)
    cur = _load(impl / CURRENT)
    project = Path(ctx["project"])
    ledger = _load(_ledger_path(impl))["candidates"] if _ledger_path(impl).is_file() else []
    index = len(ledger) + 1
    lane = f"{ctx.get('lane', '32')}-cand{index:02d}"
    updates = {PARAM_VARS[k]: (v, f"actuator timing.repair_setup parameter {k}")
               for k, v in params.items() if v is not None}
    base_cfg = Path(ctx["configs"][REPAIR_STEP])
    cfg = _ll.derive_step_config(base_cfg, base_cfg.with_name(f"{REPAIR_STEP}@{lane}.json"),
                                 updates)
    row: Dict[str, Any] = {"candidate": lane, "params": params,
                           "from": cur.get("candidate"), "config": str(cfg)}
    try:
        folder, measurement = _candidate(ctx, cfg, Path(cur["repair_input"]), lane)
        repaired = folder / "state_out.json"
    except _ll.Refusal as exc:
        # The tool refused THIS candidate (e.g. the fork's scoped route
        # refuses a route that adds a whole-design violation, DRT-0712). The
        # pointer stays on the adopted state, so the controller sees no
        # improvement and moves to the next rung; the reason is in the ledger.
        row.update(decision="TOOL_REFUSED", reason=str(exc))
        _ledger_append(impl, row)
        print(f"candidate {lane}: {exc}")
        return 0
    state = _load(repaired)
    metrics = state.get("metrics") or {}
    row.update(repair_state=str(repaired), measurement=measurement,
               repair_metrics={k: v for k, v in metrics.items()
                               if k.startswith("vibeic__prr__")})
    pg = (supply_ownership(ctx, Path(state["def"]))
          if metrics.get("vibeic__prr__changed") != 0 else
          {"verdict": "NOT_APPLICABLE", "reason": "no instance changed; the input DEF"})
    row["supply_ownership"] = {k: pg.get(k) for k in ("verdict", "code", "reason")}
    if pg.get("verdict") == "FAIL":
        row.update(decision="REFUSED", reason=f"supply ownership: {pg.get('reason')}")
        _ledger_append(impl, row)
        print(f"candidate {lane} refused: {row['reason']}")
        return 0
    # Antenna, by the step-26 instrument, on the candidate and on what it was
    # built from: a candidate that creates an antenna violation (or cannot be
    # counted) never moves the pointer, whatever it did for timing or DRV.
    before = (cur.get("measurement") or {}).get("antenna_nets")
    after = measurement.get("antenna_nets")
    row["antenna"] = {"before": before, "after": after}
    if after is None or (before is not None and after > before):
        row.update(decision="REFUSED",
                   reason=(f"antenna (OpenROAD.CheckAntennas): violating nets "
                           f"{before} -> {after}"))
        _ledger_append(impl, row)
        print(f"candidate {lane} refused: {row['reason']}")
        return 0
    write_json(impl / CURRENT, {
        "candidate": lane, "repair_input": str(repaired),
        "repair_state": str(repaired), "repair_folder": str(folder),
        "measurement": measurement,
        "supply_ownership": row["supply_ownership"]})
    row.update(decision="PROPOSED")
    _ledger_append(impl, row)
    print(f"candidate {lane}: setup {measurement.get('setup_ws_min')} "
          f"hold {measurement.get('hold_ws_min')} drv {measurement.get('drv_count')}")
    return 0


# --- the step --------------------------------------------------------------------

def signoff_scene_sdc(sdc: Path, out: Path, derate_early: float,
                      derate_late: float) -> Path:
    """The deck's SDC plus the sign-off STA's flat-OCV derate: the repair
    scene is the sign-off scene (T98's lesson: a repair that does not see the
    sign-off's derate cannot close the sign-off's hold)."""
    from _atomic_artefact import write_text
    write_text(out, sdc.read_text(errors="replace").rstrip("\n") + "\n"
               "# vibe-ic T102: the sign-off STA's flat OCV\n"
               f"set_timing_derate -early {derate_early}\n"
               f"set_timing_derate -late {derate_late}\n")
    return out


#: The spec's declared timing margins (structured L-doc fields, ns).
SPEC_MARGIN_KEYS = {"setup": ("setup_margin_ns",), "hold": ("hold_margin_ns",)}
SPEC_MARGIN_LDOCS = ("L8_TIMING_WAVEFORM.json", "L19_CONSTRAINTS_PDK.json")


def _walk_numbers(node: Any, keys: Sequence[str]) -> List[float]:
    found: List[float] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k in keys and isinstance(v, (int, float)) and not isinstance(v, bool):
                found.append(float(v))
            else:
                found.extend(_walk_numbers(v, keys))
    elif isinstance(node, list):
        for v in node:
            found.extend(_walk_numbers(v, keys))
    return found


def sdc_clock_uncertainty(sdc_text: str) -> Dict[str, List[float]]:
    """`set_clock_uncertainty` values the SDC declares, by check. A command
    with neither -setup nor -hold applies to both (the SDC standard)."""
    out: Dict[str, List[float]] = {"setup": [], "hold": []}
    for line in sdc_text.splitlines():
        words = line.split("#", 1)[0].split()
        if not words or words[0] != "set_clock_uncertainty":
            continue
        value = None
        for word in words[1:]:
            try:
                value = float(word)
                break
            except ValueError:
                continue
        if value is None:
            continue
        checks = [c for c in ("setup", "hold") if f"-{c}" in words[1:]] or ["setup", "hold"]
        for check in checks:
            out[check].append(value)
    return out


def declared_timing_floor(project: Path, sdc: Path) -> Dict[str, Tuple[float, str]]:
    """The floor a HARD-violation repair may spend setup/hold slack down to,
    from the design's DECLARED inputs only (owner ruling, T102 r3): the spec's
    declared margin, else the SDC's own clock uncertainty, else 0 (WNS >= 0 on
    the sign-off scene). Never a typed number."""
    docs = project / "phase1/generated_docs"
    floors: Dict[str, Tuple[float, str]] = {}
    try:
        unc = sdc_clock_uncertainty(sdc.read_text(errors="replace"))
    except OSError:
        unc = {"setup": [], "hold": []}
    for check, keys in SPEC_MARGIN_KEYS.items():
        spec = []
        for name in SPEC_MARGIN_LDOCS:
            path = docs / name
            try:
                spec += [(v, f"{name} {'/'.join(keys)}")
                         for v in _walk_numbers(json.loads(path.read_text()), keys)]
            except (OSError, ValueError):
                continue
        if spec:
            floors[check] = max(spec)
        elif unc[check]:
            floors[check] = (max(unc[check]),
                             f"{sdc.name} set_clock_uncertainty ({check})")
        else:
            floors[check] = (0.0, "no declared margin or uncertainty: "
                                  "WNS >= 0 on the sign-off scene")
    return floors


def _refused_by_tool(report: Dict[str, Any], cap: Dict[str, Any], out: Path) -> bool:
    report["fork_capability"] = cap
    if cap.get("capable") is True:
        return False
    report.update(verdict="NOT_MEASURED", code="LL_PRR_TOOL_INCAPABLE",
                  reason="the image's OpenROAD does not accept the fork's "
                         "estimate_parasitics -detailed_routing")
    write_json(out, report)
    return True


def repair_dont_use(config: Path, pdk_root: Path, pdk: str,
                    rule: Callable[[str], List[str]]) -> List[str]:
    """The cells this step's resizer may not insert: `rule` (the direct deck's
    dont_use families, supplied by the runner) over the step's own resolved
    CELL_LIBS, read through the PDK mount the step runs with.

    cmp3 D14, MEASURED on spm x gf180mcuD (32-cand01): with no exclusion,
    repair_design split high-fanout nets with `dlya_1`/`dlyb_1` delay cells
    (setup 5.15 -> 3.07 ns), and a second fanout round on a delay cell took
    setup to -0.015 ns. The direct deck and the LL placement chain exclude
    that family; step 32 now does too. A library the step names that cannot
    be read refuses: an exclusion computed over part of the library is not
    the exclusion."""
    import librelane_contract as _ll
    libs = _load(config).get("CELL_LIBS") or {}
    paths = sorted({p for v in (libs.values() if isinstance(libs, dict) else [libs])
                    for p in (v if isinstance(v, list) else [v])})
    guest = f"/pdk/{pdk}/"
    excluded: set = set()
    for value in paths:
        host = (pdk_root / pdk / value[len(guest):] if str(value).startswith(guest)
                else Path(value))
        try:
            excluded.update(rule(host.read_text(errors="replace")))
        except OSError as exc:
            raise _ll.Refusal("LL_PRR_LIBERTY_UNREADABLE", f"{value} ({host}): {exc}")
    return sorted(excluded)


def _prepare(project: Path, *, image: str, pdk: str, pdk_root: Path, sdc: Path,
             derate: Tuple[float, float], pg_rules_tcl: Optional[Path],
             refill_tcl: Optional[Path], docker: str,
             dont_use: Optional[Tuple[Callable[[str], List[str]], str]] = None,
             max_fanout: Optional[Tuple[int, str]] = None,
             ) -> Tuple[Dict[str, Path], List[str], List[Tuple[Path, str]]]:
    """The resolved configs (repair step + the three measuring steps) in the
    sign-off scene, the STA corners, and the PDK mount."""
    import librelane_contract as _ll
    folder = project / "phase3/librelane/32-config"
    scene = signoff_scene_sdc(sdc, folder / "signoff_scene.sdc", *derate)
    source = "the deck's SDC + the sign-off STA's flat-OCV derate"
    overlay = {"PNR_SDC_FILE": (str(scene.resolve()), source),
               "SIGNOFF_SDC_FILE": (str(scene.resolve()), source)}
    # T105: every LibreLane geometry step reads the tech LEF the route read
    # (the flow's via-legalized LEF, bound by sha256), never the PDK's own.
    # Here that is OpenROAD.RCX (DEF + RCX_LEF) and the direct-route bridge's
    # DEF -> ODB; an APPLIED record whose file is not found by its hash
    # refuses LL_ROUTE_TECH_LEF_UNBOUND.
    import librelane_pv_signoff as _pv
    overlay.update(_pv.tech_lef_overlay(project) or {})
    ids = [REPAIR_STEP, *MEASURE_STEPS]
    configs = _ll.resolve_step_configs(project, image, pdk, ids, pdk_root=pdk_root,
                                       folder="32-config", overlay=overlay, docker=docker)
    extra = {}
    if pg_rules_tcl is not None:
        extra["VIBEIC_PRR_PG_RULES_TCL"] = (str(pg_rules_tcl.resolve()),
                                            "the direct deck's add_global_connection rules")
    if refill_tcl is not None:
        extra["VIBEIC_PRR_REFILL_TCL"] = (str(refill_tcl.resolve()),
                                          "the direct deck's own filler policy")
    if max_fanout is not None:
        extra["VIBEIC_PRR_MAX_FANOUT"] = max_fanout
    if dont_use is not None:
        excluded = repair_dont_use(configs[REPAIR_STEP], pdk_root, pdk, dont_use[0])
        if excluded:
            extra["EXTRA_EXCLUDED_CELLS"] = (excluded, dont_use[1])
    if extra:
        configs[REPAIR_STEP] = _ll.derive_step_config(configs[REPAIR_STEP],
                                                      configs[REPAIR_STEP], extra)
    corners = list(_load(configs["OpenROAD.STAPostPNR"]).get("STA_CORNERS") or [])
    return configs, corners, [(pdk_root / pdk, f"/pdk/{pdk}")]


def close_arm(project: Path, name: str, state0: Path, *, image: str, pdk: str,
              configs: Dict[str, Path], corners: List[str],
              mounts: List[Tuple[Path, str]], controllers: Sequence[str] = CONTROLLERS,
              registry: Optional[Path] = None,
              programs_dir: Optional[Path] = None,
              floors: Optional[Dict[str, Tuple[float, str]]] = None) -> Dict[str, Any]:
    """One repair arm from `state0`: the census baseline (same instruments as
    every candidate), then the closure's controllers in order. With no
    controllers it is the measurement of `state0` alone (a route that is its
    own arm, e.g. the pre-DRT repair's). `name == "librelane"` keeps the
    single-arm layout (`ARM_REL/impl`, lanes `32-*`)."""
    import librelane_contract as _ll
    from _ppa import closure as _cl
    arm = project / ARM_REL if name == "librelane" else project / ARM_REL / name
    impl = arm / IMPL_DIR
    impl.mkdir(parents=True, exist_ok=True)
    lane = "32" if name == "librelane" else f"32-{name}"
    ctx = {"project": str(project.resolve()), "image": image, "pdk": pdk,
           "mounts": [(str(h.resolve()), g) for h, g in mounts],
           "configs": {k: str(v.resolve()) for k, v in configs.items()},
           "corners": corners, "lane": lane, "arm": name,
           "floors": {k: list(v) for k, v in (floors or {}).items()}}
    write_json(impl / CONTEXT, ctx)
    ledger = _ledger_path(impl)
    if ledger.is_file():
        ledger.unlink()
    census = _ll.derive_step_config(
        configs[REPAIR_STEP], configs[REPAIR_STEP].with_name(f"{REPAIR_STEP}@census.json"),
        {"VIBEIC_PRR_CENSUS_ONLY": (True, "the input route's census: repair nothing")})
    folder0, baseline = _candidate(ctx, census, state0, f"{lane}-base")
    write_json(impl / CURRENT, {"candidate": None, "repair_input": str(state0),
                                "repair_state": str(state0), "repair_folder": str(folder0),
                                "measurement": baseline})
    report: Dict[str, Any] = {"arm": name, "input_state": str(state0), "corners": corners,
                              "floors": ctx["floors"],
                              "baseline": baseline,
                              "baseline_antenna": baseline.get("antenna_nets"),
                              # The census step's own metrics on the arm's input
                              # route (its unrouted census): what a promoter of
                              # THIS route, with no candidate, measured.
                              "baseline_repair_metrics": {
                                  k: v for k, v in (_load(folder0 / "state_out.json")
                                                    .get("metrics") or {}).items()
                                  if k.startswith("vibeic__prr__")}}
    runs = []
    if controllers:
        reg = _cl.load_registry(registry, programs_dir=programs_dir)
        controller = _cl.ClosureController(reg, impl, arm / "closure")
        for cid in controllers:
            runs.append(controller.run_controller(cid).to_record())
    report["closure"] = runs
    candidates = _load(ledger)["candidates"] if ledger.is_file() else []
    # One ledger row per actuation, in order: the closure's verdict on each.
    actuated = [(r.get("controller"), it) for r in runs
                for it in r.get("iterations") or [] if it.get("argv")]
    for row, (cid, it) in zip(candidates, actuated):
        row["controller"] = cid
        row["closure_decision"] = it.get("decision")
        row["closure_reason"] = it.get("decision_reason")
    report["candidates"] = candidates
    final = _load(impl / CURRENT)
    report["adopted"] = final.get("candidate")
    report["final"] = final.get("measurement")
    report["final_antenna"] = (final.get("measurement") or {}).get("antenna_nets")
    report["final_supply_ownership"] = final.get("supply_ownership")
    report["adopted_state"] = final.get("repair_state")
    return report


def run(project: Path, *, image: str, pdk: str, pdk_root: Path,
        views: Dict[str, Path], sdc: Path, derate: Tuple[float, float],
        pg_rules_tcl: Optional[Path] = None, refill_tcl: Optional[Path] = None,
        registry: Optional[Path] = None, programs_dir: Optional[Path] = None,
        docker: str = "docker",
        dont_use: Optional[Tuple[Callable[[str], List[str]], str]] = None,
        max_fanout: Optional[Tuple[int, str]] = None) -> Dict[str, Any]:
    """Step 32 on LibreLane after a DIRECT route: bridge the routed views,
    then one closure arm. Returns the report (also written to `REPORT_REL`).
    `adopted` is the candidate the closure left adopted (`None`: the input
    route stays)."""
    import librelane_contract as _ll
    report: Dict[str, Any] = {"step": STEP, "mode": "librelane", "image": image,
                              "site": "after_direct_route"}
    out = project / REPORT_REL
    if _refused_by_tool(report, fork_capability(image, docker), out):
        return report
    configs, corners, mounts = _prepare(
        project, image=image, pdk=pdk, pdk_root=pdk_root, sdc=sdc, derate=derate,
        pg_rules_tcl=pg_rules_tcl, refill_tcl=refill_tcl, docker=docker,
        dont_use=dont_use, max_fanout=max_fanout)
    state0 = _ll.state_from_direct(project, image, configs[REPAIR_STEP], views,
                                   project / "phase3/librelane/32-config/bridge",
                                   mounts=mounts,
                                   chain=[configs[s] for s in MEASURE_STEPS],
                                   docker=docker)
    report.update(close_arm(project, "librelane", state0, image=image, pdk=pdk,
                            configs=configs, corners=corners, mounts=mounts,
                            registry=registry, programs_dir=programs_dir,
                            floors=declared_timing_floor(project, sdc)))
    report["verdict"] = "PASS"
    write_json(out, report)
    return report


#: review70 step 32, dual_tool_option: arm A is the repair BEFORE detailed
#: routing (LibreLane RepairDesignPostGRT + ResizerTimingPostGRT, switched on
#: by the flow's own gates), arm B is this step's repair AFTER it, and the two
#: can be chained. The variables are LibreLane's own flow gates.
PREGRT_GATES = ("RUN_POST_GRT_DESIGN_REPAIR", "RUN_POST_GRT_RESIZER_TIMING")
DUAL_ARMS = ("postdrt", "pregrt", "pregrt_postdrt")
#: The review's "better": per-corner hold and setup worst slack (STAPostPNR),
#: with router DRC and antenna held at 0 (feasibility) and DRV too.
DUAL_OBJECTIVES = {"hold_ws_min": "max", "setup_ws_min": "max"}


def _arm_gate(folder: Path, arm: Dict[str, Any], route_drc: Optional[int],
              scope: Dict[str, str]) -> Path:
    final = arm.get("final") or {}
    rows = {k: ({"status": "MEASURED", "value": final.get(k)}
                if isinstance(final.get(k), (int, float)) else {"status": "NOT_MEASURED"})
            for k in (*DUAL_OBJECTIVES, "drv_count", "antenna_nets")}
    rows["route_drc"] = ({"status": "MEASURED", "value": route_drc}
                         if isinstance(route_drc, int) else {"status": "NOT_MEASURED"})
    verdict = "PASS" if all(r["status"] == "MEASURED" for r in rows.values()) else "NOT_MEASURED"
    path = folder / "gate.json"
    write_json(path, {"verdict": verdict, "metrics": rows, "scope": scope})
    return path


def select_dual(project: Path, arms: Dict[str, Dict[str, Any]],
                route_drc: Dict[str, Optional[int]], scope: Dict[str, str]) -> Dict[str, Any]:
    """Feasible first (route DRC 0, antenna 0, DRV 0, all measured), then the
    Pareto frontier on worst hold and setup slack (`select_arms`); a frontier
    tie goes to hold, then setup, then the arm order of `DUAL_ARMS` (the
    cheaper arm first: no extra route)."""
    import librelane_contract as _ll
    root = project / ARM_REL
    gates = {n: _arm_gate(root / "gates" / n, a, route_drc.get(n), scope)
             for n, a in arms.items()}
    docs = {n: _load(p) for n, p in gates.items()}

    def value(n: str, k: str) -> Any:
        return docs[n]["metrics"][k].get("value")

    feasible = [n for n in arms if docs[n]["verdict"] == "PASS"
                and value(n, "route_drc") == 0 and value(n, "antenna_nets") == 0
                and value(n, "drv_count") == 0]
    pool = feasible or [n for n in arms if docs[n]["verdict"] == "PASS"]
    if not pool:
        sel = {"selection": "UNDETERMINED", "reason": "LL_PRR_DUAL_NOT_MEASURED",
               "arms": list(arms)}
        write_json(root / "selection.json", sel)
        return sel
    sel = _ll.select_arms({n: gates[n] for n in pool}, DUAL_OBJECTIVES,
                          root / "selection.json")
    sel = dict(sel, feasible=feasible, considered=pool)
    if sel.get("selection") not in pool:
        frontier = sel.get("frontier") or pool
        order = {n: i for i, n in enumerate(DUAL_ARMS)}
        sel = dict(sel, selection=min(frontier, key=lambda n: (
            -value(n, "hold_ws_min"), -value(n, "setup_ws_min"), order.get(n, 99))),
                   tie_break="review70 step 32: hold, then setup, then the arm "
                             "without an extra route")
    write_json(root / "selection.json", sel)
    return sel


def run_in_chain(project: Path, *, mode: str, image: str, pdk: str, pdk_root: Path,
                 sdc: Path, derate: Tuple[float, float], route_state: Path,
                 route_drc: Optional[int],
                 variant_arm: Optional[Callable[[str, Dict[str, Tuple[Any, str]]],
                                                Dict[str, Any]]] = None,
                 pg_rules_tcl: Optional[Path] = None,
                 registry: Optional[Path] = None, programs_dir: Optional[Path] = None,
                 docker: str = "docker",
                 dont_use: Optional[Tuple[Callable[[str], List[str]], str]] = None,
        max_fanout: Optional[Tuple[int, str]] = None,
                 ) -> Dict[str, Any]:
    """Step 32 on LibreLane INSIDE the step-21 LibreLane chain
    (LL21 -> Vibeic.PostRouteRepair -> tail): the routed State is the
    selected route arm's own (no DEF crosses a session), and the route's
    fillers are LibreLane's (`OpenROAD.FillInsertion`), so the refill is
    LibreLane's too.

    `dual`: arm `postdrt` (this repair on the route), arm `pregrt` (step 21's
    LibreLane route again with the flow's post-GRT repair gates on, measured
    by the same instruments), and arm `pregrt_postdrt` (this repair on that
    route); `select_dual` picks."""
    report: Dict[str, Any] = {"step": STEP, "mode": mode, "image": image,
                              "site": "after_route", "route_state": str(route_state)}
    out = project / REPORT_REL
    if _refused_by_tool(report, fork_capability(image, docker), out):
        return report
    configs, corners, mounts = _prepare(
        project, image=image, pdk=pdk, pdk_root=pdk_root, sdc=sdc, derate=derate,
        pg_rules_tcl=pg_rules_tcl, refill_tcl=None, docker=docker,
        dont_use=dont_use, max_fanout=max_fanout)
    common = dict(image=image, pdk=pdk, configs=configs, corners=corners, mounts=mounts,
                  registry=registry, programs_dir=programs_dir,
                  floors=declared_timing_floor(project, sdc))
    if mode != "dual":
        report.update(close_arm(project, "librelane", route_state, **common))
        report["verdict"] = "PASS"
        write_json(out, report)
        return report
    if variant_arm is None:
        raise ValueError("LL_PRR_DUAL_NEEDS_LL21: the pre-DRT arm is step 21's "
                         "LibreLane route; select 21 librelane or dual")
    arms: Dict[str, Dict[str, Any]] = {}
    drcs: Dict[str, Optional[int]] = {}
    arms["postdrt"] = close_arm(project, "postdrt", route_state, **common)
    drcs["postdrt"] = route_drc
    pre = variant_arm("21-route-pregrt", {
        g: (True, "review70 step 32 dual arm A: the flow's own post-GRT repair")
        for g in PREGRT_GATES})
    runs = pre.get("route_drc") or []
    pre_drc = runs[-1].get("markers") if runs else None
    arms["pregrt"] = close_arm(project, "pregrt", Path(pre["final"]), controllers=(),
                               **common)
    drcs["pregrt"] = pre_drc
    arms["pregrt_postdrt"] = close_arm(project, "pregrt_postdrt", Path(pre["final"]),
                                       **common)
    drcs["pregrt_postdrt"] = pre_drc
    scope = {"step": STEP, "corners": ",".join(corners),
             "measured_by": "OpenROAD.CheckAntennas + RCX + STAPostPNR (sign-off scene)"}
    sel = select_dual(project, arms, drcs, scope)
    report.update(arms=arms, route_drc=drcs, selection=sel)
    chosen = arms.get(sel.get("selection"))
    if chosen is None:
        report.update(verdict="NOT_MEASURED", code="LL_PRR_DUAL_UNDETERMINED")
        write_json(out, report)
        return report
    report.update({k: chosen[k] for k in ("baseline", "final", "adopted", "adopted_state",
                                           "final_antenna", "baseline_antenna",
                                           "final_supply_ownership", "candidates",
                                           "closure", "baseline_repair_metrics")
                   if k in chosen})
    report["selected_arm"] = sel["selection"]
    report["verdict"] = "PASS"
    write_json(out, report)
    return report


def handoff(project: Path, report: Dict[str, Any], targets: Dict[str, Path]) -> Dict[str, Any]:
    """The adopted candidate's views onto the paths the direct flow reads."""
    import librelane_contract as _ll
    return _ll.handoff_to_direct(Path(report["adopted_state"]), targets,
                                 project / "reports/phase3/librelane_postroute_repair_handoff.json")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    m = sub.add_parser("measure", help="closure measurement over the adopted candidate")
    m.add_argument("--domain", choices=sorted(DOMAIN_VALUE), required=True)
    m.add_argument("--json", type=Path, required=True)
    a = sub.add_parser("actuate", help="one repair candidate from the adopted state")
    for name in PARAM_VARS:
        a.add_argument("--" + name.replace("_", "-"), type=float, default=None)
    args = parser.parse_args(argv)
    impl = Path.cwd()
    if args.command == "measure":
        return measure(impl, args.domain, args.json)
    params = {k: getattr(args, k) for k in PARAM_VARS}
    return actuate(impl, params)


if __name__ == "__main__":
    sys.exit(main())
