"""Observe complete Default consumers at actual runner calls, without IC claims."""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

PROGRAMS = Path(os.environ.get('VIBEIC_COMPONENT_JOIN_PROGRAMS',
                              str(Path(__file__).resolve().parents[1])))
sys.path.insert(0, str(PROGRAMS))
import analog_one_shot_runner as analog
import design_one_shot_runner as design
import execution_backend_gates as gates
import execution_policy as policy
import phase1_one_shot_runner as phase1
import phase3_one_shot_runner as phase3
import step_output_collector as versions
import step_preflight as preflight


@pytest.fixture(autouse=True)
def default_mode(monkeypatch):
    request = {**policy.request(), 'mode': 'default'}
    monkeypatch.setattr(policy, 'request', lambda: request)
    monkeypatch.setattr(policy, '_ordinary_runtime', None)


def write(project, relative, content=b'current invocation'):
    path = project / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def saved(project, sid):
    rows = [r for r in versions._read_index(project)['steps'] if r['id'] == sid]
    assert rows, f'no invocation for Step {sid}'
    folder = project / 'steps' / rows[-1]['folder']
    return folder, json.loads((folder / 'outputs.json').read_text())


def prior(folder, relative):
    records = json.loads((folder / 'before/outputs.json').read_text())['outputs']
    item = next(r for r in records if r['rel'] == relative)
    return (folder / 'before' / item['snapshot']).read_bytes()


def reject_consumer(monkeypatch):
    seen = []
    def reject(project, row):
        seen.append(str(row['id']))
        return {'status': 'FAIL', 'step': {'id': row['id'], 'status': 'FAIL'},
                'ledger': []}
    monkeypatch.setattr(gates, 'evaluate', reject)
    return seen


@pytest.mark.parametrize('failed,status', [
    *[(sid, status) for sid in ('11', 'DT1', '12') for status in ('FAIL', 'NOT_MEASURED')],
    pytest.param(None, 'FAIL', id='all_accepted'),
    pytest.param('previous_11', 'FAIL', id='previous_rejection_ignored'),
])
def test_default_dft_chain_stops_before_next_producer_on_complete_rejection(
        tmp_path, monkeypatch, failed, status):
    """Control-only fake tools; actual chain, retention and dispatch execute."""
    calls = []
    write(tmp_path, 'phase2/stage2/synth/netlist.v', b'module top(input clk); endmodule')
    if failed == 'previous_11':
        attempt = versions.begin_step(tmp_path, '11')
        versions.finish_step(tmp_path, attempt, verdict='FAIL', result={
            'canonical_consumer': {'status': 'FAIL', 'step': {'id': '11', 'status': 'FAIL'}}})
    def consume(project, row):
        sid = str(row['id'])
        return {'status': status if sid == failed else 'PASS', 'ledger': [],
                'step': {'id': sid, 'status': status if sid == failed else 'PASS'}}
    monkeypatch.setattr(gates, 'evaluate', consume)
    monkeypatch.setattr(gates, 'contracts', lambda *a: {'control_fixture': {}})
    @preflight.retained_step('11', canonical_acceptance=True)
    def insertion(project, *args):
        calls.append('11')
        write(project, 'phase2/stage2/dft/cut_netlist.v', b'module top(input clk); endmodule')
        return design._dft_owned_result([
            design.StepResult('dft_insertion', 'PASS', 0.0, 'native control PASS')], 'fixture')
    def transition(project, *args):
        calls.append('DT1')
        write(project, 'reports/phase2/dft/transition_coverage.json', b'{"verdict":"PASS"}')
        return {'rc': 0, 'pdk': 'fixture'}
    @preflight.retained_step('12', canonical_acceptance=True)
    def post(project, *args):
        calls.append('12')
        write(project, 'phase2/stage2/synth/post_dft_netlist.v', b'module top(input clk); endmodule')
        return design._dft_owned_result([
            design.StepResult('post_dft_opt', 'PASS', 0.0, 'native control PASS')])
    def lec(project, top, container, results, **kwargs):
        calls.append('13')
        return results + [design.StepResult('lec_equivalence', 'PASS', 0.0, 'native control PASS')]
    monkeypatch.setattr(design, '_step_dft_insertion', insertion)
    monkeypatch.setattr(design, '_run_transition_atpg', transition)
    monkeypatch.setattr(design, '_step_post_dft_optimization', post)
    monkeypatch.setattr(design, 'step_lec_equivalence', lec)
    result = design.step_dft_lec_chain(tmp_path, 'top', 'unused', 'digital', full_chip=True)
    rejecting = failed in ('11', 'DT1', '12')
    expected = (['11', 'DT1', '12'][:['11', 'DT1', '12'].index(failed) + 1]
                if rejecting else ['11', 'DT1', '12', '13'])
    assert calls == expected, 'a rejecting complete gate must stop the next producer'
    if rejecting:
        assert result[-1].status == status and result[-1].extras['step_id'] == failed
        assert saved(tmp_path, failed)[1]['status'] == status
    else:
        assert [row.name for row in result] == ['dft_insertion', 'post_dft_opt', 'lec_equivalence']
        assert all(row.status == 'PASS' for row in result)
    assert result[0].status == 'PASS', 'the original native result remains unchanged'


@pytest.mark.parametrize('failed,status', [
    *[(sid, status) for sid in ('11', 'DT1', '12') for status in ('FAIL', 'NOT_MEASURED')],
    pytest.param('lightweight', 'NOT_APPLICABLE', id='lightweight_keeps_lec'),
    pytest.param('undeclared_dft', 'NOT_APPLICABLE', id='undeclared_dft_keeps_lec'),
])
def test_default_dft_native_rejection_stops_without_complete_consumer(
        tmp_path, monkeypatch, failed, status):
    """Native failure has no completed consumer; it must still stop its successor."""
    calls = []
    declared_absent = failed in ('lightweight', 'undeclared_dft')
    write(tmp_path, 'phase2/stage2/synth/netlist.v', b'module top(input clk); endmodule')
    monkeypatch.setattr(gates, 'evaluate', lambda project, row: {
        'status': 'PASS', 'ledger': [], 'step': {'id': str(row['id']), 'status': 'PASS'}})
    monkeypatch.setattr(gates, 'contracts', lambda *a: {'control_fixture': {}})
    def native_row(name, sid):
        word = 'NOT_APPLICABLE' if declared_absent else status if failed == sid else 'PASS'
        return design.StepResult(name, word, 0.0, 'native control outcome',
            declared_by='declared no DFT in control fixture' if word == 'NOT_APPLICABLE' else '',
            reason_class=design._V.ReasonClass.NOT_EXECUTED if word == 'NOT_MEASURED' else '')
    @preflight.retained_step('11', canonical_acceptance=True)
    def insertion(project, *args):
        calls.append('11')
        return design._dft_owned_result([native_row('dft_insertion', '11')], 'fixture')
    def transition(project, *args):
        calls.append('DT1')
        rc = 2 if declared_absent or failed == 'DT1' and status == 'NOT_MEASURED' else 1 if failed == 'DT1' else 0
        return {'rc': rc, 'pdk': 'fixture'}
    @preflight.retained_step('12', canonical_acceptance=True)
    def post(project, *args):
        calls.append('12')
        return design._dft_owned_result([native_row('post_dft_opt', '12')])
    def lec(project, top, container, results, **kwargs):
        calls.append('13')
        return results + [design.StepResult('lec_equivalence', 'PASS', 0.0, 'native control PASS')]
    monkeypatch.setattr(design, '_step_dft_insertion', insertion)
    monkeypatch.setattr(design, '_run_transition_atpg', transition)
    monkeypatch.setattr(design, '_step_post_dft_optimization', post)
    monkeypatch.setattr(design, 'step_lec_equivalence', lec)
    result = design.step_dft_lec_chain(tmp_path, 'top', 'unused', 'digital',
                                      full_chip=(failed != 'lightweight'))
    if declared_absent:
        assert calls == ['11', 'DT1', '12', '13']
        assert result[-1].name == 'lec_equivalence' and result[-1].status == 'PASS'
    else:
        assert calls == ['11', 'DT1', '12'][:['11', 'DT1', '12'].index(failed) + 1]
        assert result[-1].status == status
        record = saved(tmp_path, failed)[1]
        assert record['status'] == status
        assert record['result']['canonical_consumer'] is None


@pytest.mark.parametrize('status', ['FAIL', 'NOT_MEASURED'])
def test_lightweight_dft_execution_error_still_stops_lec(tmp_path, monkeypatch, status):
    """Skipping heavy DFT does not excuse a post-DFT tool that actually failed."""
    write(tmp_path, 'phase2/stage2/synth/netlist.v', b'module top(input clk); endmodule')
    monkeypatch.setattr(gates, 'evaluate', lambda project, row: {
        'status': 'PASS', 'ledger': [], 'step': {'id': str(row['id']), 'status': 'PASS'}})
    monkeypatch.setattr(gates, 'contracts', lambda *a: {'control_fixture': {}})
    monkeypatch.setattr(design, '_run_transition_atpg', lambda *a: {'rc': 2, 'pdk': None})
    @preflight.retained_step('12', canonical_acceptance=True)
    def post(project, *args):
        return design._dft_owned_result([design.StepResult(
            'post_dft_opt', status, 0.0, 'post-DFT tool execution error',
            reason_class=design._V.ReasonClass.EXECUTION_ERROR
                         if status == 'NOT_MEASURED' else '')])
    monkeypatch.setattr(design, '_step_post_dft_optimization', post)
    monkeypatch.setattr(design, 'step_lec_equivalence', lambda *a, **k:
                        pytest.fail('a post-DFT execution error cannot dispatch LEC'))
    result = design.step_dft_lec_chain(tmp_path, 'top', 'unused', 'digital', full_chip=False)
    assert result[0].status == 'NOT_APPLICABLE'
    assert result[-1].status == status
    record = saved(tmp_path, '12')[1]
    assert record['status'] == status and record['result']['canonical_consumer'] is None


@pytest.mark.parametrize('runner,site,sid', [
    ('design_one_shot_runner', 'rtl_gen', '1'),
    ('design_one_shot_runner', 'asic_sdc', '7'),
    ('design_one_shot_runner', 'yosys_synth', '9'),
    ('phase3_one_shot_runner', 'synth', '9'),
])
def test_preflight_runs_consumer_after_actual_producer_without_leaking_kwarg(
        tmp_path, monkeypatch, runner, site, sid):
    seen = reject_consumer(monkeypatch)
    monkeypatch.setattr(preflight, 'decide', lambda *a, **k: preflight.Decision(
        runner=runner, site=site, flow_steps=[sid], allow=True, verdict='READY'))
    monkeypatch.setattr(preflight, 'record', lambda *a, **k: None)
    result = SimpleNamespace(name=site, status='PASS', detail='', extras={})
    def native(project):  # No **kwargs: internal flags must not leak to tools.
        assert not seen
        write(project, 'reports/native-current.log')
        return result
    assert preflight.gate(tmp_path, runner, site, lambda *a: None,
                          native, tmp_path,
                          _preflight_canonical_acceptance=True) is result
    assert seen == [sid]
    folder, record = saved(tmp_path, sid)
    assert record['status'] == 'FAIL' and not record['canonical_pass']
    assert (folder / 'files/reports/native-current.log').read_bytes() == b'current invocation'
    assert versions.latest_pass(tmp_path, sid) is None


@pytest.mark.parametrize('incoming', [0, 1])
def test_phase1_complete_consumer_runs_after_second_track_and_keeps_first_failure(
        tmp_path, monkeypatch, incoming):
    seen = reject_consumer(monkeypatch)
    def expert(project):
        assert not seen
        write(project, 'reports/phase1/expert-track-current.json', b'{"verdict":"PASS"}')
        return 0
    monkeypatch.setattr(phase1, '_run_expert_track', expert)
    assert phase1.run_phase1_second_track(tmp_path, incoming) == incoming
    assert seen == (['D1'] if incoming == 0 else [])
    assert saved(tmp_path, 'D1')[1]['status'] == 'FAIL'
    assert versions.latest_pass(tmp_path, 'D1') is None


def test_real_fmeda_pass_cannot_mask_rejecting_complete_consumer(tmp_path, monkeypatch):
    seen = reject_consumer(monkeypatch)
    rel = 'reports/phase2/safety/fmeda_coverage.json'
    path = write(tmp_path, rel, b'previous coverage')
    calls = []
    def native(argv, **kwargs):
        current = tmp_path / argv[argv.index('--json') + 1]
        assert not current.exists() and not seen
        calls.append(Path(argv[1]).name)
        current.write_text('{"verdict":"PASS"}')
        return SimpleNamespace(returncode=0, stdout='current coverage', stderr='')
    monkeypatch.setattr(design._pr, 'run', native)
    assert design.step_fmeda_fault_injection(tmp_path).status == 'PASS'
    assert seen == ['FS1']
    assert calls == ['fmeda_fault_injection_coverage.py', 'fmeda_coverage_check.py']
    folder, record = saved(tmp_path, 'FS1')
    assert record['status'] == 'FAIL' and not record['canonical_pass']
    assert prior(folder, rel) == b'previous coverage'
    assert (folder / 'files' / rel).read_bytes() == path.read_bytes()


@pytest.mark.parametrize('name,sid', [
    ('step_signoff_metrics_aggregate', '37.4'),
    ('step_ip_release_docs_gen', '37.5ip'),
    ('step_ic_release_docs_gen', '37.5ic'),
])
def test_real_final_producer_invokes_complete_consumer_after_all_outputs(
        tmp_path, monkeypatch, name, sid):
    seen = reject_consumer(monkeypatch)
    monkeypatch.setattr(phase3, '_ordinary_phase3_row', lambda *a, **k: None)
    monkeypatch.setattr(phase3, '_canonical_step_condition', lambda *a: (True, 'IC fixture'))
    monkeypatch.setattr(phase3, '_ip_delivery_declared_na', lambda *a: (False, '', {}))
    monkeypatch.setattr(phase3, '_write_ip_release_docs_context', lambda *a, **k:
                        write(tmp_path, 'reports/ip-context.json', b'{}'))
    monkeypatch.setattr(phase3, '_emit_drc_attribution_reports', lambda *a: [])
    calls = []
    def native(label, argv, t0, **kwargs):
        assert not seen
        calls.append(argv)
        write(tmp_path, 'reports/final-producer-current.log')
        return SimpleNamespace(returncode=0, stdout='produced', stderr=''), None
    monkeypatch.setattr(phase3, '_run_producer', native)
    assert getattr(phase3, name)(tmp_path).status == 'PASS'
    assert seen == [sid]
    assert len(calls) == (2 if sid == '37.4' else 1)
    assert saved(tmp_path, sid)[1]['status'] == 'FAIL'
    assert versions.latest_pass(tmp_path, sid) is None


@pytest.mark.parametrize('rc,status', [(0, 'FAIL'), (1, 'FAIL'), (2, 'NOT_MEASURED'),
                                     (69, 'NOT_MEASURED')])
def test_real_a9_cosim_keeps_native_result_and_complete_consumer(tmp_path, monkeypatch, rc, status):
    seen = reject_consumer(monkeypatch)
    monkeypatch.setattr(analog._pr, 'run', lambda *a, **k:
                        SimpleNamespace(returncode=rc, stdout='current cosim', stderr=''))
    result = analog._a9_cosim(tmp_path, SimpleNamespace(container='transport fixture'))
    assert result['rc'] == rc
    assert seen == (['A9'] if rc == 0 else [])
    assert saved(tmp_path, 'A9')[1]['status'] == status
    assert versions.latest_pass(tmp_path, 'A9') is None


@pytest.mark.parametrize('rc,fresh,status', [(0, False, 'NOT_MEASURED'),
                                         (0, True, 'FAIL'), (1, False, 'FAIL')])
def test_actual_phase2_dt1_cannot_borrow_previous_coverage(tmp_path, monkeypatch, rc, fresh, status):
    seen = reject_consumer(monkeypatch)
    write(tmp_path, 'phase2/stage2/dft/cut_netlist.v', b'module top(input clk); endmodule')
    rel = 'reports/phase2/dft/transition_coverage.json'
    output = write(tmp_path, rel, b'previous PASS coverage')
    def native(argv, **kwargs):
        assert not output.exists(), 'previous coverage must be saved before launching'
        assert argv[argv.index('--max-faults') + 1] == '400'
        assert not seen
        if fresh:
            output.write_text('{"verdict":"PASS"}')
        return SimpleNamespace(returncode=rc, stdout='', stderr='no fresh output')
    monkeypatch.setattr(design._pr, 'run', native)
    result = preflight.retained_call(tmp_path, 'DT1', design._run_transition_atpg,
                                    tmp_path, 'clk', 'fixture-pdk', canonical_acceptance=True)
    assert result['rc'] == (2 if rc == 0 and not fresh else rc)
    assert seen == (['DT1'] if fresh and rc == 0 else [])
    folder, record = saved(tmp_path, 'DT1')
    assert record['status'] == status and not record['canonical_pass']
    assert prior(folder, rel) == b'previous PASS coverage'
    assert versions.latest_pass(tmp_path, 'DT1') is None


def test_actual_phase2_dft_chain_retains_dt1_precondition_without_native_launch(tmp_path, monkeypatch):
    write(tmp_path, 'phase2/stage2/synth/netlist.v', b'module top(input clk); endmodule')
    rel = 'reports/phase2/dft/transition_coverage.json'
    output = write(tmp_path, rel, b'previous coverage before unmet precondition')
    monkeypatch.setattr(design._pr, 'run', lambda *a, **k:
                        pytest.fail('missing cut netlist cannot launch ATPG'))
    monkeypatch.setattr(design, 'step_lec_equivalence', lambda *a, **k: a[3])
    monkeypatch.setattr(design, '_derive_dft_clock_name', lambda *a, **k: 'clk')
    result = design.step_dft_lec_chain(tmp_path, 'top', 'unused', 'digital', full_chip=False)
    assert isinstance(result, list)
    folder, record = saved(tmp_path, 'DT1')
    assert record['status'] == 'NOT_MEASURED' and not record['canonical_pass']
    assert not output.exists()
    assert prior(folder, rel) == b'previous coverage before unmet precondition'
    assert (tmp_path / 'phase2/stage2/dft/transition_atpg_not_run.json').is_file()


def test_real_step8_postcheck_consumer_rejects_after_native_pass(tmp_path, monkeypatch):
    from test_step8_post_prelayout import project
    project(tmp_path)
    seen = reject_consumer(monkeypatch)
    result = design.step_sdc_validation_post_prelayout(tmp_path, 'top')
    assert result.status == 'PASS', result.detail
    assert seen == ['8']
    assert saved(tmp_path, '8')[1]['status'] == 'FAIL'
    assert versions.latest_pass(tmp_path, '8') is None


def test_real_analog_main_reaches_each_complete_consumer_without_leaking_control_flag(
        tmp_path, monkeypatch):
    from test_step_preflight_analog_phase1 import _analog_project
    project = _analog_project(tmp_path, with_l5=True)
    # Contract migration: accepted intermediates preserve complete wiring;
    # terminal refusals are exercised by the independent actual-main controls.
    seen = []
    def accept(project, row):
        seen.append(str(row['id']))
        return {'status': 'PASS', 'step': {'id': row['id'], 'status': 'PASS'}, 'ledger': []}
    monkeypatch.setattr(gates, 'evaluate', accept)
    monkeypatch.setattr(gates, 'contracts', lambda *a: {})
    def ready(project, runner, site, **kwargs):
        return preflight.Decision(runner=runner, site=site, flow_steps=[site],
                                  allow=True, verdict='READY')
    monkeypatch.setattr(preflight, 'decide', ready)
    monkeypatch.setattr(preflight, 'record', lambda *a, **k: None)
    produced = []
    def native(project, block, name, args=None):
        produced.append(name)
        write(project, f'reports/analog/current-{name}.json', b'{"current":true}')
        return analog.StepResult(name, block.get('name'), 'PASS', 0.0, 'native fixture')
    monkeypatch.setattr(analog, 'step_for_block', native)
    monkeypatch.setattr(analog._pr, 'run', lambda *a, **k:
                        SimpleNamespace(returncode=2, stdout='no L22 scenario', stderr=''))
    monkeypatch.setattr(sys, 'argv', ['analog_one_shot_runner', str(project)])
    analog.main()
    assert produced == list(analog._AI_STEP_NAMES)
    assert seen == [f'A{i}' for i in range(1, 9)]
    for sid in seen:
        record = saved(project, sid)[1]
        assert record['status'] == 'PASS' and record['canonical_pass']
        assert versions.latest_pass(project, sid)['folder'] == record['folder']
    # A9 is complete only at the once-per-chip cosim call after all blocks.
    assert saved(project, 'A9')[1]['status'] == 'NOT_MEASURED'


def test_ultra_complete_leaf_keeps_existing_behavior_without_default_history(tmp_path, monkeypatch):
    monkeypatch.setattr(policy, 'request', lambda: {'mode': 'ultra'})
    seen = reject_consumer(monkeypatch)
    monkeypatch.setattr(phase1, '_run_expert_track', lambda *a: 0)
    assert phase1.run_phase1_second_track(tmp_path, 0) == 0
    assert not seen and not (tmp_path / 'steps/index.json').exists()


_ANALOG_ROW_CASES = [
    *[pytest.param(n, status, 'refusal', id=f'{n}block-A3-{status}')
      for n in (1, 2) for status in ('FAIL', 'NOT_MEASURED')],
    *[pytest.param(n, status, 'accepted', id=f'{n}block-accepted-{status}')
      for n in (1, 2) for status in ('PASS', 'PASS_WITH_WAIVERS', 'NOT_APPLICABLE')],
    *[pytest.param(n, 'PASS', mode, id=f'{n}block-{mode}')
      for n in (1, 2) for mode in ('previous', 'partial_fail', 'native_fail', 'last_gap')],
    pytest.param(2, 'PASS', 'fail_then_gap', id='2block-native-FAIL-before-gap'),
    pytest.param(2, 'PASS', 'subset', id='subset-existing-declaration'),
    pytest.param(2, 'PASS', 'subset_fallback', id='subset-L5-fallback-declaration'),
    *[pytest.param(1, status, mode, id=f'A9-{mode}-{status}')
      for mode, status in (('cosim_1', 'FAIL'), ('cosim_2', 'NOT_MEASURED'),
                           ('a9_refusal', 'FAIL'), ('a9_refusal', 'NOT_MEASURED'))],
]


@pytest.mark.parametrize('block_count,status,case', _ANALOG_ROW_CASES)
def test_default_analog_whole_row_acceptance_controls_next_native_call(
        tmp_path, monkeypatch, block_count, status, case):
    """Actual main/retained consumer transport; no analog measurement claim."""
    import time
    import ai_signed_judgement
    from test_step_preflight_analog_phase1 import _analog_project
    project = _analog_project(tmp_path, with_l5=True)
    declared = [{'name': f'block_{i}', 'type': 'neutral'} for i in range(block_count)]
    declaration = project / 'phase3/analog/analog_block_list.json'
    declaration.write_text(json.dumps({'blocks': declared, 'block_count': block_count}))
    subset = case in ('subset', 'subset_fallback')
    if case == 'subset_fallback':
        declaration.unlink()
        (project / 'phase1/generated_docs/L5_ADI_SPEC.json').write_text(
            json.dumps({'analog_blocks': declared}))
    selected = declared[:1] if subset else declared
    previous = {}
    for block in declared:
        relative = f"phase3/analog/{block['name']}/{block['name']}.sp"
        previous[relative] = f"* previous {block['name']}\n".encode()
        write(project, relative, previous[relative])
    if case == 'previous':
        attempt = versions.begin_step(project, 'A3')
        versions.finish_step(project, attempt, verdict='FAIL', result={
            'canonical_consumer': {'status': 'FAIL', 'step': {'id': 'A3', 'status': 'FAIL'},
                                   'ledger': []}})
    calls, consumers, cosim_calls = [], [], []
    def ready(project, runner, site, **kwargs):
        return preflight.Decision(runner=runner, site=site, flow_steps=[site],
                                  allow=True, verdict='READY')
    monkeypatch.setattr(preflight, 'decide', ready)
    monkeypatch.setattr(preflight, 'record', lambda *a, **k: None)
    monkeypatch.setattr(gates, 'contracts', lambda *a: {})
    monkeypatch.setattr(ai_signed_judgement, 'pending', lambda *a: {})
    def native(project, block, name, args=None):
        calls.append((block['name'], name))
        if name == 'A3_netlist_gen':
            write(project, f"phase3/analog/{block['name']}/{block['name']}.sp",
                  f"* current {block['name']}\n".encode())
        else:
            write(project, f"reports/analog/current-{block['name']}-{name}.json", b'{"current":true}')
        native_status = 'PASS'
        if name == 'A3_netlist_gen':
            if case in ('native_fail', 'fail_then_gap') and block == selected[0]:
                native_status = 'FAIL'
            if case in ('last_gap', 'fail_then_gap') and block == selected[-1]:
                native_status = 'NOT_MEASURED'
        return analog.StepResult(name, block['name'], native_status, 0.0, 'controlled native return',
                                 reason_class='not_executed' if native_status == 'NOT_MEASURED' else '')
    def consume(project, row):
        sid = str(row['id'])
        current = [name for name, step in calls if step == 'A3_netlist_gen']
        complete = set(current) == {block['name'] for block in declared}
        outcome = 'PASS'
        if sid == 'A3':
            outcome = status if complete and case == 'refusal' else status if complete and case == 'accepted' else (
                'FAIL' if not complete and case in ('partial_fail', 'last_gap') else
                'NOT_MEASURED' if not complete else 'PASS')
        if sid == 'A9' and case == 'a9_refusal':
            outcome = status
        consumers.append({'id': sid, 'status': outcome, 'population': current[:],
                          'complete_population': complete, 'producer_count': len(calls)})
        return {'status': outcome, 'step': {'id': sid, 'status': outcome,
                'reasons': ['controlled full-row outcome']}, 'ledger': [],
                'population': current[:], 'complete_population': complete}
    def cosim(argv, **kwargs):
        if len(argv) < 2 or Path(argv[1]).name != 'analog_a9_cosim_emit.py':
            return SimpleNamespace(returncode=0, stdout='unrelated summary transport', stderr='')
        cosim_calls.append(list(argv))
        rc = 1 if case == 'cosim_1' else 2 if case == 'cosim_2' else 0
        return SimpleNamespace(returncode=rc, stdout='controlled once-per-chip cosim', stderr='')
    monkeypatch.setattr(analog, 'step_for_block', native)
    monkeypatch.setattr(gates, 'evaluate', consume)
    monkeypatch.setattr(analog._pr, 'run', cosim)
    argv = ['analog_one_shot_runner', str(project)]
    if subset:
        argv += ['--blocks', selected[0]['name']]
    monkeypatch.setattr(sys, 'argv', argv)
    started = time.time()
    rc = analog.main()
    report = json.loads(analog._pl.report_path(project, 'analog_one_shot.json').read_text())
    current = versions.current_acceptance(project, started)
    records = [json.loads(path.read_text()) for path in (project / 'steps').glob('*/*/*/outputs.json')]
    records = [r for r in records if isinstance(r.get('result'), dict)]
    actual_declaration = json.loads(declaration.read_text())['blocks']
    value = {'case': case, 'block_count': block_count, 'consumer_status': status, 'rc': rc,
             'verdict': report['verdict'], 'calls': calls, 'consumers': consumers,
             'A4_native_count': sum(step == 'A4_corner_sweep' for _, step in calls),
             'cosim_count': len(cosim_calls), 'a9_cosim': report['a9_cosim'],
             'current_acceptance': [{'id': r['id'], 'status': r['status'], 'folder': r['folder'],
                                     'producer_rows': r['result'].get('rows'),
                                     'canonical_consumer': r['result'].get('canonical_consumer')}
                                    for r in current],
             'declared_names': [b['name'] for b in actual_declaration]}
    print('ANALOG_BARRIER_VALUE ' + json.dumps(value, sort_keys=True))
    # Native output history remains reachable after either stop or continuation.
    for relative, content in previous.items():
        copied = [folder / 'before' / item['snapshot']
                  for folder in (project / 'steps').glob('*/*/*')
                  for item in (json.loads((folder / 'before/outputs.json').read_text())['outputs']
                               if (folder / 'before/outputs.json').is_file() else [])
                  if item['rel'] == relative]
        assert any(path.read_bytes() == content for path in copied), relative
    assert {b['name'] for b in actual_declaration} == {b['name'] for b in declared}
    stopped = case == 'refusal' or case in ('native_fail', 'fail_then_gap', 'last_gap')
    if stopped:
        assert value['A4_native_count'] == 0, value
        assert len([step for _, step in calls if step == 'A3_netlist_gen']) == len(selected)
        assert value['cosim_count'] == 0, value
        assert (rc, report['verdict']) == (1, status if case == 'refusal' else 'NOT_MEASURED' if case == 'last_gap' else 'FAIL')
    else:
        assert calls == [(b['name'], step) for step in analog._AI_STEP_NAMES for b in selected], value
        assert value['cosim_count'] == 1, value
        expected = status if case in ('cosim_1', 'cosim_2', 'a9_refusal') else (
            'NOT_MEASURED' if case == 'last_gap' else 'PASS')
        assert (rc, report['verdict']) == (0 if expected == 'PASS' else 1, expected), value
    assert len([r for r in records if r['result'].get('site') == 'A3']) == len(selected)
    for record in records:
        if record['result'].get('site') == 'A3' and record['result'].get('canonical_consumer'):
            assert record['result']['rows'][0]['status'] == 'PASS'
            assert record['status'] == record['result']['canonical_consumer']['status']
    if case == 'last_gap':
        assert not any(row['status'] == 'FAIL' for row in report['steps'])
        terminal = saved(project, 'A3')[1]
        assert terminal['status'] == 'NOT_MEASURED'
        assert terminal['result']['canonical_consumer'] is None
    if case == 'refusal':
        row = next(r for r in current if r['id'] == 'A3')
        assert row['result']['canonical_consumer']['complete_population']
        assert row['status'] == status and not row['canonical_pass']
