"""D22 uses the existing Default synthesis owner and its complete consumer.

The other frontend rows are deliberately not translated into an old shared
span. Native proof/receipts are component evidence; 109 metric acceptance is
NOT_MEASURED. The external qualification registry must be pre-reviewed.
"""
from __future__ import annotations
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
import uuid
from contextvars import ContextVar

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from _atomic_artefact import write_json
from flow109_admission import AdmissionRefusal, require_launch_admission

INPUT = 'input/flow109/D22.json'
SCHEMA = 'vibeic.flow109.frontend-synthesis.v1'
_ACTIVE = ContextVar('flow109_synthesis_launch', default=None)
STEPS = {'Yosys.JsonHeader', 'Yosys.Synthesis', 'Checker.YosysUnmappedCells',
         'Checker.YosysSynthChecks', 'Checker.NetlistAssignStatements'}


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def operation_sources():
    from execution_provider_catalog import source_closure
    from librelane.steps import pyosys
    from librelane.common import get_script_dir
    import pyosys.libyosys as native_yosys
    seeds = {HERE / 'design_one_shot_runner.py', HERE / 'step_preflight.py',
             HERE / 'execution_backend_gates.py', HERE / 'librelane_contract.py'}
    paths = source_closure(seeds)
    paths.update((Path(__file__), HERE / 'flow109.py', HERE / 'flow109_admission.py'))
    result = {str(p.relative_to(HERE.parent)): _sha(p) for p in sorted(paths)}
    native = Path(inspect.getsourcefile(pyosys))
    result['native/librelane/steps/pyosys.py'] = _sha(native)
    result['native/pyosys/libyosys.so'] = _sha(native_yosys.__file__)
    scripts = Path(get_script_dir())
    for path in sorted((scripts / 'pyosys').rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts:
            result['native/librelane/scripts/' + str(path.relative_to(scripts))] = _sha(path)
    return result


def _read(path):
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict):
        raise AdmissionRefusal('D22_INPUT_INVALID', str(path))
    return value


def input_census(project):
    """Bind existing design input roots, not newly produced synthesis outputs."""
    project = Path(project)
    roots = ('input', 'input_doc', 'phase1/generated_docs', 'phase2/stage1/rtl',
             'phase2/stage2/constraints')
    paths = {p for root in roots for p in (project / root).rglob('*') if p.is_file()}
    switch = project / 'phase3/librelane_switch.json'
    if switch.is_file():
        paths.add(switch)
    return {str(p.relative_to(project)): _sha(p) for p in sorted(paths)}


def _before(project, context, prepared):
    require_launch_admission(project, '9', {'flow109_context': context})
    if operation_sources() != prepared['sources']:
        raise AdmissionRefusal('D22_OPERATION_SOURCE_CHANGED', 'source changed before synthesis launch')
    if input_census(project) != prepared['inputs']:
        raise AdmissionRefusal('D22_INPUT_CHANGED', 'consumed source population changed')


# The current request is visible at the contract's real config/native launch
# boundaries too. No request means ordinary old-flow behavior is unchanged.
def require_librelane_launch(project, operation, *, configuration=False):
    context_path = os.environ.get('VIBEIC_FLOW109_CONTEXT')
    if not context_path:
        return
    context = _read(context_path)
    if context.get('requested_step') != 'D22':
        return
    if operation not in STEPS:
        raise AdmissionRefusal('D22_NATIVE_OPERATION_UNQUALIFIED', str(operation))
    active = _ACTIVE.get()
    if active is None or active['project'] != Path(project).resolve() or active['context'] != context:
        raise AdmissionRefusal('D22_LAUNCH_CONTEXT_UNBOUND', str(operation))
    _before(Path(project), context, active['prepared'])
    if not configuration:
        active['operations'].append(operation)


def prepare(project, context):
    require_launch_admission(project, '9', {'flow109_context': context})
    if context.get('requested_step') != 'D22':
        raise AdmissionRefusal('D22_CONTEXT_MISMATCH', 'synthesis component only')
    sources = operation_sources()
    for use in context['runtime_tool_uses']:
        if use.get('semantics', {}).get('operation_sources') != sources:
            raise AdmissionRefusal('D22_OPERATION_SOURCE_CHANGED', use['use_id'])
    manifest = _read(project / INPUT)
    import re
    if (manifest.get('schema') != SCHEMA
            or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$]*', str(manifest.get('top', '')))
            or not isinstance(manifest.get('pdk'), str) or not manifest['pdk']):
        raise AdmissionRefusal('D22_INPUT_INVALID', 'explicit top and PDK required')
    if not (project / 'input/submission_template/tapeout_declaration.json').is_file():
        raise AdmissionRefusal('D22_REQUIRED_INPUT_ABSENT', 'canonical submission declaration required')
    import librelane_contract as contract
    if contract.selected_mode(project, '9') != 'librelane':
        raise AdmissionRefusal('D22_ROUTE_UNSUPPORTED', 'only the existing native LibreLane Default owner is selected')
    import design_one_shot_runner as runner
    pdk = runner._phase2_pdk_config(project, manifest['pdk'])
    liberty = Path(pdk.liberty).resolve()
    for use in context['runtime_tool_uses']:
        deck = use.get('deck') or {}
        if (not deck.get('path') or (project / deck['path']).resolve() != liberty
                or deck.get('sha256') != _sha(liberty)):
            raise AdmissionRefusal('D22_LIBRARY_NOT_QUALIFIED', use['use_id'])
        config = _read(project / use['config']['path'])
        if config != {'schema': SCHEMA, 'operation': 'Default.NativeMappedSynthesis',
                      'pdk': manifest['pdk'], 'top': manifest['top']}:
            raise AdmissionRefusal('D22_CONFIGURATION_UNSUPPORTED', use['use_id'])
    inputs = input_census(project)
    if not any(p.startswith('phase2/stage1/rtl/') for p in inputs):
        raise AdmissionRefusal('D22_RTL_ABSENT', 'no bound RTL population')
    return {'manifest': manifest, 'pdk': pdk, 'sources': sources, 'inputs': inputs}


def execute(project, context):
    project = Path(project).resolve()
    attempt = project / 'reports/flow109/D22' / uuid.uuid4().hex
    attempt.mkdir(parents=True, exist_ok=False)
    record = {'schema': SCHEMA, 'step_id': 'D22', 'component_status': 'NOT_MEASURED',
              'flow109_acceptance': 'NOT_MEASURED', 'native_started': False,
              'context_sha256': context.get('context_sha256')}
    try:
        prepared = prepare(project, context)
        _before(project, context, prepared)
        import design_one_shot_runner as runner
        import step_preflight
        from dataclasses import asdict
        import step_output_collector as versions
        # Exactly the existing owner and the same retained complete consumer.
        # No frontend Controller/Ultra arm, copied Yosys recipe, or new checker.
        active = {'project': project, 'context': context, 'prepared': prepared, 'operations': []}
        token = _ACTIVE.set(active)
        try:
            result = step_preflight.retained_call(project, '9', runner.step_yosys_synth,
                project, prepared['manifest']['top'], '', None,
                pdk_name=prepared['manifest']['pdk'], canonical_acceptance=True)
        finally:
            _ACTIVE.reset(token)
        record['native_operations'] = active['operations']
        record['native_started'] = bool(active['operations'])
        record['producer'] = asdict(result)
        record['canonical_current_pass'] = versions.latest_pass(project, '9')
        record['source_sha256'] = prepared['sources']
        record['inputs'] = prepared['inputs']
        _before(project, context, prepared)
        # Native execution is established by the existing receipt, not by
        # merely entering a function which can refuse before tool launch.
        folder = project / 'phase3/librelane/02-yosys-synthesis'
        receipt = folder / 'vibeic_receipt.json'
        if 'Yosys.Synthesis' in active['operations'] and receipt.is_file():
            record['native_receipt'] = {'path': str(receipt.relative_to(project)), 'sha256': _sha(receipt)}
        if result.status == 'FAIL':
            record.update(component_status='FAIL', reason='D22_EXISTING_PRODUCER_FAILED')
        elif result.status == 'PASS' and record['canonical_current_pass']:
            record.update(component_status='PASS', reason='D22_EXISTING_CANONICAL_CONSUMER_PASSED')
        else:
            record['reason'] = 'D22_CANONICAL_ACCEPTANCE_NOT_MEASURED'
    except AdmissionRefusal as exc:
        record.update(component_status=exc.status, reason=exc.code, detail=str(exc))
    except (OSError, ValueError, ImportError) as exc:
        record.update(reason='D22_NATIVE_UNAVAILABLE_OR_UNSUPPORTED', detail=str(exc))
    write_json(attempt / 'component.json', record)
    return record
