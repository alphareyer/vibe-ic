"""Synthesis strategy evidence cannot become a routed sign-off selection."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _ppa import synthesis


def report(path, *, stage='post_route', lec='PROVEN', area='PASS',
           wns=1, tns=0, cell_area=100, power=None):
    metrics = {'timing__setup__ws': {'status': 'MEASURED', 'value': wns},
               'timing__setup__tns': {'status': 'MEASURED', 'value': tns},
               'design__instance__area': {'status': 'MEASURED', 'value': cell_area}}
    if power is not None:
        metrics['power__total'] = {'status': 'MEASURED', 'value': power}
    path.write_text(json.dumps({'verdict': 'PASS', 'scope': {'stage': stage, 'corner': 'ss'},
                                'lec': lec, 'area_budget': area, 'metrics': metrics}))
    return path


def test_nine_tool_strategies_and_clock_gate_widths_are_declared():
    assert len(synthesis.STRATEGIES) == 9
    assert synthesis.STRATEGIES[0] == 'AREA 0'
    assert synthesis.STRATEGIES[-1] == 'DELAY 4'
    assert synthesis.CLOCK_GATE_WIDTHS == (None, 4, 8, 16)
    cfg = synthesis.strategy_config({'VERILOG_FILES': ['in.v']}, 'DELAY 4', 8)
    assert cfg['SYNTH_STRATEGY'] == 'DELAY 4'
    assert cfg['SYNTH_CLOCKGATE_MIN_WIDTH'] == 8
    with pytest.raises(synthesis.ll.Refusal):
        synthesis.strategy_config({}, 'unknown')


def test_pre_pnr_proxy_and_unproved_lec_cannot_select(tmp_path):
    first = report(tmp_path / 'first.json', stage='pre_pnr')
    second = report(tmp_path / 'second.json', lec='NOT_MEASURED')
    out = tmp_path / 'selection.json'
    result = synthesis.select_postroute({'first': first, 'second': second}, out)
    assert result['selection'] == 'UNDETERMINED'
    assert result['reason'] == 'LL_POSTROUTE_FEASIBILITY'


def test_routed_same_scope_measurements_select_and_power_is_required_for_clock_gating(tmp_path):
    first = report(tmp_path / 'first.json', wns=1, cell_area=100)
    second = report(tmp_path / 'second.json', wns=2, cell_area=90)
    out = tmp_path / 'selection.json'
    arms = {'first': first, 'second': second}
    assert synthesis.select_postroute(arms, out)['selection'] == 'second'
    assert synthesis.select_postroute(arms, out, require_power=True)['reason'] == 'LL_POSTROUTE_POWER_NOT_MEASURED'
    report(first, wns=1, cell_area=100, power=5)
    report(second, wns=2, cell_area=90, power=4)
    assert synthesis.select_postroute(arms, out, require_power=True)['selection'] == 'second'
