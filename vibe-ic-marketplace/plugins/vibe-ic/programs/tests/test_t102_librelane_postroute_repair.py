"""T102: step 32 (post-route repair) as a LibreLane custom step under the PPA closure.

The closure, the registry, the measurement and actuator CLIs, the supply
ownership gate and the runner's deck builder run for real. Only an EDA tool's
file writes are substituted: `librelane_contract.run_chain` (the `docker run`
of a LibreLane step) is replaced by a writer of the step folders a real run
leaves (`state_out.json` with the tool's metric names, the repair step's DEF).
"""
import importlib
import json
import os
import re
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

STEP_DIR = PROGRAMS / 'librelane_plugins' / 'librelane_plugin_vibeic'
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
    put(impl / prr.CURRENT, {'candidate': None, 'antenna': antenna, 'measurement': dict(
        measurement, sta_state=str(sta), sta_state_sha256=contract.digest(sta))})
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
           'configs': {prr.REPAIR_STEP: str(cfg), 'OpenROAD.RCX': str(cfg),
                       'OpenROAD.STAPostPNR': str(cfg)}, 'corners': CORNERS}
    arm = project / prr.ARM_REL
    impl = arm / prr.IMPL_DIR
    put(impl / prr.CONTEXT, ctx)
    sta = put(project / 'phase3/librelane/32-base/03-sta/state_out.json',
              {'metrics': _sta_metrics(*baseline)})
    input_state = put(project / 'phase3/librelane/32-config/bridge/state_in.json', {'def': 'x'})
    put(impl / prr.CURRENT, {'candidate': None, 'repair_input': str(input_state),
                            'repair_state': str(input_state), 'antenna': 0,
                            'measurement': dict(prr.summarize(_sta_metrics(*baseline), CORNERS),
                                                sta_state=str(sta),
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


def _cand(setup, hold, drv=(0, 0, 0), *, changed=1, owned=True, antenna=0):
    return {'def': _def(owned), 'sta_metrics': _sta_metrics(setup, hold, drv),
            'repair_metrics': {'vibeic__prr__changed': changed,
                               'vibeic__prr__before__antenna__violating_nets': 0,
                               'vibeic__prr__after__antenna__violating_nets': antenna}}


def test_a_drv_fix_that_gives_up_setup_is_rolled_back(tmp_path, monkeypatch):
    """T98's finding: a DRV repair took SS setup +3.33 -> +0.14 because it was
    the first candidate that passed DRV. Setup is re-measured on every
    candidate, so that candidate is rolled back and the input route stays."""
    project, arm, impl, shim = _scenario_impl(
        tmp_path, baseline=(3.33, 0.2, (4, 0, 0)),
        candidates=[_cand(0.14, 0.2, (0, 0, 0))])
    ctl = _controller(impl, arm, shim, monkeypatch, tmp_path)
    assert ctl.run_controller('postroute.repair_setup').outcome is closure.Outcome.NOT_TRIGGERED
    run = ctl.run_controller('postroute.repair_drv')
    assert run.iterations and run.iterations[0].decision == 'ROLLED_BACK', run.to_record()
    assert 'timing.setup.wns_ns: 3.33 -> 0.14' in run.iterations[0].decision_reason
    assert json.loads((impl / prr.CURRENT).read_text())['candidate'] is None
    ledger = json.loads((arm / prr.LEDGER).read_text())['candidates']
    assert [r['decision'] for r in ledger] == ['PROPOSED']


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


def test_a_hold_repair_that_leaves_an_antenna_is_rolled_back(tmp_path, monkeypatch):
    project, arm, impl, shim = _scenario_impl(
        tmp_path, baseline=(1.0, -0.2),
        candidates=[_cand(1.0, 0.05, antenna=2)])
    ctl = _controller(impl, arm, shim, monkeypatch, tmp_path)
    run = ctl.run_controller('postroute.repair_hold')
    assert run.iterations[0].decision == 'ROLLED_BACK'
    assert 'antenna.violation_count: 0.0 -> 2.0' in run.iterations[0].decision_reason


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
    assert '-nets [dict keys $::vic_dirty]' in tcl


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
    assert prr.refusal('dual').startswith('LL_PRR_DUAL_UNSUPPORTED')


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
