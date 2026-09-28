"""W5b producer: the segment-2 flow actually contains the kept plugin steps."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import librelane_whole_flow as W  # noqa: E402
import librelane_contract as LC  # noqa: E402


@pytest.fixture
def project(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    layout = project / "core.nl.v"
    layout.write_text("module core(input clk); endmodule\n")
    sdc = project / "clock.sdc"
    sdc.write_text("create_clock -period 10 [get_ports clk]\n")

    def emit(_project, _pdk, out):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("{}\n")
        out.with_suffix(".provenance.json").write_text("{}\n")
        return {}

    monkeypatch.setattr(LC, "emit_config", emit)
    monkeypatch.setattr(LC, "librelane_flow", lambda _project: ("Chip", "DIE"))
    monkeypatch.setattr(LC, "apply_flow_die", lambda *args: None)
    monkeypatch.setattr(LC, "_apply_layout_top", lambda *args: None)
    monkeypatch.setattr(W, "ignore_disconnected_masters", lambda *args: ([], ""))
    return project, layout, sdc


def test_segment2_declares_both_kept_steps_and_the_operator_spare_density(project):
    root, layout, sdc = project
    out = W.segment2_config(root, "processA", root / "segment2.json",
                            layout_netlist=layout, sdc=sdc, sdc_source="step 7",
                            spare_density=.037)
    got = json.loads(out.read_text())
    assert got["meta"] == {"flow": "Chip", "substituting_steps": {
        "+OpenROAD.DetailedPlacement": "Vibeic.InsertSpareCells",
        "+OpenROAD.DetailedRouting": "Vibeic.PostRouteRepair"}}
    assert got["VIBEIC_SPARE_DENSITY"] == .037
    assert "ClockPathDriveSizing" not in json.dumps(got)
    assert "NamedViolationReroute" not in json.dumps(got)
    provenance = json.loads(out.with_suffix(".provenance.json").read_text())
    assert "--spare-density" in provenance["VIBEIC_SPARE_DENSITY"]


def test_the_real_invocation_uses_meta_flow_and_mounts_the_plugin(project):
    root, layout, sdc = project
    out = W.segment2_config(root, "processA", root / "segment2.json",
                            layout_netlist=layout, sdc=sdc, sdc_source="step 7")
    argv = W.librelane_argv(root, "img", out, flow="Chip", tag="segment2",
                            pdk="processA", pdk_root=root, scl="libA")
    assert "--flow" not in argv
    assert "PYTHONPATH=" + str(LC.PLUGIN_ROOT.resolve()) in argv
    assert f"{LC.PLUGIN_ROOT.parent.resolve()}:{LC.PLUGIN_ROOT.parent.resolve()}:ro" in argv
    assert argv[-1] == str(out.resolve())


def test_default_invocation_still_uses_plain_flow_without_plugin(project):
    root, _layout, _sdc = project
    out = root / "default.json"
    out.write_text("{}\n")
    argv = W.librelane_argv(root, "img", out, flow="Chip", tag="default",
                            pdk="processA", pdk_root=root, scl="libA")
    assert argv[argv.index("--flow") + 1] == "Chip"
    assert not any(arg.startswith("PYTHONPATH=") for arg in argv)


def test_conflicting_meta_flow_refuses_before_tool_execution(project):
    root, layout, sdc = project
    out = W.segment2_config(root, "processA", root / "segment2.json",
                            layout_netlist=layout, sdc=sdc, sdc_source="step 7")
    with pytest.raises(LC.Refusal) as exc:
        W.librelane_argv(root, "img", out, flow="Classic", tag="segment2",
                         pdk="processA", pdk_root=root, scl="libA")
    assert exc.value.code == "LL_WHOLE_FLOW_META_CONFLICT"


def test_a_changed_custom_step_plan_refuses_before_tool_execution(project):
    root, layout, sdc = project
    out = W.segment2_config(root, "processA", root / "segment2.json",
                            layout_netlist=layout, sdc=sdc, sdc_source="step 7")
    config = json.loads(out.read_text())
    config["meta"]["substituting_steps"].pop("+OpenROAD.DetailedRouting")
    out.write_text(json.dumps(config))
    with pytest.raises(LC.Refusal) as exc:
        W.librelane_argv(root, "img", out, flow="Chip", tag="segment2",
                         pdk="processA", pdk_root=root, scl="libA")
    assert exc.value.code == "LL_WHOLE_FLOW_STEPS_CONFLICT"


@pytest.mark.parametrize("repair_changes_route", [False, True])
def test_w6_imports_the_actual_custom_step_outputs(tmp_path, repair_changes_route):
    """The W7b consumer sees the placement and route that the whole flow used."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_llv1_w6_librelane_import import (  # noqa: E402
        _add_steps, _project, _rows_of, RUN_REL,
    )
    import librelane_import as LI  # noqa: E402

    subject = _project(tmp_path)
    run = subject / RUN_REL
    placed = (run / "34-openroad-detailedplacement/spm.def").read_text()
    routed = (run / "44-openroad-detailedrouting/spm.def").read_text()
    _add_steps(subject, "'OpenROAD.CTS'", [
        ("Vibeic.InsertSpareCells", "34a-vibeic-insertsparecells",
         "Vibeic.InsertSpareCells", {
             "spm.def": placed + "\n# W5b placed spares\n",
             "spm.odb": "spares placed",
             "spare_cells.json": '{"measured":{"inserted":2}}\n',
             "openroad-insertsparecells.log": "spare insertion completed\n"})])
    _add_steps(subject, "'Odb.RemoveRoutingObstructions'", [
        ("Vibeic.PostRouteRepair", "44a-vibeic-postrouterepair",
         "Vibeic.PostRouteRepair", {
             "spm.def": routed + "\n# W5b repaired route\n",
             "spm.odb": "repaired route",
             "openroad-postrouterepair.log": "post-route repair completed\n"})])
    repair = run / "44a-vibeic-postrouterepair/state_out.json"
    if not repair_changes_route:
        state = json.loads(repair.read_text())
        original = json.loads((run / "44-openroad-detailedrouting/state_out.json").read_text())
        state["def"], state["odb"] = original["def"], original["odb"]
        repair.write_text(json.dumps(state))
    doc = LI.import_run(subject, run)
    rows = {r["canonical_path"]: r for r in _rows_of(doc)}
    place_row = rows["phase3/stage3/pnr/placed.def"]
    assert place_row["step_id"] == "18"
    assert place_row["tool_step_id"] == "Vibeic.InsertSpareCells"
    assert (subject / "phase3/stage3/pnr/placed.def").read_text().endswith(
        "# W5b placed spares\n")
    route_row = rows["phase3/stage3/pnr/routed.def"]
    assert route_row["step_id"] == ("32" if repair_changes_route else "21")
    assert route_row["tool_step_id"] == (
        "Vibeic.PostRouteRepair" if repair_changes_route
        else "OpenROAD.DetailedRouting")
    assert "reports/phase3/librelane/18/spare_cells.json" in rows
    assert "reports/phase3/librelane/32/state_out.json" in rows
