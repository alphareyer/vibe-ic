"""Neutral source controls: real Default Step23 producer into the L24 reader.

Only the existing native process/STA transport edges are replaced. The separate
pinned-image toy PG probe exercises the generated Tcl and actual OpenDB/PDNSim.
"""
import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import execution_backend_producers as producers
import librelane_contract as LC
import librelane_ir_antenna as PG
import l24_signoff_evidence_backed_check as gate
import l24_signoff_requirements_extract as requirements
import phase3_one_shot_runner as R


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value))
    return path


def b64(value):
    return base64.b64encode(value.encode()).decode()


def setup_step23(project, monkeypatch, case):
    pnr = project / 'phase3/stage3/pnr'
    text = 'VERSION 5.8 ;\nDESIGN neutral ;\nEND DESIGN\n'
    put(pnr / 'neutral.def', text)
    put(pnr / 'routed.def', text)
    put(project / 'input/docs/requirements.md',
        'No power network requirement is stated.\n' if case == 'not_requested' else
        'Power network shall have no disconnected ports.\n')
    lef = put(project / 'neutral.lef', 'SOURCE_CONTROL: native process is substituted\n')
    config = put(project / 'phase3/librelane/22-config/OpenROAD.RCX.json', {
        'DESIGN_NAME': 'neutral', 'TECH_LEFS': {'nom_*': str(lef)},
        'CELL_LEFS': [str(lef)], 'VDD_NETS': ['VDD'], 'GND_NETS': ['VSS']})
    spef = put(project / 'phase3/stage3/extracted/neutral.spef', '*SPEF "IEEE 1481-1998"\n')
    internal = case in ('internal', 'source_mismatch', 'changed_pad_source')
    if internal:
        pad = {'verdict': 'WROTE', 'power_pad_plan': {
            'power_net': 'VDD', 'ground_net': 'VSS', 'instances': ['u_pad'],
            'core_supply_pads': [{'kind': 'power', 'master': 'PAD', 'terminal': 'BOND'}]},
            'pad_instances': {'u_pad': {'kind': 'power', 'master': 'PAD', 'is_supply_pad': True,
                'terminal': 'BOND', 'port': 'VDD_BOND', 'grid_net': 'VDD',
                'derivation': {'fed_rail': 'FED'},
                'supply_connections': {'FED': 'VDD', 'BOND': 'VDD_BOND'}}}}
        put(project / PG.PAD_PLAN_REL, pad)
    monkeypatch.setattr(producers, '_parameters', lambda *args:
                        (R, LC, SimpleNamespace(name='neutral'), ''))
    monkeypatch.setattr(R, '_librelane_handed_spefs', lambda *args: {'nom': spef})
    def sta(*args, **kwargs):
        folder = project / 'native-sta'
        put(folder / 'state_out.json', {})
        put(folder / 'TT/vibeic_signoff.rpt', 'SOURCE_CONTROL STA transport\n')
        put(project / 'reports/phase3/sta_postpnr_signoff.json', {
            'sta_state': str(folder / 'state_out.json'),
            'sta_state_sha256': LC.digest(folder / 'state_out.json'), 'corners': {'TT': {}}})
    monkeypatch.setattr(R, '_librelane_signoff_record', sta)
    monkeypatch.setattr(R, '_log_surviving_artefact', lambda *a, **k: None)
    monkeypatch.setattr(R, '_achievable_fmax_from_tool_arm', lambda *a, **k: None)
    monkeypatch.setattr(R, '_resolve_clock_spec', lambda *a, **k: (20, 'source control'))
    import clock_target_provenance as clocks
    monkeypatch.setattr(clocks, 'emit_report', lambda *a, **k: None)
    monkeypatch.setattr(clocks, 'stamp_signoff_records', lambda *a, **k: None)
    calls = []
    def native(project, image, cfg, body, folder, name, mounts, docker):
        calls.append(body[0])
        put(folder / (name + '.tcl'), body[0])
        put(folder / (name + '.log'), 'SOURCE_CONTROL native transport; no EDA measurement\n')
        if case == 'source_mismatch':
            raise LC.Refusal('LL_BRIDGE_CONVERSION_FAILED', 'Internal supply source does not match the producer')
        lines = []
        nets = [('VDD', 'POWER'), ('VSS', 'GROUND')]
        if internal:
            nets.append(('VDD_BOND', 'POWER'))
        for net, use in nets:
            lines.append('\t'.join(['N', b64(net), use]))
        for pin, net, use in [('P', 'VDD', 'POWER'), ('G', 'VSS', 'GROUND')]:
            nn = '' if case == 'no_net' and pin == 'P' else net
            placed = '0' if case == 'unplaced' and pin == 'P' else '1'
            shapes = '0' if case == 'shapeless' and pin == 'P' else '1'
            kind = 'SIGNAL' if case == 'unclassified' and pin == 'P' else use
            lines.append('\t'.join(['I', b64('u_core'), b64(pin), b64(nn), kind, placed, shapes]))
        if internal:
            for pin, net in [('FED', 'VDD'), ('BOND', 'VDD_BOND')]:
                lines.append('\t'.join(['I', b64('u_pad'), b64(pin), b64(net), 'POWER', '1', '1']))
        for net, use in ([('VDD_BOND', 'POWER'), ('VSS', 'GROUND')] if internal else nets):
            valid = '0' if case == 'missing_bpin' and use == 'POWER' else '1'
            lines.append('\t'.join(['B', b64(net), b64(net), use, '1', valid, valid]))
        for net, use in nets:
            report = folder / ('grid-' + net.encode().hex() + '.rpt')
            put(report, '\n')
            mode = 'resistor_fed_internal' if internal and net == 'VDD' else 'external'
            failed = case in ('iterm_open', 'bterm_open', 'fail_plus_partial') and net == 'VDD'
            result = 'PSM-0069' if failed else '1'
            answer = 'UNKNOWN' if failed or case == 'invalid_boolean' else '1'
            lines.append('\t'.join(['C', b64(net), mode, '1' if failed else '0', answer,
                                    b64(result), b64(str(report))]))
        if case not in ('partial', 'fail_plus_partial'):
            lines.append('\t'.join(['END', str(len(nets)), '4' if internal else '2', '2']))
        # Missing the primary native measurement is unmeasured. An absent
        # per-net error file is normal when the real native boolean is true.
        if case != 'missing_report':
            put(folder / 'power-connectivity.tsv', '' if case == 'empty' else '\n'.join(lines) + '\n')
        return folder / (name + '.tcl')
    monkeypatch.setattr(LC, '_openroad_convert', native)
    params = {'step_id': '23', 'top': 'neutral', 'pdk_root': str(project),
              'image_id': 'source-unit-transport', 'execution_mode': 'default'}
    return params, calls, lef


@pytest.mark.parametrize('case,producer_verdict,outcome', [
    ('direct', 'PASS', 'BACKED'),
    ('internal', 'PASS', 'BACKED'),
    ('iterm_open', 'FAIL', 'UNMET_MEASURED_FAILING'),
    ('bterm_open', 'FAIL', 'UNMET_MEASURED_FAILING'),
    ('no_net', 'FAIL', 'UNMET_MEASURED_FAILING'),
    ('unplaced', 'NOT_MEASURED', 'UNMET_NO_READING'),
    ('shapeless', 'NOT_MEASURED', 'UNMET_NO_READING'),
    ('missing_bpin', 'NOT_MEASURED', 'UNMET_NO_READING'),
    ('missing_report', 'NOT_MEASURED', 'UNMET_NO_READING'),
    ('empty', 'NOT_MEASURED', 'UNMET_NO_READING'),
    ('partial', 'NOT_MEASURED', 'UNMET_NO_READING'),
    ('invalid_boolean', 'NOT_MEASURED', 'UNMET_NO_READING'),
    ('source_mismatch', 'NOT_MEASURED', 'UNMET_NO_READING'),
    ('unclassified', 'NOT_MEASURED', 'UNMET_NO_READING'),
    ('fail_plus_partial', 'FAIL', 'UNMET_MEASURED_FAILING'),
    ('stale_route', 'PASS', 'UNMET_NO_READING'),
    ('changed_lef', 'PASS', 'UNMET_NO_READING'),
    ('changed_pad_source', 'PASS', 'UNMET_NO_READING'),
])
def test_step23_power_reading_reaches_the_current_l24_consumer(
        tmp_path, monkeypatch, case, producer_verdict, outcome):
    params, calls, lef = setup_step23(tmp_path, monkeypatch, case)
    result = producers.produce_signoff(tmp_path, params)
    if case == 'stale_route':
        put(tmp_path / 'phase3/stage3/pnr/routed.def', 'current promoted route\n')
    elif case == 'changed_lef':
        lef.write_text('changed current technology\n')
    elif case == 'changed_pad_source':
        put(tmp_path / PG.PAD_PLAN_REL, {})
    doc = {'fields': requirements.extract_signoff_requirements(tmp_path)}
    observed = []
    failures, messages = gate._requirements_backed(tmp_path, doc, 'L24_SIGNOFF.json', observed)
    report = tmp_path / 'reports/phase3/no_disconnected_ports.json'
    (tmp_path / 'power-api.observation.json').write_text(json.dumps({
        'case': case, 'scope': 'SOURCE_CONTROL only; native transport substituted',
        'producer': result, 'consumer': observed, 'failures': failures,
        'report': json.loads(report.read_text()) if report.is_file() else None}, indent=2) + '\n')
    assert result['verdict'] == producer_verdict, result
    assert len(calls) == 1, 'the current producer must actually call its native transport'
    assert observed[0]['outcome'] == outcome, observed
    assert bool(failures) is (outcome != 'BACKED')
    assert '-floorplanning' not in calls[0]
    if case in ('internal', 'source_mismatch', 'changed_pad_source'):
        assert 'Internal supply source does not match the producer' in calls[0]
        assert '[llength [$core_net getBTerms]] != 0' in calls[0]
        assert '-dont_require_terminals' in calls[0]
    else:
        assert '-dont_require_terminals' not in calls[0]


def test_unstated_power_requirement_does_not_launch_an_extra_native_job(tmp_path, monkeypatch):
    params, calls, _ = setup_step23(tmp_path, monkeypatch, 'not_requested')
    result = producers.produce_signoff(tmp_path, params)
    assert result['verdict'] == 'PASS', result
    assert calls == []
    report = json.loads((tmp_path / 'reports/phase3/no_disconnected_ports.json').read_text())
    assert report['verdict'] == 'NOT_APPLICABLE'
