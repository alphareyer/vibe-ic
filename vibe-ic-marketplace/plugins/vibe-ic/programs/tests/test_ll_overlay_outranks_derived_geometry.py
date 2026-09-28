"""The explicit overlay outranks the run's derived die and core, in ORDER.

Review of cmp3 D1 (review_D1_D2_D3_D6.json, D1 correctness): D1's
`test_an_explicit_overlay_still_outranks_the_derived_die` overlaid only
PDN_CFG, so it never tested which of the derived rectangle and the overlay is
applied last. Here the overlay carries DIE_AREA and CORE_AREA themselves.

THE RULE, stated once:
  * `resolve_step_configs` applies the declared and derived geometry first
    (D1's `_apply_runner_floorplan`), then the caller's overlay, so an overlay
    value AND its source win.
  * D1's derived checks judge the RECORD, before the overlay: an inconsistent
    `floorplan_rectangles.json` is refused even when an overlay would replace
    both rectangles. An overlay does not launder a bad record.
  * The rectangles the tool is finally handed are judged after the overlay
    too (`LL_CONFIG_CORE_OUTSIDE_DIE`), whoever supplied each half.

Two tests need D1 in the tree (it lands first): with D1 absent, nothing
derives a die from the record, so the conflict and record-check cases cannot
be reached. They are red on a tree without D1 by design, and named here.
"""
import importlib
import json
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
contract = importlib.import_module('librelane_contract')
from test_t95_mig_front import design, put  # noqa: E402

CHAIN = ['OpenROAD.Floorplan', 'OpenROAD.PadRing']
RECORD_REL = 'reports/phase3/floorplan_rectangles.json'
RECORD = {'program': 'phase3_one_shot_runner.step_pnr',
          'die_rect_um': [0, 0, 3162, 3162],
          'die_source': '--die-um 3162x3162 at the origin',
          'floorplan_rect_um': None, 'floorplan_rect_is_the_die': True,
          'core_pad_um': 393}
OVERLAY = {'DIE_AREA': ([0, 0, 4000, 4000], 'test overlay: die'),
           'CORE_AREA': ([400, 400, 3600, 3600], 'test overlay: core')}


@pytest.fixture
def resolver(monkeypatch):
    calls = []

    def run(cmd, **_kw):
        design_json, requested, root = Path(cmd[-5]), Path(cmd[-4]), Path(cmd[-3])
        d = json.loads(design_json.read_text())
        calls.append(d)
        for s in json.loads(requested.read_text()):
            put(root / f'{s}.json', {'DIE_AREA': d.get('DIE_AREA'),
                                     'CORE_AREA': d.get('CORE_AREA'),
                                     'meta': {'step': s}})
            put(root / f'{s}.views.json', {'step': s, 'inputs': [], 'outputs': []})
        put(root / 'flow_gates.json', {})
        return contract.subprocess.CompletedProcess(cmd, 0, '', '')

    monkeypatch.setattr(contract, 'image_capability', lambda *a, **k: {})
    monkeypatch.setattr(contract.subprocess, 'run', run)
    return calls


def project(tmp_path, record=RECORD):
    p = design(tmp_path, {'deliverable': 'DIE', 'top_cell': 'core',
                          'die_area_um': 'NOT_DETERMINED'},
               pads={'PAD_SOUTH': ['u_a'], 'PAD_NORTH': ['u_b'],
                     'PAD_EAST': [], 'PAD_WEST': []})
    if record is not None:
        put(p / RECORD_REL, record)
    (tmp_path / 'pdkroot' / 'processA').mkdir(parents=True, exist_ok=True)
    return p


def resolve(tmp_path, p, overlay):
    configs = contract.resolve_step_configs(p, 'img', 'processA', CHAIN,
                                            pdk_root=tmp_path / 'pdkroot',
                                            folder='15-config', overlay=overlay)
    root = p / 'phase3/librelane/15-config'
    return ({s: json.loads(c.read_text()) for s, c in configs.items()},
            json.loads((root / 'design.provenance.json').read_text()))


def test_an_overlay_die_and_core_and_their_sources_win(tmp_path, resolver):
    """With D1: the record derives [0,0,3162,3162]; the overlay applied after it
    must win, value and source. Applied BEFORE D1's rule instead, it would be
    read as a declared die and refused against the record."""
    p = project(tmp_path)
    configs, sources = resolve(tmp_path, p, dict(OVERLAY))
    for step in CHAIN:
        assert configs[step]['DIE_AREA'] == [0, 0, 4000, 4000], step
        assert configs[step]['CORE_AREA'] == [400, 400, 3600, 3600], step
    assert sources['DIE_AREA'] == 'test overlay: die'
    assert sources['CORE_AREA'] == 'test overlay: core'


def test_an_overlay_core_outside_the_die_it_is_handed_with_is_refused(tmp_path, resolver):
    p = project(tmp_path)
    overlay = dict(OVERLAY, CORE_AREA=([400, 400, 4200, 3600], 'test overlay: core'))
    with pytest.raises(contract.Refusal, match='LL_CONFIG_CORE_OUTSIDE_DIE') as exc:
        resolve(tmp_path, p, overlay)
    assert 'test overlay: core' in str(exc.value)
    assert resolver == []


def test_an_overlay_core_outside_the_derived_die_is_refused(tmp_path, resolver):
    """Needs D1: the die comes from the record, the core from the overlay."""
    p = project(tmp_path)
    overlay = {'CORE_AREA': ([400, 400, 3600, 3600], 'test overlay: core')}
    with pytest.raises(contract.Refusal, match='LL_CONFIG_CORE_OUTSIDE_DIE') as exc:
        resolve(tmp_path, p, overlay)
    assert RECORD_REL in str(exc.value)


def test_the_derived_checks_judge_the_record_even_under_an_overlay(tmp_path, resolver):
    """Needs D1: a record whose core lies outside its die is refused before the
    overlay, even though the overlay would replace both rectangles."""
    p = project(tmp_path, dict(RECORD, floorplan_rect_um=[100, 100, 3200, 3000],
                               floorplan_rect_is_the_die=False))
    with pytest.raises(contract.Refusal, match='LL_DERIVED_CORE_OUTSIDE_DIE'):
        resolve(tmp_path, p, dict(OVERLAY))
    assert resolver == []
