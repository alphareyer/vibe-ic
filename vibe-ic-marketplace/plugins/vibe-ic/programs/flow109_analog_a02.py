"""A02 declared analog-model qualification through real ngspice batches.

The existing completeness gate supplies unwaived inventory evidence only.
Source-approved smoke decks provide the device/corner scope; this adapter does
not infer topology, sizing, thresholds, or signoff requirements.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from _atomic_artefact import write_bytes, write_json, write_text
import _watchdog
from flow109 import Flow109Error, validate_execution_context
from flow109_admission import AdmissionRefusal, require_launch_admission
import flow109_analog_model_check as model_check
import pdk_analog_completeness_check as completeness

SCHEMA = 'vibeic.flow109.analog-model-qualification.v1'
INPUT = 'input/flow109/A02.json'
FREEZE = 'adopted/analog_model_qualification/model_smoke_freeze.json'
IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_.-]*\Z')
MEASURE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise AdmissionRefusal('A02_INPUT_INVALID', str(path)) from exc
    if not isinstance(value, dict):
        raise AdmissionRefusal('A02_INPUT_INVALID', str(path))
    return value


def _bound(project, value, code='A02_STALE_OR_MISSING_INPUT'):
    if not isinstance(value, dict) or set(value) != {'path', 'sha256'}:
        raise AdmissionRefusal('A02_UNBOUND_INPUT', str(value))
    if not isinstance(value['path'], str) or not isinstance(value['sha256'], str):
        raise AdmissionRefusal('A02_UNBOUND_INPUT', str(value))
    relative = Path(value['path'])
    if relative.is_absolute():
        raise AdmissionRefusal('A02_UNBOUND_INPUT', value['path'])
    raw = project / relative
    cursor = project
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise AdmissionRefusal(code, 'symlinked path component: ' + str(cursor))
    path = raw.resolve()
    if (not path.is_relative_to(project) or not path.is_file()
            or _sha(path) != value['sha256']):
        raise AdmissionRefusal(code, str(path))
    return path


def _record(project, path):
    path = Path(path).resolve()
    return {'path': str(path.relative_to(project)), 'sha256': _sha(path)}


def operation_sources():
    from execution_provider_catalog import source_closure
    paths = source_closure({Path(__file__), HERE / 'flow109_analog_model_check.py',
                            HERE / 'pdk_analog_completeness_check.py'})
    paths.update((HERE / 'flow109.py', HERE / 'flow109_admission.py'))
    return {str(path.relative_to(HERE.parent)): _sha(path) for path in sorted(paths)}


def _inventory(project, declared):
    has_analog, count = completeness._has_analog_blocks(project)
    if not has_analog:
        raise AdmissionRefusal('A02_ANALOG_BLOCKS_ABSENT', 'declared analog population required')
    axes = {name: completeness._axis_status(project, name) for name in completeness._AXES}
    missing = [name for name, (present, _) in axes.items() if not present]
    if missing:
        raise AdmissionRefusal('A02_PDK_INVENTORY_INCOMPLETE', ','.join(missing))
    if completeness._waiver_count(project):
        raise AdmissionRefusal('A02_PDK_WAIVER_UNSUPPORTED', 'model qualification cannot be waived')
    files = declared.get('inventory_files')
    if not isinstance(files, dict) or set(files) != set(completeness._AXES):
        raise AdmissionRefusal('A02_PDK_DECLARATION_INVALID', 'all inventory axes must be source-owned')
    for name, (_present, found) in axes.items():
        path = _bound(project, files[name])
        if str(path.relative_to(project)) != found:
            raise AdmissionRefusal('A02_PDK_INVENTORY_BINDING_MISMATCH', name)
    return {'checker': 'pdk_analog_completeness_check', 'analog_blocks': count,
            'waivers': 0, 'verdict': 'PASS',
            'axes': {name: {'present': present, 'path': path}
                     for name, (present, path) in axes.items()}}


def _deck_contract(smoke, model, deck):
    text = deck.read_text()
    model_text = model.read_text(errors='replace')
    if re.search(r'(?im)^\s*\.inc\S*', text + '\n' + model_text):
        raise AdmissionRefusal('A02_TRANSITIVE_MODEL_INPUT_UNIMPLEMENTED', smoke['id'])
    if any(len(line.split()) >= 3 for line in model_text.splitlines()
           if re.match(r'^\s*\.lib\b', line, re.I)):
        raise AdmissionRefusal('A02_TRANSITIVE_MODEL_INPUT_UNIMPLEMENTED', smoke['id'])
    loads = []
    for line in text.splitlines():
        words = line.split()
        if len(words) >= 3 and words[0].lower() == '.lib':
            loads.append((words[1], words[2]))
    if loads != [(smoke['model']['path'], smoke['corner'])]:
        raise AdmissionRefusal('A02_MODEL_CORNER_BINDING_MISMATCH', smoke['id'])
    starts = re.findall(r'(?im)^\s*\.lib\s+(\S+)\s*$', model_text)
    ends = re.findall(r'(?im)^\s*\.endl\s+(\S+)\s*$', model_text)
    if starts != ends or starts.count(smoke['corner']) != 1:
        raise AdmissionRefusal('A02_MODEL_CORNER_ABSENT', smoke['id'] + ':' + smoke['corner'])
    if re.search(r'(?im)^\s*\.(?:control|shell|source)\b', text):
        raise AdmissionRefusal('A02_DECK_SCOPE_UNIMPLEMENTED', smoke['id'])
    if not re.search(r'(?im)^\s*[mxqrc]\S*\s+.*\b' + re.escape(smoke['device']) + r'\b', text):
        raise AdmissionRefusal('A02_DEVICE_NOT_INSTANTIATED', smoke['id'])
    declared = set(re.findall(r'(?im)^\s*\.meas(?:ure)?\s+\w+\s+([A-Za-z_][A-Za-z0-9_]*)\b', text))
    if not set(smoke['required_measurements']) <= declared:
        raise AdmissionRefusal('A02_MEASUREMENT_NOT_DECLARED', smoke['id'])


def prepare(project, context):
    context = validate_execution_context(context)
    if context['requested_step'] != 'A02' or context['native_step_id'] != 'flow109.A02':
        raise Flow109Error('FLOW109_A02_WRONG_COMPONENT', str(context['requested_step']))
    require_launch_admission(project, 'flow109.A02', {'flow109_context': context})
    sources = operation_sources()
    uses = context['runtime_tool_uses']
    if (len(uses) != 1 or uses[0]['tool_id'] != 'ngspice'
            or uses[0]['semantics'].get('operation_sources') != sources):
        raise AdmissionRefusal('QUALIFICATION_OPERATION_SOURCE_MISMATCH',
                               'A02 must bind adapter, inventory gate and current consumer')
    manifest_path = project / INPUT
    manifest = _read(manifest_path)
    if manifest.get('schema') != SCHEMA or manifest.get('basis') != 'declared_model_device_smoke':
        raise AdmissionRefusal('A02_BASIS_UNSUPPORTED', str(manifest.get('basis')))
    pdk_path = _bound(project, manifest.get('pdk'))
    requirements_path = _bound(project, manifest.get('requirements'))
    requirements = _read(requirements_path)
    approval = requirements.get('approval', {})
    required = requirements.get('required_smokes')
    if (requirements.get('schema') != SCHEMA or approval.get('status') != 'APPROVED'
            or approval.get('authority') != 'requirements_owner'
            or not approval.get('source_reference') or not isinstance(required, list) or not required):
        raise AdmissionRefusal('A02_REQUIREMENTS_NOT_APPROVED', str(requirements_path))
    if (any(not isinstance(row, dict) for row in required)
            or len({row.get('id') for row in required}) != len(required)):
        raise AdmissionRefusal('A02_REQUIRED_SMOKE_INVALID', str(requirements_path))
    smokes = manifest.get('smokes')
    if not isinstance(smokes, list) or not smokes:
        raise AdmissionRefusal('A02_SMOKE_SET_ABSENT', INPUT)
    ids = [row.get('id') for row in smokes if isinstance(row, dict)]
    if (len(ids) != len(smokes) or len(set(ids)) != len(ids)
            or not all(isinstance(v, str) and IDENT.fullmatch(v) for v in ids)):
        raise AdmissionRefusal('A02_SMOKE_SET_INVALID', str(ids))
    pdk = _read(pdk_path)
    declared_models = pdk.get('model_files')
    if (pdk.get('schema') != SCHEMA or not isinstance(declared_models, list) or not declared_models
            or any(not isinstance(row, dict) for row in declared_models)):
        raise AdmissionRefusal('A02_PDK_DECLARATION_INVALID', str(pdk_path))
    if sorted(smokes, key=lambda x: x['id']) != sorted(required, key=lambda x: x.get('id', '')):
        raise AdmissionRefusal('A02_REQUIRED_SMOKE_MISMATCH', 'manifest must equal approved required set')
    inputs = {str(manifest_path.resolve()): _sha(manifest_path),
              str(pdk_path): _sha(pdk_path), str(requirements_path): _sha(requirements_path)}
    resolved = []
    for smoke in smokes:
        if (not IDENT.fullmatch(str(smoke.get('device', '')))
                or not IDENT.fullmatch(str(smoke.get('corner', '')))
                or not isinstance(smoke.get('required_measurements'), list)
                or not smoke['required_measurements']
                or not all(isinstance(v, str) and MEASURE.fullmatch(v)
                           for v in smoke['required_measurements'])):
            raise AdmissionRefusal('A02_SMOKE_DECLARATION_INVALID', str(smoke.get('id')))
        model = _bound(project, smoke.get('model'))
        deck = _bound(project, smoke.get('deck'))
        if smoke['model'] not in declared_models:
            raise AdmissionRefusal('A02_MODEL_NOT_IN_DECLARED_PDK', smoke['id'])
        _deck_contract(smoke, model, deck)
        inputs[str(model)] = _sha(model)
        inputs[str(deck)] = _sha(deck)
        resolved.append((smoke, model, deck))
    inventory = _inventory(project, pdk)
    for record in pdk['inventory_files'].values():
        path = _bound(project, record)
        inputs[str(path)] = _sha(path)
    identity = uses[0]
    if (identity['config'] != _record(project, manifest_path)
            or identity['pdk'] != manifest['pdk']
            or identity['deck'] != manifest['requirements']
            or identity['corner'] != sorted({row['corner'] for row in smokes})):
        raise AdmissionRefusal('A02_RUNTIME_SCOPE_MISMATCH', uses[0]['use_id'])
    return {'requirements': requirements, 'resolved': resolved, 'inputs': inputs,
            'sources': sources, 'binary': identity['executable']['path'],
            'inventory': inventory}


def _before(project, context, prepared):
    require_launch_admission(project, 'flow109.A02', {'flow109_context': context})
    if operation_sources() != prepared['sources']:
        raise AdmissionRefusal('A02_OPERATION_SOURCE_CHANGED', 'before native launch')
    for path, digest in prepared['inputs'].items():
        if not Path(path).is_file() or _sha(path) != digest:
            raise AdmissionRefusal('A02_STALE_OR_MISSING_INPUT', path)


def execute(project, context):
    project = Path(project).resolve()
    require_launch_admission(project, 'flow109.A02', {'flow109_context': context})
    (project / FREEZE).unlink(missing_ok=True)
    prepared = prepare(project, context)
    parent = project / 'reports/flow109/attempts'
    parent.mkdir(parents=True, exist_ok=True)
    attempt = Path(tempfile.mkdtemp(prefix='A02-', dir=parent))
    report = {'schema': SCHEMA, 'step_id': 'A02', 'native_step_id': 'flow109.A02',
              'component_status': 'NOT_MEASURED', 'flow109_acceptance': 'NOT_MEASURED',
              'context_sha256': context['context_sha256'], 'native_started': False,
              'inventory': prepared['inventory'], 'operation_sources': prepared['sources'],
              'inputs': prepared['inputs'], 'smokes': [],
              'unimplemented_obligations': [o['id'] for o in context['resolved_row']['obligations']],
              'qualification_scope': 'developer/component only; PDK and benchmark signoff NOT_MEASURED'}
    for smoke, model, deck in prepared['resolved']:
        _before(project, context, prepared)
        model_bytes = model.read_bytes()
        deck_text = deck.read_text()
        if (hashlib.sha256(model_bytes).hexdigest() != smoke['model']['sha256']
                or hashlib.sha256(deck_text.encode()).hexdigest() != smoke['deck']['sha256']):
            raise AdmissionRefusal('A02_STALE_OR_MISSING_INPUT', smoke['id'])
        # The engine consumes immutable attempt-local bytes, not a lexical
        # path that could be exchanged after the admission check.
        run_model = attempt / (smoke['id'] + '.model.lib')
        run_deck = attempt / (smoke['id'] + '.sp')
        write_bytes(run_model, model_bytes)
        old = '.lib ' + smoke['model']['path'] + ' ' + smoke['corner']
        new = '.lib ' + str(run_model) + ' ' + smoke['corner']
        if deck_text.count(old) != 1:
            raise AdmissionRefusal('A02_MODEL_CORNER_BINDING_MISMATCH', smoke['id'])
        write_text(run_deck, deck_text.replace(old, new))
        log = attempt / (smoke['id'] + '.ngspice.log')
        argv = [prepared['binary'], '-b', str(run_deck)]
        supervised = _watchdog.run_host_supervised(
            argv, cwd=str(project), stall_grace_s=60, hard_ceiling_s=120)
        completed = _watchdog.completed_process(argv, supervised)
        report['native_started'] = True
        log.write_text(completed.stdout + completed.stderr)
        report['smokes'].append({**smoke, 'argv': argv, 'returncode': completed.returncode,
                                 'log': _record(project, log),
                                 'executed_model': _record(project, run_model),
                                 'executed_deck': _record(project, run_deck),
                                 'supervision': supervised.supervision,
                                 'outcome': supervised.outcome})
        if supervised.outcome == 'stalled' or completed.returncode == _watchdog.RC_STALLED:
            break
    _before(project, context, prepared)
    if any(row.get('outcome') == 'stalled' for row in report['smokes']):
        consumed = {'verdict': 'NOT_MEASURED', 'reason': 'A02_NATIVE_STALLED',
                    'smokes': [], 'scope': 'native model smoke made no progress'}
    else:
        consumed = model_check.consume(project, prepared['requirements'], report)
    report['consumer'] = consumed
    report['component_status'] = consumed['verdict']
    report['reason'] = consumed['reason']
    if consumed['verdict'] == 'PASS':
        freeze = {'schema': SCHEMA, 'status': 'PASS', 'scope': consumed['scope'],
                  'context_sha256': context['context_sha256'], 'inventory': prepared['inventory'],
                  'smokes': consumed['smokes'], 'inputs': prepared['inputs']}
        write_json(project / FREEZE, freeze)
        report['freeze'] = _record(project, project / FREEZE)
    write_json(attempt / 'component.json', report)
    return report
