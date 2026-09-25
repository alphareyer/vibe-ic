"""Real contract consumer with EDA file writes substituted at the process edge."""
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
contract = importlib.import_module('librelane_contract')


def put(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))
    return path


def design(tmp_path):
    p = tmp_path / 'design'
    put(p / 'phase1/generated_docs/L8_TIMING_WAVEFORM.json', {
        'clock_domains': [{'role': 'primary', 'pdk_scoped_target': 'processA',
                           'period_ns': 12, 'source_pin': 'clk'}]})
    put(p / 'phase1/generated_docs/L9_INTEGRATION_SPEC.json', {'top_module': 'block'})
    put(p / 'phase1/generated_docs/L19_CONSTRAINTS_PDK.json', {'fields': {
        'constraint_declarations': [
            {'token': 'MAX_FANOUT_CONSTRAINT', 'scope': 'processA*', 'value': '4',
             'source': 'input/docs/L9.txt', 'line': 8},
            {'token': 'FP_PDN_SKIPTRIM', 'scope': None, 'value': 'true',
             'source': 'input/docs/L1.txt', 'line': 9}]}})
    put(p / 'input/submission_template/tapeout_declaration.json', {'answers': {
        'top_cell': 'chip_top', 'die_area_um': [0, 0, 100, 100],
        'core_area_um': [10, 10, 90, 90]}})
    put(p / 'phase3/stage3/pnr/pad_assignment.json', {
        'PAD_NORTH': ['u_a'], 'PAD_SITE_NAME': 'siteA',
        'PAD_CORNER_SITE_NAME': 'cornerSiteA', 'PAD_CORNER': 'cornerA',
        'PAD_FILLERS': ['fillA'], 'PAD_EDGE_SPACING': '12.5',
        'PAD_ROTATION_HORIZONTAL': 'R0'})
    return p


def test_emit_declared_values_and_sources(tmp_path):
    p = design(tmp_path)
    (p / 'phase2/stage1/rtl').mkdir(parents=True)
    (p / 'phase2/stage1/rtl/block.v').write_text('module block; endmodule')
    (p / 'phase3/stage3/pnr/chip_top_io.v').write_text('module chip_top; endmodule')
    (p / 'phase3/stage3/pnr/constraint.sdc').write_text('create_clock -period 12 clk')
    result = contract.emit_config(p, 'processA', p / 'phase3/librelane/config.json')
    assert result['CLOCK_PERIOD'] == 12
    assert result['MAX_FANOUT_CONSTRAINT'] == 4
    assert result['DIE_AREA'] == [0, 0, 100, 100]
    assert result['PAD_NORTH'] == ['u_a']
    assert result['PAD_CORNER'] == ['cornerA']
    assert result['PAD_FILLERS'] == ['fillA']
    assert result['PAD_EDGE_SPACING'] == 12.5
    assert result['PAD_ROTATION_HORIZONTAL'] == 'R0'
    assert result['PDN_SKIPTRIM'] is True
    assert result['VERILOG_FILES'][0].endswith('/block.v')
    assert result['PNR_SDC_FILE'].endswith('/constraint.sdc')
    assert 'MAX_TRANSITION_CONSTRAINT' not in result
    provenance = json.loads((p / 'phase3/librelane/config.provenance.json').read_text())
    assert all(k in provenance for k in result)


def test_invalid_declared_pad_spacing_is_refused(tmp_path):
    p = design(tmp_path)
    pads = p / 'phase3/stage3/pnr/pad_assignment.json'
    doc = json.loads(pads.read_text())
    doc['PAD_EDGE_SPACING'] = '-1'
    put(pads, doc)
    with pytest.raises(contract.Refusal, match='LL_PAD_SPACING_INVALID'):
        contract.emit_config(p, 'processA', p / 'phase3/librelane/config.json')


def test_switch_defaults_to_direct_and_rejects_bad_value(tmp_path):
    p = design(tmp_path)
    assert contract.selected_mode(p, '15.5ic') == 'direct'
    put(p / 'phase3/librelane_switch.json', {'steps': {'15.5ic': 'dual'}})
    assert contract.selected_mode(p, '15.5ic') == 'dual'
    put(p / 'phase3/librelane_switch.json', {'steps': {'15.5ic': 'fallback'}})
    with pytest.raises(contract.Refusal, match='LL_INVALID_SWITCH'):
        contract.selected_mode(p, '15.5ic')


def test_image_incapable_is_named_and_not_fallback(monkeypatch):
    monkeypatch.setattr(contract.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1))
    with pytest.raises(contract.Refusal, match='LL_IMAGE_INCAPABLE'):
        contract.image_capability('released-image')


def test_chain_resumes_success_and_reruns_failed_step(tmp_path, monkeypatch):
    p = design(tmp_path)
    source = p / 'source'
    source.mkdir()
    for name in ('a.odb', 'a.def', 'a.nl', 'a.sdc'):
        (source / name).write_text(name)
    state = put(p / 'initial.json', {k: str(source / ('a.' + k)) for k in ('odb', 'def', 'nl', 'sdc')})
    cfg = put(p / 'config.json', {'meta': {'step': 'OpenROAD.PadRing'}})
    calls = []

    def fake_run(cmd, **_):
        calls.append(cmd)
        folder = Path(cmd[cmd.index('-o') + 1])
        for key in ('odb', 'def', 'nl', 'sdc'):
            (folder / ('b.' + key)).write_text(key)
        put(folder / 'state_out.json', {k: str(folder / ('b.' + k)) for k in ('odb', 'def', 'nl', 'sdc')})
        return SimpleNamespace(returncode=0, stdout='ok', stderr='')

    monkeypatch.setattr(contract, 'image_capability', lambda *a: None)
    monkeypatch.setattr(contract.subprocess, 'run', fake_run)
    steps = [('OpenROAD.PadRing', cfg, state)]
    folder = contract.run_chain(p, 'candidate', steps)[0]
    assert len(calls) == 1
    assert 'state_out.json' in json.loads((folder / 'vibeic_receipt.json').read_text())['sha256']
    contract.run_chain(p, 'candidate', steps)
    assert len(calls) == 1
    (folder / 'state_out.json').unlink()
    contract.run_chain(p, 'candidate', steps)
    assert len(calls) == 2


def test_floorplan_accepts_netlist_only_before_it_creates_geometry(tmp_path, monkeypatch):
    p = design(tmp_path)
    netlist = p / 'source.nl.v'
    netlist.write_text('module block; endmodule\n')
    state = put(p / 'initial.json', {'nl': str(netlist)})
    cfg = put(p / 'config.json', {'meta': {'step': 'OpenROAD.Floorplan'}})

    def fake_floorplan(cmd, **_):
        folder = Path(cmd[cmd.index('-o') + 1])
        views = {}
        for key in ('odb', 'def', 'nl', 'sdc'):
            view = folder / ('block.' + key)
            view.write_text(key)
            views[key] = str(view)
        put(folder / 'state_out.json', views)
        return SimpleNamespace(returncode=0, stdout='floorplan', stderr='')

    monkeypatch.setattr(contract, 'image_capability', lambda *a: None)
    monkeypatch.setattr(contract.subprocess, 'run', fake_floorplan)
    assert contract.run_chain(p, 'candidate', [('OpenROAD.Floorplan', cfg, state)])[0].joinpath('state_out.json').is_file()


def test_tap_step_refuses_missing_geometry_input(tmp_path, monkeypatch):
    p = design(tmp_path)
    netlist = p / 'source.nl.v'
    netlist.write_text('module block; endmodule\n')
    state = put(p / 'initial.json', {'nl': str(netlist)})
    cfg = put(p / 'config.json', {'meta': {'step': 'OpenROAD.TapEndcapInsertion'}})
    monkeypatch.setattr(contract, 'image_capability', lambda *a: None)
    with pytest.raises(contract.Refusal, match='LL_STATE_MISSING'):
        contract.run_chain(p, 'candidate', [('OpenROAD.TapEndcapInsertion', cfg, state)])


def test_missing_metric_stays_unmeasured_and_cannot_win(tmp_path):
    folder = tmp_path / 'arm'
    put(folder / 'state_out.json', {'metrics': {'area': 10}})
    first = contract.judge_step(folder, ['area', 'timing'], tmp_path / 'first.json')
    assert first['verdict'] == 'NOT_MEASURED'
    assert first['metrics']['timing']['status'] == 'NOT_MEASURED'
    put(tmp_path / 'second.json', {'verdict': 'PASS', 'scope': {'step': 'same'},
                                   'metrics': {'area': {'status': 'MEASURED', 'value': 11},
                                               'timing': {'status': 'MEASURED', 'value': 2}}})
    result = contract.select_arms({'ll': tmp_path / 'first.json', 'or': tmp_path / 'second.json'},
                                  {'area': 'min', 'timing': 'max'}, tmp_path / 'selection.json')
    assert result['selection'] == 'UNDETERMINED'
    assert result['reason'] == 'LL_ARM_NOT_MEASURED'


def test_missing_report_is_unmeasured(tmp_path):
    folder = tmp_path / 'arm'
    put(folder / 'state_out.json', {'metrics': {'pads': 3}})
    result = contract.judge_step(folder, ['pads'], tmp_path / 'gate.json',
                                 {'pads': {'min': 1}}, ['pad.rpt'])
    assert result['verdict'] == 'NOT_MEASURED'
    (folder / 'pad.rpt').write_text('pad report')
    result = contract.judge_step(folder, ['pads'], tmp_path / 'gate.json',
                                 {'pads': {'min': 1}}, ['pad.rpt'])
    assert result['verdict'] == 'PASS'
    assert result['reports']['pad.rpt']['sha256'] == contract.digest(folder / 'pad.rpt')


def test_pareto_keeps_losing_arm_and_ties(tmp_path):
    for name, area, timing in [('ll', 10, 3), ('or', 12, 2)]:
        put(tmp_path / (name + '.json'), {'verdict': 'PASS', 'scope': {'step': 'same'}, 'metrics': {
            'area': {'status': 'MEASURED', 'value': area},
            'timing': {'status': 'MEASURED', 'value': timing}}})
    arms = {name: tmp_path / (name + '.json') for name in ('ll', 'or')}
    result = contract.select_arms(arms, {'area': 'min', 'timing': 'max'}, tmp_path / 'sel.json')
    assert result['selection'] == 'll'
    assert set(result['arms']) == {'ll', 'or'}


def test_dual_executes_in_separate_directories_and_keeps_loser(tmp_path):
    seen = []

    def arm(name, area, timing):
        def produce(folder):
            seen.append((name, folder))
            return put(folder / 'gate.json', {'verdict': 'PASS', 'scope': {'step': 'same'},
                                              'metrics': {'area': {'status': 'MEASURED', 'value': area},
                                                          'timing': {'status': 'MEASURED', 'value': timing}}})
        return produce

    result = contract.execute_dual(tmp_path, '15.5ic', arm('ll', 10, 3),
                                   arm('or', 12, 2), {'area': 'min', 'timing': 'max'})
    assert result['selection'] == 'librelane'
    assert seen[0][1] != seen[1][1]
    assert all((folder / 'gate.json').is_file() for _, folder in seen)
