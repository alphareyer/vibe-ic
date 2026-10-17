"""Source CLI controls: native backend entries remain bounded and mode-specific.

No phase/tool is dispatched: every positive entry deliberately omits its exit.
"""
from pathlib import Path
import subprocess
import sys

import pytest

from _route_fixture import stage_owner_route


RUNNER = Path(__file__).resolve().parents[1] / "vibe_ic_one_shot_runner.py"


def observe(project, step, mode="default"):
    stage_owner_route(project, "ic")
    return subprocess.run(
        [sys.executable, str(RUNNER), str(project), "--route", "ic",
         "--execution-mode", mode, "--entry-step", step,
         "--no-dashboard", "--skip-hardware"],
        capture_output=True, text=True)


@pytest.mark.parametrize("step", ["32", "23", "35", "17", "15.5ic", "31"])
def test_supported_default_entry_reaches_required_exit_guard(tmp_path, step):
    result = observe(tmp_path, step)
    assert result.returncode == 2
    assert "needs --exit-step" in result.stderr, result.stdout + result.stderr
    assert "no runner can be entered" not in result.stderr


@pytest.mark.parametrize("step", ["999", "not_a_step", "36", "3"])
def test_unknown_or_non_native_span_keeps_entry_refusal(tmp_path, step):
    result = observe(tmp_path, step)
    assert result.returncode == 2
    if step == "36":
        # The fixed-order release owner now supports this canonical row;
        # it must still refuse an unbounded entry before dispatching a tool.
        assert "needs --exit-step" in result.stderr, result.stdout + result.stderr
        assert "no runner can be entered" not in result.stderr
    else:
        assert "no runner can be entered" in result.stderr, result.stdout + result.stderr


def test_ultra_does_not_gain_midspan_entry(tmp_path):
    result = observe(tmp_path, "32", "ultra")
    assert result.returncode == 2
    assert "no runner can be entered" in result.stderr, result.stdout + result.stderr
    assert "needs --exit-step" not in result.stderr
    assert "[entry] step 32 ->" not in result.stdout


def test_other_runner_owner_is_not_reassigned_to_phase3(tmp_path):
    result = observe(tmp_path, "A2")
    assert result.returncode == 2
    assert "owned by analog_one_shot_runner" in result.stderr
    assert "[entry] step A2 -> phase3" not in result.stdout


def test_backend_window_authority_reads_the_shipped_canonical_row(monkeypatch):
    import _flow_yaml
    import execution_policy
    import phase3_one_shot_runner as phase3
    from _hostpaths import require_repo

    flow = _flow_yaml.load(require_repo(
        "vibe-ic-marketplace", "plugins", "vibe-ic", "flow",
        "phase1_phase2_phase3.yaml"))
    row = next(item for item in flow["steps"] if str(item["id"]) == "32")
    assert row["stage"] == "stage3"
    monkeypatch.setattr(execution_policy, "request", lambda: {"mode": "default"})
    assert phase3._phase3_window_sites(str(row["id"]), str(row["id"])) == ["default_backend"]
    monkeypatch.setattr(execution_policy, "request", lambda: {"mode": "ultra"})
    assert phase3._phase3_window_sites(str(row["id"]), str(row["id"])) == ["enclosing_canonicalize"]


@pytest.fixture
def cross_phase_transport(monkeypatch):
    from test_execution_receipt_chain import isolated_transport
    transport = isolated_transport.__wrapped__(monkeypatch)
    next(transport)
    yield
    with pytest.raises(StopIteration):
        next(transport)


@pytest.mark.parametrize("case", ["current", "phase2_failure", "handoff_failure",
                                  "handoff_missing_input", "step31"])
def test_default_cross_phase_keeps_synthesis_in_phase2(
        tmp_path, monkeypatch, cross_phase_transport, case):
    """Real front door, Step9/14 owners and P3 window; no physical EDA.

    The Phase2 process transport drives the existing Step9 producer only;
    this does not claim execution of all of design.main or Steps10--13.
    The actual Step14 producer/full consumer must authorize the transition.
    The real P3 dispatcher then stops on absent physical inputs, rather than
    running a native physical tool or inventing a physical row PASS.
    """
    import json
    import os
    from dataclasses import asdict
    from types import SimpleNamespace
    import _path_layout as layout
    import execution_policy as policy
    import phase3_one_shot_runner as phase3
    import design_one_shot_runner as design
    import librelane_contract as contract
    import step_output_collector as versions
    import vibe_ic_one_shot_runner as front
    from test_default_frontend_r2 import _synthesis, put
    from test_execution_receipt_chain import real_entry
    from test_step14_report_producer_authorship import GOOD

    project, rtl, pdk, native_calls = _synthesis(tmp_path, monkeypatch, ordinary=True)
    stage_owner_route(project, "ic")
    if case != "handoff_missing_input":
        put(project / "phase1/generated_docs/L5_ADI_SPEC.json", {"no_analog": True})
    # Admission reads image metadata, not the forbidden generic producer.
    # Replay only this exact read from the real enclosing launch observation.
    import _eda_pin
    docker_metadata = _eda_pin._docker
    authority = json.loads(Path(os.environ["VIBEIC_LIBRELANE_LOCAL_ATTESTATION"]).read_text())
    def inspect_metadata(*argv, **kwargs):
        if argv[:3] == ("inspect", "--format", "{{.Image}}\t{{.Config.Image}}"):
            item = authority["container_inspect"]
            return 0, item["Image"] + "\t" + item["Config"]["Image"] + "\n", ""
        return docker_metadata(*argv, **kwargs)
    monkeypatch.setattr(_eda_pin, "_docker", inspect_metadata)
    # Native resolved config and original report files, before the neutral
    # transport issues its first receipt. Do not restamp real field evidence.
    resolve, chain = contract.resolve_step_config, contract.run_chain

    def resolved(*args, **kwargs):
        path = resolve(*args, **kwargs)
        cfg = json.loads(path.read_text())
        cfg.update(TRISTATE_CELLS=[], SYNTH_CHECKS_ALLOW_TRISTATE=True,
                   SYNTH_ELABORATE_ONLY=False)
        put(path, cfg)
        return path

    def native_chain(*args, **kwargs):
        folders = chain(*args, **kwargs)
        folder = folders[1]
        recipe = GOOD.replace("core", "top").replace("TIE_H Y", "lib__tieh Z").replace(
            "TIE_L Y", "lib__tiel ZN")
        (folder / "COMMANDS").write_text("yosys -p '" + "; ".join(recipe.splitlines()) + "'\n")
        for name in ("pre_synth_chk.rpt", "chk.rpt"):
            text = "Found and reported 0 problems.\n"
            if case == "phase2_failure" and name == "chk.rpt":
                text = ("Warning: Wire top.\\live is used but has no driver.\n"
                        "Found and reported 1 problems.\n")
            (folder / "reports" / name).write_text(text)
        path = folder / "vibeic_receipt.json"
        receipt = json.loads(path.read_text())
        receipt["sha256"] = {str(p.relative_to(folder)): contract.digest(p)
                             for p in folder.rglob("*") if p.is_file() and p != path}
        put(path, receipt)
        return folders

    monkeypatch.setattr(contract, "resolve_step_config", resolved)
    monkeypatch.setattr(contract, "run_chain", native_chain)
    preserved = {p: p.read_bytes() for p in (project / "phase1").rglob("*.json")}
    preserved[rtl] = rtl.read_bytes()
    history = project / "steps/history/old_attempt/outputs.json"
    put(history, {"scope": "SOURCE_CONTROL historical bytes; not a PASS claim"})
    preserved[history] = history.read_bytes()
    observed = {"case": case, "phase_calls": [], "native_calls": native_calls}
    original_phase = front._run_phase

    class WrongPhysicalOwner(Exception):
        pass

    def unexpected_synthesis(*args, **kwargs):
        observed["wrong_physical_owner"] = "step_synth"
        raise WrongPhysicalOwner("the physical window re-entered synthesis")

    def process(label, runner, argv, env=None, **kwargs):
        observed["phase_calls"].append(runner.name)
        if runner.name == "phase2_one_shot_runner.py":
            row = design.step_yosys_synth(project, "top", "", pdk_name=pdk.name)
            observed["step9"] = asdict(row)
            path = layout.report_path(project, "phase2_one_shot.json")
            put(path, {"verdict": row.status, "steps": [asdict(row)],
                       "scope": "SOURCE_CONTROL Step9 producer; neutral phase process transport",
                       "declared_window": {"entry_step": "9", "exit_step": "38"}})
            if row.status == "PASS":
                canonical = project / "phase2/stage2/synth/netlist.v"
                if case == "handoff_failure":
                    canonical.write_text(canonical.read_text() + "// changed handoff bytes\n")
            return 0 if row.status == "PASS" else 1
        if runner.name == "phase3_one_shot_runner.py":
            entry = argv[argv.index("--entry-step") + 1]
            exit_ = argv[argv.index("--exit-step") + 1]
            sites = phase3._phase3_window_sites(entry, exit_)
            observed.update(p3_entry=entry, p3_exit=exit_, p3_sites=sites,
                            p3_order=phase3._phase3_window_steps(entry, exit_))
            args = SimpleNamespace(entry_step=entry, exit_step=exit_,
                                   container="", die_um="auto", util=.4,
                                   spare_density=None, pdk=pdk.name)
            with monkeypatch.context() as edge:
                edge.setenv("VIBEIC_PHASE3_WINDOW_RUN_ID", env["VIBEIC_PHASE3_WINDOW_RUN_ID"])
                # This expensive native leaf is deliberately forbidden here:
                # Phase2 has just run the actual selected synthesis owner.
                edge.setattr(phase3, "step_synth", unexpected_synthesis)
                return phase3._run_phase3_window(project, "top", pdk, args, sites)
        if runner.name == "phase1_one_shot_runner.py":
            raise AssertionError("a synthesis resume must preserve Phase1")
        return original_phase(label, runner, argv, env=env, **kwargs)

    monkeypatch.setattr(front, "_run_phase", process)
    real_entry("IC", "default", project)
    entry, exit_ = ("31", "31") if case == "step31" else ("9", "38")
    monkeypatch.setattr(sys, "argv", [str(RUNNER), str(project), "--route", "ic",
        "--execution-mode", "default", "--entry-step", entry, "--exit-step", exit_,
        "--top-name", "top", "--ic-name", "top", "--pdk", pdk.name,
        "--container", "", "--no-dashboard", "--skip-hardware", "--skip-analog"])
    try:
        observed["frontdoor_rc"] = front.main()
    except WrongPhysicalOwner as exc:
        observed["wrong_owner_reason"] = str(exc)
    finally:
        index = versions._read_index(project)
        observed["saved_rows"] = []
        for item in index["steps"]:
            if item.get("versioned"):
                path = project / "steps" / item["folder"] / "outputs.json"
                if path.is_file():
                    record = json.loads(path.read_text())
                    observed["saved_rows"].append({"id": record["id"],
                        "status": record["status"], "canonical_pass": record.get("canonical_pass"),
                        "result": record.get("result")})
        (project / "cross_phase_observation.json").write_text(
            json.dumps(observed, indent=2) + "\n")
    assert {p: p.read_bytes() for p in preserved} == preserved
    assert "phase1_one_shot_runner.py" not in observed["phase_calls"]
    if case in ("phase2_failure", "handoff_failure", "handoff_missing_input"):
        assert "phase3_one_shot_runner.py" not in observed["phase_calls"], observed
        summary = json.loads(layout.report_path(project, "vibe_ic_one_shot.json").read_text())
        assert summary["halted_at"] == "phase2", summary
        if case == "phase2_failure":
            assert observed["step9"]["status"] == "FAIL"
        else:
            expected = "FAIL" if case == "handoff_failure" else "NOT_MEASURED"
            assert summary["step14_handoff"]["verdict"] == expected, summary
    else:
        assert observed["p3_sites"] == ["default_backend"], observed
        assert observed["p3_entry"] == ("31" if case == "step31" else "15")
        assert "wrong_physical_owner" not in observed
        if case == "current":
            step14 = next(r for r in observed["saved_rows"] if r["id"] == "14")
            assert step14["canonical_pass"] and step14["status"] == "PASS", step14
            assert len(native_calls) == 1
            assert observed["p3_order"][0] == "15" and observed["p3_order"][-1] == "38"
        else:
            assert native_calls == []
            assert observed["p3_order"] == ["31"]
        # The untouched required-input guards stop before native physical work.
        first = next(r for r in observed["saved_rows"] if r["id"] == observed["p3_entry"])
        assert first["status"] == "NOT_MEASURED" and not first["canonical_pass"]


def _entry10_retained_checkpoint(tmp_path, monkeypatch):
    """Seed the existing neutral Step9 fixture; resume readers are never stubbed.

    This is retained-artifact input data, not a new synthesis qualification.
    The source45 transport supplies the receipt-bound native grammar only.
    """
    import json
    import librelane_contract as contract
    import design_one_shot_runner as design
    from test_default_frontend_r2 import _synthesis, put
    from test_step14_report_producer_authorship import GOOD

    with monkeypatch.context() as seed:
        project, rtl, pdk, seed_calls = _synthesis(tmp_path, seed, ordinary=True)
        put(project / 'phase1/generated_docs/L5_ADI_SPEC.json', {'no_analog': True})
        resolve, chain = contract.resolve_step_config, contract.run_chain

        def resolved(*args, **kwargs):
            path = resolve(*args, **kwargs)
            cfg = json.loads(path.read_text())
            cfg.update(TRISTATE_CELLS=[], SYNTH_CHECKS_ALLOW_TRISTATE=True,
                       SYNTH_ELABORATE_ONLY=False)
            put(path, cfg)
            return path

        def native_data(*args, **kwargs):
            folders = chain(*args, **kwargs)
            folder = folders[1]
            recipe = GOOD.replace('core', 'top').replace('TIE_H Y', 'lib__tieh Z').replace(
                'TIE_L Y', 'lib__tiel ZN')
            (folder / 'COMMANDS').write_text("yosys -p '" + '; '.join(recipe.splitlines()) + "'\n")
            for name in ('pre_synth_chk.rpt', 'chk.rpt'):
                (folder / 'reports' / name).write_text('Found and reported 0 problems.\n')
            path = folder / 'vibeic_receipt.json'
            receipt = json.loads(path.read_text())
            receipt['sha256'] = {str(p.relative_to(folder)): contract.digest(p)
                                 for p in folder.rglob('*') if p.is_file() and p != path}
            put(path, receipt)
            return folders

        seed.setattr(contract, 'resolve_step_config', resolved)
        seed.setattr(contract, 'run_chain', native_data)
        import step_preflight as preflight
        row = preflight.retained_call(
            project, '9', design.step_yosys_synth, project, 'top', '',
            pdk_name=pdk.name, canonical_acceptance=True)
        assert row.status == 'PASS', row.detail
    # All seed transports have left scope before the actual entry helper reads 9.
    import step_output_collector as versions
    import synth_handoff_netlist_check as handoff
    prior = versions.latest_pass(project, '9')
    assert prior and prior['canonical_pass'] and prior['current_inputs'], prior
    assert handoff.bound_handoff(project)['verdict'] == 'PASS'
    return project, rtl, pdk


def _entry10_prelayout_transport(project, pdk, monkeypatch, case, observed):
    """Neutral native file transport; P3, LP readers and full consumers stay real."""
    import json
    import shutil
    import librelane_contract as contract
    import librelane_prelayout as prelayout
    import phase3_one_shot_runner as phase3
    from test_step8_post_prelayout import put

    root = Path(pdk.liberty).parents[4]
    monkeypatch.setattr(contract, 'pdk_root_resolution', lambda *a, **k: {'path': str(root)})
    monkeypatch.setattr(phase3, '_detect_pdk', lambda current, name: pdk if name == pdk.name else None)
    # The saved synthesis receipt keeps the same stated native image namespace.
    monkeypatch.setattr(contract, 'resolve_image', lambda *_: 'img')
    put(project / 'phase3/librelane_switch.json',
        {'steps': {'7': 'librelane', '8': 'librelane', '10': 'librelane'}})
    deck = ('create_clock -name clk -period 10 [get_ports clk]\n'
            'set_input_delay 1 -clock clk [get_ports a]\n'
            'set_output_delay 1 -clock clk [get_ports q]\n')
    put(project / 'input/constraints/clock.sdc', deck)
    libs = {}
    for process in ('ss', 'tt', 'ff'):
        lib = put(project / f'input/pdk/liberty/neutral__{process}.lib',
                  Path(pdk.liberty).read_text())
        libs[f'*_{process}_*'] = [str(lib)]
    corners = [f'{rc}_{process}_025C' for rc in ('min', 'nom', 'max')
               for process in ('ss', 'tt', 'ff')]
    population = {'STA_CORNERS': corners, 'DEFAULT_CORNER': 'nom_tt_025C', 'LIB': libs}

    def resolved(current, image, raw, output, **kwargs):
        observed['native_resolution'].append(json.loads(raw.read_text())['meta']['step'])
        return put(output, {**json.loads(raw.read_text()), **population})

    def native(current, image, steps, **kwargs):
        observed['native_prelayout'].append([step for step, _, _ in steps])
        outputs = []
        previous = None
        for index, (sid, cfg_path, state_in) in enumerate(steps, 1):
            folder = project / 'phase3/librelane/prelayout/design_sdc' / f'{index:02d}-{sid.lower().replace(".", "-")}'
            cfg = json.loads(Path(cfg_path).read_text())
            state = json.loads((previous or Path(state_in)).read_text())
            put(folder / 'config.json', cfg)
            if sid == 'OpenROAD.STAPrePNR':
                state['metrics'] = {}
                calibration = Path(prelayout.__file__).parent / 'calibration'
                for corner in (c for c in corners if c.startswith('nom_')):
                    target = folder / corner
                    target.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(calibration / 'sta_prepnr_linked_clean_negative.log', target / 'sta.log')
                    shutil.copyfile(calibration / 'sta_prepnr_check_setup_clean_negative.rpt', target / 'checks.rpt')
                    process = 'ss' if '_ss_' in corner else 'tt'
                    for sense in ('max', 'min'):
                        shutil.copyfile(calibration / f'sta_prepnr_cal_{process}_{sense}.rpt', target / f'{sense}.rpt')
                    for check, slack in (('setup', -.25 if case == '10FAIL' else 1.0), ('hold', .2)):
                        for metric, value in (('ws', slack), ('wns', min(slack, 0)), ('tns', min(slack, 0))):
                            if case != '10NOT_MEASURED':
                                state['metrics'][f'timing__{check}__{metric}__corner:{corner}'] = value
                    # Native command-unit output, before the fixture receipt.
                    put(target / 'vibeic_time_unit.rpt', 'STA_TIME_UNIT: ns\n')
            put(folder / 'state_out.json', state)
            put(folder / 'invocation.log', 'neutral native OpenROAD prelayout process output\n')
            fingerprint = {'step': sid, 'image': image, 'config': contract.digest(Path(cfg_path)),
                'state': contract.digest(previous or Path(state_in)),
                'state_files': {str(Path(state['nl'])): contract.digest(Path(state['nl']))},
                'config_files': contract.config_file_hashes(cfg, kwargs.get('mounts') or [])}
            put(folder / 'input_fingerprint.json', fingerprint)
            put(folder / 'pdk_root.json', {'cli_pdk_root': kwargs['pdk_root'],
                'mounts_under_it': [[str(host), guest] for host, guest in kwargs.get('mounts', [])],
                'stated_by': 'run_chain(pdk_root=...)'})
            put(folder / 'vibeic_receipt.json', {'input': fingerprint,
                'sha256': {str(p.relative_to(folder)): contract.digest(p)
                           for p in folder.rglob('*') if p.is_file()}})
            previous = folder / 'state_out.json'
            outputs.append(folder)
        return outputs

    monkeypatch.setattr(prelayout, 'resolve_step_config', resolved)
    monkeypatch.setattr(prelayout, 'run_chain', native)


@pytest.mark.parametrize('case', ['current', 'exit10', 'missing9', '10FAIL',
                                  '10NOT_MEASURED', '14FAIL', '14NOT_MEASURED', 'phase2FAIL'])
def test_default_entry10_resumes_current_checkpoint(
        tmp_path, monkeypatch, cross_phase_transport, case):
    """Retained9 reader + real Step10/full consumer + routing/real14 boundary.

    Phase2 process output represents routing only: it proves entry11/exit38,
    never qualification of unchanged DFT/LEC11--13. Native STA file writes are
    neutral calibration data, not an actual physical or timing run.
    """
    import json
    import os
    from types import SimpleNamespace
    import _eda_pin
    import _path_layout as layout
    import phase3_one_shot_runner as phase3
    import step_output_collector as versions
    import vibe_ic_one_shot_runner as front
    from test_default_frontend_r2 import put
    from test_execution_receipt_chain import real_entry

    project, rtl, pdk = _entry10_retained_checkpoint(tmp_path, monkeypatch)
    observed = {'case': case, 'phase_calls': [], 'native_resolution': [],
                'native_prelayout': [], 'phase2_scope': 'ROUTING_ONLY_NOT_DFT_LEC_QUALIFICATION'}
    _entry10_prelayout_transport(project, pdk, monkeypatch, case, observed)
    stage_owner_route(project, 'ic')
    history = put(project / 'steps/history/retained_entry10/outputs.json',
                  {'scope': 'neutral retained input history; no current PASS claim'})
    preserved = {path: path.read_bytes() for path in (project / 'phase1').rglob('*.json')}
    preserved.update({rtl: rtl.read_bytes(), history: history.read_bytes()})
    saved9 = versions.latest_pass(project, '9')
    receipt9 = project / saved9['folder'] / 'outputs.json'
    if not receipt9.is_file():
        receipt9 = project / 'steps' / saved9['folder'] / 'outputs.json'
    original9 = receipt9.read_bytes()
    if case == 'missing9':
        (project / 'phase2/stage2/synth/netlist.v').unlink()
    authority = json.loads(Path(os.environ['VIBEIC_LIBRELANE_LOCAL_ATTESTATION']).read_text())
    metadata = _eda_pin._docker
    def image_metadata(*argv, **kwargs):
        if argv[:3] == ('inspect', '--format', '{{.Image}}\t{{.Config.Image}}'):
            item = authority['container_inspect']
            return 0, item['Image'] + '\t' + item['Config']['Image'] + '\n', ''
        return metadata(*argv, **kwargs)
    monkeypatch.setattr(_eda_pin, '_docker', image_metadata)
    original_phase = front._run_phase
    def process(label, runner, argv, env=None, **kwargs):
        observed['phase_calls'].append(runner.name)
        if runner.name == 'phase2_one_shot_runner.py':
            observed['phase2_argv'] = list(argv)
            assert argv[argv.index('--entry-step') + 1] == '11'
            assert argv[argv.index('--exit-step') + 1] == '38'
            if case == '14FAIL':
                nl = project / 'phase2/stage2/synth/netlist.v'
                nl.write_text(nl.read_text() + '// changed retained handoff\n')
            elif case == '14NOT_MEASURED':
                (project / 'phase1/generated_docs/L5_ADI_SPEC.json').unlink()
            status = 'FAIL' if case == 'phase2FAIL' else 'PASS'
            put(layout.report_path(project, 'phase2_one_shot.json'), {
                'verdict': status, 'steps': [],
                'scope': 'SOURCE_CONTROL routing-only phase process; no DFT/LEC tool qualification',
                'declared_window': {'entry_step': '11', 'exit_step': '38'}})
            return 1 if status == 'FAIL' else 0
        if runner.name == 'phase3_one_shot_runner.py':
            entry = argv[argv.index('--entry-step') + 1]
            exit_ = argv[argv.index('--exit-step') + 1]
            sites = phase3._phase3_window_sites(entry, exit_)
            observed.update(p3_entry=entry, p3_exit=exit_, p3_sites=sites)
            args = SimpleNamespace(entry_step=entry, exit_step=exit_, container='',
                die_um='auto', util=.4, spare_density=None, pdk=pdk.name)
            with monkeypatch.context() as edge:
                edge.setenv('VIBEIC_PHASE3_WINDOW_RUN_ID', env['VIBEIC_PHASE3_WINDOW_RUN_ID'])
                return phase3._run_phase3_window(project, 'top', pdk, args, sites)
        if runner.name == 'phase1_one_shot_runner.py':
            raise AssertionError('Step10 resume dispatched Phase1')
        return original_phase(label, runner, argv, env=env, **kwargs)
    monkeypatch.setattr(front, '_run_phase', process)
    real_entry('IC', 'default', project)
    exit_ = '10' if case == 'exit10' else '38'
    monkeypatch.setattr(sys, 'argv', [str(RUNNER), str(project), '--route', 'ic',
        '--execution-mode', 'default', '--entry-step', '10', '--exit-step', exit_,
        '--top-name', 'top', '--ic-name', 'top', '--pdk', pdk.name, '--container', '',
        '--no-dashboard', '--skip-hardware', '--skip-analog'])
    try:
        observed['frontdoor_rc'] = front.main()
    finally:
        observed['rows'] = []
        for item in versions._read_index(project)['steps']:
            if item.get('versioned'):
                path = project / 'steps' / item['folder'] / 'outputs.json'
                if path.is_file():
                    row = json.loads(path.read_text())
                    observed['rows'].append({key: row.get(key) for key in
                        ('id', 'status', 'canonical_pass', 'result')})
        summary_path = layout.report_path(project, 'vibe_ic_one_shot.json')
        observed['summary'] = json.loads(summary_path.read_text()) if summary_path.is_file() else None
        (project / 'ENTRY10_OBSERVATION.json').write_text(json.dumps(observed, indent=2) + '\n')
    assert receipt9.read_bytes() == original9
    for path, data in preserved.items():
        if case == '14NOT_MEASURED' and path.name == 'L5_ADI_SPEC.json':
            continue
        assert path.read_bytes() == data, path
    assert 'phase1_one_shot_runner.py' not in observed['phase_calls']
    summary = observed['summary']
    assert summary is not None, observed
    if case == 'missing9':
        assert not observed['native_prelayout'] and not observed['phase_calls'], observed
        assert summary['step10_prelayout']['verdict'] == 'NOT_MEASURED', observed
    else:
        step10 = next(row for row in observed['rows'] if str(row['id']) == '10')
        expected = 'FAIL' if case == '10FAIL' else 'NOT_MEASURED' if case == '10NOT_MEASURED' else 'PASS'
        assert step10['status'] == expected, observed
        assert bool(step10['canonical_pass']) is (expected == 'PASS'), observed
        if case in ('10FAIL', '10NOT_MEASURED', 'exit10'):
            assert not observed['phase_calls'], observed
            assert observed['frontdoor_rc'] == (0 if case == 'exit10' else 1 if case == '10FAIL' else 2)
        elif case in ('14FAIL', '14NOT_MEASURED', 'phase2FAIL'):
            assert observed['phase_calls'] == ['phase2_one_shot_runner.py'], observed
            assert summary['halted_at'] == 'phase2', observed
            if case != 'phase2FAIL':
                assert summary['step14_handoff']['verdict'] == ('FAIL' if case == '14FAIL' else 'NOT_MEASURED'), observed
        else:
            step14 = next(row for row in observed['rows'] if str(row['id']) == '14')
            assert step14['status'] == 'PASS' and step14['canonical_pass'], observed
            assert observed['p3_entry'] == '15' and observed['p3_sites'] == ['default_backend'], observed
