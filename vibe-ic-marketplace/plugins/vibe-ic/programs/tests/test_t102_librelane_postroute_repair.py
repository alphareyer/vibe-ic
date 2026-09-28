"""T102: step 32 (post-route repair) as a LibreLane custom step under the PPA closure.

The closure, the registry, the measurement and actuator CLIs, the supply
ownership gate and the runner's deck builder run for real. Only an EDA tool's
file writes are substituted: `librelane_contract.run_chain` (the `docker run`
of a LibreLane step) is replaced by a writer of the step folders a real run
leaves (`state_out.json` with the tool's metric names, the repair step's DEF).
"""
import importlib
from types import SimpleNamespace
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
PLUGIN = PROGRAMS.parent
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(PROGRAMS / 'tests'))
prr = importlib.import_module('librelane_postroute_repair')
closure = importlib.import_module('_ppa.closure')
contract = importlib.import_module('librelane_contract')

from _stated_eda_image import state_the_image  # noqa: E402

STEP_DIR = PROGRAMS / 'librelane_plugins' / 'librelane_plugin_vibeic'


@pytest.fixture(autouse=True)
def _stated_image(monkeypatch):
    # the identity is stated, never asked of this host (no docker in the image)
    state_the_image(monkeypatch)
    for name in ('VIBEIC_LIBRELANE_IMAGE', 'VIBEIC_LIBRELANE_PDK_ROOT'):
        monkeypatch.delenv(name, raising=False)
REGISTRY = PLUGIN / 'config' / 'ppa_actuator_registry.yaml'
CORNERS = ['nom_tt_025C_5v00', 'nom_ss_125C_4v50', 'nom_ff_n40C_5v50']


def put(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))
    return path


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# ------------------------------------------------------- the fork capability ---

#: Recorded on vibeic-eda 0.3.79 (sha256:93d88e9e…), 2026-09-27, by
#: `fork_capability`: the fork accepts `-detailed_routing` (it gets as far as
#: "no network"), and rejects the bogus control flag at the parser.
FORK_TRANSCRIPT = (
    "[INFO] Executing command: 'bash -c openroad -no_init -no_splash -exit /dev/stdin 2>&1'\n"
    "VIBEIC_PRR_PROBE_REAL: Error: no network has been linked.\n"
    "[ERROR STA-0562] estimate_parasitics -vibeic_probe_unknown_control_flag is not "
    "a known keyword or flag.\n"
    "VIBEIC_PRR_PROBE_CTRL: STA-0562\n")
#: An OpenROAD without the flag rejects it exactly as it rejects the control
#: (the same parser, the same answer); both answer forms the parser gives.
STOCK_TRANSCRIPTS = (
    "VIBEIC_PRR_PROBE_REAL: STA-0562\nVIBEIC_PRR_PROBE_CTRL: STA-0562\n",
    "VIBEIC_PRR_PROBE_REAL: estimate_parasitics -detailed_routing is not a known "
    "keyword or flag.\nVIBEIC_PRR_PROBE_CTRL: estimate_parasitics "
    "-vibeic_probe_unknown_control_flag is not a known keyword or flag.\n")


def test_the_fork_flag_is_told_from_a_bogus_flag_not_from_help_text():
    assert prr.flag_accepted_vs_control(FORK_TRANSCRIPT) is True
    for stock in STOCK_TRANSCRIPTS:
        assert prr.flag_accepted_vs_control(stock) is False
    # An answer that is absent is unmeasured, never a capability.
    assert prr.flag_accepted_vs_control("VIBEIC_PRR_PROBE_REAL: x\n") is None
    assert prr.flag_accepted_vs_control("") is None


def test_fork_capability_probe_uses_shared_supervisor_without_raw_stdin(
        monkeypatch):
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(
            cmd, 0, stdout=FORK_TRANSCRIPT, stderr="")

    monkeypatch.setattr(contract, "run_container", run)
    result = prr.fork_capability("img")
    assert result["capable"] is True
    assert calls[0][1] == {"probe_deadline_s": contract.PROBE_DEADLINE_S}
    assert "-i" not in calls[0][0]
    assert prr._PROBE_REAL in calls[0][0][-1]


def test_fork_capability_deadline_is_unmeasured(monkeypatch):
    def deadline(*_args, **_kwargs):
        raise contract.Refusal("LL_TOOL_DEADLINE", "probe deadline elapsed")

    monkeypatch.setattr(contract, "run_container", deadline)
    result = prr.fork_capability("img")
    assert result["capable"] is None
    assert result["code"] == "LL_TOOL_DEADLINE"
    assert "LL_TOOL_DEADLINE" in result["reason"]


def test_probe_deadline_is_recorded_as_unmeasured_not_incapable(tmp_path,
                                                                monkeypatch):
    monkeypatch.setattr(prr, "fork_capability", lambda *_a, **_k: {
        "capable": None, "image": "img", "code": "LL_TOOL_DEADLINE",
        "reason": "LL_TOOL_DEADLINE: named probe container was reaped"})
    monkeypatch.setattr(contract, "resolve_step_configs",
                        lambda *_a, **_k: pytest.fail("no config may be resolved"))
    report = prr.run(tmp_path, image="img", pdk="processA", pdk_root=tmp_path,
                     views={}, sdc=tmp_path / "x.sdc", derate=(0.95, 1.05))
    assert report["verdict"] == "NOT_MEASURED"
    assert report["code"] == "LL_TOOL_DEADLINE"
    assert report["reason_class"] == "execution_error"
    assert "named probe container" in report["reason"]
    assert json.loads((tmp_path / prr.REPORT_REL).read_text()) == report


def test_an_incapable_image_refuses_before_any_state_is_built(tmp_path, monkeypatch):
    monkeypatch.setattr(prr, 'fork_capability',
                        lambda image, docker='docker': {'capable': False, 'image': image})
    monkeypatch.setattr(contract, 'resolve_step_configs',
                        lambda *a, **k: pytest.fail('no config may be resolved'))
    report = prr.run(tmp_path, image='img', pdk='pdk', pdk_root=tmp_path,
                     views={}, sdc=tmp_path / 'x.sdc', derate=(0.95, 1.05))
    assert report['verdict'] == 'NOT_MEASURED'
    assert report['code'] == 'LL_PRR_TOOL_INCAPABLE'
    assert json.loads((tmp_path / prr.REPORT_REL).read_text())['code'] == 'LL_PRR_TOOL_INCAPABLE'


# ------------------------------------------------------------- measurement ---

def _sta_metrics(setup, hold, drv=(0, 0, 0), corners=CORNERS):
    metrics = {}
    for c in corners:
        metrics[f'timing__setup__ws__corner:{c}'] = setup.get(c, 5.0) if isinstance(setup, dict) else setup + (0 if 'ss' in c else 4)
        metrics[f'timing__hold__ws__corner:{c}'] = hold.get(c, 0.5) if isinstance(hold, dict) else hold + (0 if 'ss' in c else 0.3)
        for kind, n in zip(('slew', 'cap', 'fanout'), drv):
            metrics[f'design__max_{kind}_violation__count__corner:{c}'] = n if 'ss' in c else 0
    return metrics


def test_the_summary_is_the_worst_of_every_declared_corner():
    s = prr.summarize(_sta_metrics(0.7, 0.1, (2, 1, 0)), CORNERS)
    assert s['setup_ws_min'] == 0.7 and s['hold_ws_min'] == 0.1
    assert s['drv_count'] == 3 and s['unmeasured_corners'] == []


def test_a_corner_the_tool_did_not_report_leaves_the_summary_unmeasured():
    m = _sta_metrics(0.7, 0.1)
    del m['timing__hold__ws__corner:nom_ff_n40C_5v50']
    s = prr.summarize(m, CORNERS)
    assert s['setup_ws_min'] is None and s['hold_ws_min'] is None and s['drv_count'] is None
    assert s['unmeasured_corners'] == ['nom_ff_n40C_5v50']
    assert prr.summarize(_sta_metrics(0.7, 0.1), [])['setup_ws_min'] is None


def _impl_with(tmp_path, measurement, antenna=0):
    impl = tmp_path / 'impl'
    sta = put(tmp_path / 'sta/state_out.json', {'metrics': {}})
    put(impl / prr.CURRENT, {'candidate': None, 'measurement': dict(
        measurement, antenna_nets=antenna, sta_state=str(sta),
        sta_state_sha256=contract.digest(sta))})
    return impl, sta


def test_measure_reads_the_adopted_candidate_and_refuses_what_it_cannot_answer(tmp_path):
    impl, sta = _impl_with(tmp_path, {'setup_ws_min': -0.2, 'hold_ws_min': 0.1, 'drv_count': 3})
    out = tmp_path / 'm.json'
    assert prr.measure(impl, 'setup', out) == 0
    assert json.loads(out.read_text())['value'] == -0.2
    assert prr.measure(impl, 'antenna', out) == 0 and json.loads(out.read_text())['value'] == 0
    impl2, _ = _impl_with(tmp_path / 'b', {'setup_ws_min': None})
    assert prr.measure(impl2, 'setup', out) == prr.RC_UNDETERMINED
    sta.write_text('{"metrics": {"changed": 1}}')          # not the adopted state
    assert prr.measure(impl, 'setup', out) == prr.RC_UNDETERMINED
    assert prr.measure(tmp_path / 'nothing', 'hold', out) == prr.RC_UNDETERMINED


# ------------------------------------------------------ the registry binding ---

def test_the_repair_actuator_and_its_domains_are_executable_and_in_the_tree():
    reg = closure.load_registry(REGISTRY)
    assert reg.verify_bindings() == []
    act = reg.actuators['timing.repair_setup']
    assert act.binding is closure.Binding.EXECUTABLE
    assert act.program_path() == PROGRAMS / 'librelane_postroute_repair.py'
    for name in ('timing.setup', 'timing.hold', 'timing.drv', 'antenna.violations'):
        assert name in act.remeasure_domains
        dom = reg.domains[name]
        assert dom.binding is closure.Binding.EXECUTABLE, name
        assert dom.program_path() == PROGRAMS / 'librelane_postroute_repair.py'
    for cid in prr.CONTROLLERS:
        assert reg.controllers[cid].actuator_id == 'timing.repair_setup'
    # Every actuator parameter reaches a variable the plugin step declares.
    assert set(act.parameters) == set(prr.PARAM_VARS)


def test_the_hold_buffer_budget_above_the_guardrail_is_refused_not_clamped():
    act = closure.load_registry(REGISTRY).actuators['timing.repair_setup']
    with pytest.raises(closure.ParameterError):
        act.bind_params({'hold_max_buffer_pct': 6})
    with pytest.raises(closure.ParameterError):
        act.bind_params({'max_utilization_pct': 50})


# ------------------------------------------------ the closure, end to end ---

LEF = textwrap.dedent('''\
    MACRO buf
      CLASS CORE ;
      PIN A
        DIRECTION INPUT ;
        USE SIGNAL ;
      END A
      PIN VDD
        DIRECTION INOUT ;
        USE POWER ;
      END VDD
      PIN VSS
        DIRECTION INOUT ;
        USE GROUND ;
      END VSS
    END buf
    ''')


def _def(extra_owned=True):
    specials = ('- VDD ( * VDD ) + USE POWER ;\n- VSS ( * VSS ) + USE GROUND ;\n'
                if extra_owned else
                '- VDD ( u1 VDD ) + USE POWER ;\n- VSS ( u1 VSS ) + USE GROUND ;\n')
    return ('VERSION 5.8 ;\nDESIGN top ;\nUNITS DISTANCE MICRONS 1000 ;\n'
            'COMPONENTS 2 ;\n- u1 buf + PLACED ( 0 0 ) N ;\n- hold1 buf + PLACED ( 10 0 ) N ;\n'
            'END COMPONENTS\nSPECIALNETS 2 ;\n' + specials + 'END SPECIALNETS\nEND DESIGN\n')


SHIM = textwrap.dedent('''\
    """Test shim: the real program with only `run_chain` (the docker run of a
    LibreLane step) replaced by a writer of the folders a real run leaves."""
    import json, os, sys
    from pathlib import Path
    sys.path.insert(0, os.environ["PRR_REAL_PROGRAMS"])
    import librelane_contract as ll
    import librelane_postroute_repair as prr
    SCENARIO = json.loads(Path(os.environ["PRR_SCENARIO"]).read_text())

    def run_chain(project, image, steps, *, mounts=None, lane=None, **kw):
        base = Path(project) / "phase3/librelane" / lane
        spec = SCENARIO[lane]
        folders = []
        for i, (step, cfg, state) in enumerate(steps, 1):
            folder = base / f"{i:02d}-{step.lower().replace('.', '-')}"
            folder.mkdir(parents=True, exist_ok=True)
            if step == prr.REPAIR_STEP:
                d = folder / "top.def"
                d.write_text(spec["def"])
                metrics = spec["repair_metrics"]
                doc = {"def": str(d), "metrics": metrics}
            elif step.endswith("CheckAntennas"):
                doc = {"metrics": spec.get("antenna_metrics", {})}
            else:
                doc = {"metrics": spec["sta_metrics"] if step.endswith("STAPostPNR") else {}}
            (folder / "state_out.json").write_text(json.dumps(doc))
            folders.append(folder)
        return folders

    ll.run_chain = run_chain
    sys.exit(prr.main())
    ''')


def _scenario_impl(tmp_path, baseline, candidates):
    """An implementation root exactly as `run` leaves it after its baseline,
    and a shim program directory whose `run_chain` plays `candidates`."""
    project = tmp_path / 'proj'
    lef = write(tmp_path / 'cells.lef', LEF)
    cfg = put(project / 'phase3/librelane/32-config/Vibeic.PostRouteRepair.json',
              {'meta': {'step': prr.REPAIR_STEP}, 'CELL_LEFS': [str(lef)]})
    ctx = {'project': str(project), 'image': 'img', 'pdk': 'pdk', 'mounts': [],
           'configs': {step: str(cfg) for step in (prr.REPAIR_STEP, *prr.MEASURE_STEPS)},
           'corners': CORNERS}
    arm = project / prr.ARM_REL
    impl = arm / prr.IMPL_DIR
    put(impl / prr.CONTEXT, ctx)
    sta = put(project / 'phase3/librelane/32-base/03-sta/state_out.json',
              {'metrics': _sta_metrics(*baseline)})
    input_state = put(project / 'phase3/librelane/32-config/bridge/state_in.json', {'def': 'x'})
    put(impl / prr.CURRENT, {'candidate': None, 'repair_input': str(input_state),
                            'repair_state': str(input_state),
                            'measurement': dict(prr.summarize(_sta_metrics(*baseline), CORNERS),
                                                antenna_nets=0, sta_state=str(sta),
                                                sta_state_sha256=contract.digest(sta))})
    scenario = {f'32-cand{i:02d}': c for i, c in enumerate(candidates, 1)}
    put(tmp_path / 'scenario.json', scenario)
    shim = tmp_path / 'shim'
    write(shim / 'librelane_postroute_repair.py', SHIM)
    return project, arm, impl, shim


def _controller(impl, arm, shim, monkeypatch, tmp_path):
    monkeypatch.setenv('PRR_REAL_PROGRAMS', str(PROGRAMS))
    monkeypatch.setenv('PRR_SCENARIO', str(tmp_path / 'scenario.json'))
    reg = closure.load_registry(REGISTRY, programs_dir=shim)
    return closure.ClosureController(reg, impl, arm / 'closure')


def _ant(nets):
    return {} if nets is None else {'antenna__violating__nets': nets,
                                    'antenna__violating__pins': nets}


def _cand(setup, hold, drv=(0, 0, 0), *, changed=1, owned=True, antenna=0):
    return {'def': _def(owned), 'sta_metrics': _sta_metrics(setup, hold, drv),
            'antenna_metrics': _ant(antenna),
            'repair_metrics': {'vibeic__prr__changed': changed}}


def _with_floors(impl, floors):
    ctx = json.loads((impl / prr.CONTEXT).read_text())
    ctx['floors'] = floors
    (impl / prr.CONTEXT).write_text(json.dumps(ctx))


def test_the_ll21_max_fanout_repair_is_adopted_with_setup_still_met(tmp_path, monkeypatch):
    """OWNER RULING (T102 r3), on the case measured on the spm LL15..21 chain:
    3 max_fanout violations per corner (27), the only fix costs setup
    +5.259 -> +3.086. A HARD-violation repair may spend MET setup slack down
    to the declared floor (spm declares none: WNS >= 0)."""
    project, arm, impl, shim = _scenario_impl(
        tmp_path, baseline=(5.259, 0.391, (0, 0, 3)),
        candidates=[_cand(3.086, 0.391, (0, 0, 0))])
    ctl = _controller(impl, arm, shim, monkeypatch, tmp_path)
    run = ctl.run_controller('postroute.repair_drv')
    assert [it.decision for it in run.iterations] == ['PROMOTED'], run.to_record()
    assert run.outcome is closure.Outcome.CONVERGED
    assert 'hard-violation repair spent: timing.setup.wns_ns: 5.259 -> 3.086' \
        in run.iterations[0].decision_reason
    assert json.loads((impl / prr.CURRENT).read_text())['candidate'] == '32-cand01'


@pytest.mark.parametrize('changed', [0, 1])
def test_residual_fanout_refuses_even_a_noop_candidate(tmp_path, monkeypatch, changed):
    project, arm, impl, shim = _scenario_impl(
        tmp_path, baseline=(5.0, 0.2, (0, 0, 3)),
        candidates=[_cand(4.0, 0.2, (0, 0, 3), changed=changed)])
    _controller(impl, arm, shim, monkeypatch, tmp_path).run_controller(
        'postroute.repair_drv')
    rows = json.loads((arm / prr.LEDGER).read_text())['candidates']
    assert rows[0]['decision'] == 'REFUSED'
    assert rows[0]['fanout']['verdict'] == 'FAIL'
    assert rows[0]['fanout']['violations'] == 3
    assert json.loads((impl / prr.CURRENT).read_text())['candidate'] is None


def test_routed_fanout_failure_blocks_the_phase3_step(tmp_path):
    runner = importlib.import_module('phase3_one_shot_runner')
    result = runner._postroute_repair_librelane_result(
        tmp_path, tmp_path / 'pnr',
        {'verdict': 'FAIL', 'code': 'LL_PRR_FANOUT_VIOLATION',
         'reason': 'declared postroute max fanout has 3 residual violations'},
        0.0, handed=False)
    assert result.status == 'FAIL'
    assert 'LL_PRR_FANOUT_VIOLATION' in result.detail
    assert result.reason_class == ''


def test_a_hard_repair_that_makes_setup_negative_is_rejected(tmp_path, monkeypatch):
    project, arm, impl, shim = _scenario_impl(
        tmp_path, baseline=(0.8, 0.391, (0, 0, 3)),
        candidates=[_cand(-0.05, 0.391, (0, 0, 0))])
    ctl = _controller(impl, arm, shim, monkeypatch, tmp_path)
    run = ctl.run_controller('postroute.repair_drv')
    assert run.iterations[0].decision == 'ROLLED_BACK'
    assert 'below its floor 0.0' in run.iterations[0].decision_reason
    assert json.loads((impl / prr.CURRENT).read_text())['candidate'] is None


def test_a_hard_repair_stops_at_the_declared_floor(tmp_path, monkeypatch):
    """T98's case (+3.33 -> +0.14) against a design that DECLARES 0.5 ns of
    setup margin: the floor is the declaration's, and 0.14 is below it."""
    project, arm, impl, shim = _scenario_impl(
        tmp_path, baseline=(3.33, 0.2, (4, 0, 0)),
        candidates=[_cand(0.14, 0.2, (0, 0, 0))])
    _with_floors(impl, {'setup': [0.5, 'L8_TIMING_WAVEFORM.json setup_margin_ns']})
    ctl = _controller(impl, arm, shim, monkeypatch, tmp_path)
    run = ctl.run_controller('postroute.repair_drv')
    assert run.iterations[0].decision == 'ROLLED_BACK'
    assert 'below its floor 0.5' in run.iterations[0].decision_reason
    rec = run.to_record()['iterations'][0]['measurements']['timing.setup']
    assert rec['floor'] == {'value': 0.5, 'source': 'L8_TIMING_WAVEFORM.json setup_margin_ns'}


def test_a_soft_only_candidate_still_follows_the_one_ps_rule(tmp_path, monkeypatch):
    """No hard violation is being fixed (a hold repair): setup may not move
    more than the declared 1 ps, however much slack stays."""
    project, arm, impl, shim = _scenario_impl(
        tmp_path, baseline=(5.259, -0.1),
        candidates=[_cand(5.0, 0.2)])
    ctl = _controller(impl, arm, shim, monkeypatch, tmp_path)
    run = ctl.run_controller('postroute.repair_hold')
    assert run.iterations[0].decision == 'ROLLED_BACK'
    assert 'timing.setup.wns_ns: 5.259 -> 5.0 (maximize)' in run.iterations[0].decision_reason


def test_the_floor_comes_from_declared_inputs_only(tmp_path):
    project = tmp_path / 'p'
    sdc = write(project / 'c.sdc', 'create_clock -name clk -period 10 [get_ports clk]\n')
    f = prr.declared_timing_floor(project, sdc)
    assert f['setup'][0] == 0.0 and 'WNS >= 0' in f['setup'][1]
    write(sdc, 'set_clock_uncertainty -setup 0.25 [get_clocks clk]\n'
               'set_clock_uncertainty 0.1 [get_clocks clk] # both checks\n')
    f = prr.declared_timing_floor(project, sdc)
    assert f['setup'][0] == 0.25 and 'set_clock_uncertainty' in f['setup'][1]
    assert f['hold'][0] == 0.1
    put(project / 'phase1/generated_docs/L8_TIMING_WAVEFORM.json',
        {'timing': {'setup_margin_ns': 0.4}})
    f = prr.declared_timing_floor(project, sdc)
    assert f['setup'] == (0.4, 'L8_TIMING_WAVEFORM.json setup_margin_ns')
    assert f['hold'][0] == 0.1


def test_hardness_and_floor_are_declared_in_the_registry(tmp_path):
    import yaml
    reg = closure.load_registry(REGISTRY)
    assert reg.domains['timing.drv'].hardness == 'hard'
    assert reg.domains['antenna.violations'].hardness == 'hard'
    assert reg.domains['timing.setup'].hardness == 'soft'
    assert reg.domains['timing.setup'].floor_pointer == '/floor'
    doc = yaml.safe_load(REGISTRY.read_text())
    doc['domains']['timing.drv']['floor_pointer'] = '/floor'
    bad = write(tmp_path / 'r.yaml', yaml.safe_dump(doc))
    with pytest.raises(closure.RegistryError, match='HARD domain has no floor'):
        closure.load_registry(bad)


def test_a_setup_violation_is_repaired_and_the_improving_candidate_adopted(tmp_path, monkeypatch):
    project, arm, impl, shim = _scenario_impl(
        tmp_path, baseline=(-0.5, 0.2),
        candidates=[_cand(-0.1, 0.2), _cand(0.05, 0.2)])
    ctl = _controller(impl, arm, shim, monkeypatch, tmp_path)
    run = ctl.run_controller('postroute.repair_setup')
    assert [it.decision for it in run.iterations] == ['PROMOTED', 'PROMOTED']
    assert run.outcome is closure.Outcome.CONVERGED
    cur = json.loads((impl / prr.CURRENT).read_text())
    assert cur['candidate'] == '32-cand02'
    # The next candidate starts from the adopted one, never from the input.
    ledger = json.loads((arm / prr.LEDGER).read_text())['candidates']
    assert ledger[1]['from'] == '32-cand01'
    assert ledger[0]['params']['setup_margin_ns'] == 0
    assert ledger[1]['params']['setup_margin_ns'] == 0.05


def test_a_candidate_with_a_supply_pin_off_its_net_is_never_adopted(tmp_path, monkeypatch):
    """F24: the inserted buffer's supply pins on no net. The candidate improves
    setup and is refused anyway, by the supply-ownership gate on its own DEF."""
    project, arm, impl, shim = _scenario_impl(
        tmp_path, baseline=(-0.5, 0.2),
        candidates=[_cand(0.3, 0.2, owned=False)])
    ctl = _controller(impl, arm, shim, monkeypatch, tmp_path)
    run = ctl.run_controller('postroute.repair_setup')
    assert run.iterations[0].decision == 'ROLLED_BACK'
    assert json.loads((impl / prr.CURRENT).read_text())['candidate'] is None
    row = json.loads((arm / prr.LEDGER).read_text())['candidates'][0]
    assert row['decision'] == 'REFUSED'
    assert row['supply_ownership']['verdict'] == 'FAIL'


def test_a_hold_repair_is_adopted_through_extraction_noise_on_setup(tmp_path, monkeypatch):
    """MEASURED on spm (arm L): 17 hold buffers took worst hold -0.335 ->
    +0.326 ns and moved worst setup 4.026052 -> 4.025948 ns on paths they never
    touched. That is not a regression the step may roll a hold closure back
    for; the declared 1 ps tolerance is what tells the two apart."""
    project, arm, impl, shim = _scenario_impl(
        tmp_path, baseline=(4.026052, -0.335),
        candidates=[_cand(4.025948, 0.326)])
    ctl = _controller(impl, arm, shim, monkeypatch, tmp_path)
    run = ctl.run_controller('postroute.repair_hold')
    assert [it.decision for it in run.iterations] == ['PROMOTED'], run.to_record()
    assert run.outcome is closure.Outcome.CONVERGED
    assert json.loads((impl / prr.CURRENT).read_text())['candidate'] == '32-cand01'


def test_a_regression_tolerance_is_declared_and_never_negative(tmp_path):
    import yaml
    doc = yaml.safe_load(REGISTRY.read_text())
    assert doc['domains']['timing.setup']['regression_tolerance'] == 0.001
    assert 'regression_tolerance' not in doc['domains']['timing.drv']
    doc['domains']['timing.setup']['regression_tolerance'] = -0.1
    bad = write(tmp_path / 'r.yaml', yaml.safe_dump(doc))
    with pytest.raises(closure.RegistryError, match='regression_tolerance'):
        closure.load_registry(bad)
    reg = closure.load_registry(REGISTRY)
    setup = reg.domains['timing.setup']
    assert not setup.regresses(4.025948, 4.026052)
    assert setup.regresses(0.14, 3.33)
    # the objective's own test is untouched: a smaller loss is still no gain
    assert not setup.improves(4.0259, 4.0260)
    assert reg.domains['pnr.repair_deck.required_completeness'].regression_tolerance == 0


@pytest.mark.parametrize('antenna', [4, None])
def test_a_repair_that_creates_an_antenna_violation_is_never_adopted(tmp_path, monkeypatch, antenna):
    """Lane mig99 measured it on spm: the direct SDR DRV repair candidate
    ADDED 4 antenna violations to an antenna-clean route. Every candidate is
    counted by the step-26 instrument (OpenROAD.CheckAntennas) and one that
    creates a violation -- or cannot be counted -- is refused, however much it
    closed timing."""
    project, arm, impl, shim = _scenario_impl(
        tmp_path, baseline=(1.0, -0.2),
        candidates=[_cand(1.0, 0.05, antenna=antenna)])
    ctl = _controller(impl, arm, shim, monkeypatch, tmp_path)
    run = ctl.run_controller('postroute.repair_hold')
    assert run.iterations[0].decision == 'ROLLED_BACK'
    assert json.loads((impl / prr.CURRENT).read_text())['candidate'] is None
    row = json.loads((arm / prr.LEDGER).read_text())['candidates'][0]
    assert row['decision'] == 'REFUSED' and row['reason'].startswith('antenna (OpenROAD.CheckAntennas)')
    assert row['antenna'] == {'before': 0, 'after': antenna}


def test_antenna_is_measured_by_the_step_26_instrument_on_every_candidate():
    assert prr.MEASURE_STEPS[0] == 'OpenROAD.CheckAntennas'
    import librelane_ir_antenna as step26
    assert set(prr.ANTENNA_METRICS) == set(step26.ROUTER_METRICS)


def test_run_bridges_measures_closes_and_records_every_candidates_fate(tmp_path, monkeypatch):
    """`run` end to end: the bridge, the census baseline (same instruments),
    the three controllers in order, and a report naming the adopted candidate
    and the closure's verdict on every candidate."""
    project = tmp_path / 'proj'
    lef = write(tmp_path / 'cells.lef', LEF)
    base, cand = (4.026052, -0.335), _cand(4.025948, 0.326)
    scenario = {'32-base': {'def': _def(True), 'sta_metrics': _sta_metrics(*base),
                            'antenna_metrics': _ant(0),
                            'repair_metrics': {'vibeic__prr__changed': 0}},
                '32-cand01': cand}
    put(tmp_path / 'scenario.json', scenario)
    shim = tmp_path / 'shim'
    write(shim / 'librelane_postroute_repair.py', SHIM)
    monkeypatch.setenv('PRR_REAL_PROGRAMS', str(PROGRAMS))
    monkeypatch.setenv('PRR_SCENARIO', str(tmp_path / 'scenario.json'))
    ns = {}
    exec(compile(SHIM.split('ll.run_chain = run_chain')[0].replace(
        'sys.path.insert(0, os.environ["PRR_REAL_PROGRAMS"])', ''), 'shim', 'exec'), ns)
    monkeypatch.setattr(contract, 'run_chain', ns['run_chain'])
    monkeypatch.setattr(prr, 'fork_capability',
                        lambda image, docker='docker': {'capable': True, 'image': image})
    seen = {}

    def configs(project_, image, pdk, ids, *, pdk_root, folder, overlay, docker):
        seen['overlay'] = overlay
        root = project_ / 'phase3/librelane' / folder
        return {i: put(root / f'{i}.json', {'meta': {'step': i}, 'CELL_LEFS': [str(lef)],
                                            'STA_CORNERS': CORNERS}) for i in ids}
    monkeypatch.setattr(contract, 'resolve_step_configs', configs)
    monkeypatch.setattr(contract, 'state_from_direct',
                        lambda project_, image, cfg, views, out, **k: put(out / 'state_in.json',
                                                                         {'def': str(views['def'])}))
    sdc = write(project / 'phase3/stage3/pnr/constraint.sdc', 'create_clock -period 10 [get_ports clk]\n')
    report = prr.run(project, image='img', pdk='pdk', pdk_root=tmp_path, views={'def': sdc},
                     sdc=sdc, derate=(0.95, 1.05), programs_dir=shim)
    assert report['verdict'] == 'PASS' and report['adopted'] == '32-cand01'
    assert [r['outcome'] for r in report['closure']] == ['NOT_TRIGGERED', 'CONVERGED', 'NOT_TRIGGERED']
    assert report['candidates'][0]['closure_decision'] == 'PROMOTED'
    assert report['candidates'][0]['controller'] == 'postroute.repair_hold'
    assert report['baseline']['hold_ws_min'] == -0.335 and report['final']['hold_ws_min'] == 0.326
    # the repair scene is the sign-off scene
    scene = Path(seen['overlay']['PNR_SDC_FILE'][0])
    assert seen['overlay']['SIGNOFF_SDC_FILE'][0] == str(scene)
    assert scene.read_text().endswith('set_timing_derate -early 0.95\nset_timing_derate -late 1.05\n')
    census = json.loads((project / 'phase3/librelane/32-config/Vibeic.PostRouteRepair@census.json').read_text())
    assert census['VIBEIC_PRR_CENSUS_ONLY'] is True


# ----------------------------------------------------- the plugin step ---

def _tcl_env_vars():
    tcl = (STEP_DIR / 'postroute_repair.tcl').read_text()
    return set(re.findall(r'::env\((VIBEIC_PRR_[A-Z_]+)\)', tcl)) | set(
        re.findall(r'\b(VIBEIC_PRR_[A-Z_]+)\b', tcl))


def test_every_variable_the_script_reads_is_declared_by_the_step():
    src = (STEP_DIR / 'postroute_repair.py').read_text()
    declared = set(re.findall(r'"(VIBEIC_PRR_[A-Z_]+)"', src))
    used = {v for v in _tcl_env_vars() if not v.endswith(('_TOOL_INCAPABLE', '_EXTRACTION_FAILED',
                                                           '_CORNER_UNANNOTATED', '_ROUTE_REFUSED'))}
    assert used <= declared, sorted(used - declared)
    assert set(prr.PARAM_VARS.values()) <= declared


def test_the_step_is_registered_with_every_earlier_plugin_step():
    init = (STEP_DIR / '__init__.py').read_text()
    for name in ('InsertSpareCells', 'GateLevelSim', 'ClockPathDriveSizing',
                 'IRDropChecker', 'TransientIR', 'PostRouteRepair'):
        assert re.search(rf'\b{name}\b', init), name
    src = (STEP_DIR / 'postroute_repair.py').read_text()
    assert 'id = "Vibeic.PostRouteRepair"' in src
    assert src.count('@Step.factory.register()') == 1


def test_the_script_orders_the_repair_and_connects_before_it_writes():
    tcl = (STEP_DIR / 'postroute_repair.tcl').read_text()
    lines = [ln.strip() for ln in tcl.splitlines() if ln.strip() and not ln.strip().startswith('#')]

    def at(pattern):
        hits = [i for i, ln in enumerate(lines) if re.search(pattern, ln)]
        assert hits, pattern
        return hits[0]
    # parasitics from the router's wires, then the fork flag, then repairs
    assert at(r'extract_parasitics') < at(r'estimate_parasitics -detailed_routing')
    assert at(r'estimate_parasitics -detailed_routing') < at(r'log_cmd repair_design')
    # setup, then hold
    assert at(r'log_cmd repair_design') < at(r'repair_timing \{\*\}\$setup_args') < at(r'repair_timing \{\*\}\$hold_args')
    # a no-op repair stops before anything is re-routed
    assert at(r'vic_changed == 0') < at(r'detailed_placement')
    # supply connected after legalization, before the ECO route and the write
    assert at(r'detailed_placement') < at(r'^global_connect') < at(r'detailed_route') < at(r'^write_views')
    assert 'set_global_connections' in tcl
    # the ECO route is scoped to the touched nets
    assert '-nets [dict keys $dirty]' in tcl
    assert 'vic_eco_route ::vic_dirty eco_route' in tcl
    # antenna residue goes to the tool's repair, on the new diodes' nets only,
    # after the ECO route and before anything is written
    assert at(r'set ::vic_eco_ok \[vic_eco_route') < at(r'log_cmd repair_antennas') \
        < at(r'vic_eco_route ::vic_ant_dirty antenna_route') < at(r'^write_views')


@pytest.mark.skipif(not __import__('shutil').which('tclsh'), reason='tclsh absent')
def test_the_neighbour_reader_takes_the_nets_that_share_a_violation(tmp_path):
    """The script's own proc, run by tclsh on the router's report grammar
    (recorded from the fork's DRT-0712 refusal on spm, 2026-09-27)."""
    tcl = (STEP_DIR / 'postroute_repair.tcl').read_text()
    proc = tcl[tcl.index('proc vic_violation_neighbours'):]
    proc = proc[:proc.index('\n}\n') + 3]
    drc = write(tmp_path / 'eco.drc', textwrap.dedent('''\
        violation type: Metal Spacing
        \tsrcs: net:u_core/_092_ net:net7
        \tbbox = (621.1800, 2753.0500) - (621.4100, 2753.1000) on Layer Metal2
        violation type: Min Area
        \tsrcs: net:y__core
        \tbbox = (418.6900, 2236.4500) - (419.0700, 2236.8300) on Layer Metal2
        violation type: Metal Spacing
        \tsrcs: net:VDD net:net7
        \tbbox = (1.0, 2.0) - (3.0, 4.0) on Layer Metal1
        '''))
    script = write(tmp_path / 't.tcl', textwrap.dedent('''\
        # odb, reduced to the three calls the proc makes
        proc ::blk_find {cmd name} {
            if {$name eq "VDD"} { return ::net_vdd }
            return ::net_sig
        }
        proc ::net_vdd {cmd} { if {$cmd eq "getSigType"} { return POWER }; return 0 }
        proc ::net_sig {cmd} { if {$cmd eq "getSigType"} { return SIGNAL }; return 0 }
        set ::block ::blk_find
        ''') + proc + f'''
set got [vic_violation_neighbours {drc} [dict create net7 x]]
puts [lsort [dict keys $got]]
''')
    import subprocess
    out = subprocess.run(['tclsh', str(script)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ['u_core/_092_'], out.stdout


# ------------------------------------------------------------ the runner ---

def test_the_switch_selects_step_32_and_refuses_dual(tmp_path):
    runner = importlib.import_module('phase3_one_shot_runner')
    assert runner._librelane_postroute_repair_mode(tmp_path) == 'direct'
    put(tmp_path / 'phase3/librelane_switch.json', {'steps': {'32': 'librelane'}})
    assert runner._librelane_postroute_repair_mode(tmp_path) == 'librelane'
    assert prr.refusal('librelane') is None
    # dual needs step 21 on LibreLane: its pre-DRT arm is that chain's own
    assert prr.refusal('dual').startswith('LL_PRR_DUAL_NEEDS_LL21')
    assert prr.refusal('dual', 'librelane') is None and prr.refusal('dual', 'dual') is None


def _deck_kwargs(tmp_path, monkeypatch, switch):
    import test_librelane_cts_hold as harness
    runner = harness.runner
    monkeypatch.setattr(runner, '_openroad_supports_postroute_spef_repair', lambda c: True)
    return harness._drive_step_pnr(tmp_path, monkeypatch, switch)


def test_with_step_32_on_librelane_the_deck_carries_no_repair_of_its_own(tmp_path, monkeypatch):
    """The custom step owns the post-route repair: the deck's two sign-off DRV
    repair transactions are not emitted, and nothing else changes."""
    direct = _deck_kwargs(tmp_path / 'd', monkeypatch, None)
    ll = _deck_kwargs(tmp_path / 'l', monkeypatch, {'32': 'librelane'})
    other = _deck_kwargs(tmp_path / 'o', monkeypatch, {'19': 'direct'})
    joined = lambda kw: '\n'.join(str(v) for v in kw.values() if isinstance(v, str))
    assert 'postroute_drv_repair' in joined(direct)
    assert 'postroute_drv_reconverge' in joined(direct)
    assert 'postroute_drv_repair' not in joined(ll).replace('SDR_SKIP_STOCK_OPENROAD', '')
    assert 'SDR2_BEGIN' not in joined(ll)
    # a switch that names other steps leaves the deck exactly as it was
    norm = lambda kw: {k: str(v).replace(str(tmp_path / 'o'), 'P').replace(str(tmp_path / 'd'), 'P')
                       for k, v in kw.items()}
    assert norm(other) == norm(direct)


def _runner_step(tmp_path, monkeypatch, report):
    import test_pad_connected_pdn_ring as ring_fixture
    runner = importlib.import_module('phase3_one_shot_runner')
    project = tmp_path / 'proj'
    pnr = project / 'phase3/stage3/pnr'
    write(pnr / 'routed.def', 'DESIGN top ;\nEND DESIGN\n')
    write(pnr / 'dut_pnr.v', 'module top(); endmodule\n')
    write(pnr / 'constraint.sdc', '')
    write(pnr / 'pnr.tcl', 'add_global_connection -net VDD -pin_pattern {^VDD$} -power\n')
    put(project / 'phase3/librelane_switch.json', {'steps': {'32': 'librelane'}})
    pdk = ring_fixture._pdk(tmp_path, ring=None)
    monkeypatch.setattr(contract, 'resolve_image', lambda p=None: 'img')
    monkeypatch.setattr(contract, 'pdk_root_resolution',
                        lambda *a, **k: {'path': str(tmp_path / 'pdkroot')})
    calls = {}

    def run(project_, **kw):
        calls.update(kw)
        return report(project_)
    monkeypatch.setattr(prr, 'run', run)
    result = runner.step_postroute_repair_librelane(project, 'dut', pdk, 'unused')
    return runner, project, pnr, result, calls


def test_the_runner_hands_the_adopted_route_to_the_direct_paths(tmp_path, monkeypatch):
    def report(project):
        folder = project / 'phase3/librelane/32-cand01/01-vibeic-postrouterepair'
        d = write(folder / 'top.def', 'DESIGN top ;\n# repaired\nEND DESIGN\n')
        n = write(folder / 'top.nl.v', 'module top(); /* repaired */ endmodule\n')
        state = put(folder / 'state_out.json', {'def': str(d), 'nl': str(n)})
        # the adopted candidate's own measurement of its output (cmp3 D15):
        # OpenROAD.CheckAntennas after the repair, and its unrouted census
        return {'verdict': 'PASS', 'adopted': '32-cand01', 'adopted_state': str(state),
                'baseline': {'hold_ws_min': -0.335, 'drv_count': 0},
                'final': {'hold_ws_min': 0.326, 'drv_count': 0, 'antenna_nets': 0,
                          'antenna_pins': 0, 'antenna_state': 'cand01/02-checkantennas'},
                'candidates': [{'candidate': '32-cand01',
                                'repair_metrics': {'vibeic__prr__unrouted__added': 0}}],
                'corners': CORNERS,
                'final_supply_ownership': {'verdict': 'PASS'}}
    runner, project, pnr, result, calls = _runner_step(tmp_path, monkeypatch, report)
    assert result.status == 'PASS' and result.detail.startswith('ADOPTED 32-cand01'), result.detail
    assert '# repaired' in (pnr / 'routed.def').read_text()
    assert 'repaired' in (pnr / 'dut_pnr.v').read_text()
    assert '# repaired' not in (pnr / 'routed_base_prerepair.def').read_text()
    claim = json.loads((pnr / runner._DRV_PROMOTION_CLAIM).read_text())
    assert claim['program'] == 'librelane_postroute_repair' and claim['promoted'] is True
    assert not (pnr / runner._DRV_PROMOTION_NOT_RUN).exists()
    # the deck's own supply rules and fill policy travel with the database
    assert 'add_global_connection' in Path(calls['pg_rules_tcl']).read_text()
    assert calls['derate'] == (runner._FLAT_OCV_DERATE_EARLY, runner._FLAT_OCV_DERATE_LATE)
    handoff = json.loads((project / 'reports/phase3/librelane_postroute_repair_handoff.json').read_text())
    assert set(handoff['views']) == {'def', 'nl'}


def test_the_runner_keeps_the_input_route_and_says_why_when_nothing_is_adopted(tmp_path, monkeypatch):
    def report(project):
        return {'verdict': 'PASS', 'adopted': None, 'baseline': {}, 'final': {},
                'closure': [{'controller': 'postroute.repair_hold', 'outcome': 'PLATEAU'}]}
    runner, project, pnr, result, _ = _runner_step(tmp_path, monkeypatch, report)
    assert result.status == 'PASS' and 'no candidate adopted' in result.detail
    assert (pnr / 'routed.def').read_text() == 'DESIGN top ;\nEND DESIGN\n'
    assert not (pnr / 'routed_base_prerepair.def').exists()
    rec = json.loads((pnr / runner._DRV_PROMOTION_NOT_RUN).read_text())
    assert rec['not_run_stage'] == 'librelane_closure_kept_input'
    assert 'postroute.repair_hold: PLATEAU' in rec['reason']


def test_the_main_flow_replaces_the_direct_repair_producers_only_when_selected():
    """Source contract of the one call site: the LibreLane producer runs only
    when selected, and then neither direct producer does."""
    src = (PROGRAMS / 'phase3_one_shot_runner.py').read_text()
    at = src.index('_prr_on_librelane = _librelane_postroute_repair_mode(project) != "direct"')
    block = src[at:at + 3000]
    assert 'if _chain_ok and _prr_on_librelane:' in block
    assert block.index('step_postroute_repair_librelane') < block.index('step_signoff_spef_repair')
    assert block.count('if _chain_ok and not _prr_on_librelane:') == 2


# --------------------------------------------- r2: inside the LL21 chain, dual ---

def _chain_setup(tmp_path, monkeypatch, scenario):
    """`run_in_chain` with the real closure (subprocess shim), the real
    registry and only the docker runs faked."""
    project = tmp_path / 'proj'
    lef = write(tmp_path / 'cells.lef', LEF)
    put(tmp_path / 'scenario.json', scenario)
    shim = tmp_path / 'shim'
    write(shim / 'librelane_postroute_repair.py', SHIM)
    monkeypatch.setenv('PRR_REAL_PROGRAMS', str(PROGRAMS))
    monkeypatch.setenv('PRR_SCENARIO', str(tmp_path / 'scenario.json'))
    ns = {}
    exec(compile(SHIM.split('ll.run_chain = run_chain')[0].replace(
        'sys.path.insert(0, os.environ["PRR_REAL_PROGRAMS"])', ''), 'shim', 'exec'), ns)
    monkeypatch.setattr(contract, 'run_chain', ns['run_chain'])
    monkeypatch.setattr(prr, 'fork_capability',
                        lambda image, docker='docker': {'capable': True, 'image': image})

    def configs(project_, image, pdk, ids, *, pdk_root, folder, overlay, docker):
        root = project_ / 'phase3/librelane' / folder
        return {i: put(root / f'{i}.json', {'meta': {'step': i}, 'CELL_LEFS': [str(lef)],
                                            'STA_CORNERS': CORNERS}) for i in ids}
    monkeypatch.setattr(contract, 'resolve_step_configs', configs)
    monkeypatch.setattr(contract, 'state_from_direct',
                        lambda *a, **k: pytest.fail('inside the chain nothing is bridged'))
    sdc = write(project / 'phase3/stage3/pnr/constraint.sdc', '')
    route = put(project / 'phase3/librelane/21-route/13-fill/state_out.json',
                {'odb': 'r.odb', 'def': 'r.def'})
    return project, shim, sdc, route


def _base(setup, hold, antenna=0):
    return {'def': _def(True), 'sta_metrics': _sta_metrics(setup, hold),
            'antenna_metrics': _ant(antenna), 'repair_metrics': {'vibeic__prr__changed': 0}}


def test_in_the_chain_the_route_state_is_repaired_without_a_bridge(tmp_path, monkeypatch):
    project, shim, sdc, route = _chain_setup(tmp_path, monkeypatch, {
        '32-base': _base(4.026052, -0.335), '32-cand01': _cand(4.025948, 0.326)})
    report = prr.run_in_chain(project, mode='librelane', image='img', pdk='pdk',
                              pdk_root=tmp_path, sdc=sdc, derate=(0.95, 1.05),
                              route_state=route, route_drc=0, programs_dir=shim)
    assert report['site'] == 'after_route' and report['adopted'] == '32-cand01'
    assert report['baseline']['hold_ws_min'] == -0.335
    ctx = json.loads((project / prr.ARM_REL / prr.IMPL_DIR / prr.CONTEXT).read_text())
    assert 'VIBEIC_PRR_REFILL_TCL' not in json.loads(
        Path(ctx['configs'][prr.REPAIR_STEP]).read_text()), \
        "the LL21 route's fillers are LibreLane's; the refill is too"


def test_dual_runs_three_arms_and_selects_by_hold_then_setup(tmp_path, monkeypatch):
    """review70 step 32 dual: A = the pre-DRT repair (step 21's LibreLane
    route with the flow's RUN_POST_GRT_* gates on), B = this repair after
    DRT, and A then B. Every arm is measured by the same instruments."""
    scenario = {
        '32-postdrt-base': _base(4.0, -0.3), '32-postdrt-cand01': _cand(3.99995, 0.30),
        '32-pregrt-base': _base(4.2, 0.10),
        '32-pregrt_postdrt-base': _base(4.2, 0.10)}
    project, shim, sdc, route = _chain_setup(tmp_path, monkeypatch, scenario)
    pre_state = put(project / 'phase3/librelane/21-route-pregrt/13-fill/state_out.json',
                    {'odb': 'p.odb', 'def': 'p.def'})
    seen = {}

    def variant_arm(lane, extra):
        seen[lane] = extra
        return {'final': pre_state, 'route_drc': [{'run': 'drt-run-0', 'markers': 0}]}
    report = prr.run_in_chain(project, mode='dual', image='img', pdk='pdk',
                              pdk_root=tmp_path, sdc=sdc, derate=(0.95, 1.05),
                              route_state=route, route_drc=0, variant_arm=variant_arm,
                              programs_dir=shim)
    assert set(seen['21-route-pregrt']) == set(prr.PREGRT_GATES)
    assert all(v[0] is True for v in seen['21-route-pregrt'].values())
    assert set(report['arms']) == set(prr.DUAL_ARMS)
    assert report['arms']['pregrt']['closure'] == [], 'arm A is the route alone'
    assert report['selected_arm'] == 'postdrt', report['selection']
    assert report['adopted'] == '32-postdrt-cand01'


def test_dual_never_selects_an_arm_with_a_route_or_antenna_violation(tmp_path, monkeypatch):
    scenario = {
        '32-postdrt-base': _base(4.0, 0.2),
        '32-pregrt-base': _base(4.5, 0.5, antenna=2),
        '32-pregrt_postdrt-base': _base(4.5, 0.5, antenna=2)}
    project, shim, sdc, route = _chain_setup(tmp_path, monkeypatch, scenario)
    pre_state = put(project / 'phase3/librelane/21-route-pregrt/13-fill/state_out.json',
                    {'odb': 'p.odb', 'def': 'p.def'})
    report = prr.run_in_chain(
        project, mode='dual', image='img', pdk='pdk', pdk_root=tmp_path, sdc=sdc,
        derate=(0.95, 1.05), route_state=route, route_drc=0, programs_dir=shim,
        variant_arm=lambda lane, extra: {'final': pre_state, 'route_drc': []})
    assert report['selection']['feasible'] == ['postdrt']
    assert report['selected_arm'] == 'postdrt'


def test_the_route_chain_calls_step_32_before_its_handoff():
    """Source contract of step 21's one call site: step 32 runs on the
    selected route before the views are copied and the tail resumes."""
    src = (PROGRAMS / 'librelane_route.py').read_text()
    at = src.index('step32 = getattr(R, "postroute_repair_after_route", None)')
    assert at < src.index('targets = {"odb": out_dir / "routed_preantenna.odb",')
    assert 'views = dict(views, **post32["views"])' in src
    runner = importlib.import_module('phase3_one_shot_runner')
    assert callable(runner.postroute_repair_after_route)


def test_the_runner_records_the_in_chain_repair_without_repairing_twice(tmp_path, monkeypatch):
    runner = importlib.import_module('phase3_one_shot_runner')
    import test_pad_connected_pdn_ring as ring_fixture
    project = tmp_path / 'proj'
    pnr = project / 'phase3/stage3/pnr'
    write(pnr / 'routed.def', 'DESIGN top ;\n# adopted\nEND DESIGN\n')
    write(pnr / 'dut_pnr.v', 'module top(); endmodule\n')
    write(pnr / 'constraint.sdc', '')
    put(project / 'phase3/librelane_switch.json',
        {'steps': {'21': 'librelane', '32': 'librelane'}})
    report = put(project / prr.REPORT_REL, {
        'verdict': 'PASS', 'site': 'after_route', 'adopted': '32-cand01',
        'baseline': {'hold_ws_min': -0.3},
        'final': {'hold_ws_min': 0.3, 'antenna_nets': 0, 'antenna_pins': 0,
                  'antenna_state': 'cand01/02-checkantennas'},
        'candidates': [{'candidate': '32-cand01',
                        'repair_metrics': {'vibeic__prr__unrouted__added': 0}}]})
    put(project / 'reports/phase3/librelane_route_handoff.json',
        {'postroute_repair': {'report_sha256': contract.digest(report)}})
    monkeypatch.setattr(prr, 'run', lambda *a, **k: pytest.fail('repaired twice'))
    pdk = ring_fixture._pdk(tmp_path, ring=None)
    result = runner.step_postroute_repair_librelane(project, 'dut', pdk, 'unused')
    assert result.status == 'PASS' and 'in the step-21 LibreLane chain' in result.detail
    assert '# adopted' in (pnr / 'routed.def').read_text()
    assert json.loads((pnr / runner._DRV_PROMOTION_CLAIM).read_text())['promoted'] is True
    # a report the route receipt does not name is not this run's
    put(project / 'reports/phase3/librelane_route_handoff.json',
        {'postroute_repair': {'report_sha256': 'other'}})
    assert runner._postroute_repair_in_chain_report(project) is None


def test_the_route_hook_hands_on_the_adopted_database_and_keeps_the_base(tmp_path, monkeypatch):
    runner = importlib.import_module('phase3_one_shot_runner')
    import test_pad_connected_pdn_ring as ring_fixture
    project = tmp_path / 'proj'
    put(project / 'phase3/librelane_switch.json', {'steps': {'21': 'librelane', '32': 'dual'}})
    base_def = write(tmp_path / 'route.def', 'DESIGN top ;\nEND DESIGN\n')
    route_state = put(tmp_path / 'route_state.json', {'odb': 'r.odb', 'def': str(base_def)})
    adopted = put(tmp_path / 'cand/state_out.json', {'odb': str(tmp_path / 'c.odb'),
                                                     'def': str(tmp_path / 'c.def')})
    calls = {}

    def run_in_chain(project_, **kw):
        calls.update(kw)
        return put(project_ / prr.REPORT_REL, {'verdict': 'PASS', 'adopted': '32-postdrt-cand01',
                                               'adopted_state': str(adopted),
                                               'selected_arm': 'postdrt',
                                               # its own measurement (cmp3 D15)
                                               'final': {'antenna_nets': 0, 'antenna_pins': 0},
                                               'candidates': [{
                                                   'candidate': '32-postdrt-cand01',
                                                   'repair_metrics': {
                                                       'vibeic__prr__unrouted__added': 0}}]}) and \
            json.loads((project_ / prr.REPORT_REL).read_text())
    monkeypatch.setattr(prr, 'run_in_chain', run_in_chain)
    pdk = ring_fixture._pdk(tmp_path, ring=None)
    out = runner.postroute_repair_after_route(
        project=project, pdk=pdk, image='img', pdk_root=tmp_path, sdc=base_def,
        deck='add_global_connection -net VDD -pin_pattern {^VDD$} -power\n',
        route_state=route_state, route_views={'odb': tmp_path / 'r.odb', 'def': base_def},
        route_drc=0, variant_arm=lambda *a: None)
    assert calls['mode'] == 'dual' and calls['route_state'] == route_state
    assert out['views'] == {'odb': tmp_path / 'c.odb', 'def': tmp_path / 'c.def'}
    assert out['record']['report_sha256'] == contract.digest(project / prr.REPORT_REL)
    assert (runner._pl.pnr_dir(project) / 'routed_base_prerepair.def').read_text() == base_def.read_text()
    # step 32 direct, or a direct route handed over: nothing runs here
    put(project / 'phase3/librelane_switch.json', {'steps': {'21': 'librelane'}})
    assert runner.postroute_repair_after_route(
        project=project, pdk=pdk, image='img', pdk_root=tmp_path, sdc=base_def, deck='',
        route_state=route_state, route_views={}, route_drc=0, variant_arm=None) is None


def test_the_class_default_route_resolves_the_pdk_like_every_other_step(tmp_path, monkeypatch):
    """r4: with no switch file the route asks the contract's resolver for THIS
    design's PDK and image (materialised from the image), as 15..20 and 32 do;
    measured on spm: asking with no PDK/image refused LL_PDK_ROOT_NOT_DECLARED."""
    import test_librelane_route as tr
    runner = tr.runner
    project, out_dir, pnr_tcl = tr._route_project(tmp_path, {'steps': {'21': 'librelane'}})
    (project / 'phase3/librelane_switch.json').unlink(missing_ok=True)

    def docker(container, cmd, *a, **k):
        for ext in ('odb', 'def', 'nl.v'):
            write(out_dir / f'route_split/pre_route.{ext}', 'x\n')
        return 0, '', ''
    asked = {}

    def resolver(*a, **k):
        asked.update(args=a, kwargs=k)
        return None
    monkeypatch.setattr(runner, '_docker_exec', docker)
    monkeypatch.setattr(runner, '_after_restore_tcl', lambda *a, **k: '')
    monkeypatch.setattr(runner, '_container_mounts', lambda c: [])
    monkeypatch.setattr(contract, 'resolve_pdk_root', resolver)
    rc, out, _ = tr.route.execute(
        runner, project=project, pdk=SimpleNamespace(name='pdkX'), container='c',
        out_dir=out_dir, out_dir_c=str(out_dir), pnr_tcl=pnr_tcl, mode='librelane',
        cmd=f'openroad {pnr_tcl} | tee {out_dir}/openroad.log', spare_plan=None,
        exec_kwargs={})
    assert asked['args'][:2] == (project, 'pdkX')
    assert asked['kwargs']['image'] == contract.resolve_image(project)


def test_every_geometry_step_reads_the_routes_tech_lef(tmp_path, monkeypatch):
    """T105: the route's via-legalized tech LEF, bound by sha256, is what
    step 32's RCX and bridge read; an APPLIED record whose file is gone
    refuses by name instead of falling back to the PDK's LEF."""
    import librelane_pv_signoff as pv
    project = tmp_path / 'proj'
    sdc = write(project / 'phase3/stage3/pnr/constraint.sdc', '')
    lef = write(project / 'phase3/stage3/pnr/active_via_legalized.tlef', 'VERSION 5.8 ;\n')
    put(project / pv.VIA_LEGALIZATION_REL, {'status': 'APPLIED', 'derived_tech_lef': str(lef),
                                            'derived_sha256': contract.digest(lef)})
    seen = {}

    def configs(project_, image, pdk, ids, *, pdk_root, folder, overlay, docker):
        seen.update(overlay)
        root = project_ / 'phase3/librelane' / folder
        return {i: put(root / f'{i}.json', {'meta': {'step': i}, 'STA_CORNERS': CORNERS})
                for i in ids}
    monkeypatch.setattr(contract, 'resolve_step_configs', configs)
    prr._prepare(project, image='img', pdk='pdk', pdk_root=tmp_path, sdc=sdc,
                 derate=(0.95, 1.05), pg_rules_tcl=None, refill_tcl=None, docker='docker')
    assert seen['TECH_LEFS'][0] == {'*': str(lef.resolve())}
    lef.write_text('changed\n')
    with pytest.raises(contract.Refusal) as exc:
        prr._prepare(project, image='img', pdk='pdk', pdk_root=tmp_path, sdc=sdc,
                     derate=(0.95, 1.05), pg_rules_tcl=None, refill_tcl=None, docker='docker')
    assert exc.value.code == 'LL_ROUTE_TECH_LEF_UNBOUND'


def test_a_repair_not_found_in_the_chain_says_so_and_why(tmp_path, monkeypatch, capsys):
    """silent_decline_audit: when step 32 is not taken from the step-21 chain,
    the decline is disclosed with its reason before the after-route repair."""
    runner = importlib.import_module('phase3_one_shot_runner')
    import test_pad_connected_pdn_ring as ring_fixture
    project = tmp_path / 'proj'
    put(project / 'phase3/librelane_switch.json', {'steps': {'21': 'direct', '32': 'librelane'}})
    pdk = ring_fixture._pdk(tmp_path, ring=None)
    runner.step_postroute_repair_librelane(project, 'dut', pdk, 'unused')
    err = capsys.readouterr().err
    assert 'PRR_NOT_IN_CHAIN: step 21 routed direct' in err
    put(project / 'phase3/librelane_switch.json', {'steps': {'21': 'librelane', '32': 'librelane'}})
    put(project / 'reports/phase3/librelane_route_handoff.json', {'postroute_repair': None})
    report, why = runner._postroute_repair_in_chain(project)
    assert report is None and 'names no step-32 report' in why
