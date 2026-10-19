"""Authored SystemVerilog unit TBs retain their source identity across emission."""
import json

import pytest

import testbench_gen as tb


def project(tmp_path):
    root = tmp_path / 'project'
    docs = root / 'phase1/generated_docs'
    docs.mkdir(parents=True)
    (docs / 'L10_TEST_CASES.json').write_text(json.dumps({'test_cases': [
        {'name': 'neutral_case', 'kind': 'functional_vector',
         'stimulus': '0', 'expected': '0'}]}))
    rtl = root / 'phase2/stage1/rtl'
    rtl.mkdir(parents=True)
    (rtl / 'neutral.v').write_text('module neutral(input d, output y); assign y=d; endmodule\n')
    return root


def authored_sv(root):
    path = root / 'phase2/stage1/sim/tb/neutral_case.sv'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('module neutral_case; initial begin #1; $finish; end endmodule\n')
    return path


def test_discovered_sv_is_preserved_by_public_helper(tmp_path):
    root = project(tmp_path)
    path = authored_sv(root)
    assert path in tb.authored_unit_tb_paths(root)
    report = {}
    assert tb.authored_oracle_preserved(path.parent, path.stem, report) == path
    assert report['preserved_authored'] == [{'case': path.stem, 'path': str(path)}]


def test_emitter_keeps_authored_sv_and_no_same_case_scaffold(tmp_path):
    root = project(tmp_path)
    path = authored_sv(root)
    before = path.read_bytes()
    report = {}
    assert tb.emit_unit_tbs(root, 'neutral', report=report) == 1
    assert path.read_bytes() == before
    assert not path.with_suffix('.v').exists()
    assert report['preserved_authored'] == [{'case': path.stem, 'path': str(path)}]
    rows = tb.oracle_provenance(root)['cases']
    assert len(rows) == 1 and rows[0]['case'] == path.stem
    assert rows[0]['source'] == tb.ORACLE_SOURCE_AUTHORED


def test_two_extensions_of_one_case_are_refused_before_emission(tmp_path):
    root = project(tmp_path)
    sv = authored_sv(root)
    verilog = sv.with_suffix('.v')
    verilog.write_bytes(sv.read_bytes())
    with pytest.raises(ValueError, match='duplicate unit TB case'):
        tb.authored_oracle_preserved(sv.parent, sv.stem, {})
    report = {}
    assert tb.emit_unit_tbs(root, 'neutral', report=report) == -2
    assert 'duplicate unit TB case' in report['reason']
    with pytest.raises(ValueError, match='duplicate unit TB case'):
        tb.authored_unit_tb_paths(root)
    execution = {}
    assert tb.run_unit_tbs(root, None, execution,
                           dispatch=lambda *_: pytest.fail('ambiguous case reached simulator')) == -2
    assert 'duplicate unit TB case' in execution['reason']
    assert sv.read_bytes() == verilog.read_bytes()


def test_sv_generated_marker_does_not_gain_authored_credit(tmp_path):
    root = project(tmp_path)
    sv = authored_sv(root)
    sv.write_text('// ' + tb.ORACLE_GENERATED_MARKER + 'unit\n' + sv.read_text())
    assert tb.authored_oracle_preserved(sv.parent, sv.stem, {}) is None
    assert sv not in tb.authored_unit_tb_paths(root)
    report = {}
    assert tb.emit_unit_tbs(root, 'neutral', report=report) == -2
    assert 'cannot refresh non-authored SystemVerilog' in report['reason']
    assert not sv.with_suffix('.v').exists()


def test_ordinary_consumer_selects_sv_and_reports_native_build_error(tmp_path):
    root = project(tmp_path)
    sv = authored_sv(root)
    assert tb.emit_unit_tbs(root, 'neutral') == 1
    builds = []

    def dispatch(argv, *_):
        if '--version' in argv:
            return 0, 'Verilator available'
        builds.append(argv)
        return 1, 'deliberate native build refusal'

    report = {}
    assert tb.run_unit_tbs(root, None, report, dispatch=dispatch) == 0
    assert report['errored'] == 1 and report['sim_executed'] is False
    assert any(str(sv) in arg for argv in builds for arg in argv)
