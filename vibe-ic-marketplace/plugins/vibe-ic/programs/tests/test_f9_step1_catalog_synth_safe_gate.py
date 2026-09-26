"""F9: step 1 runs catalog_synth_safe_params_check; applicability is DECLARED.

The gate applies when the design's own input docs name a catalog IP as its
reuse (``ip_catalog_query.declared_catalog_reuse``, the reading that routes
``step_rtl_gen`` to ``catalog-glue-author``). The step's verdict is read through
the real ``flow_compliance_check.check_step`` on step 1 as the flow YAML
declares it, and the gate runs as the flow runs it: a subprocess that
elaborates with real Yosys (host, else the EDA image). Nothing is faked.

  declared catalog IP, unsafe parameter   -> gate FAIL, step 1 FAIL
  declared catalog IP, safe parameter     -> gate PASS, step 1 PASS
  no declared catalog IP                  -> DESIGN_DECLARED_NA, step 1 PASS
  declaration missing / unparseable       -> refused (rc 1), step 1 FAIL
  pull record without a declaration       -> judged, never N/A

``tempfile.mkdtemp`` rather than ``tmp_path``: in the EDA image ``tmp_path``
carries a newline, and the gate hands these paths to a Yosys script.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

PLUGIN = Path(__file__).resolve().parents[2]
PROGRAMS = PLUGIN / 'programs'
sys.path.insert(0, str(PROGRAMS))
import flow_compliance_check as fcc  # noqa: E402

CAL = PROGRAMS / 'calibration'
GATE_CMD = ('catalog_synth_safe_params_check . --json '
            'reports/phase2/gates/catalog_synth_safe_params.json')
REPORT = 'reports/phase2/gates/catalog_synth_safe_params.json'
# serv's manifest matches this L2 (rv32i, bit-serial). Only the SENTENCE naming
# serv makes it the design's declared reuse; the match alone is a guess.
L2_MATCH = {'cpu_isa': 'rv32i', 'cpu_arch': 'bit-serial'}
NAMES_SERV = 'The design reuses the serv core from the IP catalog.'


def _step1():
    flow = yaml.safe_load((PLUGIN / 'flow/phase1_phase2_phase3.yaml').read_text())
    return next(s for s in flow['steps'] if str(s.get('id')) == '1')


@pytest.fixture
def root():
    path = Path(tempfile.mkdtemp(prefix='f9_'))
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _put(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(obj if isinstance(obj, str) else json.dumps(obj))


def _catalog_design(root, glue, l2):
    """The calibration IP, staged under one of serv's manifest file names."""
    p = root / 'proj'
    rtl = p / 'phase2/stage1/rtl'
    rtl.mkdir(parents=True)
    shutil.copy(CAL / 'cal_catalog_ip.v', rtl / 'serv_top.v')
    shutil.copy(CAL / f'cal_catalog_glue_{glue}.v', rtl / 'glue.v')
    _put(p / 'phase1/generated_docs/L2_FRS.json', l2)
    _put(p / 'phase1/generated_docs/L9_INTEGRATION_SPEC.json', {'top_module': 'top'})
    return p


def _plain_design(root, docs=True):
    p = root / 'proj'
    _put(p / 'phase2/stage1/rtl/core.v',
         'module core (input clk, output reg q);\n'
         '  always @(posedge clk) q <= ~q;\nendmodule\n')
    if docs:
        _put(p / 'phase1/generated_docs/L1_DATASHEET.json',
             {'description': 'a toggle flip-flop'})
    return p


def _run(p):
    result = fcc.check_step(p, _step1(), {})
    report = json.loads((p / REPORT).read_text())
    return result, report


def _declared(report):
    return (report.get('declaration') or {}).get('declared_catalog_reuse')


def test_step1_declares_the_gate_as_a_blocking_clause():
    clauses = _step1()['gate']['all_of']
    assert {'program_exit_zero': GATE_CMD} in clauses


def test_declared_catalog_ip_with_an_unsafe_parameter_fails_step1(root):
    p = _catalog_design(root, 'unsafe', dict(L2_MATCH, description=NAMES_SERV))
    result, report = _run(p)
    assert (report['verdict'], result.status) == ('FAIL', 'FAIL')
    unsafe = {r['hdlname'] for r in report['instances'] if not r['safe']}
    assert unsafe == {'ipcore', 'ipwrap'}
    assert any('catalog_synth_safe_params_check' in r for r in result.reasons)
    assert _declared(report) == ['serv']


def test_declared_catalog_ip_with_a_safe_parameter_passes_step1(root):
    p = _catalog_design(root, 'pinned', dict(L2_MATCH, description=NAMES_SERV))
    result, report = _run(p)
    assert (report['verdict'], result.status) == ('PASS', 'PASS')
    assert [r['hdlname'] for r in report['instances'] if r['safe']] == [
        r['hdlname'] for r in report['instances']] != []
    assert _declared(report) == ['serv']


def test_a_catalog_match_the_docs_do_not_name_is_not_a_declaration(root):
    # The same RTL and a serv-matching L2 -- but the docs never name serv, so
    # the design declares no reuse. The gate stands down from the DECLARATION.
    p = _catalog_design(root, 'unsafe', L2_MATCH)
    result, report = _run(p)
    assert report['verdict'] == 'NOT_APPLICABLE'
    assert 'yosys' not in report
    assert _declared(report) == []


def test_no_declared_catalog_ip_is_declared_na_and_step1_keeps_pass(root):
    p = _plain_design(root)
    result, report = _run(p)
    assert (report['verdict'], result.status) == ('NOT_APPLICABLE', 'PASS')
    assert report.get('reason_class') == 'DESIGN_DECLARED_NA'
    assert any('stated_class=DESIGN_DECLARED_NA' in r for r in result.reasons)
    assert (report.get('declaration') or {}).get('documents') == [
        'phase1/generated_docs/L1_DATASHEET.json']
    assert (report.get('applicability_evidence') or {}).get(
        'declared_population') == 0
    assert 'yosys' not in report


@pytest.mark.parametrize('state', ['missing', 'unparseable'])
def test_an_unread_declaration_is_refused_never_na(root, state):
    p = _plain_design(root, docs=(state == 'unparseable'))
    if state == 'unparseable':
        _put(p / 'phase1/generated_docs/L2_FRS.json', '{"cpu_isa": ')
    result, report = _run(p)
    assert (report['verdict'], result.status) == ('NOT_MEASURED', 'FAIL')
    assert report['reason'].startswith(
        'declaration missing' if state == 'missing' else 'declaration unreadable')


def test_a_pull_record_widens_the_judged_set_and_never_makes_it_na(root):
    # No declaration names serv; the flow's own pull record says it copied it.
    # That artefact may add an IP to judge, so the unsafe glue still FAILs.
    p = _catalog_design(root, 'unsafe', {'description': 'a counter'})
    (p / 'phase2/stage1/rtl/serv_top.v').rename(p / 'phase2/stage1/rtl/ip.v')
    _put(p / 'plugin_output/declaration.json', {'ip_catalog_used': [
        {'ip_name': 'serv', 'status': 'PASS',
         'files_copied': [{'dest': str(p / 'phase2/stage1/rtl/ip.v')}]}]})
    result, report = _run(p)
    assert (report['verdict'], result.status) == ('FAIL', 'FAIL')
    assert _declared(report) == []

