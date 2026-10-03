"""Focused binding controls for pad-ring density exclusion.

The fixture uses arbitrary geometry and a faithful Floorplan receipt shape.  It
proves that a settled producer record can feed the ordinary fill consumer,
that an explicit owner answer wins, and that a changed settled config refuses.
"""
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import librelane_fill_dfm as fill  # noqa: E402
from librelane_contract import Refusal  # noqa: E402


def _put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


def _fixture(tmp_path):
    project = tmp_path / 'project'
    pdk_root = tmp_path / 'pdks'
    tech = pdk_root / 'processA/tech'
    tech.mkdir(parents=True)
    (tech / 'routing.tlef').write_text(
        'MANUFACTURINGGRID 0.01 ;\nLAYER Metal1\n TYPE ROUTING ;\n'
        ' WIDTH 0.2 ;\n SPACING 0.3 ;\nEND Metal1\n')
    (tech / 'layers.map').write_text('Metal1 NET 61 0\n')
    (tech / 'density.drc').write_text(
        'chip_area = extent.sized(0.0).area\n'
        'extract_single_layer_from_design.call(:metal1_drawn, 61, 0)\n'
        'extract_single_layer_from_design.call(:metal1_dummy, 61, 7)\n'
        "extent.output('M1.4', '30%')\n")
    (tech / 'pad.lef').write_text('MACRO PAD_CELL\n CLASS PAD ;\nEND PAD_CELL\n')
    routed = project / 'phase3/stage3/pnr/routed.def'
    routed.parent.mkdir(parents=True)
    routed.write_text(
        'VERSION 5.8 ;\nUNITS DISTANCE MICRONS 1000 ;\n'
        'DIEAREA ( 0 0 ) ( 120000 100000 ) ;\n'
        'COMPONENTS 1 ;\n- PAD_0 PAD_CELL + FIXED ( 0 0 ) N ;\n'
        'END COMPONENTS\nEND DESIGN\n')
    cfg = _put(tmp_path / 'density.json', {
        'KLAYOUT_DEF_LAYER_MAP': '/pdk/processA/tech/layers.map',
        'KLAYOUT_DENSITY_RUNSET': '/pdk/processA/tech/density.drc',
        'TECH_LEFS': {'nom_*': '/pdk/processA/tech/routing.tlef'},
        'PAD_LEFS': ['/pdk/processA/tech/pad.lef'],
        'DESIGN_NAME': 'generic_top', 'DIE_AREA': [0, 0, 120, 100]})
    _put(project / 'input/submission_template/tapeout_declaration.json',
         {'answers': {}})

    record = project / 'reports/phase3/floorplan_rectangles.json'
    _put(record, {'program': 'phase3_one_shot_runner.step_pnr',
                  'die_rect_um': [0, 0, 120, 100],
                  'floorplan_rect_um': [17, 11, 103, 89],
                  'floorplan_rect_is_the_die': False})
    resolved = {'DESIGN_NAME': 'generic_top',
                'DIE_AREA': [0, 0, 120, 100],
                'CORE_AREA': [17, 11, 103, 89], 'FP_SIZING': 'absolute'}
    config = project / 'phase3/librelane/15-config/OpenROAD.Floorplan.json'
    _put(config, resolved)
    step = project / 'phase3/librelane/15-floorplan/02-openroad-floorplan'
    step.mkdir(parents=True)
    resolved_step = _put(step / 'config.json', resolved)
    placed = step / 'generic_top.def'
    placed.write_text('UNITS DISTANCE MICRONS 1000 ;\n'
                      'DESIGN generic_top ;\n'
                      'DIEAREA ( 0 0 ) ( 120000 100000 ) ;\n')
    state = _put(step / 'state_out.json', {'def': str(placed), 'metrics': {
        'design__die__bbox': '0.0 0.0 120.0 100.0',
        'design__core__bbox': '17.0 11.0 103.0 89.0'}})
    receipt = step / 'vibeic_receipt.json'
    _put(receipt, {'input': {'step': 'OpenROAD.Floorplan'}, 'sha256': {
        'config.json': hashlib.sha256(resolved_step.read_bytes()).hexdigest(),
        'state_out.json': hashlib.sha256(state.read_bytes()).hexdigest(),
        'generic_top.def': hashlib.sha256(placed.read_bytes()).hexdigest()}})
    gds = project / 'pdk_filled.gds'
    gds.write_bytes(b'PDK filler GDS')
    return project, pdk_root, cfg, gds, config


def _runner(project):
    def run(cmd, **_kwargs):
        if 'openroad' in cmd:
            Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
            Path(cmd[-1]).with_name('placed_keepouts.txt').write_text(
                'DBU 1000\nTOTAL 1\nBOX PAD PAD_CELL 0 0 1000 1000\nEND 1\n')
        else:
            Path(cmd[cmd.index('--out') + 1]).write_bytes(b'changed fill')
            _put(Path(cmd[cmd.index('--report') + 1]), {
                'verdict': 'PASS', 'layers': [{'name': 'metal1',
                                               'density_before': .28,
                                               'density_after': .34}]})
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    return run


def test_settled_floorplan_record_feeds_density_without_owner_rectangle(
        tmp_path, monkeypatch):
    project, pdk_root, cfg, gds, _ = _fixture(tmp_path)
    monkeypatch.setattr(fill, 'run_container', _runner(project))
    result = fill.top_up_density(project, 'image', pdk_root, 'processA', gds,
                                 cfg, '37-derived-core')
    derived = json.loads(Path(result['config']).read_text())
    binding = derived['_derivation']['pad_ring_exclusion']
    assert binding['core'] == [17.0, 11.0, 103.0, 89.0]
    assert 'floorplan_rectangles.json' in binding['source']
    assert 'OpenROAD.Floorplan' in binding['source']
    assert derived['keepout_edge_um'] == 17


def test_settled_floorplan_uses_recorded_design_name_def(tmp_path):
    project, _pdk_root, _cfg, _gds, _config = _fixture(tmp_path)
    die, core, source = fill.settled_floorplan_geometry(project)
    assert die == [0, 0, 120, 100]
    assert core == [17, 11, 103, 89]
    assert 'generic_top.def' in source


def test_explicit_owner_core_has_precedence_over_settled_geometry(
        tmp_path, monkeypatch):
    project, pdk_root, cfg, gds, _ = _fixture(tmp_path)
    monkeypatch.setattr(fill, 'declaration_config', lambda _project: (
        {'CORE_AREA': [9, 8, 111, 92]}, {'CORE_AREA': 'owner declaration'}))
    monkeypatch.setattr(fill, 'run_container', _runner(project))
    result = fill.top_up_density(project, 'image', pdk_root, 'processA', gds,
                                 cfg, '37-owner-core')
    derived = json.loads(Path(result['config']).read_text())
    binding = derived['_derivation']['pad_ring_exclusion']
    assert binding['core'] == [9, 8, 111, 92]
    assert binding['source'] == 'owner declaration'
    assert derived['keepout_edge_um'] == 9


def test_changed_settled_floorplan_config_refuses_before_fill(
        tmp_path, monkeypatch):
    project, pdk_root, cfg, gds, config = _fixture(tmp_path)
    changed = json.loads(config.read_text())
    changed['CORE_AREA'] = [18, 11, 103, 89]
    _put(config, changed)
    monkeypatch.setattr(fill, 'run_container', _runner(project))
    with pytest.raises(Refusal, match='LL_FLOORPLAN_CORE_PROVENANCE'):
        fill.top_up_density(project, 'image', pdk_root, 'processA', gds,
                            cfg, '37-stale-core')


def test_missing_settled_floorplan_receipt_refuses_before_fill(
        tmp_path, monkeypatch):
    project, pdk_root, cfg, gds, _ = _fixture(tmp_path)
    (project / 'phase3/librelane/15-floorplan/02-openroad-floorplan/'
     'vibeic_receipt.json').unlink()
    monkeypatch.setattr(fill, 'run_container', _runner(project))
    with pytest.raises(Refusal, match='LL_FLOORPLAN_CORE_PROVENANCE'):
        fill.top_up_density(project, 'image', pdk_root, 'processA', gds,
                            cfg, '37-missing-receipt')
