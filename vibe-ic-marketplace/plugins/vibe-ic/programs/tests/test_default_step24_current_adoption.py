"""Neutral instrument writes; real Default producer, adoption and consumers."""
import hashlib
import json
from pathlib import Path

import pytest

import em_current_density_check as emc
import execution_adapters_backend as adapters
import execution_backend_producers as backend
import execution_policy as policy
import librelane_ir_antenna as la
import phase3_one_shot_runner as runner
import step_output_collector as versions
from test_backend_input_contract_sdc import _project
from test_pad_connected_pdn_ring import _pdk


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inputs(tmp_path):
    root = _project(tmp_path)
    for rel in ("input/step_0_5ic_answers.json", "input/submission_template/neutral.json"):
        _write(root, rel, "{}\n")
    for rel in ("phase3/stage3/pnr/routed.def", "phase3/stage3/pnr/neutral.def"):
        _write(root, rel, "DESIGN neutral ;\nEND DESIGN\n")
    _write(root, "phase3/stage3/pnr/neutral_pnr.v", "module neutral; endmodule\n")
    _write(root, "phase3/stage3/pnr/pdn.done", "native PDN completed\n")
    _write(root, "phase3/stage3/extracted/neutral.spef", "*SPEF neutral\n")
    _write(root, "phase2/stage2/constraints/pvt_matrix.json", "{}\n")
    return root


def _contract(project, mode):
    # Exercise the shared issuer, including its actual mode handoff, rather
    # than depending on a new helper keyword unavailable on the red product.
    issued = {}
    policy._fixed_inputs({"project": project, "policy": {"mode": mode}}, "23", parameters=issued)
    return issued["resolved_input_manifest"]["files"]


@pytest.mark.parametrize("case", ["current", "native_fail", "missing_native",
    "changed_input", "changed_artifact", "changed_liberty", "changed_tool_state", "missing_liberties"])
def test_default_step24_adopts_its_current_native_primary(tmp_path, monkeypatch, case):
    project = _inputs(tmp_path)
    pdk = _pdk(tmp_path, ring=None)
    monkeypatch.setattr(runner, "_backend_parameter_values", lambda *_: {
        "pdk_root": str(tmp_path), "image_id": "SOURCE_CONTROL_ONLY"})
    seen = {}
    def parameters(_root, params):
        seen["manifest"] = params["resolved_input_manifest"]
        return runner, None, pdk, "SOURCE_CONTROL_ONLY"
    monkeypatch.setattr(backend, "_parameters", parameters)
    monkeypatch.setattr(runner, "_librelane_step_ctx", lambda *_: ("SOURCE_CONTROL_ONLY", tmp_path))
    calls = []
    assets = {}

    def instrument(root, *_args, **kw):
        calls.append("native_ir_transport")
        if case == "missing_native":
            raise ValueError("neutral instrument did not produce a native result")
        folder = "phase3/librelane/24/01-openroad-irdropreport"
        drop = 0.6 if case == "native_fail" else 0.01
        metrics = {"design_powergrid__drop__worst__net:" + net: drop for net in ("VDD", "VSS")}
        state = _write(root, folder + "/state_out.json", json.dumps({"metrics": metrics}))
        odb = _write(root, folder + "/input.odb", "neutral ODB instrument bytes\n")
        tech = _write(root, folder + "/tech.lef", "neutral technology instrument bytes\n")
        lib = _write(root, folder + "/timing.lib", "library(neutral) {}\n")
        _write(root, folder + "/state_in.json", json.dumps({"odb": str(odb),
            "sdc": str(kw["sdc"]), "spef": {"unit": str(kw["spef"])}}))
        _write(root, "phase3/librelane/24-config/OpenROAD.IRDropReport.json", json.dumps({
            "DEFAULT_CORNER": "unit", "TECH_LEFS": {"unit": str(tech)}}))
        _write(root, folder + "/openroad-irdropreport.log",
            "Reading timing library for the 'unit' corner at '" + str(lib) + "'\n")
        _write(root, folder + "/irdrop.rpt", "OpenROAD PSM static IR source-control report\n" +
            "".join(f"neutral grid node {i}: voltage drop {drop} V\n" for i in range(40)))
        # The real runner appends its tool invocation to this audit-only log.
        with (root / "provenance.jsonl").open("a") as stream:
            stream.write('{"record":"neutral invocation"}\n')
        assets.update(state=state, liberty=lib, report=root / folder / "irdrop.rpt")
        return {"tool_state": str(state), "tool_state_sha256": _sha(state),
            "def_sha256": _sha(kw["routed_def"]), "spef_sha256": _sha(kw["spef"]),
            "vdd_nets": ["VDD"], "gnd_nets": ["VSS"], "lib_voltage_v": 5.0,
            "budget_pct": 10.0, "budget_source": "neutral instrument budget",
            "rc_model": {"verdict": "AGREE", "disagreeing": []},
            "cross_check": {"verdict": "MEASURED", "per_net_worst_drop_v": {"VDD": drop, "VSS": drop}},
            "transient": {"verdict": "MEASURED", "max_dynamic_drop_mv": 10.0, "vdd": 5.0}}

    monkeypatch.setattr(la, "run_ir", instrument)
    original = runner._librelane_step24_publish
    publishes = []

    def publish(root, doc):
        original(root, doc)
        publishes.append(True)
        if len(publishes) == 1:
            if case == "missing_liberties":
                doc["native_basis"]["liberties"] = []
                _write(root, "reports/phase3/ir_drop_librelane.json", json.dumps(doc))
            target = {"changed_input": root / "phase3/stage3/pnr/constraint.sdc",
                      "changed_artifact": assets.get("report"),
                      "changed_liberty": assets.get("liberty"),
                      "changed_tool_state": assets.get("state")}.get(case)
            if target:
                target.write_text(target.read_text() + "\nchanged\n")

    monkeypatch.setattr(runner, "_librelane_step24_publish", publish)
    row = runner._default_pnr_rows(project, "neutral", pdk, "SOURCE_CONTROL_ONLY", ["24"])
    doc = json.loads((project / "reports/phase3/ir_drop_librelane.json").read_text())
    receipt = json.loads((project / "reports/phase3/default_steps/24/backend_result.json").read_text())
    validation = emc.validate_primary_ir(project)
    valid, verdict = validation["valid"], validation.get("verdict")
    _write(project, "API_OBSERVATION.json", json.dumps({"case": case, "status": row.status,
        "detail": row.detail, "primary": doc, "receipt": receipt,
        "validation": [valid, verdict], "calls": calls, "seen": seen,
        "source_control_only": True}, indent=2))
    # The whole Default row retains its existing failed-producer FAIL;
    # the unavailable primary remains explicitly NOT_MEASURED underneath.
    expected = "PASS" if case == "current" else "FAIL"
    assert calls == ["native_ir_transport"]
    assert row.status == expected, row.detail
    assert not (project / "reports/phase3/ir_drop_psm_cross_check.json").exists()
    assert "power_basis" not in doc
    if case in ("current", "native_fail"):
        assert doc["adopted"] is True
        assert valid is True
        assert verdict == expected
        assert doc["judgment"]["verdict"] == expected
    else:
        assert valid is False
        assert verdict == "NOT_MEASURED"
    if case == "current":
        assert "provenance.jsonl" not in seen["manifest"]["files"]
        assert receipt["canonical_step_result"]["status"] == "PASS"
        assert versions.latest_pass(project, "24")["current_inputs"] is True
        with (project / "provenance.jsonl").open("a") as stream:
            stream.write('{"record":"next producer"}\n')
        assert versions.latest_pass(project, "24")["current_inputs"] is True
        (project / "phase3/stage3/pnr/routed.def").write_text("changed real layout\n")
        assert versions.latest_pass(project, "24")["current_inputs"] is False


@pytest.mark.parametrize("changed", ["journal", "step8", "sdc"])
def test_default_step23_checkpoint_keeps_scientific_inputs_current(tmp_path, changed):
    project = _inputs(tmp_path)
    issued = {}
    policy._fixed_inputs({"project": project, "policy": {"mode": "default"}}, "23", parameters=issued)
    files = issued["resolved_input_manifest"]["files"]
    assert "provenance.jsonl" not in files
    assert "phase3/librelane/prelayout/gates/step8_sdc_opensta.json" in files
    assert "phase2/stage2/constraints/spm.asic.sdc" in files
    # Exercise the existing version API with neutral output bytes. This is
    # a source-level currentness control, not a Step23 native qualification.
    from execution_adapters_backend import ROWS
    row = ROWS["23"]["canonical_row"]
    for spec in row.get("required_outputs", []):
        for alternative in spec.split(" OR "):
            _write(project, alternative.strip().replace("*", "neutral"), "neutral output\n")
    attempt = versions.begin_step(project, "23", files)
    versions.finish_step(project, attempt, verdict="PASS", canonical_pass=True)
    assert versions.latest_pass(project, "23")["current_inputs"] is True
    rel = {"journal": "provenance.jsonl", "step8": "phase3/librelane/prelayout/gates/step8_sdc_opensta.json",
           "sdc": "phase2/stage2/constraints/spm.asic.sdc"}[changed]
    with (project / rel).open("a") as stream:
        stream.write("next producer\n")
    assert versions.latest_pass(project, "23")["current_inputs"] is (changed == "journal")


def test_ultra_transport_and_explicit_step7_journal_binding_are_retained(tmp_path):
    project = _inputs(tmp_path)
    assert "provenance.jsonl" in _contract(project, "ultra")
    step7 = project / "phase2/stage2/constraints/asic_sdc.json"
    doc = json.loads(step7.read_text())
    doc["inputs"] = {"provenance.jsonl": _sha(project / "provenance.jsonl")}
    step7.write_text(json.dumps(doc))
    assert "provenance.jsonl" in _contract(project, "default")


def test_default_reads_semantic_image_live_but_keeps_explicit_file_bindings(tmp_path):
    project = _inputs(tmp_path)
    report = _write(project, "reports/container_image.json", json.dumps({
        "image_id": "sha256:neutral", "container": "first", "verdict": "PASS"}))
    step7 = project / "phase2/stage2/constraints/asic_sdc.json"
    doc = json.loads(step7.read_text())
    doc["inputs"] = {"@container_image": "semantic image binding"}
    step7.write_text(json.dumps(doc))
    default = _contract(project, "default")
    ultra = _contract(project, "ultra")
    assert "reports/container_image.json" not in default
    assert "phase2/stage2/constraints/asic_sdc.json" in default
    assert "reports/container_image.json" in ultra
    report.write_text(report.read_text().replace("first", "next"))
    assert default == _contract(project, "default")
    doc["inputs"]["reports/container_image.json"] = _sha(report)
    step7.write_text(json.dumps(doc))
    assert "reports/container_image.json" in _contract(project, "default")
