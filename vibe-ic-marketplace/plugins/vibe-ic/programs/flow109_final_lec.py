"""Blocking D44 component: native LibreLane EQY on the adopted final netlist.

No pre-layout proof is reused. Functional model/port census and complete EQY
partition results are required. Unproven/unsupported transformations remain
NOT_MEASURED. Produces a common netlist baseline, not downstream STA/LVS PASS.
"""
from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from _atomic_artefact import write_json
from flow109_admission import AdmissionRefusal, require_launch_admission
import librelane_eqy as eqy

SCHEMA = 'vibeic.flow109.final-lec.v1'
INPUT = 'input/flow109/D44.json'
BASELINE = 'adopted/final_lec/sta_lvs_release_netlist_mapping.json'


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def operation_sources():
    from execution_provider_catalog import source_closure
    paths = source_closure({HERE / 'librelane_eqy.py', HERE / 'lec_post_layout_check.py'})
    paths.update((Path(__file__), HERE / 'flow109.py', HERE / 'flow109_admission.py'))
    result = {str(path.relative_to(HERE.parent)): _sha(path) for path in sorted(paths)}
    # Native EQY is Python too; its installed Step implementation is part of
    # the qualified operation, rather than merely the interpreter version.
    from librelane.steps.yosys import EQY
    native = Path(inspect.getsourcefile(EQY))
    result['native/librelane/steps/yosys.py'] = _sha(native)
    return result


def _read(path):
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise AdmissionRefusal('D44_INPUT_ABSENT', str(path)) from exc
    if not isinstance(value, dict):
        raise AdmissionRefusal('D44_INPUT_INVALID', str(path))
    return value


def _bound(project, value):
    if not isinstance(value, dict) or not value.get('path') or not value.get('sha256'):
        raise AdmissionRefusal('D44_INPUT_UNBOUND', str(value))
    path = (project / value['path']).resolve()
    if not path.is_file() or _sha(path) != value['sha256']:
        raise AdmissionRefusal('D44_INPUT_STALE_OR_MISSING', str(path))
    return path


def _record(project, path):
    path = Path(path).resolve()
    return {'path': str(path.relative_to(project)) if path.is_relative_to(project) else str(path),
            'sha256': _sha(path)}


def prepare(project, context):
    if context.get('requested_step') != 'D44' or context.get('native_step_id') != 'flow109.D44':
        raise AdmissionRefusal('D44_CONTEXT_MISMATCH', 'final netlist EQY component only')
    sources = operation_sources()
    uses = context['runtime_tool_uses']
    for use in uses:
        if use.get('semantics', {}).get('operation_sources') != sources:
            raise AdmissionRefusal('D44_OPERATION_SOURCE_CHANGED', use.get('use_id'))
    manifest = _read(project / INPUT)
    if manifest.get('schema') != SCHEMA or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$]*', str(manifest.get('top', ''))):
        raise AdmissionRefusal('D44_INPUT_INVALID', 'schema/top must identify the exact proof subject')
    if manifest.get('transformations') != []:
        raise AdmissionRefusal('D44_TRANSFORMATION_NOT_QUALIFIED',
                               'power/physical-cell or port transformations require their own proven source chain')
    rtl = manifest.get('rtl')
    if not isinstance(rtl, list) or not rtl:
        raise AdmissionRefusal('D44_RTL_ABSENT', 'nonempty reference RTL population required')
    paths = {'rtl': [_bound(project, item) for item in rtl],
             'adopted_netlist': _bound(project, manifest.get('adopted_netlist')),
             'liberty': _bound(project, manifest.get('liberty'))}
    roles = manifest.get('consumer_netlists')
    if not isinstance(roles, dict) or set(roles) != {'sta', 'lvs_reference', 'release'}:
        raise AdmissionRefusal('D44_CONSUMER_BASELINE_ABSENT', 'STA/LVS-reference/release must all have a source')
    for role, item in roles.items():
        value = _bound(project, item)
        if _sha(value) != _sha(paths['adopted_netlist']):
            raise AdmissionRefusal('D44_CONSUMER_BASELINE_DIFFERS', role)
        paths[role] = value
    native = manifest.get('librelane')
    if (not isinstance(native, dict) or set(native) != {'PDK', 'STD_CELL_LIBRARY', 'pdk_root'}
            or not Path(native['pdk_root']).is_dir()):
        raise AdmissionRefusal('D44_PDK_CONFIGURATION_ABSENT', 'explicit existing LibreLane PDK root/library required')
    for use in uses:
        if _bound(project, use['deck']) != paths['liberty']:
            raise AdmissionRefusal('D44_LIBRARY_NOT_QUALIFIED', use['use_id'])
        pdk = _read(_bound(project, use['pdk']))
        if pdk != {'librelane': native, 'liberty': manifest['liberty']}:
            raise AdmissionRefusal('D44_PDK_SCOPE_NOT_QUALIFIED', use['use_id'])
        config = _read(_bound(project, use['config']))
        if config != {'schema': SCHEMA, 'operation': 'Yosys.EQY', 'transformations': []}:
            raise AdmissionRefusal('D44_CONFIGURATION_UNSUPPORTED', use['use_id'])
    inputs = {INPUT: _sha(project / INPUT)}
    for group in paths.values():
        for path in group if isinstance(group, list) else [group]:
            inputs[str(path)] = _sha(path)
    return {'manifest': manifest, 'paths': paths, 'sources': sources, 'inputs': inputs,
            'tools': {use['tool_id']: use['executable']['path'] for use in uses}}


def _before(project, context, prepared):
    require_launch_admission(project, 'flow109.D44', {'flow109_context': context})
    if operation_sources() != prepared['sources']:
        raise AdmissionRefusal('D44_OPERATION_SOURCE_CHANGED', 'source changed before native launch')
    for name, sha in prepared['inputs'].items():
        if not (project / name).is_file() or _sha(project / name) != sha:
            raise AdmissionRefusal('D44_INPUT_STALE_OR_MISSING', name)


def _yosys_word(path):
    return '"' + str(path).replace('\\', '\\\\').replace('"', '\\"') + '"'


def _model_census(project, prepared, attempt, before):
    """Model/port elaboration only: no second equivalence engine or proof."""
    result = {}
    for side, files in [('gold', prepared['paths']['rtl']),
                        ('gate', [prepared['paths']['adopted_netlist']])]:
        output = attempt / (side + '.model.json')
        text = ''
        if side == 'gate':
            text += 'read_liberty -ignore_miss_func ' + _yosys_word(prepared['paths']['liberty']) + '\n'
        text += 'read_verilog -formal -sv -icells ' + ' '.join(_yosys_word(p) for p in files) + '\n'
        text += f"hierarchy -check -top {prepared['manifest']['top']}\nproc\n"
        text += 'write_json ' + _yosys_word(output) + '\n'
        script = attempt / (side + '.model.ys')
        script.write_text(text)
        before()
        process = subprocess.run([prepared['tools']['yosys'], '-Q', '-T', '-s', str(script)],
                                 capture_output=True, text=True)
        (attempt / (side + '.model.log')).write_text(process.stdout + process.stderr)
        if process.returncode or not output.is_file():
            raise AdmissionRefusal('D44_MODEL_ELABORATION_UNSUPPORTED', side)
        modules = _read(output).get('modules', {})
        top = prepared['manifest']['top']
        pending, visited = [top], set()
        while pending:
            name = pending.pop()
            if name in visited or name.startswith('$'):
                continue
            visited.add(name)
            module = modules.get(name)
            attrs = module.get('attributes', {}) if isinstance(module, dict) else {}
            if module is None or any(str(attrs.get(a, '0')).strip('0') for a in ('blackbox', 'whitebox')):
                raise AdmissionRefusal('D44_FUNCTIONAL_MODEL_UNSUPPORTED', f'{side}:{name}')
            pending.extend(cell['type'] for cell in module.get('cells', {}).values())
        result[side] = {'modules': sorted(visited), 'ports': modules[top].get('ports', {})}
    # Never drop/rename a port merely to make the proof line up.
    interface = lambda side: {name: (p['direction'], len(p['bits']))
                              for name, p in result[side]['ports'].items()}
    if interface('gold') != interface('gate'):
        raise AdmissionRefusal('D44_PORT_MAPPING_UNSUPPORTED', 'reference and adopted interfaces differ')
    return result


def _native_eqy(project, prepared, attempt, before):
    from librelane.config import Config
    from librelane.state import State, DesignFormat
    from librelane.common import Path as LLPath
    from librelane.steps.yosys import EQY
    from librelane.steps import StepError

    manifest, paths = prepared['manifest'], prepared['paths']
    script = attempt / 'final.eqy'
    script.write_text(eqy.eqy_script(manifest['top'], paths['rtl'], paths['adopted_netlist'], str(paths['liberty'])))
    config, _ = Config.load({'DESIGN_NAME': manifest['top'],
        'PDK': manifest['librelane']['PDK'], 'STD_CELL_LIBRARY': manifest['librelane']['STD_CELL_LIBRARY'],
        'VERILOG_FILES': [str(p) for p in paths['rtl']], 'EQY_SCRIPT': str(script),
        'EQY_FORCE_ACCEPT_PDK': True}, EQY.get_all_config_variables(), design_dir=str(project),
        pdk_root=manifest['librelane']['pdk_root'])
    (attempt / 'resolved-config.json').write_text(config.dumps())
    # Native LibreLane has two preprocessing branches. This tranche qualifies
    # its generic iverilog branch; it does not silently run the other helper
    # (which is absent from the current image and source-owned use inventory).
    if config['PDK'].startswith('sky130A'):
        raise AdmissionRefusal('D44_NATIVE_PREPROCESSOR_UNQUALIFIED',
                               'LibreLane formal_pdk_proc branch needs a separately qualified tool use')
    model_inputs = {str(p): _sha(p) for p in config['CELL_VERILOG_MODELS']}
    step = EQY(config=config, state_in=State({DesignFormat.NETLIST: LLPath(str(paths['adopted_netlist']))}))
    before()
    try:
        step.start(step_dir=str(attempt / 'native'))
        native_error = None
    except StepError as exc:
        # A native counterexample is an answer. Consume retained partitions;
        # do not relabel all EQY nonzero exits as proof of inequivalence.
        native_error = str(exc)
    if any(_sha(path) != sha for path, sha in model_inputs.items()):
        raise AdmissionRefusal('D44_MODEL_INPUT_CHANGED', 'LibreLane preprocessing model changed', status='FAIL')
    return native_error, model_inputs


def consume_eqy(folder, output):
    """Extend the existing consumer with a complete declared partition census."""
    result = eqy.judge_eqy(folder, output)
    listing = folder / 'scratch/partition.list'
    def incomplete(reason, **extra):
        value = {**result, 'verdict': 'FAIL' if result['verdict'] == 'FAIL' else 'NOT_MEASURED',
                 'explanation': reason, **extra}
        write_json(output, value)
        return value
    if not listing.is_file():
        return incomplete('D44_PARTITION_LIST_ABSENT')
    names = []
    for line in listing.read_text().splitlines():
        if not line.strip():
            continue
        words = line.partition(':')[0].split()
        if len(words) < 2:
            return incomplete('D44_PARTITION_LIST_INVALID')
        names.append(words[1])
    parts = result.get('partitions') or {}
    if not names or len(names) != len(set(names)) or set(names) != set(parts):
        return incomplete('D44_PARTITION_COVERAGE_INCOMPLETE', declared_partitions=names)
    evidence = [listing, *sorted((folder / 'scratch/strategies').glob('*/*/status'))]
    value = {**result, 'declared_partitions': names,
             'evidence_sha256': {str(p.relative_to(folder)): _sha(p) for p in evidence}}
    write_json(output, value)
    return value


def execute(project, context):
    project = Path(project).resolve()
    attempt = project / 'reports/flow109/D44' / uuid.uuid4().hex
    attempt.mkdir(parents=True, exist_ok=False)
    record = {'schema': SCHEMA, 'step_id': 'D44', 'component_status': 'NOT_MEASURED',
              'flow109_acceptance': 'NOT_MEASURED', 'native_started': False,
              'context_sha256': context.get('context_sha256'), 'baseline_adoption': 'NOT_MEASURED'}
    try:
        require_launch_admission(project, 'flow109.D44', {'flow109_context': context})
        prepared = prepare(project, context)
        record.update(source_sha256=prepared['sources'], inputs=prepared['inputs'])
        def before():
            _before(project, context, prepared)
            record['native_started'] = True
        record['model_census'] = _model_census(project, prepared, attempt, before)
        native_error, models = _native_eqy(project, prepared, attempt, before)
        record.update(native_error=native_error, preprocessing_models=models)
        proof = consume_eqy(attempt / 'native', attempt / 'eqy-result.json')
        record['proof'] = proof
        _before(project, context, prepared)
        record['component_status'] = {'PASS': 'PASS', 'FAIL': 'FAIL'}.get(proof['verdict'], 'NOT_MEASURED')
        record['reason'] = proof.get('explanation')
        if native_error and record['component_status'] == 'PASS':
            record.update(component_status='NOT_MEASURED', reason='D44_NATIVE_STEP_INCOMPLETE')
        if record['component_status'] == 'PASS':
            paths = prepared['paths']
            baseline = {'schema': SCHEMA, 'producer': 'flow109_final_lec',
                'context_sha256': context['context_sha256'],
                'source_sha256': prepared['sources'],
                'rtl': [_record(project, p) for p in paths['rtl']],
                'adopted_netlist': _record(project, paths['adopted_netlist']),
                'consumer_netlists': {r: _record(project, paths[r]) for r in ('sta', 'lvs_reference', 'release')},
                'transformations': [], 'mapping': 'same_logical_bytes',
                'consumer_adoption': 'NOT_MEASURED', 'proof': _record(project, attempt / 'eqy-result.json')}
            write_json(project / BASELINE, baseline)
            record['common_baseline'] = _record(project, project / BASELINE)
    except AdmissionRefusal as exc:
        record.update(component_status=exc.status, reason=exc.code, detail=str(exc))
    except (ImportError, OSError, ValueError) as exc:
        record.update(component_status='NOT_MEASURED', reason='D44_NATIVE_UNAVAILABLE_OR_UNSUPPORTED', detail=str(exc))
    record['receipt'] = {'path': str((attempt / 'component.json').relative_to(project))}
    write_json(attempt / 'component.json', record)
    return record
