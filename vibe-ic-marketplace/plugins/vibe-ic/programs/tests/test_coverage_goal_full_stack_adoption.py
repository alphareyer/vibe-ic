"""Reader integration controls; native proof validation stays with Step 5."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cpu_functional_oracle_waiver_check as gate
import bit_level_full_stack_tb_check as fs
import professional_tb_gen as professional


def _project(tmp_path):
    gd = tmp_path / 'phase1/generated_docs'
    gd.mkdir(parents=True)
    (gd / 'L10_TEST_CASES.json').write_text(json.dumps({'test_cases': [
        {'name': 'boundaries', 'kind': 'coverage_goal', 'stimulus': 'operand boundary scenarios',
         'expected': '100% PASS', 'coverage_scope': 'operand boundary scenarios'}]}))
    return tmp_path


def _native():
    return {'pass': True, 'vacuous_pass': False, 'functional_record': 'native/functional_cases.json',
            'population': [{'case': 'boundaries', 'family': 'declared_arithmetic_scenario',
                            'state': 'passed', 'checks': {'total': 25, 'passed': 25, 'errors': 0}}]}


def test_current_validated_full_stack_scenario_is_adopted(monkeypatch, tmp_path):
    project = _project(tmp_path)
    monkeypatch.setattr(fs, 'functional_full_stack_verdict', lambda p: (0, _native(), None))
    result = gate._coverage_goal_summary(project)
    assert result['rows'][0]['verdict'] == 'PASS'
    assert result['rows'][0]['native_comparisons']['total'] == 25


@pytest.mark.parametrize('change', ['fail', 'unmeasured', 'vacuous', 'wrong_family',
                                    'zero_population', 'missing_case', 'errors', 'short'])
def test_unaccepted_or_unmeasured_native_output_never_credits(monkeypatch, tmp_path, change):
    project = _project(tmp_path)
    native = _native()
    row = native['population'][0]
    if change in ('fail', 'unmeasured'):
        native['pass'] = False
        native['verdict'] = 'FAIL' if change == 'fail' else 'NOT_MEASURED'
    elif change == 'vacuous': native['vacuous_pass'] = True
    elif change == 'wrong_family': row['family'] = 'arithmetic_golden'
    elif change == 'zero_population': row['checks'].update(total=0, passed=0)
    elif change == 'missing_case': native['population'] = []
    elif change == 'errors': row['checks']['errors'] = 1
    elif change == 'short': row['state'] = 'short_population'
    monkeypatch.setattr(fs, 'functional_full_stack_verdict', lambda p: (
        1 if change == 'fail' else 2 if change == 'unmeasured' else 0, native, None))
    assert gate._coverage_goal_summary(project)['rows'][0]['verdict'] != 'PASS'


def test_existing_failed_scenario_keeps_precedence(monkeypatch, tmp_path):
    project = _project(tmp_path)
    original = gate._cgc.measure_goals
    def failed(*a, **k):
        summary = original(*a, **k)
        summary['rows'][0]['verdict'] = 'FAIL'
        return summary
    monkeypatch.setattr(gate._cgc, 'measure_goals', failed)
    monkeypatch.setattr(professional, 'random_pair_goal_measurement', lambda *a: {
        'verdict': 'NOT_MEASURED', 'why': 'unrelated old slot'})
    monkeypatch.setattr(fs, 'functional_full_stack_verdict', lambda p: (0, _native(), None))
    assert gate._coverage_goal_summary(project)['rows'][0]['verdict'] == 'FAIL'


def test_professional_native_failure_keeps_precedence(monkeypatch, tmp_path):
    project = _project(tmp_path)
    monkeypatch.setattr(professional, 'random_pair_goal_measurement', lambda *a: {
        'verdict': 'FAIL', 'why': 'current measured mismatch'})
    monkeypatch.setattr(fs, 'functional_full_stack_verdict', lambda p: (0, _native(), None))
    assert gate._coverage_goal_summary(project)['rows'][0]['verdict'] == 'FAIL'


@pytest.mark.parametrize('dimension', ['branch', 'toggle', 'instruction'])
def test_same_name_arithmetic_pass_cannot_credit_coverage_dimension(monkeypatch, tmp_path, dimension):
    project = _project(tmp_path)
    path = project / 'phase1/generated_docs/L10_TEST_CASES.json'
    doc = json.loads(path.read_text())
    goal = doc['test_cases'][0]
    goal.update(stimulus=dimension + ' coverage from random 10000 pairs',
                coverage_scope=dimension + ' coverage from random 10000 pairs')
    path.write_text(json.dumps(doc))
    monkeypatch.setattr(fs, 'functional_full_stack_verdict', lambda p: (0, _native(), None))
    measured = gate._coverage_goal_summary(project)['rows'][0]
    assert dimension in measured['dimensions']
    assert measured['verdict'] == 'NOT_MEASURED'


@pytest.mark.parametrize('duplicate_at', ['declared', 'native'])
def test_duplicate_case_name_cannot_bind_different_scopes(monkeypatch, tmp_path, duplicate_at):
    project = _project(tmp_path)
    native = _native()
    if duplicate_at == 'declared':
        path = project / 'phase1/generated_docs/L10_TEST_CASES.json'
        doc = json.loads(path.read_text())
        doc['test_cases'].append(dict(doc['test_cases'][0], stimulus='different reset scenarios',
                                      coverage_scope='different reset scenarios'))
        path.write_text(json.dumps(doc))
    else:
        native['population'].append(dict(native['population'][0]))
    monkeypatch.setattr(fs, 'functional_full_stack_verdict', lambda p: (0, native, None))
    assert all(row['verdict'] == 'NOT_MEASURED' for row in gate._coverage_goal_summary(project)['rows'])


def test_real_full_stack_consumer_rederives_family_from_measured_tb(monkeypatch, tmp_path):
    from test_fullstacktb_functional_population import _mk_project, _write, _FakeSim
    import full_stack_functional_tb as producer
    import testbench_gen
    case = {'name': 'boundaries', 'kind': 'coverage_goal', 'stimulus': 'operand boundary scenarios',
            'expected': '100% PASS', 'coverage_scope': 'operand boundary scenarios'}
    project = _mk_project(tmp_path, die=False, cases=[case])
    prior = producer.functional_dir(project)
    _write(prior / 'boundaries.v', 'module boundaries; reg clk=0, rst=1, y=0; reg [7:0] x=0; '
           'wire p; dut_core dut(.clk(clk),.rst(rst),.x(x),.y(y),.p(p)); endmodule\n')
    monkeypatch.setattr(testbench_gen, '_detect_ic_class', lambda p: 'digital_arithmetic_primitive')
    rec = producer.generate(project, route='HARDMACRO', dispatch=_FakeSim())
    assert rec['verdict'] == 'PASS', rec
    assert rec['cases'][0]['family'] == 'authored_or_delivered'
    rec['cases'][0]['family'] = 'declared_arithmetic_scenario'
    producer.record_path(project).write_text(json.dumps(rec))
    rc, native, _ = fs.functional_full_stack_verdict(project)
    assert rc == 0, native
    assert native['population'][0]['family'] == 'authored_or_delivered'
    assert gate._coverage_goal_summary(project)['rows'][0]['verdict'] == 'NOT_MEASURED'


def test_other_explicitly_bound_scenario_remains_required(monkeypatch, tmp_path):
    project = _project(tmp_path)
    path = project / 'phase1/generated_docs/L10_TEST_CASES.json'
    doc = json.loads(path.read_text())
    doc['test_cases'][0]['covered_by'] = ['separate_required_scenario']
    path.write_text(json.dumps(doc))
    monkeypatch.setattr(fs, 'functional_full_stack_verdict', lambda p: (0, _native(), None))
    measured = gate._coverage_goal_summary(project)['rows'][0]
    assert measured['bound_cases'] == ['separate_required_scenario']
    assert measured['verdict'] == 'NOT_MEASURED'
