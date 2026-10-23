"""D64 actual new-geometry component; no benchmark PASS/PDK qualification."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import flow109
import flow109_admission as admission
import flow109_final_geometry as geometry
import librelane_fill_stream as stream
from test_librelane_fill_stream import _native_case, _put


def _case(project, *, ic=False):
    d62, native = _native_case(project)
    native.pop('seal_gds')
    views = {'netlist': _put(project, 'inputs/adopted.v', 'module fill_smoke(input a,output y);assign y=a;endmodule\n'),
             'sdc': _put(project, 'inputs/adopted.sdc', 'create_clock -period 10 [get_ports a]\n')}
    core = [0, 0, 20, 20]
    config = _put(project, 'inputs/D64-operation.json', {'schema': geometry.SCHEMA,
        'basis': 'D62_native_odb_and_def', 'seal_policy': 'hard_ip_no_seal', 'core_area_um': core})
    bundle = _put(project, 'inputs/D64-model-bundle.json', {'native_stream_inputs': native, 'adopted_views': views})
    features = next(p['features'] for p in flow109.load_registry()['reference_profiles']
                    if p['name'] == ('Small digital IC without DFT' if ic else 'Digital hard-IP'))
    plan = flow109.resolve_plan(features)
    manifest = {'schema': geometry.SCHEMA, 'd62_receipt': d62,
        'native_stream_inputs': native, 'adopted_views': views, 'seal_policy': 'hard_ip_no_seal',
        'core_area_um': core, 'operation_config': config, 'model_bundle': bundle,
        'librelane': {'PDK': 'gf180mcuD', 'STD_CELL_LIBRARY': 'gf180mcu_fd_sc_mcu7t5v0', 'pdk_root': '/foss/pdks'}}
    _put(project, geometry.INPUT, manifest)
    uses, qualified = [], []
    for use in flow109.resolve_step(plan, 'D64')['tool_uses']:
        executable = stream._record(project, Path(shutil.which(use['executable_name'])).resolve())
        identity = {'use_id': use['use_id'], 'tool_id': use['tool_id'],
            'version': 'developer native component control; not PDK qualification',
            'build_sha256': executable['sha256'], 'executable': executable,
            'config': config, 'pdk': bundle, 'deck': bundle, 'corner': 'synthetic_geometry',
            'semantics': {'purpose': use['purpose'], 'native_steps': use['native_steps'],
                          'operation_sources': geometry.operation_sources()}}
        record = {'use_id': use['use_id'], 'status': 'QUALIFIED', 'identity': identity,
            'reference_evidence': [_put(project, 'inputs/D64-qualification-control.json',
                {'scope': 'developer contract controls, not production qualification'})]}
        qualified.append(record)
        uses.append({**identity, 'qualification_sha256': admission.canonical_sha(record)})
    _put(project, 'D64-qualifications.json', {'schema': 'vibeic.flow109.qualifications.v1', 'uses': qualified})
    _put(project, 'D64-manifest.json', {'features': features, 'runtime_tool_uses': uses,
        'qualification_registry': 'D64-qualifications.json'})
    return flow109.execution_context(plan, 'D64', 'D64', runtime_tool_uses=uses,
        qualification_registry='D64-qualifications.json')


def test_actual_general_frontdoor_consumes_new_d62_geometry_and_native_finishing_xor(tmp_path):
    _case(tmp_path)
    old = _put(tmp_path, 'reports/phase3/gds_xor.json', {'verdict': 'PASS', 'scope': 'old geometry not adopted'})
    before = (tmp_path / old['path']).read_bytes()
    proc = subprocess.run([sys.executable, str(geometry.HERE / 'vibe_ic_one_shot_runner.py'),
        str(tmp_path), '--flow', '109', '--flow109-manifest', str(tmp_path / 'D64-manifest.json'),
        '--entry-step', 'D64', '--exit-step', 'D64', '--route', 'ip', '--no-dashboard'],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    record = json.loads((tmp_path / 'reports/flow109/components/D64.json').read_text())
    assert record['component_status'] == 'PASS', record
    current = geometry.validate_final_geometry(tmp_path, record['receipt'])
    assert current['component_geometry_current'] is True
    assert current['flow109_acceptance'] == current['final_geometry_adoption'] == 'NOT_MEASURED'
    assert record['geometry_proof']['fill_count'] > 0
    assert record['finishing_xor']['defect_count'] == 0
    assert record['finishing_xor']['design_pair_count'] > 0
    assert (tmp_path / old['path']).read_bytes() == before
    assert current['gds']['sha256'] != record['adopted_views']['netlist']['sha256']


@pytest.mark.parametrize('damage', ['qualification', 'source', 'd62_rules', 'ic_no_seal'])
def test_invalid_geometry_or_ic_seal_requirement_never_launches_native_stream(tmp_path, monkeypatch, damage):
    context = _case(tmp_path, ic=damage == 'ic_no_seal')
    if damage == 'qualification':
        path = tmp_path / 'D64-qualifications.json'
        doc = json.loads(path.read_text());doc['uses'][0]['status'] = 'UNSUPPORTED';path.write_text(json.dumps(doc))
    elif damage == 'source':
        monkeypatch.setattr(geometry, 'operation_sources', lambda: {'changed': 'native API'})
    elif damage == 'd62_rules':
        (tmp_path / 'inputs/rules.json').write_text('{}')
    calls = []
    monkeypatch.setattr(stream, 'stream_d62', lambda *a, **k: calls.append('native'))
    result = geometry.execute(tmp_path, context)
    assert result['component_status'] == 'NOT_MEASURED', result
    assert result['native_started'] is False and calls == []
    if damage == 'ic_no_seal':
        assert result['reason'] == 'D64_D61_SEAL_REENTRY_NOT_IMPLEMENTED'
