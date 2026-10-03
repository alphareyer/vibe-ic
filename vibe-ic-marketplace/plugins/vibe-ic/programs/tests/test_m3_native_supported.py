"""Native model component controls; never certify physical/whole-design PASS."""
import json
import shutil
import sys
from pathlib import Path

import pytest

import mixed_signal_m3_run as m3
import mixed_signal_signoff_run as m4
import mixed_signal_cosim_check as cosim_gate
import mixed_signal_interface_si_check as si_gate
from programs.tests.test_execution_receipt_chain import isolated_transport, real_entry

FIXTURE = Path(__file__).parent / "fixtures/m3_native"


def read(path):
    return json.loads(path.read_text())


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n")


@pytest.fixture
def project(tmp_path):
    dst = tmp_path / "component"
    shutil.copytree(FIXTURE, dst)
    return dst


@pytest.fixture
def measured(project):
    # All positive bytes are produced by real tools, never hand-authored PASS.
    assert m3.main([str(project), "--top", "bridge_top", "--container", "host"]) == 2
    assert m3.audit(project, "cosim")["passed"] is True
    return project


def rebind_outputs(project, receipt):
    # Hostile rehashes defeat checksum-only gates. Native derivation still wins.
    receipt["outputs"] = {rel: m3.digest(project / rel) for rel in receipt["outputs"] if (project / rel).is_file()}
    write(project / m3.RECEIPT, receipt)


def test_ordinary_producer_runs_native_component_cosim(measured):
    evidence = read(measured / m3.COSIM)
    assert evidence["verdict"] == "PASS"
    scenario = evidence["scenarios"][0]
    assert scenario["sample_count"] >= 3
    assert scenario["criteria"][0]["measured"] >= 0.99
    assert scenario["metrics"]["drive_transitions"] == 1
    receipt = read(measured / m3.RECEIPT)
    assert len(receipt["execution"]) == 8
    assert set(receipt["tool_versions"]) == {"ngspice", "iverilog", "vvp"}
    assert 0 < receipt["execution"][5]["peak_rss_kib"] < 8 * 1024**2
    assert cosim_gate.main([str(measured), "--require-current-production"]) == 0
    assert si_gate.main([str(measured), "--require-current-production"]) == 1
    assert read(measured / m3.SI)["coverage"]["noise"] == "NOT_MEASURED"
    assert m3.audit(measured, "cosim")["full_design_verified"] is False
    assert m4.audit(measured)["passed"] is False
    assert not (measured / m4.OUTPUT).exists()  # auditor cannot produce signoff
    assert m4.main([str(measured), "--top", "bridge_top"]) == 2
    signoff = read(measured / m4.OUTPUT)
    assert signoff["ready_for_tapeout"] is False
    checks = {c["step"]: c["verdict"] for c in signoff["checks"]}
    assert checks["M3_cosim"] == "PASS"
    assert checks["M3_si"] == "NOT_MEASURED"
    assert checks["PV_drc"] == "NOT_MEASURED"


@pytest.mark.parametrize("damage,rule", [
    ("missing_execution", "MISSING_EXECUTION"),
    ("syntax_only", "MISSING_EXECUTION"),
    ("wrong_compile_source", "MISSING_EXECUTION"),
    ("wrong_design", "WRONG_DESIGN"),
    ("wrong_path", "WRONG_OUTPUT_PATH"),
    ("changed_model", "STALE_INPUT"),
    ("changed_wrapper", "STALE_INPUT"),
    ("changed_model_rehashed", "STALE_INPUT"),
    ("empty_scenarios", "CONTRADICTS_NATIVE_OUTPUT"),
    ("empty_plan", "STALE_INPUT"),
    ("version_assertion", "MISSING_TOOL_VERSION"),
    ("replayed_log", "STALE_NATIVE_OUTPUT"),
    ("replayed_wave", "STALE_NATIVE_OUTPUT"),
    ("wrong_run_token", "MISSING_EXECUTION"),
    ("unordered_wave", "FAILED_MEASUREMENT"),
    ("fabricated_measurement", "CONTRADICTS_NATIVE_OUTPUT"),
    ("absent_wave", "STALE_NATIVE_OUTPUT"),
])
def test_native_reverse_mutations_rejected(measured, damage, rule):
    import os
    receipt = read(measured / m3.RECEIPT)
    root = measured / receipt["native_root"]
    work = root / "enable_receiver"
    if damage == "missing_execution":
        receipt["execution"] = []
    elif damage == "syntax_only":
        receipt["execution"] = [e for e in receipt["execution"] if e["stage"] != "measurement"]
    elif damage == "wrong_compile_source":
        receipt["execution"][3]["command"][-2] = str(measured / "other.v")
    elif damage == "wrong_design":
        result = read(measured / m3.COSIM); result["top"] = "another_design"
        write(measured / m3.COSIM, result)
    elif damage == "wrong_path":
        receipt["native_root"] = m3.DIR + "/native/foreign"
    elif damage.startswith("changed_model"):
        model = measured / "input/mixed_signal/receiver.sp"
        model.write_text(model.read_text().replace("1000", "2000"))
        if damage.endswith("rehashed"):
            receipt["inputs"] = m3.inputs(measured, "bridge_top")
    elif damage == "changed_wrapper":
        netlist = measured / "phase3/stage3/pnr/bridge_top_pnr.v"
        netlist.write_text(netlist.read_text().replace("accepted <= sense", "accepted <= 0"))
    elif damage == "empty_scenarios":
        result = read(measured / m3.COSIM); result["scenarios"] = []
        write(measured / m3.COSIM, result)
    elif damage == "empty_plan":
        plan = measured / "phase1/generated_docs/L22_VERIFICATION_PLAN.json"
        data = read(plan); data["fields"]["verification_plan"]["cosim_scenarios"] = []
        write(plan, data)
    elif damage == "version_assertion":
        receipt["tool_versions"]["ngspice"] = "invented version"
    elif damage in ("replayed_log", "replayed_wave"):
        path = work / ("drive.log" if damage == "replayed_log" else "wave.txt")
        os.utime(path, ns=(1, 1))
    elif damage == "wrong_run_token":
        path = work / "drive.log"
        stamp = path.stat().st_mtime_ns
        path.write_text(path.read_text().replace(receipt["run_id"], "f" * 32))
        os.utime(path, ns=(stamp, stamp))
    elif damage == "unordered_wave":
        path = work / "wave.txt"
        stamp = path.stat().st_mtime_ns
        lines = path.read_text().splitlines(); lines[2], lines[3] = lines[3], lines[2]
        path.write_text("\n".join(lines) + "\n")
        os.utime(path, ns=(stamp, stamp))
    elif damage == "fabricated_measurement":
        result = read(measured / m3.COSIM)
        result["scenarios"][0]["metrics"]["settled_voltage_min_v"] = 42
        write(measured / m3.COSIM, result)
    elif damage == "absent_wave":
        (work / "wave.txt").unlink()
    rebind_outputs(measured, receipt)
    rejected = m3.audit(measured, "cosim")
    assert rejected["passed"] is False
    assert rejected["findings"][0]["rule"] == rule, rejected
    assert cosim_gate.main([str(measured), "--require-current-production"]) == 1


@pytest.mark.parametrize("damage", ["wrong_response", "slow_receiver", "failed_spice"])
def test_actual_failed_measurement_propagates_to_m4(project, damage):
    if damage == "wrong_response":
        path = project / "phase3/stage3/pnr/bridge_top_pnr.v"
        path.write_text(path.read_text().replace("accepted <= sense", "accepted <= 0"))
    else:
        path = project / "input/mixed_signal/receiver.sp"
        if damage == "slow_receiver":
            path.write_text(path.read_text().replace("10p", "1u"))
        else:
            path.write_text(path.read_text().replace("Rfunctional din sense 1000", "Rfunctional din sense nonexistent_parameter"))
    assert m3.main([str(project), "--top", "bridge_top", "--container", "host"]) == 1
    result = read(project / m3.COSIM)
    assert result["verdict"] == "FAIL", result
    assert m3.audit(project, "cosim")["verdict"] == "FAIL"
    assert cosim_gate.main([str(project), "--require-current-production"]) == 1
    assert m4.main([str(project), "--top", "bridge_top"]) == 1
    derived = read(project / m4.OUTPUT)
    assert derived["ready_for_tapeout"] is False
    assert derived["verdict"] == "FAIL"
    assert next(c for c in derived["checks"] if c["step"] == "M3_cosim")["verdict"] == "FAIL"
    assert m4.audit(project)["verdict"] == "FAIL"


def test_fixed_runner_uses_real_native_producer_and_both_gates(project, monkeypatch):
    import vibe_ic_one_shot_runner as runner
    real_entry('IC', 'default', project)
    analog = project / "phase3/analog/analog_block_list.json"
    analog.parent.mkdir(parents=True)
    shutil.copyfile(project / "phase1/analog/analog_block_list.json", analog)
    calls = []
    native = {"mixed_signal_m3_run": m3, "mixed_signal_cosim_check": cosim_gate,
              "mixed_signal_interface_si_check": si_gate}
    def bounded(label, program, args, env=None):
        name = Path(program).stem
        calls.append(name)
        if name == "mixed_signal_power_domain_run":
            # Caller-only setup outside M3: M2 is owned/reviewed independently.
            write(project / m3.DIR / "power_domain_producer_audit.json", {"program": name, "verdict": "PASS"})
        if name in native:
            args = list(args)
            if "--container" in args:
                args[args.index("--container") + 1] = "host"
            return native[name].main(args)
        return 0
    monkeypatch.setattr(runner, "_run_phase", bounded)
    monkeypatch.setattr(sys, "argv", ["runner", str(project), "--top", "bridge_top",
                                     "--route", "ic", "--skip-phase1", "--no-dashboard"])
    runner.main()
    report = read(project / "reports/orchestrator/vibe_ic_one_shot.json")
    phases = {row["name"]: row for row in report["phases"]}
    assert phases["mixed_signal_M3"]["verdict"] == "NOT_READY"
    assert phases["mixed_signal_M4"]["verdict"] == "NOT_READY"
    assert calls.count("mixed_signal_m3_run") == 1
    assert calls.count("mixed_signal_cosim_check") == 1
    assert calls.count("mixed_signal_interface_si_check") == 1
    assert "mixed_signal_signoff_run" not in calls
    assert read(project / m3.COSIM)["verdict"] == "PASS"
    assert read(project / m3.SI)["verdict"] == "NOT_MEASURED"


@pytest.mark.parametrize("kind", ["cosim", "si"])
def test_native_audit_cannot_overwrite_execution_artifacts(measured, kind):
    receipt = read(measured / m3.RECEIPT)
    wave = measured / receipt["native_root"] / "enable_receiver/wave.txt"
    before = wave.read_bytes()
    gate = cosim_gate if kind == "cosim" else si_gate
    assert gate.main([str(measured), "--require-current-production", "--json", str(wave)]) == 1
    assert wave.read_bytes() == before


@pytest.mark.parametrize("damage", ["missing_spef", "missing_liberty", "truncated_spef"])
def test_si_incomplete_current_views_remain_unmeasured(project, damage):
    contract_path = project / "input/mixed_signal/native.json"
    data = read(contract_path)
    data["si"] = {"netlist": data["digital"]["netlist"], "sdc": "input/mixed_signal/timing.sdc",
                  "spef": "input/mixed_signal/current.spef", "liberties": ["input/mixed_signal/current.lib"],
                  "vdd_v": 1, "noise_margin_mv": 100,
                  "interfaces": [{"pin": "sense", "criteria": [{"metric": "slew_rise_max", "max": 1}]}]}
    (project / data["si"]["sdc"]).write_text("create_clock -period 20 [get_ports clk]\n")
    # A real committed extraction EXCERPT has no D_NET records. It cannot be
    # substituted for a complete current post-route parasitic/timing view.
    excerpt = FIXTURE.parent / "librelane_import/8HD-4/runs/cmp3/54-openroad-rcx/nom/spm.nom.spef"
    if damage != "missing_spef":
        text = excerpt.read_text().replace('*DESIGN "spm"', '*DESIGN "bridge_top"')
        (project / data["si"]["spef"]).write_text(text)
    if damage != "missing_liberty":
        # Deliberately invalid library; never creates a positive STA fixture.
        (project / data["si"]["liberties"][0]).write_text("missing qualified Liberty timing\n")
    write(contract_path, data)
    assert m3.main([str(project), "--top", "bridge_top", "--container", "host"]) == 2
    assert m3.audit(project, "cosim")["passed"] is True
    si = read(project / m3.SI)
    assert si["verdict"] == "NOT_MEASURED"
    assert si["coverage"]["timing"] == "NOT_MEASURED"
    assert si_gate.main([str(project), "--require-current-production"]) == 1
    if damage == "truncated_spef":
        assert "complete extracted D_NET" in si["blockers"][0]


def test_replayed_other_project_receipt_cannot_qualify(measured, tmp_path):
    copy = tmp_path / "foreign_copy"
    shutil.copytree(measured, copy)
    assert m3.audit(copy, "cosim")["findings"][0]["rule"] == "WRONG_PROJECT"
    assert cosim_gate.main([str(copy), "--require-current-production"]) == 1


def test_driver_changed_by_measured_feedback_requires_coupled_solver(project):
    path = project / "phase3/stage3/pnr/bridge_top_pnr.v"
    path.write_text(path.read_text().replace("enable <= 1", "enable <= !sense"))
    assert m3.main([str(project), "--top", "bridge_top", "--container", "host"]) == 2
    result = read(project / m3.COSIM)
    assert result["verdict"] == "NOT_MEASURED"
    assert result["scenarios"][0]["rule"] == "FEEDBACK_ALTERS_DRIVE"
    assert cosim_gate.main([str(project), "--require-current-production"]) == 1


@pytest.mark.parametrize("step,state", [("M1", "FAIL"), ("M1", "NOT_MEASURED"),
                                       ("M2", "FAIL"), ("M2", "NOT_MEASURED")])
def test_m4_retains_required_upstream_blocked_verdict(project, step, state):
    # Negative evidence metadata only: no fabricated positive qualification.
    if step == "M1":
        write(project / m3.DIR / "merge.json", {"verdict": state})
        write(project / m3.DIR / "top_lvs.json", {"verdict": state})
    else:
        write(project / m3.DIR / "power_domain_run.json", {"verdict": state})
    assert m4.main([str(project), "--top", "bridge_top"]) == (1 if state == "FAIL" else 2)
    result = read(project / m4.OUTPUT)
    assert next(c for c in result["checks"] if c["step"] == step)["verdict"] == state
    assert result["ready_for_tapeout"] is False
    assert m4.audit(project)["passed"] is False
