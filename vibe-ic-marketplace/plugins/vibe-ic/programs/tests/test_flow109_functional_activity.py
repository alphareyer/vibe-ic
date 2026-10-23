"""Real D55 GLS -> scoped VCD -> OpenSTA vector-power component controls.

Developer controls are not production PDK qualification or final power signoff.
All source inputs are neutral fixtures, independent of any benchmark IC.
"""
from __future__ import annotations

import copy
import fnmatch
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

import flow109
import flow109_admission as admission
import flow109_functional_activity as activity
from programs.tests._hostpaths import require_repo


def _put(project, name, value):
    path = project / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value))
    return activity._record(project, path)


def _case(project):
    from librelane.config import Config
    from librelane.steps.yosys import EQY
    config, _ = Config.load({'DESIGN_NAME': 'activity_beta', 'PDK': 'gf180mcuD',
        'STD_CELL_LIBRARY': 'gf180mcu_fd_sc_mcu7t5v0', 'VERILOG_FILES': []},
        EQY.get_all_config_variables(), design_dir=str(project), pdk_root='/foss/pdks')
    library = Path(next(iter(next(group for pattern, group in config['CELL_LIBS'].items()
                        if fnmatch.fnmatch(config['DEFAULT_CORNER'], pattern)))))
    fixture = require_repo('vibe-ic-marketplace', 'plugins', 'vibe-ic', 'programs', 'tests',
                           'fixtures', 'flow109_functional_activity')
    _put(project, 'inputs/reference.v', (fixture / 'rtl.v').read_text())
    script = project / 'map.ys'
    script.write_text('read_verilog inputs/reference.v\nsynth -top activity_beta\n'
                     f'abc -liberty {library}\nclean\nwrite_verilog -noattr inputs/adopted.v\n')
    result = subprocess.run(['yosys', '-Q', '-T', '-s', str(script)], cwd=project, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    tb = _put(project, 'inputs/workload.v', (fixture / 'tb.v').read_text())
    loads = {'rails': {'core': {'voltage_v': 5.0}}, 'ports': {
        'y': {'rail': 'core', 'capacitance_library_units': 0.05},
        'z': {'rail': 'core', 'capacitance_library_units': 0.07}}}
    requirement = _put(project, 'inputs/requirements.json', {
        'approval': {'status': 'APPROVED', 'authority': 'requirements_owner',
                     'source_reference': 'neutral developer fixture, not a product requirement'},
        'modes': {'mission': {'required_ports': ['a', 'b', 'c', 'y', 'z'],
                             'window': {'start_tick': 0, 'end_tick': 45008, 'timescale': '1ps'}}},
        'external_loads': loads})
    load = _put(project, 'inputs/loads.json', {**loads, 'requirements_sha256': requirement['sha256']})
    liberty = activity._record(project, library)
    models = [activity._record(project, Path(p)) for p in config['CELL_VERILOG_MODELS']]
    manifest = {'schema': activity.SCHEMA, 'basis': 'adopted_netlist_gls', 'top': 'activity_beta',
        'adopted_netlist': activity._record(project, project / 'inputs/adopted.v'),
        'liberty': liberty, 'simulation_models': models, 'requirements': requirement, 'loads': load,
        'sdc': _put(project, 'inputs/constraints.sdc', 'create_clock -name virtual -period 10\n'),
        'modes': {'mission': {'testbench': tb}}}
    _put(project, activity.INPUT, manifest)
    operation = _put(project, 'input/flow109/D55-operation.json',
        {'schema': activity.SCHEMA, 'basis': 'adopted_netlist_gls', 'window': 'full_vcd', 'rail_count': 1})
    pdk = _put(project, 'input/flow109/D55-pdk.json', {'liberty': liberty, 'simulation_models': models})
    features = next(p['features'] for p in flow109.load_registry()['reference_profiles']
                    if p['name'] == 'Small digital IC without DFT')
    plan = flow109.resolve_plan(features)
    references = [_put(project, 'qualification-control.json',
        {'scope': 'native developer contract control; not a production qualification'})]
    uses, records = [], []
    for use in flow109.resolve_step(plan, 'D55')['tool_uses']:
        executable = activity._record(project, Path(shutil.which(use['executable_name'])).resolve())
        identity = {'use_id': use['use_id'], 'tool_id': use['tool_id'],
            'version': 'native developer control (executable hash bound)', 'build_sha256': executable['sha256'],
            'executable': executable, 'config': operation, 'pdk': pdk, 'deck': liberty,
            'corner': config['DEFAULT_CORNER'], 'semantics': {'purpose': use['purpose'],
                'native_steps': use['native_steps'], 'operation_sources': activity.operation_sources()}}
        record = {'use_id': use['use_id'], 'status': 'QUALIFIED', 'identity': identity,
                  'reference_evidence': references}
        records.append(record)
        uses.append({**identity, 'qualification_sha256': admission.canonical_sha(record)})
    _put(project, 'qualifications.json', {'schema': 'vibeic.flow109.qualifications.v1', 'uses': records})
    _put(project, 'manifest.json', {'features': features, 'runtime_tool_uses': uses,
                                  'qualification_registry': 'qualifications.json'})
    return flow109.execution_context(plan, 'D55', 'D55', runtime_tool_uses=uses,
                                      qualification_registry='qualifications.json')


def test_actual_front_door_runs_gls_and_consumes_all_data_ports_not_only_clock(tmp_path):
    _case(tmp_path)
    old = _put(tmp_path, 'reports/phase3/power.rpt', 'untouched legacy vectorless power')
    main = activity.HERE / 'vibe_ic_one_shot_runner.py'
    result = subprocess.run([sys.executable, str(main), str(tmp_path), '--flow', '109',
        '--flow109-manifest', str(tmp_path / 'manifest.json'), '--entry-step', 'D55', '--exit-step', 'D55',
        '--route', 'ic', '--no-dashboard'], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads((tmp_path / 'reports/flow109/components/D55.json').read_text())
    assert record['component_status'] == 'PASS', record
    assert record['flow109_acceptance'] == record['final_power_adoption'] == 'NOT_MEASURED'
    mode = record['modes']['mission']
    assert mode['consumption']['activity_provenance']['annotated_pin_activities'] > 0
    assert set(mode['consumption']['ports']) == {'a', 'b', 'c', 'y', 'z'}
    assert all(v['origin'] == 'vcd' for v in mode['consumption']['ports'].values())
    assert mode['census']['ports']['c']['transitions'] > 0
    assert mode['consumption']['power']['total_w'] > 0
    assert len(record['commands']) == 3
    assert activity._record(tmp_path, tmp_path / old['path']) == old
    frozen = json.loads((tmp_path / activity.FREEZE).read_text())
    assert frozen['adopted_netlist']['sha256'] == activity._sha(tmp_path / 'inputs/adopted.v')


@pytest.mark.parametrize('damage, reason', [
    ('mode', 'D55_REQUIRED_MODE_COVERAGE'), ('rtl', 'D55_ACTIVITY_BASIS_UNIMPLEMENTED'),
    ('load', 'D55_LOAD_REQUIREMENT_MISMATCH'), ('source', 'D55_OPERATION_SOURCE_CHANGED'),
    ('stale', 'D55_STALE_OR_MISSING_INPUT'), ('approval', 'D55_REQUIREMENTS_NOT_APPROVED'),
    ('unsupported', 'TOOL_USE_NOT_QUALIFIED')])
def test_bad_scope_or_unqualified_inputs_never_launch_native(tmp_path, monkeypatch, damage, reason):
    context = _case(tmp_path)
    manifest = json.loads((tmp_path / activity.INPUT).read_text())
    if damage == 'mode':
        manifest['modes'] = {}
    elif damage == 'rtl':
        manifest['basis'] = 'rtl_vcd'
    elif damage == 'load':
        manifest['loads'] = _put(tmp_path, 'inputs/wrong-load.json', {'ports': {}, 'rails': {}})
    elif damage == 'source':
        monkeypatch.setattr(activity, 'operation_sources', lambda: {'changed': 'source'})
    elif damage == 'stale':
        (tmp_path / 'inputs/adopted.v').write_text('module stale; endmodule')
    elif damage == 'approval':
        requirement = json.loads((tmp_path / 'inputs/requirements.json').read_text())
        requirement['approval']['authority'] = 'AI'
        manifest['requirements'] = _put(tmp_path, 'inputs/requirements.json', requirement)
    else:
        registry = json.loads((tmp_path / 'qualifications.json').read_text())
        registry['uses'][0]['status'] = 'UNSUPPORTED'
        (tmp_path / 'qualifications.json').write_text(json.dumps(registry))
    _put(tmp_path, activity.INPUT, manifest)
    calls = []
    monkeypatch.setattr(activity.dump, 'dump', lambda *a: calls.append('native'))
    record = activity.execute(tmp_path, context)
    assert record['component_status'] == 'NOT_MEASURED', record
    assert reason in record['reason'], record
    assert calls == [] and not record['native_started']
    assert not (tmp_path / activity.FREEZE).exists()


def test_simulator_failure_with_partial_vcd_cannot_freeze_activity(tmp_path):
    context = _case(tmp_path)
    path = tmp_path / activity.INPUT
    manifest = json.loads(path.read_text())
    text = (tmp_path / 'inputs/workload.v').read_text().replace('#5; $finish;', '#5; $fatal(1, "late failure");')
    manifest['modes']['mission']['testbench'] = _put(tmp_path, 'inputs/workload.v', text)
    _put(tmp_path, activity.INPUT, manifest)
    record = activity.execute(tmp_path, context)
    assert record['component_status'] == 'NOT_MEASURED'
    assert record['reason'] == 'D55_NATIVE_TOOL_FAILED'
    assert record['native_started'] and not (tmp_path / activity.FREEZE).exists()


def test_native_wrong_vcd_scope_does_not_pass_on_vectorless_nonzero_power(tmp_path, monkeypatch):
    context = _case(tmp_path)
    original = activity.dump.dump
    def wrong(*args):
        result = original(*args)
        result['scope'] = 'workload/wrong_dut'
        return result
    monkeypatch.setattr(activity.dump, 'dump', wrong)
    record = activity.execute(tmp_path, context)
    assert record['component_status'] == 'NOT_MEASURED'
    assert record['reason'] == 'D55_VCD_REQUIRED_SIGNAL_MISSING'
    assert len(record['commands']) == 2


def test_actual_power_rejects_clock_only_or_fallback_data_annotation(tmp_path):
    context = _case(tmp_path)
    record = activity.execute(tmp_path, context)
    assert record['component_status'] == 'PASS', record
    path = tmp_path / record['modes']['mission']['power_report']['path']
    text = path.read_text()
    assert 'D55_ACTIVITY c ' in text
    # All waveforms and nonzero power remain; only a required DATA port's
    # native origin changes to vectorless fallback.
    import re
    changed = re.sub(r'(^D55_ACTIVITY c .*?) vcd$', r'\1 input', text, flags=re.M)
    assert changed != text
    path.write_text(changed)
    spec = json.loads((tmp_path / 'inputs/requirements.json').read_text())['modes']['mission']
    loads = json.loads((tmp_path / 'inputs/loads.json').read_text())
    with pytest.raises(admission.AdmissionRefusal, match='D55_REQUIRED_PORT_NOT_VCD_ANNOTATED'):
        activity.consume_power(path, spec, loads, Path(record['receipt']['path']).parent.name)


def test_actual_launch_rechecks_d03_after_preparation(tmp_path, monkeypatch):
    context = _case(tmp_path)
    original = activity.prepare
    def drift(*args):
        result = original(*args)
        path = tmp_path / 'qualifications.json'
        registry = json.loads(path.read_text())
        registry['uses'][0]['status'] = 'UNSUPPORTED'
        path.write_text(json.dumps(registry))
        return result
    monkeypatch.setattr(activity, 'prepare', drift)
    record = activity.execute(tmp_path, context)
    assert record['component_status'] == 'NOT_MEASURED'
    assert not record['native_started'] and not record['commands']


def test_pinned_librelane_has_no_native_vcd_configuration_and_component_is_not_final_power():
    from librelane.steps.openroad import STAPostPNR
    assert not any('VCD' in v.name or 'SAIF' in v.name for v in STAPostPNR.get_all_config_variables())
    row = next(r for r in flow109.load_registry()['rows'] if r['id'] == 'D55')
    assert row['when'] == 'physical'
    assert row['metric_acceptance_status'] == 'UNIMPLEMENTED'
    assert {o['status'] for o in row['obligations']} == {'UNIMPLEMENTED_METRIC_BINDING'}
