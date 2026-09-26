#!/usr/bin/env python3
"""Steps 28, 29 and 33 read the post-route state the tool already produced.

T100 (steps 22/23) runs LibreLane `OpenROAD.STAPostPNR` for every STA corner
and records the step in `reports/phase3/sta_postpnr_signoff.json`. That step
writes, per corner, an SDF (`write_sdf -include_typ -divider . -corner <c>`)
and a `report_power -corner <c>` report. Steps 29 and 33 CONSUME that state;
they never re-run it:

* ``stapostpnr_state``: the recorded state, refused by name when step 23 did
  not run on the tool (`LL_STAPOSTPNR_STATE_ABSENT`) or when the state on disk
  is not the one step 23 recorded (`LL_STAPOSTPNR_STATE_DRIFT`).
* step 29: ``gate_level_sim`` runs the design's L10 suite against every corner
  SDF as the LibreLane custom step `Vibeic.GateLevelSim`, and
  ``sdf_gate_sim.judge_tool_arm`` judges it.
* step 33: the per-corner power is `_ppa.power.stapostpnr_corner_power`; this
  module only locates the state.
* step 28: ``tap_pitch`` reads `FP_TAPCELL_DIST` from a LibreLane-resolved step
  config (the PDK's own LibreLane declaration as the tool resolved it), and
  ``perc_tool_metrics`` reads antenna / PDN / IR metrics from the LibreLane
  step states the run holds. IR is a declared seam (`PERC_IR_TOOL_SEAM`)
  until step 24 lands on the tool.

chip-AGNOSTIC: no design, PDK or corner literal selects a branch.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _atomic_artefact import write_json  # noqa: E402
from librelane_contract import (Refusal, _load, _state_step_id, digest,  # noqa: E402
                                resolve_step_configs, run_chain)

#: Where step 23 records the STAPostPNR run it signed off (T100).
STEP23_RECORD = 'reports/phase3/sta_postpnr_signoff.json'
GLS_STEP = 'Vibeic.GateLevelSim'


def stapostpnr_state(project: Path) -> Tuple[Path, dict]:
    """(step folder, state) of the STAPostPNR run step 23 recorded."""
    record = project / STEP23_RECORD
    if not record.is_file():
        raise Refusal('LL_STAPOSTPNR_STATE_ABSENT',
                      f'{record}: step 23 did not run on LibreLane')
    doc = _load(record)
    state_path = Path(str(doc.get('sta_state') or ''))
    if not state_path.is_file():
        raise Refusal('LL_STAPOSTPNR_STATE_ABSENT', f'{record}: sta_state {state_path}')
    if digest(state_path) != doc.get('sta_state_sha256'):
        raise Refusal('LL_STAPOSTPNR_STATE_DRIFT',
                      f'{state_path} is not the state step 23 recorded')
    if _state_step_id(state_path.parent) != 'OpenROAD.STAPostPNR':
        raise Refusal('LL_NOT_STAPOSTPNR', str(state_path.parent))
    return state_path.parent, _load(state_path)


def corner_sdfs(state: dict) -> Dict[str, Path]:
    """{corner: SDF} from the state; every named file must exist."""
    sdfs = state.get('sdf') or {}
    if not isinstance(sdfs, dict) or not sdfs:
        raise Refusal('LL_STAPOSTPNR_NO_SDF', 'the STAPostPNR state names no SDF')
    missing = [c for c, p in sdfs.items() if not Path(p).is_file()]
    if missing:
        raise Refusal('LL_STAPOSTPNR_SDF_MISSING', ', '.join(sorted(missing)))
    return {c: Path(p) for c, p in sdfs.items()}


# --- step 29 -----------------------------------------------------------------

def gate_level_sim(project: Path, top: str, image: str, pdk_root: Path, pdk: str,
                   *, direct_sdfs: Optional[List[Path]] = None) -> dict:
    """Run the L10 suite against every STAPostPNR corner SDF (custom step),
    judge it, and write `reports/phase3/gls_corners.json`."""
    import sdf_gate_sim as sgs
    sta_folder, state = stapostpnr_state(project)
    sdfs = corner_sdfs(state)
    netlist = Path(str(state.get('nl') or ''))
    if not netlist.is_file():
        raise Refusal('LL_STATE_FILE_MISSING', f'nl: {netlist}')
    gls_dir = project / 'phase3/librelane/29-config/gls'
    configs = resolve_step_configs(
        project, image, pdk, [GLS_STEP], pdk_root=pdk_root, folder='29-config',
        overlay={'VIBEIC_GLS_MANIFEST': (str(gls_dir / 'gls_manifest.json'),
                                         'sdf_gate_sim.tool_arm_manifest (this run)')})
    config = _load(configs[GLS_STEP])
    if config.get('VIBEIC_GLS_MANIFEST') != str(gls_dir / 'gls_manifest.json'):
        raise Refusal('LL_STEP_CONFIG_MISSING', f'{GLS_STEP}: VIBEIC_GLS_MANIFEST not resolved')
    declared = [Path(str(p)) for key in ('CELL_VERILOG_MODELS', 'PAD_VERILOG_MODELS')
                for p in (config.get(key) or [])]
    host = {f'/pdk/{pdk}': str(pdk_root / pdk)}
    host_models = [Path(_map(str(p), host)) for p in declared]
    manifest = sgs.tool_arm_manifest(project, top, netlist, host_models, gls_dir,
                                     path_map={v: k for k, v in host.items()})
    folder = run_chain(project, image, [(GLS_STEP, configs[GLS_STEP],
                                         sta_folder / 'state_out.json')],
                       mounts=[(pdk_root / pdk, f'/pdk/{pdk}')], lane='29')[0]
    judged = sgs.judge_tool_arm(folder, manifest)
    judged.update({
        'step': '29', 'arm': 'librelane:OpenROAD.STAPostPNR SDF x Vibeic.GateLevelSim',
        'sta_state': str(sta_folder / 'state_out.json'),
        'sta_state_sha256': digest(sta_folder / 'state_out.json'),
        'sdf_sha256': {c: digest(p) for c, p in sorted(sdfs.items())},
        'gls_step': str(folder),
        'coverage': {'tool_sdf_corners': len(sdfs),
                     'direct_sdf_corners': len(direct_sdfs or [])},
        'model_closure': manifest['model_closure'],
    })
    write_json(project / 'reports/phase3/gls_corners.json', judged)
    return judged


def _map(path: str, prefixes: Dict[str, str]) -> str:
    for guest, host in prefixes.items():
        if path.startswith(guest):
            return host + path[len(guest):]
    return path


# --- step 28 -----------------------------------------------------------------

#: The step-28 IR category's tool source is step 24 on LibreLane
#: (`OpenROAD.IRDropReport`, lane T101). Until that lane records its state the
#: IR category keeps the direct `ir_drop.json`: a named seam, not a guess.
PERC_IR_TOOL_SEAM = 'PERC_IR_TOOL_SEAM: step 24 has no LibreLane state in this run'

#: Metric -> PERC category. A category reads the tool metric only when EVERY
#: metric it names is present in one LibreLane state.
PERC_TOOL_METRICS = {
    'Antenna': ('antenna__violating__nets', 'antenna__violating__pins',
                'route__antenna_violation__count'),
    'PDN connectivity': ('design__power_grid_violation__count',),
}


def _librelane_states(project: Path) -> List[Path]:
    root = project / 'phase3/librelane'
    if not root.is_dir():
        return []
    return sorted((p for p in root.rglob('state_out.json') if 'attempts' not in p.parts),
                  key=lambda p: p.stat().st_mtime)


def perc_tool_metrics(project: Path) -> Dict[str, Any]:
    """Per PERC category, the newest LibreLane state carrying all its metrics:
    the values, the state path and its sha256. Absent -> not in the result."""
    out: Dict[str, Any] = {}
    for state_path in _librelane_states(project):
        metrics = _load(state_path).get('metrics') or {}
        for category, names in PERC_TOOL_METRICS.items():
            if all(isinstance(metrics.get(n), (int, float)) for n in names):
                out[category] = {'metrics': {n: metrics[n] for n in names},
                                 'state': str(state_path),
                                 'state_sha256': digest(state_path),
                                 'step': _state_step_id(state_path.parent)}
    return out


def tap_pitch(project: Path) -> Optional[Tuple[float, str]]:
    """`FP_TAPCELL_DIST` as LibreLane resolved it from the PDK's own config,
    from any resolved step config in the run, with the file it came from."""
    root = project / 'phase3/librelane'
    if not root.is_dir():
        return None
    for path in sorted(root.rglob('*.json')):
        if path.name.endswith(('.views.json', '.provenance.json')) or 'attempts' in path.parts:
            continue
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        value = doc.get('FP_TAPCELL_DIST') if isinstance(doc, dict) else None
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return float(value), f'{path.relative_to(project)}:FP_TAPCELL_DIST'
    return None
