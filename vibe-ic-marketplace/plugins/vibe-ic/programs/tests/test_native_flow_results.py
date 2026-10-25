"""Native outcomes are read once; missing or failed checkers never become PASS."""
import csv
import json
from pathlib import Path

import pytest

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


@pytest.mark.parametrize("destination", ["summary", "row"])
def test_cli_publication_failure_preserves_previous_report_and_native_failure(
        tmp_path, monkeypatch, destination):
    import sys
    import native_flow_results as native
    import _atomic_artefact as atomic
    run, mapping = fixture(tmp_path, complete=False)
    output = tmp_path / "summary.json"
    rows = tmp_path / "rows"
    rows.mkdir()
    target = output if destination == "summary" else rows / "example.json"
    previous = '{"previous_complete_report": true}\n'
    target.write_text(previous)
    monkeypatch.setattr(sys, "argv", ["native_flow_results.py", str(run), str(mapping),
        "--returncode", "2", "--output", str(output), "--rows-dir", str(rows)])
    replace = atomic.os.replace

    def failed_publish(source, dest):
        if Path(dest) == target:
            raise OSError("controlled publication failure")
        return replace(source, dest)

    monkeypatch.setattr(atomic.os, "replace", failed_publish)
    refused = False
    try:
        native.main()
    except OSError as exc:
        assert "controlled publication failure" in str(exc)
        refused = True
    assert target.read_text() == previous, "failed publication replaced the complete report"
    assert refused, "publication failure was swallowed"
    monkeypatch.setattr(atomic.os, "replace", replace)
    native.main()
    result = json.loads(output.read_text())
    assert result["counts"] == {"FAIL": 1}
    assert result["first_native_failure"]["native_step"] == "Checker.Test"
    assert json.loads((rows / "example.json").read_text()) == result["rows"][0]
    assert result["rows"][0]["global_acceptance_promoted"] is False
