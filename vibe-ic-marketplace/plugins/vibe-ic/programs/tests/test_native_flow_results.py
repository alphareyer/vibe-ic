"""Native outcomes are read once; missing or failed checkers never become PASS."""
import csv
import json
from pathlib import Path

from native_flow_results import collect


def fixture(tmp_path, complete=True, deferred=False):
    run = tmp_path / 'run'
    step = run / '01-checker-test'
    step.mkdir(parents=True)
    (step / 'config.json').write_text(json.dumps({'meta': {'step': 'Checker.Test'},
                                                'DESIGN_NAME': 'core'}))
    if complete:
        (step / 'state_out.json').write_text(json.dumps({'metrics': {'native_count': 0}}))
    (run / 'flow.log').write_text("Running 'Checker.Test' at '01-checker-test'…\n"
                                  + ('native failure - deferred\n' if deferred else ''))
    mapping = tmp_path / 'map.csv'
    with mapping.open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=['id', 'name', 'native', 'when', 'action'])
        writer.writeheader()
        writer.writerow(dict(id='example', name='neutral', native='Checker.Test',
                             when='always', action='native checker result'))
    return run, mapping


def test_completed_native_checker_keeps_its_metrics_without_promoting_ledger(tmp_path):
    run, mapping = fixture(tmp_path)
    result = collect(run, mapping, returncode=0, accepted_ids=['example'])
    row = result['rows'][0]
    assert row['status'] == 'PASS'
    assert row['metrics']['Checker.Test']['native_count'] == 0
    assert row['already_accepted'] is True
    assert row['global_acceptance_promoted'] is False


def test_native_failure_stays_failed(tmp_path):
    run, mapping = fixture(tmp_path, complete=False)
    result = collect(run, mapping, returncode=2)
    assert result['rows'][0]['status'] == 'FAIL'
    assert result['first_native_failure']['native_step'] == 'Checker.Test'


def test_deferred_native_failure_cannot_be_hidden_by_a_completed_state(tmp_path):
    run, mapping = fixture(tmp_path, deferred=True)
    assert collect(run, mapping, returncode=2)['rows'][0]['status'] == 'FAIL'


def test_interruption_is_not_a_design_fail_and_missing_state_is_not_pass(tmp_path):
    run, mapping = fixture(tmp_path, complete=False)
    for returncode in (0, 137):
        assert collect(run, mapping, returncode=returncode)['rows'][0]['status'] == 'NOT_RUN'
