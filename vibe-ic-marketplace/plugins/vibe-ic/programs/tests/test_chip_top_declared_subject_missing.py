"""A catalog helper must never replace an unimplemented design subject."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import design_one_shot_runner as runner


def project(tmp_path, top, sources):
    docs = tmp_path / 'phase1/generated_docs'
    docs.mkdir(parents=True)
    (docs / 'L9_INTEGRATION_SPEC.json').write_text(json.dumps({
        'top_module': top,
        'top_ports': [{'name': 'clk', 'direction': 'input'},
                      {'name': 'out', 'direction': 'output'}],
    }))
    rtl = tmp_path / 'phase2/stage1/rtl'
    rtl.mkdir(parents=True)
    for name, text in sources.items():
        (rtl / name).write_text(text)
    return rtl


def test_missing_declared_subject_refuses_pin_matching_helper(tmp_path, capsys):
    rtl = project(tmp_path, 'required_soc', {
        'helper.v': 'module helper(input clk, output out); assign out=clk; endmodule',
        'leaf.v': 'module leaf(input data, output result); assign result=data; endmodule',
    })
    assert runner._autoemit_chip_top_wrapper(tmp_path, rtl, 'chip_top') is None
    assert not (rtl / 'chip_top.v').exists()
    assert 'CHIP_TOP_SUBJECT_MISSING' in capsys.readouterr().out


def test_present_declared_subject_wins_in_multimodule_file(tmp_path):
    rtl = project(tmp_path, 'required_soc', {
        'helper.v': ('module helper(input clk, output out); assign out=clk; endmodule\n'
                     'module required_soc(input clk, output out); helper u(clk,out); endmodule'),
    })
    emitted = runner._autoemit_chip_top_wrapper(tmp_path, rtl, 'chip_top')
    assert emitted is not None
    assert 'required_soc u_dut' in emitted.read_text()


def test_wrapper_sentinel_preserves_single_leaf_autoemit(tmp_path):
    rtl = project(tmp_path, 'chip_top', {
        'leaf.v': 'module leaf(input clk, output out); assign out=clk; endmodule',
    })
    emitted = runner._autoemit_chip_top_wrapper(tmp_path, rtl, 'chip_top')
    assert emitted is not None
    assert 'leaf u_dut' in emitted.read_text()


def test_existing_authored_wrapper_is_preserved(tmp_path):
    text = 'module chip_top(input clk, output out); assign out=clk; endmodule'
    rtl = project(tmp_path, 'required_soc', {'chip_top.v': text})
    assert runner._autoemit_chip_top_wrapper(tmp_path, rtl, 'chip_top') is None
    assert (rtl / 'chip_top.v').read_text() == text
