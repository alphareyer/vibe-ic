"""The chip path's LibreLane layout chains link the chip top, not the core (cmp3 D7).

THE DEFECT. `resolve_step_configs` took DESIGN_NAME from the declared
`top_cell`, which on spm and subservient names the CORE (the product name).
Step 15.5ic's producer wraps that core in a pad-carrying top
(`reports/phase3/io_pad_chip_top.json`: `chip_top_module`, `core_module`), and
the chain's netlist carries both modules -- but LibreLane links DESIGN_NAME, so
OpenROAD.Floorplan elaborated the 273 core cells alone and OpenROAD.PadRing
failed `[ERROR] No instance u_pad_supply_power found.` (spm x gf180mcuD, lane
fxpad, image 0.3.83). The direct deck links the chip top
(`_inject_padring_chip_top`), so every later chain bridging a direct chip DEF
(DESIGN chip_top) into a config naming the core was refused
LL_BRIDGE_DESIGN_MISMATCH.

Real `librelane_contract` throughout; only the image resolver's file writes
are modelled.
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
from test_t95_mig_front import design, put, OWNER  # noqa: E402

#: The chip-top producer's record, as step 15.5ic writes it.
RECORD_REL = 'reports/phase3/io_pad_chip_top.json'
PADS = {'PAD_SOUTH': ['u_pad_vdd', 'u_pad_a'], 'PAD_NORTH': ['u_pad_b'],
        'PAD_EAST': [], 'PAD_WEST': []}
RECORD = {'verdict': 'WROTE', 'chip_top_verilog': 'phase3/stage3/pnr/chip_top_io.v',
          'chip_top_module': 'chip_top', 'core_module': 'core'}
#: The run's own floorplan record, so a floorplan chain has a die whatever
#: else the contract derives from it.
FLOORPLAN = {'program': 'phase3_one_shot_runner.step_pnr', 'die_rect_um': [0, 0, 500, 500],
             'die_source': 'test', 'floorplan_rect_um': None,
             'floorplan_rect_is_the_die': True, 'core_pad_um': 50}
LAYOUT_CHAINS = [['OpenROAD.Floorplan', 'OpenROAD.PadRing'],
                 ['OpenROAD.GlobalPlacement'], ['OpenROAD.DetailedRouting']]


@pytest.fixture
def resolver(monkeypatch):
    calls = []

    def run(cmd, **_kw):
        design_json, requested, root = Path(cmd[-5]), Path(cmd[-4]), Path(cmd[-3])
        d = json.loads(design_json.read_text())
        calls.append(d)
        for s in json.loads(requested.read_text()):
            put(root / f'{s}.json', {'DESIGN_NAME': d.get('DESIGN_NAME'),
                                     'DIE_AREA': d.get('DIE_AREA'),
                                     'meta': {'step': s}})
            put(root / f'{s}.views.json', {'step': s, 'inputs': [], 'outputs': []})
        put(root / 'flow_gates.json', {})
        return contract.subprocess.CompletedProcess(cmd, 0, '', '')

    monkeypatch.setattr(contract, 'image_capability', lambda *a, **k: {})
    monkeypatch.setattr(contract.subprocess, 'run', run)
    return calls


def chip(tmp_path, record=RECORD, self_tapeout=True, provenance=None, **answers):
    p = design(tmp_path, {'deliverable': 'DIE', 'top_cell': 'core', **answers},
               provenance, pads=PADS)
    if self_tapeout:
        put(p / 'input/submission_template/SELF_TAPEOUT.txt', 'self tape-out\n')
    if record is not None:
        put(p / RECORD_REL, record)
    put(p / 'reports/phase3/floorplan_rectangles.json', FLOORPLAN)
    (tmp_path / 'pdkroot' / 'processA').mkdir(parents=True, exist_ok=True)
    return p


def resolve(tmp_path, p, steps, overlay=None):
    configs = contract.resolve_step_configs(p, 'img', 'processA', steps,
                                            pdk_root=tmp_path / 'pdkroot',
                                            folder='x-config', overlay=overlay)
    root = p / 'phase3/librelane/x-config'
    return configs, json.loads((root / 'design.provenance.json').read_text())


def _def(name):
    return (f'VERSION 5.8 ;\nDESIGN {name} ;\nUNITS DISTANCE MICRONS 1000 ;\n'
            f'DIEAREA ( 0 0 ) ( 500000 500000 ) ;\nEND DESIGN\n')


# ── red on main ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize('steps', LAYOUT_CHAINS)
def test_a_chip_path_layout_chain_links_the_chip_top(tmp_path, resolver, steps):
    p = chip(tmp_path)
    configs, sources = resolve(tmp_path, p, steps)
    for step, path in configs.items():
        assert json.loads(path.read_text())['DESIGN_NAME'] == 'chip_top', step
    assert sources['DESIGN_NAME'].startswith(
        RECORD_REL + '.chip_top_module'), sources['DESIGN_NAME']
    assert "'core'" in sources['DESIGN_NAME']


def test_a_declared_top_that_is_neither_module_is_refused(tmp_path, resolver):
    p = chip(tmp_path, top_cell='something_else')
    with pytest.raises(contract.Refusal, match='LL_TOP_CELL_CONFLICT'):
        resolve(tmp_path, p, ['OpenROAD.GlobalPlacement'])
    assert resolver == []


def test_the_direct_chip_def_bridges_into_a_later_chain(tmp_path, resolver):
    """The direct deck's routed DEF on the chip path says DESIGN chip_top; a
    later LibreLane chain must take it, and its receipt names that top."""
    p = chip(tmp_path)
    configs, _ = resolve(tmp_path, p, ['OpenROAD.DetailedRouting'])
    views = {'def': put(tmp_path / 'v/routed.def', _def('chip_top')),
             'odb': put(tmp_path / 'v/routed.odb', 'odb'),
             'nl': put(tmp_path / 'v/chip.nl.v', 'module chip_top; endmodule\n'),
             'sdc': put(tmp_path / 'v/c.sdc', '')}
    out = tmp_path / 'bridge'
    contract.state_from_direct(p, 'img', configs['OpenROAD.DetailedRouting'], views, out)
    assert json.loads((out / 'bridge_receipt.json').read_text())['design_name'] == 'chip_top'
    # ...and the core's DEF is not silently accepted by a chip-top chain.
    views['def'] = put(tmp_path / 'v/core.def', _def('core'))
    with pytest.raises(contract.Refusal, match='LL_BRIDGE_DESIGN_MISMATCH'):
        contract.state_from_direct(p, 'img', configs['OpenROAD.DetailedRouting'],
                                   views, tmp_path / 'bridge2')


@pytest.mark.parametrize('view', ['def', 'post_cts_def'])
def test_a_handed_def_names_its_top_in_the_receipt(tmp_path, view):
    state = put(tmp_path / 'state_out.json',
                {view: str(put(tmp_path / 's/fp.def', _def('chip_top'))),
                 'odb': str(put(tmp_path / 's/fp.odb', 'odb'))})
    doc = contract.handoff_to_direct(state, {view: tmp_path / 'd/floorplan.def',
                                             'odb': tmp_path / 'd/fp.odb'},
                                     tmp_path / 'receipt.json')
    assert doc['views'][view]['design'] == 'chip_top'
    assert 'design' not in doc['views']['odb']
    assert json.loads((tmp_path / 'receipt.json').read_text())['views'][view]['design'] \
        == 'chip_top'


# ── controls: green on main and on the fix ───────────────────────────────────

def test_a_hardmacro_keeps_its_declared_top(tmp_path, resolver):
    p = chip(tmp_path, deliverable='HARDMACRO', macro_area_um=[0, 0, 500, 500],
             provenance={'deliverable': OWNER})
    configs, sources = resolve(tmp_path, p, ['OpenROAD.GlobalPlacement'])
    assert json.loads(configs['OpenROAD.GlobalPlacement'].read_text())['DESIGN_NAME'] == 'core'
    assert sources['DESIGN_NAME'].endswith('answers.top_cell')


def test_a_core_only_design_keeps_its_declared_top(tmp_path, resolver):
    p = chip(tmp_path, self_tapeout=False)
    configs, sources = resolve(tmp_path, p, ['OpenROAD.GlobalPlacement'])
    assert json.loads(configs['OpenROAD.GlobalPlacement'].read_text())['DESIGN_NAME'] == 'core'
    assert sources['DESIGN_NAME'].endswith('answers.top_cell')


@pytest.mark.parametrize('record', [None, dict(RECORD, verdict='REFUSED'),
                                    dict(RECORD, chip_top_module='core')])
def test_no_written_chip_top_leaves_the_declared_top(tmp_path, resolver, record):
    p = chip(tmp_path, record=record)
    configs, sources = resolve(tmp_path, p, ['OpenROAD.GlobalPlacement'])
    assert json.loads(configs['OpenROAD.GlobalPlacement'].read_text())['DESIGN_NAME'] == 'core'


def test_a_declared_chip_top_keeps_its_declared_source(tmp_path, resolver):
    p = chip(tmp_path, top_cell='chip_top')
    configs, sources = resolve(tmp_path, p, ['OpenROAD.GlobalPlacement'])
    assert json.loads(configs['OpenROAD.GlobalPlacement'].read_text())['DESIGN_NAME'] == 'chip_top'
    assert sources['DESIGN_NAME'].endswith('answers.top_cell')


def test_an_explicit_overlay_still_outranks_the_chip_top(tmp_path, resolver):
    p = chip(tmp_path)
    configs, sources = resolve(tmp_path, p, ['OpenROAD.GlobalPlacement'],
                               overlay={'DESIGN_NAME': ('other', 'test overlay')})
    assert json.loads(configs['OpenROAD.GlobalPlacement'].read_text())['DESIGN_NAME'] == 'other'


def test_a_record_naming_one_module_twice_is_no_chip_top(tmp_path, resolver):
    """A record whose chip top IS its core wrapped nothing: no substitution,
    and no conflict against a declared name either."""
    p = chip(tmp_path, record=dict(RECORD, chip_top_module='core'), top_cell='other')
    configs, sources = resolve(tmp_path, p, ['OpenROAD.GlobalPlacement'])
    assert json.loads(configs['OpenROAD.GlobalPlacement'].read_text())['DESIGN_NAME'] == 'other'
    assert sources['DESIGN_NAME'].endswith('answers.top_cell')
