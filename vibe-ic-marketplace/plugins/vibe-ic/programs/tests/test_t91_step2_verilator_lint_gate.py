"""Step 2 lint through LibreLane Verilator.Lint (T91, lane mig-rtlver).

The transcripts are real LibreLane Verilator.Lint output from the released
image (programs/calibration/librelane_verilator_lint_*.log). Only the tool's
file writes are substituted, at the subprocess edge of librelane_contract.
"""
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
contract = importlib.import_module('librelane_contract')
hub = importlib.import_module('_rtl_include_hub')

CAL = PROGRAMS / 'calibration'
POS = (CAL / 'librelane_verilator_lint_pos.log').read_text()
NEG = (CAL / 'librelane_verilator_lint_neg.log').read_text()
POS_SRC = '/tmp/vlcal/phase2/stage1/rtl/cal_pos.v'
NEG_SRC = '/tmp/vlcal/phase2/stage1/rtl/cal_neg.v'
# The MULTIDRIVEN diagnostic alone, cut from the real positive transcript.
MULTI = POS[POS.index('%Warning-MULTIDRIVEN'):POS.index('%Error: Exiting')]


def gate():
    return importlib.import_module('verilator_lint_gate')


def put(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))
    return path


def step_dir(root, log, linted, errors, warnings, latch=0):
    root.mkdir(parents=True, exist_ok=True)
    (root / 'verilator-lint.log').write_text(log)
    put(root / 'config.json', {'VERILOG_FILES': [str(p) for p in linted]})
    put(root / 'state_out.json', {'metrics': {
        'design__lint_error__count': errors, 'design__lint_warning__count': warnings,
        'design__inferred_latch__count': latch,
        'design__lint_timing_construct__count': 0}})
    return root


def rtl(tmp_path, name='cal_pos.v', body='module cal_pos; endmodule\n'):
    path = tmp_path / 'design/phase2/stage1/rtl' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def test_error_and_curated_warning_in_the_design_block(tmp_path):
    src = rtl(tmp_path)
    folder = step_dir(tmp_path / 'lint', POS.replace(POS_SRC, str(src)), [src], 1, 1, 1)
    report = gate().judge(folder, [src])
    assert report['verdict'] == 'FAIL'
    assert sorted(row['code'] for row in report['blocking']) == ['LATCH', 'MULTIDRIVEN']


def test_curated_warning_alone_blocks_in_the_design(tmp_path):
    src = rtl(tmp_path)
    folder = step_dir(tmp_path / 'lint', MULTI.replace(POS_SRC, str(src)), [src], 0, 1)
    report = gate().judge(folder, [src])
    assert (report['verdict'], [r['code'] for r in report['blocking']]) == \
        ('FAIL', ['MULTIDRIVEN'])


def test_curated_warning_outside_the_design_sources_does_not_block(tmp_path):
    src = rtl(tmp_path)
    model = str(tmp_path / 'toolbox_tmp/cells.bb.v')
    folder = step_dir(tmp_path / 'lint', MULTI.replace(POS_SRC, model), [src], 0, 1)
    report = gate().judge(folder, [src])
    assert (report['verdict'], report['blocking']) == ('PASS', [])
    assert [r['code'] for r in report['diagnostics']] == ['MULTIDRIVEN']


def test_uncurated_warning_is_reported_and_passes(tmp_path):
    src = rtl(tmp_path, 'cal_neg.v')
    folder = step_dir(tmp_path / 'lint', NEG.replace(NEG_SRC, str(src)), [src], 0, 1)
    report = gate().judge(folder, [src])
    assert report['verdict'] == 'PASS'
    assert [r['code'] for r in report['diagnostics']] == ['UNUSEDSIGNAL']


def test_empty_file_set_is_not_measured_never_pass(tmp_path):
    folder = step_dir(tmp_path / 'lint', NEG, [], 0, 1)
    report = gate().judge(folder, [])
    assert (report['verdict'], report['reason_class']) == ('NOT_MEASURED', 'INPUT_ABSENT')
    assert gate().main(['--step-dir', str(folder), '--json', str(tmp_path / 'g.json')]) == 2


def test_a_v_file_the_linter_skipped_is_a_file_set_mismatch(tmp_path):
    """The step-2 lint once globbed *.sv only and linted none of a .v design."""
    sv = rtl(tmp_path, 'top.sv')
    v = rtl(tmp_path, 'leaf.v')
    folder = step_dir(tmp_path / 'lint', NEG.replace(NEG_SRC, str(sv)), [sv], 0, 1)
    report = gate().judge(folder, [sv, v])
    assert (report['verdict'], report['reason_class']) == ('FAIL', 'LINT_FILE_SET_MISMATCH')
    assert report['file_set']['missing'] == [str(v.resolve())]


def test_reader_disagreeing_with_the_tool_counters_is_not_measured(tmp_path):
    src = rtl(tmp_path)
    folder = step_dir(tmp_path / 'lint', POS.replace(POS_SRC, str(src)), [src], 0, 1)
    report = gate().judge(folder, [src])
    assert (report['verdict'], report['reason_class']) == ('NOT_MEASURED', 'INSTRUMENT_DISAGREES')


def test_missing_step_output_is_not_measured(tmp_path):
    report = gate().judge(tmp_path / 'absent', [rtl(tmp_path)])
    assert (report['verdict'], report['reason_class']) == ('NOT_MEASURED', 'TOOL_OUTPUT_ABSENT')


def test_silicon_selection_takes_v_and_sv_and_skips_testbenches(tmp_path):
    for name in ('core.v', 'bus_pkg.sv', 'core_tb.sv', 'top.sv'):
        rtl(tmp_path, name)
    select = getattr(hub, 'silicon_rtl_selection', None)
    names = [p.name for p in select(tmp_path / 'design/phase2/stage1/rtl')] if select else []
    assert names == ['bus_pkg.sv', 'top.sv', 'core.v']


def test_lint_config_carries_declared_inputs_and_refuses_foreign_rtl(tmp_path):
    src = rtl(tmp_path)
    out = tmp_path / 'design/cfg.json'
    config = contract.emit_lint_config(tmp_path / 'design', 'pdkA', out, 'cal_pos',
                                       [src], 'L9_INTEGRATION_SPEC.top_module')
    assert config['meta'] == {'step': 'Verilator.Lint'}
    assert config['VERILOG_FILES'] == [str(src.resolve())]
    assert set(json.loads(out.with_suffix('.provenance.json').read_text())) == \
        {'DESIGN_NAME', 'PDK', 'VERILOG_FILES'}
    with pytest.raises(contract.Refusal, match='LL_LINT_INPUT_MISSING'):
        contract.emit_lint_config(tmp_path / 'design', 'pdkA', out, 'cal_pos',
                                  [tmp_path / 'elsewhere.v'], 'x')
    with pytest.raises(contract.Refusal, match='LL_TOP_UNDECLARED'):
        contract.emit_lint_config(tmp_path / 'design', 'pdkA', out, '', [src], 'x')


# ---- the runner call site -------------------------------------------------

def _design(tmp_path, mode, log, errors, warnings):
    project = tmp_path / 'design'
    src = rtl(tmp_path, 'cal_pos.v', (
        'module cal_pos(input wire clk, input wire a, output reg q);\n'
        '    always @(posedge clk) q <= a;\n'
        '    always @(negedge clk) q <= ~a;\nendmodule\n'))
    rtl(tmp_path, 'cal_pos_tb.v', 'module cal_pos_tb; endmodule\n')
    put(project / 'phase1/generated_docs/L9_INTEGRATION_SPEC.json', {'top_module': 'cal_pos'})
    if mode:
        put(project / 'phase3/librelane_switch.json',
            {'steps': {'2': mode}, 'image': 'img', 'pdk': 'pdkA'})
    calls = []

    def tool(cmd, **_):
        calls.append(cmd)
        if '-c' in cmd and 'Config.load' in cmd[cmd.index('-c') + 1]:
            script = cmd[cmd.index('-c') + 1]
            source = script.split("p='", 1)[1].split("'", 1)[0]
            output = script.split("out='", 1)[1].split("'", 1)[0]
            Path(output).write_text(Path(source).read_text())
        elif 'librelane.steps' in cmd and 'run' in cmd:
            folder = Path(cmd[cmd.index('-o') + 1])
            config = json.loads(Path(cmd[cmd.index('-c') + 1]).read_text())
            step_dir(folder, log.replace(POS_SRC, config['VERILOG_FILES'][0]),
                     config['VERILOG_FILES'], errors, warnings)
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    return project, src, calls, tool


def _runner(monkeypatch, tool):
    runner = importlib.import_module('design_one_shot_runner')
    monkeypatch.setattr(contract.subprocess, 'run', tool)
    return runner, getattr(runner, 'step_rtl_lint_tool', None)


def test_librelane_mode_lints_the_synthesis_file_set_and_blocks(tmp_path, monkeypatch):
    project, src, calls, tool = _design(tmp_path, 'librelane', MULTI, 0, 1)
    _, step = _runner(monkeypatch, tool)
    row = step(project) if step else None
    report_path = project / 'reports/phase2/lint/verilator_lint_gate.json'
    report = json.loads(report_path.read_text()) if report_path.is_file() else {}
    assert report.get('file_set', {}).get('linted') == [str(src.resolve())]
    assert (row.status if row else None, report.get('verdict')) == ('FAIL', 'FAIL')
    assert any('librelane.steps' in c for c in calls)


def test_direct_mode_is_unchanged_and_adds_no_row(tmp_path, monkeypatch):
    project, _, calls, tool = _design(tmp_path, None, NEG, 0, 1)
    runner, step = _runner(monkeypatch, tool)
    assert step is not None and step(project) is None and calls == []


def test_dual_mode_blocks_on_the_union_and_names_the_arm(tmp_path, monkeypatch):
    project, src, _, tool = _design(tmp_path, 'dual', MULTI, 0, 1)
    _, step = _runner(monkeypatch, tool)
    row = step(project) if step else None
    dual_path = project / 'reports/phase2/lint/lint_dual.json'
    dual = json.loads(dual_path.read_text()) if dual_path.is_file() else {}
    assert (row.status if row else None, dual.get('verdict')) == ('FAIL', 'FAIL')
    assert dual['arms']['librelane']['verdict'] == 'FAIL'
    assert dual['only_librelane'] == [[src.name, 5]]
    assert (project / 'reports/phase2/lint/rtl_hygiene_direct_arm.json').is_file()
