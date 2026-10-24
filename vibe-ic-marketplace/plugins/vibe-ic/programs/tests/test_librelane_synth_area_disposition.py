"""A missing area comparison cannot mask the native handoff checks.

Only native synthesis/config resolution and final receipt publication are
fixtures. Area, netlist, PDK, provenance and constant-handoff programs execute
as real subprocesses against neutral fixture artifacts. This is not an EDA run.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import phase3_one_shot_runner as runner
import librelane_contract as lc
import synth_area_stats_emit as area_emit
import synth_handoff_netlist_check as handoff
import execution_production as production


def put(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) + '\n')
    return path


def synth_case(tmp_path, monkeypatch, ceiling=None, damage=None, critical='clean',
               postmap=False, prior_provenance=None):
    project = tmp_path / 'project'
    rtl = project / 'phase2/stage1/rtl/top.v'
    rtl.parent.mkdir(parents=True)
    rtl.write_text('module top(input a, output y);\nassign y = a;\nendmodule\n')
    root = tmp_path / 'pdk'
    root.mkdir()
    liberty = root / 'neutral_sc__tt.lib'
    liberty.write_text('library(neutral_sc) {\ncell(BUF_X1) { area : 1; }\n}\n')
    pdk = SimpleNamespace(name='neutral', liberty=str(liberty), macro_libs=[], macro_lefs=[], macro_v=[])
    if postmap:
        put(project / 'input/project.json', {'pdk': 'neutral'})
        put(project / 'phase3/librelane_switch.json', {'post_map_repair': 'pre_placement'})
    if ceiling is not None:
        put(project / 'phase1/generated_docs/L19_CONSTRAINTS_PDK.json',
            {'fields': {'die_area_budget_um': ceiling}})
    monkeypatch.setattr(lc, 'resolve_image', lambda p: 'stated/tool@sha256:' + 'a' * 64)
    monkeypatch.setattr(lc, 'pdk_root_resolution', lambda *a, **k: {'path': str(root)})
    monkeypatch.setattr(runner, '_librelane_synth_fanout_updates', lambda *a: {})

    def config(project, pdk, output, *args, **kwargs):
        put(output.with_suffix('.provenance.json'), {})
        return {'SYNTH_FSM_ENCFILE': False, 'SYNTH_TIEHI_CELL': 'TIE_H/Y', 'SYNTH_TIELO_CELL': 'TIE_L/Y',
                **({'PDK': 'neutral', 'VERILOG_FILES': [str(rtl)]} if postmap else {}),
                **_critical_reports(critical)[0]}
    monkeypatch.setattr(lc, 'emit_synthesis_config', config)
    monkeypatch.setattr(lc, 'resolve_step_config',
                        lambda project, image, raw, output, **kw: put(output, json.loads(raw.read_text())))
    folder = project / 'phase3/librelane/02-yosys-synthesis'
    raw = folder / 'top.nl.v'
    if prior_provenance is not None:
        put(folder / 'provenance_gate.json', prior_provenance)

    def chain(project, image, steps, **kwargs):
        folder.mkdir(parents=True, exist_ok=True)
        raw.write_text('module top(input a, output y);\nBUF_X1 u_buf (.A(a), .Y(y));\nendmodule\n')
        put(folder / 'config.json', json.loads(steps[1][1].read_text()))
        put(folder / 'reports/stat.json', {'modules': {'\\top': {'num_cells': 1, 'area': 1.0,
                                                               **({'num_submodules': 0} if postmap else {})}}})
        put(folder / 'state_out.json', {'nl': str(raw), 'metrics': {
            'design__instance__count': 1, 'design__instance__area': 1.0,
            'design__instance_unmapped__count': 0, 'synthesis__check_error__count': 0}})
        _, pre, final = _critical_reports(critical)
        (folder / 'reports/pre_synth_chk.rpt').write_text(pre)
        (folder / 'reports/chk.rpt').write_text(final)
        if postmap:
            put(folder / 'pdk_root.json', {'cli_pdk_root': '/pdk',
                'stated_by': 'run_chain(pdk_root=...)', 'mounts_under_it': []})
            put(folder / 'input_fingerprint.json', {
                'step': 'Yosys.Synthesis',
                'config': hashlib.sha256(steps[1][1].read_bytes()).hexdigest(),
                'config_files': {str(rtl): hashlib.sha256(rtl.read_bytes()).hexdigest()},
                'state_files': {}})
        put(folder / 'vibeic_receipt.json', {**({'input': {
            'step': 'Yosys.Synthesis', 'config': hashlib.sha256(steps[1][1].read_bytes()).hexdigest(),
            'config_files': {str(rtl): hashlib.sha256(rtl.read_bytes()).hexdigest()},
            'state_files': {}}} if postmap else {}), 'sha256': {
            str(path.relative_to(folder)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in folder.rglob('*') if path.is_file()}})
        if critical == 'missing_final':
            (folder / 'reports/chk.rpt').unlink()
        elif critical == 'changed_final':
            (folder / 'reports/chk.rpt').write_text(final + '# changed after receipt\n')
        if damage == 'missing_tool_netlist':
            raw.unlink()
        if damage == 'area_cli_error':
            put(folder / 'area_budget_gate.json', {'verdict': 'INCOMPLETE'})
        return [folder, folder]
    monkeypatch.setattr(lc, 'run_chain', chain)

    def emit(project, log, netlist, **kwargs):
        return put(project / 'phase2/stage2/synth/stats.json', {
            'cell_count': 1, 'chip_area': 1.0, 'chip_area_unit': 'um^2',
            'top_module': 'top', 'selection': {'rule': 'SINGLE_MODULE_NO_HIERARCHY'},
            'netlist_sha256': 'sha256:' + hashlib.sha256(netlist.read_bytes()).hexdigest()})
    monkeypatch.setattr(area_emit, 'emit_for_run', emit)
    published = []
    if postmap:
        real_publish = handoff.publish_handoff

        def publish(*args):
            result = real_publish(*args)
            published.append(args)
            return result
        monkeypatch.setattr(handoff, 'publish_handoff', publish)
        import librelane_postmap

        def no_child(project, *args, **kwargs):
            # Native post-map transport is outside this fixture. The real
            # publisher has run, but a requested terminal child is still absent.
            # The ordinary strict reader must not mistake its parent for it.
            argv = [sys.executable, str(PROGRAMS / 'provenance_check.py'), str(project),
                    '--output', 'phase2/stage2/synth/netlist.v', '--tool', 'yosys',
                    '--require-measured', '--json', str(folder / 'terminal_missing_child.json')]
            checked = real_run(argv, capture_output=True, text=True)
            assert checked.returncode == 1, checked.stdout + checked.stderr
            return None
        monkeypatch.setattr(librelane_postmap, 'run_and_publish', no_child)
    else:
        monkeypatch.setattr(handoff, 'publish_handoff', lambda *a: published.append(a))
    real_run = subprocess.run
    calls = []

    def checked_run(argv, **kwargs):
        program = Path(argv[1]).stem if len(argv) > 1 else ''
        calls.append(program)
        if program == 'provenance_check' and damage in (
                'measurement_missing', 'measurement_false',
                'measurement_benign', 'measurement_foreign'):
            ledger = project / 'provenance.jsonl'
            rows = [json.loads(line) for line in ledger.read_text().splitlines()]
            for row in rows:
                if damage == 'measurement_missing':
                    row.pop('measurement', None)
                elif damage == 'measurement_foreign':
                    row['tool'] = 'foreign_tool'
                else:
                    row['measurement'].update(
                        measured=False,
                        not_measured_class=('NOTHING_TO_MEASURE' if damage == 'measurement_benign'
                                            else 'TOOL_DID_NOT_RUN'))
            ledger.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        if damage == program:
            if program == 'synth_netlist_check':
                Path(argv[argv.index('--netlist') + 1]).write_text('')
            elif program == 'pdk_consistency_check':
                liberty.write_text('library(neutral_sc) {\ncell(OTHER_X1) { area : 1; }\n}\n')
            elif program == 'provenance_check':
                (project / 'provenance.jsonl').write_text('')
            elif program == 'synth_handoff_netlist_check':
                put(folder / 'config.json', {})
        if program == 'provenance_check' and damage in {
                'native_source_changed', 'foreign_parent', 'missing_parent_measurement'}:
            if damage == 'native_source_changed':
                raw.write_text(raw.read_text() + '// changed after alias publication\n')
            else:
                ledger = project / 'provenance.jsonl'
                rows = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
                for row in rows:
                    if damage == 'foreign_parent':
                        row['tool'] = 'foreign'
                    else:
                        row.pop('measurement', None)
                ledger.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        if program == 'area_total_vs_budget_check' and damage == 'area_cli_error':
            return subprocess.CompletedProcess(argv, 2, '', 'usage error')
        result = real_run(argv, **kwargs)
        if program == 'provenance_check':
            report = Path(argv[argv.index('--json') + 1])
            observed = json.loads(report.read_text())
            put(folder / 'provenance_cli_observation.json',
                {'returncode': result.returncode, 'report': observed})
            # Corrupt only a report the ordinary subprocess actually wrote;
            # these are consumer transport controls, not EDA measurements.
            if damage == 'report_missing':
                report.unlink()
            elif damage == 'report_malformed':
                report.write_text('{invalid json')
            elif damage and damage.startswith('report_'):
                if damage == 'report_empty_checks': observed['checks'] = []
                elif damage == 'report_duplicate_checks': observed['checks'] *= 2
                elif damage == 'report_wrong_output': observed['checks'][0]['output'] = 'another/native.v'
                elif damage == 'report_unknown_status': observed['checks'][0]['status'] = 'UNKNOWN'
                elif damage == 'report_measurement_missing': observed['checks'][0].pop('measurement')
                elif damage == 'report_measurement_string':
                    observed['checks'][0]['measurement'].update(declared='true', measured='true')
                put(report, observed)
        if program == 'area_total_vs_budget_check' and damage in {'area_report_missing', 'area_report_malformed', 'area_report_conflicting'}:
            report = folder / 'area_budget_gate.json'
            if damage == 'area_report_missing':
                report.unlink(missing_ok=True)
            elif damage == 'area_report_malformed':
                report.write_text('{invalid json')
            else:
                put(report, {'verdict': 'PASS'})
        return result
    monkeypatch.setattr(runner.subprocess, 'run', checked_run)
    try:
        result = runner._step_synth_librelane(project, 'top', pdk, 'stated-container')
    finally:
        runner.set_invocation_provenance_sink(None)
    return result, calls, published, folder


CHECKS = ['synth_netlist_check', 'pdk_consistency_check', 'provenance_check', 'synth_handoff_netlist_check']


@pytest.mark.parametrize('damage,status', [
    (None, 'PASS'), ('native_source_changed', 'FAIL'),
    ('foreign_parent', 'FAIL'), ('missing_parent_measurement', 'NOT_MEASURED'),
])
def test_first_postmap_parent_gate_precedes_actual_handoff_publication(tmp_path, monkeypatch, damage, status):
    """Real producer control flow/readers/publisher, neutral transport only.

    The terminal post-map child is deliberately not produced in this fixture;
    its ordinary reader must still refuse, not inherit parent-only admission.
    """
    result, calls, published, folder = synth_case(
        tmp_path, monkeypatch, ceiling='10x10', damage=damage, postmap=True)
    assert result.status == status, result.detail
    report = json.loads((folder / 'provenance_gate.json').read_text())
    if damage is None:
        assert len(published) == 1
        source = json.loads((folder / 'state_out.json').read_text())['nl']
        project = folder.parents[2]
        assert report['checks'][0]['output'] == str(Path(source).relative_to(project))
        assert all(row['status'] == 'PASS' for row in report['checks'])
        binding = json.loads((project / 'phase2/stage2/synth/synth_inputs.json').read_text())
        assert binding['librelane_synthesis']['netlist_sha256'] == hashlib.sha256(Path(source).read_bytes()).hexdigest()
        terminal = json.loads((folder / 'terminal_missing_child.json').read_text())
        assert all(row['status'] == 'FAIL' for row in terminal['checks'])
        assert 'synth_handoff_netlist_check' in calls
    else:
        assert not published
        assert 'synth_handoff_netlist_check' not in calls
        expected = 'UNMEASURED' if damage == 'missing_parent_measurement' else 'FAIL'
        assert report['checks'][0]['status'] == expected


def test_uncompared_area_runs_checks_and_remains_unmeasured(tmp_path, monkeypatch):
    result, calls, published, folder = synth_case(tmp_path, monkeypatch)
    assert result.status == 'NOT_MEASURED', result.detail
    assert result.reason_class == 'inconclusive'
    assert [c for c in calls if c in CHECKS] == CHECKS
    assert len(published) == 1
    report = json.loads((folder / 'area_budget_gate.json').read_text())
    assert report['verdict'] == 'INCOMPLETE'
    assert production._gate_verdict(2, report) == 'NOT_MEASURED'
    # The actual Step9 consumer must retain this producer disposition.
    outputs = tmp_path / 'outputs'
    binding = {'required_gates': list(production.REQUIRED_GATES)}
    put(outputs / 'producer.json', {'binding': binding, 'status': result.status, 'detail': result.detail})
    (outputs / 'native_commands.jsonl').write_text('{}\n')
    evidence = production.validate_synthesis(outputs, binding)
    assert evidence.verdict == 'NOT_MEASURED'
    assert evidence.gates['area_total_vs_budget_check'] == 'NOT_MEASURED'


@pytest.mark.parametrize('damage,code', [
    ('synth_netlist_check', 'LL_SYNTH_NETLIST_GATE_FAILED'),
    ('pdk_consistency_check', 'LL_PDK_CONSISTENCY_FAILED'),
    ('provenance_check', 'LL_PROVENANCE_FAILED'),
    ('synth_handoff_netlist_check', 'LL_HANDOFF_NETLIST_FAILED'),
])
def test_actual_handoff_failure_wins_over_uncompared_area(tmp_path, monkeypatch, damage, code):
    result, calls, published, _ = synth_case(tmp_path, monkeypatch, damage=damage)
    assert result.status == 'FAIL', result.detail
    assert code in result.detail
    assert damage in calls
    assert not published


@pytest.mark.parametrize('ceiling,status', [('10x10', 'PASS'), ('0.5x0.5', 'FAIL')])
def test_measured_area_decisions_are_unchanged(tmp_path, monkeypatch, ceiling, status):
    result, calls, published, _ = synth_case(tmp_path, monkeypatch, ceiling=ceiling)
    assert result.status == status, result.detail
    if status == 'FAIL':
        assert 'area_total_vs_budget_check rc=1' in result.detail
        assert not published
    else:
        assert [c for c in calls if c in CHECKS] == CHECKS
        assert len(published) == 1


def test_missing_tool_netlist_remains_failure(tmp_path, monkeypatch):
    result, calls, published, _ = synth_case(tmp_path, monkeypatch, damage='missing_tool_netlist')
    assert result.status == 'FAIL'
    assert 'LL_SYNTH_OUTPUT_MISSING' in result.detail
    assert not published


@pytest.mark.parametrize('damage', [None, 'provenance_check', 'synth_netlist_check'])
def test_first_parent_provenance_precedes_requested_postmap_handoff(
        tmp_path, monkeypatch, damage):
    """Run the owner and real gates before any handoff/child is published.

    Native transport and final publishers are fixtures, as in synth_case;
    the actual provenance subprocess must consume the original Yosys source.
    The separate terminal-alias tests still require the complete child.
    """
    import librelane_postmap as postmap

    project = tmp_path / 'project'
    put(project / 'phase3/librelane_switch.json',
        {'steps': {'9': 'librelane'}, 'post_map_repair': 'pre_placement'})
    child_calls = []
    monkeypatch.setattr(postmap, 'run_and_publish',
                        lambda *a, **kw: child_calls.append((a, kw)))
    result, calls, published, folder = synth_case(
        tmp_path, monkeypatch, ceiling='10x10', damage=damage)
    if damage:
        assert result.status == 'FAIL', result.detail
        assert not published
        assert not child_calls
        return
    assert result.status == 'PASS', result.detail
    assert [c for c in calls if c in CHECKS] == CHECKS
    assert len(published) == len(child_calls) == 1
    sidecar = json.loads((project / 'phase2/stage2/synth/synth_inputs.json').read_text())
    assert 'librelane_synthesis' not in sidecar
    report = json.loads((folder / 'provenance_gate.json').read_text())
    assert len(report['checks']) == 1
    check = report['checks'][0]
    assert check['status'] == 'PASS', check
    assert check['output'] == 'phase3/librelane/02-yosys-synthesis/top.nl.v'
    assert check['tool'] == 'yosys'
    assert check['measurement']['measured'] is True
    assert check['declared_under'] == 'phase2/stage2/synth/top_synth.v'
    rows = [json.loads(line) for line in (project / 'provenance.jsonl').read_text().splitlines()]
    assert rows[-1]['produced_by'] == 'LibreLane.Yosys.Synthesis'


@pytest.mark.parametrize('damage,status,cli_rc,cli_status', [
    (None, 'PASS', 0, 'PASS'),
    ('measurement_missing', 'NOT_MEASURED', 0, 'UNMEASURED'),
    ('measurement_false', 'FAIL', 1, 'FAIL'),
    ('measurement_benign', 'NOT_MEASURED', 0, 'PASS'),
    ('measurement_foreign', 'FAIL', 1, 'FAIL'),
    ('report_missing', 'FAIL', 0, 'PASS'),
    ('report_malformed', 'FAIL', 0, 'PASS'),
    ('report_empty_checks', 'FAIL', 0, 'PASS'),
    ('report_duplicate_checks', 'FAIL', 0, 'PASS'),
    ('report_wrong_output', 'FAIL', 0, 'PASS'),
    ('report_unknown_status', 'FAIL', 0, 'PASS'),
    ('report_measurement_missing', 'FAIL', 0, 'PASS'),
    ('report_measurement_string', 'FAIL', 0, 'PASS'),
])
def test_initial_parent_disposition_controls_publication(
        tmp_path, monkeypatch, damage, status, cli_rc, cli_status):
    """No handoff/postmap may consume an unmeasured or malformed parent.

    The actual provenance subprocess retains its public three-state contract.
    Native transport/final publishers are neutral fixtures, not EDA results.
    """
    import librelane_postmap as postmap

    project = tmp_path / 'project'
    put(project / 'phase3/librelane_switch.json',
        {'steps': {'9': 'librelane'}, 'post_map_repair': 'pre_placement'})
    child_calls = []
    monkeypatch.setattr(postmap, 'run_and_publish',
                        lambda *a, **kw: child_calls.append((a, kw)))
    result, _, published, folder = synth_case(
        tmp_path, monkeypatch, ceiling='10x10', damage=damage)
    observation = json.loads((folder / 'provenance_cli_observation.json').read_text())
    put(folder / 'owner_publication_observation.json', {
        'damage': damage, 'owner_status': result.status,
        'owner_detail': result.detail, 'reason_class': result.reason_class,
        'handoff_publish_count': len(published), 'postmap_entry_count': len(child_calls),
        'ordinary_cli': observation,
        'scope': 'real runner and ordinary gate subprocess; native transport and final publishers are fixtures',
    })
    assert observation['returncode'] == cli_rc
    assert observation['report']['checks'][0]['status'] == cli_status
    assert result.status == status, result.detail
    assert len(published) == len(child_calls) == (1 if status == 'PASS' else 0)
    if status == 'NOT_MEASURED':
        assert result.reason_class == 'inconclusive'
    elif damage and damage.startswith('report_'):
        assert 'LL_PROVENANCE_REPORT_INVALID' in result.detail


def test_retry_retains_the_previous_provenance_report(tmp_path, monkeypatch):
    prior = {'schema': 'prior-provenance/1', 'invocation': 'previous'}
    result, _, published, folder = synth_case(
        tmp_path, monkeypatch, ceiling='10x10', prior_provenance=prior)
    assert result.status == 'PASS', result.detail
    assert len(published) == 1
    retained = list(folder.glob('provenance_gate.*.previous.json'))
    assert len(retained) == 1
    assert json.loads(retained[0].read_text()) == prior
    current = json.loads((folder / 'provenance_gate.json').read_text())
    assert current != prior
    assert current['checks'][0]['status'] == 'PASS'


@pytest.mark.parametrize('damage', ['area_report_missing', 'area_report_malformed', 'area_report_conflicting', 'area_cli_error'])
def test_unexplained_rc2_never_reuses_or_fabricates_area_evidence(tmp_path, monkeypatch, damage):
    result, calls, published, _ = synth_case(tmp_path, monkeypatch, damage=damage)
    assert result.status == 'FAIL', result.detail
    assert 'LL_SYNTH_AREA_REPORT_INVALID' in result.detail
    assert not published


def _critical_reports(case):
    config = {'TRISTATE_CELLS': ['SAFE_TBUF_*'],
              'SYNTH_CHECKS_ALLOW_TRISTATE': True,
              'SYNTH_ELABORATE_ONLY': case == 'elaborate'}
    clean = 'Found and reported 0 problems.\n'
    undriven = ('Warning: Wire core.\\unused [1] is used but has no driver.\n'
                'Found and reported 1 problems.\n')
    tristate = ('Warning: multiple conflicting drivers for core.\\bus:\n'
                '    port Y[0] of cell u_buf (SAFE_TBUF_X1)\n'
                'Found and reported 1 problems.\n')
    return (config, undriven if case == 'pre_bad' else clean,
            undriven if case in ('final_bad', 'elaborate') else
            tristate if case == 'tristate' else clean)


@pytest.mark.parametrize('critical', ['final_bad', 'pre_bad', 'clean', 'tristate',
                                     'elaborate', 'missing_final', 'changed_final'])
def test_selected_synthesis_critical_checks(tmp_path, monkeypatch, critical):
    """The stale metric is zero while both real public consumers read CHECKs.

    Native transport is neutral; receipt/config selection, report parsing,
    Step9 publication and the retained Step14 writer/consumer remain real.
    """
    from test_step14_report_producer_authorship import (
        project_fixture, actual_cli, digest, put as write_json, ga)

    accepted = critical in ('clean', 'tristate', 'elaborate')
    with monkeypatch.context() as patch:
        result, _, published, folder = synth_case(
            tmp_path / 'step9', patch, ceiling='10x10', critical=critical)
    assert json.loads((folder / 'state_out.json').read_text())['metrics']['synthesis__check_error__count'] == 0

    # Model an already selected receipt from an old image whose counter only
    # read pre_synth_chk. These are original transport fixture bytes, not a
    # newly fabricated measurement or an edit to any real project evidence.
    project, _ = project_fixture(tmp_path / 'step14')
    folder = project / 'phase3/librelane/02-yosys-synthesis'
    cfg, pre, final = _critical_reports(critical)
    native_config = json.loads((folder / 'config.json').read_text())
    native_config.update(cfg)
    write_json(folder / 'config.json', native_config)
    resolved = project / 'phase3/librelane/synthesis_resolved.json'
    write_json(resolved, native_config)
    (folder / 'reports/pre_synth_chk.rpt').write_text(pre)
    (folder / 'reports/chk.rpt').write_text(final)
    receipt_path = folder / 'vibeic_receipt.json'
    receipt = json.loads(receipt_path.read_text())
    receipt['input']['config'] = digest(resolved)
    write_json(folder / 'input_fingerprint.json', receipt['input'])
    receipt['sha256'] = {str(path.relative_to(folder)): digest(path)
                         for path in folder.rglob('*')
                         if path.is_file() and path != receipt_path}
    write_json(receipt_path, receipt)
    sidecar = project / 'phase2/stage2/synth/synth_inputs.json'
    binding = json.loads(sidecar.read_text())
    binding['librelane_synthesis']['receipt_sha256'] = digest(receipt_path)
    write_json(sidecar, binding)
    if critical == 'missing_final':
        (folder / 'reports/chk.rpt').unlink()
    elif critical == 'changed_final':
        (folder / 'reports/chk.rpt').write_text(final + '# changed after receipt\n')
    cp, report = actual_cli(project, 'yosys_script_template_check',
                            project / 'retained_check.json', ga.ROLE_PRODUCER)
    observation = {'case': critical, 'step9_status': result.status,
                   'step9_detail': result.detail, 'step14_rc': cp.returncode,
                   'step14_report': report}
    (project / 'critical_check_observation.json').write_text(
        json.dumps(observation, indent=2) + '\n')
    assert (result.status == 'PASS') == accepted, observation
    assert bool(published) == accepted
    assert (cp.returncode == 0) == accepted, observation
    assert (report['verdict'] == 'PASS') == accepted, observation
    if critical in ('final_bad', 'pre_bad'):
        assert 'critical CHECK errors' in result.detail
        assert 'critical CHECK errors' in str(report), report
    elif critical in ('missing_final', 'changed_final'):
        assert 'chk.rpt' in result.detail and 'chk.rpt' in str(report)


@pytest.mark.parametrize('ceiling,damage,expected', [
    (None, None, 'PASS'), ('0.5x0.5', None, 'FAIL'),
    (None, 'pdk_consistency_check', 'FAIL'),
    (None, 'area_report_conflicting', 'FAIL'),
    (None, 'measurement_missing', 'NOT_MEASURED'),
])
def test_default_native_synthesis_completes_without_claiming_absent_ppa_target(
        tmp_path, monkeypatch, ceiling, damage, expected):
    import flow109_frontend as frontend
    token = frontend._ACTIVE.set({'project': (tmp_path / 'project').resolve(),
                                 'context': {'runtime_tool_uses': None}})
    try:
        result, calls, published, folder = synth_case(tmp_path, monkeypatch,
                                                     ceiling=ceiling, damage=damage)
    finally:
        frontend._ACTIVE.reset(token)
    assert result.status == expected, result.detail
    if expected == 'PASS':
        assert result.extras['completed'] is True
        assert result.extras['default_row_status'] == 'PASS'
        assert result.extras['ppa_acceptance'] == 'NOT_MEASURED'
        assert [c for c in calls if c in CHECKS] == CHECKS
        assert len(published) == 1
    else:
        assert not published
