"""Current report lifecycle through neutral normal dispatch/Core/Journal."""
import json
from pathlib import Path

import pytest

from programs.tests import test_execution_policy_choice_wait as W

em, policy, H = W.em, W.policy, W.H


@pytest.mark.parametrize('case', [
    'adopted', 'choice-refused', 'import-rollback', 'verify-rollback',
    'timeout', 'measured-fail', 'not-measured',
])
def test_normal_dispatch_current_result_tracks_successful_consumer_only(
        tmp_path, monkeypatch, case):
    """Real finite text tools and authority; no EDA, IC or mocked verifier."""
    response = tmp_path / 'choice.json'
    fault = {'measured-fail': 'fail_gate', 'not-measured': 'unmeasured'}.get(case)
    arms = (H.adapter('a', fault=fault),) if fault else None
    project, controller, source, runtime = W._runtime(
        tmp_path, monkeypatch, arms=arms, response=response,
        wait_s=1 if case == 'timeout' else 2)
    observation = {'case': case}
    original = b'previous canonical output\n'
    (project / 'value.txt').write_bytes(original)
    if case in ('import-rollback', 'verify-rollback'):
        from execution_backend_snapshot import Journal
        write = Journal.write

        def write_with_fault(self, name, data):
            write(self, name, data)
            if name == 'value.txt':
                if case == 'import-rollback':
                    raise OSError('neutral publication failed after write')
                source.write_text('changed during canonical import\n')
        monkeypatch.setattr(Journal, 'write', write_with_fault)

    awaiting = []

    def respond(request_path):
        run = runtime['runs']['1'][0]
        initial = (run / 'result.json').read_bytes()
        assert json.loads(initial)['status'] == 'AWAITING_AI_SELECTION'
        awaiting.append(initial)
        decision = H.choice(runtime['contexts']['1'], run, 'b')
        if case == 'choice-refused':
            decision['receipt_sha256'] = '0' * 64
        return decision

    if case in ('timeout', 'measured-fail', 'not-measured'):
        result = policy.dispatch_fixed_step(project, '1')
        outcome = {'result': result}
    else:
        outcome, thread = W._dispatch_with_choice_writer(
            project, response, runtime, monkeypatch, respond)
        assert not thread.is_alive()
    run = runtime['runs']['1'][0]
    report = json.loads((run / 'result.json').read_text())
    versions = sorted(run.glob('result.superseded-*.json'))
    observation.update(report=report, versions=[p.name for p in versions],
                       exception=str(outcome.get('error')),
                       caller_status=(outcome.get('result') or {}).get('status'))
    (tmp_path / 'REPORTING_OBSERVATION.json').write_text(
        json.dumps(observation, indent=2))
    if case == 'adopted':
        assert 'error' not in outcome
        actual = (outcome['result']['status'], report['status'])
        assert actual == ('ADOPTED', 'ADOPTED'), ('returned and current report', actual)
        assert report == outcome['result']
        assert (project / 'value.txt').read_text() == 'ONE INPUT\n'
        assert controller.verify_adoption(runtime['contexts']['1'], run)['selected'] == 'b'
        assert len(versions) == 1 and versions[0].read_bytes() == awaiting[0]
        # Reconsumption of the same terminal result must not rewrite history.
        terminal = (run / 'result.json').read_bytes()
        repeated = policy.dispatch_fixed_step(project, '1')
        assert repeated == outcome['result']
        assert (run / 'result.json').read_bytes() == terminal
        assert sorted(run.glob('result.superseded-*.json')) == versions
    elif case in ('choice-refused', 'import-rollback', 'verify-rollback'):
        exc = outcome['error']
        if case == 'import-rollback':
            assert type(exc) is OSError
            assert str(exc) == 'neutral publication failed after write'
        else:
            assert isinstance(exc, em.Refusal)
            assert exc.code == ('AI_RECEIPT_DIGEST_MISMATCH' if case == 'choice-refused'
                                else 'CURRENT_INPUT_CHANGED')
        assert report['status'] == 'AWAITING_AI_SELECTION'
        assert (run / 'result.json').read_bytes() == awaiting[0]
        assert versions == []
        assert (project / 'value.txt').read_bytes() == original
        adoption = json.loads((run / 'adoption.json').read_text())
        assert adoption['status'] == ('REFUSED' if case == 'choice-refused' else 'ADOPTED')
    else:
        expected = {'timeout': 'AWAITING_AI_SELECTION', 'measured-fail': 'FAIL',
                    'not-measured': 'NOT_MEASURED'}[case]
        assert report['status'] == outcome['result']['status'] == expected
        assert versions == []
        assert not (run / 'adoption.json').exists()
        assert (project / 'value.txt').read_bytes() == original
        if fault:
            before = (run / 'result.json').read_bytes()
            with pytest.raises(em.Refusal, match='AI_CHOICE_INELIGIBLE'):
                policy.dispatch_fixed_step(project, '1',
                    choice=H.choice(runtime['contexts']['1'], run, 'a'))
            assert (run / 'result.json').read_bytes() == before
