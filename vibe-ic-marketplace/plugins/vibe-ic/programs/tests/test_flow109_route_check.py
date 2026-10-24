"""Focused D42 source controls; native GF180 evidence is retained separately."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

import flow109
from flow109_admission import AdmissionRefusal
import flow109_route_check as route


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def _installed_native_source_identity(monkeypatch):
    """Host source tests substitute only the absent image-owned source files."""
    monkeypatch.setattr(route, "operation_sources", lambda: {
        "programs/flow109_route_check.py": _sha(Path(route.__file__))})


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    return path


def _receipt(folder, step, state, *, reports=(), invocation="actual native fixture output\n"):
    folder.mkdir(parents=True, exist_ok=True)
    state_path = _json(folder / "state_out.json", state)
    fingerprint = {"step": step}
    _json(folder / "input_fingerprint.json", fingerprint)
    _json(folder / "pdk_root.json", {"cli_pdk_root": "/pdk"})
    (folder / "invocation.log").write_text(invocation)
    hashes = {name: _sha(folder / name) for name in
              ("state_out.json", "input_fingerprint.json", "pdk_root.json", "invocation.log")}
    for name, content in reports:
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        hashes[name] = _sha(path)
    _json(folder / "vibeic_receipt.json", {"input": fingerprint, "sha256": hashes})
    return state_path


def _profile():
    return copy.deepcopy(next(
        item["features"] for item in flow109.load_registry()["reference_profiles"]
        if item["name"] == "Small digital IC without DFT"))


def _context():
    return flow109.execution_context(flow109.resolve_plan(_profile()), "D42", "D42")


def _d41(project, monkeypatch):
    monkeypatch.setenv("VIBEIC_EDA_IMAGE", route.EDA_IMAGE)
    pdk = project / "pdk"
    (pdk / "gf180mcuD").mkdir(parents=True)
    monkeypatch.setenv("VIBEIC_LIBRELANE_PDK_ROOT", str(pdk))
    _json(project / "reports/phase3/default_steps/32/backend_result.json", {
        "producer_verdict": "PASS",
        "gates": {"backend_canonical": "PASS", "backend_native_substance": "PASS"}})
    folder = project / "phase3/librelane/32-cand09/01-vibeic-postrouterepair"
    views = {}
    for name, content in (("def", "VERSION 5.8 ;\nDESIGN chip_top ;\nEND DESIGN\n"),
                          ("odb", "ODB\n"), ("nl", "module chip_top; endmodule\n"),
                          ("pnl", "module chip_top; endmodule\n"),
                          ("sdc", "create_clock -period 10 clk\n")):
        views[name] = folder / ("chip_top." + ("nl.v" if name == "nl" else
                                               "pnl.v" if name == "pnl" else name))
        views[name].parent.mkdir(parents=True, exist_ok=True)
        views[name].write_text(content)
    state = {name: str(path) for name, path in views.items()}
    state["metrics"] = {"route__drc_errors": 0}
    _receipt(folder, "Vibeic.PostRouteRepair", state)
    _json(folder / "config.json", {"PDK": "gf180mcuD", "STD_CELL_LIBRARY": "sc"})
    # config.json is an output in a real receipt; bind it after the helper made
    # the minimal receipt.
    receipt = json.loads((folder / "vibeic_receipt.json").read_text())
    receipt["sha256"].update({path.name: _sha(path) for path in views.values()})
    receipt["sha256"]["config.json"] = _sha(folder / "config.json")
    _json(folder / "vibeic_receipt.json", receipt)
    live_def = project / "phase3/stage3/pnr/routed.def"
    live_nl = project / "phase3/stage3/pnr/subservient_pnr.v"
    live_def.parent.mkdir(parents=True, exist_ok=True)
    live_def.write_bytes(views["def"].read_bytes())
    live_nl.write_bytes(views["nl"].read_bytes())
    _json(project / "reports/phase3/librelane_postroute_repair.json", {"adopted": "32-cand09"})
    _json(project / "reports/phase3/librelane_postroute_repair_handoff.json", {
        "state": str(folder / "state_out.json"), "state_sha256": _sha(folder / "state_out.json"),
        "views": {
            "def": {"source": str(views["def"]), "source_sha256": _sha(views["def"]),
                    "dest": str(live_def), "dest_sha256": _sha(live_def)},
            "nl": {"source": str(views["nl"]), "source_sha256": _sha(views["nl"]),
                   "dest": str(live_nl), "dest_sha256": _sha(live_nl)}}})
    return views


def _native_folders(project, lane, *, route_errors=0, disconnected=0,
                    wire_max=25, wire_threshold="100", omit_report=None):
    values = {
        "Checker.TrDRC": {"route__drc_errors": route_errors},
        "Odb.ReportDisconnectedPins": {"design__critical_disconnected_pin__count": disconnected},
        "Checker.DisconnectedPins": {"design__critical_disconnected_pin__count": disconnected},
        "Odb.ReportWireLength": {"route__wirelength__max": wire_max},
        "Checker.WireLength": {"route__wirelength__max": wire_max},
    }
    folders = []
    for index, step in enumerate(route.STEPS, 1):
        folder = project / "phase3/librelane" / lane / f"{index:02d}-{step.lower().replace('.', '-')}"
        reports = []
        if step == "Odb.ReportDisconnectedPins" and omit_report != "disconnected":
            reports.append(("full_disconnected_pins_table.txt", "Pin,Reason\n"))
        if step == "Odb.ReportWireLength" and omit_report != "wire":
            reports.append(("wire_lengths.csv", "net,length_um\n"))
        invocation = ("Threshold for Threshold-surpassing long wires is not set. "
                      "The checker will be skipped.\n"
                      if step == "Checker.WireLength" and wire_threshold is None
                      else "actual native fixture output\n")
        _receipt(folder, step, {"metrics": values[step]}, reports=reports,
                 invocation=invocation)
        _json(folder / "config.json", {"meta": {"step": step},
                                       "WIRE_LENGTH_THRESHOLD": wire_threshold})
        folders.append(folder)
    return folders


def test_default_d42_dispatch_and_product_reverse():
    plan = flow109.resolve_plan(_profile())
    row = flow109.resolve_execution_window(plan, "D42", "D42")["resolved_row"]
    assert row["profile"]["dispatch"]["adapter"] == "flow109_route_check"
    assert row["profile"]["native_steps"] == route.STEPS
    window = flow109.resolve_execution_window(plan, "D42", "D43")
    assert window["dispatch_kind"] == "selected_native_sequence"
    reverse = copy.deepcopy(plan)
    next(item for item in reverse["rows"] if item["id"] == "D42")["profile"].pop("dispatch")
    with pytest.raises(flow109.Flow109Error, match="FLOW109_PRODUCER_UNIMPLEMENTED"):
        flow109.resolve_execution_window(reverse, "D42", "D42")


def test_whole_d42_adapter_positive_uses_native_consumer(tmp_path, monkeypatch):
    _d41(tmp_path, monkeypatch)
    configs = {}
    for step in route.STEPS:
        configs[step] = _json(tmp_path / "configs" / f"{step}.json", {
            "meta": {"step": step}, "WIRE_LENGTH_THRESHOLD": "100"})
    monkeypatch.setattr(route.contract, "resolve_step_configs", lambda *a, **k: configs)
    monkeypatch.setattr(route, "require_launch_admission", lambda *a, **k: {"status": "PASS"})
    monkeypatch.setattr(route.contract, "run_chain", lambda project, image, steps, **kw:
                        _native_folders(project, kw["lane"]))
    result = route.execute(tmp_path, _context())
    assert result["component_status"] == result["metric_acceptance_status"] == "PASS"
    assert result["measurements"]["route_short_overlap_errors"] == 0
    assert result["measurements"]["disconnected_required_pins"] == 0
    assert result["flow109_acceptance"] == result["whole_flow_acceptance"] == "NOT_MEASURED"


def test_unset_pdk_wire_threshold_is_disclosed_not_an_invented_requirement(
        tmp_path, monkeypatch):
    _d41(tmp_path, monkeypatch)
    configs = {step: _json(tmp_path / "configs" / f"{step}.json", {
        "meta": {"step": step}, "WIRE_LENGTH_THRESHOLD": None}) for step in route.STEPS}
    monkeypatch.setattr(route.contract, "resolve_step_configs", lambda *a, **k: configs)
    monkeypatch.setattr(route, "require_launch_admission", lambda *a, **k: {"status": "PASS"})
    monkeypatch.setattr(route.contract, "run_chain", lambda project, image, steps, **kw:
        _native_folders(project, kw["lane"], wire_threshold=None))
    result = route.execute(tmp_path, _context())
    assert result["component_status"] == "PASS"
    assert result["wire_length_check"]["status"] == "NOT_APPLICABLE"
    assert result["measurements"]["wire_length_threshold_um"] is None


def test_consumer_only_reentry_reuses_exact_native_chain(tmp_path, monkeypatch):
    _d41(tmp_path, monkeypatch)
    prepared = route.prepare(tmp_path, _context())
    old_id = "e9c551fef2c94712987378e4b70cd4c1"
    folders = _native_folders(tmp_path, "D42-route-check-" + old_id[:12], disconnected=21,
                              wire_max=2051.8, wire_threshold=None)
    fingerprint_path = folders[0] / "input_fingerprint.json"
    fingerprint = json.loads(fingerprint_path.read_text())
    fingerprint.update(image=prepared["image"], state_files={
        str(path): prepared["inputs"]["view_" + name]["sha256"]
        for name, path in prepared["views"].items()})
    _json(fingerprint_path, fingerprint)
    native_receipt_path = folders[0] / "vibeic_receipt.json"
    native_receipt = json.loads(native_receipt_path.read_text())
    native_receipt["input"] = fingerprint
    native_receipt["sha256"]["input_fingerprint.json"] = _sha(fingerprint_path)
    _json(native_receipt_path, native_receipt)
    _json(tmp_path / "reports/flow109/D42" / old_id / "receipt.json", {
        "schema": route.SCHEMA, "reason": "D42_WIRE_THRESHOLD_UNSTATED",
        "native_started": True, "inputs": prepared["inputs"], "image": prepared["image"],
        "adopted_candidate": prepared["candidate"]})
    monkeypatch.setattr(route, "require_launch_admission", lambda *a, **k: {"status": "PASS"})
    monkeypatch.setattr(route.contract, "run_chain",
                        lambda *a, **k: pytest.fail("retained native chain must not replay"))
    result = route.execute(tmp_path, _context())
    assert result["component_status"] == "FAIL"
    assert result["failed_obligations"] == ["route_check.disconnected_required_pins"]
    assert result["native_started"] is False and result["native_reused"] is True
    assert result["wire_length_check"]["status"] == "NOT_APPLICABLE"


@pytest.mark.parametrize("which", ["def", "odb", "nl", "sdc"])
def test_changed_or_missing_adopted_input_refuses_before_native(tmp_path, monkeypatch, which):
    views = _d41(tmp_path, monkeypatch)
    views[which].write_text("changed\n")
    with pytest.raises((AdmissionRefusal, route.contract.Refusal),
                       match="D42_D41_VIEW_STALE|D42_D41_HANDOFF_STALE|LL_STEP_OUTPUT_UNBOUND"):
        route.prepare(tmp_path, _context())


def test_failed_d41_refuses_before_native(tmp_path, monkeypatch):
    _d41(tmp_path, monkeypatch)
    path = tmp_path / "reports/phase3/default_steps/32/backend_result.json"
    body = json.loads(path.read_text())
    body["gates"]["backend_native_substance"] = "FAIL"
    _json(path, body)
    with pytest.raises(AdmissionRefusal, match="D42_D41_NOT_ACCEPTED"):
        route.prepare(tmp_path, _context())


def test_same_bytes_project_symlink_refuses_before_native(tmp_path, monkeypatch):
    views = _d41(tmp_path, monkeypatch)
    folder = views["odb"].parent
    alias = folder / "alias.odb"
    alias.symlink_to(views["odb"].name)
    state_path = folder / "state_out.json"
    state = json.loads(state_path.read_text())
    state["odb"] = str(alias)
    _json(state_path, state)
    native_receipt_path = folder / "vibeic_receipt.json"
    native_receipt = json.loads(native_receipt_path.read_text())
    native_receipt["sha256"]["state_out.json"] = _sha(state_path)
    native_receipt["sha256"]["alias.odb"] = _sha(alias)
    _json(native_receipt_path, native_receipt)
    handoff_path = tmp_path / "reports/phase3/librelane_postroute_repair_handoff.json"
    handoff = json.loads(handoff_path.read_text())
    handoff["state_sha256"] = _sha(state_path)
    _json(handoff_path, handoff)
    with pytest.raises(AdmissionRefusal, match="D42_SUBJECT_SYMLINK"):
        route.prepare(tmp_path, _context())


@pytest.mark.parametrize(
    "route_errors,disconnected,omit,expected,reason",
    [(1, 0, None, "FAIL", "D42_NATIVE_CHECK_FAILED"),
     (0, 2, None, "FAIL", "D42_NATIVE_CHECK_FAILED"),
     (0, 0, "disconnected", "NOT_MEASURED", "D42_DISCONNECTED_REPORT_EMPTY"),
     (0, 0, "wire", "NOT_MEASURED", "D42_WIRE_REPORT_EMPTY")])
def test_native_fail_and_missing_reports_never_pass(
        tmp_path, monkeypatch, route_errors, disconnected, omit, expected, reason):
    _d41(tmp_path, monkeypatch)
    configs = {step: _json(tmp_path / "configs" / f"{step}.json", {
        "meta": {"step": step}, "WIRE_LENGTH_THRESHOLD": "100"}) for step in route.STEPS}
    monkeypatch.setattr(route.contract, "resolve_step_configs", lambda *a, **k: configs)
    monkeypatch.setattr(route, "require_launch_admission", lambda *a, **k: {"status": "PASS"})
    monkeypatch.setattr(route.contract, "run_chain", lambda project, image, steps, **kw:
        _native_folders(project, kw["lane"], route_errors=route_errors,
                        disconnected=disconnected, omit_report=omit))
    result = route.execute(tmp_path, _context())
    assert result["component_status"] == expected
    assert result["reason"] == reason
    assert result["component_status"] != "PASS"
