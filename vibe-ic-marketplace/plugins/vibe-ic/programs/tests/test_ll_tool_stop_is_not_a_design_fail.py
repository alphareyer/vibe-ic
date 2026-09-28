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

import pytest

import _plugin_tree  # noqa: F401 -- puts programs/ on sys.path
import _analog_producer_common as _pc
import verdict as _V

import test_librelane_state_bridge as SB
import test_analog_a6_librelane_drc as T6
import test_analog_a7_post_layout_emit as T7

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
    assert {c: contract.tool_stop_reason(c) for c, _ in STOPS} == dict(STOPS)
    assert contract.tool_stop_reason('LL_STEP_FAILED') is None
    assert contract.tool_stop_reason(None) is None
