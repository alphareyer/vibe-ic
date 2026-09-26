#!/usr/bin/env python3
"""Project-local LibreLane step handoff. No Phase-3 step opts in implicitly."""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import shutil
import subprocess
import sys
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


def emit_config(project: Path, pdk: str, output: Path) -> dict:
    """Emit only declared inputs; unavailable values stay absent, never guessed."""
    root = project / 'phase1/generated_docs'
    l8 = _load(root / 'L8_TIMING_WAVEFORM.json')
    l9 = _load(root / 'L9_INTEGRATION_SPEC.json')
    l19 = _load(root / 'L19_CONSTRAINTS_PDK.json')
    declaration = _load(project / 'input/submission_template/tapeout_declaration.json')
    pads = _load(project / 'phase3/stage3/pnr/pad_assignment.json')
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
    answers = declaration.get('answers', {})
    _set(result, sources, 'DESIGN_NAME', answers.get('top_cell'), 'input/submission_template/tapeout_declaration.answers.top_cell')
    _set(result, sources, 'DIE_AREA', answers.get('die_area_um'), 'input/submission_template/tapeout_declaration.answers.die_area_um')
    _set(result, sources, 'CORE_AREA', answers.get('core_area_um'), 'input/submission_template/tapeout_declaration.answers.core_area_um')
    if 'DIE_AREA' in result:
        _set(result, sources, 'FP_SIZING', 'absolute', 'input/submission_template/tapeout_declaration.answers.die_area_um')
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
    for key in ('PAD_SOUTH', 'PAD_EAST', 'PAD_NORTH', 'PAD_WEST'):
        _set(result, sources, key, pads.get(key), 'phase3/stage3/pnr/pad_assignment.json.' + key)
    for key in ('PAD_SITE_NAME', 'PAD_CORNER_SITE_NAME', 'PAD_FILLERS',
                'PAD_ROTATION_HORIZONTAL', 'PAD_ROTATION_VERTICAL',
                'PAD_ROTATION_CORNER'):
        _set(result, sources, key, pads.get(key),
             'phase3/stage3/pnr/pad_assignment.json.' + key)
    if pads.get('PAD_CORNER'):
        corner = pads['PAD_CORNER']
        _set(result, sources, 'PAD_CORNER',
             corner if isinstance(corner, list) else [corner],
             'phase3/stage3/pnr/pad_assignment.json.PAD_CORNER')
    if pads.get('PAD_EDGE_SPACING') is not None:
        try:
            spacing = float(pads['PAD_EDGE_SPACING'])
        except (TypeError, ValueError) as exc:
            raise Refusal('LL_PAD_SPACING_INVALID', str(pads['PAD_EDGE_SPACING'])) from exc
        if not 0 <= spacing < float('inf'):
            raise Refusal('LL_PAD_SPACING_INVALID', str(spacing))
        _set(result, sources, 'PAD_EDGE_SPACING', spacing,
             'phase3/stage3/pnr/pad_assignment.json.PAD_EDGE_SPACING')
    write_json(output, result)
    write_json(output.with_suffix('.provenance.json'), sources)
    return result


def emit_synthesis_config(project: Path, pdk: str, output: Path,
                          rtl_files: list[Path], defines: list[str],
                          use_slang: bool,
                          std_cell_library: str | None = None,
                          synth_liberty: str | None = None) -> dict:
    """Bind Yosys.Synthesis to the caller's selected design inputs."""
    import sparse_fsm_detect

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
        raise Refusal('LL_STAT_TOP_MISSING', top)
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


def _check_state(state: dict, *, outputs: bool = False,
                 step_id: str = '') -> None:
    # Early steps consume only the views produced so far. Floorplan creates
    # the first ODB/DEF/SDC from the mapped netlist.
    # A lone analog block has only a GDS: these steps declare GDS as their
    # sole required input (magic.py RCX/DRC, klayout.py DRC).
    early = {'Yosys.JsonHeader': (), 'Yosys.Synthesis': ('json_h',),
             'OpenROAD.CheckSDCFiles': ('nl',), 'OpenROAD.STAPrePNR': ('nl',),
             'OpenROAD.Floorplan': ('nl',),
             'Magic.RCX': ('gds',), 'Magic.DRC': ('gds',),
             'KLayout.DRC': ('gds',)}
    required = early.get(step_id, ('odb', 'def', 'nl', 'sdc'))
    if not outputs:
        for key in required:
            if not state.get(key):
                raise Refusal('LL_STATE_MISSING', key)
    for path in _walk_paths({k: v for k, v in state.items() if k != 'metrics'}):
        if not path.is_file():
            raise Refusal('LL_STATE_FILE_MISSING', str(path))


def image_capability(image: str, docker: str = 'docker') -> None:
    probe = [docker, 'run', '--rm', '--entrypoint', 'sh', image, '-c',
             'python3 -m librelane.steps run --help >/dev/null && '
             'yosys -Q -T -y /dev/null -p help >/dev/null']
    result = subprocess.run(probe, capture_output=True, text=True)
    if result.returncode:
        raise Refusal('LL_IMAGE_INCAPABLE', f'{image}: LibreLane CLI or yosys CLI -y unavailable (rc={result.returncode})')


def selected_mode(project: Path, step: str) -> str:
    path = project / 'phase3/librelane_switch.json'
    if not path.is_file():
        return 'direct'
    mode = _load(path).get('steps', {}).get(step, 'direct')
    if mode not in ('direct', 'librelane', 'dual'):
        raise Refusal('LL_INVALID_SWITCH', f'{step}: {mode}')
    return mode


def resolve_step_configs(project: Path, image: str, pdk: str,
                         step_ids: list[str], *, pdk_root: Path,
                         docker: str = 'docker') -> dict[str, Path]:
    """Resolve step configs from declared design inputs and the image's PDK.

    The installed LibreLane resolver supplies PDK values.  A prior run's
    resolved.json (including its design-specific numbers) is never an input.
    """
    image_capability(image, docker)
    if not (pdk_root / pdk).is_dir():
        raise Refusal('LL_PDK_MISSING', str(pdk_root / pdk))
    root = project / 'phase3/librelane/37-config'
    root.mkdir(parents=True, exist_ok=True)
    design = root / 'design.json'
    emit_config(project, pdk, design)
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
    selected["meta"] = {"librelane_version": __import__("librelane.__version__", fromlist=["__version__"]).__version__, "step": step_id}
    Path(output, step_id + ".json").write_text(json.dumps(selected, indent=2, default=str) + "\\n")
'''
    cmd = [docker, 'run', '--rm', '-v', f'{project.resolve()}:{project.resolve()}',
           '-v', f'{pdk_root.resolve()}:/pdk:ro',
           '--entrypoint', 'python3', image, '-c', script, str(design),
           str(requested), str(root), pdk, str(project.resolve())]
    result = subprocess.run(cmd, capture_output=True, text=True)
    (root / 'resolution.log').write_text(result.stdout + '\n' + result.stderr)
    if result.returncode:
        raise Refusal('LL_CONFIG_RESOLUTION_FAILED', str(root / 'resolution.log'))
    configs = {step: root / f'{step}.json' for step in step_ids}
    for step, path in configs.items():
        if not path.is_file() or _load(path).get('meta', {}).get('step') != step:
            raise Refusal('LL_STEP_CONFIG_MISSING', step)
    return configs


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
              namespace: str | None = None) -> list[Path]:
    """Run pinned per-step snapshots. Step directories retain both inputs and outputs."""
    image_capability(image, docker)
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
    for index, (step_id, config, initial_state) in enumerate(steps, 1):
        name = f'{index:02d}-{step_id.lower().replace(".", "-")}'
        folder = base / name
        state_path = previous or initial_state
        state = _load(state_path)
        _check_state(state, step_id=step_id)
        if _load(config).get('meta', {}).get('step') != step_id:
            raise Refusal('LL_STEP_CONFIG_MISMATCH', step_id)
        fingerprint = {'image': image, 'config': digest(config), 'state': digest(state_path),
                       'state_files': {str(path): digest(path) for path in _walk_paths(
                           {k: v for k, v in state.items() if k != 'metrics'})}, 'step': step_id}
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
