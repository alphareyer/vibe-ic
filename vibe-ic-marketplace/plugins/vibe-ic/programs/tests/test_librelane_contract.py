"""Real contract consumer with EDA file writes substituted at the process edge."""
import importlib
import json
import sys
import threading
import time
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

def test_synthesis_config_uses_selected_sources_and_lec_hooks(tmp_path):
    p = design(tmp_path)
    rtl = p / 'phase2/stage1/rtl'
    rtl.mkdir(parents=True)
    package = rtl / 'pkg.sv'
    block = rtl / 'block.sv'
    package.write_text('package pkg; endpackage')
    block.write_text('module block(input clk, output reg q); always @(posedge clk) q <= 1; endmodule')
    result = contract.emit_synthesis_config(
        p, 'processA', p / 'phase3/librelane/synth.json',
        [package, block], ['SIMULATION'], True)
    assert result['VERILOG_FILES'] == [str(package), str(block)]
    assert result['VERILOG_DEFINES'] == ['SIMULATION']
    assert result['USE_SLANG'] is True
    assert result['SYNTH_FSM_ENCFILE'] is True
    assert result['SYNTH_PRESERVE_FSM_REGISTERS'] == []
    assert 'VERILOG_FILES' in json.loads(
        (p / 'phase3/librelane/synth.provenance.json').read_text())


def test_synthesis_config_refuses_external_or_missing_rtl(tmp_path):
    p = design(tmp_path)
    with pytest.raises(contract.Refusal, match='LL_SYNTH_INPUT_MISSING'):
        contract.emit_synthesis_config(p, 'processA', p / 'synth.json',
                                       [tmp_path / 'other.sv'], [], False)


def test_native_stat_binds_area_gate_and_netlist_to_tool_output(tmp_path):
    netlist = tmp_path / 'block.nl.v'
    netlist.write_text('module block; endmodule\n')
    stat = put(tmp_path / 'stat.json', {'modules': {
        '\\block': {'num_cells': 4, 'area': 12.5}}})
    stats = put(tmp_path / 'stats.json', {'cell_count': 4, 'chip_area': 12.5,
        'netlist_sha256': 'sha256:' + contract.digest(netlist)})
    state = {'nl': str(netlist), 'metrics': {
        'design__instance__count': 4, 'design__instance__area': 12.5}}
    output = tmp_path / 'binding.json'
    assert contract.verify_synthesis_stat(stat, state, stats, 'block', output)['status'] == 'PASS'
    assert json.loads(output.read_text())['native_netlist_sha256'] == contract.digest(netlist)

    put(stats, {'cell_count': 3, 'chip_area': 12.5,
                'netlist_sha256': 'sha256:' + contract.digest(netlist)})
    with pytest.raises(contract.Refusal, match='LL_STAT_MISMATCH'):
        contract.verify_synthesis_stat(stat, state, stats, 'block', output)
    assert not output.exists()

    put(stats, {'cell_count': 4, 'chip_area': 12.5,
                'netlist_sha256': 'sha256:' + contract.digest(netlist)})
    netlist.write_text('module block; wire changed; endmodule\n')
    with pytest.raises(contract.Refusal, match='LL_STAT_NETLIST_MISMATCH'):
        contract.verify_synthesis_stat(stat, state, stats, 'block', output)


def test_synthesis_chain_accepts_pre_netlist_state_and_keeps_tool_output(tmp_path, monkeypatch):
    p = design(tmp_path)
    initial = put(p / 'initial.json', {'json_h': str(put(p / 'header.json', {}))})
    config = put(p / 'config.json', {'meta': {'step': 'Yosys.Synthesis'}})

    def tool_run(cmd, **_):
        folder = Path(cmd[cmd.index('-o') + 1])
        netlist = folder / 'block.nl.v'
        netlist.write_text('module block; endmodule')
        put(folder / 'state_out.json', {'nl': str(netlist)})
        return SimpleNamespace(returncode=0, stdout='ok', stderr='')

    monkeypatch.setattr(contract, 'image_capability', lambda *a: None)
    monkeypatch.setattr(contract.subprocess, 'run', tool_run)
    folder = contract.run_chain(p, 'candidate', [('Yosys.Synthesis', config, initial)], pdk_root='/pdk')[0]
    assert (folder / 'block.nl.v').is_file()
    assert json.loads((folder / 'state_out.json').read_text())['nl'].endswith('block.nl.v')


def test_stream_lane_and_synthesis_namespace_keep_separate_receipts(tmp_path, monkeypatch):
    p = design(tmp_path)
    netlist = p / 'block.nl.v'
    netlist.write_text('module block; endmodule')
    initial = put(p / 'initial.json', {'nl': str(netlist)})
    config = put(p / 'config.json', {'meta': {'step': 'OpenROAD.Floorplan'}})

    def tool_run(cmd, **_):
        folder = Path(cmd[cmd.index('-o') + 1])
        put(folder / 'state_out.json', {'nl': str(netlist)})
        return SimpleNamespace(returncode=0, stdout='ok', stderr='')

    monkeypatch.setattr(contract, 'image_capability', lambda *a: None)
    monkeypatch.setattr(contract.subprocess, 'run', tool_run)
    steps = [('OpenROAD.Floorplan', config, initial)]
    stream = contract.run_chain(p, 'candidate', steps, lane='stream37', pdk_root='/pdk')[0]
    synth = contract.run_chain(p, 'candidate', steps,
                               namespace='ppa_synthesis/arm0', pdk_root='/pdk')[0]
    assert stream == p / 'phase3/librelane/stream37/01-openroad-floorplan'
    assert synth == p / 'phase3/librelane/ppa_synthesis/arm0/01-openroad-floorplan'
    assert stream.joinpath('vibeic_receipt.json').is_file()
    assert synth.joinpath('vibeic_receipt.json').is_file()
    with pytest.raises(contract.Refusal, match='LL_LANE_NAMESPACE_CONFLICT'):
        contract.run_chain(p, 'candidate', steps, lane='stream37',
                           namespace='ppa_synthesis/arm0', pdk_root='/pdk')


def test_switch_defaults_to_direct_and_rejects_bad_value(tmp_path):
    p = design(tmp_path)
    assert contract.selected_mode(p, '15.5ic') == 'direct'
    put(p / 'phase3/librelane_switch.json', {'steps': {'15.5ic': 'dual'}})
    assert contract.selected_mode(p, '15.5ic') == 'dual'
    put(p / 'phase3/librelane_switch.json', {'steps': {'15.5ic': 'fallback'}})
    with pytest.raises(contract.Refusal, match='LL_INVALID_SWITCH'):
        contract.selected_mode(p, '15.5ic')


def test_step9_dual_does_not_silently_run_direct_without_routed_evidence(tmp_path):
    runner = importlib.import_module('phase3_one_shot_runner')
    p = design(tmp_path)
    put(p / 'phase3/librelane_switch.json', {'steps': {'9': 'dual'}})
    result = runner.step_synth(p, 'block', None, '')
    assert result.status == 'FAIL'
    assert 'LL_DUAL_POSTROUTE_NOT_READY' in result.detail


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
    folder = contract.run_chain(p, 'candidate', steps, pdk_root='/pdk')[0]
    assert len(calls) == 1
    assert 'state_out.json' in json.loads((folder / 'vibeic_receipt.json').read_text())['sha256']
    contract.run_chain(p, 'candidate', steps, pdk_root='/pdk')
    assert len(calls) == 1
    (folder / 'state_out.json').unlink()
    contract.run_chain(p, 'candidate', steps, pdk_root='/pdk')
    assert len(calls) == 2


def test_chain_stall_is_reaped_by_its_name_and_recorded_unmeasured(tmp_path, monkeypatch):
    p = design(tmp_path)
    source = p / 'block.nl.v'
    source.write_text('module block; endmodule\n')
    initial = put(p / 'initial.json', {'nl': str(source)})
    config = put(p / 'config.json', {'meta': {'step': 'OpenROAD.Floorplan'}})
    reaped = threading.Event()
    launched = []
    names = []

    def tool_run(cmd, **_):
        if len(cmd) > 1 and cmd[1] == 'inspect':
            return SimpleNamespace(returncode=0, stdout='0', stderr='')
        if len(cmd) > 1 and cmd[1] == 'rm':
            names.append((cmd[-1], 'stalled'))
            reaped.set()
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        launched.append(cmd)
        folder = Path(cmd[cmd.index('-o') + 1])
        put(folder / 'state_out.json', {'nl': str(source)})
        # The fake tool is allowed to finish on its own, so the pre-fix path
        # returns PASS after one second instead of hanging the test session.
        stopped = reaped.wait(1.0)
        if stopped:
            return SimpleNamespace(returncode=137, stdout='', stderr='stopped')
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    monkeypatch.setattr(contract, 'image_capability', lambda *a: None)
    monkeypatch.setattr(contract.subprocess, 'run', tool_run)
    monkeypatch.setattr(contract, 'TOOL_STALL_GRACE_S', 0.15)
    import _watchdog
    monkeypatch.setattr(_watchdog, 'host_tree_progress', lambda pid: None)

    with pytest.raises(contract.Refusal, match='LL_TOOL_STALLED'):
        contract.run_chain(p, 'candidate', [('OpenROAD.Floorplan', config, initial)],
                           pdk_root='/pdk')
    folder = p / 'phase3/librelane/01-openroad-floorplan'
    record = json.loads((folder / 'vibeic_stalled.json').read_text())
    assert record['verdict'] == 'NOT_MEASURED'
    assert record['reason_class'] == 'stalled'
    assert not (folder / 'state_out.json').exists()
    assert (folder / 'state_out.stalled.json').is_file()
    assert not (folder / 'vibeic_receipt.json').exists()
    assert names and all(reason == 'stalled' for _name, reason in names)
    assert len({name for name, _reason in names}) == 1
    assert launched[0][launched[0].index('--name') + 1] == names[0][0]
    assert '--memory' in launched[0] and '--memory-swap' in launched[0]


def test_chain_progressing_past_stall_grace_is_not_killed(tmp_path, monkeypatch):
    p = design(tmp_path)
    source = p / 'block.nl.v'
    source.write_text('module block; endmodule\n')
    initial = put(p / 'initial.json', {'nl': str(source)})
    config = put(p / 'config.json', {'meta': {'step': 'OpenROAD.Floorplan'}})
    reaped = []

    def tool_run(cmd, **_):
        if len(cmd) > 1 and cmd[1] == 'inspect':
            return SimpleNamespace(returncode=0, stdout='123', stderr='')
        if len(cmd) > 1 and cmd[1] == 'rm':
            reaped.append((cmd[-1], 'stalled'))
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        folder = Path(cmd[cmd.index('-o') + 1])
        log = folder / 'tool.log'
        for i in range(7):
            log.write_text('working\n' * (i + 1))
            time.sleep(0.08)
        put(folder / 'state_out.json', {'nl': str(source)})
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    monkeypatch.setattr(contract, 'image_capability', lambda *a: None)
    monkeypatch.setattr(contract.subprocess, 'run', tool_run)
    monkeypatch.setattr(contract, 'TOOL_STALL_GRACE_S', 0.15)
    import _watchdog
    readings = iter(range(1, 1000))
    monkeypatch.setattr(_watchdog, 'host_tree_progress', lambda pid: next(readings))

    folder = contract.run_chain(
        p, 'candidate', [('OpenROAD.Floorplan', config, initial)],
        pdk_root='/pdk')[0]
    assert (folder / 'state_out.json').is_file()
    assert not (folder / 'vibeic_stalled.json').exists()
    assert not reaped


def test_step31_reports_chain_stall_as_unmeasured(tmp_path, monkeypatch):
    runner = importlib.import_module('phase3_one_shot_runner')
    pv = importlib.import_module('librelane_pv_signoff')
    monkeypatch.setattr(contract, 'resolve_image', lambda project: 'candidate')
    monkeypatch.setattr(contract, 'resolve_pdk_root',
                        lambda project, pdk, image=None: str(tmp_path))

    def stalled(*args, **kwargs):
        raise contract.Refusal('LL_TOOL_STALLED', 'the step made no forward progress')

    monkeypatch.setattr(pv, 'run_half', stalled)
    result = runner._step31_librelane(
        tmp_path, 'block', SimpleNamespace(name='processA'), 'lvs', publish=False)
    assert result.status == 'NOT_MEASURED'
    assert result.reason_class == runner._V.ReasonClass.STALLED
    assert 'LL_TOOL_STALLED' in result.detail


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
    assert contract.run_chain(p, 'candidate', [('OpenROAD.Floorplan', cfg, state)], pdk_root='/pdk')[0].joinpath('state_out.json').is_file()


def test_tap_step_refuses_missing_geometry_input(tmp_path, monkeypatch):
    p = design(tmp_path)
    netlist = p / 'source.nl.v'
    netlist.write_text('module block; endmodule\n')
    state = put(p / 'initial.json', {'nl': str(netlist)})
    cfg = put(p / 'config.json', {'meta': {'step': 'OpenROAD.TapEndcapInsertion'}})
    monkeypatch.setattr(contract, 'image_capability', lambda *a: None)
    with pytest.raises(contract.Refusal, match='LL_STATE_MISSING'):
        contract.run_chain(p, 'candidate', [('OpenROAD.TapEndcapInsertion', cfg, state)], pdk_root='/pdk')


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
