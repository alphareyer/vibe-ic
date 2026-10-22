"""Retained byte inspection is advisory and cannot expand a bounded dispatch."""
import json
import sys
from pathlib import Path

import pytest

import step_output_collector as versions


def retained(project):
    source = project / 'input/neutral.sdc'
    source.parent.mkdir(parents=True)
    source.write_text('create_clock -period 12 [get_ports clk]\n')
    attempt = versions.begin_step(project, '1', {'input/neutral.sdc': versions._sha(source)})
    output = project / 'phase2/stage1/rtl/neutral.v'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('module neutral(input clk); endmodule\n')
    saved = versions.finish_step(project, attempt, verdict='PASS', canonical_pass=True)
    assert saved['outputs']
    return source, output


@pytest.mark.parametrize('change', ['none', 'input', 'output', 'missing', 'relocated', 'code_metadata'])
def test_retained_differences_never_select_more_steps(tmp_path, change):
    source, output = retained(tmp_path)
    historical = (tmp_path / 'steps/latest_pass.json').read_bytes()
    if change == 'input':
        source.write_text('create_clock -period 8 [get_ports clk]\n')
    elif change == 'output':
        output.write_text('corrupt\n')
    elif change == 'missing':
        output.unlink()
    elif change == 'relocated':
        source.rename(source.with_name('moved.sdc'))
    elif change == 'code_metadata':
        (tmp_path / 'plugin_version.json').write_text('{"version":"new"}\n')
    report = versions.continuation_evidence(tmp_path, ['32'])
    assert report['requested_steps'] == ['32']
    assert report['automatically_added_steps'] == []
    row = report['checkpoints'][0]
    assert row['historical_canonical_pass'] is True
    assert row['observation'] == ('unchanged' if change in ('none', 'code_metadata') else 'differences')
    assert report['retained_unchanged_steps'] == (['1'] if change in ('none', 'code_metadata') else [])
    if change not in ('none', 'code_metadata'):
        assert row['differences'] and report['unknown_impact'] == ['1']
    assert (tmp_path / 'steps/latest_pass.json').read_bytes() == historical


def test_unknown_provenance_is_not_a_restart_or_pass(tmp_path):
    report = versions.continuation_evidence(tmp_path, ['27'])
    assert report['unknown_impact']
    assert report['checkpoints'] == []
    assert report['requested_steps'] == ['27'] and report['automatically_added_steps'] == []


@pytest.mark.parametrize('entry,exit_', [('32', '32'), ('17', '21')])
def test_actual_frontdoor_prints_inventory_before_only_requested_dispatch(
        tmp_path, monkeypatch, capsys, entry, exit_):
    import vibe_ic_one_shot_runner as front
    from _route_fixture import stage_owner_route
    source, output = retained(tmp_path)
    preserved = {p: p.read_bytes() for p in (source, output)}
    stage_owner_route(tmp_path, 'ic')
    monkeypatch.setattr(front, '_capture_container_image', lambda *_: {})
    class ReachedChild(Exception):
        pass
    def dispatch(label, runner, argv, **kwargs):
        assert Path(runner).name == 'phase3_one_shot_runner.py'
        assert argv[argv.index('--entry-step') + 1] == entry
        assert argv[argv.index('--exit-step') + 1] == exit_
        reports = list((tmp_path / 'reports/orchestrator').glob('continuation_plan-*.json'))
        assert len(reports) == 1
        report = json.loads(reports[0].read_text())
        assert report['requested_steps'][0] == entry and report['requested_steps'][-1] == exit_
        assert report['automatically_added_steps'] == []
        assert report['retained_unchanged_steps'] == ['1']
        raise ReachedChild
    monkeypatch.setattr(front, '_run_phase', dispatch)
    monkeypatch.setattr(sys, 'argv', ['runner', str(tmp_path), '--route', 'ic',
        '--execution-mode', 'default', '--entry-step', entry, '--exit-step', exit_,
        '--container', '', '--no-dashboard'])
    with pytest.raises(ReachedChild):
        front.main()
    assert '[continuation] requested=' in capsys.readouterr().out
    assert {p: p.read_bytes() for p in preserved} == preserved
