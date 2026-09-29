#!/usr/bin/env python3
"""Step 15.5ic books every exit code its programs document, and never raises.

Lane llf (W7a) found that `step_pad_ring_gen` computed a NOT_MEASURED reason
class for rc 2 and other codes and then built the StepResult WITHOUT it:
`StepResult` refuses a NOT_MEASURED with no reason, so the step RAISED
ValueError instead of reporting. Each program's own contract:

  pad_assignment_gen            1 REFUSE (answers owed)   2 NOT_ASKED
  pad_ring_gen                  1 refusal                 2 SKIP (inputs absent /
                                                            rotation it cannot honour)
  pad_ring_check                1 wrong or silent report  2 disclosed absence
  pad_bterm_coincidence_check   1 a net could not be decided
                                2 nothing to decide (no pad terminal)

A finding stays FAIL; a documented could-not-measure is NOT_MEASURED with the
reason that says why; an exit no program documents is an execution error.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import _plugin_tree  # noqa: F401 -- puts programs/ on sys.path
import phase3_one_shot_runner as R
import verdict as _V
import test_librelane_state_bridge as SB
import librelane_contract as LC

NM = _V.Verdict.NOT_MEASURED.value
FAIL = _V.Verdict.FAIL.value
RC = _V.ReasonClass

PROGRAMS = ["pad_assignment_gen.py", "pad_ring_gen.py", "pad_ring_check.py",
            "pad_bterm_coincidence_check.py"]


def _pdk(tmp_path: Path):
    return SimpleNamespace(name="tpdk", tech_lef=str(tmp_path / "t.tlef"))


def _drive(tmp_path, monkeypatch, failing: str, rc: int):
    """Run the step with every program exiting 0 except `failing` -> `rc`."""
    monkeypatch.setattr(R, "_padring_pdk_root_and_tree",
                        lambda pdk, container: (str(tmp_path), "tpdk"))
    ran = []

    def run(argv, **_k):
        name = Path(argv[1]).name
        ran.append(name)
        code = rc if name == failing else 0
        return SimpleNamespace(returncode=code,
                               stdout=f"{name} said rc {code}", stderr="")
    monkeypatch.setattr(R._pr, "run", run)
    result = R.step_pad_ring_gen(tmp_path, None, _pdk(tmp_path))
    return result, ran


CASES = [
    ("pad_assignment_gen.py", 1, FAIL, ""),
    ("pad_assignment_gen.py", 2, NM, RC.NOT_EXECUTED.value),
    ("pad_ring_gen.py", 1, FAIL, ""),
    ("pad_ring_gen.py", 2, NM, RC.INPUT_ABSENT.value),
    ("pad_ring_check.py", 1, FAIL, ""),
    ("pad_ring_check.py", 2, NM, RC.INPUT_ABSENT.value),
    ("pad_bterm_coincidence_check.py", 1, NM, RC.INCONCLUSIVE.value),
    ("pad_bterm_coincidence_check.py", 2, NM, RC.NO_POPULATION.value),
]


@pytest.mark.parametrize("program,rc,status,reason", CASES)
def test_each_documented_rc_is_booked_with_its_reason(
        tmp_path, monkeypatch, program, rc, status, reason):
    result, ran = _drive(tmp_path, monkeypatch, program, rc)
    assert (result.status, result.reason_class) == (status, reason)
    # the producer's own words and its rc are the detail, and nothing after
    # the refusing program ran
    assert f"{program}: rc={rc}" in result.detail
    assert f"{program} said rc {rc}" in result.detail
    assert ran == PROGRAMS[:PROGRAMS.index(program) + 1]


@pytest.mark.parametrize("program", PROGRAMS)
@pytest.mark.parametrize("rc", [3, 137, -9])
def test_an_undocumented_exit_is_an_execution_error_not_an_exception(
        tmp_path, monkeypatch, program, rc):
    result, _ = _drive(tmp_path, monkeypatch, program, rc)
    assert (result.status, result.reason_class) == (
        NM, RC.EXECUTION_ERROR.value)
    assert f"{program}: rc={rc}" in result.detail


def test_a_missing_program_is_tool_absent(tmp_path, monkeypatch):
    real = R.PROGRAMS_DIR
    fake = tmp_path / "programs"
    fake.mkdir()
    for name in PROGRAMS[:2]:
        (fake / name).write_text("")
    monkeypatch.setattr(R, "PROGRAMS_DIR", fake)
    result, ran = _drive(tmp_path, monkeypatch, "", 0)
    assert (result.status, result.reason_class) == (NM, RC.TOOL_ABSENT.value)
    assert "pad_ring_check.py: program absent" in result.detail
    assert ran == PROGRAMS[:2]
    assert real.is_dir()


def test_all_zero_without_the_declared_outputs_is_fail(tmp_path, monkeypatch):
    """Control: the existing rule stands -- rc 0 everywhere with the step's
    declared outputs absent is FAIL, and a FAIL carries no reason class."""
    result, ran = _drive(tmp_path, monkeypatch, "", 0)
    assert ran == PROGRAMS
    assert (result.status, result.reason_class) == (FAIL, "")
    assert "required output(s) are absent" in result.detail


@pytest.mark.parametrize('rc,reason', [
    (R._RC_STALLED, RC.STALLED.value),
    (124, RC.BUDGET_EXHAUSTED.value),
])
def test_container_watchdog_stop_keeps_its_reason(tmp_path, monkeypatch, rc, reason):
    monkeypatch.setattr(R, '_padring_pdk_root_and_tree',
                        lambda *a: (str(tmp_path), 'tpdk'))
    monkeypatch.setattr(R, '_to_container_path', lambda path, container: path)
    monkeypatch.setattr(R, '_docker_exec',
                        lambda *a, **k: (rc, '', 'watchdog stopped pad_ring_check'))
    result = R.step_pad_ring_gen(tmp_path, 'test-container', _pdk(tmp_path))
    assert (result.status, result.reason_class) == (NM, reason), result.detail
    assert 'pad_assignment_gen.py' in result.detail and f'rc={rc}' in result.detail


@pytest.mark.parametrize('program,rc,reason', [
    ('pad_assignment_gen.py', 2, RC.NOT_EXECUTED.value),
    ('pad_ring_check.py', 2, RC.INPUT_ABSENT.value),
    ('pad_ring_gen.py', 2, RC.INPUT_ABSENT.value),
    ('pad_bterm_coincidence_check.py', 1, RC.INCONCLUSIVE.value),
    ('pad_bterm_coincidence_check.py', R._RC_STALLED, RC.STALLED.value),
])
def test_default_librelane_pad_path_uses_producers_documented_rc(
        tmp_path, monkeypatch, program, rc, reason):
    project = tmp_path / 'project'
    out_dir = project / 'phase3/stage3/pnr'
    wrapper = SB.write(out_dir / 'chip_top_io.v',
                       'module chip_top(a);\n  input a;\n  core u_core (.a(a));\nendmodule\n')
    netlist = SB.write(project / 'phase3/stage2/core.v',
                       'module core(a);\n  input a;\nendmodule\n')
    SB.put(project / 'phase3/librelane_switch.json',
           {'steps': {'15': 'librelane', '15.5ic': 'librelane'},
            'pdk_root_host': str(tmp_path / 'pdkroot')})
    monkeypatch.setattr(LC, 'resolve_image', lambda p: SB.stated_image())
    # CR4 validates resolved exclusion policy separately; this test drives
    # the documented pad-ring producer exit after that preflight.
    monkeypatch.setattr(R, '_resolved_cell_policy',
                        lambda configs, *_a, **_k: (configs, set()))
    monkeypatch.setattr(R, '_padring_chip_top_record', lambda p: {
        'core_module': 'core', 'chip_top_module': 'chip_top',
        'chip_top_verilog': str(wrapper.relative_to(project))})
    monkeypatch.setattr(R, 'pnr_input_netlist', lambda p, core: (netlist, 'n', False))
    def docker(_container, _cmd, **kw):
        name = Path(kw.get('marker') or '').name
        return ((rc, '', 'watchdog stopped') if name == program else (0, 'ok', ''))
    monkeypatch.setattr(R, '_docker_exec', docker)
    steps = ['OpenROAD.Floorplan', 'OpenROAD.PadRing', 'OpenROAD.CutRows',
             'OpenROAD.GeneratePDN', 'Odb.RemovePDNObstructions']
    monkeypatch.setattr(LC, 'flow_segment', lambda image, first, last, **k:
                        steps if last == 'Odb.RemovePDNObstructions' else steps[:2])
    monkeypatch.setattr(LC, 'resolve_step_configs',
                        lambda project, image, pdk, ids, **kw:
                        {s: SB._declared(tmp_path, s) for s in ids})
    monkeypatch.setattr(LC, 'emit_pdn_cfg',
                        lambda image, pdk, out, **kw: SB.write(out, 'pdn\n'))
    def chain(project, image, triples, **kw):
        folders = []
        for step, _cfg, _state in triples:
            folder = project / 'phase3/librelane/15-floorplan' / step
            views = {'def': str(SB.write(folder / 'chip_top.def', SB.DEF_TEXT)),
                     'odb': str(SB.write(folder / 'chip_top.odb', step))}
            SB.put(folder / 'state_out.json', views)
            folders.append(folder)
        return folders
    monkeypatch.setattr(LC, 'run_chain', chain)
    def host_run(argv, **kw):
        name = Path(argv[1]).name
        return SimpleNamespace(returncode=rc if name == program else 0,
                               stdout=f'{name} rc={rc}', stderr='')
    monkeypatch.setattr(R._pr, 'run', host_run)
    pdk = SB._pdk(tmp_path)
    pdk.macro_lefs, pdk.macro_gds = [], []
    result, consumer = R._prepare_librelane_floorplan_for_route(
        project, pdk, 'c', out_dir, SB._deck(),
        {'15': 'librelane', '15.5ic': 'librelane'},
        io_view_discover=lambda *a: (['/pdk/io.lef'], ['/pdk/io.gds']))
    assert consumer is None
    assert (result.status, result.reason_class) == (NM, reason), result.detail
    assert result.extras['finding'].startswith(('LL_PAD_ASSIGNMENT', 'PADRING_'))
