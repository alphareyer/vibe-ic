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
                elif case == "handoff_missing_input":
                    canonical.unlink()
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
                        "status": record["status"], "canonical_pass": record["canonical_pass"],
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
