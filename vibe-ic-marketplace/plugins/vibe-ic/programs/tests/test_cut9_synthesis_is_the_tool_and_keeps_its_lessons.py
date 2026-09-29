"""CUT_W4 item 1 (R-0929-TOOL-DEFAULT): step 9 on the chip path is LibreLane
Yosys.Synthesis, and the lessons of the direct recipe it replaces reach the
tool's configuration.

MEASURED on the spm IC/DIE tool arm (cut_9/tool, vibeic-eda 0.3.86): the
declared MAX_FANOUT_CONSTRAINT=4 resolved while SYNTH_ABC_BUFFER_ONLY and
SYNTH_ABC_BUFFERING were both False, so the tool's ABC script carried no
`buffer -N` and synthesis never bounded fanout at the sign-off cap.
"""
import json
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import librelane_contract as contract  # noqa: E402
from test_librelane_contract import design  # noqa: E402  (the contract's own fixture)


def _chip_project(tmp_path: Path) -> Path:
    p = design(tmp_path)
    rtl = p / 'phase2/stage1/rtl'
    rtl.mkdir(parents=True, exist_ok=True)
    (rtl / 'block.v').write_text(
        'module block(input clk, input d, output reg q); '
        'always @(posedge clk) q <= d; endmodule\n')
    return p


def test_the_tool_synthesis_bounds_fanout_without_sizing(tmp_path):
    p = _chip_project(tmp_path)
    rtl = [p / 'phase2/stage1/rtl/block.v']
    out = p / 'phase3/librelane/synth_config.json'
    result = contract.emit_synthesis_config(p, 'processA', out, rtl, [], False)
    assert result['SYNTH_ABC_BUFFER_ONLY'] is True
    # buffer-only is exclusive with the sizing variant (construct_abc_script.py:72)
    assert not result.get('SYNTH_ABC_BUFFERING')
    prov = json.loads(out.with_suffix('.provenance.json').read_text())
    assert '_ABC_FANOUT_SCRIPT' in prov['SYNTH_ABC_BUFFER_ONLY']


def test_the_chip_path_defaults_step_9_to_the_tool(monkeypatch, tmp_path):
    import _tapeout_declaration as TD
    monkeypatch.setattr(TD, 'requests_pad_ring', lambda project: True)
    assert contract.selected_mode(tmp_path, '9') == 'librelane'


def test_a_core_only_design_keeps_the_direct_recipe(monkeypatch, tmp_path):
    import _tapeout_declaration as TD
    monkeypatch.setattr(TD, 'requests_pad_ring', lambda project: False)
    assert contract.selected_mode(tmp_path, '9') == 'direct'


def test_a_project_may_name_step_9_direct(monkeypatch, tmp_path):
    import _tapeout_declaration as TD
    monkeypatch.setattr(TD, 'requests_pad_ring', lambda project: True)
    (tmp_path / 'phase3').mkdir()
    (tmp_path / 'phase3/librelane_switch.json').write_text(
        json.dumps({'steps': {'9': 'direct'}}))
    assert contract.selected_mode(tmp_path, '9') == 'direct'
