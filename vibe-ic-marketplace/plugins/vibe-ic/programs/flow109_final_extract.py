"""D69 native owner: final GDS/DEF -> released floating-fill Magic SPEF tool."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import uuid
import shutil
import subprocess
import time

import sys

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from _atomic_artefact import write_json
from flow109_admission import AdmissionRefusal, require_launch_admission
import flow109_density_fill as density
import librelane_fill_stream as files
from librelane_contract import Refusal
from spef_extraction_check import scan_spef

SCHEMA = 'vibeic.flow109.final-extract.v1'
INPUT = 'input/flow109/D69.json'


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise AdmissionRefusal('D69_REQUIRED_INPUT_ABSENT', str(path)) from exc
    if not isinstance(value, dict):
        raise AdmissionRefusal('D69_REQUIRED_INPUT_INVALID', str(path))
    return value


def _bound(project, ref):
    return files._bound(Path(project), ref)


def _retained_geometry(project, manifest):
    """Reopen the existing Default native owner; never translate its verdict."""
    import librelane_fill_dfm as retained
    import _physical_current as current
    ref = manifest.get('retained_default_fill')
    report = _bound(project, ref)
    if report.parts[-3:] != ('reports', 'phase3', 'fill_librelane.json'):
        raise AdmissionRefusal('D69_RETAINED_OWNER_INVALID', str(report))
    owner = report.parents[2]
    stream = retained.validate_default_stream(owner)
    document = _read(report)
    tool = document['odb']['tool']
    state_path = Path(tool['state'])
    from librelane_contract import digest, validate_step_receipt
    validate_step_receipt(state_path.parent, retained.ODB_FILL_STEP)
    if digest(state_path) != tool['state_sha256']:
        raise AdmissionRefusal('D69_RETAINED_STATE_CHANGED', str(state_path))
    state = _read(state_path)
    if not Path(state['odb']).resolve().is_relative_to(state_path.parent.resolve()):
        raise AdmissionRefusal('D69_RETAINED_ODB_UNBOUND', str(state['odb']))
    if digest(Path(state['def'])) != tool['filled_def_sha256']:
        raise AdmissionRefusal('D69_RETAINED_DEF_CHANGED', str(state['def']))
    actual = current.default_signoff_inputs(owner)
    views = manifest.get('adopted_views') or {}
    if not actual or set(views) != {'netlist', 'sdc'}:
        raise AdmissionRefusal('D69_ADOPTED_VIEWS_ABSENT', 'current netlist and SDC required')
    expected_sdc = Path(actual.get('sdc') or owner / 'phase3/stage3/pnr/constraint.sdc')
    for key, expected in [('netlist', Path(actual['netlist'])), ('sdc', expected_sdc)]:
        if _bound(project, views[key]).resolve() != expected.resolve():
            raise AdmissionRefusal('D69_RETAINED_ADOPTED_VIEW_MISMATCH', key)
    if Path(actual['gds']).resolve() != Path(stream['canonical']).resolve():
        raise AdmissionRefusal('D69_RETAINED_GDS_MISMATCH', str(actual['gds']))
    if manifest.get('librelane', {}).get('image', stream['image']) != stream['image']:
        raise AdmissionRefusal('D69_RETAINED_IMAGE_MISMATCH', 'native image differs from stream owner')
    # Native cell-fill/stream evidence is not a dbFill census or fill-sensitive RC proof.
    geometry = {'odb': files._record(project, Path(state['odb'])),
                'filled_def': files._record(project, Path(state['def'])),
                'retained_default_fill': ref,
                'native_fill_state': files._record(project, state_path),
                'native_fill_receipt': files._record(project, state_path.parent / 'vibeic_receipt.json'),
                'final_gds': files._record(project, Path(actual['gds']))}
    return geometry


def _native_refusal_status(code):
    return 'FAIL' if code == 'LL_STEP_FAILED' else 'NOT_MEASURED'


def _fill_extraction_scope(d62):
    """Current-geometry correlation is separate from tool availability."""
    measurements = d62.get('measurements') or {}
    count = measurements.get('fill_count_after')
    return {'status': 'NOT_MEASURED', 'reason': 'D69_CURRENT_FINAL_FILL_CORRELATION_NOT_MEASURED',
            'native_fill_count': count if type(count) is int else None,
            'basis': 'source-owned D62 census; current final-GDS correlation remains separate'}


def _component_scope(record, d62):
    fill_scope = _fill_extraction_scope(d62)
    if (record.get('reason') != 'D69_NATIVE_FINAL_FILL_SPEF_MEASURED'
            or record.get('component_scope') != 'native_final_gds_floating_fill_component'
            or record.get('fill_sensitive_parasitics') != fill_scope):
        raise AdmissionRefusal('D69_COMPONENT_SCOPE_CHANGED',
                               'native component cannot invent current-geometry correlation')
    return fill_scope


def operation_sources():
    paths = (Path(__file__), Path(files.__file__),
             Path(density.__file__), Path(__file__).with_name('spef_extraction_check.py'),
             Path(__file__).with_name('librelane_fill_dfm.py'),
             Path(__file__).with_name('_physical_current.py'))
    return {str(path.resolve()): _sha(path) for path in paths}


def _native_outputs(project, receipt_ref, gds_ref, def_ref):
    """Consume the released tool's own receipt, not a replacement extractor."""
    native = _read(_bound(project, receipt_ref))
    if (native.get('status') != 'PASS'
            or native.get('geometry') != {'path': str(_bound(project, gds_ref)), 'sha256': gds_ref['sha256']}
            or native.get('routed_def') != {'path': str(_bound(project, def_ref)), 'sha256': def_ref['sha256']}
            or native.get('top') != files.fill_records(_bound(project, def_ref))['top']
            or native.get('declared_corner_set') != ['min', 'nom', 'max']
            or set(native.get('corners') or {}) != {'min', 'nom', 'max'}
            or native.get('floating_boundary_condition') != 'zero-net-charge'
            or native.get('grounded_fill_approximation') is not False
            or type(native.get('keep_net_count')) is not int or native['keep_net_count'] <= 0):
        raise AdmissionRefusal('D69_NATIVE_RECEIPT_MISMATCH', str(receipt_ref))
    _bound(project, native.get('fill_aware_tech'))
    spefs, scans = {}, {}
    for corner, reduction in native['corners'].items():
        if (reduction.get('status') != 'PASS'
                or reduction.get('method') != 'exact-capacitance-matrix-schur-complement'
                or reduction.get('floating_boundary_condition') != 'zero-net-charge'
                or reduction.get('grounded_fill_approximation') is not False
                or reduction.get('retained_net_count') != native['keep_net_count']
                or reduction.get('keep_nets_sha256') != native.get('keep_nets_sha256')):
            raise AdmissionRefusal('D69_NATIVE_CORNER_MISMATCH', corner)
        for key in ('raw_spef', 'keep_nets', 'output_spef'):
            _bound(project, {'path': reduction.get(key), 'sha256': reduction.get(key + '_sha256')})
        path = Path(reduction['output_spef']); facts = scan_spef(path)
        if (not facts['has_header'] or not facts['has_design'] or not (facts['d_nets'] or facts['r_nets'])
                or facts['negative_caps'] or facts['negative_net_totals']):
            raise AdmissionRefusal('D69_SPEF_CONSUMER_FAILED', corner, status='FAIL')
        spefs[corner], scans[corner] = files._record(project, path), facts
    return spefs, scans


def execute(project, context):
    project = Path(project).resolve()
    attempt = project / 'reports/flow109/D69' / uuid.uuid4().hex
    attempt.mkdir(parents=True, exist_ok=False)
    result = {'schema': SCHEMA, 'step_id': 'D69', 'component_status': 'NOT_MEASURED',
              'flow109_acceptance': 'NOT_MEASURED', 'native_started': False,
              'unmeasured_obligations': ['final_extractor_qualification',
                  'fill_sensitive_reference_correlation', 'final_parasitic_corner_coverage'],
              'context_sha256': context.get('context_sha256')}
    try:
        require_launch_admission(project, 'flow109.D69', {'flow109_context': context})
        manifest = _read(project / INPUT)
        if manifest.get('schema') != SCHEMA:
            raise AdmissionRefusal('D69_REQUIRED_INPUT_INVALID', 'schema')
        retained_geometry = None
        if manifest.get('retained_default_fill') is not None:
            if manifest.get('d62_receipt') is not None or manifest.get('stream_bridge') is not None:
                raise AdmissionRefusal('D69_GEOMETRY_AUTHORITY_AMBIGUOUS', 'one native owner required')
            retained_geometry = _retained_geometry(project, manifest)
            d62 = {}
            bridge = {'odb': retained_geometry['odb']}
            odb = _bound(project, bridge['odb'])
            d62_path = _bound(project, manifest['retained_default_fill'])
            routed_def = _bound(project, retained_geometry['filled_def'])
            final_gds = _bound(project, retained_geometry['final_gds'])
        else:
            d62 = density.validate_filled_odb(project, manifest.get('d62_receipt'))
            bridge = files.validate_bridge(project, manifest.get('stream_bridge'))
            if bridge['d62_receipt'] != manifest['d62_receipt'] or bridge['odb'] != d62['odb']:
                raise AdmissionRefusal('D69_FINAL_GEOMETRY_MISMATCH', 'stream-out and D62 ODB differ')
            odb, d62_path = _bound(project, bridge['odb']), _bound(project, manifest['d62_receipt'])
            d62doc = _read(d62_path)
            routed_def = _bound(project, d62doc['outputs']['filled.def'])
            final_gds = _bound(project, bridge.get('gds'))
        fill_scope = _fill_extraction_scope(d62)
        result['fill_sensitive_parasitics'] = fill_scope
        views = manifest.get('adopted_views') or {}
        if set(views) != {'netlist', 'sdc'}:
            raise AdmissionRefusal('D69_ADOPTED_VIEWS_ABSENT', 'netlist and SDC required')
        netlist = _bound(project, views['netlist']); sdc = _bound(project, views['sdc'])
        basis = {str(p): _sha(p) for p in (project / INPUT, odb, routed_def, final_gds, netlist, sdc, d62_path)}
        sources = operation_sources()
        binary = shutil.which('vibeic-final-fill-spef')
        if binary is None:
            raise AdmissionRefusal('D69_NATIVE_TOOL_ABSENT', 'released vibeic-final-fill-spef required')
        tool = files._record(project, Path(binary))
        argv = [binary, 'extract', '--gds', str(final_gds), '--def', str(routed_def),
                '--top', files.fill_records(routed_def)['top'], '--output-dir', str(attempt / 'native')]
        result['native_started'] = True
        started = time.monotonic()
        completed = subprocess.run(argv, cwd=project, capture_output=True, text=True)
        log = attempt / 'native.log'; log.write_text(completed.stdout + completed.stderr)
        result['native_execution'] = {'argv': argv, 'returncode': completed.returncode,
            'elapsed_seconds': time.monotonic() - started, 'log': files._record(project, log), 'tool': tool}
        if completed.returncode != 0:
            raise AdmissionRefusal('D69_NATIVE_EXTRACT_FAILED', str(log), status='FAIL')
        if sources != operation_sources() or any(_sha(Path(p)) != digest for p, digest in basis.items()):
            raise AdmissionRefusal('D69_INPUT_CHANGED', 'source or geometry changed during extraction')
        _bound(project, tool)
        native_ref = files._record(project, attempt / 'native/FINAL_FILL_EXTRACTION.json')
        gds_ref, def_ref = files._record(project, final_gds), files._record(project, routed_def)
        final_spefs, scans = _native_outputs(project, native_ref, gds_ref, def_ref)
        result.update(component_status='PASS', reason='D69_NATIVE_FINAL_FILL_SPEF_MEASURED',
                      producer='flow109_final_extract',
                      component_scope='native_final_gds_floating_fill_component',
                      d62_receipt=manifest.get('d62_receipt'), stream_bridge=manifest.get('stream_bridge'),
                      retained_geometry=retained_geometry,
                      adopted_views=views, inputs=basis,
                      operation_sources=sources,
                      final_geometry=bridge['odb'],
                      final_gds=gds_ref, final_def=def_ref, native_receipt=native_ref,
                      spefs=final_spefs, final_spefs=final_spefs,
                      spef_consumer=scans, execution_context=context)
    except AdmissionRefusal as exc:
        result.update(component_status=exc.status, reason=exc.code, detail=str(exc))
    except Refusal as exc:
        result.update(component_status=_native_refusal_status(exc.code),
                      reason=exc.code, detail=str(exc))
    except Exception as exc:
        result.update(component_status='NOT_MEASURED', reason='D69_NATIVE_UNAVAILABLE_OR_UNSUPPORTED', detail=str(exc))
    path = attempt / 'component.json'; write_json(path, result)
    result['receipt'] = files._record(project, path)
    return result


def validate_final_parasitics(project, receipt_ref):
    project = Path(project).resolve(); record = _read(_bound(project, receipt_ref))
    if record.get('schema') != SCHEMA or record.get('component_status') != 'PASS':
        raise AdmissionRefusal('D69_COMPONENT_NOT_ACCEPTED', 'current final-fill native receipt required')
    require_launch_admission(project, 'flow109.D69', {'flow109_context': record.get('execution_context')})
    if record.get('operation_sources') != operation_sources():
        raise AdmissionRefusal('D69_OPERATION_SOURCE_CHANGED', 'adapter or retained consumer changed')
    if record.get('retained_geometry') is not None:
        geometry = _retained_geometry(project, {**record['retained_geometry'],
                                               'adopted_views': record['adopted_views']})
        if geometry != record['retained_geometry']:
            raise AdmissionRefusal('D69_RETAINED_GEOMETRY_CHANGED', 'native owner changed')
        d62 = {}
        bridge = {'odb': geometry['odb']}
        filled_def = geometry['filled_def']
        final_gds = geometry['final_gds']
    else:
        d62 = density.validate_filled_odb(project, record['d62_receipt'])
        bridge = files.validate_bridge(project, record['stream_bridge'])
        if bridge['d62_receipt'] != record['d62_receipt'] or bridge['odb'] != d62['odb']:
            raise AdmissionRefusal('D69_FINAL_GEOMETRY_MISMATCH', 'stream-out and D62 ODB differ')
        filled_def = _read(_bound(project, record['d62_receipt']))['outputs']['filled.def']
        final_gds = bridge['gds']
    if (record.get('producer') != 'flow109_final_extract'
            or record.get('final_geometry') != bridge['odb']
            or record.get('final_gds') != final_gds or record.get('final_def') != filled_def
            or record.get('final_spefs') != record.get('spefs')):
        raise AdmissionRefusal('D69_FINAL_OUTPUT_BINDING_MISMATCH', 'final ODB/SPEF aliases drifted')
    fill_scope = _component_scope(record, d62)
    for path, sha in record['inputs'].items():
        if not Path(path).is_file() or _sha(path) != sha:
            raise AdmissionRefusal('D69_INPUT_CHANGED', path)
    _bound(project, record['native_execution']['tool'])
    spefs, scans = _native_outputs(project, record['native_receipt'], final_gds, filled_def)
    if spefs != record['spefs'] or scans != record['spef_consumer']:
        raise AdmissionRefusal('D69_SPEF_CHANGED', 'native corner outputs changed')
    return {'odb': bridge['odb'], 'filled_def': filled_def, 'd62_receipt': record['d62_receipt'],
            'adopted_views': record['adopted_views'],
            'spefs': record['spefs'], 'final_geometry': record['final_geometry'],
            'final_spefs': record['final_spefs'], 'parasitic_receipt': receipt_ref,
            'final_gds': final_gds,
            'fill_sensitive_parasitics': fill_scope,
            'component_parasitics_current': True, 'flow109_acceptance': 'NOT_MEASURED'}
