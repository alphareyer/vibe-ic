"""Exercise source-owned selection through the canonical front door.

These are configuration/dispatch tests, not EDA qualification or IC PASS.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import flow109
from programs.tests._hostpaths import require_repo


def profile(name="Small digital IC without DFT"):
    registry = flow109.load_registry()
    return copy.deepcopy(next(p["features"] for p in registry["reference_profiles"] if p["name"] == name))


def test_source_mapping_preserves_native_identity_and_unfiltered_position():
    source = require_repo("vibe-ic-marketplace", "plugins", "vibe-ic", "flow", "phase1_phase2_phase3.yaml")
    import yaml
    native = yaml.safe_load(source.read_text())["steps"]
    plan = flow109.resolve_plan(profile())
    cts = flow109.resolve_step(plan, "D38")
    assert cts["legacy70"] == [{"position": 34, "id": "19", "name": native[33]["name"]}]
    assert cts["position"] > 34
    assert cts["profile"]["dispatch"]["native_step_id"] == "19"
    assert cts["tool_uses"][0]["use_id"] == "flow109.D38.default.openroad"
    assert plan["runtime_verdict"] == plan["flow109_acceptance"] == "NOT_MEASURED"
    assert [g["id"] for g in plan["gate_layers"]] == [f"G{i}" for i in range(1, 10)]
    assert cts["gate_matrix_status"] == "NOT_MEASURED_FOR_109"


def test_reference_profiles_close_without_turning_unknown_into_false():
    for item in flow109.load_registry()["reference_profiles"]:
        plan = flow109.resolve_plan(item["features"])
        assert plan["selected_count"] > 0
        assert plan["status"] == "PLAN_RESOLVED_NOT_EXECUTED"
    features = profile()
    del features["has_digital"]
    with pytest.raises(flow109.Flow109Error, match="FEATURE_UNRESOLVED"):
        flow109.resolve_plan(features)


@pytest.mark.parametrize("mutation", ["derived", "endpoint", "fpga", "non_boolean"])
def test_invalid_feature_sets_refuse_before_dispatch(mutation):
    features = profile()
    if mutation == "derived":
        features["digital_physical"] = False
    elif mutation == "endpoint":
        features["soft_ip"] = True
    elif mutation == "fpga":
        features = profile("Analog model-IP")
        features["fpga_final"] = True
    else:
        features["has_digital"] = "false"
    with pytest.raises(flow109.Flow109Error):
        flow109.resolve_plan(features)


def test_boolean_grammar_visits_hidden_unknowns_and_rejects_code():
    with pytest.raises(flow109.Flow109Error, match="FEATURE_UNRESOLVED"):
        flow109.predicate("always or omitted", {"always": True})
    with pytest.raises(flow109.Flow109Error, match="PREDICATE_INVALID"):
        flow109.predicate("open('unsafe')", {})


def test_analog_profile_cannot_be_forced_into_digital_default():
    features = profile("Analog hard-IP")
    plan = flow109.resolve_plan(features)
    assert flow109.resolve_step(plan, "D69")["selected_profile"] == "analog_die"
    with pytest.raises(flow109.Flow109Error, match="PROFILE_INAPPLICABLE"):
        flow109.resolve_plan(features, {"D69": "default"})


def test_odb_fill_is_not_aliased_to_legacy_gds_fill_pass():
    plan = flow109.resolve_plan(profile())
    fill = flow109.resolve_step(plan, "D62")
    assert fill["profile"]["entrypoint"]["command"] == "density_fill"
    assert fill["profile"]["geometry_format"] == "ODB"
    assert fill["profile"]["native_steps"] == [
        "OpenROAD.DensityFill", "OpenROAD.CheckMetalDensity",
        "OpenROAD.CheckFillObstructions"]
    window = flow109.resolve_execution_window(plan, "D62", "D62")
    assert window["native_step_id"] == "flow109.D62"
    assert window["resolved_row"]["profile"]["dispatch"]["adapter"] == "flow109_density_fill"
    assert window["resolved_row"]["profile"]["dispatch"]["flow109_metrics_status"] == "IMPLEMENTED_D62_RUNTIME"
    final_extract = flow109.resolve_execution_window(plan, "D69", "D69")
    assert final_extract["resolved_row"]["profile"]["dispatch"]["adapter"] == "flow109_final_extract"
    final_electrical = flow109.resolve_execution_window(plan, "D70", "D70")
    assert final_electrical["resolved_row"]["profile"]["dispatch"]["adapter"] == "flow109_final_electrical"
    continuation = flow109.resolve_execution_window(plan, "D38", "D41")
    assert [row["id"] for row in continuation["resolved_rows"]] == ["D38", "D39", "D40", "D41"]
    assert continuation["native_entry_step"] == "19"
    assert continuation["native_exit_step"] == "32"


def test_context_rejects_changed_tool_identity_and_changed_selection():
    plan = flow109.resolve_plan(profile())
    original = flow109.execution_context(plan, "D38", "D38", runtime_tool_uses=[],
                                         qualification_registry="input/qualifications.json")
    assert flow109.validate_execution_context(original) == original
    changed = copy.deepcopy(original)
    changed["runtime_tool_uses"].append({"use_id": "unrelated"})
    with pytest.raises(flow109.Flow109Error, match="CONTEXT_STALE"):
        flow109.validate_execution_context(changed)
    changed = copy.deepcopy(original)
    changed["resolved_row"]["profile"]["dispatch"]["native_step_id"] = "21"
    changed["context_sha256"] = flow109.digest({k: v for k, v in changed.items() if k != "context_sha256"})
    with pytest.raises(flow109.Flow109Error, match="CONTEXT_STALE"):
        flow109.validate_execution_context(changed)


def test_canonical_cli_plan_does_not_credit_or_rewrite_old_pass(tmp_path):
    runner = require_repo("vibe-ic-marketplace", "plugins", "vibe-ic", "programs", "vibe_ic_one_shot_runner.py")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"features": profile()}))
    old = tmp_path / "reports" / "phase3_one_shot.json"
    old.parent.mkdir()
    old.write_text('{"verdict":"PASS","native_identity":"preserve"}\n')
    before = old.read_bytes()
    command = [sys.executable, str(runner), str(tmp_path), "--flow", "109",
               "--flow109-manifest", str(manifest), "--flow109-plan-only", "--route", "ic", "--no-dashboard"]
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    plan = json.loads((tmp_path / "reports" / "flow109" / "plan.json").read_text())
    assert plan["flow109_acceptance"] == "NOT_MEASURED"
    assert old.read_bytes() == before
    assert not (tmp_path / "reports" / "flow109" / "admission.json").exists()
    # Use a currently unmapped selected row, not a permanently unimplemented
    # D69: the final RCX producer is now wired.
    unmapped = next(row['id'] for row in plan['rows'] if not row['profile'].get('dispatch'))
    result = subprocess.run([x for x in command if x != "--flow109-plan-only"]
                            + ["--entry-step", unmapped, "--exit-step", unmapped],
                            text=True, capture_output=True)
    assert result.returncode == 2
    assert "FLOW109_PRODUCER_UNIMPLEMENTED" in result.stderr
    assert old.read_bytes() == before
    # The implemented fill component still cannot launch without qualification.
    result = subprocess.run([x for x in command if x != "--flow109-plan-only"]
                            + ["--entry-step", "D62", "--exit-step", "D62"],
                            text=True, capture_output=True)
    assert result.returncode == 2
    assert "QUALIFICATION_RUNTIME_ABSENT" in result.stderr
    refusal = json.loads((tmp_path / "reports/flow109/admission.json").read_text())
    assert refusal["native_started"] is False
    assert old.read_bytes() == before


def test_real_front_door_selection_translates_only_the_named_component(tmp_path, monkeypatch):
    import _delivery_route
    assert _delivery_route.admit(tmp_path, "ic") is None
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"features": profile(), "runtime_tool_uses": [],
                                    "qualification_registry": "input/qualifications.json"}))
    observed = []
    monkeypatch.setenv(flow109.CONTEXT_ENV, "")
    # This control observes the selection/dispatch contract only. Qualification
    # behavior itself is covered by the admission module's native controls.
    monkeypatch.setitem(sys.modules, "flow109_admission", SimpleNamespace(
        require_admission=lambda project, context: observed.append(context) or {"status": "PASS", "test_scope": "selection_only"}))
    args = SimpleNamespace(flow="109", flow109_manifest=manifest, flow109_plan_only=False,
                           execution_mode="default", entry_step="D38", exit_step="D38")
    assert flow109.prepare_front_door(args, tmp_path) is None
    assert args.entry_step == args.exit_step == "19"
    assert observed[0]["requested_step"] == "D38"
    context = json.loads(Path(__import__("os").environ[flow109.CONTEXT_ENV]).read_text())
    assert flow109.validate_execution_context(context) == observed[0]


def test_frontend_rows_route_stable_ids_to_existing_default_owner_sites():
    plan = flow109.resolve_plan(profile())
    expected = {
        "D01": ("D1", "phase1_one_shot_runner", "D1", "D1"),
        "D02": ("0.5ic", "phase1_one_shot_runner", "D1", "D1"),
        "D09": ("1", "design_one_shot_runner", "1", "1"),
        "D10": ("P0", "design_one_shot_runner", "1", "1"),
        "D11": ("2", "design_one_shot_runner", "2", "3"),
        "D12": ("3", "design_one_shot_runner", "2", "3"),
        "D13": ("4", "design_one_shot_runner", "4", "4"),
        "D14": ("5", "design_one_shot_runner", "4", "5"),
        "D19": ("7", "design_one_shot_runner", "7", "7"),
        "D20": ("8", "design_one_shot_runner", "8", "8"),
        "D25": ("13", "design_one_shot_runner", "11", "13"),
        "D26": ("10", "design_one_shot_runner", "10", "10"),
        "D30": ("14", "design_one_shot_runner", "14", "14"),
    }
    for row_id, values in expected.items():
        row = flow109.resolve_step(plan, row_id)
        dispatch = row["profile"]["dispatch"]
        assert dispatch["kind"] == "canonical_owner_window"
        assert (dispatch["native_step_id"], dispatch["owner_runner"],
                dispatch["owner_entry_step"], dispatch["owner_exit_step"]) == values
        assert dispatch["native_step_id"] == row["legacy70"][0]["id"]
        assert row["profile"]["execution_status"] == "NATIVE_COMPONENT_ONLY_NOT_109_ACCEPTED"
        assert dispatch["flow109_metrics_status"] == "UNIMPLEMENTED"


def test_d24_routes_to_existing_default_dft_producer_and_consumer(tmp_path, monkeypatch):
    features = profile("Mapped soft-IP with prestitched scan")
    plan = flow109.resolve_plan(features)
    row = flow109.resolve_step(plan, "D24")
    dispatch = row["profile"]["dispatch"]
    assert dispatch["kind"] == "canonical_owner_window"
    assert (dispatch["native_step_id"], dispatch["owner_runner"],
            dispatch["owner_entry_step"], dispatch["owner_exit_step"]) == (
                "12", "design_one_shot_runner", "12", "12")
    assert dispatch["producer"] == (
        "design_one_shot_runner.py:_step_post_dft_optimization")
    assert dispatch["consumer"] == (
        "design_one_shot_runner retained Step 12 canonical acceptance")
    assert row["profile"]["execution_status"] == (
        "NATIVE_COMPONENT_ONLY_NOT_109_ACCEPTED")
    import _delivery_route
    assert _delivery_route.admit(tmp_path, "ip") is None
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"features": features}))
    monkeypatch.setenv(flow109.CONTEXT_ENV, "")
    args = SimpleNamespace(flow="109", flow109_manifest=manifest,
                           flow109_plan_only=False, execution_mode="default",
                           entry_step="D24", exit_step="D24", container="")
    assert flow109.prepare_front_door(args, tmp_path) is None
    assert (args.entry_step, args.exit_step) == ("12", "12")
    owner = json.loads((tmp_path / "reports/flow109/owner_route.json").read_text())
    assert owner["owner_runner"] == "design_one_shot_runner"
    assert owner["native_step_id"] == "12"


def test_d23_routes_to_existing_default_dft_producer_and_consumer(tmp_path, monkeypatch):
    features = profile("Mapped soft-IP with prestitched scan")
    plan = flow109.resolve_plan(features)
    row = flow109.resolve_step(plan, "D23")
    dispatch = row["profile"]["dispatch"]
    assert dispatch["kind"] == "canonical_owner_window"
    assert (dispatch["native_step_id"], dispatch["owner_runner"],
            dispatch["owner_entry_step"], dispatch["owner_exit_step"]) == (
                "11", "design_one_shot_runner", "11", "11")
    assert dispatch["producer"] == (
        "design_one_shot_runner.py:_step_dft_insertion")
    assert dispatch["consumer"] == (
        "design_one_shot_runner retained Step 11 canonical acceptance")
    assert row["profile"]["execution_status"] == (
        "NATIVE_COMPONENT_ONLY_NOT_109_ACCEPTED")
    import _delivery_route
    assert _delivery_route.admit(tmp_path, "ip") is None
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"features": features}))
    monkeypatch.setenv(flow109.CONTEXT_ENV, "")
    args = SimpleNamespace(flow="109", flow109_manifest=manifest,
                           flow109_plan_only=False, execution_mode="default",
                           entry_step="D23", exit_step="D23", container="")
    assert flow109.prepare_front_door(args, tmp_path) is None
    assert (args.entry_step, args.exit_step) == ("11", "11")
    owner = json.loads((tmp_path / "reports/flow109/owner_route.json").read_text())
    assert owner["owner_runner"] == "design_one_shot_runner"
    assert owner["native_step_id"] == "11"


def _step9_native_state(tmp_path, monkeypatch):
    import hashlib
    import vibe_ic_one_shot_runner as runner

    netlist = tmp_path / "phase2/stage2/synth/netlist.v"
    netlist.parent.mkdir(parents=True)
    netlist.write_text("module top(input clk); endmodule\n")
    sha = hashlib.sha256(netlist.read_bytes()).hexdigest()
    folder = "phase2/stage2/9_current"
    record = tmp_path / "steps" / folder / "outputs.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({
        "status": "NOT_MEASURED",
        "canonical_pass": False,
        "outputs": [{"rel": "phase2/stage2/synth/netlist.v", "sha256": sha}],
    }))
    versions = SimpleNamespace(
        _read_index=lambda _p: {"steps": [{"id": "9", "folder": folder}]},
        _versions_root=lambda _p: tmp_path / "steps",
        _sha=lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest(),
    )
    handoff = SimpleNamespace(bound_handoff=lambda _p: {
        "verdict": "PASS", "canonical": str(netlist), "mapped": str(netlist),
        "netlist_sha256": sha, "findings": [],
    })
    monkeypatch.setitem(sys.modules, "step_output_collector", versions)
    monkeypatch.setitem(sys.modules, "synth_handoff_netlist_check", handoff)
    return runner, netlist


def test_d23_accepts_current_native_step9_handoff_without_area_budget(
        tmp_path, monkeypatch):
    runner, _ = _step9_native_state(tmp_path, monkeypatch)

    current, error = runner._current_step9_checkpoint(tmp_path)

    assert error is None
    assert current["step9_record"]["status"] == "NOT_MEASURED"
    assert current["native_handoff"]["verdict"] == "PASS"


def test_d23_rejects_step9_handoff_after_netlist_bytes_change(tmp_path, monkeypatch):
    runner, netlist = _step9_native_state(tmp_path, monkeypatch)
    netlist.write_text("module changed; endmodule\n")

    current, error = runner._current_step9_checkpoint(tmp_path)

    assert current is None
    assert "no longer matches" in error


def test_d23_rejects_wrong_l20_before_scan_or_atpg_launch(tmp_path, monkeypatch):
    import vibe_ic_one_shot_runner as runner

    mapped = tmp_path / "phase2/stage2/synth/top_synth.v"
    mapped.parent.mkdir(parents=True)
    mapped.write_text("module top(input clk); endmodule\n")
    monkeypatch.setattr(runner, "_current_step9_checkpoint",
                        lambda _p: ({"native_handoff": {"verdict": "PASS"}}, None))
    design = SimpleNamespace(
        derive_dft_clock_reset=lambda _p: ("clk", "rst", False),
        _step_dft_insertion=lambda *a, **k: pytest.fail(
            "wrong L20 must refuse before the native producer"),
    )
    atpg = SimpleNamespace(resolve_mapped_netlist=lambda *_a, **_k: (
        "phase2/stage2/synth/top_synth.v", "test"))
    scan = SimpleNamespace(
        count_flops=lambda _text: 64,
        load_l20_scan_contract=lambda *_a, **_k: (
            None, ["L20 length=63 does not match mapped-netlist flop count 64"]),
    )
    monkeypatch.setitem(sys.modules, "design_one_shot_runner", design)
    monkeypatch.setitem(sys.modules, "fault_atpg_run", atpg)
    monkeypatch.setitem(sys.modules, "fault_scan_chain_insert", scan)

    result = runner._complete_step11_dft(tmp_path, "top", "unused")

    assert result["verdict"] == "NOT_MEASURED"
    assert "length=63" in result["detail"]


def test_owner_route_uses_native_span_without_generic_qualification_registry(
        tmp_path, monkeypatch):
    # prepare_front_door intentionally installs the active request; restore
    # that environment at this control's boundary, like the older control.
    monkeypatch.setenv(flow109.CONTEXT_ENV, "")
    monkeypatch.delenv(flow109.ATTEMPT_ENV, raising=False)
    import _delivery_route
    assert _delivery_route.admit(tmp_path, "ic") is None
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"features": profile()}))
    monkeypatch.setitem(sys.modules, "flow109_admission", SimpleNamespace(
        require_admission=lambda *a, **k: pytest.fail("generic qualification must not run")))
    args = SimpleNamespace(flow="109", flow109_manifest=manifest,
                           flow109_plan_only=False, execution_mode="default",
                           entry_step="D12", exit_step="D12")
    assert flow109.prepare_front_door(args, tmp_path) is None
    assert (args.entry_step, args.exit_step) == ("2", "3")
    route = json.loads((tmp_path / "reports/flow109/owner_route.json").read_text())
    assert route["owner_runner"] == "design_one_shot_runner"
    assert route["native_step_id"] == "3"
    assert route["component_status"] == route["flow109_acceptance"] == "NOT_MEASURED"
    request = json.loads(Path(__import__("os").environ[flow109.CONTEXT_ENV]).read_text())
    assert request["requested_step"] == "D12"
    assert request["resolved_row"]["id"] == "D12"
    assert request["context_sha256"] == route["context_sha256"]


@pytest.mark.parametrize("step, adapter, component_scope", [
    ("D45", "flow109_netlist_cdc", None),
    ("D69", "flow109_final_extract", "final_filled_odb_rcx_component_only"),
    ("D70", "flow109_final_electrical", "final_sta_native_component_only"),
    ("D71", "flow109_final_gls", "final_sdf_gls_component_only"),
])
def test_registered_native_component_route_does_not_claim_generic_qualification(
        step, adapter, component_scope):
    plan = flow109.resolve_plan(profile())
    row = flow109.resolve_step(plan, step)
    dispatch = flow109.source_owned_native_dispatch(row)
    assert dispatch["adapter"] == adapter
    assert dispatch.get("component_scope") == component_scope
    context = flow109.execution_context(plan, step, step)
    admission = flow109.source_owned_native_admission(context)
    assert admission["admitted"] is True
    assert admission["status"] == "NOT_APPLICABLE"
    assert admission["qualification_status"] == "NOT_CLAIMED"
    assert admission["flow109_acceptance"] == "NOT_MEASURED"
    claimed = flow109.execution_context(plan, step, step, runtime_tool_uses=[],
                                        qualification_registry="input/qualifications.json")
    assert flow109.source_owned_native_admission(claimed) is None
    assert flow109.source_owned_native_dispatch(flow109.resolve_step(plan, "D62")) is None


def test_final_native_window_reaches_real_missing_input_refusal_without_generic_claim(
        tmp_path, monkeypatch):
    import _delivery_route
    import flow109_admission

    assert _delivery_route.admit(tmp_path, "ic") is None
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"features": profile()}))
    for name in (flow109.CONTEXT_ENV, flow109.ATTEMPT_ENV):
        monkeypatch.setenv(name, __import__("os").environ.get(name, ""))
        monkeypatch.delenv(name)
    monkeypatch.setattr(
        flow109_admission, "require_admission",
        lambda *a, **k: pytest.fail("generic qualification must remain unclaimed"))
    args = SimpleNamespace(flow="109", flow109_manifest=manifest,
                           flow109_plan_only=False, execution_mode="default",
                           entry_step="D69", exit_step="D71")

    assert flow109.prepare_front_door(args, tmp_path) == 2
    d69 = json.loads((tmp_path / "reports/flow109/components/D69.json").read_text())
    assert d69["component_status"] == "NOT_MEASURED"
    assert d69["reason"] == "D69_REQUIRED_INPUT_ABSENT"
    assert d69["native_started"] is False
    assert not (tmp_path / "reports/flow109/components/D70.json").exists()
    assert not (tmp_path / "reports/flow109/components/D71.json").exists()
    attempt = next((tmp_path / "reports/flow109/attempts").iterdir())
    terminal = json.loads((attempt / "result.json").read_text())
    assert terminal["component_status"] == "NOT_MEASURED"
    assert terminal["flow109_acceptance"] == "NOT_MEASURED"
    assert terminal["whole_flow_acceptance"] == "NOT_MEASURED"


@pytest.mark.parametrize("step, reason", [
    ("D69", "D69_REQUIRED_INPUT_ABSENT"),
    ("D70", "D70_REQUIRED_INPUT_ABSENT"),
    ("D71", "D71_INPUT_INVALID"),
])
def test_final_native_single_entry_reaches_own_missing_input_refusal(
        tmp_path, monkeypatch, step, reason):
    import _delivery_route
    import flow109_admission

    assert _delivery_route.admit(tmp_path, "ic") is None
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"features": profile()}))
    for name in (flow109.CONTEXT_ENV, flow109.ATTEMPT_ENV):
        monkeypatch.setenv(name, __import__("os").environ.get(name, ""))
        monkeypatch.delenv(name)
    monkeypatch.setattr(
        flow109_admission, "require_admission",
        lambda *a, **k: pytest.fail("generic qualification must remain unclaimed"))
    args = SimpleNamespace(flow="109", flow109_manifest=manifest,
                           flow109_plan_only=False, execution_mode="default",
                           entry_step=step, exit_step=step)

    assert flow109.prepare_front_door(args, tmp_path) == 2
    component = json.loads(
        (tmp_path / f"reports/flow109/components/{step}.json").read_text())
    assert component["component_status"] == "NOT_MEASURED"
    assert component["reason"] == reason
    assert component["native_started"] is False
    admission_record = json.loads(
        (tmp_path / "reports/flow109/admission.json").read_text())
    assert admission_record["status"] == "NOT_APPLICABLE"
    assert admission_record["qualification_status"] == "NOT_CLAIMED"
    assert admission_record["flow109_acceptance"] == "NOT_MEASURED"


def test_unregistered_adapter_cannot_select_existing_native_policy():
    row = flow109.resolve_step(flow109.resolve_plan(profile()), "D45")
    row["profile"]["dispatch"]["adapter"] = "caller_plugin"
    with pytest.raises(flow109.Flow109Error, match="FLOW109_NATIVE_OWNER_INVALID"):
        flow109.source_owned_native_dispatch(row)


def test_forged_backend_owner_cannot_select_existing_native_policy():
    row = flow109.resolve_step(flow109.resolve_plan(profile()), "D31")
    row["profile"]["dispatch"]["native_step_id"] = "caller-step"
    with pytest.raises(flow109.Flow109Error, match="FLOW109_NATIVE_OWNER_INVALID"):
        flow109.source_owned_native_dispatch(row)


def test_d34_clock_planning_registers_real_special_owner_and_strict_claim_path():
    import flow109_clock_plan

    plan = flow109.resolve_plan(profile())
    row = flow109.resolve_step(plan, "D34")
    dispatch = flow109.source_owned_native_dispatch(row)
    assert dispatch["native_step_id"] == "flow109.D34"
    assert dispatch["adapter"] == "flow109_clock_plan"
    assert dispatch["admission_policy"] == "source_owned_existing_native"
    assert dispatch["acceptance_scope"] == "existing_native_component_only"
    assert flow109_clock_plan._consumer_row()["id"] == 16
    sources = flow109_clock_plan.operation_sources()
    assert "programs/phase3_one_shot_runner.py" in sources
    assert "programs/_physical_current.py" in sources
    assert "programs/clock_plan_check.py" in sources
    context = flow109.execution_context(plan, "D34", "D34")
    admission = flow109.source_owned_native_admission(context)
    assert admission["status"] == "NOT_APPLICABLE"
    assert admission["qualification_status"] == "NOT_CLAIMED"
    assert admission["flow109_acceptance"] == "NOT_MEASURED"
    claimed = flow109.execution_context(
        plan, "D34", "D34", runtime_tool_uses=[],
        qualification_registry="input/qualifications.json")
    assert flow109.source_owned_native_admission(claimed) is None

    row["profile"]["dispatch"]["adapter"] = "flow109_missing_clock_owner"
    with pytest.raises(flow109.Flow109Error, match="FLOW109_NATIVE_OWNER_INVALID"):
        flow109.source_owned_native_dispatch(row)


def test_d44_retained_lec_registers_only_existing_native_admission_fields():
    plan = flow109.resolve_plan(profile())
    row = flow109.resolve_step(plan, "D44")
    dispatch = flow109.source_owned_native_dispatch(row)
    assert dispatch["adapter"] == "flow109_final_lec"
    assert dispatch["native_step_id"] == "flow109.D44"
    assert dispatch["admission_policy"] == "source_owned_existing_native"
    assert dispatch["acceptance_scope"] == "existing_native_component_only"
    context = flow109.execution_context(plan, "D44", "D44")
    admission = flow109.source_owned_native_admission(context)
    assert admission["status"] == "NOT_APPLICABLE"
    assert admission["qualification_status"] == "NOT_CLAIMED"
    assert admission["flow109_acceptance"] == "NOT_MEASURED"

    del row["profile"]["dispatch"]["acceptance_scope"]
    with pytest.raises(flow109.Flow109Error, match="FLOW109_NATIVE_OWNER_INVALID"):
        flow109.source_owned_native_dispatch(row)


def test_d63_routes_only_to_current_canonical_default35_owner():
    plan = flow109.resolve_plan(profile())
    row = flow109.resolve_step(plan, "D63")
    dispatch = flow109.source_owned_native_dispatch(row)

    assert row["profile"]["execution_status"] == "NATIVE_COMPONENT_ONLY_NOT_109_ACCEPTED"
    assert dispatch["kind"] == "canonical_backend_window"
    assert dispatch["native_step_id"] == "35"
    assert dispatch["producer"] == "execution_backend_producers.py:produce"
    assert dispatch["consumer"] == "execution_backend_gates.py:evaluate"
    assert dispatch["native_required_outputs"] == ["reports/phase3/dfm_screen.json"]
    assert dispatch["acceptance_scope"] == "existing_native_component_only"
    assert dispatch["flow109_metrics_status"] == "UNIMPLEMENTED"
    assert row["metric_acceptance_status"] == "UNIMPLEMENTED"
    assert row["gate_matrix_status"] == "NOT_MEASURED_FOR_109"

    context = flow109.execution_context(plan, "D63", "D63")
    admission = flow109.source_owned_native_admission(context)
    assert admission["admitted"] is True
    assert admission["qualification_status"] == "NOT_CLAIMED"
    assert admission["flow109_acceptance"] == "NOT_MEASURED"

    context["resolved_row"]["profile"]["dispatch"]["native_step_id"] = "34"
    with pytest.raises(flow109.Flow109Error, match="FLOW109_CONTEXT_STALE"):
        flow109.validate_execution_context(context)


def test_owner_route_refuses_wrong_owner_and_unknown_native_mapping():
    plan = flow109.resolve_plan(profile())
    row = next(item for item in plan["rows"] if item["id"] == "D12")
    row["profile"]["dispatch"]["owner_runner"] = "phase1_one_shot_runner"
    with pytest.raises(flow109.Flow109Error, match="FLOW109_OWNER_MISMATCH"):
        flow109.resolve_execution_window(plan, "D12", "D12")
    plan = flow109.resolve_plan(profile())
    row = next(item for item in plan["rows"] if item["id"] == "D12")
    row["profile"]["dispatch"]["native_step_id"] = "12"
    with pytest.raises(flow109.Flow109Error, match="FLOW109_OWNER_NATIVE_MAPPING_UNKNOWN"):
        flow109.resolve_execution_window(plan, "D12", "D12")


def test_frontend_owner_window_preserves_existing_indivisible_spans():
    window = flow109.resolve_execution_window(flow109.resolve_plan(profile()), "D09", "D14")
    assert window["dispatch_kind"] == "canonical_owner_window"
    assert [row["id"] for row in window["resolved_rows"]] == [
        "D09", "D10", "D11", "D12", "D13", "D14"]
    assert (window["native_entry_step"], window["native_exit_step"]) == ("1", "5")
