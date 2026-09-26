"""F17: step 23 gate 1 (`achieved_period_recorded_check`) and its record.

MEASURED on the spm copy of run23 (vibeic-eda 0.3.79), all numbers are SETUP
SLACK at the same 24 ns clock `clk`, on the same routed netlist `spm_pnr.v`,
with the same flat-OCV derate (early 0.95 / late 1.05):

    reports/phase3/sta_spef_based.rpt   FF process, nom SPEF   12.13   <- gate 1 read this
                                        SS process, nom SPEF    0.55
                                        TT process, nom SPEF    8.51
    reports/phase3/sta_mcorner_ocv.rpt  SS process, max SPEF    0.36   <- the record
    STAPostPNR (tool arm)               max_ss                  0.3624

`sta_spef_based.rpt` times three process corners in one file, FF first, and
gate 1 took the first `worst slack max` in it: the FASTEST corner's slack. The
design reaches only what its worst corner allows, so the record (0.36) is
right and the reader was wrong.

The rules this file pins:
  * direct: gate 1 reads the worst `worst slack max` over every corner section
    of every post-route sign-off report, never the first line of one file;
  * direct: a record whose slack is not that worst REFUSES
    (`ACHIEVED_PERIOD_SLACK_DISAGREES`, rc 1) instead of passing on presence;
  * librelane|dual: `achievable_fmax.json` is written from the tool arm's
    worst setup (`librelane_signoff.step23_tool_arm`), and the direct deck
    never stands in when the tool arm cannot be read.
"""
import ast
import importlib
import json
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
APR = importlib.import_module('achieved_period_recorded_check')
F15 = importlib.import_module('test_f15_step23_gates_read_the_tool_arm')

LIB = '/pdk/p/libs.ref/cells/lib/cells__{}.lib'


def _section(head: str, slack: float, hold: float = 0.4) -> str:
    """One corner section as the direct decks write it (banner, basis stamps,
    `worst slack max`), then its hold twin, which must never be read."""
    return (f'=== SETUP corner: {head} ===\nSTA_BASIS: POST_ROUTE_SPEF\n'
            f'STA_TIME_UNIT: ns\nOCV_DERATE_APPLIED early=0.95 late=1.05 flat-OCV\n'
            f'Startpoint: a\nEndpoint: b\n'
            f'{slack:.2f}   slack (MET)\n\nworst slack max {slack:.2f}\ntns max 0.00\n'
            f'=== HOLD corner: {head} ===\nSTA_BASIS: POST_ROUTE_SPEF\n'
            f'worst slack min {hold:.2f}\ntns min 0.00\n')


def spm_like(project: Path, *, record=0.36, order=('FF', 'SS', 'TT')) -> Path:
    """The three direct sign-off reports, with the spm run's numbers."""
    by_process = {'FF': 12.13, 'SS': 0.55, 'TT': 8.51}
    based = project / 'reports/phase3/sta_spef_based.rpt'
    based.parent.mkdir(parents=True, exist_ok=True)
    based.write_text(''.join(_section(f'process={p}', by_process[p]) for p in order))
    (project / 'reports/phase3/sta_spef_multicorner.rpt').write_text(
        '# Multi-corner SPEF STA\n'
        f'=== SETUP (max-RC corner, SPEF=max, liberty={LIB.format("tt")}) ===\n'
        'worst slack max 8.90\n'
        f'=== HOLD (min-RC corner, SPEF=min, liberty={LIB.format("tt")}) ===\n'
        'worst slack min 0.97\n')
    (project / 'reports/phase3/sta_mcorner_ocv.rpt').write_text(
        _section(f'process=SS liberty={LIB.format("ss")}, SPEF=top.max.spef', 0.36, 0.49))
    if record is not None:
        (project / APR.ACHIEVED_REL).write_text(json.dumps({
            'spec_period_ns': 24.0, 'worst_setup_slack_ns': record,
            'achievable_period_ns': round(24.0 - record, 4),
            'relaxation_applied': False}))
    return project


def _gate(project: Path, tmp_path: Path):
    out = tmp_path / 'gate1.json'
    rc = APR.main([str(project), '--json', str(out)])
    return rc, json.loads(out.read_text())


# ------------------------------------------------------ direct: the reader ---

def test_the_direct_reader_takes_the_worst_corner_not_the_first(tmp_path):
    rc, doc = _gate(spm_like(tmp_path), tmp_path)
    assert (rc, doc['verdict'], doc['setup_slack_ns']) == (0, 'PASS', 0.36), doc
    assert doc['slack_source'] == 'reports/phase3/sta_mcorner_ocv.rpt', doc
    # every corner section was read, hold sections never
    assert sorted(c['slack_ns'] for c in doc['slack_candidates']) == \
        [0.36, 0.55, 8.51, 8.9, 12.13], doc


@pytest.mark.parametrize('order', [('FF', 'SS', 'TT'), ('TT', 'FF', 'SS'),
                                   ('SS', 'TT', 'FF')])
def test_the_worst_section_of_one_file_is_found_in_any_order(tmp_path, order):
    """One multi-corner report alone: its worst corner, wherever it sits."""
    project = spm_like(tmp_path, record=None, order=order)
    for rel in ('reports/phase3/sta_spef_multicorner.rpt',
                'reports/phase3/sta_mcorner_ocv.rpt'):
        (project / rel).unlink()
    slack, source = APR.measured_setup_slack(project)
    assert (slack, source) == (0.55, 'reports/phase3/sta_spef_based.rpt')
    section = [c for c in APR.setup_slack_candidates(project) if c[0] == slack][0][2]
    assert 'process=SS' in section


def test_the_pnr_session_report_is_not_a_signoff_when_one_exists(tmp_path):
    """`phase3/reports/sta.rpt` is the PnR session's own STA; a worse number
    in it does not stand for the sign-off's."""
    project = spm_like(tmp_path)
    pnr = project / 'phase3/reports/sta.rpt'
    pnr.parent.mkdir(parents=True, exist_ok=True)
    pnr.write_text('=== SETUP corner: native_path ===\nworst slack max -3.00\n')
    assert APR.measured_setup_slack(project) == (0.36, 'reports/phase3/sta_mcorner_ocv.rpt')


# ------------------------------------------------ direct: the disagreement ---

def test_a_record_of_the_fastest_corner_is_refused(tmp_path):
    """The spm defect in reverse: a record made from the FF corner's 12.13 ns.
    Before F17 this PASSED -- the gate read the same wrong number."""
    rc, doc = _gate(spm_like(tmp_path, record=12.13), tmp_path)
    assert (rc, doc['verdict'], doc['refusal']) == \
        (1, 'REFUSED', 'ACHIEVED_PERIOD_SLACK_DISAGREES'), doc
    assert (doc['setup_slack_ns'], doc['recorded_setup_slack_ns']) == (0.36, 12.13)


def test_any_disagreement_beyond_the_printed_precision_is_refused(tmp_path):
    rc, doc = _gate(spm_like(tmp_path, record=0.55), tmp_path)
    assert (rc, doc['verdict']) == (1, 'REFUSED'), doc
    # ...while the record's own rounding is not a disagreement
    rc, doc = _gate(spm_like(tmp_path / 'r', record=0.3624), tmp_path)
    assert (rc, doc['verdict']) == (0, 'PASS'), doc


def test_no_record_is_still_the_old_finding_not_a_refusal(tmp_path):
    rc, doc = _gate(spm_like(tmp_path, record=None), tmp_path)
    assert (rc, doc['verdict']) == (1, 'FAIL'), doc
    assert [f['rule'] for f in doc['findings']] == ['ACHIEVED_PERIOD_NOT_RECORDED']
    assert doc['setup_slack_ns'] == 0.36


# ------------------------------------------- tool arm: the record's producer ---

def _runner():
    return importlib.import_module('phase3_one_shot_runner')


def _tool(tmp_path: Path, **kw) -> Path:
    project = F15.tool_project(tmp_path, **kw)
    (project / 'config.json').write_text(json.dumps({'CLOCK_PERIOD': 24.0}))
    # the direct OCV deck's stance, as the runner wrote it before STAPostPNR
    stance = project / 'reports/phase3/mcorner_ocv_stance.json'
    stance.parent.mkdir(parents=True, exist_ok=True)
    stance.write_text(json.dumps({'setup_worst_slack_ns': 0.36}))
    return project


@pytest.mark.parametrize('mode', ['librelane', 'dual'])
def test_the_tool_arm_writes_the_reached_period(tmp_path, mode):
    project = _tool(tmp_path, mode=mode, setup={'max_ss_125C_4v50': 0.3624,
                                                 'nom_tt_025C_5v00': 8.5})
    notes, written = [], []
    _runner()._achievable_fmax_from_tool_arm(project, 'top', notes, written)
    doc = json.loads((project / APR.ACHIEVED_REL).read_text())
    assert doc['worst_setup_slack_ns'] == 0.3624, (doc, notes)
    assert doc['spec_period_ns'] == 24.0 and doc['achievable_period_ns'] == 23.6376
    assert doc['slack_source'].startswith('max_ss_125C_4v50/')
    assert doc['direct_ocv_setup_slack_ns'] == 0.36
    assert doc['basis']['producer'] == 'librelane:OpenROAD.STAPostPNR'
    assert doc['relaxation_applied'] is False
    assert written == [str(project / APR.ACHIEVED_REL)]
    # ...and gate 1 credits exactly that record
    rc, gate = _gate(project, tmp_path)
    assert (rc, gate['verdict'], gate['setup_slack_ns']) == (0, 'PASS', 0.3624), gate


def test_the_tool_arm_record_moves_with_the_tool_not_the_deck(tmp_path):
    """Reverse: the direct deck's stance says 0.36; the tool's worst corner is
    another one. The record follows the tool."""
    project = _tool(tmp_path, setup={'min_ff_n40C_5v50': -0.25})
    _runner()._achievable_fmax_from_tool_arm(project, 'top', [], [])
    doc = json.loads((project / APR.ACHIEVED_REL).read_text())
    assert (doc['worst_setup_slack_ns'], doc['spec_met']) == (-0.25, False)
    assert doc['slack_source'].startswith('min_ff_n40C_5v50/')


def test_an_unreadable_tool_arm_leaves_no_direct_record(tmp_path):
    project = _tool(tmp_path)
    (project / APR.ACHIEVED_REL).write_text(json.dumps({
        'spec_period_ns': 24.0, 'worst_setup_slack_ns': 0.36,
        'achievable_period_ns': 23.64, 'relaxation_applied': False}))
    (project / F15.STA / 'max_ss_125C_4v50' / 'vibeic_signoff.rpt').unlink()
    notes = []
    _runner()._achievable_fmax_from_tool_arm(project, 'top', notes, [])
    assert not (project / APR.ACHIEVED_REL).exists(), notes
    assert 'LL_STA_CORNER_ARTEFACT_MISSING' in notes[-1]
    rc, gate = _gate(project, tmp_path)
    assert (rc, gate['verdict']) == (1, 'REFUSED'), gate


# ------------------------------------------------------- the runner wiring ---

def _step23_source() -> ast.Module:
    return ast.parse((PROGRAMS / 'phase3_one_shot_runner.py').read_text())


def test_the_direct_deck_writes_the_record_only_when_step_23_is_direct():
    """The direct write of `achievable_fmax.json` sits under
    `_ll_m23 == "direct"`, and the tool-arm producer is called when it is not."""
    tree = _step23_source()
    guarded_write = called = False
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = ast.unparse(node.test)
            body = ast.unparse(ast.Module(body=node.body, type_ignores=[]))
            if test == "_ll_m23 == 'direct'" and 'achievable_fmax.json' in body:
                guarded_write = True
            if "_ll_m23 != 'direct'" in test and '_achievable_fmax_from_tool_arm(' in body:
                called = True
    assert guarded_write, 'the direct deck writes achievable_fmax.json in every mode'
    assert called, 'no call records the reached period from the tool arm'
