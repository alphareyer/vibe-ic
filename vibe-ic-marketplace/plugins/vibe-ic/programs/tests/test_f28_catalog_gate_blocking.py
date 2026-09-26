"""F28: step 1's catalog synth-safe gate BLOCKS inline, and a declared catalog IP
the elaborated top never reaches FAILs.

Two owner rulings on the gate F9 wired:

1. ``design_one_shot_runner`` runs the gate at step 1 and its exit status
   decides the step (``step_catalog_synth_safe_params``); the gate's header
   says ``ENFORCEMENT: blocking``. The two change together:
   ``flow_gate_enforcement_audit`` reads a blocking declaration without an
   inline spawn as ``contradiction::``, and an inline spawn under an advisory
   declaration as ``declared_weaker_than_wired``. The mutation arm below strips
   the spawn from a copy of the runner and requires the contradiction back.
2. The input declares serv as its reuse; the glue never instantiates it. Yosys's
   ``hierarchy -top`` drops every serv module, so the gate judged zero rows and
   answered PASS. It now FAILs with ``CATALOG_REUSE_DECLARED_NOT_INSTANTIATED``.
   An IP only the pull record names is not held to that (the design never said
   it would use it), which the control below pins.

Real Yosys (host, else the EDA image), as in test_f9. ``tempfile.mkdtemp``
rather than ``tmp_path``: in the EDA image ``tmp_path`` carries a newline.
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
import flow_gate_enforcement_audit as AUDIT  # noqa: E402

GATE = 'catalog_synth_safe_params_check'
CODE = 'CATALOG_REUSE_DECLARED_NOT_INSTANTIATED'
CAL = PROGRAMS / 'calibration'
FLOW = PLUGIN / 'flow/phase1_phase2_phase3.yaml'
REPORT = 'reports/phase2/gates/catalog_synth_safe_params.json'
RUNNER = 'design_one_shot_runner.py'
L2_DECLARES_SERV = {'cpu_isa': 'rv32i', 'cpu_arch': 'bit-serial',
                    'description': 'The design reuses the serv core from the IP catalog.'}
#: The inline spawn this change adds; the mutation arm deletes exactly it.
INLINE_CALL = '        plan.append(step_catalog_synth_safe_params(project))\n'


@pytest.fixture
def root():
    path = Path(tempfile.mkdtemp(prefix='f28_'))
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _put(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(obj if isinstance(obj, str) else json.dumps(obj))


def _design(root, glue, l2=L2_DECLARES_SERV):
    """The calibration IP staged under one of serv's manifest file names."""
    p = root / 'proj'
    rtl = p / 'phase2/stage1/rtl'
    rtl.mkdir(parents=True)
    shutil.copy(CAL / 'cal_catalog_ip.v', rtl / 'serv_top.v')
    shutil.copy(CAL / f'cal_catalog_glue_{glue}.v', rtl / 'glue.v')
    _put(p / 'phase1/generated_docs/L2_FRS.json', l2)
    _put(p / 'phase1/generated_docs/L9_INTEGRATION_SPEC.json', {'top_module': 'top'})
    return p


def _step1():
    flow = yaml.safe_load(FLOW.read_text())
    return next(s for s in flow['steps'] if str(s.get('id')) == '1')


def _step1_verdict(p):
    result = fcc.check_step(p, _step1(), {})
    return result, json.loads((p / REPORT).read_text())


# -- ruling 2: declared but never reached ---------------------------------

def test_a_declared_ip_the_top_never_reaches_fails_step1(root):
    p = _design(root, 'unreached')
    result, report = _step1_verdict(p)
    assert (report['verdict'], result.status) == ('FAIL', 'FAIL'), report
    assert report['failure_codes'] == [CODE]
    assert report['declaration']['declared_catalog_reuse'] == ['serv']
    assert report['reached_ips'] == [] and report['instances'] == []
    assert CODE in report['reason'] and "['serv']" in report['reason']


def test_the_same_declaration_reached_and_pinned_still_passes(root):
    # The control: only the glue differs, and it instantiates the IP.
    p = _design(root, 'pinned')
    result, report = _step1_verdict(p)
    assert (report['verdict'], result.status) == ('PASS', 'PASS'), report
    assert report['reached_ips'] == ['serv']
    assert 'failure_codes' not in report


def test_an_undeclared_pulled_ip_is_not_held_to_reachability(root):
    # The pull record names serv, the docs do not: the design never said it
    # would use it, so an unreached copy is not a spec/RTL inconsistency.
    p = _design(root, 'unreached', {'description': 'a counter'})
    (p / 'phase2/stage1/rtl/serv_top.v').rename(p / 'phase2/stage1/rtl/ip.v')
    _put(p / 'plugin_output/declaration.json', {'ip_catalog_used': [
        {'ip_name': 'serv', 'status': 'PASS',
         'files_copied': [{'dest': str(p / 'phase2/stage1/rtl/ip.v')}]}]})
    result, report = _step1_verdict(p)
    assert report['declaration']['declared_catalog_reuse'] == []
    assert (report['verdict'], result.status) == ('PASS', 'PASS'), report
    assert report['reached_ips'] == []


def test_the_unsafe_path_keeps_its_own_code(root):
    # Reached but unsafe: the new code must not be charged, the old FAIL names
    # its own.
    p = _design(root, 'unsafe')
    _, report = _step1_verdict(p)
    assert report['verdict'] == 'FAIL'
    assert report['failure_codes'] == ['CATALOG_SYNTH_SAFE_VALUE_NOT_PINNED']


# -- ruling 1: the runner blocks on it inline ------------------------------

def test_the_runner_step_fails_on_the_gate_it_spawns(root):
    import design_one_shot_runner as dor
    p = _design(root, 'unreached')
    sr = dor.step_catalog_synth_safe_params(p)
    assert sr.status == 'FAIL', sr
    assert sr.extras['failure_codes'] == [CODE]
    assert sr.output_files == [REPORT]


def test_the_runner_step_passes_and_stands_down_as_the_gate_does(root):
    import design_one_shot_runner as dor
    assert dor.step_catalog_synth_safe_params(_design(root, 'pinned')).status == 'PASS'
    shutil.rmtree(root / 'proj')
    plain = _design(root, 'pinned', {'description': 'a toggle flip-flop'})
    sr = dor.step_catalog_synth_safe_params(plain)
    assert (sr.status, sr.declared_by) == ('NOT_APPLICABLE', 'DESIGN_DECLARED_NA'), sr


def test_the_runner_step_never_credits_an_unread_declaration(root):
    import design_one_shot_runner as dor
    p = _design(root, 'pinned')
    _put(p / 'phase1/generated_docs/L2_FRS.json', '{"cpu_isa": ')
    sr = dor.step_catalog_synth_safe_params(p)
    assert (sr.status, sr.reason_class) == ('NOT_MEASURED', 'input_absent'), sr


def _gate_row(programs):
    rep = AUDIT.audit(FLOW, programs)
    return rep, next(g for g in rep['gates'] if g['gate'] == GATE)


def _programs_copy(tmp_path, runner_text):
    d = tmp_path / 'programs'
    d.mkdir(parents=True)
    (d / RUNNER).write_text(runner_text)
    (d / f'{GATE}.py').write_text((PROGRAMS / f'{GATE}.py').read_text())
    return d


def test_the_audit_reads_the_gate_as_declared_blocking_and_enforced():
    rep, row = _gate_row(PROGRAMS)
    assert (row['declared'], row['enforcement'], row['wiring']) == (
        'blocking', 'ENFORCED', 'INLINE_BLOCKING'), row
    assert GATE not in {c['gate'] for c in rep['contradictions']}
    assert GATE not in {u['gate'] for u in rep['undeclared_audit_only']}
    assert GATE not in {g['gate'] for g in rep['declared_weaker_than_wired']}


def test_mutation_removing_the_inline_spawn_is_a_contradiction(tmp_path):
    text = (PROGRAMS / RUNNER).read_text()
    assert text.count(INLINE_CALL) == 1, 'the inline call moved; update INLINE_CALL'
    live = _gate_row(_programs_copy(tmp_path / 'live', text))[1]
    assert live['wiring'] == 'INLINE_BLOCKING', live
    rep, row = _gate_row(_programs_copy(tmp_path / 'cut', text.replace(INLINE_CALL, '')))
    assert row['wiring'] == 'NOT_INVOKED', row
    assert GATE in {c['gate'] for c in rep['contradictions']}


def test_the_register_is_not_written_for_it():
    # Newly ENFORCED is not debt paid from a register it was never in: neither
    # register names the gate, and the shipped audit passes without a write.
    doc = json.loads((PROGRAMS / 'flow_gate_enforcement_baseline.json').read_text())
    for key in ('known', 'undeclared_known'):
        assert [e for e in doc[key] if e.endswith(f'::{GATE}')] == [], doc[key]
