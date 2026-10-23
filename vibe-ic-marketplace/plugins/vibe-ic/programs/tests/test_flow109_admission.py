"""Admission contract controls; synthetic references are NOT native qualification."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import flow109
import flow109_admission as admission


def artifact(project, name, content):
    path = project / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return {'path': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def fixture_context(project, monkeypatch):
    features = next(p['features'] for p in flow109.load_registry()['reference_profiles']
                    if p['name'] == 'Small digital IC without DFT')
    plan = flow109.resolve_plan(features)
    use = flow109.resolve_step(plan, 'D38')['tool_uses'][0]
    exe = artifact(project, 'unit-openroad', 'unit test executable identity, not a real tool')
    exe['path'] = str(project / exe['path'])
    monkeypatch.setattr(admission.shutil, 'which', lambda name: exe['path'] if name == 'openroad' else None)
    identity = {'use_id': use['use_id'], 'tool_id': 'openroad', 'version': 'unit-control',
                'build_sha256': exe['sha256'], 'executable': exe,
                'config': artifact(project, 'config.json', '{}'),
                'pdk': artifact(project, 'pdk.ref', 'unit PDK'),
                'deck': artifact(project, 'deck.ref', 'unit deck'), 'corner': 'unit-corner',
                'semantics': {'purpose': use['purpose'], 'native_steps': use['native_steps']}}
    record = {'use_id': use['use_id'], 'status': 'QUALIFIED', 'identity': identity,
              'reference_evidence': [artifact(project, 'reference.json', '{"unit_control":true}') ]}
    registry = {'schema': 'vibeic.flow109.qualifications.v1', 'uses': [record]}
    (project / 'qualifications.json').write_text(json.dumps(registry))
    context = flow109.execution_context(plan, 'D38', 'D38',
        runtime_tool_uses=[{**identity, 'qualification_sha256': admission.canonical_sha(record)}],
        qualification_registry='qualifications.json')
    return context, registry


def rebind(context):
    context['context_sha256'] = flow109.digest({k:v for k,v in context.items() if k != 'context_sha256'})


def test_current_per_use_contract_accepts_without_mutating_evidence(tmp_path, monkeypatch):
    context, _ = fixture_context(tmp_path, monkeypatch)
    before = {p: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    result = admission.require_admission(tmp_path, context)
    assert result['status'] == 'PASS' and result['native_started'] is False
    assert all(p.read_bytes() == data for p,data in before.items())


@pytest.mark.parametrize('field', ['version','build_sha256','config','pdk','deck','corner','semantics','tool_id'])
def test_runtime_identity_change_refuses(tmp_path, monkeypatch, field):
    context, _ = fixture_context(tmp_path, monkeypatch)
    context['runtime_tool_uses'][0][field] = 'changed'
    rebind(context)
    with pytest.raises(admission.AdmissionRefusal, match='IDENTITY_MISMATCH'):
        admission.require_admission(tmp_path, context)


@pytest.mark.parametrize('status', ['UNSUPPORTED', 'STALE', 'FAIL', 'NOT_MEASURED'])
def test_unqualified_use_refuses(tmp_path, monkeypatch, status):
    context, registry = fixture_context(tmp_path, monkeypatch)
    registry['uses'][0]['status'] = status
    (tmp_path / 'qualifications.json').write_text(json.dumps(registry))
    with pytest.raises(admission.AdmissionRefusal, match='QUALIFICATION_' + status):
        admission.require_admission(tmp_path, context)


@pytest.mark.parametrize('name', ['config.json','pdk.ref','deck.ref','reference.json','unit-openroad'])
@pytest.mark.parametrize('damage', ['missing', 'changed'])
def test_evidence_missing_or_stale_refuses(tmp_path, monkeypatch, name, damage):
    context, _ = fixture_context(tmp_path, monkeypatch)
    path = tmp_path / name
    if damage == 'missing':
        path.unlink()
    else:
        path.write_text('changed')
    with pytest.raises(admission.AdmissionRefusal):
        admission.require_admission(tmp_path, context)


def test_unrelated_qualification_does_not_invalidate_current_use(tmp_path, monkeypatch):
    context, registry = fixture_context(tmp_path, monkeypatch)
    registry['uses'].append({'use_id': 'another-step', 'status': 'FAIL'})
    (tmp_path / 'qualifications.json').write_text(json.dumps(registry))
    assert admission.require_admission(tmp_path, context)['status'] == 'PASS'


def test_scope_cannot_qualify_cts_with_version_probe(tmp_path, monkeypatch):
    context, registry = fixture_context(tmp_path, monkeypatch)
    registry['uses'][0]['identity']['semantics']['native_steps'] = ['--version']
    context['runtime_tool_uses'][0]['semantics']['native_steps'] = ['--version']
    context['runtime_tool_uses'][0]['qualification_sha256'] = admission.canonical_sha(registry['uses'][0])
    rebind(context)
    (tmp_path / 'qualifications.json').write_text(json.dumps(registry))
    with pytest.raises(admission.AdmissionRefusal, match='SCOPE_MISMATCH'):
        admission.require_admission(tmp_path, context)


def test_legacy_launch_has_no_new_requirement(tmp_path, monkeypatch):
    monkeypatch.delenv('VIBEIC_FLOW109_CONTEXT', raising=False)
    assert admission.require_launch_admission(tmp_path, '19', {}) is None


def test_worker_refuses_before_native_producer(tmp_path, monkeypatch):
    import execution_backend_worker as worker
    inputs = tmp_path / 'inputs'
    project = inputs / 'project'
    project.mkdir(parents=True)
    context, _ = fixture_context(project, monkeypatch)
    context['runtime_tool_uses'][0]['corner'] = 'unsupported-corner'
    rebind(context)
    calls = []
    monkeypatch.setattr(worker, 'produce', lambda *a: calls.append('native'))
    monkeypatch.setenv('VIBEIC_EXECUTION_BINDING', json.dumps({'required_gates': []}))
    result = worker.execute(inputs, tmp_path / 'outputs', step_id='19', params={
        'flow109_context': context,
        'resolved_input_manifest': {'declarations': [], 'files': {}, 'missing': []}})
    assert not calls
    assert result['verdict'] != 'PASS'


def test_direct_default_refuses_before_input_refresh_and_native(tmp_path, monkeypatch):
    import phase3_one_shot_runner as runner
    import execution_policy
    import flow_compliance_check as compliance
    context, _ = fixture_context(tmp_path, monkeypatch)
    context['runtime_tool_uses'][0]['corner'] = 'unsupported-corner'
    rebind(context)
    path = tmp_path / 'context.json'
    path.write_text(json.dumps(context))
    monkeypatch.setenv('VIBEIC_FLOW109_CONTEXT', str(path))
    monkeypatch.setattr(runner, '_backend_parameter_values', lambda *a: {})
    monkeypatch.setattr(compliance, '_check_condition', lambda *a: True)
    calls = []
    monkeypatch.setattr(execution_policy, '_fixed_inputs', lambda *a,**kw: calls.append('input-binding'))
    result = runner._default_pnr_rows(tmp_path, 'unit', SimpleNamespace(name='unit'), '', ['19'])
    assert result.status == 'NOT_MEASURED'
    assert calls == []
    assert 'QUALIFICATION_IDENTITY_MISMATCH' in result.detail
