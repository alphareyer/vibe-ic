#!/usr/bin/env python3
"""Opt-in steps 22 and 23 through LibreLane OpenROAD.RCX + OpenROAD.STAPostPNR.

Step 22 (parasitic extraction) is the tool's work: `OpenROAD.RCX` runs one
OpenRCX extraction per `RCX_RULESETS` corner, each with that corner's own tech
LEF, from the PDK's own LibreLane declaration.  The direct path read one tech
LEF for every corner.  The direct routed views reach the tool through the
contract's bridge (`state_from_direct`), and the tool's SPEFs return to the
paths the direct consumers read through `handoff_to_direct`, so step 23's
direct decks, step 27's SI screen, and DT2/DT3 read the tool's parasitics.

Step 23 (post-route sign-off STA) is `OpenROAD.STAPostPNR`: every STA corner
(process x RC) with that corner's SPEF, liberty and SDF.  What vibe-ic added to
the direct decks is carried into the tool's own corner process through
`STA_EXTRA_CORNER_TCL_FILE` (sourced by LibreLane's `sta/corner.tcl` after the
SPEF is read and before any report):

* the OCV derate, from the PDK's own `TIME_DERATING_CONSTRAINT` (a percent in
  the PDK's LibreLane config; divided by 100.0 here because LibreLane's
  `base.sdc` divides by the integer 100 and applies no derate at all for an
  integer percent -- fixed on the fork's main as upstream #1014, not in the
  0.3.79 image);
* recovery/removal/min-pulse-width checks (`report_check_types`), which the
  tool's corner script does not ask for;
* the report body the step-23 gates already read, emitted by OpenSTA inside the
  tool's corner process (`vibeic_signoff.rpt` beside `max.rpt`).

The derate has a declared source or none is applied: a PDK that declares no
`TIME_DERATING_CONSTRAINT` gets `OCV_DERATE_NOT_DECLARED` in that report,
which the sign-off rigor gate reads as the absence it is.

The dual for step 23 is an AGREEMENT check, not a pick: `agreement` re-times
every corner STAPostPNR analysed, with the same netlist, SDC, SPEF, liberty
files and extra Tcl, in the image's standalone OpenSTA (`sta`, a second
engine binary), and compares worst slack, TNS and violating-endpoint counts
for setup and hold.  A disagreement is an instrument refusal
(`LL_STA_ARMS_DISAGREE`), never a choice of the better number.

chip-AGNOSTIC: no design, PDK or corner literal selects a branch.
"""
from __future__ import annotations

import fnmatch
import json
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _atomic_artefact import write_json  # noqa: E402
import _docker_memory as _dmem  # noqa: E402 — every `docker run` carries the ceiling
from librelane_contract import (PDK_GUEST_ROOT, Refusal, _load, digest, handoff_to_direct,  # noqa: E402
                                resolve_step_configs, run_chain, run_container,
                                state_from_direct)

STEPS = ('OpenROAD.RCX', 'OpenROAD.STAPostPNR')
#: Where the extra corner Tcl lives in the project, and its provenance.
EXTRA_TCL = 'phase3/librelane/22-config/vibeic_signoff_corner.tcl'
DERATE_SOURCE = ("PDK LibreLane config TIME_DERATING_CONSTRAINT (percent), read "
                 "from the step's own environment by the corner process")
#: The report the extra Tcl writes into each STAPostPNR corner directory.
CORNER_REPORT = 'vibeic_signoff.rpt'
#: The marker the extra Tcl writes when the derate command succeeded.
DERATE_MARKER = 'OCV_DERATE_APPLIED'
NO_DERATE_MARKER = 'OCV_DERATE_NOT_DECLARED'

_DERATE_TCL = r'''
# --- vibe-ic step 23: OCV derate from the PDK's declared percent -----------
set _vibeic_rpt [file join $::env(_LIB_SAVE_DIR) vibeic_signoff.rpt]
set _vibeic_f [open $_vibeic_rpt w]
if {[info exists ::env(TIME_DERATING_CONSTRAINT)] && [string is double -strict $::env(TIME_DERATING_CONSTRAINT)]} {
  set _vibeic_d [expr {double($::env(TIME_DERATING_CONSTRAINT)) / 100.0}]
  set _vibeic_early [expr {1.0 - $_vibeic_d}]
  set _vibeic_late [expr {1.0 + $_vibeic_d}]
  # Two commands: this OpenSTA rejects -early and -late together (Error 110).
  set_timing_derate -early $_vibeic_early
  set_timing_derate -late $_vibeic_late
  puts $_vibeic_f "OCV_DERATE_APPLIED early=$_vibeic_early late=$_vibeic_late flat-OCV source=TIME_DERATING_CONSTRAINT=$::env(TIME_DERATING_CONSTRAINT)"
} else {
  puts $_vibeic_f "OCV_DERATE_NOT_DECLARED: the PDK declares no TIME_DERATING_CONSTRAINT"
}
puts $_vibeic_f "STA_BASIS: POST_ROUTE_SPEF"
puts $_vibeic_f "STA_BASIS_CORNER: $corner_name"
# The unit the corner's slack is printed in, asked of this OpenSTA (the
# direct decks' stamp): without it the step-23 audit publishes no slack in ns.
set _vibeic_tu ""
catch {set _vibeic_tu "[sta::unit_scale_abbreviation time][sta::unit_suffix time]"}
if {$_vibeic_tu ne ""} {
  puts $_vibeic_f "STA_TIME_UNIT: $_vibeic_tu"
} else {
  puts $_vibeic_f "STA_TIME_UNIT_NOT_STATED: this interpreter could not answer sta::unit_scale_abbreviation/unit_suffix for time"
}
close $_vibeic_f
'''


def write_extra_corner_tcl(project: Path, body: str = '') -> Path:
    """The derate block, then the caller's report body (vibe-ic's own
    report_checks/check-type emitters, pointed at `$_vibeic_rpt`)."""
    path = project / EXTRA_TCL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_DERATE_TCL.lstrip('\n') + (body.rstrip('\n') + '\n' if body else ''))
    return path


def run(project: Path, image: str, pdk_root: Path, pdk: str, *,
        routed_def: Path, netlist: Path, sdc: Path, lane: str = '22-23',
        extract: bool = True, time: bool = True,
        direct_spefs: Optional[dict[str, Path]] = None,
        report_body: str = '') -> dict:
    """direct routed views -> bridge -> RCX and/or STAPostPNR in the image.

    ``extract``: step 22 on the tool (RCX).  ``time``: step 23 on the tool
    (STAPostPNR).  With ``extract`` off, STAPostPNR times ``direct_spefs``
    (``{rc_corner: path}``, e.g. ``{'nom': ..., 'min': ..., 'max': ...}``),
    each bound to the PDK's own RCX corner pattern; a pattern with no direct
    SPEF refuses rather than timing a corner on a neighbour's parasitics.
    """
    if not (extract or time):
        raise Refusal('LL_SIGNOFF_NOTHING_SELECTED', 'neither step 22 nor 23')
    extra = write_extra_corner_tcl(project, report_body) if time else None
    overlay = ({'STA_EXTRA_CORNER_TCL_FILE': ('dir::' + EXTRA_TCL, DERATE_SOURCE +
                                              '; report body: vibe-ic step-23 emitters')}
               if extra else None)
    configs = resolve_step_configs(project, image, pdk, list(STEPS), pdk_root=pdk_root,
                                   folder='22-config', overlay=overlay)
    rulesets = _load(configs['OpenROAD.RCX']).get('RCX_RULESETS') or {}
    if not isinstance(rulesets, dict) or not rulesets:
        # A PDK that declares no OpenRCX rules is the LEF-RC/analytical
        # fallback's case (the direct path); the tool would warn and skip.
        raise Refusal('LL_RCX_RULESETS_UNDECLARED', f'{pdk}: RCX_RULESETS is empty')
    mounts = [(pdk_root / pdk, f'/pdk/{pdk}')]
    for source in (routed_def, netlist, sdc):
        if not Path(source).is_file():
            raise Refusal('LL_ROUTE_VIEW_MISSING', str(source))
    views: dict[str, Any] = {'def': routed_def, 'nl': netlist, 'sdc': sdc}
    steps = (['OpenROAD.RCX'] if extract else []) + (['OpenROAD.STAPostPNR'] if time else [])
    if not extract:
        spef: dict[str, Path] = {}
        for pattern in rulesets:
            source = (direct_spefs or {}).get(rc_corner(pattern))
            if source is None:
                raise Refusal('LL_DIRECT_SPEF_MISSING',
                              f'{pattern}: no {rc_corner(pattern)!r} entry among the '
                              f'direct corner SPEFs {sorted(direct_spefs or {})}')
            if not Path(source).is_file():
                raise Refusal('LL_DIRECT_SPEF_MISSING', f'{pattern}: no file at {source}')
            spef[pattern] = Path(source)
        views['spef'] = spef
    state = state_from_direct(project, image, configs[steps[0]], views,
                              project / 'phase3/librelane/22-config/bridge',
                              chain=[configs[s] for s in steps[1:]], mounts=mounts)
    folders = run_chain(project, image, [(s, configs[s], state) for s in steps],
                        mounts=mounts, lane=lane, pdk_root=PDK_GUEST_ROOT)
    spefs = _load(folders[0] / 'state_out.json').get('spef') or {}
    if not isinstance(spefs, dict) or not spefs:
        raise Refusal('LL_RCX_NO_SPEF', str(folders[0] / 'state_out.json'))
    return {'rcx': folders[0] if extract else None,
            'sta': folders[-1] if time else None,
            'spef': {pattern: Path(path) for pattern, path in spefs.items()},
            'configs': configs, 'extra_tcl': extra, 'mounts': mounts}


def rc_corner(pattern: str) -> str:
    """`nom_*` -> `nom`: the name RCX gives the corner's own directory."""
    return pattern.strip('*_')


def publish_spefs(result: dict, top: str, nominal: Path, corner_dir: Path,
                  receipt: Path) -> dict[str, Path]:
    """Hand the tool's SPEFs to the paths the direct consumers read."""
    targets: dict[str, Path] = {}
    for pattern in result['spef']:
        name = rc_corner(pattern)
        targets[f'spef:{pattern}'] = corner_dir / f'{top}.{name}.spef'
    nominal_pattern = next((p for p in result['spef'] if rc_corner(p) == 'nom'), None)
    if nominal_pattern is None:
        # The corners came from the state the first step of the chain wrote:
        # RCX's when step 22 ran on the tool, else STAPostPNR's.
        state_out = (result['rcx'] or result['sta']) / 'state_out.json'
        raise Refusal('LL_RCX_NOMINAL_MISSING',
                      f"no 'nom' corner among the SPEF corners "
                      f"{sorted(result['spef'])} in {state_out}")
    doc = handoff_to_direct(result['rcx'] / 'state_out.json', targets, receipt)
    # The nominal SPEF is the same tool file handed to a second consumer path
    # (one receipt maps a view to one destination).
    nominal_doc = handoff_to_direct(result['rcx'] / 'state_out.json',
                                    {f'spef:{nominal_pattern}': nominal},
                                    receipt.with_name(receipt.stem + '_nominal.json'))
    handed = {rc_corner(view.partition(':')[2]): Path(row['dest'])
              for view, row in doc['views'].items()}
    handed['__nominal__'] = Path(nominal_doc['views'][f'spef:{nominal_pattern}']['dest'])
    return handed


# --- SPEF census -----------------------------------------------------------

def spef_census(path: Path) -> dict:
    """Per-net total C and the split into grounded and coupling entries, in pF.

    IEEE 1481: `*C_UNIT <n> <FF|PF>`, `*NAME_MAP` (`*<id> <name>`), and per net
    `*D_NET <net> <total>` followed by `*CAP` rows `<i> <node> <value>`
    (grounded) or `<i> <node> <node> <value>` (coupling).  A coupling
    capacitor is listed under each of its two nets, so `coupling_pf` counts it
    once per net, the way the net totals do.
    """
    scale = None
    names: dict[str, str] = {}
    nets: dict[str, float] = {}
    ground = coupling = 0.0
    coupling_rows = 0
    section = None
    current = None
    with Path(path).open(errors='replace') as stream:
        for raw in stream:
            words = raw.split()
            if not words:
                continue
            head = words[0]
            if head == '*C_UNIT' and len(words) >= 3:
                unit = {'FF': 1e-3, 'PF': 1.0}.get(words[2].upper())
                if unit is None:
                    raise Refusal('LL_SPEF_UNIT_UNKNOWN', f'{path}: {raw.strip()}')
                scale = float(words[1]) * unit
            elif head == '*NAME_MAP':
                section = 'map'
            elif section == 'map' and head.startswith('*') and head[1:].isdigit() and len(words) >= 2:
                names[head] = words[1]
            elif head == '*D_NET' and len(words) >= 3:
                section = 'net'
                current = names.get(words[1], words[1])
                nets[current] = float(words[2])
            elif head == '*CAP':
                section = 'cap'
            elif head in ('*RES', '*CONN', '*END', '*INDUC'):
                section = 'net' if head != '*END' else None
            elif section == 'cap' and head.isdigit():
                if len(words) == 3:
                    ground += float(words[2])
                elif len(words) >= 4:
                    coupling += float(words[3])
                    coupling_rows += 1
    if scale is None:
        raise Refusal('LL_SPEF_UNIT_UNDECLARED', str(path))
    return {'path': str(path), 'sha256': digest(Path(path)), 'unit_pf': scale,
            'nets': {n: v * scale for n, v in nets.items()},
            'net_count': len(nets), 'total_pf': sum(nets.values()) * scale,
            'ground_pf': ground * scale, 'coupling_pf': coupling * scale,
            'coupling_rows': coupling_rows}


def compare_extraction(direct: dict[str, Path], tool: dict[str, Path],
                       sampled_nets: list[str]) -> dict:
    """Per RC corner: totals, and per-net deltas on the sampled nets."""
    rows: dict[str, Any] = {}
    for corner in sorted(set(direct) | set(tool)):
        if corner not in direct or corner not in tool:
            rows[corner] = {'status': 'NOT_MEASURED',
                            'missing': 'direct' if corner not in direct else 'tool'}
            continue
        a, b = spef_census(direct[corner]), spef_census(tool[corner])
        nets = []
        for net in sampled_nets:
            ca, cb = a['nets'].get(net), b['nets'].get(net)
            nets.append({'net': net, 'direct_pf': ca, 'tool_pf': cb,
                         'delta_pct': (None if ca in (None, 0) or cb is None
                                       else round(100.0 * (cb - ca) / ca, 3))})
        rows[corner] = {'status': 'MEASURED',
                        'direct': {k: a[k] for k in ('path', 'sha256', 'net_count', 'total_pf',
                                                     'ground_pf', 'coupling_pf', 'coupling_rows')},
                        'tool': {k: b[k] for k in ('path', 'sha256', 'net_count', 'total_pf',
                                                   'ground_pf', 'coupling_pf', 'coupling_rows')},
                        'total_delta_pct': round(100.0 * (b['total_pf'] - a['total_pf'])
                                                 / a['total_pf'], 3) if a['total_pf'] else None,
                        'nets_only_direct': len(set(a['nets']) - set(b['nets'])),
                        'nets_only_tool': len(set(b['nets']) - set(a['nets'])),
                        'sampled': nets}
    return rows


# --- step 22 dual: accuracy against a field-solver reference ---------------

#: The step-22 dual selection criterion: per-net total C against the
#: field-solver reference (`rcx_field_solver_reference.py`), smaller is better.
ACCURACY_OBJECTIVES = {'c_mean_abs_err_pct': 'min'}


def load_reference(path: Path) -> dict:
    """A field-solver reference record, refused unless it states nets."""
    ref = _load(Path(path))
    nets = ref.get('nets')
    if ref.get('kind') != 'field_solver_reference' or not isinstance(nets, dict) or not nets:
        raise Refusal('LL_RC_REFERENCE_EMPTY', f'{path}: no field-solver nets')
    if any(not isinstance(v, (int, float)) or not v > 0 for v in nets.values()):
        raise Refusal('LL_RC_REFERENCE_NONPOSITIVE', str(path))
    return ref


def arm_accuracy(spef: Path, reference: dict, reference_sha: str) -> dict:
    """One arm's report in `select_arms`' shape: per-net total C error against
    the reference, MEASURED only when every reference net is in the SPEF."""
    census = spef_census(spef)
    rows, missing = [], []
    for net, ref_pf in sorted(reference['nets'].items()):
        got = census['nets'].get(net)
        if got is None:
            missing.append(net)
            continue
        rows.append({'net': net, 'reference_pf': ref_pf, 'arm_pf': got,
                     'err_pct': 100.0 * (got - ref_pf) / ref_pf})
    errs = sorted(r['err_pct'] for r in rows)
    measured = bool(rows) and not missing
    value = (sum(abs(e) for e in errs) / len(errs)) if measured else None
    return {'verdict': 'PASS' if measured else 'NOT_MEASURED',
            'scope': {'reference_sha256': reference_sha,
                      'rc_corner': reference.get('rc_corner'),
                      'nets': len(reference['nets'])},
            'metrics': {'c_mean_abs_err_pct': {
                'status': 'MEASURED' if measured else 'NOT_MEASURED', 'value': value}},
            'c_median_err_pct': errs[len(errs) // 2] if errs else None,
            'c_sum_err_pct': (100.0 * (sum(r['arm_pf'] for r in rows)
                                       - sum(r['reference_pf'] for r in rows))
                              / sum(r['reference_pf'] for r in rows)) if rows else None,
            'missing_nets': missing, 'spef': str(spef), 'spef_sha256': census['sha256'],
            'rows': rows}


def accuracy_selection(arms: dict[str, Path], reference_path: Path, out_dir: Path) -> dict:
    """The step-22 dual selection: each arm's SPEF (at the reference's RC
    corner) against the field-solver reference, picked by `select_arms` on
    ACCURACY_OBJECTIVES.  Equal accuracy is a tie: both arms are kept."""
    from librelane_contract import select_arms
    reference = load_reference(reference_path)
    sha = digest(Path(reference_path))
    out_dir.mkdir(parents=True, exist_ok=True)
    reports = {}
    for name, spef in sorted(arms.items()):
        reports[name] = out_dir / f'{name}.json'
        write_json(reports[name], arm_accuracy(Path(spef), reference, sha))
    verdict = select_arms(reports, ACCURACY_OBJECTIVES, out_dir / 'selection.json')
    return {**verdict, 'criterion': 'accuracy against the field-solver reference',
            'objectives': ACCURACY_OBJECTIVES, 'reference': str(reference_path),
            'reference_sha256': sha, 'reports': {k: str(v) for k, v in reports.items()}}


# --- per-corner timing from the tool's own records -------------------------

#: LibreLane STAPostPNR metric names, per corner (`<metric>__corner:<name>`).
TIMING_METRICS = {
    'setup_ws': 'timing__setup__ws', 'setup_tns': 'timing__setup__tns',
    'setup_vio': 'timing__setup_vio__count',
    'hold_ws': 'timing__hold__ws', 'hold_tns': 'timing__hold__tns',
    'hold_vio': 'timing__hold_vio__count',
    'max_slew_vio': 'design__max_slew_violation__count',
    'max_cap_vio': 'design__max_cap_violation__count',
    'max_fanout_vio': 'design__max_fanout_violation__count',
}


def corner_timing(sta_folder: Path) -> dict:
    """Per corner the step analysed: the TIMING_METRICS, the derate the
    corner applied (from its own report), and whether any clock stayed ideal."""
    state = _load(sta_folder / 'state_out.json')
    metrics = dict(state.get('metrics') or {})
    if (sta_folder / 'metrics.json').is_file():
        metrics.update(_load(sta_folder / 'metrics.json'))
    corners: dict[str, Any] = {}
    for folder in sorted(p for p in sta_folder.iterdir() if (p / 'sta.log').is_file()):
        name = folder.name
        row: dict[str, Any] = {}
        for key, metric in TIMING_METRICS.items():
            value = metrics.get(f'{metric}__corner:{name}')
            row[key] = value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
        report = folder / CORNER_REPORT
        text = report.read_text(errors='replace') if report.is_file() else ''
        derate = next((line for line in text.splitlines() if line.startswith(DERATE_MARKER)), None)
        row['derate'] = (derate if derate else
                         NO_DERATE_MARKER if NO_DERATE_MARKER in text else None)
        unpropagated = folder / 'unpropagated.rpt'
        row['ideal_clocks'] = ([line.strip() for line in unpropagated.read_text().splitlines()
                                if line.strip()] if unpropagated.is_file() else None)
        row['report'] = str(report) if report.is_file() else None
        corners[name] = row
    return corners


def judge_timing(corners: dict) -> dict:
    """PASS only when every analysed corner measured every metric, applied a
    declared derate (or recorded that none is declared), timed propagated
    clocks, and has no violation.  Missing = NOT_MEASURED, never 0."""
    if not corners:
        return {'verdict': 'NOT_MEASURED', 'reason': 'no analysed corner'}
    missing = {c: [k for k, v in row.items() if k in TIMING_METRICS and v is None]
               for c, row in corners.items()}
    missing = {c: v for c, v in missing.items() if v}
    no_derate = [c for c, row in corners.items() if row.get('derate') is None]
    ideal = {c: row['ideal_clocks'] for c, row in corners.items()
             if row.get('ideal_clocks') is None or row.get('ideal_clocks')}
    if missing or no_derate:
        return {'verdict': 'NOT_MEASURED', 'missing_metrics': missing,
                'derate_unrecorded': no_derate}
    if ideal:
        # An ideal clock on a post-route deck is the defect the direct path
        # fixed by hand (subservient r7); here the tool must say it propagated.
        return {'verdict': 'FAIL', 'reason': 'LL_POSTROUTE_IDEAL_CLOCK', 'ideal_clocks': ideal}
    failing = sorted(c for c, row in corners.items()
                     if row['setup_ws'] < 0 or row['hold_ws'] < 0 or row['setup_vio'] or row['hold_vio']
                     or row['max_slew_vio'] or row['max_cap_vio'])
    worst_setup = min(corners, key=lambda c: corners[c]['setup_ws'])
    worst_hold = min(corners, key=lambda c: corners[c]['hold_ws'])
    return {'verdict': 'FAIL' if failing else 'PASS', 'failing_corners': failing,
            'worst_setup': {'corner': worst_setup, 'ws': corners[worst_setup]['setup_ws']},
            'worst_hold': {'corner': worst_hold, 'ws': corners[worst_hold]['hold_ws']}}


# --- the step-23 gates' reader of the tool arm -----------------------------

#: The step-23 tool record `_librelane_signoff_record` writes.
SIGNOFF_RECORD = 'reports/phase3/sta_postpnr_signoff.json'
#: The views a STAPostPNR corner timed.  Each must still be the bytes the
#: tool was given, or its reports describe a design that is no longer there.
_TIMED_VIEWS = ('def', 'nl', 'sdc', 'spef')


def step23_tool_arm(project: Path, artefacts: tuple[str, ...] = (CORNER_REPORT,)) -> Optional[dict]:
    """What a step-23 gate judges when step 23 runs `librelane` or `dual`.

    ``None`` when step 23 runs ``direct`` (the gate reads the direct decks, as
    before).  Otherwise, per corner the tool declared (`STA_CORNERS` in the
    step's own config): the ``artefacts`` it wrote in that corner's directory
    (text + sha256), its metrics, and its scope.  The scope comes only from
    files the tool bound: the RC corner from the SPEF pattern the tool matched
    for the corner, and process / voltage / temperature from the cell
    liberty its config binds to the corner, read by `_ppa`'s own
    `parse_liberty_pvt` (an unreadable field stays a named gap, never a guess).

    Every file is checked against the sha256 the contract's receipt recorded
    when the tool wrote it, and every view the tool timed against the file on
    disk now.  Anything missing, unreadable, unbound or stale raises
    `Refusal`: a gate must refuse, never fall back to our own deck.
    """
    from librelane_contract import selected_mode
    from _ppa.backends.opensta import parse_liberty_pvt
    mode = selected_mode(project, '23')
    if mode == 'direct':
        return None
    record = project / SIGNOFF_RECORD
    try:
        doc = _load(record)
        state_path = Path(doc['sta_state'])
        folder = state_path.parent
        receipt = _load(folder / 'vibeic_receipt.json')
        state = _load(state_path)
        config = _load(folder / 'config.json')
    except (OSError, ValueError, KeyError, TypeError, Refusal) as exc:
        raise Refusal('LL_STA_TOOL_ARM_UNREADABLE', f'{record}: {exc}') from exc
    if doc.get('mode') != mode:
        raise Refusal('LL_STA_RECORD_STALE', f'{record} is mode {doc.get("mode")}, the switch says {mode}')
    bound = receipt.get('sha256') or {}
    for name in ('state_out.json', 'config.json'):
        if bound.get(name) != digest(folder / name):
            raise Refusal('LL_STA_ARTEFACT_UNBOUND', f'{folder / name}: not the bytes the tool wrote')
    if doc.get('sta_state_sha256') != bound['state_out.json']:
        raise Refusal('LL_STA_RECORD_STALE', f'{record} names another STAPostPNR state')
    timed = (receipt.get('input') or {}).get('state_files') or {}
    spefs = state.get('spef') or {}
    views = [state.get(v) for v in _TIMED_VIEWS if v != 'spef'] + list(spefs.values())
    for view in views:
        if not view or str(view) not in timed or not Path(view).is_file() \
                or digest(Path(view)) != timed[str(view)]:
            raise Refusal('LL_STA_INPUT_STALE', f'{view}: not the file the tool timed')
    declared = config.get('STA_CORNERS') or []
    if not declared:
        raise Refusal('LL_STA_NO_CORNER', f'{folder / "config.json"}: STA_CORNERS is empty')
    metrics = state.get('metrics') or {}
    corners: dict[str, Any] = {}
    for name in declared:
        matched = [p for p in spefs if fnmatch.fnmatch(name, p)]
        libs = [lib for p, row in (config.get('CELL_LIBS') or {}).items()
                if fnmatch.fnmatch(name, p) for lib in row]
        pvt = parse_liberty_pvt(libs[0]) if len(libs) == 1 else None
        gaps = dict(pvt.gaps) if pvt else {'process': f'{len(libs)} cell liberties bound'}
        row: dict[str, Any] = {
            'rc_corner': rc_corner(matched[0]) if len(matched) == 1 else None,
            'process': pvt.process if pvt else None,
            'voltage_v': pvt.voltage_v if pvt else None,
            'temperature_c': pvt.temperature_c if pvt else None,
            'liberty': libs[0] if len(libs) == 1 else None,
            'metrics': {k: metrics.get(f'{m}__corner:{name}') for k, m in TIMING_METRICS.items()},
            'files': {}}
        if len(matched) != 1:
            gaps['rc_corner'] = f'{len(matched)} SPEF patterns match'
        row['scope_gaps'] = gaps
        for artefact in artefacts:
            rel = f'{name}/{artefact}'
            path = folder / rel
            if not path.is_file():
                raise Refusal('LL_STA_CORNER_ARTEFACT_MISSING', str(path))
            sha = digest(path)
            if bound.get(rel) != sha:
                raise Refusal('LL_STA_ARTEFACT_UNBOUND', f'{path}: not the bytes the tool wrote')
            row['files'][artefact] = {'path': str(path), 'sha256': sha,
                                      'text': path.read_text(errors='replace')}
        corners[name] = row
    return {'mode': mode, 'record': str(record), 'record_sha256': digest(record),
            'folder': str(folder), 'state_sha256': bound['state_out.json'],
            'corners': corners}


def tool_arm_basis(arm: dict) -> dict:
    """The part of `step23_tool_arm` a gate records as its basis (no text)."""
    return {'producer': 'librelane:OpenROAD.STAPostPNR', 'mode': arm['mode'],
            'record': arm['record'], 'record_sha256': arm['record_sha256'],
            'state_sha256': arm['state_sha256'],
            'corners': {name: {**{k: v for k, v in row.items() if k != 'files'},
                               'files': {a: {k: v for k, v in f.items() if k != 'text'}
                                         for a, f in row['files'].items()}}
                        for name, row in arm['corners'].items()}}


# --- critical nets --------------------------------------------------------

#: `report_checks -fields {... net ...}` prints a net on its own row as
#: `<name> (net)`.
_NET_ROW = re.compile(r'^\s+(\S+) \(net\)\s*$', re.M)
_PATH_SPLIT = re.compile(r'^Startpoint: ', re.M)


def critical_nets(sta_folder: Path, corner: str, paths: int = 5) -> list[str]:
    """Nets on the `paths` worst setup and hold paths of `corner`, in order."""
    out: list[str] = []
    for name in ('max.rpt', 'min.rpt'):
        report = sta_folder / corner / name
        if not report.is_file():
            raise Refusal('LL_CORNER_REPORT_MISSING', str(report))
        for chunk in _PATH_SPLIT.split(report.read_text(errors='replace'))[1:paths + 1]:
            for net in _NET_ROW.findall(chunk):
                if net not in out:
                    out.append(net)
    return out


# --- the agreement arm: standalone OpenSTA on the same inputs ---------------

_STA_LIBRARY_RE = re.compile(
    r"^Reading cell library for the '([^']+)' corner at '([^']+)'", re.M)
_ARM_LINE = re.compile(r'^VIBEIC_ARM (\S+) (\S+)$', re.M)

_ARM_TCL = r'''set_cmd_units -time ns -capacitance pF -current mA -voltage V -resistance kOhm -distance um
foreach _l $::env(VIBEIC_ARM_LIBS) { read_liberty $_l }
read_verilog $::env(VIBEIC_ARM_NETLIST)
link_design $::env(VIBEIC_ARM_TOP)
set ::env(OPENLANE_SDC_IDEAL_CLOCKS) 0
read_sdc $::env(VIBEIC_ARM_SDC)
set_propagated_clock [all_clocks]
read_spef $::env(VIBEIC_ARM_SPEF)
set corner_name $::env(VIBEIC_ARM_CORNER)
set ::env(_LIB_SAVE_DIR) $::env(VIBEIC_ARM_DIR)
if {$::env(VIBEIC_ARM_EXTRA) ne ""} { source $::env(VIBEIC_ARM_EXTRA) }
foreach {_k _d} {setup max hold min} {
  puts "VIBEIC_ARM ${_k}_ws [worst_slack -$_d]"
  puts "VIBEIC_ARM ${_k}_tns [total_negative_slack -$_d]"
  puts "VIBEIC_ARM ${_k}_vio [llength [find_timing_paths -unique_paths_to_endpoint -path_delay $_d -group_path_count 999999999 -slack_max 0]]"
}
puts "VIBEIC_ARM max_slew_vio [sta::max_slew_violation_count]"
puts "VIBEIC_ARM max_cap_vio [sta::max_capacitance_violation_count]"
'''


def _arm_values(text: str) -> dict[str, Optional[float]]:
    values: dict[str, Optional[float]] = {}
    for key, raw in _ARM_LINE.findall(text):
        try:
            value = float(raw)
        except ValueError:
            value = None
        values[key] = value if value is None or math.isfinite(value) else None
    return values


def agreement(project: Path, image: str, sta_folder: Path, mounts: list[tuple[Path, str]],
              output: Path, *, tolerance_ns: float = 1e-3, docker: str = 'docker') -> dict:
    """Re-time every analysed corner in standalone `sta`; compare with the tool."""
    state = _load(sta_folder / 'state_out.json')
    config = _load(sta_folder / 'config.json') if (sta_folder / 'config.json').is_file() else {}
    tool = corner_timing(sta_folder)
    spefs = state.get('spef') or {}
    arms = project / 'phase3/tool_arms/23/opensta'
    arms.mkdir(parents=True, exist_ok=True)
    (arms / 'arm.tcl').write_text(_ARM_TCL)
    rows: dict[str, Any] = {}
    disagree: list[str] = []
    for corner, measured in tool.items():
        log = (sta_folder / corner / 'sta.log').read_text(errors='replace')
        libs = [path for name, path in _STA_LIBRARY_RE.findall(log) if name == corner]
        matched = [p for pattern, p in spefs.items() if fnmatch.fnmatch(corner, pattern)]
        if not libs or len(matched) != 1:
            rows[corner] = {'verdict': 'NOT_MEASURED', 'libs': libs, 'spef_matches': len(matched)}
            disagree.append(corner)
            continue
        folder = arms / corner
        folder.mkdir(parents=True, exist_ok=True)
        env = {'VIBEIC_ARM_LIBS': ' '.join(libs), 'VIBEIC_ARM_NETLIST': state['nl'],
               'VIBEIC_ARM_TOP': str(config.get('DESIGN_NAME') or ''),
               'VIBEIC_ARM_SDC': str(config.get('SIGNOFF_SDC_FILE') or state['sdc']),
               'VIBEIC_ARM_SPEF': matched[0], 'VIBEIC_ARM_CORNER': corner,
               'VIBEIC_ARM_DIR': str(folder),
               'VIBEIC_ARM_EXTRA': str(config.get('STA_EXTRA_CORNER_TCL_FILE') or ''),
               'TIME_DERATING_CONSTRAINT': str(config.get('TIME_DERATING_CONSTRAINT') or '')}
        if not env['VIBEIC_ARM_TOP']:
            raise Refusal('LL_STA_CONFIG_UNREAD', str(sta_folder / 'config.json'))
        volumes = ['-v', f'{project.resolve()}:{project.resolve()}']
        for host, guest in mounts:
            volumes += ['-v', f'{host.resolve()}:{guest}:ro']
        cmd = ['docker' if docker == 'docker' else docker, 'run', '--rm', '--network', 'none',
               *_dmem.docker_memory_flags(), *volumes]
        for key, value in env.items():
            cmd += ['-e', f'{key}={value}']
        cmd += ['--entrypoint', 'sta', image, '-no_init', '-no_splash', '-exit', str(arms / 'arm.tcl')]
        completed = run_container(cmd, supervised=True, log=folder / 'sta.log')
        (folder / 'sta.log').write_text(completed.stdout + '\n' + completed.stderr)
        arm = _arm_values(completed.stdout)
        compared: dict[str, Any] = {}
        ok = completed.returncode == 0
        for key in ('setup_ws', 'setup_tns', 'setup_vio', 'hold_ws', 'hold_tns', 'hold_vio',
                    'max_slew_vio', 'max_cap_vio'):
            a, b = arm.get(key), measured.get(key)
            same = (a is not None and b is not None and
                    (abs(a - b) <= tolerance_ns if not key.endswith('vio') else a == b))
            compared[key] = {'opensta': a, 'stapostpnr': b, 'agree': same}
            ok = ok and same
        rows[corner] = {'verdict': 'AGREE' if ok else 'DISAGREE', 'rc': completed.returncode,
                        'libs': libs, 'spef': matched[0], 'metrics': compared}
        if not ok:
            disagree.append(corner)
    verdict = ('NOT_MEASURED' if not rows else 'DISAGREE' if disagree else 'AGREE')
    document = {'verdict': verdict, 'tolerance_ns': tolerance_ns, 'corners': rows,
                'disagreeing_corners': disagree, 'tool_step': str(sta_folder),
                'tool_state_sha256': digest(sta_folder / 'state_out.json'),
                'arm': 'standalone OpenSTA (sta) in the same image, same netlist/SDC/SPEF/liberty/extra Tcl'}
    write_json(output, document)
    return document


# --- the deck arm: vibe-ic's own multi-corner OCV deck vs the tool ---------

_STANZA = re.compile(r'^=== (SETUP|HOLD) corner: .*?===\s*$', re.M)
_STANZA_FIELD = re.compile(
    r'^(STA_BASIS_LIBERTY|STA_BASIS_CORNER|STA_BASIS_SPEF|OCV_DERATE_APPLIED)[: ](.*)$', re.M)
_WORST = re.compile(r'^worst slack (max|min) (\S+)\s*$', re.M)
_DERATE_VALUES = re.compile(r'early=(\S+) late=(\S+)')


def _derate(text: Optional[str]) -> Optional[tuple[float, float]]:
    found = _DERATE_VALUES.search(text or '')
    try:
        return (float(found.group(1)), float(found.group(2))) if found else None
    except ValueError:
        return None


def deck_agreement(report_text: str, sta_folder: Path, output: Path, *,
                   spef_dir: Path, tolerance_ns: float = 0.006) -> dict:
    """Each SETUP/HOLD stanza of the direct multi-corner OCV report against
    the tool corner that read the same liberty and the same RC corner.

    Comparable only when both read the same SPEF bytes (the stanza's
    `STA_BASIS_SPEF`, resolved in ``spef_dir``) and applied the same derate;
    otherwise the stanza is NOT_COMPARABLE (declared, never a disagreement):
    a direct report timed before a re-extraction is stale, not wrong.  The direct report prints
    slack to two decimals, hence the default tolerance.
    """
    tool = corner_timing(sta_folder)
    state = _load(sta_folder / 'state_out.json')
    spefs = state.get('spef') or {}
    rows = []
    starts = [m for m in _STANZA.finditer(report_text)]
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(report_text)
        body = report_text[match.end():end]
        fields = dict(_STANZA_FIELD.findall(body))
        kind = match.group(1)
        worst = dict(_WORST.findall(body))
        value = worst.get('max' if kind == 'SETUP' else 'min')
        library = Path(fields.get('STA_BASIS_LIBERTY', '').strip()).name
        rc = fields.get('STA_BASIS_CORNER', '').strip()
        row: dict[str, Any] = {'stanza': kind, 'liberty': library, 'rc_corner': rc,
                               'direct_ws': float(value) if value not in (None, '') else None}
        candidates = []
        for corner in tool:
            log = (sta_folder / corner / 'sta.log').read_text(errors='replace')
            libs = {Path(p).name for n, p in _STA_LIBRARY_RE.findall(log) if n == corner}
            matched = [p for p, _ in spefs.items() if fnmatch.fnmatch(corner, p)]
            if library in libs and len(matched) == 1 and rc_corner(matched[0]) == rc:
                candidates.append((corner, Path(spefs[matched[0]])))
        direct_spef = spef_dir / fields.get('STA_BASIS_SPEF', '').strip()
        if len(candidates) != 1 or row['direct_ws'] is None:
            row.update(verdict='NOT_COMPARABLE', tool_corners=[c for c, _ in candidates])
        elif not (direct_spef.is_file() and candidates[0][1].is_file()
                  and digest(direct_spef) == digest(candidates[0][1])):
            row.update(verdict='NOT_COMPARABLE', tool_corner=candidates[0][0],
                       reason='the direct stanza and the tool corner read different SPEF bytes')
        else:
            corner = candidates[0][0]
            tool_ws = tool[corner]['setup_ws' if kind == 'SETUP' else 'hold_ws']
            same_derate = _derate(fields.get('OCV_DERATE_APPLIED')) == _derate(tool[corner]['derate'])
            row.update(tool_corner=corner, tool_ws=tool_ws,
                       direct_derate=_derate(fields.get('OCV_DERATE_APPLIED')),
                       tool_derate=_derate(tool[corner]['derate']))
            if not same_derate or tool_ws is None:
                row['verdict'] = 'NOT_COMPARABLE'
            else:
                row['verdict'] = ('AGREE' if abs(tool_ws - row['direct_ws']) <= tolerance_ns
                                  else 'DISAGREE')
        rows.append(row)
    compared = [r for r in rows if r['verdict'] in ('AGREE', 'DISAGREE')]
    verdict = ('NOT_MEASURED' if not compared else
               'DISAGREE' if any(r['verdict'] == 'DISAGREE' for r in compared) else 'AGREE')
    document = {'verdict': verdict, 'tolerance_ns': tolerance_ns, 'stanzas': rows,
                'tool_step': str(sta_folder)}
    write_json(output, document)
    return document


# --- step 27: OpenSTA scripts on the tool's own inputs, in the tool image --

def run_sta_script(project: Path, image: str, mounts: list[tuple[Path, str]],
                   script: Path, log: Path, *, docker: str = 'docker') -> subprocess.CompletedProcess:
    """Standalone `sta` in the image, with the project and the PDK mounted at
    the paths the tool's state names (the `/pdk/<pdk>` liberties)."""
    volumes = ['-v', f'{project.resolve()}:{project.resolve()}']
    for host, guest in mounts:
        volumes += ['-v', f'{host.resolve()}:{guest}:ro']
    completed = run_container([docker, 'run', '--rm', '--network', 'none',
                               *_dmem.docker_memory_flags(), *volumes,
                               '--entrypoint', 'sta', image, '-no_init', '-no_splash',
                               '-exit', str(script)], supervised=True, log=log)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(completed.stdout + '\n' + completed.stderr)
    return completed
