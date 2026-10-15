"""Step23 writes current hold/clock verdicts before its independent audit."""
import json
import importlib
import shlex

import _flow_yaml

import pytest

import execution_backend_producers as backend
import flow_declared_producer_run as declared
import test_f15_step23_gates_read_the_tool_arm as fixture

REPORTS = {
    'hold_corner_coverage_check': 'reports/phase3/sta/hold_corner_coverage.json',
    'sta_assumed_clock_disclosure_check': 'reports/phase3/sta/assumed_clock_disclosure.json',
    'clock_target_record_agreement_check': 'reports/phase3/clock_target_record_agreement.json',
}


@pytest.mark.parametrize('damage', ['none', 'hold', 'assumed_clock', 'clock_target'])
def test_default_current_step_produces_all_three_reports_and_keeps_failure(tmp_path, monkeypatch, damage):
    project = fixture.tool_project(
        tmp_path, hold_inf=('min_ff_n40C_5v50',) if damage == 'hold' else ())
    fixture.gate_inputs(project, assumed=damage == 'assumed_clock', stamp=False)
    if damage == 'clock_target':
        target = project/'reports/phase3/clock_target_provenance.json'
        doc = json.loads(target.read_text())
        doc['period_ns'] = 25.0
        target.write_text(json.dumps(doc))
    # This is the actual per-row backend producer seam, with actual checker
    # subprocesses. Native STA writes are controlled by the shared fixture.
    result = backend._run_current_step_declared_producers(project, '23')
    rows = {row['program']: row for row in result['results']}
    for program, relative in REPORTS.items():
        assert program in rows, f'{program} was left to the auditor instead of the producer'
        row = rows[program]
        assert row['executed'] is True and row['target_exists_after'] is True, row
        assert row['authorship'] == 'producer', row
        assert (project/relative).is_file()
        assert row['target_sha256'] == declared._target_digest(project/relative)
    allowed, reason, verdict = backend._current_step_producer_authority(result, project=project)
    if damage == 'none':
        assert allowed and verdict == 'PASS', (result, reason, verdict)
        assert all(rows[program]['rc'] == 0 for program in REPORTS)
        # Invoke each unchanged checker through the actual canonical command,
        # with the audit's real role. The producer's raw report is immutable.
        definition = next(r for r in _flow_yaml.load()['steps'] if str(r['id']) == '23')
        before = {r: (project / r).read_bytes() for r in REPORTS.values()}
        with monkeypatch.context() as audit:
            audit.setenv('VIBEIC_FCC_ROLE', 'audit')
            for program, relative in REPORTS.items():
                command = next(c['program_exit_zero'] for c in definition['gate']['all_of']
                               if str(c.get('program_exit_zero', '')).startswith(program + ' '))
                args = shlex.split(command)[1:]
                args[0] = str(project)
                output = args.index('--json') + 1
                args[output] = str(project / args[output])
                assert args[output] != str(project / relative)
                assert importlib.import_module(program).main(args) == 0
                assert json.loads(open(args[output]).read())['invoked_as'] == 'audit'
        assert before == {r: (project / r).read_bytes() for r in REPORTS.values()}
        # This source fixture has no per-pin DRV capture. Exercise the real
        # complete consumer and retain that missing measurement, never mock P.
        import execution_backend_gates as gates
        full = gates.evaluate(project, definition)
        assert full['status'] != 'PASS', full
        assert before == {r: (project / r).read_bytes() for r in REPORTS.values()}
        (project / 'STEP23_SOURCE_OBSERVATION.json').write_text(json.dumps({
            'producer_census': result, 'complete_canonical_consumer': full,
            'producer_reports_unchanged': True,
            'full_IC_positive': 'NOT_MEASURED: no complete native DRV capture in this source fixture',
            'scope': 'actual typed producers and audit checkers; no native IC measurement'}, indent=2))
    else:
        failed = {'hold': 'hold_corner_coverage_check',
                  'assumed_clock': 'sta_assumed_clock_disclosure_check',
                  'clock_target': 'clock_target_record_agreement_check'}[damage]
        assert rows[failed]['rc'] == 1, rows[failed]
        assert allowed is False and verdict == 'FAIL', (result, reason, verdict)


@pytest.mark.parametrize('program', sorted(REPORTS))
def test_the_same_checker_called_by_audit_is_not_producer_evidence(tmp_path, monkeypatch, program):
    import importlib
    import _gate_authorship as authorship
    project = fixture.tool_project(tmp_path)
    fixture.gate_inputs(project)
    target = project/REPORTS[program]
    monkeypatch.setenv(authorship.ROLE_ENV, authorship.ROLE_AUDIT)
    assert importlib.import_module(program).main([str(project), '--json', str(target)]) == 0
    assert json.loads(target.read_text())['invoked_as'] == 'audit'
    assert declared._producer_target_has_content(target) is False


def test_drv_audit_uses_its_own_verdict_without_replacing_producer_evidence(tmp_path, monkeypatch):
    """Actual project judge; unavailable capture remains NM, never invented P."""
    import test_drv_signoff_judge as fixture_drv
    import drv_signoff_judge as drv
    fixture_drv._synthetic_scene_profile.__wrapped__(tmp_path, monkeypatch)
    bundle, *_ = fixture_drv._project_clean_opensta_census_bundle(tmp_path, monkeypatch)
    # The native capture data is an explicit neutral unit fixture. The real
    # project-aware scientific judge still has to accept its whole census.
    assert drv.judge(bundle, project=tmp_path)['verdict'] == 'PASS'
    source = tmp_path / 'reports/phase3/sta/drv_signoff_bundle.json'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps(bundle))
    raw = tmp_path / 'reports/phase3/sta/drv_signoff.json'
    producer_rc = drv.main([str(tmp_path), '--json', str(raw)])
    assert producer_rc == 1
    assert json.loads(raw.read_text())['verdict'] == 'NOT_MEASURED'
    original = raw.read_bytes()
    row = next(r for r in _flow_yaml.load()['steps'] if str(r['id']) == '23')
    command = next(c['program_exit_zero'] for c in row['gate']['all_of']
                   if str(c.get('program_exit_zero', '')).startswith('drv_signoff_judge '))
    args = shlex.split(command)[1:]
    args[0] = str(tmp_path)
    target = args.index('--json') + 1
    args[target] = str(tmp_path / args[target])
    assert args[target] != str(raw)
    with monkeypatch.context() as audit:
        audit.setenv('VIBEIC_FCC_ROLE', 'audit')
        assert drv.main(args) == 1
    assert raw.read_bytes() == original
    verdict = json.loads(open(args[target]).read())
    assert verdict['verdict'] == 'NOT_MEASURED'
    assert any('capture plan absent' in reason for reason in verdict['not_measured'])
    (tmp_path / 'STEP23_DRV_VERDICT_OBSERVATION.json').write_text(json.dumps({
        'neutral_project_scientific_judge': 'PASS',
        'actual_producer_main_rc': producer_rc, 'canonical_audit_result': verdict,
        'producer_report_unchanged': True,
        'scope': 'source-control census only; no native capture or whole IC PASS'}, indent=2))
