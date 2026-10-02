"""Neutral checked-in routed artefacts, a real Yosys run and fixed callers.

Run in the EDA environment (Yosys is required, never skipped). Only unrelated
Phase 1/2/3 and M1 work is stubbed in the bounded orchestrator test. M2 and its
gate run unchanged. The fixture is synthetic and proves structural wiring;
it is not a PDK or a silicon signoff.
"""
import json
import os
import shutil
import sys
from pathlib import Path

import pytest
import yaml

import power_domain_crossing_check as crossing
import vibe_ic_one_shot_runner as runner

PROGRAMS = Path(__file__).resolve().parent.parent
FIXTURE = PROGRAMS / "tests/fixtures/m2_placed"
FLOW = PROGRAMS.parent / "flow/phase1_phase2_phase3.yaml"
REPORTS = "reports/analog/mixed_signal"


def fixture(tmp_path):
    project = tmp_path / "project"
    shutil.copytree(FIXTURE, project)
    # Analog's fixed runner stages its execution declaration here; Phase 1's
    # declaration above is the canonical mixed-signal applicability input.
    analog = project / "phase3/analog/analog_block_list.json"
    analog.parent.mkdir(parents=True)
    shutil.copyfile(project / "phase1/analog/analog_block_list.json", analog)
    return project


def invoke(project, *args):
    import mixed_signal_power_domain_run as producer
    return producer.main([str(project), *args])


def test_fixed_runner_reaches_producer_and_existing_gate(tmp_path, monkeypatch):
    project = fixture(tmp_path)
    from _route_fixture import stage_owner_route
    stage_owner_route(project, "ic")
    rtl = project / "phase2/stage1/rtl/boundary.v"
    rtl.parent.mkdir(parents=True)
    rtl.write_text("module boundary(); endmodule\n")
    calls = []

    def bounded(label, program, args, env=None):
        calls.append(Path(program).stem)
        if Path(program).stem == "mixed_signal_power_domain_run":
            return invoke(project, "--top", "boundary", "--container", "host",
                          "--json", args[args.index("--json") + 1])
        return 0

    monkeypatch.setattr(runner, "_run_phase", bounded)
    monkeypatch.setattr(sys, "argv", ["runner", str(project), "--top", "boundary",
                                      "--route", "ic", "--skip-phase1", "--no-dashboard"])
    runner.main()
    # This existing gate returns rc=2 on the unwired base, and rc=0 after
    # the real fixed caller produces the routed evidence. No new-symbol red.
    assert crossing.main([str(project)]) == 0
    assert calls.count("mixed_signal_power_domain_run") == 1
    assert invoke(project, "--check-only") == 0
    rep = json.loads((project / "reports/orchestrator/vibe_ic_one_shot.json").read_text())
    assert next(p for p in rep["phases"] if p["name"] == "mixed_signal_M2")["verdict"] == "PASS"


def test_native_positive_and_canonical_gate(tmp_path):
    project = fixture(tmp_path)
    assert invoke(project) == 0
    pd = json.loads((project / REPORTS / "power_domain.json").read_text())
    assert len(pd["crossings"]) == 1
    assert pd["examined_cells"] == 4
    assert pd["crossings"][0]["path_cells"] == ["u_shift", "u_iso"]
    assert invoke(project, "--check-only") == 0
    import flow_compliance_check as flow
    step = next(s for s in yaml.safe_load(FLOW.read_text())["steps"] if s["id"] == "M2")
    assert flow.check_step(project, step, {}).status == "PASS"
    # This is a real blocking caller, not an assertion about YAML spelling.
    netlist = project / "phase3/stage3/pnr/boundary_pnr.v"
    netlist.write_text(netlist.read_text().replace(".A(raw)", ".A(1'b0)"))
    assert flow.check_step(project, step, {}).status == "FAIL"


@pytest.mark.parametrize("damage", ["missing", "malformed", "stale", "bypass", "unplaced", "wrong_type", "unknown_voltage", "unsupported_upf"])
def test_native_bad_inputs_withdraw_previous_admission(tmp_path, damage):
    project = fixture(tmp_path)
    assert invoke(project) == 0
    netlist = project / "phase3/stage3/pnr/boundary_pnr.v"
    upf = project / "phase2/stage2/constraints/boundary.upf"
    if damage == "missing":
        netlist.unlink()
    elif damage == "malformed":
        netlist.write_text("this is not Verilog")
    elif damage == "stale":
        netlist.write_text(netlist.read_text() + "\n// changed\n")
        assert invoke(project, "--check-only") == 1
        return
    elif damage == "bypass":
        netlist.write_text(netlist.read_text().replace(".A(isolated)", ".A(raw)"))
    elif damage == "unplaced":
        p = project / "phase3/stage3/pnr/routed.def"
        p.write_text(p.read_text().replace("+ PLACED ( 2000 1000 ) N", "+ UNPLACED"))
    elif damage == "wrong_type":
        p = project / "input/pdk/liberty/neutral.lib"
        p.write_text(p.read_text().replace("level_shifter_type : HL", "level_shifter_type : LH"))
    elif damage == "unknown_voltage":
        upf.write_text(upf.read_text().replace(" -voltage 1.8", ""))
    else:
        upf.write_text(upf.read_text() + "source other.upf\n")
    assert invoke(project) in (1, 2)
    assert invoke(project, "--check-only") == 1
    assert not (project / REPORTS / "power_domain_run.json").exists()


@pytest.mark.parametrize("damage", ["missing", "malformed", "stale", "no_write", "malformed_tool", "stale_tool", "input_changed"])
def test_tool_and_sidecar_outputs_fail_closed(tmp_path, monkeypatch, damage):
    import mixed_signal_power_domain_run as producer
    project = fixture(tmp_path)
    assert invoke(project) == 0
    prior_native = (project / REPORTS / "power_domain_tool/netlist.json").read_bytes()
    if damage in ("missing", "malformed", "stale"):
        p = project / REPORTS / "level_shifter.json"
        if damage == "missing":
            p.unlink()
        elif damage == "malformed":
            p.write_text("{broken")
        else:
            p.write_text(p.read_text() + "\n")
        assert invoke(project, "--check-only") == 1
    else:
        def fake(script, work, container):
            (work / "yosys.log").write_text("fake success without valid evidence")
            if damage == "malformed_tool":
                (work / "netlist.json").write_text("{broken")
            elif damage == "stale_tool":
                p = work / "netlist.json"
                p.write_bytes(prior_native)
                os.utime(p, (1, 1))
            elif damage == "input_changed":
                p = project / "phase3/stage3/pnr/boundary_pnr.v"
                p.write_text(p.read_text() + "// concurrent edit\n")
            return 0
        monkeypatch.setattr(producer, "run_tool", fake)
        assert invoke(project) == 1
        assert invoke(project, "--check-only") == 1


def test_digital_only_does_not_invoke_m2(tmp_path, monkeypatch):
    project = fixture(tmp_path)
    (project / "phase1/analog/analog_block_list.json").unlink()
    (project / "phase3/analog/analog_block_list.json").unlink()
    from _route_fixture import stage_owner_route
    stage_owner_route(project, "ic")
    calls = []
    monkeypatch.setattr(runner, "_run_phase", lambda label, p, args, env=None: calls.append(Path(p).stem) or 0)
    monkeypatch.setattr(sys, "argv", ["runner", str(project), "--route", "ic", "--skip-phase1", "--no-dashboard"])
    runner.main()
    assert "mixed_signal_power_domain_run" not in calls


@pytest.mark.parametrize("fault", ["m1_failed", "missing_report", "malformed_report", "stale_report"])
def test_fixed_runner_never_admits_absent_measurement(tmp_path, monkeypatch, fault):
    project = fixture(tmp_path)
    from _route_fixture import stage_owner_route
    stage_owner_route(project, "ic")
    calls = []
    if fault == "stale_report":
        p = project / REPORTS / "power_domain_producer_audit.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"program": "mixed_signal_power_domain_run", "verdict": "PASS"}))
    def bounded(label, program, args, env=None):
        name = Path(program).stem
        calls.append(name)
        if name == "mixed_signal_top_lvs_run" and fault == "m1_failed":
            return 1
        if name == "mixed_signal_power_domain_run" and fault == "malformed_report":
            p = Path(args[args.index("--json") + 1])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("{broken")
        return 0
    monkeypatch.setattr(runner, "_run_phase", bounded)
    monkeypatch.setattr(sys, "argv", ["runner", str(project), "--top", "boundary",
                                     "--route", "ic", "--skip-phase1", "--no-dashboard"])
    runner.main()
    rep = json.loads((project / "reports/orchestrator/vibe_ic_one_shot.json").read_text())
    row = next(p for p in rep["phases"] if p["name"] == "mixed_signal_M2")
    assert row["verdict"] == ("NOT_READY" if fault == "m1_failed" else "FAIL")
    assert row["rc"] != 0
    assert calls.count("mixed_signal_power_domain_run") == (0 if fault == "m1_failed" else 1)
