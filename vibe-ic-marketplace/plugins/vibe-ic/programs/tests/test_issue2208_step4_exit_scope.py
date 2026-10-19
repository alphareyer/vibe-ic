"""Execute the actual coordinator; unrelated tools are bounded recording stubs."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import design_one_shot_runner as runner


@pytest.mark.parametrize('exit_step', ['2', '4', None])
def test_main_scopes_step4_producers_and_real_consumer(tmp_path, monkeypatch, exit_step):
    docs = tmp_path / 'phase1/generated_docs'
    docs.mkdir(parents=True)
    for n in range(1, 14):
        (docs / f'L{n}.json').write_text('{}')
    calls = []
    for name in list(vars(runner)):
        if not name.startswith('step_') or name == 'step_step4_functional_evidence':
            continue
        def stub(*args, _name=name, **kwargs):
            calls.append(_name)
            # R-0915-85 — the stub stands for "some other step ran and is not
            # this test's subject", which was `SKIP`. That is `NOT_APPLICABLE`
            # -- declared out of this case's scope -- and NOT `NOT_MEASURED`,
            # which would make every stubbed row a hole and put the whole run
            # off PASS for a reason the fixture invented.
            result = runner.StepResult(
                _name.removeprefix('step_'), "NOT_APPLICABLE", 0.0,
                'unrelated tool stub',
                declared_by='this test stubs every step but its subject')
            return [result] if _name == 'step_dft_lec_chain' else result
        monkeypatch.setattr(runner, name, stub)
    monkeypatch.setattr(runner._spf, 'gate', lambda project, owner, site, refuse, fn, *a, **kw:
                        fn(*a, **{k: v for k, v in kw.items() if not k.startswith('_preflight')}))
    monkeypatch.setattr(runner._pl, 'emit_final_summary', lambda *a, **kw: False)
    published = {}

    def publish(project, programs, owner, summary, name, only_steps=None):
        # `only_steps` is R-0924-1's bounded steps view: a run that declared a
        # window refreshes its own step rows and carries the rest. Recorded
        # here so this test -- which already drives main() with and without a
        # window -- also witnesses that the window reaches the view.
        out = tmp_path / name
        out.write_text(json.dumps(summary))
        published['only_steps'] = only_steps
        return {}, out
    monkeypatch.setattr(runner._pl, 'publish_report_then_steps_view', publish)
    argv = ['runner', str(tmp_path), '--skip-hardware', '--skip-phase3', '--skip-analog',
            '--max-rtl-repair-retries', '0']
    if exit_step:
        argv += ['--exit-step', exit_step]
    monkeypatch.setattr(sys, 'argv', argv)
    rc = runner.main()
    report = json.loads((tmp_path / 'phase2_one_shot.json').read_text())
    rows = {row['name']: row for row in report['steps']}
    # R-0924-1: the declared window bounds the finalize tail, and says so.
    if exit_step:
        assert published['only_steps'], (
            'a run declaring --exit-step must bound the steps view to its own '
            'rows; the view was rebuilt for the whole flow')
        assert isinstance(published['only_steps'], set)
        assert report['declared_window']['exit_step'] == exit_step
        assert report['declared_window']['flags'] == [f'--exit-step {exit_step}']
        disclosed = {d['refresh']: d['kind']
                     for d in report['bounded_disclosures']}
        assert disclosed.get('final_audit') == 'skipped', disclosed
        assert disclosed.get('emit_final_summary') == 'skipped', disclosed
        assert disclosed.get('steps_view') == 'narrowed', disclosed
        assert rows['final_audit']['status'] == 'NOT_APPLICABLE', rows['final_audit']
    else:
        assert published['only_steps'] is None, (
            'an unbounded run must rebuild the whole steps view')
        assert 'declared_window' not in report
        assert 'bounded_disclosures' not in report
    if exit_step == '2':
        for name in ['sim', 'reference_tb', 'step4_functional_evidence', 'verilator_coverage']:
            assert rows[name]['status'] == 'NOT_APPLICABLE', rows[name]
        assert not any(name in calls for name in ['step_full_stack_tb_gen', 'step_reference_tb',
                                                  'step_professional_tb_gen', 'step_verilator_coverage'])
        assert rc == 0, report
    else:
        assert 'step_reference_tb' in calls
        assert 'step_full_stack_tb_gen' in calls
        assert rows['step4_functional_evidence']['status'] == 'FAIL', rows
        assert rc == 1


_NEUTRAL_STEP4_OUTPUTS = (
    'phase2/stage1/sim/results.xml',
    'phase2/stage1/sim_professional/neutral_logic/results.xml',
    'phase2/stage1/sim_professional/l10_unit_tb/results.xml',
    'reports/phase2/coverage/coverage_actual.json',
    'reports/phase2/coverage/coverage_verilator.json',
    'phase2/stage1/sim_professional/neutral_logic/cocotb_run.log',
    'step4_execution.json',
)


def _neutral_selected_step4(root, inputs):
    """A real finite Python subprocess, not simulator/native qualification."""
    root = Path(root)
    def write(rel, value):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value))
    plan_rel = 'phase2/stage1/sim_professional/neutral_logic/verification_plan.json'
    write(plan_rel, (Path(inputs) / plan_rel).read_text())
    junit = '<testsuite tests="1" failures="0" errors="0"><testcase name="neutral"/></testsuite>\n'
    for rel in _NEUTRAL_STEP4_OUTPUTS[:3]:
        write(rel, junit)
    write(_NEUTRAL_STEP4_OUTPUTS[3], {'verdict': 'PASS', 'tests': 1})
    write(_NEUTRAL_STEP4_OUTPUTS[4], {'verdict': 'PASS', 'totals': {'line': {'pct': 100}}})
    write(_NEUTRAL_STEP4_OUTPUTS[5], 'neutral process completed\n')
    write('reports/phase2/gates/professional_tb.json',
          {'status': 'PASS', 'functional_mismatch': False})
    write('reports/phase2/sim/l10_execution.json',
          {'schema': 'vibeic.l10_execution.v1', 'producer': 'neutral-subprocess',
           'cases': [{'id': 'neutral', 'verdict': 'PASS', 'sim_executed': True}]})
    write('phase2/stage1/sim_professional/neutral_logic/random-measurement.json',
          {'instrument': 'neutral-subprocess', 'value': 1})
    write('step4_execution.json', {
        'schema': 'execution_step4/1',
        'functional': {'verdict': 'PASS', 'rc': 0,
            'junit': str(root / _NEUTRAL_STEP4_OUTPUTS[1]),
            'log': str(root / _NEUTRAL_STEP4_OUTPUTS[5])},
        'coverage': {'verdict': 'PASS'},
        'scenario_execution': {'sim_executed': True, 'passed': 1, 'failed': 0, 'errored': 0}})


def _validate_neutral_selected_step4(outputs, binding):
    """Keep real Core/value proof; substitute only unrelated FCC native leaves."""
    from dataclasses import replace
    from types import SimpleNamespace
    import execution_step4 as step4
    import _flow_yaml
    # Load the real canonical registry before the bounded neutral leaf context;
    # its module constants must never cache the test's miniature Step4 row.
    import execution_frontend_providers
    import flow_compliance_check as flow
    from programs.tests import test_execution_modes as H
    truth = H.validate_text(outputs, binding)
    contract = {'id': '4', 'required_outputs': list(_NEUTRAL_STEP4_OUTPUTS),
                'gate': {'program_exit_zero': 'transform .'}}
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(_flow_yaml, 'load', lambda: {'steps': [contract]})
        patch.setattr(flow, 'check_step', lambda *a, **k:
                      SimpleNamespace(status='PASS', reasons=['neutral source-only leaf']))
        patch.setattr(flow, '__check_program_exit_zero', lambda *a, **k:
                      SimpleNamespace(passed=truth.verdict == 'PASS',
                                      exit_code=0 if truth.verdict == 'PASS' else 1))
        patch.setattr(flow, '_command_json_report', lambda *a, **k: {'verdict': truth.verdict})
        evidence = step4.validate(outputs, binding, ('transform',))
    artifacts = dict(evidence.outputs, **truth.outputs)
    return replace(evidence, verdict=truth.verdict, outputs=artifacts,
                   metrics={'source_boundary': 1.0})


@pytest.mark.parametrize('case', [
    'ultra-adopted', 'default', 'consumer-fails', 'report-missing', 'report-changed'],
    ids=['selected-producers-once', 'default-original-producers', 'required-consumer-fail',
         'missing-selected-report-refused', 'changed-selected-report-refused'])
def test_main_reuses_only_real_adopted_step4_producers(tmp_path, monkeypatch, case):
    """Real main/Controller/adopt/Journal; no EDA, IC or measured leaf mocked P."""
    from dataclasses import replace
    import execution_modes as em
    import execution_policy as policy
    from execution_provider_catalog import implementation_closure
    from programs.tests import test_execution_modes as H
    from programs.tests import test_execution_policy_choice_wait as W
    project, _, _, runtime = W._runtime(tmp_path, monkeypatch, wait_s=2)
    docs = project / 'phase1/generated_docs'
    docs.mkdir(parents=True)
    for n in range(1, 14):
        (docs / f'L{n}.json').write_text('{}')
    (docs / 'L10_TEST_CASES.json').write_text('{}')
    (docs / 'L12_BEHAVIORAL_SEQUENCES.json').write_text('{}')
    rtl = project / 'phase2/stage1/rtl/neutral_logic.v'
    rtl.parent.mkdir(parents=True)
    rtl.write_text('module neutral_logic; endmodule\n')
    bundle = project / 'phase2/stage1/sim_professional/neutral_logic'
    bundle.mkdir(parents=True)
    plan_path = bundle / 'verification_plan.json'
    plan_path.write_text(json.dumps({'random_goal_plan': {
        'measurement_file': 'random-measurement.json', 'attempt': 'issued-attempt'}}))
    original_plan = plan_path.read_bytes()
    arm = H.adapter('a')
    here = Path(__file__).resolve()
    source = dict(arm.source_files)
    source[str(here)] = em.digest(here)
    source.update({str(path): em.digest(path) for path in em._source_closure(source)})
    cwd = project / 'reports/execution/wait-test-invocation/4/a/outputs'
    source.update({str(path): em.digest(path)
                   for path in implementation_closure(here, cwd=cwd)})
    required = ('value.txt', 'measurement.json', *_NEUTRAL_STEP4_OUTPUTS)
    arm = replace(arm, step_id='4', tool_id='frontend-worker', source_files=source,
        components=arm.components + (em.Component('neutral-step4-producer',
            (str(Path(sys.executable).resolve()), str(here), '--neutral-step4', '{outputs}', '{inputs}')),),
        required_outputs=required, output_contract={rel: (rel,) for rel in required},
        objective={'parameters_from': 'issued_manifest'}, validate=_validate_neutral_selected_step4)
    registry = em.Registry(); registry.register(arm)
    controller = em.Controller(registry, em.Budget(1, 512), {
        'meta': {'test_only': True}, 'steps': [{'id': '4',
        'mandatory_gate_programs': ['transform'], 'required_output_contract': list(required)}]})
    runtime.update(registry=registry, controller=controller, parameters={'top': 'neutral_logic'})
    # The input plan is real source and must be staged unchanged, not rebuilt.
    monkeypatch.setattr(policy, '_fixed_inputs', lambda *a, **k: {
        'text.txt': project / 'source.txt',
        str(plan_path.relative_to(project)): plan_path})
    pending = policy.dispatch_fixed_step(project, '4')
    assert pending['status'] == 'AWAITING_AI_SELECTION'
    context = runtime['contexts']['4']; run = runtime['runs']['4'][0]
    choice = H.choice(context, run, 'a')
    response = tmp_path / 'choice.json'
    response.write_text(json.dumps(choice))
    runtime['policy']['choice'] = str(response)
    if case == 'default':
        runtime['policy']['mode'] = 'default'
    monkeypatch.setattr(runner, '_bootstrap_execution_policy', lambda *a, **k: runtime)
    calls = []
    duplicates = {'step_l10_unit_tb_gen', 'step_l10_unit_tb_run',
                  'step_professional_tb_gen', 'step_verilator_coverage'}
    def measured_stub(*args, _name, **kwargs):
        calls.append(_name)
        if _name in duplicates:
            if _name == 'step_professional_tb_gen':
                plan_path.write_text('{"random_goal_plan":{"attempt":"second-producer"}}')
            if _name == 'step_l10_unit_tb_run':
                p = project / _NEUTRAL_STEP4_OUTPUTS[2]
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text('overwritten by duplicate unit producer\n')
        return runner.StepResult(_name.removeprefix('step_'), 'NOT_APPLICABLE', 0.0,
                                 'unrelated neutral recorder', declared_by='neutral source-only fixture')
    for name in list(vars(runner)):
        if not name.startswith('step_'):
            continue
        def stub(*args, _name=name, **kwargs):
            result = measured_stub(*args, _name=_name, **kwargs)
            return [result] if _name == 'step_dft_lec_chain' else result
        monkeypatch.setattr(runner, name, stub)
    def consumer(current, *args, **kwargs):
        calls.append('required-consumer')
        failed = case == 'consumer-fails'
        return runner.StepResult('step4_functional_evidence', 'FAIL' if failed else 'PASS',
                                 0.0, 'neutral required consumer value=' + str(not failed))
    monkeypatch.setattr(runner, 'step_step4_functional_evidence', consumer)
    def preflight(current, owner, site, refuse, fn, *args, **kwargs):
        if site == 'sim':
            # Actual ordinary dispatcher performs real pending->adopt->import->verify.
            dispatched = policy.dispatch_ordinary_site(current, owner, site, refuse)
            if dispatched is not None:
                report = project / 'reports/phase2/sim/l10_execution.json'
                if case == 'report-missing':
                    report.unlink(missing_ok=True)
                elif case == 'report-changed':
                    report.parent.mkdir(parents=True, exist_ok=True)
                    report.write_text('{"producer":"old-current","verdict":"PASS"}')
                return dispatched
        return fn(*args, **{k: v for k, v in kwargs.items() if not k.startswith('_preflight')})
    monkeypatch.setattr(runner._spf, 'gate', preflight)
    monkeypatch.setattr(runner, '_phase2_needs_logical_pad_top', lambda *a: False)
    monkeypatch.setattr(runner._pl, 'emit_final_summary', lambda *a, **k: False)
    def publish(current, programs, owner, summary, name, only_steps=None):
        out = tmp_path / name;out.write_text(json.dumps(summary));return {}, out
    monkeypatch.setattr(runner._pl, 'publish_report_then_steps_view', publish)
    monkeypatch.setattr(sys, 'argv', ['runner', str(project), '--top-name', 'neutral_logic',
        '--entry-step', '4', '--exit-step', '4', '--skip-hardware', '--skip-phase3',
        '--skip-analog', '--max-rtl-repair-retries', '0'])
    rc = runner.main()
    report = json.loads((tmp_path / 'phase2_one_shot.json').read_text())
    rows = {r['name']: r for r in report['steps']}
    assert all(name in calls for name in ('step_full_stack_functional_tb', 'step_reference_tb',
        'step_analog_acceptance_tb_gen', 'step_analog_acceptance_tb_run', 'required-consumer')), calls
    if case == 'default':
        assert duplicates.issubset(calls), calls
        assert plan_path.read_bytes() != original_plan
        assert not (run / 'adoption.json').exists()
    else:
        adopted = json.loads((run / 'adoption.json').read_text())
        assert adopted['status'] == 'ADOPTED'
        observed_duplicates = sorted(duplicates.intersection(calls))
        assert observed_duplicates == [], (
            'already-adopted Step4 producers executed again', observed_duplicates)
        assert plan_path.read_bytes() == original_plan
        assert 'step_full_stack_tb_gen' in calls, calls
        if case in ('report-missing', 'report-changed'):
            assert rc != 0 and rows['l10_unit_tb_run']['status'] == 'NOT_MEASURED', report
        else:
            assert (project / _NEUTRAL_STEP4_OUTPUTS[2]).read_text().startswith('<testsuite')
            assert rc == (1 if case == 'consumer-fails' else 0), report
            assert rows['step4_functional_evidence']['status'] == ('FAIL' if case == 'consumer-fails' else 'PASS')
            assert controller.verify_adoption(context, run)['status'] == 'ADOPTED'
    (tmp_path / 'CONTINUATION_OBSERVATION.json').write_text(json.dumps({
        'case': case, 'calls': calls, 'actual_main_rc': rc,
        'plan_unchanged': plan_path.read_bytes() == original_plan,
        'row_statuses': {k: v['status'] for k, v in rows.items()},
        'scope': 'real coordinator/Core/adopt/Journal with finite neutral Python process; native/FCC measurement leaves and unrelated tools are explicit test seams'}, indent=2))


if __name__ == '__main__':
    if len(sys.argv) != 4 or sys.argv[1] != '--neutral-step4':
        raise SystemExit(2)
    _neutral_selected_step4(sys.argv[2], sys.argv[3])
