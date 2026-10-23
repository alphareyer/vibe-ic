"""D64 new-geometry component, never adoption of historical stream/PV PASS.

A separately qualified Python/pya stream producer consumes the genuine D62
receipt; native FinishingXOR judges the resulting geometry. Hard-IP/no-seal
is the first implemented scope. IC seal re-entry requires frozen D61 authority
and is not bypassed by a caller's no-seal flag. Full-row adoption remains open.
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import math
from pathlib import Path
import sys
import uuid

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from _atomic_artefact import write_json
from flow109_admission import AdmissionRefusal, require_launch_admission
import flow109_density_fill as density
import librelane_fill_stream as stream

SCHEMA = 'vibeic.flow109.final-geometry.v1'
INPUT = 'input/flow109/D64.json'


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def operation_sources():
    from execution_provider_catalog import source_closure
    import pya  # actual native stream API, not an unrelated klayout binary
    import klayout.db
    import klayout.rdb
    paths = source_closure({HERE / 'librelane_fill_stream.py', HERE / 'gds_xor_check.py'})
    paths.update([Path(__file__), HERE / 'flow109.py', HERE / 'flow109_admission.py',
                  HERE / 'flow109_density_fill.py', HERE / 'flow109_density_fill.tcl',
                  HERE / 'librelane_fill_stream_native.py'])
    paths.update((HERE / 'librelane_plugins/librelane_plugin_vibeic').glob('*.py'))
    paths.update((HERE / 'librelane_plugins/librelane_plugin_vibeic').glob('*.drc'))
    from librelane.steps.klayout import StreamOut
    paths.add(Path(inspect.getsourcefile(StreamOut)))
    result = {str(p.relative_to(HERE.parent)) if p.is_relative_to(HERE.parent) else str(p): _sha(p)
              for p in sorted(paths)}
    # pya is a Python facade over shared native KLayout modules. Bind what
    # this interpreter actually loaded, including the extension libraries.
    for name, module in sorted(sys.modules.items()):
        if name == 'pya' or name.startswith(('pya.', 'klayout.')):
            filename = getattr(module, '__file__', None)
            if filename and Path(filename).is_file():
                result['native-api/' + name] = _sha(filename)
    result['native/librelane/stream_out.py'] = _sha(stream.native_script())
    return result


def _read(path):
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise AdmissionRefusal('D64_REQUIRED_INPUT_ABSENT', str(path)) from exc
    if not isinstance(value, dict):
        raise AdmissionRefusal('D64_REQUIRED_INPUT_INVALID', str(path))
    return value


def prepare(project, context):
    if context.get('requested_step') != 'D64' or context.get('native_step_id') != 'flow109.D64':
        raise AdmissionRefusal('D64_CONTEXT_MISMATCH', 'final geometry adapter only')
    source = operation_sources()
    for use in context['runtime_tool_uses']:
        if use.get('semantics', {}).get('operation_sources') != source:
            raise AdmissionRefusal('D64_OPERATION_SOURCE_CHANGED', use['use_id'])
    manifest = _read(project / INPUT)
    if manifest.get('schema') != SCHEMA:
        raise AdmissionRefusal('D64_REQUIRED_INPUT_INVALID', 'schema')
    ref = manifest.get('d62_receipt')
    d62 = density.validate_filled_odb(project, ref)
    native = manifest.get('native_stream_inputs')
    if not isinstance(native, dict):
        raise AdmissionRefusal('D64_STREAM_SOURCE_UNBOUND', 'typed native materials required')
    # Never mistake an already sealed full-core GDS for a seal-only view.
    if context['features']['ic']:
        raise AdmissionRefusal('D64_D61_SEAL_REENTRY_NOT_IMPLEMENTED',
            'IC requires frozen D61 provider/deck/dimensions/die-ID authority and fresh bounded re-entry; no-seal flags cannot waive it')
    if native.get('seal_gds') is not None or manifest.get('seal_policy') != 'hard_ip_no_seal':
        raise AdmissionRefusal('D64_SEAL_SCOPE_UNIMPLEMENTED', 'only declared hard-IP without a seal is implemented')
    if not context['features']['hard_ip']:
        raise AdmissionRefusal('D64_DELIVERY_SCOPE_UNIMPLEMENTED', 'digital hard-IP first tranche only')
    views = manifest.get('adopted_views')
    if not isinstance(views, dict) or set(views) != {'netlist', 'sdc'}:
        raise AdmissionRefusal('D64_ADOPTED_VIEWS_ABSENT', 'netlist and SDC sources must remain explicit')
    paths = {name: stream._bound(project, value) for name, value in views.items()}
    paths['d62_receipt'] = stream._bound(project, ref)
    paths['manifest'] = project / INPUT
    for key, value in native.items():
        for index, item in enumerate(value if isinstance(value, list) else [value]):
            if item is not None:
                paths[f'native.{key}.{index}'] = stream._bound(project, item)
    core = manifest.get('core_area_um')
    if (not isinstance(core, list) or len(core) != 4
            or any(type(n) not in (int, float) or not math.isfinite(n) for n in core)
            or core[0] >= core[2] or core[1] >= core[3]):
        raise AdmissionRefusal('D64_CORE_UNBOUND', 'source-declared core rectangle required')
    config_ref = manifest.get('operation_config')
    config_path = stream._bound(project, config_ref)
    config = _read(config_path)
    if config != {'schema': SCHEMA, 'basis': 'D62_native_odb_and_def',
                  'seal_policy': 'hard_ip_no_seal', 'core_area_um': core}:
        raise AdmissionRefusal('D64_OPERATION_CONFIG_UNSUPPORTED', 'exact frozen operation required')
    model_ref = manifest.get('model_bundle')
    model = _read(stream._bound(project, model_ref))
    if model != {'native_stream_inputs': native, 'adopted_views': views}:
        raise AdmissionRefusal('D64_MODEL_SOURCE_MISMATCH', 'native materials and retained views differ')
    for use in context['runtime_tool_uses']:
        if use.get('config') != config_ref or use.get('deck') != model_ref or use.get('pdk') != model_ref:
            raise AdmissionRefusal('D64_QUALIFICATION_SCOPE_MISMATCH', use['use_id'])
    native_config = manifest.get('librelane')
    if (not isinstance(native_config, dict) or set(native_config) != {'PDK', 'STD_CELL_LIBRARY', 'pdk_root'}
            or not Path(native_config['pdk_root']).is_dir()):
        raise AdmissionRefusal('D64_NATIVE_CONFIGURATION_ABSENT', 'explicit current LibreLane configuration required')
    paths['operation_config'], paths['model_bundle'] = config_path, stream._bound(project, model_ref)
    return {'manifest': manifest, 'd62': d62, 'sources': source,
            'inputs': {str(p): _sha(p) for p in paths.values()}}


def _before(project, context, prepared):
    require_launch_admission(project, 'flow109.D64', {'flow109_context': context})
    if operation_sources() != prepared['sources']:
        raise AdmissionRefusal('D64_OPERATION_SOURCE_CHANGED', 'before actual native launch')
    for path, sha in prepared['inputs'].items():
        if not Path(path).is_file() or _sha(path) != sha:
            raise AdmissionRefusal('D64_INPUT_CHANGED', path)
    density.validate_filled_odb(project, prepared['manifest']['d62_receipt'])


def _finishing_xor(project, context, prepared, bridge, attempt):
    from librelane.config import Config
    from librelane.state import State, DesignFormat
    from librelane.common import Path as LLPath
    sys.path.insert(0, str(HERE / 'librelane_plugins'))
    from librelane_plugin_vibeic.signoff import FinishingXOR
    native = prepared['manifest']['librelane']
    stream_doc = _read(stream._bound(project, bridge))
    before = stream._bound(project, stream_doc['outputs']['bare.gds'])
    final = stream._bound(project, stream_doc['outputs']['filled.gds'])
    top = stream.fill_records(stream._bound(project, stream_doc['filled_def']))['top']
    config, _ = Config.load({'DESIGN_NAME': top, 'PDK': native['PDK'],
        'STD_CELL_LIBRARY': native['STD_CELL_LIBRARY'],
        'VIBEIC_FINISHING_PRE_GDS': str(before),
        'VIBEIC_FINISHING_CORE_AREA': ','.join(map(str, prepared['manifest']['core_area_um'])),
        'VIBEIC_FINISHING_XOR_THREADS': 2}, FinishingXOR.get_all_config_variables(),
        design_dir=str(project), pdk_root=native['pdk_root'])
    step = FinishingXOR(config=config, state_in=State({DesignFormat.GDS: LLPath(str(final))}))
    _before(project, context, prepared)
    state = step.start(step_dir=str(attempt / 'finishing-xor'))
    value = state.metrics.get('vibeic__finishing_xor__defect__count')
    if type(value) is not int or value != 0:
        raise AdmissionRefusal('D64_FINISHING_XOR_NOT_CLEAN', str(value), status='FAIL')
    pair_count = state.metrics.get('vibeic__finishing_xor__design_pair__count')
    if type(pair_count) is not int or pair_count <= 0:
        raise AdmissionRefusal('D64_FINISHING_XOR_VACUOUS', str(pair_count))
    return {'defect_count': value, 'design_pair_count': pair_count,
            'state': stream._record(project, attempt / 'finishing-xor/state_out.json'),
            'config': stream._record(project, attempt / 'finishing-xor/config.json'),
            'report': stream._record(project, attempt / 'finishing-xor/finishing_xor.xml'),
            'scope': 'native finishing geometry; exact allowed fill geometry checked separately'}


def execute(project, context):
    project = Path(project).resolve()
    attempt = project / 'reports/flow109/D64' / uuid.uuid4().hex
    attempt.mkdir(parents=True, exist_ok=False)
    result = {'schema': SCHEMA, 'step_id': 'D64', 'component_status': 'NOT_MEASURED',
              'flow109_acceptance': 'NOT_MEASURED', 'native_started': False,
              'final_geometry_adoption': 'NOT_MEASURED', 'context_sha256': context.get('context_sha256')}
    try:
        require_launch_admission(project, 'flow109.D64', {'flow109_context': context})
        prepared = prepare(project, context)
        def before():
            _before(project, context, prepared)
            result['native_started'] = True
        bridge = stream.stream_d62(project, prepared['manifest']['d62_receipt'],
            prepared['manifest']['native_stream_inputs'], attempt / 'native-stream', before_launch=before)
        result['stream_bridge'] = bridge
        result['finishing_xor'] = _finishing_xor(project, context, prepared, bridge, attempt)
        _before(project, context, prepared)
        common = stream.validate_bridge(project, bridge)
        result.update(component_status='PASS', reason='D64_CURRENT_D62_GEOMETRY_COMPONENT_MEASURED',
            inputs=prepared['inputs'], operation_sources=prepared['sources'],
            gds=common['gds'], odb=common['odb'], d62_receipt=common['d62_receipt'],
            adopted_views=prepared['manifest']['adopted_views'], geometry_proof=common['geometry_proof'])
    except AdmissionRefusal as exc:
        result.update(component_status=exc.status, reason=exc.code, detail=str(exc))
    except Exception as exc:
        result.update(component_status='NOT_MEASURED', reason='D64_NATIVE_UNAVAILABLE_OR_UNSUPPORTED', detail=str(exc))
    path = attempt / 'component.json'
    write_json(path, result)
    result['receipt'] = stream._record(project, path)
    if result['component_status'] == 'PASS':
        try:
            result['handoff'] = validate_final_geometry(project, result['receipt'])
        except Exception as exc:
            result.pop('receipt', None)
            result.update(component_status='NOT_MEASURED', reason='D64_CURRENT_CONSUMER_REFUSED', detail=str(exc))
            write_json(path, result)
            result['receipt'] = stream._record(project, path)
    return result


def validate_final_geometry(project, receipt_ref):
    """Read this producer's current evidence; never accept a legacy PASS alias."""
    project = Path(project).resolve()
    record = _read(stream._bound(project, receipt_ref))
    if record.get('schema') != SCHEMA or record.get('component_status') != 'PASS':
        raise AdmissionRefusal('D64_COMPONENT_NOT_ACCEPTED', 'current native geometry receipt required')
    if record.get('operation_sources') != operation_sources():
        raise AdmissionRefusal('D64_OPERATION_SOURCE_CHANGED', 'current field mapping/native sources required')
    for path, sha in record['inputs'].items():
        if not Path(path).is_file() or _sha(path) != sha:
            raise AdmissionRefusal('D64_INPUT_CHANGED', path)
    common = stream.validate_bridge(project, record['stream_bridge'])
    if any(record[key] != common[key] for key in ('gds', 'odb', 'd62_receipt', 'geometry_proof')):
        raise AdmissionRefusal('D64_COMMON_GEOMETRY_MISMATCH', 'ODB/GDS/fill producer must share one geometry proof')
    xor = record['finishing_xor']
    state = _read(stream._bound(project, xor['state']))
    config = _read(stream._bound(project, xor['config']))
    report = stream._bound(project, xor['report'])
    if (state.get('gds') != str(stream._bound(project, common['gds']))
            or config.get('VIBEIC_FINISHING_PRE_GDS') !=
            str(stream._bound(project, _read(stream._bound(project, record['stream_bridge']))['outputs']['bare.gds']))
            or state.get('metrics', {}).get('vibeic__finishing_xor__defect__count') != 0
            or state.get('metrics', {}).get('vibeic__finishing_xor__design_pair__count') != xor['design_pair_count']
            or not xor['design_pair_count'] > 0):
        raise AdmissionRefusal('D64_FINISHING_PROOF_CHANGED', 'native state/config/report source differs')
    import klayout.rdb as rdb
    database = rdb.ReportDatabase(); database.load(str(report))
    if database.num_items() != 0:
        raise AdmissionRefusal('D64_FINISHING_XOR_NOT_CLEAN', 'actual native report contains differences', status='FAIL')
    return {'gds': common['gds'], 'odb': common['odb'], 'd62_receipt': common['d62_receipt'],
            'geometry_receipt': receipt_ref, 'adopted_views': record['adopted_views'],
            'component_geometry_current': True, 'final_geometry_adoption': 'NOT_MEASURED',
            'flow109_acceptance': 'NOT_MEASURED'}
