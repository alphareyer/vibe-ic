"""Cold/resume Default order controls. Synthetic transport is not IC proof."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import _flow_yaml as flow
import execution_policy as policy
import phase3_one_shot_runner as runner
import step_preflight as preflight


@pytest.fixture(autouse=True)
def default_mode(monkeypatch):
    original = policy.request
    monkeypatch.setattr(policy, 'request', lambda: dict(original(), mode='default'))
    monkeypatch.setattr(policy, '_ordinary_runtime', None)


def test_cold_order_has_no_future_dependency_and_preserves_all_legacy_ids():
    rows = flow.load()['steps']
    ids = [str(row['id']) for row in rows]
    assert len(ids) == len(set(ids)) == 70
    for row in rows:
        for upstream in row.get('blocks_on', []):
            assert ids.index(str(upstream)) < ids.index(str(row['id'])), (row['id'], upstream)
    assert runner._phase3_window_steps('32', '23') == ['32', '22', '23']
    assert runner._phase3_window_steps('37', '34') == ['37', '26.5ic', '34']
    assert runner._phase3_window_steps('37.4', '38') == ['37.4', '36', '37.5ip', '37.5ic', '38']
    by_id = {str(row['id']): row for row in rows}
    assert by_id['36']['prepares'] == [38]
    assert by_id['32']['blocks_on'] == [21, 7]
    assert by_id['22']['blocks_on'] == [32]
    assert 'gds_antenna_deck_check' not in by_id['26']['programs']
    assert 'gds_antenna_deck_check' in by_id['37']['programs']


@pytest.mark.parametrize('entry,exit_', [('26.5ic', '27'), ('34', '37'), ('23', '32')])
def test_legacy_label_backward_resume_is_refused(entry, exit_):
    with pytest.raises(ValueError, match='reversed'):
        runner._phase3_window_steps(entry, exit_)


@pytest.mark.parametrize('sid,earlier,later', [('31', '37', '36'), ('34', '37', '31'), ('36', '37.4', '37.5ic')])
def test_actual_preflight_charges_due_inputs_in_yaml_order(tmp_path, sid, earlier, later):
    due, error = preflight.due_steps('phase3_one_shot_runner', 'default_step_' + sid)
    assert error is None
    assert earlier in due and later not in due
    decision = preflight.decide(tmp_path, 'phase3_one_shot_runner', 'default_step_' + sid)
    assert decision.flow_steps == [sid]
    assert not any(item.get('from') == earlier and item['state'] == 'not-yet-due'
                   for item in decision.inputs)
    assert not decision.allow, decision


def test_stage5_and_bounded_calls_share_the_actual_row_plan():
    plan = preflight.runner_plan('phase3_one_shot_runner')
    assert dict(plan.sites)['pnr'] == flow.ordered_step_ids(execution_stages=(4,))
    assert dict(plan.sites)['pnr'][-1] == '21'
    rows = flow.ordered_step_ids(execution_stages=(5,))
    assert tuple(runner._phase3_window_steps(rows[0], rows[-1])) == rows
    assert {'DT2', 'DT3'} <= set(rows)
    assert runner._phase3_window_sites(rows[0], rows[-1]) == ['default_backend']
    pruned = preflight.exit_pruned_sites(plan.sites, '37')
    assert 'default_step_26.5ic' in pruned
    assert 'default_step_32' not in pruned


@pytest.mark.parametrize('case', ['clean', 'adopted', 'measured_fail'])
def test_real_cold_step32_does_not_require_a_future_step22_spef(tmp_path, monkeypatch, case):
    from test_default_step32_output_publication import test_current_step32_publishes_before_its_output_receipt
    test_current_step32_publishes_before_its_output_receipt(tmp_path, monkeypatch, case)
    receipts = list(tmp_path.rglob('default_steps/32/backend_result.json'))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text())
    assert receipt['verdict'] == ('FAIL' if case == 'measured_fail' else 'PASS')
    assert not list(tmp_path.rglob('*.spef'))
