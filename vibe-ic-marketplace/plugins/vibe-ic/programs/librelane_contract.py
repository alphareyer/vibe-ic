#!/usr/bin/env python3
"""Project-local LibreLane step handoff. No Phase-3 step opts in implicitly."""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _atomic_artefact import write_json  # noqa: E402


class Refusal(RuntimeError):
    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(f"{code}: {detail}")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise Refusal('LL_INVALID_JSON_OBJECT', str(path))
    return value


def _set(out: dict, provenance: dict, key: str, value: Any, source: str) -> None:
    if value is None or value in ('NOT_DETERMINED', 'NOT_MEASURED', ''):
        return
    out[key] = value
    provenance[key] = source


#: The L-docs `emit_config` consumes, by EXACT name. A prefix match lets one
#: document stand in for another (D1: `L8_TIMING_WAVEFORM` satisfied an `L8_`
#: slot while `L8_RTL_CONSTANTS` was absent, and the clauses reading it SKIPPED).
EMIT_CONFIG_LDOCS = ('L8_TIMING_WAVEFORM.json', 'L9_INTEGRATION_SPEC.json',
                     'L19_CONSTRAINTS_PDK.json')
DECLARATION_REL = 'input/submission_template/tapeout_declaration.json'
SLOTS_REL = 'input/submission_template/slots'


def _ldoc(root: Path, name: str) -> dict[str, Any]:
    path = root / name
    if not path.is_file():
        raise Refusal('LL_LDOC_MISSING', f'{name} (emit_config reads it by exact name)')
    return _load(path)


def _rect(value: Any, key: str) -> list[float | int]:
    if not (isinstance(value, list) and len(value) == 4 and
            all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)
            and value[0] < value[2] and value[1] < value[3]):
        raise Refusal('LL_DECLARATION_RECT_INVALID', f'{key}: {value!r}')
    return value


def _def_die_area(path: Path) -> list[float] | None:
    """DIEAREA of a DEF template in microns, or None when it states none."""
    text = path.read_text(errors='replace')
    units = re.search(r'^\s*UNITS\s+DISTANCE\s+MICRONS\s+(\d+)\s*;', text, re.M)
    area = re.search(r'^\s*DIEAREA((?:\s*\(\s*-?\d+\s+-?\d+\s*\))+)\s*;', text, re.M)
    if not (units and area):
        return None
    points = [(int(x), int(y)) for x, y in re.findall(r'\(\s*(-?\d+)\s+(-?\d+)\s*\)', area.group(1))]
    scale = int(units.group(1))
    xs, ys = [x for x, _ in points], [y for _, y in points]
    return [min(xs) / scale, min(ys) / scale, max(xs) / scale, max(ys) / scale]


def _slot_def_template(project: Path) -> tuple[str, str] | None:
    """The operator slot's own ``FP_DEF_TEMPLATE``, as (dir:: path, source)."""
    import yaml
    found: dict[str, str] = {}
    slots = project / SLOTS_REL
    for slot in sorted(list(slots.glob('*.yaml')) + list(slots.glob('*.yml'))):
        try:
            mapping = yaml.safe_load(slot.read_text())
        except (OSError, yaml.YAMLError) as exc:
            raise Refusal('LL_SLOT_UNREADABLE', f'{slot}: {exc}') from exc
        value = mapping.get('FP_DEF_TEMPLATE') if isinstance(mapping, dict) else None
        if value in (None, ''):
            continue
        raw = str(value)
        raw = raw[5:] if raw.startswith('dir::') else raw
        template = (slot.parent / raw).resolve()
        if not template.is_file() or not template.is_relative_to(project.resolve()):
            raise Refusal('LL_DEF_TEMPLATE_MISSING', f'{slot.name}: {value}')
        found[str(template)] = (f'{slot.relative_to(project)}.FP_DEF_TEMPLATE '
                                f'(sha256:{digest(template)})')
    if len(found) > 1:
        raise Refusal('LL_DEF_TEMPLATE_AMBIGUOUS', ', '.join(sorted(found)))
    if not found:
        return None
    path, source = next(iter(found.items()))
    return 'dir::' + str(Path(path).relative_to(project.resolve())), source


#: Declaration pad answers -> the LibreLane variables the pad producer writes.
_PAD_ANSWERS = {'pad_site_name': 'PAD_SITE_NAME',
                'pad_corner_site_name': 'PAD_CORNER_SITE_NAME',
                'pad_edge_spacing_um': 'PAD_EDGE_SPACING',
                'pad_fillers': 'PAD_FILLERS'}


def declaration_config(project: Path) -> tuple[dict[str, Any], dict[str, str]]:
    """Step 0.5ic -> LibreLane: the declared die, core and pads as config.

    Every value is read through ``_tapeout_declaration.answer`` -- the one
    reader, which withholds an owner-only answer nobody attested -- never out
    of ``answers`` directly. A HARDMACRO's own rectangle is ``macro_area_um``;
    its ``die_area_um`` is a question it does not owe (vibe-ic#2118). A die
    whose declared ``fp_sizing`` is ``relative`` was DERIVED from a
    utilisation, so its rectangles are not emitted as the truth.
    """
    import _tapeout_declaration as TD
    source = DECLARATION_REL.removesuffix('.json') + '.answers.'
    doc = _load(project / DECLARATION_REL)
    result: dict[str, Any] = {}
    sources: dict[str, str] = {}
    answered = {key: TD.answer(doc, key) for key in (
        'deliverable', 'top_cell', 'die_area_um', 'core_area_um', 'fp_sizing',
        'die_origin_um', 'macro_area_um', 'pad_order_by_side', 'pad_rotations',
        'pad_corner_master', *_PAD_ANSWERS)}
    given = {k: v for k, v in answered.items() if v != TD.NOT_DETERMINED}
    _set(result, sources, 'DESIGN_NAME', given.get('top_cell'), source + 'top_cell')
    sizing = given.get('fp_sizing')
    if sizing is not None and sizing not in ('absolute', 'relative'):
        raise Refusal('LL_DECLARATION_FP_SIZING_INVALID', repr(sizing))
    if given.get('deliverable') == TD.DELIVERABLE_HARDMACRO:
        if 'macro_area_um' in given:
            _set(result, sources, 'DIE_AREA', _rect(given['macro_area_um'], 'macro_area_um'),
                 source + 'macro_area_um (deliverable HARDMACRO)')
    elif sizing != 'relative':
        if 'die_area_um' in given:
            die = _rect(given['die_area_um'], 'die_area_um')
            origin = given.get('die_origin_um')
            if isinstance(origin, list) and len(origin) == 2 and list(die[:2]) != list(origin):
                raise Refusal('LL_DECLARATION_ORIGIN_MISMATCH',
                              f'die_area_um {die} vs die_origin_um {origin}')
            _set(result, sources, 'DIE_AREA', die, source + 'die_area_um')
        if 'core_area_um' in given:
            core = _rect(given['core_area_um'], 'core_area_um')
            die = result.get('DIE_AREA')
            if die and not (core[0] >= die[0] and core[1] >= die[1] and
                            core[2] <= die[2] and core[3] <= die[3]):
                raise Refusal('LL_DECLARATION_CORE_OUTSIDE_DIE', f'{core} vs {die}')
            _set(result, sources, 'CORE_AREA', core, source + 'core_area_um')
    if sizing is not None:
        _set(result, sources, 'FP_SIZING', sizing, source + 'fp_sizing')
    elif 'DIE_AREA' in result:
        _set(result, sources, 'FP_SIZING', 'absolute', sources['DIE_AREA'])
    order = given.get('pad_order_by_side')
    if isinstance(order, dict):
        for side in ('south', 'east', 'north', 'west'):
            if isinstance(order.get(side), list):
                _set(result, sources, 'PAD_' + side.upper(), order[side],
                     f'{source}pad_order_by_side.{side}')
    rotations = given.get('pad_rotations')
    if isinstance(rotations, dict):
        for axis in ('horizontal', 'vertical', 'corner'):
            _set(result, sources, 'PAD_ROTATION_' + axis.upper(), rotations.get(axis),
                 f'{source}pad_rotations.{axis}')
    if isinstance(given.get('pad_corner_master'), str):
        _set(result, sources, 'PAD_CORNER', [given['pad_corner_master']],
             source + 'pad_corner_master')
    for answer_key, key in _PAD_ANSWERS.items():
        _set(result, sources, key, given.get(answer_key), source + answer_key)
    template = _slot_def_template(project)
    if template:
        path, origin = template
        _set(result, sources, 'FP_DEF_TEMPLATE', path, origin)
        die = _def_die_area(project / path[5:])
        if die is not None and 'DIE_AREA' in result and \
                any(abs(a - b) > 1e-6 for a, b in zip(die, result['DIE_AREA'])):
            raise Refusal('LL_DEF_TEMPLATE_DIE_MISMATCH',
                          f'{path} DIEAREA {die} vs declared {result["DIE_AREA"]}')
    return result, sources


def aux_tie_dont_touch(chip_top_record: dict[str, Any]) -> list[str]:
    """The pad-control tie identities the chip-top producer declared (F23).

    Each `aux_pin_signal_connections` row names a tie instance and the net
    that joins it to one pad control pin; post-layout LEC proves exactly that
    instance/pin on exactly that net. LibreLane's RepairDesignPostGPL runs
    `repair_tie_fanout` (DESIGN_REPAIR_TIE_FANOUT), which deletes each tie and
    re-drives its load from a clone on a fresh net. MEASURED on spm x gf180mcuD
    (0.3.79): all 76 declared ties were replaced (`u_pad_clk/PD` on `net`), and
    with these names `set_dont_touch` the same call inserted 0 ties and every
    declared net survived. So they reach RSZ_DONT_TOUCH_LIST, which LibreLane's
    resizer steps apply before repairing.
    """
    rows = chip_top_record.get('aux_pin_signal_connections') or []
    if not isinstance(rows, list):
        raise Refusal('LL_AUX_TIE_RECORD_INVALID',
                      'io_pad_chip_top.json.aux_pin_signal_connections is not a list')
    names: list[str] = []
    for index, row in enumerate(rows):
        values = [row.get(k) for k in ('tie_instance', 'net')] if isinstance(row, dict) else []
        if len(values) != 2 or not all(isinstance(v, str) and v for v in values):
            raise Refusal('LL_AUX_TIE_RECORD_INVALID',
                          f'aux_pin_signal_connections[{index}] names no tie_instance/net')
        names.extend(v for v in values if v not in names)
    return names


def emit_config(project: Path, pdk: str, output: Path) -> dict:
    """Emit only declared inputs; unavailable values stay absent, never guessed."""
    root = project / 'phase1/generated_docs'
    l8, l9, l19 = (_ldoc(root, name) for name in EMIT_CONFIG_LDOCS)
    # The pad producer (step 15.5ic) runs after synthesis and pre-layout STA;
    # before it has run the PAD_* keys are undeclared, so they stay absent.
    pad_path = project / 'phase3/stage3/pnr/pad_assignment.json'
    pads = _load(pad_path) if pad_path.is_file() else {}
    result: dict[str, Any] = {}
    sources: dict[str, str] = {}
    clocks = [c for c in l8.get('clock_domains', []) if c.get('role') == 'primary'
              and c.get('pdk_scoped_target') in (None, pdk)]
    if len(clocks) != 1:
        raise Refusal('LL_CLOCK_AMBIGUOUS', f'{len(clocks)} primary clocks for {pdk}')
    c = clocks[0]
    _set(result, sources, 'CLOCK_PERIOD', c.get('period_ns'), 'L8_TIMING_WAVEFORM.clock_domains[primary].period_ns')
    _set(result, sources, 'CLOCK_PORT', c.get('source_pin'), 'L8_TIMING_WAVEFORM.clock_domains[primary].source_pin')
    rtl = project / 'phase2/stage1/rtl' / (str(l9.get('top_module', '')) + '.v')
    chip_top = project / 'phase3/stage3/pnr/chip_top_io.v'
    if rtl.is_file() and chip_top.is_file():
        _set(result, sources, 'VERILOG_FILES', ['dir::' + str(rtl.relative_to(project)),
                                                'dir::' + str(chip_top.relative_to(project))],
             'L9_INTEGRATION_SPEC.top_module + phase3/stage3/pnr/chip_top_io.v')
    sdc = project / 'phase3/stage3/pnr/constraint.sdc'
    if sdc.is_file():
        for key in ('PNR_SDC_FILE', 'SIGNOFF_SDC_FILE'):
            _set(result, sources, key, 'dir::' + str(sdc.relative_to(project)),
                 'phase3/stage3/pnr/constraint.sdc (L9-derived producer artefact)')
    declared, declared_sources = declaration_config(project)
    for key, value in declared.items():
        _set(result, sources, key, value, declared_sources[key])
    declarations = l19.get('fields', {}).get('constraint_declarations', [])
    supported = {'MAX_FANOUT_CONSTRAINT', 'MAX_TRANSITION_CONSTRAINT',
                 'MAX_CAPACITANCE_CONSTRAINT', 'FP_CORE_UTIL', 'PL_TARGET_DENSITY',
                 'PDN_VOFFSET', 'PDN_HOFFSET', 'PDN_CORE_RING',
                 'PDN_CORE_RING_CONNECT_TO_PADS', 'PDN_SKIPTRIM', 'FP_PDN_SKIPTRIM',
                 'FP_PDN_VOFFSET', 'FP_PDN_HOFFSET'}
    chosen: dict[str, list[tuple[int, Any, str]]] = {}
    for row in declarations:
        key = row.get('token')
        if key not in supported:
            continue
        scope = row.get('scope')
        if scope and not (fnmatch.fnmatch(pdk.lower(), str(scope).lower()) or
                          pdk.lower().startswith(str(scope).lower().rstrip('*_'))):
            continue
        value = str(row.get('value', '')).strip()
        if value.lower() in ('', '工具預設', 'not_determined'):
            continue
        key = {'FP_PDN_SKIPTRIM': 'PDN_SKIPTRIM', 'FP_PDN_VOFFSET': 'PDN_VOFFSET',
               'FP_PDN_HOFFSET': 'PDN_HOFFSET'}.get(key, key)
        try:
            parsed: Any = float(value.rstrip('%'))
            if parsed.is_integer():
                parsed = int(parsed)
        except ValueError:
            if value.lower().startswith('true'):
                parsed = True
            elif value.lower().startswith('false'):
                parsed = False
            else:
                continue
        priority = (2 if scope else 0) + (1 if 'L9_' in str(row.get('source')) else 0)
        chosen.setdefault(key, []).append((priority, parsed, f"L19_CONSTRAINTS_PDK.constraint_declarations:{row.get('source')}:{row.get('line')}"))
    for key, rows in chosen.items():
        priority = max(row[0] for row in rows)
        applicable = [row for row in rows if row[0] == priority]
        if len({str(row[1]) for row in applicable}) != 1:
            raise Refusal('LL_CONSTRAINT_CONFLICT', key)
        _, value, source = applicable[0]
        _set(result, sources, key, value, source)
    # Step 19 (T98): CTS leaf clusters stay within the declared fanout cap.
    # `set_max_fanout` in the SDC does not constrain clock_tree_synthesis
    # (MEASURED on spm x ihp-sg13g2: a 16-sink leaf against a declared 8), so
    # the cap reaches `-sink_clustering_size` directly, from the same
    # declaration; LibreLane's own default leaves the size unset.
    if 'MAX_FANOUT_CONSTRAINT' in result:
        _set(result, sources, 'CTS_SINK_CLUSTERING_SIZE', result['MAX_FANOUT_CONSTRAINT'],
             sources['MAX_FANOUT_CONSTRAINT'] + ' (CTS_SINK_CLUSTERING_SIZE = MAX_FANOUT_CONSTRAINT)')
    # The pad producer (15.5ic) translates the declaration; where both state
    # a value they must agree. The declaration is the input, so it wins the
    # provenance; a disagreement is refused, never resolved by either side.
    produced: dict[str, Any] = {}
    for key in ('PAD_SOUTH', 'PAD_EAST', 'PAD_NORTH', 'PAD_WEST', 'PAD_SITE_NAME',
                'PAD_CORNER_SITE_NAME', 'PAD_FILLERS', 'PAD_ROTATION_HORIZONTAL',
                'PAD_ROTATION_VERTICAL', 'PAD_ROTATION_CORNER', 'PAD_CORNER',
                'PAD_EDGE_SPACING'):
        value = pads.get(key)
        if key == 'PAD_CORNER' and value and not isinstance(value, list):
            value = [value]
        produced[key] = value
    for key in ('PAD_EDGE_SPACING',):
        for owner, value in (('pad_assignment.json', produced.get(key)),
                             ('declaration', result.get(key))):
            if value is None:
                continue
            try:
                spacing = float(value)
            except (TypeError, ValueError) as exc:
                raise Refusal('LL_PAD_SPACING_INVALID', f'{owner}: {value}') from exc
            if not 0 <= spacing < float('inf'):
                raise Refusal('LL_PAD_SPACING_INVALID', f'{owner}: {spacing}')
            if owner == 'pad_assignment.json':
                produced[key] = spacing
            else:
                result[key] = spacing
    for key, value in produced.items():
        if value in (None, '', []):
            continue
        if key in result:
            if result[key] != value:
                raise Refusal('LL_PAD_DECLARATION_CONFLICT',
                              f'{key}: declaration {result[key]!r} vs '
                              f'phase3/stage3/pnr/pad_assignment.json {value!r}')
            continue
        _set(result, sources, key, value, 'phase3/stage3/pnr/pad_assignment.json.' + key)
    # Supply nets: the chip-top producer's power-pad plan names them.  A PDN
    # grid with no net names and no ring cannot reach the supply pads: on the
    # spm chip path the PDK-default GeneratePDN measured 3,391,999
    # power-grid violations (every shape floating) until these were declared.
    plan = {}
    chip_top_record = project / 'reports/phase3/io_pad_chip_top.json'
    if chip_top_record.is_file():
        plan = _load(chip_top_record).get('power_pad_plan') or {}
        if isinstance(plan, str):
            plan = {}
    for key, field in (('VDD_NETS', 'power_net'), ('GND_NETS', 'ground_net')):
        if isinstance(plan.get(field), str) and plan[field]:
            _set(result, sources, key, [plan[field]],
                 f'reports/phase3/io_pad_chip_top.json.power_pad_plan.{field}')
    if chip_top_record.is_file():
        _set(result, sources, 'RSZ_DONT_TOUCH_LIST',
             aux_tie_dont_touch(_load(chip_top_record)) or None,
             'reports/phase3/io_pad_chip_top.json.aux_pin_signal_connections'
             '[].{tie_instance,net}')
    # Core ring + pad connection: the PDK registry's pad-connected ring, the
    # same declaration the direct deck's `add_pdn_ring` is built from.
    registry = Path(__file__).resolve().parent / 'pdk_registry.json'
    entries = _load(registry).get('pdks', []) if registry.is_file() else []
    if isinstance(entries, dict):
        entries = [dict(v, name=k) for k, v in entries.items() if isinstance(v, dict)]
    ring = next((e.get('pdn_ring') for e in entries
                 if isinstance(e, dict) and e.get('name') == pdk), None)
    has_pads = any(k in result for k in ('PAD_SOUTH', 'PAD_EAST', 'PAD_NORTH', 'PAD_WEST'))
    if isinstance(ring, dict) and ring.get('layers') and has_pads:
        _set(result, sources, 'PDN_CORE_RING', True,
             f'programs/pdk_registry.json.pdks[name={pdk}].pdn_ring')
        if ring.get('connect_to_pad_layers'):
            _set(result, sources, 'PDN_CORE_RING_CONNECT_TO_PADS', True,
                 f'programs/pdk_registry.json.pdks[name={pdk}].pdn_ring.connect_to_pad_layers')
    # Strap width/pitch: this design's own pre-route EM search (T103), measured
    # with PSM on its placed, clock-treed layout. Absent record -> absent keys.
    from _ppa.pdn_em_presweep import librelane_pdn_config
    pdn, pdn_source = librelane_pdn_config(project)
    for key, value in pdn.items():
        _set(result, sources, key, value, pdn_source)
    write_json(output, result)
    write_json(output.with_suffix('.provenance.json'), sources)
    return result


def emit_synthesis_config(project: Path, pdk: str, output: Path,
                          rtl_files: list[Path], defines: list[str],
                          use_slang: bool,
                          std_cell_library: str | None = None,
                          synth_liberty: str | None = None,
                          top: str | None = None) -> dict:
    """Bind Yosys.Synthesis to the caller's selected design inputs."""
    import sparse_fsm_detect
    import catalog_synth_safe_params_check

    if not rtl_files or any(not path.is_file() or not path.resolve().is_relative_to(project.resolve())
                            for path in rtl_files):
        raise Refusal('LL_SYNTH_INPUT_MISSING', 'selected RTL must exist inside the project')
    result = emit_config(project, pdk, output)
    sources = _load(output.with_suffix('.provenance.json'))
    _set(result, sources, 'PDK', pdk, 'resolved PDK supplied by phase3_one_shot_runner')
    if std_cell_library:
        _set(result, sources, 'STD_CELL_LIBRARY', std_cell_library,
             'resolved synthesis liberty library supplied by phase3_one_shot_runner')
    if synth_liberty:
        _set(result, sources, 'LIB', {'*': [synth_liberty]},
             'resolved synthesis liberty supplied by phase3_one_shot_runner')
    _set(result, sources, 'VERILOG_FILES',
         [str(path.resolve()) for path in rtl_files],
         'phase3_one_shot_runner.step_synth selected RTL (package-first, include-hub filtered)')
    for key in ('PNR_SDC_FILE', 'SIGNOFF_SDC_FILE'):
        if key in result and str(result[key]).startswith('dir::'):
            result[key] = str((project / str(result[key])[5:]).resolve())
    _set(result, sources, 'VERILOG_DEFINES', defines,
         'phase3_one_shot_runner.step_synth macro-aware frontend decision')
    _set(result, sources, 'USE_SLANG', use_slang,
         'phase3_one_shot_runner.step_synth frontend decision')
    sparse = sparse_fsm_detect.detect_paths(rtl_files)
    _set(result, sources, 'SYNTH_PRESERVE_FSM_REGISTERS', sparse['register_names'],
         'sparse_fsm_detect over selected design RTL')
    _set(result, sources, 'SYNTH_PRESERVE_FSM_INSTANCES', sparse['flop_instances'],
         'sparse_fsm_detect over selected design RTL')
    _set(result, sources, 'SYNTH_FSM_ENCFILE', True,
         'step 13 LEC requires the synthesis FSM recoding table')
    # A catalogued IP that is itself the top: SYNTH_PARAMETERS reaches it
    # (`chparam ... <top>`). Below the top, the glue pins it (step-1 gate).
    pinned = catalog_synth_safe_params_check.top_synth_parameters(project, top) if top else None
    if pinned:
        _set(result, sources, 'SYNTH_PARAMETERS', pinned[0], pinned[1])
    write_json(output, result)
    write_json(output.with_suffix('.provenance.json'), sources)
    return result


def emit_lint_config(project: Path, pdk: str, output: Path, top: str,
                     rtl_files: list[Path], top_source: str) -> dict:
    """Bind Verilator.Lint to the design's own RTL and declared top.

    Step 2 runs before any Phase-3 artefact exists, so this reads nothing
    from phase3/stage3; unlike `emit_config` it needs no pad or die input."""
    if not rtl_files or any(not path.is_file() or not path.resolve().is_relative_to(project.resolve())
                            for path in rtl_files):
        raise Refusal('LL_LINT_INPUT_MISSING', 'selected RTL must exist inside the project')
    if not top:
        raise Refusal('LL_TOP_UNDECLARED', 'no declared RTL top module')
    result: dict[str, Any] = {'meta': {'step': 'Verilator.Lint'}}
    sources: dict[str, str] = {}
    _set(result, sources, 'DESIGN_NAME', top, top_source)
    _set(result, sources, 'PDK', pdk, 'phase3/librelane_switch.json.pdk')
    _set(result, sources, 'VERILOG_FILES', [str(path.resolve()) for path in rtl_files],
         '_rtl_include_hub.silicon_rtl_selection (the step-9 synthesis input)')
    write_json(output, result)
    write_json(output.with_suffix('.provenance.json'), sources)
    return result


def verify_synthesis_stat(stat_path: Path, state: dict, stats_path: Path,
                          top: str, output: Path) -> dict:
    """Bind the area-gate input and copied netlist to Yosys's native stat."""
    output.unlink(missing_ok=True)
    stat = _load(stat_path)
    stats = _load(stats_path)
    module = stat.get('modules', {}).get('\\' + top)
    if module is None:
        module = stat.get('modules', {}).get(top)
    if not isinstance(module, dict):
        raise Refusal('LL_STAT_TOP_MISSING',
                      f'{top}: no modules entry for it in {stat_path}')
    metrics = state.get('metrics', {})
    native_netlist = Path(state.get('nl') or '')
    if not native_netlist.is_file():
        raise Refusal('LL_SYNTH_OUTPUT_MISSING', str(native_netlist))
    count = module.get('num_cells')
    area = module.get('area')
    if not isinstance(count, int) or isinstance(count, bool) or \
            not isinstance(area, (int, float)) or isinstance(area, bool):
        raise Refusal('LL_STAT_UNMEASURED', str(stat_path))
    other_counts = (stats.get('cell_count'), metrics.get('design__instance__count'))
    other_areas = (stats.get('chip_area'), metrics.get('design__instance__area'))
    if any(value != count for value in other_counts) or any(
            not isinstance(value, (int, float)) or isinstance(value, bool) or
            abs(value - area) > max(1e-6, abs(area) * 1e-9)
            for value in other_areas):
        raise Refusal('LL_STAT_MISMATCH', f'{stat_path} vs {stats_path}/state metrics')
    native_hash = digest(native_netlist)
    if stats.get('netlist_sha256') != 'sha256:' + native_hash:
        raise Refusal('LL_STAT_NETLIST_MISMATCH', str(native_netlist))
    report = {'status': 'PASS', 'top': top, 'cell_count': count,
              'area_um2': area, 'stat_sha256': digest(stat_path),
              'native_netlist_sha256': native_hash,
              'area_gate_input_sha256': digest(stats_path)}
    write_json(output, report)
    return report


def _walk_paths(value: Any):
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk_paths(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_paths(item)
    elif isinstance(value, str) and value.startswith('/'):
        yield Path(value)


# Early steps consume only the views produced so far. Floorplan creates
# the first ODB/DEF/SDC from the mapped netlist.
# A lone analog block has only a GDS: these steps declare GDS as their
# sole required input (magic.py RCX/DRC, klayout.py DRC).
# One table for the run_chain floor and the bridge's chain requirements.
_EARLY_STEP_INPUTS: dict[str, tuple[str, ...]] = {
    'Verilator.Lint': (), 'Yosys.JsonHeader': (), 'Yosys.Synthesis': ('json_h',),
    'OpenROAD.CheckSDCFiles': ('nl',), 'OpenROAD.STAPrePNR': ('nl',),
    'OpenROAD.Floorplan': ('nl',), 'Yosys.EQY': ('nl',),
    'Checker.YosysUnmappedCells': ('nl',), 'Checker.YosysSynthChecks': ('nl',),
    'Checker.NetlistAssignStatements': ('nl',),
    'Magic.RCX': ('gds',), 'Magic.DRC': ('gds',),
    'KLayout.DRC': ('gds',),
    # Stream-level checks and finishing read the stream alone (steps 26, 26.5ic).
    'KLayout.Antenna': ('gds',), 'Checker.KLayoutAntenna': (),
    'KLayout.SealRing': ('gds',), 'KLayout.XOR': ('mag_gds', 'klayout_gds')}


def _check_state(state: dict, *, outputs: bool = False,
                 step_id: str = '') -> None:
    required = _EARLY_STEP_INPUTS.get(step_id, ('odb', 'def', 'nl', 'sdc'))
    if not outputs:
        for key in required:
            if not state.get(key):
                raise Refusal('LL_STATE_MISSING', f'state[{key!r}] is empty')
    for path in _walk_paths({k: v for k, v in state.items() if k != 'metrics'}):
        if not path.is_file():
            raise Refusal('LL_STATE_FILE_MISSING', str(path))


def views_path(config_path: Path) -> Path:
    """Sidecar holding the step's LibreLane-declared ``inputs``/``outputs``."""
    return config_path.with_name(config_path.stem + '.views.json')


def _declared_views(config_path: Path) -> tuple[str, list[str], list[str]]:
    step_id = _load(config_path).get('meta', {}).get('step', '')
    sidecar = views_path(config_path)
    declared = _load(sidecar) if sidecar.is_file() else {}
    if declared.get('step') != step_id or not isinstance(declared.get('inputs'), list):
        raise Refusal('LL_STEP_INPUTS_UNDECLARED',
                      f'{step_id}: resolve the config with resolve_step_configs')
    return step_id, list(declared['inputs']), list(declared.get('outputs') or [])


def _required_views(config_paths: list[Path]) -> list[str]:
    """What a chain's first State must carry, from LibreLane's own declarations.

    Walking the chain in order, a view some step consumes (its declared inputs
    plus the run_chain floor) that no EARLIER step declares as an output must
    come from the bridge.  A view needed late in the chain is refused up front
    rather than when that step starts.
    """
    needed: list[str] = []
    produced: set[str] = set()
    for path in config_paths:
        step_id, inputs, outputs = _declared_views(path)
        for view in [*inputs, *_EARLY_STEP_INPUTS.get(step_id, ('odb', 'def', 'nl', 'sdc'))]:
            if view not in produced and view not in needed:
                needed.append(view)
        produced.update(outputs)
    return needed


def _def_design_name(path: Path) -> str | None:
    with path.open(errors='replace') as stream:
        for line in stream:
            words = line.split()
            if len(words) >= 2 and words[0] == 'DESIGN':
                return words[1]
            if words[:1] == ['COMPONENTS']:
                break
    return None


def _openroad_convert(project: Path, image: str, config: dict, tcl_body: list[str],
                      folder: Path, name: str, mounts: list[tuple[Path, str]],
                      docker: str) -> Path:
    """One OpenROAD session in the image, with the step config's own LEFs."""
    tech = config.get('TECH_LEFS') or {}
    tech_lef = tech.get('nom_*') or next(iter(tech.values()), None)
    if not tech_lef:
        raise Refusal('LL_TECH_LEF_MISSING', str(config.get('meta')))
    lefs = list(dict.fromkeys([tech_lef] + [x for key in ('CELL_LEFS', 'PAD_LEFS', 'MACRO_LEFS', 'EXTRA_LEFS')
                                             for x in (config.get(key) or [])]))
    tcl = folder / f'{name}.tcl'
    tcl.write_text('\n'.join([f'read_lef {{{path}}}' for path in lefs] + tcl_body) + '\n')
    volumes = ['-v', f'{project.resolve()}:{project.resolve()}']
    for host, guest in mounts:
        volumes += ['-v', f'{host.resolve()}:{guest}:ro']
    completed = subprocess.run([docker, 'run', '--rm', '--network', 'none', *volumes,
                                image, '--skip', 'openroad', '-exit', str(tcl)],
                               capture_output=True, text=True)
    (folder / f'{name}.log').write_text(completed.stdout + '\n' + completed.stderr)
    if completed.returncode:
        raise Refusal('LL_BRIDGE_CONVERSION_FAILED', str(folder / f'{name}.log'))
    return tcl


def state_from_direct(project: Path, image: str, config_path: Path,
                      views: dict[str, Any], output_dir: Path, *,
                      mounts: list[tuple[Path, str]] | None = None,
                      metrics: dict[str, Any] | None = None,
                      metrics_source: str | None = None,
                      chain: list[Path] | None = None,
                      docker: str = 'docker') -> Path:
    """direct -> LibreLane: a State the resolved step can consume, or a refusal.

    ``views`` names the direct step's real files: ``def``, ``odb``, ``nl`` (one
    path, or the ordered files the direct deck reads before ``link_design``),
    ``sdc``, ``pnl``, ``spef`` (``{corner_pattern: path}``), ``gds`` ...  The
    required set is what LibreLane declares for the step (``meta.inputs`` in a
    config from ``resolve_step_configs``) plus the run_chain floor.  An ODB
    missing beside a DEF is produced by OpenROAD from the step config's own
    LEFs; a DEF missing beside an ODB is written from it.  Any other missing
    view refuses ``LL_BRIDGE_VIEW_MISSING``; nothing is synthesized.  With
    ``chain`` (the later steps' configs, in order) the check covers every view
    a later step consumes that no earlier step produces.  Metrics
    enter only as a measured mapping with a named source.
    """
    config = _load(config_path)
    step_id = config.get('meta', {}).get('step', '')
    required = _required_views([config_path, *(chain or [])])
    output_dir.mkdir(parents=True, exist_ok=True)
    mounts = list(mounts or [])
    state: dict[str, Any] = {}
    receipt: dict[str, Any] = {'step': step_id, 'image': image,
                               'config': str(config_path), 'config_sha256': digest(config_path),
                               'required': required, 'views': {}, 'derived': {}}

    def _file(view: str, value: Any) -> Path:
        path = Path(value)
        if not path.is_file():
            raise Refusal('LL_BRIDGE_VIEW_MISSING', f'{view}: {path}')
        receipt['views'][view] = {'source': str(path.resolve()), 'sha256': digest(path)}
        return path.resolve()

    for view, value in views.items():
        if value is None:
            continue
        if view == 'nl' and isinstance(value, (list, tuple)):
            parts = [Path(x) for x in value]
            for index, part in enumerate(parts):
                _file(f'nl[{index}]', part)
            joined = output_dir / 'bridge.nl.v'
            joined.write_text(''.join(
                f'// vibe-ic bridge: {part.resolve()} sha256:{digest(part)}\n'
                + part.read_text(errors='replace') + '\n' for part in parts))
            receipt['derived']['nl'] = {'from': [str(p.resolve()) for p in parts],
                                        'sha256': digest(joined)}
            state['nl'] = str(joined.resolve())
        elif view == 'spef':
            if not isinstance(value, dict) or not value:
                raise Refusal('LL_BRIDGE_SPEF_CORNERS_UNDECLARED', str(value))
            state['spef'] = {corner: str(_file(f'spef[{corner}]', path))
                             for corner, path in value.items()}
        else:
            state[view] = str(_file(view, value))
    name = config.get('DESIGN_NAME')
    if 'def' in state and name and _def_design_name(Path(state['def'])) != name:
        raise Refusal('LL_BRIDGE_DESIGN_MISMATCH',
                      f"{state['def']}: DESIGN != config DESIGN_NAME {name}")
    if 'odb' in required and 'odb' not in state and 'def' in state:
        odb = output_dir / 'bridge.odb'
        odb.unlink(missing_ok=True)
        tcl = _openroad_convert(project, image, config,
                                [f"read_def {{{state['def']}}}", f'write_db {{{odb}}}'],
                                output_dir, 'def_to_odb', mounts, docker)
        if not odb.is_file():
            raise Refusal('LL_BRIDGE_CONVERSION_FAILED', str(output_dir / 'def_to_odb.log'))
        state['odb'] = str(odb.resolve())
        receipt['derived']['odb'] = {'from': state['def'], 'tcl_sha256': digest(tcl),
                                     'sha256': digest(odb)}
    if 'def' in required and 'def' not in state and 'odb' in state:
        out_def = output_dir / 'bridge.def'
        out_def.unlink(missing_ok=True)
        tcl = _openroad_convert(project, image, config,
                                [f"read_db {{{state['odb']}}}", f'write_def {{{out_def}}}'],
                                output_dir, 'odb_to_def', mounts, docker)
        if not out_def.is_file():
            raise Refusal('LL_BRIDGE_CONVERSION_FAILED', str(output_dir / 'odb_to_def.log'))
        state['def'] = str(out_def.resolve())
        receipt['derived']['def'] = {'from': state['odb'], 'tcl_sha256': digest(tcl),
                                     'sha256': digest(out_def)}
    missing = [view for view in required if view not in state]
    if missing:
        raise Refusal('LL_BRIDGE_VIEW_MISSING', f'{step_id}: {missing}')
    state['metrics'] = dict(metrics or {})
    if metrics:
        if not metrics_source:
            raise Refusal('LL_BRIDGE_METRICS_UNSOURCED', step_id)
        receipt['metrics_source'] = metrics_source
    path = output_dir / 'state_in.json'
    write_json(path, state)
    receipt['state_sha256'] = digest(path)
    write_json(output_dir / 'bridge_receipt.json', receipt)
    return path


def handoff_to_direct(state_path: Path, targets: dict[str, Path], receipt: Path,
                      *, path_map: dict[str, str] | None = None) -> dict:
    """LibreLane -> direct: put named state views where the direct step reads them.

    ``targets`` maps a state view (``def``, ``odb``, ``nl``, ``spef:<corner>``)
    to the file path the direct consumer expects.  ``path_map`` rewrites a
    container prefix to its host path for states written by a whole-flow run.
    Every handed file is bound by sha256 on both sides.
    """
    state = _load(state_path)
    rows: dict[str, Any] = {}
    for view, dest in targets.items():
        key, _, corner = view.partition(':')
        value = state.get(key)
        if corner:
            value = value.get(corner) if isinstance(value, dict) else None
        if not isinstance(value, str) or not value:
            raise Refusal('LL_HANDOFF_VIEW_MISSING', view)
        for guest, host in (path_map or {}).items():
            if value.startswith(guest.rstrip('/') + '/'):
                value = host.rstrip('/') + value[len(guest.rstrip('/')):]
                break
        source = Path(value)
        if not source.is_file():
            raise Refusal('LL_HANDOFF_VIEW_MISSING', f'{view}: {source}')
        dest = Path(dest)
        replaced = digest(dest) if dest.is_file() else None
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + '.handoff.tmp')
        shutil.copyfile(source, tmp)
        os.replace(tmp, dest)
        rows[view] = {'source': str(source), 'source_sha256': digest(source),
                      'dest': str(dest), 'dest_sha256': digest(dest),
                      'replaced_sha256': replaced}
        if rows[view]['source_sha256'] != rows[view]['dest_sha256']:
            raise Refusal('LL_HANDOFF_COPY_MISMATCH', view)
    document = {'state': str(state_path), 'state_sha256': digest(state_path),
                'views': rows}
    write_json(receipt, document)
    return document


# --- the direct routing deck's side of a LibreLane floorplan handoff ---

def def_supply_tcl(def_text: str, marker: str) -> str:
    """Create the supply nets and supply BTerms a tool DEF declares.

    `read_def -floorplan_initialize` lays a DEF onto the LINKED design and
    skips every net and pin the design does not own (ODB-0247/0249).  A
    synthesized netlist owns no supply net, so a LibreLane state read that way
    loses its whole PDN.  MEASURED on the clean spm GeneratePDN DEF (0.3.77):
    30,462 special-wire shapes and 38 BTerms from `read_db`; 0 and 36 from a
    bare floorplan read; 30,462 and 38 once the two supply nets and pins are
    created from the DEF's own SPECIALNETS/PINS first, with COMPONENTS,
    SPECIALNETS and VIAS byte-identical to the ODB's and identical net
    connectivity.  Only nets the DEF itself marks ``USE POWER``/``USE GROUND``
    are created.
    """
    def _section(name: str) -> str:
        m = re.search(rf"(?ms)^{name}\s+\d+\s*;(.*?)^END\s+{name}\b", def_text)
        return m.group(1) if m else ""

    supplies: dict[str, str] = {}
    wildcard: dict[str, list[str]] = {}
    for stmt in _section("SPECIALNETS").split(";"):
        m = re.match(r"\s*-\s+(\S+)", stmt)
        use = re.search(r"\+\s*USE\s+(POWER|GROUND)\b", stmt)
        if m and use:
            supplies[m.group(1)] = use.group(1)
            head = stmt.split("+", 1)[0]
            wildcard[m.group(1)] = re.findall(r"\(\s*\*\s+(\S+)\s*\)", head)
    pins: list[tuple[str, str, str]] = []
    for stmt in _section("PINS").split(";"):
        m = re.match(r"\s*-\s+(\S+)\s+\+\s*NET\s+(\S+)", stmt)
        if m and m.group(2) in supplies:
            d = re.search(r"\+\s*DIRECTION\s+(\S+)", stmt)
            pins.append((m.group(1), m.group(2), d.group(1) if d else "INOUT"))
    lines = [f'puts "{marker} librelane_supply_nets"',
             "set _ll_blk [[[ord::get_db] getChip] getBlock]"]
    for net, use in sorted(supplies.items()):
        lines.append(f'if {{[$_ll_blk findNet "{net}"] eq "NULL"}} {{ set _ll_n '
                     f'[odb::dbNet_create $_ll_blk "{net}"]; $_ll_n setSpecial; '
                     f'$_ll_n setSigType {use} }}')
    for pin, net, direction in pins:
        lines.append(f'if {{[$_ll_blk findBTerm "{pin}"] eq "NULL"}} {{ set _ll_b '
                     f'[odb::dbBTerm_create [$_ll_blk findNet "{net}"] "{pin}"]; '
                     f'$_ll_b setIoType {direction} }}')
    # The DEF's own `( * <pin> )` terms are the tool's global-connect rules
    # (LibreLane SetPowerConnections / GeneratePDN).  Registering them makes
    # the deck's later `global_connect` re-apply reach every instance created
    # after the ingest (spares, buffers, fill, diodes).  MEASURED on the spm
    # mixed chain without them: PG_RECONNECT_DELTA on_no_net 21888 -> 21888.
    rules = 0
    for net, use in sorted(supplies.items()):
        for pin in wildcard.get(net, []):
            lines.append(f'add_global_connection -net {{{net}}} -pin_pattern '
                         f'{{^{re.escape(pin)}$}} -{use.lower()}')
            rules += 1
    if rules:
        lines.append("global_connect")
    lines.append(f'puts "LIBRELANE_SUPPLY_NETS: {len(supplies)} nets, '
                 f'{len(pins)} pins, {rules} global-connect rules"')
    return "\n".join(lines)


def elide_tap_pdn_region(full_pnr_tcl: str, marker: str) -> str:
    """Remove the direct deck's own tap/PDN construction (15 → LibreLane).

    Everything after the floorplan checkpoint and before placement is step-15
    work (macro placement, tapcells, supply global-connect, PDN).  When that
    step's producer is LibreLane, its state already carries all of it.
    """
    m = re.search(
        rf'(?ms)^write_def\s+\S+/floorplan\.def\s*$\n(.*?)'
        rf'^(?=puts "{re.escape(marker)} placement"\s*$)',
        full_pnr_tcl)
    if m is None or len(re.findall(
            rf'(?m)^puts "{re.escape(marker)} placement"\s*$',
            full_pnr_tcl)) != 1:
        raise ValueError("LL_FLOORPLAN_SEAM_AMBIGUOUS: expected one floorplan "
                         "checkpoint followed by one placement stage")
    return (full_pnr_tcl[:m.start(1)]
            + "# step 15 (taps, supply connect, PDN): LibreLane state, "
              "librelane_contract.handoff_to_direct\n"
            + full_pnr_tcl[m.end(1):])


def placement_consumer_tcl(full_pnr_tcl: str, placed_def_c: str, marker: str,
                           after_load_tcl: str = '') -> str:
    """The direct deck from CTS onward, on LibreLane's placed design (step 17).

    With steps 15..18 produced by LibreLane (Floorplan..DetailedPlacement and
    `Vibeic.InsertSpareCells`), the direct deck consumes the tool's final
    placed DEF instead of rebuilding the design:

    1. the netlist load (the `read_verilog`..`link_design` block) becomes one
       full `read_def` of the handed-over DEF, which creates the block with
       every instance, net, pin, row, track and special net. The deck's
       `read_lef` lines stay, so CTS and routing use the SAME tech LEF as the
       direct flow (including its via-landing remediation), and so a resume
       or SDR child deck derived from this one still restores a checkpoint
       DEF on top of them. (MEASURED: `read_db` after `read_lef` replaces the
       whole database, tech included.)
    2. everything the deck does between the resume-elide sentinel and
       `puts "<marker> cts"` (floorplan/pad-ring ingest, taps, PDN, global
       placement, the legalization ladder, the #684 tap prune, spare
       insertion, pre-CTS repair) is removed: it is already in that DEF.
       ``after_load_tcl`` goes in its place: the session state a DEF does not
       carry (global-connect rules, dont_touch).

    The sentinel itself stays, so resume and SDR child decks still find the
    region they elide. Anything else refuses `LL_PLACEMENT_SEAM_AMBIGUOUS`.
    """
    lines = full_pnr_tcl.splitlines()
    starts = [i for i, ln in enumerate(lines) if ln.startswith('read_verilog ')]
    if not starts:
        raise ValueError('LL_PLACEMENT_SEAM_AMBIGUOUS: no read_verilog design load')
    i_rv = starts[0]
    i_ld = i_rv + 1
    while i_ld < len(lines) and (lines[i_ld].startswith('read_verilog ')
                                 or not lines[i_ld].strip()
                                 or lines[i_ld].lstrip().startswith('#')):
        i_ld += 1
    if i_ld >= len(lines) or not lines[i_ld].startswith('link_design '):
        raise ValueError('LL_PLACEMENT_SEAM_AMBIGUOUS: read_verilog is not '
                         'followed by link_design')
    lines[i_rv:i_ld + 1] = [
        '# steps 15..18: the design is LibreLane\'s placed DEF '
        '(librelane_contract.placement_consumer_tcl)',
        f'read_def {placed_def_c}']
    text = '\n'.join(lines) + '\n'
    begin = re.findall(r'(?m)^# <<<PNR_RESUME_ELIDE_BEGIN>>>\s*$', text)
    cts = re.findall(rf'(?m)^puts "{re.escape(marker)} cts"\s*$', text)
    if len(begin) != 1 or len(cts) != 1:
        raise ValueError('LL_PLACEMENT_SEAM_AMBIGUOUS: expected one resume '
                         f'sentinel and one CTS stage, found {len(begin)}/{len(cts)}')
    m = re.search(r'(?ms)^# <<<PNR_RESUME_ELIDE_BEGIN>>>\s*$\n(?:#[^\n]*\n)*'
                  rf'(.*?)^(?=puts "{re.escape(marker)} cts"\s*$)', text)
    if m is None:
        raise ValueError('LL_PLACEMENT_SEAM_AMBIGUOUS: CTS precedes the sentinel')
    ingest = (f'puts "{marker} librelane_placement_ingest"\n'
              f'puts "LIBRELANE_PLACEMENT_CONSUMED: {placed_def_c}"\n'
              + (after_load_tcl.rstrip('\n') + '\n' if after_load_tcl.strip() else ''))
    return text[:m.start(1)] + ingest + text[m.end(1):]


#: Step 17's PPA levers on the LibreLane arm: LibreLane config variables the
#: GlobalPlacement / RepairDesignPostGPL / DetailedPlacement steps read. A
#: value enters the config only as a declared project input --
#: `phase3/librelane_switch.json` `placement_levers` {KEY: value}, the file a
#: PPA candidate writes -- with that source in the provenance file.
PLACEMENT_LEVERS: dict[str, tuple[str, float | None, float | None]] = {
    'PL_TARGET_DENSITY_PCT': ('number', 0.0, 100.0),
    'PL_TIMING_DRIVEN': ('bool', None, None),
    'PL_ROUTABILITY_DRIVEN': ('bool', None, None),
    'GPL_CELL_PADDING': ('int', 0, None),
    'DPL_CELL_PADDING': ('int', 0, None),
    'PL_WIRE_LENGTH_COEF': ('number', 0.0, None),
    'PL_MAX_DISPLACEMENT_X': ('int', 0, None),
    'PL_MAX_DISPLACEMENT_Y': ('int', 0, None),
}
PLACEMENT_LEVERS_KEY = 'placement_levers'

#: A lever that replaces a deprecated design key LibreLane would otherwise
#: translate (`PL_TARGET_DENSITY` fraction -> `_PCT`); both set is a conflict.
_LEVER_SUPERSEDES = {'PL_TARGET_DENSITY_PCT': ('PL_TARGET_DENSITY',)}


def _lever_value(key: str, raw: Any) -> Any:
    kind, low, high = PLACEMENT_LEVERS[key]
    if kind == 'bool':
        if isinstance(raw, bool):
            return raw
        if str(raw).strip().lower() in ('true', 'false'):
            return str(raw).strip().lower() == 'true'
        raise ValueError(f'{raw!r} is not a boolean')
    if isinstance(raw, bool):
        raise ValueError(f'{raw!r} is not a number')
    value: Any = int(str(raw).strip()) if kind == 'int' else float(str(raw).strip())
    if value != value or value in (float('inf'), float('-inf')):
        raise ValueError(f'{raw!r} is not finite')
    if (low is not None and (value < low or (kind == 'number' and value == low))) \
            or (high is not None and value > high):
        raise ValueError(f'{raw!r} is outside the lever range')
    return value


def placement_levers(project: Path) -> dict[str, tuple[Any, str]]:
    """The project's declared placement levers -> a `resolve_step_configs`
    overlay. An unknown key refuses `LL_PLACEMENT_LEVER_UNKNOWN`; a value of
    the wrong type or outside the lever's range `LL_PLACEMENT_LEVER_INVALID`.
    Nothing is defaulted: an absent lever leaves the design declaration or
    LibreLane's own default in force."""
    path = project / 'phase3/librelane_switch.json'
    declared = _load(path).get(PLACEMENT_LEVERS_KEY) if path.is_file() else None
    if declared is None:
        return {}
    if not isinstance(declared, dict):
        raise Refusal('LL_PLACEMENT_LEVER_INVALID', f'{PLACEMENT_LEVERS_KEY} is not an object')
    overlay: dict[str, tuple[Any, str]] = {}
    for key, raw in declared.items():
        if key not in PLACEMENT_LEVERS:
            raise Refusal('LL_PLACEMENT_LEVER_UNKNOWN',
                          f'{key}: not one of {sorted(PLACEMENT_LEVERS)}')
        try:
            value = _lever_value(key, raw)
        except ValueError as exc:
            raise Refusal('LL_PLACEMENT_LEVER_INVALID', f'{key}: {exc}') from exc
        overlay[key] = (value, f'phase3/librelane_switch.json {PLACEMENT_LEVERS_KEY}.{key}')
    return overlay


#: The host cache a resolved PDK root is materialised into, one directory per
#: IMAGE ID. `VIBEIC_PDK_ROOT_CACHE` names it; else the XDG cache convention.
#: This is where the COPY lives, never where a PDK is guessed to be: the content
#: always comes out of the resolved image.
PDK_ROOT_CACHE_ENV = 'VIBEIC_PDK_ROOT_CACHE'
PDK_ROOT_MARKER = '.vibeic_pdk_root.json'
PDK_ROOT_PROVENANCE_REL = 'phase3/librelane_pdk_root.provenance.json'
_PDK_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._+-]*$')


def image_pdk_root(image: str, docker: str = 'docker') -> dict[str, str]:
    """The image's OWN PDK location: its `PDK_ROOT` env, and its image ID.

    Read with `docker image inspect` (never a pull, never a run). The digest is
    the identity and the repository is configuration (#2170): a `repo@digest`
    this host holds under another repository name is inspected by that name.
    An image not on this host, or one declaring no absolute `PDK_ROOT`, refuses.
    """
    def _inspect(ref: str) -> subprocess.CompletedProcess:
        try:
            return subprocess.run([docker, 'image', 'inspect', '--format',
                                   '{{json .Id}} {{json .Config.Env}}', ref],
                                  capture_output=True, text=True)
        except OSError as exc:
            raise Refusal('LL_IMAGE_NOT_INSPECTABLE', f'{image}: {exc}') from None
    result = _inspect(image)
    if result.returncode:
        import _eda_pin
        digest = _eda_pin.reference_digest(image)
        held = _eda_pin.local_references_for_digest(digest)[0] if digest else ()
        if held:
            result = _inspect(held[0])
    if result.returncode:
        raise Refusal('LL_IMAGE_NOT_INSPECTABLE',
                      f'{image}: rc={result.returncode} {result.stderr.strip()[:200]}')
    try:
        image_id, env = (json.loads(part) for part in result.stdout.strip().split(' ', 1))
    except ValueError:
        raise Refusal('LL_IMAGE_NOT_INSPECTABLE', f'{image}: {result.stdout[:200]!r}') from None
    values = [e.split('=', 1)[1] for e in (env or []) if e.startswith('PDK_ROOT=')]
    root = values[-1] if values else ''
    if not (isinstance(image_id, str) and re.fullmatch(r'sha256:[0-9a-f]{64}', image_id)):
        raise Refusal('LL_IMAGE_NOT_INSPECTABLE', f'{image}: image id {image_id!r}')
    if not root.startswith('/'):
        raise Refusal('LL_IMAGE_PDK_ROOT_UNDECLARED', f'{image}: PDK_ROOT={root!r}')
    return {'image_id': image_id, 'pdk_root': root.rstrip('/') or '/'}


def _pdk_root_cache() -> Path:
    declared = os.environ.get(PDK_ROOT_CACHE_ENV)
    if declared:
        return Path(declared)
    xdg = os.environ.get('XDG_CACHE_HOME')
    return (Path(xdg) if xdg else Path.home() / '.cache') / 'vibeic' / 'pdk_root'


def _materialise_image_pdk(image: str, found: dict[str, str], pdk: str,
                           docker: str) -> tuple[Path, str]:
    """Copy `<image PDK_ROOT>/<pdk>` out of the image, once per image ID.

    The copy lands in a scratch name and is renamed into place, and its marker
    is written last, so a reader sees a finished tree or none. Returns the host
    root (which holds `<pdk>/`) and whether it was `copied` or `reused`.
    """
    root = _pdk_root_cache() / found['image_id'].split(':', 1)[1]
    marker = root / f'{pdk}{PDK_ROOT_MARKER}'
    guest = f"{found['pdk_root']}/{pdk}"
    want = {'image_id': found['image_id'], 'pdk': pdk, 'guest_path': guest}
    try:
        recorded = json.loads(marker.read_text())
    except (OSError, ValueError):
        recorded = None
    if isinstance(recorded, dict) and all(recorded.get(k) == v for k, v in want.items()) \
            and (root / pdk).is_dir():
        return root, 'reused'
    import _docker_memory as _dmem
    try:
        root.mkdir(parents=True, exist_ok=True)
        scratch = Path(tempfile.mkdtemp(prefix=f'.{pdk}.partial-', dir=root))
    except OSError as exc:
        raise Refusal('LL_PDK_ROOT_CACHE_UNWRITABLE', f'{root}: {exc}') from None
    created = subprocess.run([docker, 'create', *_dmem.docker_memory_flags(),
                              '--network', 'none', '--entrypoint', 'true',
                              found['image_id']], capture_output=True, text=True)
    try:
        if created.returncode:
            raise Refusal('LL_IMAGE_PDK_NOT_EXTRACTABLE',
                          f'{image}: docker create rc={created.returncode} {created.stderr.strip()[:200]}')
        copied = subprocess.run([docker, 'cp', '-L', f'{created.stdout.strip()}:{guest}',
                                 str(scratch / pdk)], capture_output=True, text=True)
        if copied.returncode or not (scratch / pdk).is_dir():
            raise Refusal('LL_IMAGE_PDK_ABSENT',
                          f'{image}: {guest} not a directory in the image '
                          f'(rc={copied.returncode} {copied.stderr.strip()[:200]})')
        try:
            (scratch / pdk).rename(root / pdk)
        except OSError:
            if not (root / pdk).is_dir():   # another resolver won the rename; keep theirs
                raise
        write_json(marker, {**want, 'image': image, 'host_path': str(root / pdk)})
    finally:
        if not created.returncode:
            subprocess.run([docker, 'rm', '-f', created.stdout.strip()], capture_output=True)
        shutil.rmtree(scratch, ignore_errors=True)
    return root, 'copied'


#: Pruning keeps the current image's copy and this many previous ones; a copy
#: is removed only when its image is gone from this host and nothing uses it.
PDK_ROOT_KEEP_PREVIOUS = 1
PDK_ROOT_PRUNE_LOG = 'pdk_root_prune.log.jsonl'


def _docker_lines(docker: str, *argv: str) -> list[str] | None:
    """stdout lines of a docker query, or None when docker could not answer."""
    try:
        done = subprocess.run([docker, *argv], capture_output=True, text=True)
    except OSError:
        return None
    return None if done.returncode else [l.strip() for l in done.stdout.splitlines() if l.strip()]


def prune_pdk_root_cache(current_image_id: str, docker: str = 'docker') -> dict[str, Any]:
    """Remove cached PDK-root copies whose image is no longer on this host.

    Kept: the current image's copy, the `PDK_ROOT_KEEP_PREVIOUS` most recently
    made other copies, any copy whose image this host still holds, any copy a
    running container binds, and any copy still being made. When docker cannot
    list the host's images or containers nothing is removed (a copy that could
    not be looked at is never presumed unused). Every decision is appended to
    `<cache>/pdk_root_prune.log.jsonl` and returned.
    """
    cache = _pdk_root_cache()
    current = current_image_id.split(':', 1)[-1]
    record: dict[str, Any] = {'cache': str(cache), 'current': current_image_id,
                              'kept': [], 'removed': [], 'refused': None}
    try:
        others = [d for d in cache.iterdir() if d.is_dir() and d.name != current
                  and re.fullmatch(r'[0-9a-f]{64}', d.name)]
    except OSError:
        return record
    if not others:
        return record

    def made(d: Path) -> float:
        return max((m.stat().st_mtime for m in d.glob(f'*{PDK_ROOT_MARKER}')),
                   default=d.stat().st_mtime)
    others.sort(key=made, reverse=True)
    record['kept'] = [{'image_id': f'sha256:{d.name}', 'why': 'previous'}
                      for d in others[:PDK_ROOT_KEEP_PREVIOUS]]
    candidates = others[PDK_ROOT_KEEP_PREVIOUS:]
    held = sources = None
    if candidates:
        held = _docker_lines(docker, 'image', 'ls', '--no-trunc', '--format', '{{.ID}}')
        running = _docker_lines(docker, 'ps', '-q', '--no-trunc')
        sources = [] if running == [] else (_docker_lines(
            docker, 'inspect', '--format', '{{range .Mounts}}{{.Source}}\n{{end}}', *running)
            if running is not None else None)
    if candidates and (held is None or sources is None):
        record['refused'] = ('docker could not list the host images or the running '
                             "containers' mounts; nothing removed")
    elif candidates:
        used = {Path(src).relative_to(base).parts[0] for src in sources
                for base in {cache, cache.resolve()}
                if Path(src).is_relative_to(base) and Path(src) != base}
        for d in candidates:
            why = ('image still on this host' if f'sha256:{d.name}' in held else
                   'bound by a running container' if d.name in used else
                   'copy in progress' if any(d.glob('.*.partial-*')) else None)
            if why:
                record['kept'].append({'image_id': f'sha256:{d.name}', 'why': why})
                continue
            try:
                shutil.rmtree(d)
                record['removed'].append({'image_id': f'sha256:{d.name}', 'path': str(d)})
            except OSError as exc:
                record['kept'].append({'image_id': f'sha256:{d.name}', 'why': f'remove failed: {exc}'})
    try:
        with (cache / PDK_ROOT_PRUNE_LOG).open('a') as log:
            log.write(json.dumps(record, sort_keys=True) + '\n')
    except OSError:
        pass
    return record


def pdk_root_resolution(project: Path | None = None, pdk: str | None = None, *,
                        image: str | None = None, docker: str = 'docker') -> dict[str, Any]:
    """Declared > resolved at run time > refused by name, like `resolve_image`.

    1. declared: switch ``pdk_root_host``, else ``VIBEIC_LIBRELANE_PDK_ROOT``.
    2. resolved: the RESOLVED image's own ``PDK_ROOT`` env, joined with the
       design's PDK (the caller's resolved PDK, else the switch's ``pdk``) and
       copied once per image ID to a host directory every consumer can bind.
    3. neither: `LL_PDK_ROOT_NOT_RESOLVABLE`, naming why resolution failed.
    The answer says where the root came from; with a project it is also
    recorded in `phase3/librelane_pdk_root.provenance.json`.
    """
    path = project / 'phase3/librelane_switch.json' if project else None
    switch = _load(path) if path and path.is_file() else {}
    if switch.get('pdk_root_host'):
        answer = {'path': str(switch['pdk_root_host']), 'source': 'declared',
                  'declared_by': 'phase3/librelane_switch.json pdk_root_host'}
    elif os.environ.get('VIBEIC_LIBRELANE_PDK_ROOT'):
        answer = {'path': os.environ['VIBEIC_LIBRELANE_PDK_ROOT'], 'source': 'declared',
                  'declared_by': 'env VIBEIC_LIBRELANE_PDK_ROOT'}
    else:
        pdk_source = 'caller (the design\'s resolved PDK)' if pdk else \
            'phase3/librelane_switch.json pdk'
        pdk = pdk or switch.get('pdk')
        try:
            if not pdk:
                raise Refusal('LL_PDK_UNDECLARED', 'no design PDK (caller or switch pdk)')
            if not _PDK_NAME.match(str(pdk)):
                raise Refusal('LL_PDK_NAME_INVALID', repr(pdk))
            image = image or resolve_image(project)
            found = image_pdk_root(image, docker)
            root, how = _materialise_image_pdk(image, found, str(pdk), docker)
            # A new copy is when an older image's copy may have become stale.
            pruned = prune_pdk_root_cache(found['image_id'], docker) if how == 'copied' else None
        except Refusal as exc:
            raise Refusal('LL_PDK_ROOT_NOT_RESOLVABLE',
                          'not declared (switch pdk_root_host / VIBEIC_LIBRELANE_PDK_ROOT) '
                          f'and not resolved from the image: {exc}') from None
        answer = {'path': str(root), 'source': 'resolved',
                  'derivation': {'image': image, 'image_id': found['image_id'],
                                 'image_pdk_root': found['pdk_root'],
                                 'image_pdk_root_from': 'docker image inspect Config.Env PDK_ROOT',
                                 'pdk': str(pdk), 'pdk_from': pdk_source,
                                 'guest_path': f"{found['pdk_root']}/{pdk}",
                                 'host_path': str(root / str(pdk)),
                                 'cache': how, 'cache_prune': pruned}}
    if project is not None and (project / 'phase3').is_dir():
        write_json(project / PDK_ROOT_PROVENANCE_REL, answer)
    return answer


def resolve_pdk_root(project: Path | None = None, pdk: str | None = None, *,
                     image: str | None = None, docker: str = 'docker') -> str | None:
    """The host PDK root per `pdk_root_resolution`, or None when it refuses."""
    try:
        return pdk_root_resolution(project, pdk, image=image, docker=docker)['path']
    except Refusal:
        return None


def resolve_image(project: Path | None = None) -> str:
    """Switch ``image`` > ``VIBEIC_LIBRELANE_IMAGE`` > the host's resolved image.

    The fallback is the plugin's ONE runtime resolver, `_eda_pin.image_reference`
    (the newest released vibeic-eda image on this host, by its own version
    label, as a digest).  The source never stores a digest or version: the EDA
    image and the plugin release separately (owner 2026-08-21, 2026-09-17).
    When nothing resolves this refuses by name; it never guesses.
    """
    path = project / 'phase3/librelane_switch.json' if project else None
    declared = _load(path).get('image') if path and path.is_file() else None
    if declared or os.environ.get('VIBEIC_LIBRELANE_IMAGE'):
        return str(declared or os.environ['VIBEIC_LIBRELANE_IMAGE'])
    import _eda_pin
    try:
        return _eda_pin.image_reference()
    except _eda_pin.ImageNotResolvable as exc:
        raise Refusal('LL_IMAGE_NOT_RESOLVABLE', str(exc)) from None


# LibreLane's OpenROAD scripts name some commands by an ABBREVIATION of the
# real command (`est::check_corner_wire_cap` for `..._caps`, `utl::metric_int`
# for `utl::metric_integer`).  Older OpenROAD builds expanded a unique prefix;
# in 26Q3-2943 the unknown-command handler is OpenSTA's, which looks the
# prefix up relative to `::sta`, so a qualified abbreviation in another
# namespace no longer resolves and OpenROAD.STAMidPNR dies (MEASURED on
# 0.3.77: `invalid command name "est::check_corner_wire_cap"`).  The probe
# below derives, from the image's OWN script tree and OWN OpenROAD binaries,
# every referenced command that is absent but has exactly one expansion; the
# contract defines exactly those aliases in an OpenROAD init file.  A prefix
# with several expansions is a real API skew and refuses.
_TCL_PROBE = r'''set -e
root=$(python3 -c 'import librelane,os;print(os.path.join(os.path.dirname(librelane.__file__),"scripts","openroad"))')
own=$(grep -rhoE 'namespace +eval +(::)?[A-Za-z_][A-Za-z0-9_]*' "$root" --include='*.tcl' | awk '{print $3}' | sed 's/^:://' | sort -u)
grep -rhoE '\b[a-z]+::[A-Za-z_][A-Za-z0-9_]*' "$root" --include='*.tcl' | sort -u \
  | grep -vE "^($(echo $own | tr ' ' '|')|__none__)::" > /tmp/vibeic_cmds.txt
cat > /tmp/vibeic_probe.tcl <<'EOF'
set f [open /tmp/vibeic_cmds.txt]
foreach c [split [read $f] "\n"] {
  if {$c eq "" || [llength [info commands ::$c]] || [llength [info procs ::$c]]} { continue }
  puts "VIBEIC_TCL_MISSING $c [lsort [info commands ::${c}*]]"
}
EOF
for bin in /foss/tools/openroad/bin/openroad /foss/tools/openroad/bin/openroad-python; do
  [ -x "$bin" ] && LD_LIBRARY_PATH="/opt/or-tools/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    "$bin" -no_init -no_splash -exit /tmp/vibeic_probe.tcl 2>&1 | grep '^VIBEIC_TCL_MISSING' || true
done'''

_CAPABILITY: dict[tuple[str, str], dict] = {}


def _parse_tcl_probe(text: str) -> tuple[dict[str, str], list[str], dict[str, list[str]]]:
    """Unique expansions become aliases; none is recorded; several refuse."""
    aliases: dict[str, str] = {}
    unresolved: set[str] = set()
    ambiguous: dict[str, list[str]] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] != 'VIBEIC_TCL_MISSING':
            continue
        name, expansions = parts[1], [x.lstrip(':') for x in parts[2:]]
        if len(expansions) == 1:
            aliases[name] = expansions[0]
        elif expansions:
            ambiguous[name] = expansions
        else:
            unresolved.add(name)
    return aliases, sorted(unresolved), ambiguous


def image_capability(image: str, docker: str = 'docker') -> dict:
    key = (image, docker)
    if key in _CAPABILITY:
        return _CAPABILITY[key]
    probe = [docker, 'run', '--rm', '--entrypoint', 'sh', image, '-c',
             'python3 -m librelane.steps run --help >/dev/null && '
             'yosys -Q -T -y /dev/null -p help >/dev/null']
    result = subprocess.run(probe, capture_output=True, text=True)
    if result.returncode:
        raise Refusal('LL_IMAGE_INCAPABLE', f'{image}: LibreLane CLI or yosys CLI -y unavailable (rc={result.returncode})')
    tcl = subprocess.run([docker, 'run', '--rm', '--network', 'none', '--entrypoint', 'bash',
                          image, '-c', _TCL_PROBE], capture_output=True, text=True)
    if tcl.returncode:
        # The CLI probe above is the capability verdict; this one only derives
        # aliases.  Unmeasured means none are added: a step that needs one
        # then fails by name (LL_STEP_FAILED), never silently passes.
        _CAPABILITY[key] = {'image': image, 'openroad_aliases': {},
                            'tcl_probe': f'NOT_MEASURED: rc={tcl.returncode}'}
        return _CAPABILITY[key]
    aliases, unresolved, ambiguous = _parse_tcl_probe(tcl.stdout)
    if ambiguous:
        raise Refusal('LL_IMAGE_TCL_API_SKEW', json.dumps(ambiguous, sort_keys=True))
    _CAPABILITY[key] = {'image': image, 'openroad_aliases': aliases,
                        'unresolved_guarded': unresolved, 'tcl_probe': 'MEASURED'}
    return _CAPABILITY[key]


def openroad_home(folder: Path, capability: dict | None,
                  extra: list[str] | None = None) -> Path | None:
    """Write the init file that defines the probe's aliases (and a caller's
    `extra` Tcl lines, e.g. a tool debug print the caller reads back), or
    nothing when there is neither."""
    aliases = (capability or {}).get('openroad_aliases') or {}
    if not aliases and not extra:
        return None
    folder.mkdir(parents=True, exist_ok=True)
    lines = ['# vibe-ic librelane_contract: abbreviation aliases derived from the image']
    for short, full in sorted(aliases.items()):
        lines.append(f'if {{[llength [info commands ::{short}]] == 0}} '
                     f'{{ proc ::{short} {{args}} {{ return [::{full} {{*}}$args] }} }}')
    if extra:
        lines += ['# vibe-ic librelane_contract: caller-declared init lines', *extra]
    (folder / '.openroad').write_text('\n'.join(lines) + '\n')
    (folder / '.bashrc').write_text('')
    return folder


def derive_step_config(config: Path, output: Path, updates: dict[str, tuple[Any, str]]) -> Path:
    """A copy of a resolved step config with named keys set, each with its source.

    Used for values LibreLane's design-config loader drops before a step sees
    them (a plugin step's own variables) and for one step run per corner.  The
    step's view sidecar is copied beside it; every change is recorded in
    ``<output>.provenance.json``.  An unknown key is refused: the step's own
    ``Step.load`` validates the result against its declared variables.
    """
    doc = _load(config)
    provenance = {}
    for key, (value, source) in updates.items():
        doc[key] = value
        provenance[key] = source
    write_json(output, doc)
    write_json(output.with_suffix('.provenance.json'),
               {'derived_from': str(config), 'derived_from_sha256': digest(config),
                'keys': provenance})
    if views_path(config).is_file():
        write_json(views_path(output), _load(views_path(config)))
    return output


#: A step's production default once its lane has CUT OVER (MIGRATION_COMMON
#: criteria (a) and (b), or b-analog for an analog observer step). A step not
#: named here defaults to `direct`. A project opts out of a cut-over default by
#: naming the step `direct` in `phase3/librelane_switch.json`.
PRODUCTION_DEFAULTS: dict[str, str] = {}

#: The chip path: a die that carries its own pad ring
#: (`_tapeout_declaration.requests_pad_ring`, the condition of step 15.5ic).
DESIGN_CLASS_CHIP_PAD_RING = 'chip_pad_ring'

#: Production defaults of a DESIGN CLASS, for steps whose tool path is proven
#: only there. T96 (2026-09-27) cut steps 15..20 over on the chip path: the
#: LibreLane Chip segment Floorplan..PadRing..GeneratePDN (15, 15.5ic),
#: GlobalPlacement..DetailedPlacement + Vibeic.InsertSpareCells (17, 18) and
#: CTS..ResizerTimingPostCTS (19, 20), measured on spm x gf180mcuD against the
#: direct chain (docs/librelane_contract.md, "Cut-over of 15..20"). A core-only
#: or HARDMACRO design has no Chip-flow segment (LL_FLOORPLAN_CORE_ONLY_UNSUPPORTED),
#: so it keeps `direct`. A step-wide `PRODUCTION_DEFAULTS` entry outranks these.
CLASS_PRODUCTION_DEFAULTS: dict[str, dict[str, str]] = {
    DESIGN_CLASS_CHIP_PAD_RING: {'15': 'librelane', '15.5ic': 'librelane',
                                 '17': 'librelane', '18': 'librelane',
                                 '19': 'librelane', '20': 'librelane'},
}

#: A class default runs only inside the chain it continues. The producers are
#: one LibreLane chain (15.5ic -> 15 -> 17/18) or one deck region (19 with 20),
#: so a project that names one of these steps anything but `librelane` takes
#: the steps that depend on it back to `direct` with it, instead of meeting
#: the runner's split refusals (LL_FLOORPLAN_PADRING_SPLIT_UNSUPPORTED,
#: LL_PLACEMENT_NEEDS_LIBRELANE_FLOORPLAN, LL_SPARE_PLACEMENT_SPLIT_UNSUPPORTED,
#: LL_CTS_HOLD_SPLIT_UNSUPPORTED) on a combination it never asked for.
CLASS_DEFAULT_REQUIRES: dict[str, tuple[str, ...]] = {
    '15': ('15.5ic',), '17': ('15', '15.5ic', '18'), '18': ('15', '15.5ic', '17'),
    '19': ('20',), '20': ('19',)}


def design_class(project: Path) -> str | None:
    """The design class whose production defaults apply, or None."""
    import _tapeout_declaration as TD
    return DESIGN_CLASS_CHIP_PAD_RING if TD.requests_pad_ring(project) else None


def _class_default(project: Path, step: str, named: dict[str, Any],
                   _seen: frozenset[str] = frozenset()) -> str | None:
    """`step`'s class default, when every step it continues also resolves to
    `librelane`; None otherwise (the caller then uses `direct`)."""
    defaults = CLASS_PRODUCTION_DEFAULTS.get(design_class(project) or '', {})
    mode = defaults.get(step)
    if mode is None:
        return None
    seen = _seen | {step}
    for need in CLASS_DEFAULT_REQUIRES.get(step, ()):
        if need in named:
            if named[need] != 'librelane':
                return None
        elif need not in seen and _class_default(project, need, named, seen) != 'librelane':
            return None
    return mode


def selected_mode(project: Path, step: str) -> str:
    """The project's switch when it names the step, else the production
    default for the step, else the design class's default (when the steps
    it continues resolve to LibreLane too), else `direct`. An invalid mode is
    refused, from either source."""
    path = project / 'phase3/librelane_switch.json'
    steps = _load(path).get('steps', {}) if path.is_file() else {}
    if step in steps:
        mode = steps[step]
    elif step in PRODUCTION_DEFAULTS:
        mode = PRODUCTION_DEFAULTS[step]
    else:
        mode = _class_default(project, step, steps) or 'direct'
    if mode not in ('direct', 'librelane', 'dual'):
        raise Refusal('LL_INVALID_SWITCH', f'{step}: {mode}')
    return mode


def class_defaults_in_force(project: Path) -> dict[str, str]:
    """The steps this project runs on a class default rather than its switch:
    `{step: mode}` for every class-default step the switch does not name and
    whose default survives `CLASS_DEFAULT_REQUIRES`. Empty for a project
    outside every class."""
    path = project / 'phase3/librelane_switch.json'
    steps = _load(path).get('steps', {}) if path.is_file() else {}
    cls = design_class(project)
    return {step: selected_mode(project, step)
            for step in CLASS_PRODUCTION_DEFAULTS.get(cls or '', {})
            if step not in steps and step not in PRODUCTION_DEFAULTS
            and _class_default(project, step, steps) is not None}


#: vibe-ic's own LibreLane steps (`Vibeic.*`), shipped with this plugin in
#: `programs/librelane_plugins/librelane_plugin_vibeic`. LibreLane discovers a
#: `librelane_plugin_*` module on `sys.path`; the contract mounts `programs/`
#: read-only at its own path and adds the plugin root to `PYTHONPATH` only
#: for a run that names such a step, so every other step's invocation and
#: fingerprint are unchanged.
PLUGIN_ROOT = Path(__file__).resolve().parent / 'librelane_plugins'
PLUGIN_STEP_PREFIX = 'Vibeic.'
#: The `programs/` modules the plugin's steps import (inputs of those steps).
PLUGIN_HOST_MODULES = ('_spare_plan.py', 'dynamic_ir_vectored_emit.py')


def _plugin_args(step_ids: list[str]) -> list[str]:
    if not any(str(s).startswith(PLUGIN_STEP_PREFIX) for s in step_ids):
        return []
    programs = PLUGIN_ROOT.parent.resolve()
    return ['-v', f'{programs}:{programs}:ro', '-e', f'PYTHONPATH={PLUGIN_ROOT.resolve()}']


def _plugin_digests(step_id: str) -> dict[str, str]:
    """The custom step's code is an input of that step: its files, and the
    plan builder it imports, by sha256."""
    if not step_id.startswith(PLUGIN_STEP_PREFIX):
        return {}
    files = sorted(PLUGIN_ROOT.rglob('*.py')) + sorted(PLUGIN_ROOT.rglob('*.tcl')) + [
        PLUGIN_ROOT.parent / name for name in PLUGIN_HOST_MODULES]
    return {str(path.relative_to(PLUGIN_ROOT.parent)): digest(path)
            for path in files if path.is_file()}


def resolve_step_configs(project: Path, image: str, pdk: str,
                         step_ids: list[str], *, pdk_root: Path,
                         docker: str = 'docker',
                         folder: str = '37-config',
                         overlay: dict[str, tuple[Any, str]] | None = None) -> dict[str, Path]:
    """Resolve step configs from declared design inputs and the image's PDK.

    The installed LibreLane resolver supplies PDK values.  A prior run's
    resolved.json (including its design-specific numbers) is never an input.
    """
    image_capability(image, docker)
    if not (pdk_root / pdk).is_dir():
        raise Refusal('LL_PDK_MISSING', str(pdk_root / pdk))
    root = project / 'phase3/librelane' / folder
    root.mkdir(parents=True, exist_ok=True)
    design = root / 'design.json'
    emitted = emit_config(project, pdk, design)
    if overlay:
        sources = _load(design.with_suffix('.provenance.json'))
        for key, (value, source) in overlay.items():
            for older in _LEVER_SUPERSEDES.get(key, ()):
                if older in emitted:
                    emitted.pop(older)
                    sources[older] = f'superseded by {key} ({source})'
            _set(emitted, sources, key, value, source)
        write_json(design, emitted)
        write_json(design.with_suffix('.provenance.json'), sources)
    requested = root / 'steps.json'
    write_json(requested, step_ids)
    script = '''import json,sys
from pathlib import Path
from librelane.flows.chip import Chip
from librelane.steps import Step
design, requested, output, pdk, project = sys.argv[1:]
flow = Chip(config=design, pdk=pdk, pdk_root="/pdk", design_dir=project)
raw = flow.config.to_raw_dict()
for step_id in json.loads(Path(requested).read_text()):
    target = Step.factory.get(step_id)
    if target is None:
        raise ValueError("unknown LibreLane step: " + step_id)
    names = {var.name for var in target.get_all_config_variables()}
    selected = {key: value for key, value in raw.items() if key in names}
    # A step outside the Chip flow (vibe-ic's own `Vibeic.*`) declares
    # variables the flow's resolver does not know, so the resolved dict drops
    # them; take exactly those, and only those, from the declared design file.
    if step_id.startswith("Vibeic."):
        declared = json.loads(Path(design).read_text())
        selected.update({key: value for key, value in declared.items()
                         if key in names and key not in selected})
    selected["meta"] = {"librelane_version": __import__("librelane.__version__", fromlist=["__version__"]).__version__, "step": step_id}
    Path(output, step_id + ".json").write_text(json.dumps(selected, indent=2, default=str) + "\\n")
    # LibreLane's Meta refuses unknown keys, so the step's declared views live beside it.
    Path(output, step_id + ".views.json").write_text(json.dumps({
        "step": step_id,
        "inputs": [getattr(f, "id", None) or f.value.id for f in target.inputs],
        "outputs": [getattr(f, "id", None) or f.value.id for f in target.outputs]}) + "\\n")
# The flow's own gates (`Flow.gating_config_vars`): a flow skips a step whose
# gating variable is false; a caller running steps one by one must too.
gates = getattr(Chip, "gating_config_vars", {}) or {}
Path(output, "flow_gates.json").write_text(json.dumps({
    step_id: {var: raw.get(var) for var in gates.get(step_id, [])}
    for step_id in json.loads(Path(requested).read_text()) if step_id in gates},
    indent=2, default=str) + "\\n")
'''
    cmd = [docker, 'run', '--rm', '-v', f'{project.resolve()}:{project.resolve()}',
           '-v', f'{pdk_root.resolve()}:/pdk:ro', *_plugin_args(step_ids),
           '--entrypoint', 'python3', image, '-c', script, str(design),
           str(requested), str(root), pdk, str(project.resolve())]
    result = subprocess.run(cmd, capture_output=True, text=True)
    (root / 'resolution.log').write_text(result.stdout + '\n' + result.stderr)
    if result.returncode:
        raise Refusal('LL_CONFIG_RESOLUTION_FAILED', str(root / 'resolution.log'))
    configs = {step: root / f'{step}.json' for step in step_ids}
    for step, path in configs.items():
        if not path.is_file() or _load(path).get('meta', {}).get('step') != step:
            raise Refusal('LL_STEP_CONFIG_MISSING',
                          f'{step}: no config at {path} naming meta.step {step!r}')
    return configs


def flow_gated_off(config_root: Path) -> dict[str, list[str]]:
    """The requested steps the flow itself would skip, each with the gating
    variables that are false, from ``flow_gates.json`` written by
    ``resolve_step_configs`` (the image's ``Flow.gating_config_vars`` over the
    resolved design config). A missing file refuses: an ungated chain would
    run steps the flow never runs (a heuristic diode on every pin, MEASURED
    on spm: 206 diodes and 92 max-fanout DRVs)."""
    path = config_root / 'flow_gates.json'
    if not path.is_file():
        raise Refusal('LL_FLOW_GATES_UNRESOLVED', str(path))
    return {step: sorted(var for var, value in gates.items() if not value)
            for step, gates in _load(path).items()
            if any(not value for value in gates.values())}


def emit_pdn_cfg(image: str, pdk: str, output: Path, *, docker: str = 'docker') -> Path | None:
    """The image's own PDN script plus the PDK registry's pad-facing connects.

    LibreLane has no variable for an extra ``add_pdn_connect``; its default
    ``pdn_cfg.tcl`` builds the core ring but never joins it to the pads'
    core-facing supply pins.  On the spm chip path that left all 3,391,999
    grid shapes floating (``design__power_grid_violation__count``).  The
    registry's ``pdn_ring.connects`` is the declaration the direct deck's own
    ring uses; nothing else is added.  None when nothing is declared.
    """
    registry = Path(__file__).resolve().parent / 'pdk_registry.json'
    entries = _load(registry).get('pdks', []) if registry.is_file() else []
    if isinstance(entries, dict):
        entries = [dict(v, name=k) for k, v in entries.items() if isinstance(v, dict)]
    ring = next((e.get('pdn_ring') for e in entries
                 if isinstance(e, dict) and e.get('name') == pdk), None) or {}
    connects = [pair for pair in ring.get('connects') or []
                if isinstance(pair, list) and len(pair) == 2]
    if not (ring.get('connect_to_pad_layers') and connects):
        return None
    script = ('import os,librelane;print(open(os.path.join(os.path.dirname(librelane.__file__),'
              '"scripts","openroad","common","pdn_cfg.tcl")).read(),end="")')
    result = subprocess.run([docker, 'run', '--rm', '--network', 'none', '--entrypoint',
                             'python3', image, '-c', script], capture_output=True, text=True)
    if result.returncode or 'add_pdn_connect' not in result.stdout:
        raise Refusal('LL_PDN_CFG_UNREADABLE', (result.stderr or '')[-500:])
    lines = [result.stdout.rstrip('\n'), '',
             f'# vibe-ic: pdk_registry.json pdks[name={pdk}].pdn_ring.connects',
             *[f'add_pdn_connect -grid stdcell_grid -layers {{{a} {b}}}' for a, b in connects]]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('\n'.join(lines) + '\n')
    return output


def flow_segment(image: str, first: str, last: str, *, flow: str = 'Chip',
                 docker: str = 'docker') -> list[str]:
    """The step ids ``first..last`` exactly as the image's own flow orders them."""
    script = ('import json,sys;from librelane.flows import Flow;'
              'f=Flow.factory.get(sys.argv[1]);'
              'print(json.dumps([s.id for s in f.Steps]))')
    result = subprocess.run([docker, 'run', '--rm', '--network', 'none', '--entrypoint',
                             'python3', image, '-c', script, flow],
                            capture_output=True, text=True)
    try:
        order = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        order = None
    if result.returncode or not isinstance(order, list):
        raise Refusal('LL_FLOW_UNRESOLVED', f'{flow}: {(result.stderr or "")[-500:]}')
    if first not in order or last not in order or order.index(first) > order.index(last):
        raise Refusal('LL_FLOW_SEGMENT_INVALID', f'{flow}: {first}..{last}')
    return order[order.index(first):order.index(last) + 1]


def resolve_step_config(project: Path, image: str, source: Path, output: Path,
                        *, mounts: list[tuple[Path, str]] | None = None,
                        pdk_root: str | None = None, docker: str = 'docker') -> Path:
    """Ask LibreLane to apply its PDK config before the step-only CLI runs."""
    script = (
        'import json,os,tempfile;'
        'from librelane.config import Config;'
        'from librelane.steps import Step;'
        f'p={str(source)!r}; out={str(output)!r}; root={pdk_root!r};'
        '_,cls=Step.factory.from_step_config(p);'
        f'cfg,_=Config.load(p,cls.get_all_config_variables(),design_dir={str(project)!r},pdk_root=root);'
        'fd,tmp=tempfile.mkstemp(dir=os.path.dirname(out));'
        'os.write(fd,cfg.dumps().encode());os.close(fd);os.replace(tmp,out)'
    )
    volumes = ['-v', f'{project.resolve()}:{project.resolve()}']
    for host, guest in mounts or []:
        volumes += ['-v', f'{host.resolve()}:{guest}:ro']
    result = subprocess.run([docker, 'run', '--rm', *volumes,
                             '--entrypoint', 'python3', image, '-c', script],
                            capture_output=True, text=True)
    if result.returncode or not output.is_file():
        raise Refusal('LL_CONFIG_RESOLVE_FAILED',
                      (result.stderr or result.stdout)[-1000:])
    return output

def run_chain(project: Path, image: str, steps: list[tuple[str, Path, Path]],
              *, docker: str = 'docker', mounts: list[tuple[Path, str]] | None = None,
              lane: str | None = None, pdk_root: str | None = None,
              namespace: str | None = None,
              openroad_init: list[str] | None = None) -> list[Path]:
    """Run pinned per-step snapshots. Step directories retain both inputs and outputs.

    ``openroad_init``: extra Tcl lines for the OpenROAD init file every
    OpenROAD step reads (joins each step's fingerprint).
    """
    capability = image_capability(image, docker)
    outputs = []
    previous: Path | None = None
    if lane is not None and (not lane or '/' in lane or lane in ('.', '..')):
        raise Refusal('LL_INVALID_LANE', str(lane))
    if namespace and (Path(namespace).is_absolute() or
                      any(part in ('..', '.') for part in Path(namespace).parts)):
        raise Refusal('LL_INVALID_NAMESPACE', namespace)
    if lane and namespace:
        raise Refusal('LL_LANE_NAMESPACE_CONFLICT', f'{lane}: {namespace}')
    base = project / 'phase3/librelane'
    if lane:
        base /= lane
    if namespace:
        base /= namespace
    home = openroad_home(base / '.openroad_home', capability, openroad_init)
    for index, (step_id, config, initial_state) in enumerate(steps, 1):
        name = f'{index:02d}-{step_id.lower().replace(".", "-")}'
        folder = base / name
        state_path = previous or initial_state
        state = _load(state_path)
        _check_state(state, step_id=step_id)
        if _load(config).get('meta', {}).get('step') != step_id:
            raise Refusal('LL_STEP_CONFIG_MISMATCH', step_id)
        # A step's DECLARED outputs are not a promise (KLayout.StreamOut
        # declares `gds` and writes only `klayout_gds`), so the views the next
        # step declares it consumes are checked on the state it actually gets.
        if views_path(config).is_file():
            declared = _load(views_path(config)).get('inputs') or []
            missing = [view for view in declared if not state.get(view)]
            if missing:
                raise Refusal('LL_STATE_MISSING', f'{step_id}: {missing}')
        fingerprint = {'image': image, 'config': digest(config), 'state': digest(state_path),
                       'state_files': {str(path): digest(path) for path in _walk_paths(
                           {k: v for k, v in state.items() if k != 'metrics'})},
                       # Files the step config names (SDC, EQY script, PDN Tcl…)
                       # are inputs too: an edited deck must re-run the step.
                       'config_files': {str(path): digest(path) for path in _walk_paths(
                           _load(config)) if path.is_file()},
                       'step': step_id}
        if home:
            fingerprint['openroad_aliases'] = capability['openroad_aliases']
        if step_id.startswith(PLUGIN_STEP_PREFIX):
            fingerprint['plugin'] = _plugin_digests(step_id)
        if openroad_init:
            fingerprint['openroad_init'] = list(openroad_init)
        receipt = folder / 'vibeic_receipt.json'
        if receipt.exists() and _load(receipt).get('input') == fingerprint and (folder / 'state_out.json').exists():
            _check_state(_load(folder / 'state_out.json'), outputs=True)
            previous = folder / 'state_out.json'
            outputs.append(folder)
            continue
        if folder.exists():
            archive = base / 'attempts'
            archive.mkdir(parents=True, exist_ok=True)
            number = 1
            while (archive / f'{name}-{number:04d}').exists():
                number += 1
            shutil.move(str(folder), str(archive / f'{name}-{number:04d}'))
        folder.mkdir(parents=True, exist_ok=True)
        write_json(folder / 'input_fingerprint.json', fingerprint)
        volume_args = ['-v', f'{project.resolve()}:{project.resolve()}']
        for host, guest in mounts or []:
            volume_args += ['-v', f'{host.resolve()}:{guest}:ro']
        if home:
            volume_args += ['-e', f'HOME={home.resolve()}']
        volume_args += _plugin_args([step_id])
        cmd = [docker, 'run', '--rm', *volume_args, '--entrypoint', 'python3', image,
               '-m', 'librelane.steps', 'run', '--id', step_id, '-c', str(config),
               '-i', str(state_path), '-o', str(folder)]
        if pdk_root:
            cmd.extend(['--pdk-root', pdk_root])
        completed = subprocess.run(cmd, capture_output=True, text=True)
        (folder / 'invocation.log').write_text(completed.stdout + '\n' + completed.stderr)
        if completed.returncode or not (folder / 'state_out.json').exists():
            raise Refusal('LL_STEP_FAILED', f'{step_id}: rc={completed.returncode}; {folder / "invocation.log"}')
        out_state = _load(folder / 'state_out.json')
        _check_state(out_state, outputs=True)
        hashes = {'state_out.json': digest(folder / 'state_out.json')}
        for path in _walk_paths({k: v for k, v in out_state.items() if k != 'metrics'}):
            if path.is_relative_to(folder):
                hashes[str(path.relative_to(folder))] = digest(path)
        for path in folder.rglob('*'):
            if path.is_file() and path.name.endswith(('.json', '.rpt')) and path.name not in ('vibeic_receipt.json',):
                hashes[str(path.relative_to(folder))] = digest(path)
        write_json(receipt, {'input': fingerprint, 'sha256': hashes})
        previous = folder / 'state_out.json'
        outputs.append(folder)
    return outputs


#: STAPostPNR writes one `sta.log` per analysed corner. This line names each
#: cell library it read for that corner (LibreLane 3.1 `scripts/openroad/sta`).
_STA_CELL_LIBRARY_RE = re.compile(
    r"^Reading cell library for the '([^']+)' corner at '([^']+)'", re.M)


def _state_step_id(folder: Path) -> str | None:
    """The LibreLane step that wrote `folder`, from its own records."""
    receipt = folder / 'vibeic_receipt.json'
    if receipt.is_file():
        return _load(receipt).get('input', {}).get('step')
    config = folder / 'config.json'
    if config.is_file():
        return _load(config).get('meta', {}).get('step')
    return None


def post_pnr_timing_inputs(project: Path, state_path: Path, corner: str) -> dict:
    """The routed netlist, SDC, SPEF and cell libraries `OpenROAD.STAPostPNR`
    timed `corner` with, as project-relative paths.

    Netlist, SDC and SPEF come from the step's `state_out.json`; the SPEF is
    the one whose corner pattern matches `corner` (the same `fnmatch` rule
    LibreLane applies). The cell libraries are the ones the step's own
    `<corner>/sta.log` says it read. A corner the step did not analyse, an
    ambiguous or absent SPEF, or a view outside the project is refused.
    """
    folder = state_path.parent
    step = _state_step_id(folder)
    if step != 'OpenROAD.STAPostPNR':
        raise Refusal('LL_NOT_STAPOSTPNR', f'{folder}: written by {step}')
    state = _load(state_path)
    log = folder / corner / 'sta.log'
    if not log.is_file():
        analysed = sorted(d.name for d in folder.iterdir()
                          if (d / 'sta.log').is_file())
        raise Refusal('LL_CORNER_NOT_ANALYSED', f'{corner}: {analysed}')
    spefs = state.get('spef') or {}
    if isinstance(spefs, str):
        spefs = {'*': spefs}
    matched = [path for pattern, path in spefs.items()
               if fnmatch.fnmatch(corner, pattern)]
    if len(matched) != 1:
        raise Refusal('LL_SPEF_UNRESOLVED', f'{corner}: {len(matched)} matching SPEF')
    libraries = [path for name, path in
                 _STA_CELL_LIBRARY_RE.findall(log.read_text(errors='replace'))
                 if name == corner]
    if not libraries:
        raise Refusal('LL_CORNER_LIBRARY_UNREAD', str(log))
    root = project.resolve()
    result: dict[str, Any] = {'step': step, 'corner': corner,
                              'liberties': libraries}
    for key, value in (('sta_netlist', state.get('nl')),
                       ('sdc', state.get('sdc')), ('spef', matched[0]),
                       ('state', str(state_path))):
        path = Path(value or '')
        if not value or not path.is_file():
            raise Refusal('LL_STATE_FILE_MISSING',
                          f'{key}: no file at path {value!r}')
        if not path.resolve().is_relative_to(root):
            raise Refusal('LL_STATE_OUTSIDE_PROJECT', f'{key}: {value}')
        result[key] = str(path.resolve().relative_to(root))
    result['sha256'] = {key: digest(root / result[key])
                        for key in ('sta_netlist', 'sdc', 'spef', 'state')}
    return result


def judge_step(folder: Path, required_metrics: list[str], output: Path,
               limits: dict[str, dict[str, float]] | None = None,
               required_reports: list[str] | None = None,
               scope: dict[str, str] | None = None) -> dict:
    state = _load(folder / 'state_out.json')
    metrics = dict(state.get('metrics', {}))
    if (folder / 'metrics.json').is_file():
        metrics.update(_load(folder / 'metrics.json'))
    rows = {key: {'status': 'MEASURED', 'value': metrics[key]} if key in metrics and metrics[key] is not None
            else {'status': 'NOT_MEASURED'} for key in required_metrics}
    verdict = 'PASS' if rows and all(x['status'] == 'MEASURED' for x in rows.values()) else 'NOT_MEASURED'
    for key, bounds in (limits or {}).items():
        if key not in rows or rows[key]['status'] != 'MEASURED':
            verdict = 'NOT_MEASURED'
            continue
        value = rows[key]['value']
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            rows[key]['status'] = 'INVALID'
            verdict = 'FAIL'
            continue
        if any((bound == 'min' and value < threshold) or
               (bound == 'max' and value > threshold) or
               (bound == 'eq' and value != threshold)
               for bound, threshold in bounds.items()):
            rows[key]['status'] = 'FAIL'
            verdict = 'FAIL'
    result = {'verdict': verdict, 'metrics': rows, 'scope': scope or {},
              'source': str(folder / 'state_out.json'),
              'sha256': {'state_out.json': digest(folder / 'state_out.json')}}
    for name in ('metrics.json',):
        if (folder / name).exists():
            result['sha256'][name] = digest(folder / name)
    reports = {}
    for name in required_reports or []:
        path = folder / name
        if path.is_file():
            reports[name] = {'status': 'PRESENT', 'sha256': digest(path)}
            result['sha256'][name] = reports[name]['sha256']
        else:
            reports[name] = {'status': 'NOT_MEASURED'}
            if result['verdict'] != 'FAIL':
                result['verdict'] = 'NOT_MEASURED'
    result['reports'] = reports
    write_json(output, result)
    return result


def select_arms(arms: dict[str, Path], objectives: dict[str, str], output: Path) -> dict:
    """Measured, same-key Pareto selection; ties retain both arms for review."""
    from _ppa.pareto import dominates, Objective
    data = {name: _load(path) for name, path in arms.items()}
    eligible = {name: doc for name, doc in data.items()
                if doc.get('verdict') == 'PASS' and
                all(doc.get('metrics', {}).get(k, {}).get('status') == 'MEASURED' for k in objectives)}
    if len(eligible) != len(data):
        verdict = {'selection': 'UNDETERMINED', 'reason': 'LL_ARM_NOT_MEASURED', 'arms': list(arms)}
    elif len({json.dumps(doc.get('scope'), sort_keys=True) for doc in eligible.values()}) != 1 or \
            any(not isinstance(doc.get('scope'), dict) or not doc['scope'] for doc in eligible.values()):
        verdict = {'selection': 'UNDETERMINED', 'reason': 'LL_ARM_SCOPE_MISMATCH', 'arms': list(arms)}
    else:
        # Reuse the Pareto domination relation, with explicit senses and equal scope.
        names = list(eligible)
        axes = [Objective(k, k, sense, {'step': 'same'}) for k, sense in objectives.items()]
        values = {n: {'values': {k: {'value': eligible[n]['metrics'][k]['value']} for k in objectives}}
                  for n in names}
        frontier = [n for n in names if not any(dominates(values[m], values[n], axes)
                    for m in names if m != n)]
        verdict = {'selection': frontier[0] if len(frontier) == 1 else 'UNDETERMINED',
                   'frontier': frontier, 'arms': list(arms), 'reason': None if len(frontier) == 1 else 'LL_PARETO_TIE'}
    write_json(output, verdict)
    return verdict


def execute_dual(project: Path, step_id: str,
                 librelane_arm: Callable[[Path], Path],
                 openroad_arm: Callable[[Path], Path],
                 objectives: dict[str, str]) -> dict:
    """Invoke both producers in isolated directories and retain their evidence."""
    root = project / 'phase3/tool_arms' / step_id
    reports = {}
    for name, producer in (('librelane', librelane_arm), ('openroad', openroad_arm)):
        folder = root / name
        folder.mkdir(parents=True, exist_ok=True)
        report = producer(folder)
        if not report.resolve().is_relative_to(folder.resolve()) or not report.is_file():
            raise Refusal('LL_ARM_REPORT_MISSING', f'{name}: {report}')
        reports[name] = report
    return select_arms(reports, objectives, root / 'selection.json')


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    emit = commands.add_parser('emit-config')
    emit.add_argument('project', type=Path); emit.add_argument('pdk'); emit.add_argument('output', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'emit-config':
            emit_config(args.project, args.pdk, args.output)
        return 0
    except Refusal as error:
        print(error, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
