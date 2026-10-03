"""Focused runner schedule controls; no analog/native verdict is measured.

The checks prove dispatch ordering and test-producer timing only. They do not
establish a typed-current A7-to-A8 consumer binding; that remains unresolved.
"""
import json
import os
from pathlib import Path
import sys
import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(PROGRAMS.parent))

import analog_one_shot_runner as runner
import execution_policy as policy
from programs.tests import test_execution_receipt_chain as live


@pytest.fixture(autouse=True)
def isolated_execution(monkeypatch):
    saved_runtime = policy._ordinary_runtime
    for name in (policy.ENV, policy._CAPABILITY_FD_ENV,
                 'VIBEIC_EXECUTION_AUTH_SOCKET'):
        monkeypatch.delenv(name, raising=False)
    policy._ordinary_runtime = None
    yield
    for proc, fd, channel in live._launchers:
        channel.close()
        proc.wait(timeout=10)
        if proc.stderr:
            proc.stderr.close()
        try:
            os.close(fd)
        except OSError:
            pass
    live._launchers.clear()
    policy._ordinary_runtime = saved_runtime


def _source_regions():
    source = Path(runner.__file__).read_text()
    body = source[source.index("    if execution.request()['mode'] == 'ultra':"):]
    ultra, default = body.split("    else:\n        # Default deliberately", 1)
    default = default.split("    # q5 — A9's co-simulation", 1)[0]
    return ultra, default


def test_two_block_schedule_is_ultra_step_major_and_default_block_major():
    ultra, default = _source_regions()
    ultra_steps = ('"A1"', '"A2"', '"A3"', '"A4"', '"A5"',
                   '"A6"', '"A7"', '"A8"', '"A9"')
    positions = [ultra.index(step) for step in ultra_steps]
    assert positions == sorted(positions)
    # Ultra gets all upstream opportunities first, then each unified row gets
    # its whole block population before the next row.
    assert ultra.count('for blk in blocks:') == 5
    default_steps = ('"A1"', '"A2"', '"A3"', '"A4"', '"A5"',
                     '"A6"', '"A7"', '"A8"', '"A9"')
    positions = [default.index(step) for step in default_steps]
    assert positions == sorted(positions)
    assert default.count('for blk in blocks:') == 1
    # A one-element declared list follows the same loops; no second runner or
    # special multi-block-only state is introduced.
    assert 'for blk in blocks:' in ultra


def _run_main_schedule(tmp_path, blocks, mode, monkeypatch):
    project = tmp_path / 'project'
    docs = project / 'phase1/generated_docs'
    docs.mkdir(parents=True)
    (docs / 'L1_DATASHEET.json').write_text('{}\n')
    (docs / 'L5_ADI_SPEC.json').write_text(json.dumps({'analog_blocks': blocks}))
    assert not (project / 'phase3/analog').exists()
    if mode == 'ultra':
        live.real_entry('IC', 'ultra', project)

    # This control measures runner scheduling only. Step-preflight is outside
    # scope; preserve the real runner call sites while directly dispatching
    # their bounded step_for_block producer spy.
    def gate(project, runner_name, site, refusal_factory, fn, *args, **kwargs):
        kwargs.pop('_preflight_note', None)
        return fn(*args, **kwargs)
    monkeypatch.setattr(runner._spf, 'gate', gate)
    bootstrap_order = []
    def bootstrap(p, **kwargs):
        bootstrap_order.append((p / 'phase3/analog/analog_block_list.json').is_file())
        return None
    monkeypatch.setattr(policy, 'bootstrap', bootstrap)
    if mode == 'ultra':
        import execution_analog_installation as installation
        monkeypatch.setattr(installation, 'prepare_entry_request',
                            lambda p, args: {'source_only': True})

    events = []
    a8_observations = []
    def producer(p, block, step_name, *, args=None):
        name = block['name']
        events.append((name, step_name))
        output = p / 'phase3/analog' / name
        output.mkdir(parents=True, exist_ok=True)
        if step_name in runner._AI_STEP_NAMES[:5]:
            upstream = {
                'A1_spec_extract': {'spec.json': '{}\n'},
                'A2_topology_select': {'topology.json': '{}\n',
                                       'topology.md': 'inert topology\n'},
                'A3_netlist_gen': {f'{name}.sp': '* inert netlist\n'},
                'A4_corner_sweep': {'corner_results.json': '{}\n'},
                'A5_layout': {'layout.mag': 'inert layout; not native\n'},
            }
            for rel, content in upstream[step_name].items():
                (output / rel).write_text(content)
        if step_name == 'A7_post_layout_resim':
            (output / 'pre_vs_post.json').write_text(
                json.dumps({'producer_call': f'{name}:A7', 'measured': False}) + '\n')
        if step_name == 'A8_hardmacro_gen' and mode == 'ultra':
            present = {b['name']: (p / 'phase3/analog' / b['name']
                                    / 'pre_vs_post.json').is_file()
                       for b in blocks}
            a8_observations.append(present)
            assert all(present.values()), (name, present, events)
        return runner.StepResult(step_name, name, 'NOT_MEASURED', 0.0,
                                 'source-only step producer fixture',
                                 reason_class='not_executed')
    monkeypatch.setattr(runner, 'step_for_block', producer)
    monkeypatch.setattr(runner, '_a9_cosim', lambda *a, **k: {'tier': 'NOT_MEASURED'})

    previous = sys.argv
    sys.argv = [str(runner.__file__), str(project), '--execution-mode', mode]
    try:
        rc = runner.main()
    finally:
        sys.argv = previous
    return project, rc, events, a8_observations, bootstrap_order


def test_runner_main_ultra_creates_each_a7_product_before_first_a8(tmp_path,
                                                                  monkeypatch):
    blocks = [{'name': 'alpha', 'type': 'amplifier'},
              {'name': 'beta', 'type': 'filter'}]
    project, rc, events, observations, bootstrap_order = _run_main_schedule(
        tmp_path, blocks, 'ultra', monkeypatch)
    expected = ([step for _ in blocks for step in runner._AI_STEP_NAMES[:5]]
                + [step for step in ('A6_block_pv', 'A7_post_layout_resim',
                                     'A8_hardmacro_gen', 'A9_hw_verify')
                   for _ in blocks])
    assert rc == 1
    assert [step for _, step in events] == expected
    assert bootstrap_order == [True]
    assert observations[0] == {'alpha': True, 'beta': True}
    assert (project / 'phase3/analog/alpha/pre_vs_post.json').is_file()
    assert (project / 'phase3/analog/beta/pre_vs_post.json').is_file()


def test_runner_main_default_two_block_order_and_ultra_one_block(tmp_path,
                                                                monkeypatch):
    blocks = [{'name': 'alpha', 'type': 'amplifier'},
              {'name': 'beta', 'type': 'filter'}]
    _, rc, events, observations, _ = _run_main_schedule(
        tmp_path / 'default', blocks, 'default', monkeypatch)
    assert rc == 1
    assert [step for _, step in events] == [step for _ in blocks
                                             for step in runner._AI_STEP_NAMES]
    assert observations == []

    solo = [{'name': 'solo', 'type': 'amplifier'}]
    _, rc, events, observations, _ = _run_main_schedule(
        tmp_path / 'ultra-one', solo, 'ultra', monkeypatch)
    assert rc == 1
    assert [step for _, step in events] == list(runner._AI_STEP_NAMES)
    assert observations == [{'solo': True}]
