"""Native owner wiring controls; fixture receipts are not extraction qualification."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from flow109_admission import AdmissionRefusal
import flow109_final_extract as d69


def _d62(fill_count=None, **untrusted):
    measurements = {} if fill_count is None else {'fill_count_after': fill_count}
    return {'measurements': measurements, **untrusted}


@pytest.mark.parametrize('count,reason', [
    (None, 'D69_CURRENT_FINAL_FILL_CORRELATION_NOT_MEASURED'),
    (0, 'D69_CURRENT_FINAL_FILL_CORRELATION_NOT_MEASURED'),
    (147, 'D69_CURRENT_FINAL_FILL_CORRELATION_NOT_MEASURED'),
])
def test_fill_scope_is_derived_from_native_census(count, reason):
    assert d69._fill_extraction_scope(_d62(count)) == {
        'status': 'NOT_MEASURED',
        'reason': reason,
        'native_fill_count': count,
        'basis': 'source-owned D62 census; current final-GDS correlation remains separate',
    }


def test_forged_verified_string_cannot_upgrade_fill_scope():
    baseline = d69._fill_extraction_scope(_d62(147))
    forged = d69._fill_extraction_scope(_d62(147, extraction_fill_sensitivity='VERIFIED'))
    assert forged == baseline
    assert forged['status'] == 'NOT_MEASURED'


def test_consumer_refuses_forged_fill_sensitive_pass():
    scope = d69._fill_extraction_scope(_d62(147))
    record = {
        'reason': 'D69_NATIVE_FINAL_FILL_SPEF_MEASURED',
        'component_scope': 'native_final_gds_floating_fill_component',
        'fill_sensitive_parasitics': {**scope, 'status': 'PASS'},
    }
    with pytest.raises(AdmissionRefusal, match='D69_COMPONENT_SCOPE_CHANGED'):
        d69._component_scope(record, _d62(147))


def _owner_fixture(tmp_path, monkeypatch, *, top="neutral", adopted_views=None):
    from types import SimpleNamespace
    def record(path):
        return d69.files._record(tmp_path, path)
    paths = {}
    for name, text in {
        'filled.odb': 'unit ODB placeholder', 'final.gds': 'unit GDS placeholder',
        'filled.def': 'VERSION 5.8 ;\nDESIGN neutral ;\nUNITS DISTANCE MICRONS 1000 ;\n',
        'netlist.v': 'module neutral; endmodule', 'constraint.sdc': '',
        'tech': 'unit extraction-tech placeholder', 'tool': 'unit subprocess double',
    }.items():
        path = tmp_path / name; path.write_text(text.replace('neutral', top)); paths[name] = path
    d62 = tmp_path / 'd62.json'
    d62.write_text(json.dumps({'outputs': {'filled.def': record(paths['filled.def'])}}))
    manifest = {'schema': d69.SCHEMA, 'd62_receipt': record(d62),
        'stream_bridge': {'path': 'bridge.json', 'sha256': 'fixture'},
        'adopted_views': adopted_views or {'netlist': record(paths['netlist.v']),
                          'sdc': record(paths['constraint.sdc'])}}
    target = tmp_path / d69.INPUT; target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest))
    odb = record(paths['filled.odb']); launches = []
    monkeypatch.setattr(d69, 'require_launch_admission', lambda *a, **kw: None)
    monkeypatch.setattr(d69.density, 'validate_filled_odb', lambda *a, **kw:
                        {'odb': odb, 'measurements': {'fill_count_after': 147}})
    monkeypatch.setattr(d69.files, 'validate_bridge', lambda *a, **kw:
        {'d62_receipt': manifest['d62_receipt'], 'odb': odb, 'gds': record(paths['final.gds'])})
    def which(name):
        assert name == 'vibeic-final-fill-spef'
        return str(paths['tool'])
    monkeypatch.setattr(d69.shutil, 'which', which)
    original_run = d69.subprocess.run
    def invoke(argv, **kwargs):
        if argv[0] != str(paths['tool']):
            return original_run(argv, **kwargs)
        launches.append(argv)
        folder = Path(argv[argv.index('--output-dir') + 1]); folder.mkdir()
        keep = folder / 'routed-nets.txt'; keep.write_text('A\nB\n')
        corners = {}
        for corner in ('min', 'nom', 'max'):
            leaf = folder / corner; leaf.mkdir()
            raw, final = leaf / 'raw.spef', leaf / 'final.spef'
            text = ('*SPEF "IEEE 1481-1998"\n*DESIGN "neutral"\n*C_UNIT 1 PF\n'
                    '*D_NET A 0.1\n*CAP\n1 A 0.1\n*END\n'
                    '*D_NET B 0.2\n*CAP\n1 B 0.2\n*END\n')
            raw.write_text(text.replace('neutral', top)); final.write_text(text.replace('neutral', top))
            corners[corner] = {'status': 'PASS',
                'method': 'exact-capacitance-matrix-schur-complement',
                'floating_boundary_condition': 'zero-net-charge',
                'grounded_fill_approximation': False, 'retained_net_count': 2}
            for key, path in (('raw_spef', raw), ('keep_nets', keep), ('output_spef', final)):
                corners[corner].update({key: str(path), key + '_sha256': d69._sha(path)})
        receipt = {'status': 'PASS', 'geometry': {'path': str(paths['final.gds']),
                   'sha256': d69._sha(paths['final.gds'])},
            'routed_def': {'path': str(paths['filled.def']), 'sha256': d69._sha(paths['filled.def'])},
            'top': top, 'fill_aware_tech': record(paths['tech']),
            'declared_corner_set': ['min', 'nom', 'max'], 'keep_net_count': 2,
            'keep_nets_sha256': d69._sha(keep), 'corners': corners,
            'floating_boundary_condition': 'zero-net-charge', 'grounded_fill_approximation': False}
        (folder / 'FINAL_FILL_EXTRACTION.json').write_text(json.dumps(receipt))
        return SimpleNamespace(returncode=0, stdout='unit double, no native calibration', stderr='')
    monkeypatch.setattr(d69.subprocess, 'run', invoke)
    return paths, launches


def test_existing_owner_invokes_released_tool_on_final_gds_def_and_binds_spefs(tmp_path, monkeypatch):
    paths, launches = _owner_fixture(tmp_path, monkeypatch)
    result = d69.execute(tmp_path, {'context_sha256': 'developer-control'})
    assert result['component_status'] == 'PASS', result
    assert result['native_started'] is True and len(launches) == 1
    argv = launches[0]
    assert argv[1] == 'extract'
    assert argv[argv.index('--gds') + 1] == str(paths['final.gds'])
    assert argv[argv.index('--def') + 1] == str(paths['filled.def'])
    assert argv[argv.index('--top') + 1] == 'neutral'
    assert set(result['final_spefs']) == {'min', 'nom', 'max'}
    assert result['fill_sensitive_parasitics']['status'] == 'NOT_MEASURED'
    assert result['fill_sensitive_parasitics']['reason'] == 'D69_CURRENT_FINAL_FILL_CORRELATION_NOT_MEASURED'
    current = d69.validate_final_parasitics(tmp_path, result['receipt'])
    assert current['final_spefs'] == result['final_spefs']
    assert current['final_gds'] == result['final_gds']
    assert current['flow109_acceptance'] == 'NOT_MEASURED'


@pytest.mark.parametrize('damage', ['geometry', 'missing_corner', 'grounded', 'required_net', 'spef'])
def test_native_receipt_consumer_rejects_wrong_or_incomplete_final_inputs(tmp_path, monkeypatch, damage):
    _owner_fixture(tmp_path, monkeypatch)
    result = d69.execute(tmp_path, {'context_sha256': 'developer-control'})
    assert result['component_status'] == 'PASS', result
    receipt = d69._bound(tmp_path, result['native_receipt'])
    value = json.loads(receipt.read_text())
    if damage == 'geometry': value['geometry']['sha256'] = '0' * 64
    if damage == 'missing_corner': value['corners'].pop('max')
    if damage == 'grounded': value['grounded_fill_approximation'] = True
    if damage == 'required_net': value['corners']['min']['retained_net_count'] = 1
    if damage == 'spef': Path(value['corners']['min']['output_spef']).write_text('changed')
    receipt.write_text(json.dumps(value))
    with pytest.raises((AdmissionRefusal, d69.Refusal)):
        d69._native_outputs(tmp_path, d69.files._record(tmp_path, receipt),
                            result['final_gds'], result['final_def'])
