"""Current ordinary Default source controls. Substituted processes are SOURCE_FIXTURE_ONLY."""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import _plugin_tree  # noqa: F401
from _hostpaths import require_repo
import _default_backend_fixtures as BF
import librelane_contract as LC
import librelane_postroute as LP
import librelane_signoff as LS
import phase3_one_shot_runner as R
import power_report_check as PG
import si_crosstalk_check as SG
import spice_correlation_check as SC
import path_spice_tool as PST
import _opensta_current as C
from _ppa import power

SPEF = '''*SPEF "IEEE 1481-1998"
*DESIGN "neutral"
*T_UNIT 1 NS
*C_UNIT 1 PF
*R_UNIT 1 OHM
*L_UNIT 1 HENRY
*DIVIDER /
*DELIMITER :
*BUS_DELIMITER [ ]
*D_NET a 0.02
*CONN
*I u0:Z O
*I u2:A I
*CAP
1 u0:Z 0.01
2 u0:Z u1:Z 0.01
*RES
1 u0:Z u2:A 10
*END
*D_NET b 0.02
*CONN
*I u1:Z O
*P z O
*CAP
1 u1:Z 0.01
2 u1:Z u0:Z 0.01
*RES
1 u1:Z z 10
*END
'''
NETLIST = 'module neutral(input x, y, output z); wire a,b; BUF u0(.A(x),.Z(a)); BUF u1(.A(y),.Z(b)); BUF u2(.A(a),.Z(z)); endmodule\n'
POWER = '''OpenSTA report_power
Group                    Internal    Switching      Leakage        Total
                            Power        Power        Power        Power (Watts)
------------------------------------------------------------------------
Sequential           0.000000e+00 0.000000e+00 0.000000e+00 0.000000e+00   0.0%
Combinational        1.000000e-03 1.000000e-03 1.000000e-06 2.001000e-03 100.0%
Clock                0.000000e+00 0.000000e+00 0.000000e+00 0.000000e+00   0.0%
Macro                0.000000e+00 0.000000e+00 0.000000e+00 0.000000e+00   0.0%
Pad                  0.000000e+00 0.000000e+00 0.000000e+00 0.000000e+00   0.0%
------------------------------------------------------------------------
Total                1.000000e-03 1.000000e-03 1.000000e-06 2.001000e-03 100.0%
'''


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def seal(folder, fp):
    put(folder / 'input_fingerprint.json', fp)
    put(folder / 'vibeic_receipt.json', {'input': fp, 'sha256': {
        str(p.relative_to(folder)): LC.digest(p) for p in folder.rglob('*')
        if p.is_file() and p.name != 'vibeic_receipt.json'}})


def project(tmp_path):
    p = tmp_path / 'p'
    nl = write(p / 'phase3/stage3/pnr/neutral_pnr.v', NETLIST)
    spef = write(p / 'phase3/stage3/extracted/neutral.spef', SPEF)
    sdc = write(nl.parent / 'constraint.sdc', 'create_clock -name c -period 10\nset_input_delay 1 -clock c [get_ports {x y}]\nset_output_delay 1 -clock c [get_ports z]\n')
    views = {'nl': str(nl), 'sdc': str(sdc), 'spef': {'*': str(spef)}, 'metrics': {}}
    folder, _ = BF.producer(p, 'OpenROAD.STAPostPNR', views, views,
                           lane='22-23/01-openroad-stapostpnr', config_folder='22-config', name='neutral')
    lib = write(p / 'fixture_pdk/neutral/neutral.lib', 'library(neutral_typ) { default_operating_conditions: typ; }\n')
    corner = 'nom_typ'
    guest = '/pdk/neutral/neutral.lib'
    write(folder / corner / 'sta.log', f"SOURCE_FIXTURE_ONLY\nReading cell library for the '{corner}' corner at '{guest}'\n")
    write(folder / corner / 'power.rpt', POWER)
    config = p / 'phase3/librelane/22-config/OpenROAD.STAPostPNR.json'
    raw = json.loads(config.read_text())
    raw['CELL_LIBS'] = {'*': [guest]}
    put(config, raw)
    put(folder / 'config.json', raw)
    fp = json.loads((folder / 'input_fingerprint.json').read_text())
    fp['config'] = LC.digest(config)
    fp['config_files'] = {guest: LC.digest(lib)}
    fp['liberty_files'] = {guest: LC.digest(lib)}
    seal(folder, fp)
    put(p / LP.STEP23_RECORD, {'sta_state': str(folder / 'state_out.json'),
                              'sta_state_sha256': LC.digest(folder / 'state_out.json'),
                              'judgment': {'worst_setup': {'corner': corner}}})
    return p, folder, lib


def pdk(lib):
    return SimpleNamespace(name='neutral', liberty=str(lib), macro_libs=[], macro_lefs=[],
                           tech_lef='', cell_lef='')


def test_current_tool_mounts_fit_native_transport(tmp_path):
    p, _, _ = project(tmp_path)
    _, tool = C.tool_subject(p, 'neutral')
    assert tool['mounts']
    assert all(isinstance(host, Path) and host.is_dir() and guest.startswith('/pdk/')
               for host, guest in tool['mounts'])


def fake_windows(monkeypatch, p):
    def run(project, image, mounts, script, log, **kwargs):
        data = {pin: {'arr_rise_min': 1.0, 'arr_rise_max': 1.2, 'arr_fall_min': 1.0,
                'arr_fall_max': 1.2, 'slew_rise_max': 0.1, 'slew_fall_max': 0.1,
                'slack_max': 4.0} for pin in ('u0:Z', 'u1:Z', 'u2:A', 'z')}
        put(p / 'phase3/stage3/extracted/neutral_si_timing.json', {'pins': data})
        marker = re.search(r'VIBEIC_CURRENT_DONE [a-f0-9]+', script.read_text())[0]
        text = 'SOURCE_FIXTURE_ONLY\nSI_TIMING_JSON_EMIT_DONE\n' + marker + '\n'
        write(log, text)
        return SimpleNamespace(returncode=0, stdout=text, stderr='')
    monkeypatch.setattr(LS, 'run_sta_script', run)


def produce(p, folder, lib, monkeypatch, step):
    if step == '33':
        monkeypatch.setattr(R, '_docker_exec', lambda *a, **k: pytest.fail('parallel power producer ran'))
        return R._emit_power_report(p, 'neutral', pdk(lib), '', p / 'reports/phase3/power.rpt', [], basis='post_pnr')
    fake_windows(monkeypatch, p)
    return R._emit_si_crosstalk_report(p, 'neutral', p / 'phase3/stage3/extracted/neutral.spef',
        p / 'reports/phase3/ir_drop.rpt', p / 'reports/phase3/si_crosstalk.rpt', [], pdk(lib), '')


def gate(p, step):
    if step == '33':
        return PG.run([str(p), '--json', str(p / 'power-gate.json')])
    return SG.main([str(p), '--json', str(p / 'si-gate.json')])


@pytest.mark.parametrize('step', ['27', '33'])
def test_ordinary_default_adopts_one_current_tool_subject(tmp_path, monkeypatch, step):
    p, folder, lib = project(tmp_path)
    assert produce(p, folder, lib, monkeypatch, step) is True
    assert gate(p, step) == 0
    if step == '33':
        data = json.loads((p / 'reports/phase3/power.json').read_text())
        assert data['total_power_w'] == pytest.approx(0.002001)
        assert (folder / 'nom_typ/power.rpt').read_text() in (p / 'reports/phase3/power.rpt').read_text()
    else:
        assert json.loads((p / 'reports/phase3/si_crosstalk.json').read_text())['verdict'] == 'ADVISORY_SCREEN_ONLY'


@pytest.mark.parametrize('step', ['27', '33'])
@pytest.mark.parametrize('mutation', ['netlist', 'spef', 'sdc', 'liberty', 'corner', 'execution',
    'wrong_stage', 'wrong_project', 'wrong_path', 'report_replay', 'source_same_scalar', 'consumption'])
def test_current_gate_refuses_material_or_consumption_mutation(tmp_path, monkeypatch, step, mutation):
    p, folder, lib = project(tmp_path)
    assert produce(p, folder, lib, monkeypatch, step) is True
    assert gate(p, step) == 0
    if mutation in ('netlist', 'spef', 'sdc', 'liberty'):
        path = {'netlist': p / 'phase3/stage3/pnr/neutral_pnr.v',
                'spef': p / 'phase3/stage3/extracted/neutral.spef',
                'sdc': p / 'phase3/stage3/pnr/constraint.sdc', 'liberty': lib}[mutation]
        path.write_text(path.read_text() + '\n# substituted material\n')
    elif mutation == 'corner':
        path = p / LP.STEP23_RECORD
        doc = json.loads(path.read_text())
        doc['judgment']['worst_setup']['corner'] = 'different_corner'
        put(path, doc)
    elif mutation == 'execution':
        (folder / 'invocation.log').unlink()
    elif mutation == 'source_same_scalar':
        path = folder / 'nom_typ/power.rpt' if step == '33' else p / 'phase3/stage3/extracted/neutral_si_timing.json'
        path.write_text(path.read_text() + '\n ')
    else:
        name = 'power' if step == '33' else 'si_crosstalk'
        receipt = p / f'reports/phase3/{name}.current.json'
        doc = json.loads(receipt.read_text())
        if mutation == 'wrong_stage':
            doc['stage'] = 'pre_layout'
        elif mutation == 'wrong_project':
            doc['project'] = str(tmp_path / 'other')
        elif mutation == 'wrong_path':
            doc['outputs'][0]['path'] = str(tmp_path / 'other.rpt')
        elif mutation == 'consumption':
            doc['outputs'] = []
        else:
            target = p / f'reports/phase3/{name}.rpt'
            target.write_text(target.read_text() + '\n# replay from a different run\n')
        put(receipt, doc)
    assert gate(p, step) == 1


@pytest.mark.parametrize('step', ['27', '33'])
def test_missing_current_tool_refuses_even_with_copied_green_report(tmp_path, monkeypatch, step):
    p, folder, lib = project(tmp_path)
    write(p / 'reports/phase3/power.rpt', POWER * 6)
    write(p / 'phase3/stage3/pnr/sta.rpt', 'slack (MET) 3\n')
    (folder / 'invocation.log').unlink()
    assert produce(p, folder, lib, monkeypatch, step) is False
    assert gate(p, step) == 1


def test_measured_si_fail_is_not_promoted_by_an_advisory_screen(tmp_path, monkeypatch):
    p, folder, lib = project(tmp_path)
    import si_signoff_timing_aware as aware
    real = aware.run_si_signoff_timing_aware
    def failure(*a, **k):
        doc = real(*a, **k)
        doc['delta_delay'] = {'delta_delay_verdict': 'FAIL', 'violations_count': 1}
        return doc
    monkeypatch.setattr(aware, 'run_si_signoff_timing_aware', failure)
    assert produce(p, folder, lib, monkeypatch, '27')
    assert gate(p, '27') == 1


def test_power_audit_error_cannot_be_outvoted_by_current_numbers(tmp_path, monkeypatch):
    import eda_report_audit as audit
    p, folder, lib = project(tmp_path)
    assert produce(p, folder, lib, monkeypatch, '33')
    assert gate(p, '33') == 0
    real = audit._check_tool_authenticity
    def signature_error(files, mode, result):
        authentic = real(files, mode, result)
        result.findings.append(audit.Finding(rule='POWER_NO_TOOL_SIGNATURE',
            severity='ERROR', message='Reverse control: authenticated scalar cannot outvote an error'))
        return authentic
    monkeypatch.setattr(audit, '_check_tool_authenticity', signature_error)
    assert gate(p, '33') == 1
    doc = json.loads((p / 'power-gate.json').read_text())
    assert doc['passed'] is False
    assert any(f['severity'] == 'ERROR' for f in doc['findings'])


def test_native_shaped_short_power_source_is_checked_by_receipt(tmp_path, monkeypatch):
    p, folder, lib = project(tmp_path)
    source = folder / 'nom_typ/power.rpt'
    source.write_text(POWER.replace('# OpenSTA report_power', '# report_power'))
    assert source.stat().st_size < 2048
    seal(folder, json.loads((folder / 'input_fingerprint.json').read_text()))
    assert produce(p, folder, lib, monkeypatch, '33')
    assert gate(p, '33') == 0
    doc = json.loads((p / 'power-gate.json').read_text())
    assert doc['summary']['design_binding'] is True
    assert doc['summary']['current_binding'] == 'CURRENT'
    assert not any(f['severity'] == 'ERROR' for f in doc['findings'])
    source.write_text(source.read_text() + '\n ')
    assert gate(p, '33') == 1


def test_step30_copied_correlation_cannot_certify_without_execution(tmp_path):
    p, folder, lib = project(tmp_path)
    write(p / 'phase3/stage3/spice/correlation.spice', '* copied deck\n.end\n')
    put(p / 'reports/phase3/spice_correlation.json', {'correlation': {
        'liberty_spef_cone_delay_ns': 1, 'spice_path_delay_ns': 1,
        'pct_error': 0, 'tolerance_pct': 10, 'verdict': 'CORRELATED'}})
    assert SC.main([str(p), '--no-spice', '--json', str(p / 'spice-gate.json')]) == 1


def test_existing_tool_refuses_missing_matched_physical_state(tmp_path):
    result = SC.run_installed_pdk_path_correlation(tmp_path, '/unavailable.lib')
    assert result['status'] == 'ERROR'
    assert SC.main([str(tmp_path), '--json', str(tmp_path / 'spice-gate.json')]) == 1


def test_measured_spice_fail_precedes_other_unmeasured_arms():
    assert PST.step_verdict({'ngspice': {'verdict': 'CRITICAL_MISMATCH'},
                             'xyce': {'verdict': 'NOT_MEASURED'}}, {})['verdict'] == 'FAIL'


def test_real_declared_row_gate_consumes_the_current_report(tmp_path, monkeypatch):
    import yaml
    import flow_compliance_check as FC
    flow = yaml.safe_load(require_repo('vibe-ic-marketplace', 'plugins', 'vibe-ic',
        'flow', 'phase1_phase2_phase3.yaml').read_text())
    row = next(row for row in flow['steps'] if str(row['id']) == '33')
    p, folder, lib = project(tmp_path)
    assert produce(p, folder, lib, monkeypatch, '33')
    assert any('power_report_check' in str(clause) for clause in row['gate']['all_of'])
    # Exercise the declared command through the real exit-zero evaluator.
    command = row['gate']['all_of'][0]['program_exit_zero']
    clause = FC._check_program_exit_zero(p, command)
    assert clause[0] is True, clause
    (folder / 'invocation.log').unlink()
    clause = FC._check_program_exit_zero(p, command)
    assert clause[0] is False, clause


def spice_produce(p, folder, lib, monkeypatch, *, verdict='PASS'):
    write(p / 'fixture_pdk/neutral/libs.tech/ngspice/model.sp', '.model neutral nmos level=1\n')
    def run(project, image, root, pdk_name, **kwargs):
        detail = {}
        for variant in ('base', 'mutated'):
            directory = p / 'phase3/tool_arms/30/ngspice' / variant
            script = write(directory / 'arm.tcl', 'read_spef current\nwrite_path_spice\n')
            sta_log = write(directory / 'sta.log', 'SOURCE_FIXTURE_ONLY OpenSTA write_path_spice\n')
            deck = write(directory / 'path_1.sp', '* SOURCE_FIXTURE_ONLY tool deck\n.end\n')
            runnable = write(directory / 'path_1.run.sp', '* SOURCE_FIXTURE_ONLY simulator deck\n.end\n')
            sim_log = write(directory / 'path_1.log', 'SOURCE_FIXTURE_ONLY measured 1 ns\n')
            detail[variant] = {'sta_execution': {'tool': 'OpenSTA', 'rc': 0,
                'argv': ['sta', '-exit', str(script)], 'script': str(script), 'log': str(sta_log)},
                'paths': [{'deck': str(deck), 'status': 'MEASURED', 'spice_ns': 1,
                    'simulator_execution': {'tool': 'ngspice', 'rc': 0,
                        'argv': ['ngspice', '-b', str(runnable)],
                        'script': str(runnable), 'log': str(sim_log)}}]}
        doc = {'step': '30', 'corner': 'nom_typ', 'verdict': verdict,
               'arms': {'ngspice': {'verdict': 'CORRELATED' if verdict == 'PASS' else 'MISMATCH'}},
               'detail': {'ngspice': detail}}
        put(p / 'reports/phase3/spice_path_tool.json', doc)
        return doc
    monkeypatch.setattr(PST, 'run_step30', run)
    monkeypatch.setattr(LC, 'resolve_image', lambda *a, **k: BF.IMAGE)
    monkeypatch.setattr(LC, 'pdk_root_resolution', lambda *a, **k: {'path': str(p / 'fixture_pdk')})
    return SC.run_installed_pdk_path_correlation(p, str(lib))


def test_step30_ordinary_caller_adopts_the_existing_tool_product(tmp_path, monkeypatch):
    p, folder, lib = project(tmp_path)
    result = spice_produce(p, folder, lib, monkeypatch)
    assert result['status'] == 'RAN', result
    assert SC.main([str(p), '--json', str(p / 'spice-gate.json')]) == 0
    assert (p / 'phase3/stage3/spice/correlation.spice').read_bytes() == (p / 'phase3/tool_arms/30/ngspice/base/path_1.sp').read_bytes()


@pytest.mark.parametrize('mutation', ['netlist', 'spef', 'sdc', 'liberty', 'corner', 'models',
    'execution', 'report_replay', 'wrong_project', 'wrong_stage', 'wrong_path', 'consumption'])
def test_step30_gate_refuses_current_byte_and_consumer_mutations(tmp_path, monkeypatch, mutation):
    p, folder, lib = project(tmp_path)
    assert spice_produce(p, folder, lib, monkeypatch)['status'] == 'RAN'
    assert SC.main([str(p), '--no-spice', '--json', str(p / 'spice-gate.json')]) == 0
    if mutation in ('netlist', 'spef', 'sdc', 'liberty', 'models'):
        path = {'netlist': p / 'phase3/stage3/pnr/neutral_pnr.v',
                'spef': p / 'phase3/stage3/extracted/neutral.spef',
                'sdc': p / 'phase3/stage3/pnr/constraint.sdc', 'liberty': lib,
                'models': p / 'fixture_pdk/neutral/libs.tech/ngspice/model.sp'}[mutation]
        path.write_text(path.read_text() + '\n ')
    elif mutation == 'corner':
        path = p / LP.STEP23_RECORD
        doc = json.loads(path.read_text())
        doc['judgment']['worst_setup']['corner'] = 'other'
        put(path, doc)
    elif mutation == 'execution':
        (p / 'phase3/tool_arms/30/ngspice/base/path_1.log').unlink()
    elif mutation == 'report_replay':
        path = p / 'reports/phase3/spice_path_tool.json'
        path.write_text(path.read_text() + '\n ')
    else:
        path = p / 'reports/phase3/spice_correlation.current.json'
        doc = json.loads(path.read_text())
        if mutation == 'wrong_project':
            doc['project'] = str(tmp_path / 'other')
        elif mutation == 'wrong_stage':
            doc['stage'] = 'pre_layout'
        elif mutation == 'wrong_path':
            doc['outputs'][0]['path'] = str(tmp_path / 'outside.sp')
        else:
            doc['outputs'] = []
        put(path, doc)
    assert SC.main([str(p), '--no-spice', '--json', str(p / 'spice-gate.json')]) == 1


def test_step30_current_measured_fail_is_still_fail(tmp_path, monkeypatch):
    p, folder, lib = project(tmp_path)
    result = spice_produce(p, folder, lib, monkeypatch, verdict='FAIL')
    assert result['status'] == 'RAN'
    assert SC.main([str(p), '--json', str(p / 'spice-gate.json')]) == 1
    assert json.loads((p / 'spice-gate.json').read_text())['summary']['measurement'] == 'MEASURED'
