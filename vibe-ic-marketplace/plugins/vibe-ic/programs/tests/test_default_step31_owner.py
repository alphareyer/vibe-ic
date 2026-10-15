"""Actual bounded Default31 and consumers, with neutral native transports.

The invented transcripts exercise source ownership and ordering only. They
are not a native physical-verification result or an IC PASS claim.
"""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import execution_backend_producers as B
import execution_backend_worker as W
import execution_policy as P
import phase3_one_shot_runner as R
import step_output_collector as V
import flow_declared_producer_run as D
from test_backend_input_contract_sdc import _project
from test_default_physical_composed import _step31_control
from test_lvs_report_check import _PRIMARY_MATCH
from test_drc_vacuous_pass_check import _write_gds


def put(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


LAYOUT = """VERSION 5.8 ;
DESIGN chip_top ;
UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( 0 0 ) ( 100000 100000 ) ;
COMPONENTS 1 ;
- u logic + PLACED ( 2000 2000 ) N ;
END COMPONENTS
PINS 2 ;
- a + NET signal + DIRECTION INPUT + USE SIGNAL ;
- b + NET signal + DIRECTION OUTPUT + USE SIGNAL ;
END PINS
NETS 2 ;
- n ( u Y ) ;
- signal ( PIN a ) ( PIN b ) + ROUTED met1 ( 1000 1000 ) ( 9000 1000 ) ;
END NETS
SPECIALNETS 2 ;
- PWR ( * VPWR ) + USE POWER + ROUTED met1 1000 ( 0 0 ) ( 10000 0 ) ;
- GND ( * VGND ) + USE GROUND + ROUTED met1 1000 ( 0 1000 ) ( 10000 1000 ) ;
END SPECIALNETS
END DESIGN
"""



def post_stream_transport(project, pdk, monkeypatch, calls):
    """Real plan/capture/census/judge; only tool and synthetic-PDK I/O is neutral.

    LOCAL identity is the launcher's actual current-CID proof. No fixture CID,
    image digest, plan identity, census classification or verdict is supplied.
    """
    import contextlib
    import io
    import subprocess
    import _eda_image
    import drv_capture_plan as planner
    import drv_run_identity
    import drv_signoff_capture as capture
    import drv_signoff_judge as drv
    import flow_compliance_check as fcc
    import librelane_contract as ll
    from test_drv_signoff_judge import _report, _stage_receipts, _install_test_freeze

    image = os.environ["VIBEIC_EDA_IMAGE"]
    image_id = ll.local_image_attestation(image)["image_id"]
    root = project / "input/pdk"
    lib = put(root, "open_fixture/libs.ref/neutral/lib/std_typ.lib", '''library(std) {
 time_unit : "1ns"; capacitive_load_unit (1,pf);
 nom_process : 1; nom_voltage : 5; nom_temperature : 25;
 default_max_transition : 20; default_max_capacitance : 20;
 cell (logic) { pin (Y) { direction : output;
 max_transition : 20; max_capacitance : 20; } }
}''')
    pdk.liberty = str(lib)
    put(root, "open_fixture/libs.ref/neutral/lef/std.lef",
        "MACRO logic\n CLASS CORE ;\n PIN Y\n DIRECTION OUTPUT ;\n USE SIGNAL ;\n END Y\nEND logic\n")
    config = put(root, "open_fixture/libs.tech/librelane/neutral/config.tcl",
        "set ::env(MAX_FANOUT_CONSTRAINT) 4\n"
        "set ::env(MAX_TRANSITION_CONSTRAINT) 3\n"
        "set ::env(MAX_CAPACITANCE_CONSTRAINT) 0.2\n")
    put(project, "input/docs/L7_design.md", "neutral source-control verification plan\n")
    put(project, "input/docs/L9_constraints_floorplan.md", "| SYNTH_MAX_FANOUT | 4 |\n")
    sdc = put(project, "phase3/stage3/sta/signoff.sdc",
        "create_clock -name clk -period 24 [get_ports a]\n"
        "set_input_delay 4.8 -clock clk [all_inputs]\n"
        "set_output_delay 4.8 -clock clk [all_outputs]\n"
        "set_max_fanout 4 [current_design]\n"
        "set_max_transition 3 [current_design]\n"
        "set_max_capacitance 0.2 [current_design]\n")
    profile = put(project, "input/neutral_drv_scenes.json", json.dumps({
        "open_fixture": {"pvt": {"typ": {"nom_voltage": 5,
         "nom_temperature": 25}}, "rc_corners": ["nom", "max"]}}))
    monkeypatch.setattr(planner, "_SCENE_PROFILES", profile)
    monkeypatch.setattr(drv, "_SCENE_PROFILES", profile)
    monkeypatch.setattr(_eda_image, "resolve", lambda: image)
    monkeypatch.setattr(ll, "resolve_image", lambda *a, **k: image)
    monkeypatch.setattr(ll, "resolve_pdk_root", lambda *a, **k: str(root))
    put(project, "phase3/librelane_pdk_root.provenance.json", json.dumps({
        "path": str(root), "derivation": {"image_id": image_id}}))
    refs = {}
    for kind, rc in (("setup", "nom"), ("hold", "max")):
        spef = put(project, f"phase3/stage3/rcx/{rc}/top.{rc}.spef", f"*SPEF neutral {rc}\n")
        refs[f"{rc}/top.{rc}.spef"] = sha(spef)
        put(project, f"phase3/stage3/sta/sta_mcorner_ocv_{kind}.tcl",
            f"read_liberty {lib}\n"
            f"read_verilog {project / 'phase3/stage3/pnr/chip_top_pnr.v'}\n"
            "link_design chip_top\n"
            f"read_sdc {sdc}\n"
            "set_timing_derate -early 0.95\nset_timing_derate -late 1.05\n"
            f"read_spef {spef}\n")
    put(project, "phase3/stage3/rcx/COMMANDS", "SOURCE_CONTROL RCX transport\n")
    put(project, "phase3/stage3/rcx/vibeic_receipt.json", json.dumps({
        "input": {"step": "OpenROAD.RCX", "image": image,
        "state_files": {str(project / "phase3/stage3/pnr/routed.def"):
                        sha(project / "phase3/stage3/pnr/routed.def")}}, "sha256": refs}))
    drv_run_identity.record(project)
    _stage_receipts(project)
    plan = planner.build(project)
    _install_test_freeze(plan, project, monkeypatch)
    anchor = {"path": str(config), "sha256": sha(config),
              "pdk_commit": "SOURCE_CONTROL_synthetic_PDK"}
    monkeypatch.setattr(capture, "image_pdk_anchor", lambda *a: anchor)
    monkeypatch.setattr(drv, "image_pdk_anchor", lambda *a: anchor)
    monkeypatch.setattr(capture, "openroad_identity", lambda image:
                        {"commit": "SOURCE_CONTROL_openroad_transport"})

    def fresh(script, roots, *, image):
        calls.append("drv_" + script.name)
        folder = script.parent
        control = script.name == "positive_control.tcl"
        prefix = "positive_control_" if control else ""
        limits = dict(fanout=2, fanout_limit=1, cap_limit=1e-6,
                      slew_limit=1e-6) if control else dict(fanout=2)
        put(folder, prefix + "all_limits.rpt",
            _report(**limits))
        put(folder, "positive_control.rpt" if control else "violators.rpt",
            _report(**limits, violators=True))
        put(folder, prefix + "counters.log", "".join(
            f"DRV_COUNTER {kind} {int(control)}\n" for kind in drv.KINDS))
        if not control:
            put(folder, "pin_census.tsv",
                "u/Y\tpin\toutput\t1\tu\tlogic\tY\tn\tinput\t0.1\t0.1\t0\tX\t0\n")
            put(folder, "net_census.rpt", "Net n\n Total capacitance: 0.1\n"
                " Number of drivers: 1\n Number of loads: 0\n Number of pins: 1\n"
                "\nDriver pins\n u/Y output (logic)\n\n")
            put(folder, "disabled_edges.rpt", "")
            put(folder, "annotation.rpt", "Found 0 unannotated drivers.\n")
            put(folder, "clocks.rpt", "propagated\n")
        return "OpenSTA SOURCE_CONTROL aaaaaaaaaa\n"

    monkeypatch.setattr(capture, "_run_fresh", fresh)
    real_process = R._pr.run
    real_supervised = fcc._watchdog.run_host_supervised

    def process(argv, **kwargs):
        if len(argv) > 1 and Path(str(argv[1])).name == "drv_signoff_judge.py":
            # Same actual CLI main and project judge; in-process dispatch keeps
            # the explicit synthetic PDK/owner I/O boundaries local to this test.
            stdout, stderr = io.StringIO(), io.StringIO()
            with monkeypatch.context() as env:
                if kwargs.get("cwd") is not None:
                    env.chdir(kwargs["cwd"])
                for name, value in (kwargs.get("env") or {}).items():
                    env.setenv(name, value)
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    rc = drv.main([str(value) for value in argv[2:]])
            return subprocess.CompletedProcess(argv, rc, stdout.getvalue(), stderr.getvalue())
        return real_process(argv, **kwargs)

    def supervised(argv, **kwargs):
        if len(argv) > 1 and Path(str(argv[1])).name == "drv_signoff_judge.py":
            done = process(argv, **kwargs)
            return fcc._watchdog.SupervisedResult(
                done.returncode, done.stdout, done.stderr, "natural", 0)
        return real_supervised(argv, **kwargs)

    monkeypatch.setattr(R._pr, "run", process)
    monkeypatch.setattr(fcc._watchdog, "run_host_supervised", supervised)


def fixture(tmp_path, monkeypatch, case):
    # Reuse the shipped neutral DRC transport and its calibrated report grammar.
    _, _, _, calls, project = _step31_control(tmp_path, monkeypatch, "clean")
    import shutil
    shutil.copytree(_project(tmp_path / "dependencies"), project,
                    dirs_exist_ok=True, ignore=shutil.ignore_patterns("provenance.jsonl"))
    pnr = R._pl.pnr_dir(project)
    pdk_root = project / "input/pdk"
    pdk = R.PdkConfig("open_fixture", str(pdk_root / "cells.lib"),
        str(pdk_root / "tech.lef"), str(pdk_root / "cell.lef"),
        None, "neutral", str(pdk_root / "neutral.lydrc"))
    for name in ("chip_top.def", "routed.def"):
        put(project, "phase3/stage3/pnr/" + name, LAYOUT)
    _write_gds(pnr / "chip_top.gds", n_shapes=12, cell="chip_top")
    put(project, "phase3/stage3/pnr/chip_top_pnr.v",
        "module chip_top(input a, output b); wire n; logic u (.Y(n)); assign b = a; endmodule\n")
    put(project, "phase2/stage2/synth/chip_top_synth.v",
        "module chip_top(input a, output b); wire n; logic u (.Y(n)); assign b = a; endmodule\n")
    put(project, "input/step_0_5ic_answers.json", "{}\n")
    put(project, "input/submission_template/neutral.json", "{}\n")
    put(project, "phase2/stage2/constraints/pvt_matrix.json", "{}\n")
    lib = put(project, "input/pdk/cells.lib", 'library(neutral) { time_unit : "1ns"; }\n')
    pdk.liberty = str(lib)
    pdk.bridge_magicrc = str(pdk_root / "neutral.magicrc")
    pdk.bridge_netgen_setup = str(put(project, "input/pdk/neutral_setup.tcl",
                                    "# neutral native comparison setup\n"))
    put(project, "reports/density.json", json.dumps({
        "tool": "klayout neutral density instrument",
        "layers": [{"name": "met1", "density_pct": 45.0}]}))
    put(project, "phase3/stage3/pnr/routed.odb",
        "SOURCE_CONTROL neutral routed checkpoint\n")
    post_stream_transport(project, pdk, monkeypatch, calls)
    native = R._docker_exec
    observed = []

    def provenance(tool, outputs, command=None):
        files = {str(Path(p).relative_to(project)): "sha256:" + sha(p)
                 for p in outputs if Path(p).is_file()}
        with (project / "provenance.jsonl").open("a") as stream:
            stream.write(json.dumps({"record": "invocation", "tool": tool,
                "measured": True, "exit_code": 0, "scope": "SOURCE_CONTROL",
                "command": command or tool,
                "measurement": {"schema": "mcp-eda/measurement/1",
                    "measured": True, "tool": tool,
                    "operation": "neutral source-control instrument",
                    "read": [str(pnr / "chip_top.def"), str(pnr / "chip_top.gds")],
                    "wrote": [str(p) for p in outputs]},
                "inputs": {str((pnr / name).relative_to(project)):
                           "sha256:" + sha(pnr / name)
                           for name in ("chip_top.def", "chip_top.gds")},
                "outputs": files}) + "\n")

    def transport(container, command, **kwargs):
        if "netgen -batch lvs" in command:
            calls.append("netgen")
            report = project / "reports/phase3/lvs.rpt"
            text = ("Final result: Circuits do NOT match.\n" if case == "lvs_fail" else
                    "Final result: Circuits match uniquely.\n")
            body = _PRIMARY_MATCH.replace("user_project_wrapper", "chip_top")
            if case == "lvs_fail":
                body = body.replace("Final result: Circuits match uniquely.",
                    "Netlists do not match.\nFinal result: Top level cell failed pin matching.")
            report.write_text(body)
            log = report.with_suffix(".log")
            log.write_text("Netgen source-control instrument completed\n" + text)
            provenance("netgen", [report, log], command)
            return 0, text, ""
        if "/erc_chip_top.tcl" in command:
            calls.append("erc")
            text = "0 floating nets\n0 floating pins\nERC_NATIVE_DONE\n"
            for output in kwargs.get("outputs", []):
                Path(output).write_text(text)
            provenance("openroad", kwargs.get("outputs", []))
            return 0, text, ""
        result = native(container, command, **kwargs)
        outputs = kwargs.get("outputs", [])
        if outputs and ("klayout -b" in command or command.startswith("magic ")):
            provenance("klayout" if "klayout -b" in command else "magic", outputs, command)
        return result

    def extraction(container, command, outputs, **kwargs):
        calls.append("extraction")
        for output in outputs:
            output = Path(output)
            if output.suffix == ".sp":
                output.write_text(".subckt chip_top a b\nRwire a b 1\n.ends\n")
            elif output.name == "ext2spice.log":
                output.write_text("0 errors\nMAGIC_EXT2SPICE_DONE chip_top_extracted.sp\n")
            else:
                output.write_text("")
        provenance("magic", outputs)
        return 0, "0 errors\nMAGIC_EXT2SPICE_DONE chip_top_extracted.sp\n", ""

    monkeypatch.setattr(R, "_docker_exec", transport)
    monkeypatch.setattr(R, "_declared_session_exec", extraction)
    monkeypatch.setattr(R, "_backend_parameter_values", lambda *_: {
        "pdk_root": str(project / "input/pdk"), "image_id": "SOURCE_CONTROL_ONLY"})
    monkeypatch.setattr(B, "_parameters", lambda *_: (R, None, pdk, "SOURCE_CONTROL_ONLY"))
    monkeypatch.setattr(P, "request", lambda: {"mode": "default"})
    monkeypatch.setattr(P, "_ordinary_runtime", None)
    real_declared, real_gate = D.run_for_step, W.evaluate

    def declared(root, sid, *args, **kwargs):
        result = real_declared(root, sid, *args, **kwargs)
        observed.append(("declared", str(sid), result))
        if case == "missing_erc":
            (root / "reports/phase3/erc.rpt").unlink(missing_ok=True)
        elif case == "changed_perc":
            path = root / "reports/phase3/perc_sweep.json"
            path.write_text(path.read_text() + "\nchanged after producer\n")
        return result

    def consume(root, row):
        observed.append(("consumer", str(row["id"]), None))
        return real_gate(root, row)

    monkeypatch.setattr(D, "run_for_step", declared)
    monkeypatch.setattr(W, "evaluate", consume)
    import _flow_yaml
    row = next(r for r in _flow_yaml.load()["steps"] if str(r["id"]) == "31")
    for name in row["required_outputs"]:
        (project / name).unlink(missing_ok=True)
    assert all(not (project / name).exists() for name in row["required_outputs"])
    # Use the unchanged layout identity and admission contract on these
    # source-control inputs; the selected native transports remain neutral.
    basis, error = R._layout_basis(project, 'chip_top', pdk, 'SOURCE_CONTROL_ONLY')
    assert not error, error
    put(project, 'reports/phase3/prestream_gate.json', json.dumps({
        'verdict': 'PASS', 'layout_digest': basis,
        'scope': 'SOURCE_CONTROL existing pre-stream transport'}))
    R._ga.admit_gds(project, pnr / 'chip_top.gds', basis,
                    R._layout_basis_paths(project, 'chip_top'))
    calls.clear()
    return project, pdk, calls, observed, row


def current_window(tmp_path, monkeypatch):
    """Real current Default owner/declared writers/full thirteen-output consumer."""
    project, pdk, calls, observed, row = fixture(tmp_path, monkeypatch, 'current')
    before = {name: (R._pl.pnr_dir(project) / name).read_bytes()
              for name in ('routed.def', 'chip_top.gds')}
    selected = R._phase3_window_sites('31', '31')
    assert selected == ['default_backend']
    args = SimpleNamespace(entry_step='31', exit_step='31',
                           container='SOURCE_CONTROL_ONLY', die_um=None,
                           util=None, spare_density=None)
    rc = R._run_phase3_window(project, 'chip_top', pdk, args, selected)
    assert rc == 0
    assert [value[:2] for value in observed] == [('declared', '31'), ('consumer', '31')]
    assert calls.count('erc') == calls.count('extraction') == calls.count('netgen') == 1
    assert observed[0][2]['verdict'] == 'PRODUCED'
    current = V.latest_pass(project, '31')
    assert current and current['canonical_pass'] and current['current_inputs']
    assert len(row['required_outputs']) == 13
    assert all((project / name).is_file() for name in row['required_outputs'])
    for name, payload in before.items():
        assert (R._pl.pnr_dir(project) / name).read_bytes() == payload
    return project, pdk, calls, observed, args


@pytest.mark.parametrize("case", ["current", "missing_erc", "changed_perc", "lvs_fail"])
def test_bounded_default31_owns_all_current_outputs_before_the_consumer(
        tmp_path, monkeypatch, case):
    project, pdk, calls, observed, definition = fixture(tmp_path, monkeypatch, case)
    selected = R._phase3_window_sites("31", "31")
    put(project, "ROUTE_OBSERVATION.json", json.dumps({"selected": selected,
        "required_owner": "Default31 native producer and current-step declared writers"}))
    assert selected == ["default_backend"], selected
    args = SimpleNamespace(entry_step="31", exit_step="31", container="SOURCE_CONTROL_ONLY",
                           die_um=None, util=None, spare_density=None)
    rc = R._run_phase3_window(project, "chip_top", pdk, args, selected)
    result = V.latest_pass(project, "31")
    receipt_path = project / "reports/phase3/default_steps/31/backend_result.json"
    receipt = json.loads(receipt_path.read_text()) if receipt_path.is_file() else None
    observation = {"case": case, "rc": rc, "selected": selected, "calls": calls,
        "observed": observed, "latest": result, "receipt": receipt,
        "required_outputs": {name: sha(project / name) if (project / name).is_file() else None
                             for name in definition["required_outputs"]},
        "scope": "source-only neutral native instruments; complete real Default31 consumer"}
    put(project, "API_OBSERVATION.json", json.dumps(observation, indent=2))
    assert calls.count("erc") == 1
    assert calls.count("extraction") == 1
    assert calls.count("netgen") == 1
    order = [x[:2] for x in observed]
    if case == "lvs_fail":
        assert rc != 0
        assert receipt["verdict"] == "FAIL", receipt
        assert receipt["producer_verdict"] == "FAIL", receipt
        assert order == [], "a measured producer FAIL must stop before declared writers or consumers"
        assert result is None
        return
    assert order == [("declared", "31"), ("consumer", "31")]
    census = observed[0][2]
    assert census["executed"] > 0, census
    if case == "current":
        assert order == [("declared", "31"), ("consumer", "31")]
        assert census["verdict"] == "PRODUCED", census
        assert rc == 0
        assert result and result["canonical_pass"] is True
        assert len(observation["required_outputs"]) == 13
        assert calls.count("drv_measure.tcl") == 2
        assert calls.count("drv_positive_control.tcl") == 2
        plan = json.loads((project / "reports/phase3/sta/drv_capture_plan.json").read_text())
        assert plan["identity"]["capture_point"] == "post_stream"
        assert all(observation["required_outputs"].values())
        perc = json.loads((project / "reports/phase3/perc_sweep.json").read_text())
        assert perc["program"] == perc["sweep"] == "perc_corpus_sweep"
        assert perc["invoked_as"] == "producer"
    else:
        assert rc != 0
        assert result is None or result["current_inputs"] is not True
        if case == "missing_erc":
            assert observation["required_outputs"]["reports/phase3/erc.rpt"] is None
        else:
            report = project / "reports/phase3/perc_sweep.json"
            producer = next(row for row in census["results"]
                            if row["program"] == "perc_corpus_sweep")
            assert sha(report) != producer["target_sha256"]


def test_ultra_keeps_its_existing_step31_sites(monkeypatch):
    monkeypatch.setattr(P, "request", lambda: {"mode": "ultra"})
    assert R._phase3_window_sites("31", "31") == ["drc", "lvs"]
