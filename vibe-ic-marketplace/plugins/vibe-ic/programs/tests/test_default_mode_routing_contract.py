"""The 70-row Default policy is caller-owned; Controller remains Ultra-only."""
from pathlib import Path

import execution_modes as em
import execution_policy as policy


def test_default_mode_covers_every_canonical_row_without_controller(tmp_path, monkeypatch):
    portfolio = em.load_portfolio()
    flow_sha, requirements = em._canonical_flow_requirements()
    ids = list(requirements)
    assert len(ids) == 70
    assert [row['id'] for row in portfolio['steps']] == ids
    route = portfolio['meta']['default_mode']
    assert route == {
        'status': 'COMPLETE_ROUTING',
        'execution': 'canonical-producer-first',
        'step_count': 70,
        'controller': 'ultra-only',
        'default_tool_selection':
            'librelane-then-openroad-then-other-or-canonical-program',
        'runtime_evidence': 'PER_INVOCATION',
        'whole_flow_verdict': 'NOT_CLAIMED',
    }
    assert flow_sha == portfolio['meta']['canonical_flow_sha256']
    assert all(row['current_default']['source']['canonical_step_id'] == row['id']
               for row in portfolio['steps'])
    assert all(row['current_default']['source']['programs']
               for row in portfolio['steps'])

    project = tmp_path / 'subject'
    project.mkdir()
    def trap(*args, **kwargs):
        raise AssertionError('Default invoked a policy producer preparation hook')
    monkeypatch.setattr(policy, '_prepare_step9', trap)
    monkeypatch.setattr(policy, '_ensure_default_prelayout', trap)
    monkeypatch.setattr(em.Controller, 'run',
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError('Default called Controller.run')))
    monkeypatch.setattr(em.Controller, 'adopt',
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError('Default called Controller.adopt')))
    runtime = {
        'project': project.resolve(),
        'policy': {'mode': 'default'},
    }
    monkeypatch.setattr(policy, '_ordinary_runtime', runtime)
    for step_id in ids:
        assert policy.dispatch_fixed_step(project, step_id) is None, step_id
    assert policy.dispatch_ordinary_rows(project, ids, 'all',
                                         lambda detail, extras: None) is None
