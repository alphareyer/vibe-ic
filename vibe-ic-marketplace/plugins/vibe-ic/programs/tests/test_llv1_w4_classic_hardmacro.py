"""llv1 W4: a HARDMACRO / core-only design under `--librelane` is resolved
through LibreLane's Classic flow (decision 22), with Classic's own die rules.

Contracts:
  1. Without the flag nothing moves: every design resolves through Chip, and a
     core-only design still gets no VERILOG_FILES from `emit_config`.
  2. Under the flag a design with no pad ring resolves through Classic; a
     pad-ring die still resolves through Chip.
  3. Classic's VERILOG_FILES is the design's build closure (the read phase-3
     synthesis takes), with no chip-top wrapper.
  4. Die precedence (0.5ic row): a declared die wins; else a declared
     utilisation sizes it (FP_SIZING relative, sourced to the declaration);
     else the TOOL sizes it and `tool_default_die` records exactly what it
     applied, with its source. The run's own direct-deck floorplan record (the
     0.25 routing-headroom fallback) is never a Classic die source.
  5. An unknown flow is refused by name.

Real `librelane_contract.resolve_step_configs`; the only substitution is the
image's resolver (a `docker run`), whose file writes are modelled with
LibreLane's own Classic defaults (FP_SIZING relative, FP_CORE_UTIL 50), the
values the real 0.3.83 image resolved for this exact shape
(LLV1_W4.md, "real-image check").
"""
from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
contract = importlib.import_module('librelane_contract')
IF = importlib.import_module('_impl_flow')
DD = importlib.import_module('_declared_die')
from test_t95_mig_front import design, put  # noqa: E402
import _owner_declared as _OD  # noqa: E402

CHAIN = ['OpenROAD.Floorplan', 'OpenROAD.IOPlacement']
#: The direct deck's auto-die record on a HARDMACRO run (0.25 fallback).
RECORD = {'program': 'phase3_one_shot_runner.step_pnr',
          'die_rect_um': [0, 0, 400, 400],
          'die_source': 'auto-die routing-headroom-default 0.25',
          'floorplan_rect_um': None, 'floorplan_rect_is_the_die': True,
          'core_pad_um': 10}


@pytest.fixture
def resolver(monkeypatch):
    """The image's resolver, modelled with LibreLane's Classic defaults for
    every die key the design leaves out; records the flow each call named."""
    calls = []

    def run(cmd, **_kw):
        script = cmd[cmd.index('-c') + 1]
        match = re.search(r"Flow\.factory\.get\('(\w+)'\)", script)
        flow = match.group(1) if match else (
            'Chip' if 'from librelane.flows.chip import Chip\n' in script else None)
        design_json, requested, root = Path(cmd[-5]), Path(cmd[-4]), Path(cmd[-3])
        d = json.loads(design_json.read_text())
        calls.append({'flow': flow, 'design': d, 'script': script})
        for s in json.loads(requested.read_text()):
            put(root / f'{s}.json', {
                'DESIGN_NAME': d.get('DESIGN_NAME'),
                'VERILOG_FILES': d.get('VERILOG_FILES'),
                'DIE_AREA': d.get('DIE_AREA'), 'CORE_AREA': d.get('CORE_AREA'),
                'FP_SIZING': d.get('FP_SIZING', 'relative'),
                'FP_CORE_UTIL': d.get('FP_CORE_UTIL', '50'),
                'meta': {'step': s, 'librelane_version': 'x.y'}})
            put(root / f'{s}.views.json', {'step': s, 'inputs': [], 'outputs': []})
        put(root / 'flow_gates.json', {})
        return contract.subprocess.CompletedProcess(cmd, 0, '', '')

    monkeypatch.setattr(contract, 'image_capability', lambda *a, **k: {})
    monkeypatch.setattr(contract.subprocess, 'run', run)
    return calls


def macro(tmp_path, *, flag=True, record=RECORD, l19=None, **answers):
    p = design(tmp_path, {})
    (p / 'input/submission_template/tapeout_declaration.json').write_text(json.dumps(_OD.attest(
        {'schema': 'vibe-ic/tapeout_declaration/1',
         'answers': {'deliverable': 'HARDMACRO', 'top_cell': 'core', **answers}})))
    put(p / 'phase2/stage1/rtl/core.v', 'module core(input clk); sub u(); endmodule\n')
    put(p / 'phase2/stage1/rtl/sub.v', 'module sub(); endmodule\n')
    if l19 is not None:
        put(p / 'phase1/generated_docs/L19_CONSTRAINTS_PDK.json',
            {'fields': {'constraint_declarations': l19}})
    if record is not None:
        put(p / DD.FLOORPLAN_RECTANGLES_REL, record)
    (tmp_path / 'pdkroot' / 'processA').mkdir(parents=True, exist_ok=True)
    if flag:
        IF.write_record(p, 'librelane', resolved_by='test')
    return p


def chip(tmp_path):
    p = design(tmp_path, {'deliverable': 'DIE', 'top_cell': 'core',
                          'die_area_um': [0, 0, 3000, 3000]},
               pads={'PAD_SOUTH': ['u_a'], 'PAD_NORTH': ['u_b'], 'PAD_EAST': [], 'PAD_WEST': []})
    st = p / 'input' / 'submission_template'
    (st / 'SELF_TAPEOUT.txt').write_text('# self tape-out\n')
    (st / 'tapeout_declaration.json').write_text(json.dumps(_OD.attest(
        {'schema': 'vibe-ic/tapeout_declaration/1',
         'answers': {'deliverable': 'DIE', 'top_cell': 'core',
                     'die_area_um': [0, 0, 3000, 3000]}})))
    (tmp_path / 'pdkroot' / 'processA').mkdir(parents=True, exist_ok=True)
    IF.write_record(p, 'librelane', resolved_by='test')
    return p


def resolve(tmp_path, p, steps=CHAIN):
    configs = contract.resolve_step_configs(p, 'img', 'processA', steps,
                                            pdk_root=tmp_path / 'pdkroot',
                                            folder='w4-config')
    root = p / 'phase3/librelane/w4-config'
    return ({s: json.loads(c.read_text()) for s, c in configs.items()},
            json.loads((root / 'design.provenance.json').read_text()), root)


def test_without_the_flag_every_design_resolves_through_chip_as_before(tmp_path, resolver):
    p = macro(tmp_path, flag=False)
    assert getattr(contract, 'resolution_flow', lambda _p: 'Chip')(p) == 'Chip'
    configs, _, _ = resolve(tmp_path, p, ['OpenROAD.Floorplan'])
    assert resolver[-1]['flow'] == 'Chip'
    assert 'Flow.factory' not in resolver[-1]['script']
    assert configs['OpenROAD.Floorplan']['VERILOG_FILES'] is None
    # The old behaviour on a HARDMACRO: the direct deck's die reached the tool.
    assert configs['OpenROAD.Floorplan']['DIE_AREA'] == RECORD['die_rect_um']


def test_under_the_flag_a_hardmacro_resolves_through_classic(tmp_path, resolver):
    p = macro(tmp_path)
    assert contract.librelane_flow(p)[0] == 'Classic'
    resolve(tmp_path, p)
    assert resolver[-1]['flow'] == 'Classic'


def test_under_the_flag_a_pad_ring_die_still_resolves_through_chip(tmp_path, resolver):
    p = chip(tmp_path)
    assert contract.librelane_flow(p)[0] == 'Chip'
    resolve(tmp_path, p, ['OpenROAD.Floorplan'])
    assert resolver[-1]['flow'] == 'Chip'


def test_classic_verilog_files_is_the_build_closure_without_a_wrapper(tmp_path, resolver):
    p = macro(tmp_path)
    configs, sources, _ = resolve(tmp_path, p)
    files = configs['OpenROAD.Floorplan']['VERILOG_FILES']
    assert sorted(files) == ['dir::phase2/stage1/rtl/core.v', 'dir::phase2/stage1/rtl/sub.v']
    assert 'chip_top_io.v' not in json.dumps(files)
    assert 'Classic flow' in sources['VERILOG_FILES']


def test_no_declared_die_the_tool_sizes_it_and_the_default_is_recorded(tmp_path, resolver):
    p = macro(tmp_path)
    configs, sources, root = resolve(tmp_path, p)
    fp = configs['OpenROAD.Floorplan']
    # The run's 0.25 auto-die is not a Classic die source.
    assert fp['DIE_AREA'] is None and 'DIE_AREA' not in sources
    defaults = contract.tool_default_die(root)
    assert defaults['FP_CORE_UTIL']['value'] == '50'
    assert defaults['FP_SIZING']['value'] == 'relative'
    for row in defaults.values():
        assert 'default applied by OpenROAD.Floorplan' in row['source']
        assert 'x.y' in row['source']


def test_a_declared_utilisation_sizes_the_classic_die(tmp_path, resolver):
    p = macro(tmp_path, l19=[{'token': 'FP_CORE_UTIL', 'value': '35%',
                               'source': 'L9_constraints', 'line': 7}])
    configs, sources, root = resolve(tmp_path, p)
    fp = configs['OpenROAD.Floorplan']
    assert fp['FP_CORE_UTIL'] == 35 and fp['FP_SIZING'] == 'relative'
    assert 'L19_CONSTRAINTS_PDK' in sources['FP_SIZING']
    assert contract.tool_default_die(root) == {}


def test_a_declared_macro_area_wins(tmp_path, resolver):
    p = macro(tmp_path, macro_area_um=[0, 0, 250, 300])
    configs, sources, root = resolve(tmp_path, p)
    fp = configs['OpenROAD.Floorplan']
    assert fp['DIE_AREA'] == [0, 0, 250, 300] and fp['FP_SIZING'] == 'absolute'
    assert 'macro_area_um' in sources['DIE_AREA']
    assert contract.tool_default_die(root) == {}


def test_a_declared_absolute_sizing_is_not_replaced_by_utilisation(tmp_path, resolver):
    p = macro(tmp_path, fp_sizing='absolute',
              l19=[{'token': 'FP_CORE_UTIL', 'value': '35%', 'source': 'L9', 'line': 7}])
    with pytest.raises(contract.Refusal) as exc:
        resolve(tmp_path, p)
    assert exc.value.code == 'LL_CLASSIC_DIE_UNDERIVABLE'


def test_tool_defaults_ignore_a_stale_unrequested_step_config(tmp_path, resolver):
    p = macro(tmp_path, l19=[{'token': 'FP_CORE_UTIL', 'value': '35%', 'source': 'L9', 'line': 7}])
    _, _, root = resolve(tmp_path, p, ['OpenROAD.Floorplan'])
    (p / 'phase1/generated_docs/L19_CONSTRAINTS_PDK.json').unlink()
    resolve(tmp_path, p, ['OpenROAD.IOPlacement'])
    assert contract.tool_default_die(root)['FP_CORE_UTIL']['value'] == '50'


def test_an_unknown_flow_is_refused(tmp_path, resolver):
    p = macro(tmp_path)
    with pytest.raises(contract.Refusal) as exc:
        contract.resolve_step_configs(p, 'img', 'processA', CHAIN,
                                      pdk_root=tmp_path / 'pdkroot', flow='Misc')
    assert exc.value.code == 'LL_FLOW_UNSUPPORTED'
