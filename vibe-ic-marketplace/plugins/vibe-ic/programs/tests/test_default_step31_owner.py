"""Actual bounded Default31 and consumers, with neutral native transports.

The invented transcripts exercise source ownership and ordering only. They
are not a native physical-verification result or an IC PASS claim.
"""
import hashlib
import json
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
COMPONENTS 0 ;
END COMPONENTS
PINS 2 ;
- a + NET signal + DIRECTION INPUT + USE SIGNAL ;
- b + NET signal + DIRECTION OUTPUT + USE SIGNAL ;
END PINS
NETS 1 ;
- signal ( PIN a ) ( PIN b ) + ROUTED met1 ( 1000 1000 ) ( 9000 1000 ) ;
END NETS
SPECIALNETS 2 ;
- PWR ( * VPWR ) + USE POWER + ROUTED met1 1000 ( 0 0 ) ( 10000 0 ) ;
- GND ( * VGND ) + USE GROUND + ROUTED met1 1000 ( 0 1000 ) ( 10000 1000 ) ;
END SPECIALNETS
END DESIGN
"""


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
        "module chip_top(input a, output b); assign b = a; endmodule\n")
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
    calls.clear()
    return project, pdk, calls, observed, row


@pytest.mark.parametrize("case", ["current", "missing_erc", "changed_perc", "lvs_fail"])
def test_bounded_default31_owns_all_current_outputs_before_the_consumer(
        tmp_path, monkeypatch, case):
    project, pdk, calls, observed, definition = fixture(tmp_path, monkeypatch, case)
    selected = R._phase3_window_sites("31", "31")
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
    assert selected == ["default_backend"]
    assert calls.count("erc") == 1
    assert calls.count("extraction") == 1
    assert calls.count("netgen") == (2 if case == "lvs_fail" else 1)
    order = [x[:2] for x in observed]
    assert order in ([("declared", "31")],
                     [("declared", "31"), ("consumer", "31")])
    census = observed[0][2]
    assert census["executed"] > 0, census
    if case == "current":
        assert order == [("declared", "31"), ("consumer", "31")]
        assert census["verdict"] == "PRODUCED", census
        assert rc == 0
        assert result and result["canonical_pass"] is True
        assert len(observation["required_outputs"]) == 9
        assert all(observation["required_outputs"].values())
        perc = json.loads((project / "reports/phase3/perc_sweep.json").read_text())
        assert perc["program"] == perc["sweep"] == "perc_corpus_sweep"
        assert perc["invoked_as"] == "producer"
    else:
        assert rc != 0
        assert result is None or result["current_inputs"] is not True
        if case == "lvs_fail":
            assert receipt["verdict"] == "FAIL", receipt
            assert receipt["producer_verdict"] == "FAIL", receipt
        elif case == "missing_erc":
            assert observation["required_outputs"]["reports/phase3/erc.rpt"] is None
        else:
            report = project / "reports/phase3/perc_sweep.json"
            producer = next(row for row in census["results"]
                            if row["program"] == "perc_corpus_sweep")
            assert sha(report) != producer["target_sha256"]


def test_ultra_keeps_its_existing_step31_sites(monkeypatch):
    monkeypatch.setattr(P, "request", lambda: {"mode": "ultra"})
    assert R._phase3_window_sites("31", "31") == ["drc", "lvs"]
