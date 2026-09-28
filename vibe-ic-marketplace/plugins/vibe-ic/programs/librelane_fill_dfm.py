#!/usr/bin/env python3
"""Steps 34 (fill) and 35 (DFM) on the tool, opt-in through the contract.

Step 34 in LibreLane's Chip-flow order
--------------------------------------
* ODB: `OpenROAD.FillInsertion` -- `filler_placement` over the PDK's own
  `DECAP_CELLS` then `FILL_CELLS`, and `write_views` re-applies the global
  connections, so every inserted cell's supply pins land on the supply nets
  (F24 / `pg_supply_pin_ownership_check` judges the DEF it wrote).  The cells
  it placed are recorded per master and per class (decap / fill) for the
  dynamic-IR decap (F20b).  A run that places nothing while the rows still
  have free sites is a NO-OP and refuses by name (#445).
* GDS, after the seal ring: `KLayout.Filler` (the PDK's own fill script) ->
  `KLayout.Density` (the PDK's density deck) -> `Checker.KLayoutDensity`.
  The density count is the step's measurement; the checker's refusal on a
  non-zero count is recorded beside it, never raised past it.

The direct arm (vibe-ic's three GDS fill passes) stays as the `dual` second
arm.  Both arms are measured by the SAME instrument (`KLayout.Density` with
the PDK deck), and `select_arms` picks the lower density-error count; a tie
keeps the direct arm, which the flow has always shipped.

Step 35
-------
`dfm_screen_check` stays an advisory vibe-ic gate.  Its CMP half reads the
density count recorded here (`tool_density`) instead of step 34's side files
whenever step 34 ran on the tool; its via half is cross-checked against the
router's own `route__vias__singlecut` / `route__vias__multicut`
(`via_agreement`).

chip-AGNOSTIC: every cell pattern, script, deck and die comes from the
PDK's LibreLane configuration as the installed tool resolved it, or from the
design's own files.
"""
from __future__ import annotations

import fnmatch
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _atomic_artefact import write_json  # noqa: E402
from librelane_contract import (PDK_GUEST_ROOT, Refusal, _load, digest,  # noqa: E402
                                resolve_step_configs, run_chain, select_arms,
                                state_from_direct)

ODB_FILL_STEP = 'OpenROAD.FillInsertion'
GDS_FILL_STEPS = ('KLayout.Filler', 'KLayout.Density', 'Checker.KLayoutDensity')
DENSITY_STEPS = ('KLayout.Density', 'Checker.KLayoutDensity')
DENSITY_METRIC = 'klayout__density_error__count'
#: The step-34 tool record (what dfm_screen_check reads for its CMP half).
RECORD_REL = 'reports/phase3/fill_librelane.json'
#: The cells step 34 placed, per master and class (F20b reads it).
CELLS_REL = 'reports/phase3/fill_cells_placed.json'
CONFIG_FOLDER = '34-config'


def _mounts(pdk_root: Path, pdk: str) -> list:
    return [(pdk_root / pdk, f'/pdk/{pdk}')]


def _metrics(folder: Path) -> Dict[str, Any]:
    state = _load(folder / 'state_out.json')
    metrics = dict(state.get('metrics') or {})
    if (folder / 'metrics.json').is_file():
        metrics.update(_load(folder / 'metrics.json'))
    return metrics


def _count(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def cell_patterns(config: Dict[str, Any]) -> Dict[str, List[str]]:
    """The decap and fill master patterns, exactly as `fill.tcl` strips them."""
    out = {}
    for cls, key in (('decap', 'DECAP_CELLS'), ('fill', 'FILL_CELLS')):
        raw = config.get(key) or []
        if isinstance(raw, str):
            raw = raw.split()
        out[cls] = [str(p).replace("'", '') for p in raw if str(p).strip()]
    return out


def _components(def_text: str) -> Dict[str, str]:
    """{instance: master} from a DEF COMPONENTS section."""
    match = re.search(r'^\s*COMPONENTS\s+\d+\s*;(.*?)^\s*END\s+COMPONENTS',
                      def_text, re.S | re.M)
    if not match:
        return {}
    return dict(re.findall(r'^\s*-\s+(\S+)\s+(\S+)', match.group(1), re.M))


def fill_census(before_def: str, after_def: str,
                patterns: Dict[str, List[str]]) -> Dict[str, Any]:
    """The instances `after_def` adds to `before_def`, per master and class.

    A class is the pattern family the master matches first (decap before fill,
    the order `fill.tcl` hands them to `filler_placement`).  An added instance
    that matches neither is counted as `other` and disclosed: fill insertion
    must add nothing else."""
    before = _components(before_def)
    after = _components(after_def)
    added = {inst: master for inst, master in after.items() if inst not in before}
    removed = sorted(inst for inst in before if inst not in after)
    per_master: Dict[str, int] = {}
    per_class: Dict[str, int] = {'decap': 0, 'fill': 0, 'other': 0}
    class_of: Dict[str, str] = {}
    for master in added.values():
        per_master[master] = per_master.get(master, 0) + 1
    for master, n in per_master.items():
        cls = next((c for c in ('decap', 'fill')
                    if any(fnmatch.fnmatchcase(master, p) for p in patterns.get(c, []))),
                   'other')
        class_of[master] = cls
        per_class[cls] += n
    return {'instances_before': len(before), 'instances_after': len(after),
            'added': len(added), 'removed': len(removed),
            'removed_examples': removed[:20],
            'per_master': dict(sorted(per_master.items())),
            'per_class': per_class, 'class_of_master': dict(sorted(class_of.items()))}


def chain_folder(project: Path, lane: str, steps, step: str) -> Path:
    """The run_chain folder of ``step`` inside ``lane``."""
    index = list(steps).index(step) + 1
    return project / 'phase3/librelane' / lane / f'{index:02d}-{step.lower().replace(".", "-")}'


def run_density(project: Path, image: str, pdk_root: Path, pdk: str, *,
                gds: Path, lane: str, configs: Optional[Dict[str, Path]] = None,
                steps=DENSITY_STEPS) -> Dict[str, Any]:
    """The PDK density deck on ``gds`` (`KLayout.Density`), then the tool's own
    checker.  The count is the measurement; a checker refusal (count > 0) is
    recorded beside it, never raised past it.  ``steps`` may lead with
    `KLayout.Filler` (the tool arm fills first)."""
    if not Path(gds).is_file():
        raise Refusal('LL_GDS_MISSING', f'no stream file at {Path(gds)}')
    configs = configs or resolve_step_configs(project, image, pdk, list(steps),
                                              pdk_root=pdk_root, folder=CONFIG_FOLDER)
    density_cfg = _load(configs['KLayout.Density'])
    if not density_cfg.get('KLAYOUT_DENSITY_RUNSET'):
        # The step would warn and skip: a skipped deck is not a clean die.
        raise Refusal('LL_DENSITY_RUNSET_UNDECLARED', f'{pdk}: KLAYOUT_DENSITY_RUNSET')
    if 'KLayout.Filler' in steps and not _load(configs['KLayout.Filler']).get('KLAYOUT_FILLER_SCRIPT'):
        raise Refusal('LL_FILLER_SCRIPT_UNDECLARED', f'{pdk}: KLAYOUT_FILLER_SCRIPT')
    mounts = _mounts(pdk_root, pdk)
    first = list(steps)[0]
    state = state_from_direct(project, image, configs[first], {'gds': gds},
                              project / 'phase3/librelane' / CONFIG_FOLDER / f'bridge-{lane}',
                              chain=[configs[s] for s in list(steps)[1:]], mounts=mounts)
    checker_error = None
    try:
        run_chain(project, image, [(s, configs[s], state) for s in steps],
                  mounts=mounts, lane=lane, pdk_root=PDK_GUEST_ROOT)
    except Refusal as error:
        if not str(error).startswith('LL_STEP_FAILED: Checker.KLayoutDensity'):
            raise
        checker_error = str(error)
    folder = chain_folder(project, lane, steps, 'KLayout.Density')
    count = _count(_metrics(folder).get(DENSITY_METRIC))
    if count is None:
        raise Refusal('LL_DENSITY_NOT_MEASURED', f'{folder}: {DENSITY_METRIC}')
    report = folder / 'reports' / 'density.klayout.json'
    out = {'step': 'KLayout.Density', 'runset': density_cfg.get('KLAYOUT_DENSITY_RUNSET'),
           'options': density_cfg.get('KLAYOUT_DENSITY_OPTIONS'),
           'state': str(folder / 'state_out.json'),
           'state_sha256': digest(folder / 'state_out.json'),
           'subject': str(gds), 'subject_sha256': digest(Path(gds)),
           DENSITY_METRIC: count, 'checker_error': checker_error,
           'report': str(report) if report.is_file() else None,
           'rules': density_rules(report) if report.is_file() else None}
    if 'KLayout.Filler' in steps:
        filler = chain_folder(project, lane, steps, 'KLayout.Filler')
        filled = _load(filler / 'state_out.json').get('gds')
        if not filled or not Path(filled).is_file() or Path(filled).resolve() == Path(gds).resolve():
            raise Refusal('LL_FILLER_NO_OUTPUT', f'{filler}: state gds {filled!r}')
        if Path(str(_load(folder / 'state_out.json').get('gds'))).resolve() != Path(filled).resolve():
            raise Refusal('LL_DENSITY_SUBJECT_DRIFT', f'{folder} did not measure {filled}')
        cfg = _load(configs['KLayout.Filler'])
        out['filler'] = {'step': 'KLayout.Filler', 'script': cfg.get('KLAYOUT_FILLER_SCRIPT'),
                         'options': cfg.get('KLAYOUT_FILLER_OPTIONS'),
                         'state': str(filler / 'state_out.json'),
                         'state_sha256': digest(filler / 'state_out.json'),
                         'gds_in': str(gds), 'gds_in_sha256': digest(Path(gds)),
                         'filled_gds': str(filled), 'filled_sha256': digest(Path(filled))}
        out['subject'], out['subject_sha256'] = str(filled), digest(Path(filled))
    return out


def density_rules(report: Path) -> Optional[Dict[str, int]]:
    """{rule: violation count} from the deck's `density.klayout.json`."""
    try:
        doc = json.loads(report.read_text())
    except (OSError, ValueError):
        return None
    if isinstance(doc, dict):
        rows = doc.get('violations', doc)
        if isinstance(rows, dict):
            return {str(k): v for k, v in rows.items() if _count(v) is not None}
        if isinstance(rows, list):
            out: Dict[str, int] = {}
            for row in rows:
                if isinstance(row, dict):
                    name = str(row.get('rule') or row.get('category') or row.get('name') or '?')
                    out[name] = out.get(name, 0) + (_count(row.get('count')) or 1)
            return out
    return None


# --- row occupancy, from the DEF and the LEFs that define its masters -------

_LEF_MACRO = re.compile(r'^\s*MACRO\s+(\S+)(.*?)^\s*END\s+\1\b', re.S | re.M)
_LEF_SITE = re.compile(r'^\s*SITE\s+(\S+)(.*?)^\s*END\s+\1\b', re.S | re.M)
_LEF_SIZE = re.compile(r'^\s*SIZE\s+([\d.]+)\s+BY\s+([\d.]+)\s*;', re.M)
_LEF_CLASS = re.compile(r'^\s*CLASS\s+(\S+)', re.M)
_DEF_UNITS = re.compile(r'^\s*UNITS\s+DISTANCE\s+MICRONS\s+(\d+)\s*;', re.M)
_DEF_ROW = re.compile(r'^\s*ROW\s+\S+\s+(\S+)\s+-?\d+\s+-?\d+\s+\S+'
                      r'(?:\s+DO\s+(\d+)\s+BY\s+(\d+))?', re.M)
_DEF_PLACED = re.compile(r'^\s*-\s+\S+\s+(\S+)[^;]*?\+\s+(?:PLACED|FIXED)\s', re.M | re.S)


def lef_geometry(lef_texts) -> Dict[str, Dict[str, Any]]:
    """{'macros': {name: (w, h, class)}, 'sites': {name: (w, h)}} in microns."""
    macros: Dict[str, Any] = {}
    sites: Dict[str, Any] = {}
    for text in lef_texts:
        for name, body in _LEF_SITE.findall(text):
            size = _LEF_SIZE.search(body)
            if size:
                sites[name] = (float(size.group(1)), float(size.group(2)))
        for name, body in _LEF_MACRO.findall(text):
            size = _LEF_SIZE.search(body)
            cls = _LEF_CLASS.search(body)
            if size:
                # LEF keywords are case-insensitive (the gf180 cell LEF writes
                # `CLASS core`, and OpenROAD reads it as CORE).
                macros[name] = (float(size.group(1)), float(size.group(2)),
                                cls.group(1).upper() if cls else '')
    return {'macros': macros, 'sites': sites}


def row_occupancy(def_text: str, lef_texts) -> Dict[str, Any]:
    """Row area and the CORE-class instance area placed in the DEF, in um^2.

    The same quantity the direct fill emitter's odb block measured: the
    area of every placed instance whose LEF class is CORE (logic, taps and
    fill together) over the rows' site area.  An instance or row site no LEF
    defines leaves the result NOT_MEASURED -- never a guess."""
    geo = lef_geometry(lef_texts)
    rows = _DEF_ROW.findall(def_text)
    row_area = 0.0
    unknown_sites = set()
    for site, nx, ny in rows:
        if site not in geo['sites']:
            unknown_sites.add(site)
            continue
        w, h = geo['sites'][site]
        row_area += int(nx or 1) * int(ny or 1) * w * h
    comps = re.search(r'^\s*COMPONENTS\s+\d+\s*;(.*?)^\s*END\s+COMPONENTS', def_text, re.S | re.M)
    core_area = 0.0
    unknown_masters: Dict[str, int] = {}
    for master in _DEF_PLACED.findall(comps.group(1) if comps else ''):
        row = geo['macros'].get(master)
        if row is None:
            unknown_masters[master] = unknown_masters.get(master, 0) + 1
            continue
        if row[2] == 'CORE':
            core_area += row[0] * row[1]
    measured = bool(rows) and not unknown_sites and not unknown_masters and row_area > 0
    return {'status': 'MEASURED' if measured else 'NOT_MEASURED',
            'row_area_um2': round(row_area, 4), 'core_class_area_um2': round(core_area, 4),
            'row_utilization_pct': round(100.0 * core_area / row_area, 4) if measured else None,
            'unknown_sites': sorted(unknown_sites),
            'unknown_masters': dict(sorted(unknown_masters.items())[:20])}


def _host_lefs(config: Dict[str, Any], pdk_root: Path, pdk: str) -> List[Path]:
    guest = f'/pdk/{pdk}'
    out = []
    for key in ('TECH_LEFS', 'CELL_LEFS', 'PAD_LEFS', 'MACRO_LEFS', 'EXTRA_LEFS'):
        value = config.get(key) or []
        items = list(value.values()) if isinstance(value, dict) else list(value)
        for item in items:
            text = str(item)
            path = Path(str(pdk_root / pdk) + text[len(guest):]) if text.startswith(guest) else Path(text)
            if path not in out:
                out.append(path)
    return out


def run_fill_insertion(project: Path, image: str, pdk_root: Path, pdk: str, *,
                       routed_def: Path, netlist, sdc: Path,
                       lane: str = '34') -> Dict[str, Any]:
    """`OpenROAD.FillInsertion` on the ODB of the route that ships.

    Returns the tool's DEF, the cells it placed (per master, per class), the
    row occupancy before and after, and the supply-ownership verdict of the
    DEF it wrote (F24).  Refuses by name when the step placed nothing while
    the rows still had room (#445): an empty fill must not claim success."""
    import pg_supply_pin_ownership_check as _pgo
    for source in (routed_def, sdc, *(netlist if isinstance(netlist, (list, tuple)) else [netlist])):
        if not Path(source).is_file():
            raise Refusal('LL_ROUTE_VIEW_MISSING', str(source))
    configs = resolve_step_configs(project, image, pdk, [ODB_FILL_STEP],
                                   pdk_root=pdk_root, folder=CONFIG_FOLDER)
    config = _load(configs[ODB_FILL_STEP])
    patterns = cell_patterns(config)
    if not (patterns['decap'] or patterns['fill']):
        raise Refusal('LL_FILL_CELLS_UNDECLARED', f'{pdk}: DECAP_CELLS / FILL_CELLS')
    mounts = _mounts(pdk_root, pdk)
    state = state_from_direct(project, image, configs[ODB_FILL_STEP],
                              {'def': routed_def, 'nl': netlist, 'sdc': sdc},
                              project / 'phase3/librelane' / CONFIG_FOLDER / 'bridge',
                              mounts=mounts)
    folder = run_chain(project, image, [(ODB_FILL_STEP, configs[ODB_FILL_STEP], state)],
                       mounts=mounts, lane=lane, pdk_root=PDK_GUEST_ROOT)[0]
    out_state = _load(folder / 'state_out.json')
    filled = Path(str(out_state.get('def') or ''))
    if not filled.is_file():
        raise Refusal('LL_FILL_NO_DEF', f'{folder}: state def {filled}')
    before_text = Path(routed_def).read_text(errors='replace')
    after_text = filled.read_text(errors='replace')
    census = fill_census(before_text, after_text, patterns)
    lefs = _host_lefs(config, pdk_root, pdk)
    lef_texts = [p.read_text(errors='replace') for p in lefs if p.is_file()]
    occupancy = {'before': row_occupancy(before_text, lef_texts),
                 'after': row_occupancy(after_text, lef_texts)}
    ownership = _pgo.judge(after_text, [t for p, t in zip([p for p in lefs if p.is_file()], lef_texts)
                                        if not p.name.endswith('.tlef')])
    record = {'step': ODB_FILL_STEP, 'state': str(folder / 'state_out.json'),
              'state_sha256': digest(folder / 'state_out.json'),
              'subject': str(routed_def), 'subject_sha256': digest(Path(routed_def)),
              'filled_def': str(filled), 'filled_def_sha256': digest(filled),
              'patterns': patterns, 'census': census, 'occupancy': occupancy,
              'metrics': {k: v for k, v in _metrics(folder).items()
                          if k.startswith('design__instance__count')},
              'supply_ownership': {k: ownership.get(k) for k in
                                   ('verdict', 'code', 'reason', 'instances',
                                    'supply_pins_checked', 'off_supply_pins')},
              'refusals': []}
    after_util = occupancy['after'].get('row_utilization_pct')
    if census['added'] == 0 and not (isinstance(after_util, (int, float)) and after_util >= 95.0):
        record['refusals'].append(
            'LL_FILL_NO_OP: OpenROAD.FillInsertion placed 0 cells and the rows are not '
            f"full (row utilisation {after_util}%)")
    if census['removed'] or census['per_class']['other']:
        record['refusals'].append(
            f"LL_FILL_NOT_FILL_ONLY: removed {census['removed']}, "
            f"added {census['per_class']['other']} non-fill instance(s)")
    if ownership.get('verdict') == 'FAIL':
        record['refusals'].append(f"LL_FILL_SUPPLY_OWNERSHIP: {ownership.get('reason')}")
    return record


def write_direct_schema_reports(project: Path, record: Dict[str, Any],
                                subject: Dict[str, Any], subject_lines: str) -> None:
    """`reports/density.{json,rpt}` and `metal_fill.done` for a filled.def the
    TOOL wrote, in the direct producer's schema, so every consumer of those
    files (`metal_fill_density_check` first) judges the tool's output
    unchanged.  ``subject``/``subject_lines`` are the runner's own
    `_measured_subject` stamp of the routed and filled DEFs."""
    import _path_layout as _pl
    from _atomic_artefact import write_text
    census = record.get('census') or {}
    after = (record.get('occupancy') or {}).get('after') or {}
    placed = int(census.get('added') or 0)
    row_util = after.get('row_utilization_pct')
    reports = project / 'reports'
    reports.mkdir(parents=True, exist_ok=True)
    unit = '%' if row_util is not None else ''
    write_text(reports / 'density.rpt', subject_lines +
               '# Metal-fill / density report — LibreLane OpenROAD.FillInsertion\n'
               f'# filler instances placed: {placed}\n'
               f"# per class: {json.dumps(census.get('per_class'))}\n"
               '# row-area utilization (post-fill): '
               f"{row_util if row_util is not None else 'unresolved'}{unit}\n")
    write_json(reports / 'density.json', {
        'tool': 'librelane:OpenROAD.FillInsertion', 'filler_instances': placed,
        'row_utilization_pct': row_util, 'core_utilization_pct': None,
        'utilization_below_report_precision': False,
        'note': ('row_utilization_pct = CORE-class instance area / row site area, '
                 "from the tool's DEF and the LEFs that define it; per-layer CMP "
                 'density is judged by the PDK density deck (KLayout.Density) at '
                 "step 34's GDS half"),
        'measured_subject': subject})
    write_text(_pl.pnr_dir(project) / 'metal_fill.done',
               'metal_fill_done\n'
               '# LibreLane OpenROAD.FillInsertion (step 34, mig104).\n'
               f'# fillers placed: {placed}\n'
               f"# state: {record.get('state')} sha256:{record.get('state_sha256')}\n")


def cells_placed_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """What F20b reads: which cells step 34 placed, per master and class, and
    the DEF (by sha256) whose COMPONENTS hold their positions."""
    census = record.get('census') or {}
    return {'step': '34', 'producer': record.get('step'),
            'def': record.get('filled_def'), 'def_sha256': record.get('filled_def_sha256'),
            'subject_def_sha256': record.get('subject_sha256'),
            'rule': 'placed = in COMPONENTS of `def`, not in COMPONENTS of the subject DEF',
            'per_class': census.get('per_class'), 'per_master': census.get('per_master'),
            'class_of_master': census.get('class_of_master'),
            'patterns': record.get('patterns')}


# --- the GDS arms: which fill ships ------------------------------------------

def select_gds_fill(project: Path, direct: Optional[Dict[str, Any]],
                    tool: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """`select_arms` over the two measured fills, the same deck on each.

    An arm the deck did not measure is not a candidate.  A tie (both at the
    same count) keeps the direct arm: it is what the flow has shipped, and
    the tool arm has nothing measured to recommend it over the direct one."""
    root = project / 'phase3/tool_arms/34'
    root.mkdir(parents=True, exist_ok=True)
    reports = {}
    scope = {'deck': 'KLayout.Density (PDK density deck)', 'metric': DENSITY_METRIC}
    for name, arm in (('direct', direct), ('librelane', tool)):
        count = _count((arm or {}).get(DENSITY_METRIC))
        report = root / f'{name}.selection.json'
        write_json(report, {'verdict': 'PASS' if count is not None else 'NOT_MEASURED',
                            'scope': scope,
                            'metrics': {DENSITY_METRIC: {'status': 'MEASURED', 'value': count}
                                        if count is not None else {'status': 'NOT_MEASURED'}},
                            'subject_sha256': (arm or {}).get('subject_sha256')})
        reports[name] = report
    selection = select_arms(reports, {DENSITY_METRIC: 'min'}, root / 'selection.json')
    winner = selection.get('selection')
    if winner == 'UNDETERMINED' and selection.get('reason') == 'LL_PARETO_TIE':
        selection['tie_break'] = 'direct: equal density-error counts; the shipped arm stands'
        winner = 'direct'
    selection['winner'] = winner if winner in ('direct', 'librelane') else None
    write_json(root / 'selection.json', selection)
    return selection


def update_record(project: Path, section: str, value: Dict[str, Any]) -> Path:
    """Write one half's evidence (`odb` or `gds`) into the step-34 record.

    The two halves run at different points of the flow (the GDS half inside
    stream-out, the ODB half in canonicalisation), so each replaces only its
    own section; the switch mode is stamped with it."""
    path = project / RECORD_REL
    try:
        doc = _load(path) if path.is_file() else {}
    except (OSError, ValueError, Refusal):
        doc = {}
    doc['step'] = '34'
    doc[section] = value
    write_json(path, doc)
    return path


def tool_density(project: Path) -> Optional[Dict[str, Any]]:
    """Step 35's CMP half: the density count step 34 recorded from the tool.

    None when step 34 did not run on the tool (no record, or a record with no
    measured GDS arm).  A record whose tool state no longer hashes to what it
    names is refused: a cross-reference to evidence that moved is not one."""
    path = project / RECORD_REL
    if not path.is_file():
        return None
    try:
        doc = _load(path)
    except (OSError, ValueError, Refusal):
        return {'status': 'UNREADABLE', 'source': RECORD_REL}
    arms = doc.get('gds') or {}
    shipped = arms.get('shipped')
    arm = arms.get(shipped) if shipped else None
    if not isinstance(arm, dict):
        return step37_density(project) if not arms else None
    state = Path(str(arm.get('state') or ''))
    if not state.is_file() or digest(state) != arm.get('state_sha256'):
        return {'status': 'STALE', 'source': RECORD_REL, 'state': str(state)}
    count = _count(arm.get(DENSITY_METRIC))
    if count is None:
        return {'status': 'NOT_MEASURED', 'source': RECORD_REL}
    return {'status': 'MEASURED', 'errors': count, 'arm': shipped,
            'rules': arm.get('rules'), 'runset': arm.get('runset'),
            'source': RECORD_REL, 'state': str(state), 'state_sha256': arm.get('state_sha256'),
            'subject_sha256': arm.get('subject_sha256')}


STEP37_PROMOTION_REL = 'phase3/librelane/37-promotion.json'


def step37_density(project: Path) -> Optional[Dict[str, Any]]:
    """When step 37 streamed on the tool, its own SealRing -> Filler ->
    Density chain is step 34's GDS half: the density judgement of the stream
    it promoted (`librelane_step37`'s `37-<arm>-density.json`), its state
    re-hashed.  None when step 37 promoted nothing."""
    promo = project / STEP37_PROMOTION_REL
    if not promo.is_file():
        return None
    try:
        arm = _load(promo).get('selection')
        report = project / 'phase3/librelane' / f'37-{arm}-density.json'
        judged = _load(report)
    except (OSError, ValueError, Refusal):
        return {'status': 'UNREADABLE', 'source': STEP37_PROMOTION_REL}
    state = Path(str(judged.get('source') or ''))
    if not state.is_file() or digest(state) != (judged.get('sha256') or {}).get('state_out.json'):
        return {'status': 'STALE', 'source': str(report.relative_to(project)), 'state': str(state)}
    row = (judged.get('metrics') or {}).get(DENSITY_METRIC) or {}
    count = _count(row.get('value')) if row.get('status') in ('MEASURED', 'FAIL') else None
    if count is None:
        return {'status': 'NOT_MEASURED', 'source': str(report.relative_to(project))}
    return {'status': 'MEASURED', 'errors': count, 'arm': f'step37:{arm}', 'rules': None,
            'source': str(report.relative_to(project)), 'state': str(state),
            'state_sha256': digest(state)}


def router_via_counts(metrics: Dict[str, Any]) -> Optional[Dict[str, int]]:
    """The detailed router's own via counts from an OpenROAD `-metrics` JSON
    (`detailedroute__route__vias__*`) or a LibreLane state (`route__vias__*`)."""
    for prefix in ('route__vias', 'detailedroute__route__vias'):
        single = _count(metrics.get(f'{prefix}__singlecut'))
        multi = _count(metrics.get(f'{prefix}__multicut'))
        if single is not None and multi is not None:
            return {'singlecut': single, 'multicut': multi, 'key_prefix': prefix}
    return None


def via_agreement(def_single: int, def_resolved: int,
                  router: Optional[Dict[str, int]]) -> Dict[str, Any]:
    """The DEF recount against the router's own counts.

    The same route must give the same numbers: equal totals and an equal
    single-cut count AGREE, anything else on equal totals DISAGREE.  Unequal
    totals mean the two describe different routes (a post-route repair
    re-routed after the router wrote its metrics): SCOPE_DIFFERS, and neither
    number is used to excuse the other."""
    if not router:
        return {'verdict': 'NOT_MEASURED', 'reason': 'no router via metrics in this run'}
    total = router['singlecut'] + router['multicut']
    out = {'def': {'singlecut': def_single, 'resolved': def_resolved},
           'router': dict(router), 'router_total': total}
    if total != def_resolved:
        out['verdict'] = 'SCOPE_DIFFERS'
        out['reason'] = (f'the router counted {total} vias and the shipped DEF references '
                         f'{def_resolved}: the route changed after the router measured it')
    elif router['singlecut'] == def_single:
        out['verdict'] = 'AGREE'
    else:
        out['verdict'] = 'DISAGREE'
        out['reason'] = (f"router single-cut {router['singlecut']} != DEF recount "
                         f'{def_single} on the same {total} vias')
    return out
