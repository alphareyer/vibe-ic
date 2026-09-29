#!/usr/bin/env python3
"""A LibreLane tool the contract STOPPED is not a finding about the design.

`librelane_contract.run_container` (llv1 W16a) refuses `LL_TOOL_STALLED` when
a supervised tool step made no progress (container CPU and output flat for the
stall grace) and `LL_TOOL_DEADLINE` when a probe passed its deadline. Neither
says anything about the design: the tool never answered. Their consumers
booked every refusal alike: phase 3's `_fail` as a plain FAIL, analog A6 and
A7 as rc 1 with the `FAIL:` token (review wave5, W16a). Only a plain FAIL is
red; a step that did not measure is NOT_MEASURED with its reason
(`_outcome_states.py`): STALLED for a stall, BUDGET_EXHAUSTED for a probe
deadline, and the refusal's own text. A tool that ran and failed
(`LL_STEP_FAILED`) stays FAIL.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import _plugin_tree  # noqa: F401 -- puts programs/ on sys.path
import _analog_producer_common as _pc
import verdict as _V

import test_librelane_state_bridge as SB
import test_analog_a6_librelane_drc as T6
import test_analog_a7_post_layout_emit as T7
import test_prelayout_signoff_before_pnr as PRE

contract = importlib.import_module('librelane_contract')
runner = SB.runner

STOPS = [('LL_TOOL_STALLED', _V.ReasonClass.STALLED.value),
         ('LL_TOOL_DEADLINE', _V.ReasonClass.BUDGET_EXHAUSTED.value)]


def _refusing(code):
    def run_chain(*_a, **_k):
        raise contract.Refusal(code, f'probe tool: {code} detail, partial output kept')
    return run_chain


# ── phase 3: _prepare_librelane_floorplan_for_route's _fail ────────────────

def _floorplan(tmp_path, monkeypatch, code):
    project = tmp_path / 'project'
    out_dir = project / 'phase3/stage3/pnr'
    wrapper = SB.write(out_dir / 'chip_top_io.v',
                       'module chip_top(a);\n  input a;\n  core u_core (.a(a));\nendmodule\n')
    netlist = SB.write(project / 'phase3/stage2/core.v', 'module core(a);\n  input a;\nendmodule\n')
    SB.put(project / 'phase3/librelane_switch.json',
           {'steps': {'15': 'librelane', '15.5ic': 'librelane'},
            'pdk_root_host': str(tmp_path / 'pdkroot')})
    monkeypatch.setattr(contract, 'resolve_image', lambda p: SB.stated_image())
    # CR4's resolved cell-policy check has its own tests. Here the tool is
    # already admitted and the refusal must reach the StepResult unchanged.
    monkeypatch.setattr(runner, '_resolved_cell_policy',
                        lambda configs, *_a, **_k: (configs, set()))
    monkeypatch.setattr(runner, '_padring_chip_top_record', lambda p: {
        'core_module': 'core', 'chip_top_module': 'chip_top',
        'chip_top_verilog': str(wrapper.relative_to(project))})
    monkeypatch.setattr(runner, 'pnr_input_netlist', lambda p, core: (netlist, 'n', False))
    monkeypatch.setattr(runner, '_docker_exec', lambda *a, **k: (0, 'ok', ''))
    steps = ['OpenROAD.Floorplan', 'OpenROAD.PadRing', 'OpenROAD.CutRows',
             'OpenROAD.GeneratePDN', 'Odb.RemovePDNObstructions']
    monkeypatch.setattr(contract, 'flow_segment', lambda image, first, last, **k: (
        steps if last == 'Odb.RemovePDNObstructions' else steps[:2]))
    monkeypatch.setattr(contract, 'resolve_step_configs',
                        lambda project, image, pdk, step_ids, **k:
                        {s: SB._declared(tmp_path, s) for s in step_ids})
    monkeypatch.setattr(contract, 'emit_pdn_cfg',
                        lambda image, pdk, out, **k: SB.write(out, 'pdn\n'))
    monkeypatch.setattr(contract, 'run_chain', _refusing(code))
    pdk = SB._pdk(tmp_path)
    pdk.macro_lefs, pdk.macro_gds = [], []
    return runner._prepare_librelane_floorplan_for_route(
        project, pdk, 'c', out_dir, SB._deck(), {'15': 'librelane', '15.5ic': 'librelane'})


@pytest.mark.parametrize('code,reason', STOPS)
def test_phase3_books_a_stopped_tool_not_measured(tmp_path, monkeypatch, code, reason):
    result, consumer = _floorplan(tmp_path, monkeypatch, code)
    assert consumer is None
    assert result.status == 'NOT_MEASURED'
    assert result.reason_class == reason
    assert result.detail.startswith(code) and 'partial output kept' in result.detail


def test_phase3_keeps_a_tool_that_ran_and_failed_red(tmp_path, monkeypatch):
    """Control: the tool answered, and the answer was a failure."""
    result, _ = _floorplan(tmp_path, monkeypatch, 'LL_STEP_FAILED')
    assert result.status == 'FAIL'
    assert result.detail.startswith('LL_STEP_FAILED')


@pytest.mark.parametrize('code,reason', STOPS + [('LL_STEP_FAILED', '')])
def test_opt_in_synth_step_result_keeps_tool_stop(tmp_path, monkeypatch, code, reason):
    """The selected Step 9 consumer must publish the tool's refusal verdict."""
    project = tmp_path / 'synth'
    SB.put(project / 'phase3/librelane_switch.json',
           {'steps': {'9': 'librelane'}})
    SB.write(runner._pl.rtl_dir(project) / 'neutral.v',
             'module neutral(input a, output y); assign y = a; endmodule\n')
    monkeypatch.setattr(contract, 'resolve_image', lambda p: SB.stated_image())
    monkeypatch.setattr(contract, 'pdk_root_resolution', _refusing(code))
    pdk = SimpleNamespace(name='neutral', liberty=tmp_path / 'cell__tt.lib',
                          macro_libs=[], macro_lefs=[], macro_v=[])

    result = runner.step_synth(project, 'neutral', pdk, 'unused')
    assert result.name == 'synth'
    assert (result.status, result.reason_class) == (
        ('NOT_MEASURED', reason) if reason else ('FAIL', ''))
    assert code in result.detail and 'partial output kept' in result.detail


@pytest.mark.parametrize('code,reason', STOPS + [('LL_STEP_FAILED', '')])
def test_opt_in_prelayout_step_result_keeps_tool_stop(tmp_path, monkeypatch,
                                                      code, reason):
    """Step 7/8/10 must retain a stopped pre-layout probe's reason."""
    project = tmp_path / 'prelayout'
    SB.put(project / 'phase3/librelane_switch.json',
           {'steps': {'7': 'librelane', '8': 'librelane', '10': 'librelane'}})
    SB.write(project / 'input/constraints/clock.sdc',
             'create_clock -name neutral_clk -period 10 [get_ports clk]\n')
    pdk = PRE._corner_pdk(monkeypatch, str(tmp_path / 'cell__tt.lib'))
    monkeypatch.setattr(contract, 'resolve_image', lambda p: SB.stated_image())
    monkeypatch.setattr(contract, 'pdk_root_resolution', _refusing(code))

    result = runner.step_prelayout_signoff(project, 'neutral', pdk, 'unused')
    assert result.name == 'prelayout_signoff'
    assert (result.status, result.reason_class) == (
        ('NOT_MEASURED', reason) if reason else ('FAIL', ''))
    assert code in result.detail and 'partial output kept' in result.detail


@pytest.mark.parametrize('code,reason', STOPS + [('LL_STEP_FAILED', '')])
def test_step_pnr_preserves_the_pad_rows_verdict(tmp_path, monkeypatch, code, reason):
    import test_pad_connected_pdn_ring as ring_fixture
    pad, _ = _floorplan(tmp_path, monkeypatch, code)
    project = tmp_path / 'route'
    project.mkdir()
    SB.put(project / 'phase3/librelane_switch.json',
           {'steps': {'15': 'librelane', '15.5ic': 'librelane'}})
    pdk = ring_fixture._pdk(tmp_path, ring=None)
    netlist = SB.write(tmp_path / 'dut.v', 'module dut(input clk, output q); assign q=clk; endmodule\n')
    for name, value in {
        'pnr_input_netlist': lambda *a: (netlist, 'test DUT', False),
        '_v1_6_599_check_wrapper_pin_order_cfg': lambda *a: None,
        '_stage_via_legalized_tech_lef': lambda *a: {'status': 'NOT_NEEDED'},
        'set_invocation_provenance_sink': lambda *a: None,
        '_macro_supply_preroute_decision': lambda *a, **k: None,
        '_resolve_staged_silicon_sdc': lambda *a: None,
        '_liberty_drv_limits': lambda *a: {},
        '_build_auto_silicon_sdc': lambda *a, **k: '',
        '_docker_exec': lambda *a, **k: (1, '', 'no container in this test'),
        '_docker_exec_raw': lambda *a, **k: (1, '', 'no container in this test'),
        '_chip_path_requests_pad_ring': lambda *a: True,
        '_build_pnr_tcl_text': lambda **k: SB._deck(),
        '_prepare_librelane_floorplan_for_route': lambda *a, **k: (pad, None),
    }.items():
        monkeypatch.setattr(runner, name, value)
    rows = []
    pnr = runner.step_pnr(project, 'dut', pdk, 'unused', '400x400', 0.4,
                          pad_ring_results=rows)
    assert rows == [pad]
    assert pnr.extras['finding'] == 'PADRING_PREROUTE_BLOCKED'
    assert pnr.status == pad.status
    if reason:
        assert pnr.reason_class == _V.ReasonClass.UPSTREAM_REFUSED.value
        assert runner._aggregate_verdict([pad, pnr]) != 'FAIL'
    else:
        assert pnr.status == 'FAIL'
        assert runner._aggregate_verdict([pad, pnr]) == 'FAIL'


def test_phase3_keeps_the_stop_when_a_handler_names_its_own_step(tmp_path, monkeypatch):
    """The PDK-root handler books every refusal as LL_PDK_ROOT_NOT_DECLARED.
    A probe that passed its deadline there is still a stopped tool: its own
    code and reason win over the handler's name."""
    def probe_past_deadline(*_a, **_k):
        raise contract.Refusal('LL_TOOL_DEADLINE', 'docker run passed its 600 s probe deadline')
    monkeypatch.setattr(contract, 'resolve_image', lambda p: SB.stated_image())
    monkeypatch.setattr(contract, 'pdk_root_resolution', probe_past_deadline)
    result, consumer = runner._prepare_librelane_floorplan_for_route(
        tmp_path, SB._pdk(tmp_path), 'c', tmp_path / 'phase3/stage3/pnr', SB._deck(),
        {'15': 'librelane', '15.5ic': 'librelane'})
    assert consumer is None
    assert (result.status, result.reason_class) == (
        'NOT_MEASURED', _V.ReasonClass.BUDGET_EXHAUSTED.value)
    assert result.detail.startswith('LL_TOOL_DEADLINE')


# ── analog A6: LibreLane DRC arm ─────────────────────────────────────────────

@pytest.mark.parametrize('code,reason', STOPS)
def test_a6_books_a_stopped_tool_not_measured(tmp_path, monkeypatch, capsys, code, reason):
    T6.state_the_image(monkeypatch)
    stub = T6.stub.__wrapped__(tmp_path, monkeypatch)
    project = T6._project(stub)
    monkeypatch.setattr(contract, 'run_chain', _refusing(code))
    rc = T6.A6.run(project, 'blk', T6.IMAGE, None)
    assert rc == _pc.EX_ENV_REFUSED
    rec = json.loads((project / 'phase3/analog/blk/a6_librelane_drc.json').read_text())
    assert (rec['result'], rec['rule'], rec['reason_class']) == ('NOT_MEASURED', code, reason)
    err = capsys.readouterr().err
    assert err.startswith(_pc.ENV_REFUSED_TOKEN) and 'partial output kept' in err


def test_a6_keeps_a_tool_that_ran_and_failed_red(tmp_path, monkeypatch, capsys):
    T6.state_the_image(monkeypatch)
    stub = T6.stub.__wrapped__(tmp_path, monkeypatch)
    project = T6._project(stub)
    monkeypatch.setattr(contract, 'run_chain', _refusing('LL_STEP_FAILED'))
    assert T6.A6.run(project, 'blk', T6.IMAGE, None) == 1
    assert capsys.readouterr().err.startswith('FAIL:')


@pytest.mark.parametrize('code,reason', STOPS)
def test_a6_runner_keeps_the_arms_stop_reason(tmp_path, monkeypatch, code, reason):
    import analog_one_shot_runner as analog
    T6.state_the_image(monkeypatch)
    project = T6._project(tmp_path)
    (project / 'phase3/analog/analog_block_list.json').write_text(
        json.dumps({'blocks': [{'name': 'blk', 'type': 'ldo'}]}))
    SB.put(project / 'phase3/librelane_switch.json',
           {'steps': {'A6': 'librelane'}})
    record = project / 'phase3/analog/blk/a6_librelane_drc.json'
    record.write_text(json.dumps({'result': 'NOT_MEASURED', 'rule': code,
                                  'reason_class': reason}))
    monkeypatch.setattr(analog, '_a6_librelane_arm', lambda *a: {
        'rc': _pc.EX_ENV_REFUSED, 'blocking': True, 'record': str(record),
        'detail': f'{_pc.ENV_REFUSED_TOKEN} {code}: stopped'})
    real_run = analog._pr.run
    monkeypatch.setattr(analog._pr, 'run', lambda cmd, *a, **k: (
        SimpleNamespace(returncode=2, stdout='', stderr='')
        if any('drc_attribute' in str(x) for x in cmd)
        else real_run(cmd, *a, **k)))
    result = analog.step_for_block(project, {'name': 'blk', 'type': 'ldo'},
                                   'A6_block_pv', None)
    assert (result.status, result.reason_class) == ('NOT_MEASURED', reason)
    assert code in result.detail


# ── analog A7: post-layout extraction ────────────────────────────────────────

@pytest.mark.parametrize('code,reason', STOPS)
def test_a7_books_a_stopped_tool_not_measured(tmp_path, monkeypatch, capsys, code, reason):
    T7.state_the_image(monkeypatch)
    stub = T7.stub.__wrapped__(tmp_path, monkeypatch)
    project = T7._project(stub)
    monkeypatch.setattr(contract, 'run_chain', _refusing(code))
    rc = T7.A7.run(project, 'blk', 'vibeic-eda', T7.IMAGE)
    assert rc == _pc.EX_ENV_REFUSED
    rec = json.loads((project / 'phase3/analog/blk/a7_post_layout.json').read_text())
    assert (rec['result'], rec['rule'], rec['reason_class']) == ('NOT_MEASURED', code, reason)
    err = capsys.readouterr().err
    assert err.startswith(_pc.ENV_REFUSED_TOKEN) and 'partial output kept' in err


def test_a7_keeps_a_tool_that_ran_and_failed_red(tmp_path, monkeypatch, capsys):
    T7.state_the_image(monkeypatch)
    stub = T7.stub.__wrapped__(tmp_path, monkeypatch)
    project = T7._project(stub)
    monkeypatch.setattr(contract, 'run_chain', _refusing('LL_STEP_FAILED'))
    assert T7.A7.run(project, 'blk', 'vibeic-eda', T7.IMAGE) == 1
    assert capsys.readouterr().err.startswith('FAIL:')


# ── the one mapping ──────────────────────────────────────────────────────────

def test_the_contract_names_every_stop_it_raises_and_nothing_else():
    assert contract.TOOL_STOP_REASONS == dict(STOPS)
    assert contract.TIME_REFUSALS == frozenset(dict(STOPS))
    assert {c: contract.tool_stop_reason(c) for c, _ in STOPS} == dict(STOPS)
    assert contract.tool_stop_reason('LL_STEP_FAILED') is None
    assert contract.tool_stop_reason(None) is None


@pytest.mark.parametrize('boundary', ['signoff', 'step_context'])
@pytest.mark.parametrize('code,expected', [
    ('LL_TOOL_STALLED', 'LL_TOOL_STALLED'),
    ('LL_TOOL_DEADLINE', 'LL_TOOL_DEADLINE'),
    ('LL_PDK_ROOT_MISSING', 'LL_PDK_ROOT_NOT_DECLARED'),
])
def test_opt_in_root_wrapper_keeps_a_stop_code(tmp_path, monkeypatch,
                                                boundary, code, expected):
    """A PDK probe stopped before steps 22/23 or 24 must keep its own code.

    An actual missing root still gets the step's missing declaration label.
    The two wrappers are invoked through their shipped functions.
    """
    def root_refusal(*_args, **_kwargs):
        raise contract.Refusal(code, 'PDK root probe detail')

    monkeypatch.setattr(contract, 'pdk_root_resolution', root_refusal)
    monkeypatch.setattr(contract, 'resolve_image', lambda *_a: SB.stated_image())
    with pytest.raises(contract.Refusal) as got:
        if boundary == 'signoff':
            runner._librelane_signoff_run(
                tmp_path, 'top', SimpleNamespace(name='neutral'),
                extract=True, time=False)
        else:
            runner._librelane_step_ctx(tmp_path, '24', 'neutral')
    assert got.value.code == expected
