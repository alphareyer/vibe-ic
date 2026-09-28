"""Small-core PDN pitch and built-metal controls on a neutral logic tile."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import floorplan_pdn_check as gate
import phase3_one_shot_runner as runner


def _planner():
    source = PROGRAMS / "_ppa/pdn_small_core.py"
    if not source.is_file():
        return None
    spec = importlib.util.spec_from_file_location("capture_pdn_small_core", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _candidate(current=2.7, pitch=150.0):
    module = _planner()
    if module is None:
        return {"verdict": "NOT_MEASURED", "override": {}}
    return module.plan(
        [{"layer": "M5", "width": 1.0, "pitch": pitch, "offset": 2.0}],
        core_w_um=100.0, core_h_um=100.0, current_A=current,
        jmax_A_per_um={"m5": 1.0}, ir_budget_v=0.3,
        sheet_ohm_per_sq={"m5": 0.001}, spacing_um={"m5": 0.5},
        directions={"M5": "VERTICAL"}, site_dims_um=(1.0, 1.0))


def _project(tmp_path: Path, n_shapes: int, *, mode="apply") -> Path:
    project = tmp_path / "logic_tile"
    pnr = project / "phase3/stage3/pnr"
    pnr.mkdir(parents=True)
    stripes = ""
    for net, x0 in (("VDD", 10000), ("VSS", 15000)):
        shapes = "\n".join(
            f"      NEW M5 1000 + SHAPE STRIPE ( {x0 + i*10000} 1000 ) "
            f"( {x0 + i*10000} 99000 )" for i in range(n_shapes // 2))
        role = "POWER" if net == "VDD" else "GROUND"
        stripes += f"    - {net} ( c1 {net} )\n{shapes}\n      + USE {role} ;\n"
    (pnr / "floorplan.def").write_text(
        "VERSION 5.8 ;\nDESIGN logic_tile ;\n"
        "UNITS DISTANCE MICRONS 1000 ;\n"
        "DIEAREA ( 0 0 ) ( 100000 100000 ) ;\n"
        "ROW R0 SITE 1000 1000 N DO 10 BY 1 STEP 1000 0 ;\n"
        "COMPONENTS 1 ;\n - c1 NAND2 ;\nEND COMPONENTS\n"
        f"SPECIALNETS 2 ;\n{stripes}END SPECIALNETS\nEND DESIGN\n")
    (pnr / "pnr.tcl").write_text(
        "define_pdn_grid -name grid\n"
        "add_pdn_stripe -grid grid -layer M5 -width 1 -pitch 38.4 -offset 2\n"
        "pdngen\n")
    report = project / "reports/phase3"
    report.mkdir(parents=True)
    (report / "floorplan_rectangles.json").write_text(json.dumps({
        "pdn_core_floor": {"budget_pitch": {
            "verdict": "CANDIDATE", "mode": mode,
            "rows": [{"layer": "M5", "required_groups": 3}]}}}))
    return project


def _check(project: Path):
    output = project / "gate.json"
    rc = gate.main([str(project), "--json", str(output)])
    report = json.loads(output.read_text())
    return rc, report


def test_declared_budget_fits_three_groups_without_core_growth():
    result = _candidate()
    assert result["verdict"] == "CANDIDATE"
    row = result["rows"][0]
    assert row["required_groups"] == 3
    assert row["small_core"] is True
    assert 4.0 <= row["chosen_pitch_um"] < 150.0
    assert result["override"]["M5"]["pitch"] == row["chosen_pitch_um"]
    # Reverse the declared current: the same design no longer owes this pitch.
    assert _candidate(current=None)["verdict"] == "NOT_MEASURED"


def test_technology_ir_limit_can_require_a_fourth_group(tmp_path):
    module = _planner()
    project = tmp_path / "logic_tile"
    policy = project / "input/pdn_budget_pitch_policy.json"
    policy.parent.mkdir(parents=True)
    policy.write_text('{"mode":"apply"}')
    tech = """\
LAYER M5
 TYPE ROUTING ;
 DIRECTION VERTICAL ;
 WIDTH 1 ;
 SPACING 0.5 ;
 RESISTANCE RPERSQ 0.004 ;
 DCCURRENTDENSITY AVERAGE 1000 ;
END M5
"""
    detail = {"stripes": [{"layer": "M5", "width": 1.0,
                            "pitch": 150.0, "offset": 2.0}],
              "directions": {"M5": "VERTICAL"},
              "site_dims_um": [1.0, 1.0], "routing_budget": 0.5}
    result = module.plan_for_core(project, tech, 100.0, detail,
                                  2.7, "declared", 1.0, 30.0)
    assert result["mode"] == "apply"
    assert result["verdict"] == "CANDIDATE"
    assert result["rows"][0]["em_groups"] == 3
    assert result["rows"][0]["ir_groups"] == 4
    assert result["rows"][0]["required_groups"] == 4
    # Removing the technology's resistance makes the requested IR bound
    # unmeasured; it must not silently revert to an EM-only apply decision.
    assert module.plan_for_core(project, tech.replace(
        "RESISTANCE RPERSQ 0.004 ;", ""), 100.0, detail,
        2.7, "declared", 1.0, 30.0)["verdict"] == "NOT_MEASURED"


def test_floor_generator_consumes_budget_pitch_and_preserves_default(tmp_path):
    tech = tmp_path / "tech.lef"
    tech.write_text("""\
LAYER M1
 TYPE ROUTING ;
 DIRECTION HORIZONTAL ;
 PITCH 1 ;
 WIDTH 0.3 ;
END M1
LAYER M5
 TYPE ROUTING ;
 DIRECTION VERTICAL ;
 PITCH 1 ;
 WIDTH 1 ;
END M5
""")
    cells = tmp_path / "cells.lef"
    cells.write_text("""\
MACRO tile_cell
 CLASS core ;
 SIZE 2 BY 10 ;
 PIN VDD
  DIRECTION INOUT ;
  USE POWER ;
  PORT
   LAYER M1 ;
    RECT 0 9.6 2 10.4 ;
  END
 END VDD
 PIN VSS
  DIRECTION INOUT ;
  USE GROUND ;
  PORT
   LAYER M1 ;
    RECT 0 -0.4 2 0.4 ;
  END
 END VSS
END tile_cell
""")
    pdk = runner.PdkConfig(
        name="unit", liberty="/nonexistent/lib.lib", tech_lef=str(tech),
        cell_lef=str(cells), cell_gds=None, site="SITE", drc_deck=None,
        metal_prefix="M", tapcell_master=None,
        pdn_straps={"stripes": [{"layer": "M5", "width": 1.0,
                                  "pitch": 240.0, "offset": 2.0}],
                     "connects": [{"layers": ["M1", "M5"]}]})
    detail = {}
    old_floor, _ = runner._strap_plan_core_floor(pdk, detail=detail)
    candidate = _candidate(pitch=240.0)
    new_detail = {}
    new_floor, _ = runner._strap_plan_core_floor(
        pdk, detail=new_detail, strap_override=candidate["override"])
    assert old_floor > 100
    assert new_floor <= 100
    assert detail["stripes"][0]["pitch"] == 240.0
    assert new_detail["stripes"][0]["pitch"] < 240.0


def test_tcl_intent_with_zero_built_straps_fails_when_applied(tmp_path):
    project = _project(tmp_path, 0)
    path = project / "phase3/stage3/pnr/floorplan.def"
    path.write_text(path.read_text().replace(
        "SPECIALNETS 2 ;", "SPECIALNETS 2 ;\n# not SHAPE STRIPE"))
    rc, report = _check(project)
    assert rc == 1
    assert report["verdict"] == "FAIL"
    assert "PDN_DEF_STRAP_SHORTFALL" in {x["rule"] for x in report["findings"]}
    assert report["def_stripe_census"]["count"] == 0


def test_built_three_group_grid_passes_and_reverse_mutation_fails(tmp_path):
    project = _project(tmp_path, 6)
    rc, report = _check(project)
    assert rc == 0
    assert report["verdict"] == "PASS"
    assert report["def_stripe_census"]["count"] == 6
    # Remove actual metal while leaving the apparently correct Tcl untouched.
    body = (project / "phase3/stage3/pnr/floorplan.def").read_text()
    (project / "phase3/stage3/pnr/floorplan.def").write_text(
        body.replace("SHAPE STRIPE", "SHAPE FOLLOWPIN"))
    assert _check(project)[0] == 1


def test_declared_power_roles_count_with_arbitrary_net_names(tmp_path):
    project = _project(tmp_path, 6)
    path = project / "phase3/stage3/pnr/floorplan.def"
    body = path.read_text().replace("- VDD ( c1 VDD )", "- SUPPLY_A ( c1 SUPPLY_A )")
    body = body.replace("- VSS ( c1 VSS )", "- RETURN_A ( c1 RETURN_A )")
    path.write_text(body)
    rc, report = _check(project)
    assert rc == 0 and report["verdict"] == "PASS"
    census = report["def_stripe_census"]
    assert census["count"] == 6
    assert census["by_role_layer"] == {"POWER": {"m5": 3},
                                       "GROUND": {"m5": 3}}


def test_multiline_special_wire_shape_counts_by_role_and_layer(tmp_path):
    project = _project(tmp_path, 6)
    path = project / "phase3/stage3/pnr/floorplan.def"
    path.write_text(path.read_text().replace(
        "NEW M5 1000 + SHAPE STRIPE",
        "NEW M5 1000\n      + SHAPE STRIPE"))
    rc, report = _check(project)
    assert rc == 0 and report["verdict"] == "PASS"
    assert report["def_stripe_census"]["by_role_layer"] == {
        "POWER": {"m5": 3}, "GROUND": {"m5": 3}}


def test_apply_policy_rejects_unresolved_pitch_budget(tmp_path):
    project = _project(tmp_path, 2)
    policy = project / "input/pdn_budget_pitch_policy.json"
    policy.parent.mkdir(parents=True)
    policy.write_text('{"mode":"apply"}')
    report_path = project / "reports/phase3/floorplan_rectangles.json"
    reason = "M5: IR sheet resistance/budget absent"
    report_path.write_text(json.dumps({"pdn_core_floor": {"budget_pitch": {
        "mode": "apply", "verdict": "NOT_MEASURED", "reason": reason}}}))
    rc, report = _check(project)
    assert rc == 2 and report["verdict"] == "NOT_MEASURED"
    finding = next(f for f in report["findings"]
                   if f["rule"] == "PDN_BUDGET_PITCH_UNRESOLVED")
    assert reason in finding["message"]


def test_apply_policy_rejects_infeasible_or_incomplete_candidate(tmp_path):
    project = _project(tmp_path, 2)
    policy = project / "input/pdn_budget_pitch_policy.json"
    policy.parent.mkdir(parents=True)
    policy.write_text('{"mode":"apply"}')
    report_path = project / "reports/phase3/floorplan_rectangles.json"
    report_path.write_text(json.dumps({"pdn_core_floor": {"budget_pitch": {
        "mode": "apply", "verdict": "INFEASIBLE",
        "reason": "M5: no legal pitch"}}}))
    rc, report = _check(project)
    assert rc == 1 and report["verdict"] == "FAIL"
    assert "M5: no legal pitch" in next(f["message"] for f in report["findings"]
                                    if f["rule"] == "PDN_BUDGET_PITCH_UNRESOLVED")
    report_path.write_text(json.dumps({"pdn_core_floor": {"budget_pitch": {
        "mode": "apply", "verdict": "CANDIDATE", "rows": []}}}))
    rc, report = _check(project)
    assert rc == 2 and report["verdict"] == "NOT_MEASURED"
    assert "PDN_BUDGET_PITCH_UNRESOLVED" in {f["rule"] for f in report["findings"]}


def test_signal_stripes_without_budget_cannot_certify_pdn(tmp_path):
    project = _project(tmp_path, 2)
    path = project / "phase3/stage3/pnr/floorplan.def"
    path.write_text(path.read_text().replace("+ USE POWER", "+ USE SIGNAL")
                    .replace("+ USE GROUND", "+ USE SIGNAL"))
    (project / "reports/phase3/floorplan_rectangles.json").write_text("{}")
    rc, report = _check(project)
    assert rc == 2 and report["verdict"] == "NOT_MEASURED"
    assert report["def_stripe_census"]["count"] == 0
    assert "PDN_DEF_STRAP_ROLE_NOT_PG" in {f["rule"] for f in report["findings"]}


def test_no_budget_still_requires_both_supply_roles(tmp_path):
    project = _project(tmp_path, 6)
    path = project / "phase3/stage3/pnr/floorplan.def"
    path.write_text(path.read_text().replace("+ USE GROUND", "+ USE POWER"))
    (project / "reports/phase3/floorplan_rectangles.json").write_text("{}")
    rc, report = _check(project)
    assert rc == 2 and report["verdict"] == "NOT_MEASURED"
    assert report["def_stripe_census"]["by_role_layer"]["GROUND"] == {}
    assert "PDN_DEF_STRAP_SHORTFALL" in {f["rule"] for f in report["findings"]}


def test_no_budget_zero_stripes_on_declared_supply_pair_is_not_measured(tmp_path):
    project = _project(tmp_path, 0)
    (project / "reports/phase3/floorplan_rectangles.json").write_text("{}")
    rc, report = _check(project)
    assert rc == 2 and report["verdict"] == "NOT_MEASURED"
    assert report["def_stripe_census"]["count"] == 0
    assert "PDN_DEF_STRAP_SHORTFALL" in {f["rule"] for f in report["findings"]}


def test_no_budget_zero_stripes_on_one_declared_supply_is_not_clean(tmp_path):
    project = _project(tmp_path, 0)
    path = project / "phase3/stage3/pnr/floorplan.def"
    path.write_text(path.read_text().replace("+ USE GROUND", "+ USE SIGNAL"))
    (project / "reports/phase3/floorplan_rectangles.json").write_text("{}")
    rc, report = _check(project)
    census = report["def_stripe_census"]
    assert census["status"] == "MEASURED"
    assert census["count"] == 0
    assert census["declared_roles"] == ["POWER"]
    assert "PDN_DEF_STRAP_SHORTFALL" in {f["rule"] for f in report["findings"]}
    assert rc == 2 and report["verdict"] == "NOT_MEASURED"


def test_six_stripes_on_one_rail_do_not_count_as_three_groups(tmp_path):
    project = _project(tmp_path, 6)
    path = project / "phase3/stage3/pnr/floorplan.def"
    body = path.read_text()
    body = body.replace("- VSS ( c1 VSS )", "- VDD2 ( c1 VDD )")
    body = body.replace("+ USE GROUND ;", "+ USE POWER ;")
    path.write_text(body)
    rc, report = _check(project)
    assert rc == 1
    assert report["def_stripe_census"]["count"] == 6
    assert "PDN_DEF_STRAP_SHORTFALL" in {x["rule"] for x in report["findings"]}


def test_advisory_mode_discloses_existing_zero_strap_cell(tmp_path):
    rc, report = _check(_project(tmp_path, 0, mode="advisory"))
    assert rc == 0
    finding = next(x for x in report["findings"]
                   if x["rule"] == "PDN_DEF_STRAP_SHORTFALL")
    assert finding["severity"] == "ADVISORY"
