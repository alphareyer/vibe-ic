"""A02 actual-ngspice component controls; these are not PDK signoff."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import flow109
import flow109_admission as admission
import flow109_analog_a02 as analog
import flow109_analog_model_check as model_check
from _analog_producer_fixture import block, make_project


def _put(project, name, body):
    path = project / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body if isinstance(body, str) else json.dumps(body, indent=2))
    return {'path': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def _case(project, *, broken=False, missing_corner=False, transitive=False):
    make_project(project, [block('regulator_alpha', 'ldo',
        [{'name': 'Vout', 'target': 1.8, 'unit': 'V'}])])
    drc = _put(project, 'input/pdk/klayout/open_dev.drc', '# developer DRC inventory\n')
    lvs = _put(project, 'input/pdk/netgen/open_dev.tcl', '# developer LVS inventory\n')
    device = 'not_the_declared_device' if broken else 'open_n'
    model = _put(project, 'input/pdk/spice/open_dev_models.lib',
        ('.inc input/pdk/spice/unbound_child.lib\n' if transitive else '')
        + ''.join('.lib ' + corner + '\n.model ' + device
                + ' nmos level=1 vto=0.5 kp=100u\n.endl ' + corner + '\n'
                for corner in ('ss', 'tt', 'ff')))
    smokes = []
    requested_corners = ('ss', 'tt', 'absent') if missing_corner else ('ss', 'tt', 'ff')
    for corner in requested_corners:
        deck = _put(project, f'input/flow109/decks/open_n_{corner}.sp',
            '* source-owned developer model smoke\n'
            f'.lib input/pdk/spice/open_dev_models.lib {corner}\n'
            'vdrain d 0 1.8\nvgate g 0 0\n'
            'mprobe d g 0 0 open_n w=10u l=1u\n'
            '.dc vgate 0 1.8 0.9\n'
            '.measure dc drain_current find i(vdrain) at=1.8\n.end\n')
        smokes.append({'id': f'open_n_{corner}', 'device': 'open_n', 'corner': corner,
                       'model': model, 'deck': deck,
                       'required_measurements': ['drain_current']})
    requirements = _put(project, 'input/flow109/A02-requirements.json', {
        'schema': analog.SCHEMA,
        'approval': {'status': 'APPROVED', 'authority': 'requirements_owner',
                     'source_reference': 'developer open-model A02 control'},
        'required_smokes': smokes})
    pdk = _put(project, 'input/flow109/A02-pdk.json', {
        'schema': analog.SCHEMA, 'name': 'open-developer-model', 'model_files': [model],
        'inventory_files': {'spice_models': model, 'drc_deck': drc, 'lvs_deck': lvs}})
    config = _put(project, analog.INPUT, {
        'schema': analog.SCHEMA, 'basis': 'declared_model_device_smoke',
        'pdk': pdk, 'requirements': requirements, 'smokes': smokes})

    features = next(p['features'] for p in flow109.load_registry()['reference_profiles']
                    if p['name'] == 'Analog model-IP')
    plan = flow109.resolve_plan(features)
    use = flow109.resolve_step(plan, 'A02')['tool_uses'][0]
    binary = Path(shutil.which('ngspice')).resolve()
    executable = {'path': str(binary), 'sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}
    identity = {'use_id': use['use_id'], 'tool_id': 'ngspice',
                'version': subprocess.run([binary, '-v'], capture_output=True, text=True).stdout.strip(),
                'build_sha256': executable['sha256'], 'executable': executable,
                'config': config, 'pdk': pdk, 'deck': requirements,
                'corner': sorted(requested_corners),
                'semantics': {'purpose': use['purpose'], 'native_steps': use['native_steps'],
                              'operation_sources': analog.operation_sources()}}
    reference = _put(project, 'input/flow109/A02-reference.json', {
        'scope': 'developer control only', 'native_engine': 'ngspice',
        'signoff': 'NOT_MEASURED'})
    record = {'use_id': use['use_id'], 'status': 'QUALIFIED', 'identity': identity,
              'reference_evidence': [reference]}
    registry = {'schema': 'vibeic.flow109.qualifications.v1', 'uses': [record]}
    manifest = {'features': features, 'qualification_registry': 'qualifications.json',
                'runtime_tool_uses': [{**identity,
                    'qualification_sha256': admission.canonical_sha(record)}]}
    (project / 'qualifications.json').write_text(json.dumps(registry))
    (project / 'manifest.json').write_text(json.dumps(manifest))
    return manifest, registry, model


def _run(project):
    return subprocess.run([
        'python3', str(analog.HERE / 'vibe_ic_one_shot_runner.py'), str(project),
        '--flow', '109', '--flow109-manifest', str(project / 'manifest.json'),
        '--entry-step', 'A02', '--exit-step', 'A02', '--route', 'ip', '--no-dashboard'],
        capture_output=True, text=True)


def test_actual_a02_runs_declared_model_device_smoke_and_keeps_signoff_unmeasured(tmp_path):
    _case(tmp_path)
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / 'reports/flow109/components/A02.json').read_text())
    assert report['component_status'] == 'PASS'
    assert report['flow109_acceptance'] == 'NOT_MEASURED'
    assert report['inventory']['verdict'] == 'PASS' and report['inventory']['waivers'] == 0
    assert len(report['smokes']) == 3 and all(row['returncode'] == 0 for row in report['smokes'])
    assert all(row['executed_model']['path'].startswith('reports/flow109/attempts/A02-')
               and row['executed_deck']['path'].startswith('reports/flow109/attempts/A02-')
               for row in report['smokes'])
    assert {row['corner'] for row in report['consumer']['smokes']} == {'ss', 'tt', 'ff'}
    assert all(row['measurements']['drain_current'] != 0 for row in report['consumer']['smokes'])
    assert report['unimplemented_obligations'] == [
        'base_A2.device_model_load_errors', 'base_A2.model_corner_statistics_coverage']
    freeze = json.loads((tmp_path / analog.FREEZE).read_text())
    assert freeze['status'] == 'PASS' and 'signoff NOT_MEASURED' in freeze['scope']


def test_broken_declared_device_is_a_native_failure_not_a_pass(tmp_path):
    _case(tmp_path, broken=True)
    result = _run(tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    report = json.loads((tmp_path / 'reports/flow109/components/A02.json').read_text())
    assert report['component_status'] == 'FAIL'
    assert report['reason'] in ('A02_NATIVE_MODEL_LOAD_FAILED', 'A02_NATIVE_MEASUREMENT_ABSENT')
    assert report['native_started'] is True
    assert not (tmp_path / analog.FREEZE).exists()


def test_missing_declared_model_refuses_before_ngspice_or_attempt(tmp_path):
    _manifest, _registry, model = _case(tmp_path)
    (tmp_path / model['path']).unlink()
    result = _run(tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'A02_STALE_OR_MISSING_INPUT' in result.stderr
    assert not (tmp_path / 'reports/flow109/attempts').exists()


def test_stale_model_bytes_refuse_before_ngspice(tmp_path):
    _manifest, _registry, model = _case(tmp_path)
    (tmp_path / model['path']).write_text('* changed after approval\n')
    result = _run(tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'A02_STALE_OR_MISSING_INPUT' in result.stderr
    assert not (tmp_path / 'reports/flow109/attempts').exists()


def test_absent_declared_corner_refuses_before_ngspice(tmp_path):
    _case(tmp_path, missing_corner=True)
    result = _run(tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'A02_MODEL_CORNER_ABSENT' in result.stderr
    assert not (tmp_path / 'reports/flow109/attempts').exists()


def test_symlinked_model_parent_refuses_before_ngspice(tmp_path):
    _case(tmp_path)
    pdk = tmp_path / 'input/pdk'
    real = tmp_path / 'input/pdk-real'
    pdk.rename(real)
    pdk.symlink_to(real, target_is_directory=True)
    result = _run(tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'symlinked path component' in result.stderr
    assert not (tmp_path / 'reports/flow109/attempts').exists()


def test_unbound_abbreviated_include_refuses_before_ngspice(tmp_path):
    _case(tmp_path, transitive=True)
    result = _run(tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'A02_TRANSITIVE_MODEL_INPUT_UNIMPLEMENTED' in result.stderr
    assert not (tmp_path / 'reports/flow109/attempts').exists()


def test_unsupported_qualification_refuses_before_component_dispatch(tmp_path):
    manifest, registry, _model = _case(tmp_path)
    registry['uses'][0]['status'] = 'UNSUPPORTED'
    record = registry['uses'][0]
    manifest['runtime_tool_uses'][0]['qualification_sha256'] = admission.canonical_sha(record)
    (tmp_path / 'qualifications.json').write_text(json.dumps(registry))
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    result = _run(tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'QUALIFICATION_UNSUPPORTED' in result.stderr
    assert not (tmp_path / 'reports/flow109/attempts').exists()


def test_a02_diagnostic_prose_polarity_counterexample_and_source_reverse(tmp_path, monkeypatch):
    """A denied fatal phrase is clean; a live fatal phrase still blocks."""
    _case(tmp_path)
    native = _run(tmp_path)
    assert native.returncode == 0, native.stdout + native.stderr
    component_path = tmp_path / 'reports/flow109/components/A02.json'
    report = json.loads(component_path.read_text())
    requirements = json.loads((tmp_path / 'input/flow109/A02-requirements.json').read_text())
    row = report['smokes'][0]
    log = tmp_path / row['log']['path']
    original = log.read_bytes()

    log.write_bytes(original + b'\nno fatal error occurred\n')
    row['log']['sha256'] = hashlib.sha256(log.read_bytes()).hexdigest()
    consulted = model_check.consume(tmp_path, requirements, report)
    _put(tmp_path, 'reports/flow109/A02-polarity-consulted.json', consulted)
    assert consulted['verdict'] == 'PASS', consulted

    canonical = model_check._polarity.is_denied
    monkeypatch.setattr(model_check._polarity, 'is_denied', lambda *_a, **_k: None)
    reversed_result = model_check.consume(tmp_path, requirements, report)
    _put(tmp_path, 'reports/flow109/A02-source-reverse.json', reversed_result)
    assert reversed_result['verdict'] == 'FAIL'
    assert reversed_result['reason'] == 'A02_NATIVE_MODEL_LOAD_FAILED'

    monkeypatch.setattr(model_check._polarity, 'is_denied', canonical)
    restored = model_check.consume(tmp_path, requirements, report)
    _put(tmp_path, 'reports/flow109/A02-source-restored.json', restored)
    assert restored['verdict'] == 'PASS', restored

    # Here "no" belongs to the diagnostic itself: it constitutes failure and
    # must not be mistaken for prose that retracts the diagnostic.
    log.write_bytes(original + b'\nno simulations run\n')
    row['log']['sha256'] = hashlib.sha256(log.read_bytes()).hexdigest()
    constitutive_fatal = model_check.consume(tmp_path, requirements, report)
    _put(tmp_path, 'reports/flow109/A02-constitutive-fatal.json', constitutive_fatal)
    assert constitutive_fatal['verdict'] == 'FAIL'
    assert constitutive_fatal['reason'] == 'A02_NATIVE_MODEL_LOAD_FAILED'

    log.write_bytes(original + b'\nfatal error occurred\n')
    row['log']['sha256'] = hashlib.sha256(log.read_bytes()).hexdigest()
    live_fatal = model_check.consume(tmp_path, requirements, report)
    _put(tmp_path, 'reports/flow109/A02-live-fatal.json', live_fatal)
    assert live_fatal['verdict'] == 'FAIL'
    assert live_fatal['reason'] == 'A02_NATIVE_MODEL_LOAD_FAILED'

    # Measurement identifiers are formal ngspice tokens.  A token containing
    # an English denial spelling is still data, not prose to suppress.
    formal_report, formal_requirements = copy.deepcopy(report), copy.deepcopy(requirements)
    formal_row = formal_report['smokes'][0]
    formal_need = next(item for item in formal_requirements['required_smokes']
                       if item['id'] == formal_row['id'])
    formal_row['required_measurements'] = formal_need['required_measurements'] = ['no_gain']
    log.write_bytes(original + b'\nno_gain = 1.0\n')
    formal_row['log']['sha256'] = hashlib.sha256(log.read_bytes()).hexdigest()
    formal = model_check.consume(tmp_path, formal_requirements, formal_report)
    _put(tmp_path, 'reports/flow109/A02-formal-measurement.json', formal)
    assert formal['verdict'] == 'PASS', formal
    assert formal['smokes'][0]['measurements'] == {'no_gain': 1.0}
