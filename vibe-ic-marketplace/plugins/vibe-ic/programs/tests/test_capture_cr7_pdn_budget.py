"""Small-core PDN pitch and built-metal controls on a neutral logic tile."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

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
    # Both rails declared, zero metal: the budget's per-layer shortfall stays
    # advisory, but a declared rail with no stripe is never a clean grid.
    rc, report = _check(_project(tmp_path, 0, mode="advisory"))
    assert rc == 2 and report["verdict"] == "NOT_MEASURED"
    finding = next(x for x in report["findings"]
                   if x["rule"] == "PDN_DEF_STRAP_SHORTFALL")
    assert finding["severity"] == "ADVISORY"


def test_advisory_candidate_single_declared_rail_without_metal_is_not_clean(tmp_path):
    project = _project(tmp_path, 0, mode="advisory")
    path = project / "phase3/stage3/pnr/floorplan.def"
    path.write_text(path.read_text().replace("+ USE GROUND", "+ USE SIGNAL"))
    rc, report = _check(project)
    assert report["def_stripe_census"]["declared_roles"] == ["POWER"]
    assert report["def_stripe_census"]["count"] == 0
    assert rc == 2 and report["verdict"] == "NOT_MEASURED"


def test_advisory_candidate_group_shortfall_on_built_rails_stays_advisory(tmp_path):
    # One built wire per rail, three groups planned: disclosed, not blocking.
    rc, report = _check(_project(tmp_path, 2, mode="advisory"))
    assert rc == 0 and report["verdict"] == "PASS"
    finding = next(x for x in report["findings"]
                   if x["rule"] == "PDN_DEF_STRAP_SHORTFALL")
    assert finding["severity"] == "ADVISORY"


# ---------------------------------------------------------------------------
# The census reads BUILT WIRES in the DEF that carries the built grid.
# ---------------------------------------------------------------------------
def _stripe_vias(project: Path, per_rail: int = 5) -> None:
    """Add OpenROAD's stripe-crossing via paths: one point, a via name, and
    (as written) width 0 -- plus the same with a nonzero width, and a width-0
    two-point path. None of them is a strap wire."""
    path = project / "phase3/stage3/pnr/floorplan.def"
    body = path.read_text()
    for role in ("POWER", "GROUND"):
        vias = "".join(
            f"      NEW M5 0 + SHAPE STRIPE ( {20000 + i * 1000} 50000 ) via4_5_1000_1000\n"
            f"      NEW M4 0 + SHAPE STRIPE ( {20000 + i * 1000} 60000 ) via4_5_1000_1000\n"
            for i in range(per_rail))
        vias += ("      NEW M5 1000 + SHAPE STRIPE ( 30000 70000 ) via4_5_1000_1000\n"
                 "      NEW M5 0 + SHAPE STRIPE ( 30000 1000 ) ( 30000 99000 )\n")
        body = body.replace(f"      + USE {role} ;", f"{vias}      + USE {role} ;")
    path.write_text(body)


def test_stripe_crossing_vias_are_not_counted_as_straps(tmp_path):
    project = _project(tmp_path, 6)
    _stripe_vias(project)
    rc, report = _check(project)
    census = report["def_stripe_census"]
    assert census["by_role_layer"] == {"POWER": {"m5": 3}, "GROUND": {"m5": 3}}
    assert census["count"] == 6
    assert census["stripe_vias_not_counted"] == 2 * (2 * 5 + 2)
    assert rc == 0 and report["verdict"] == "PASS"


def test_vias_cannot_fill_an_apply_mode_group_shortfall(tmp_path):
    # One real wire per rail and five crossing vias on the stripe layer: only
    # one of the three planned groups is built.
    project = _project(tmp_path, 2)
    _stripe_vias(project)
    rc, report = _check(project)
    assert report["def_stripe_census"]["by_role_layer"] == {
        "POWER": {"m5": 1}, "GROUND": {"m5": 1}}
    assert rc == 1 and report["verdict"] == "FAIL"
    assert "PDN_DEF_STRAP_SHORTFALL" in {x["rule"] for x in report["findings"]}


_DIRECT_DECK = """\
# pdngen
read_verilog /work/netlist.v
link_design logic_tile
initialize_floorplan -die_area {0 0 100 100} -site SITE
write_def /work/phase3/stage3/pnr/floorplan.def
tapcell -distance 14
if {[catch {
  define_pdn_grid -name grid
  add_pdn_stripe -grid grid -layer M5 -width 1 -pitch 38.4 -offset 2
  pdngen
} _pdn_err]} {
  puts "PDN_NONFATAL: $_pdn_err"
}
# write_def /work/phase3/stage3/pnr/cts.def
global_placement -density 0.5
write_def /work/phase3/stage3/pnr/placed.def
write_def /work/phase3/stage3/pnr/routed.def
"""


def test_direct_deck_census_reads_the_def_written_after_pdngen(tmp_path):
    # The direct deck writes floorplan.def BEFORE tapcell/pdngen: it holds no
    # stripe. The grid pdngen built is first on disk in placed.def.
    built = _project(tmp_path / "built", 6)
    project = _project(tmp_path, 0)
    pnr = project / "phase3/stage3/pnr"
    (pnr / "placed.def").write_text(
        (built / "phase3/stage3/pnr/floorplan.def").read_text())
    (pnr / "pnr.tcl").write_text(_DIRECT_DECK)
    rc, report = _check(project)
    census = report["def_stripe_census"]
    assert Path(census["source"]).name == "placed.def"
    assert census["by_role_layer"] == {"POWER": {"m5": 3}, "GROUND": {"m5": 3}}
    assert rc == 0 and report["verdict"] == "PASS"
    # No DEF after pdngen on disk: the requirement on built metal is not
    # measured -- neither a pass nor a fail read off the pre-PDN floorplan.
    (pnr / "placed.def").unlink()
    rc, report = _check(project)
    finding = next(x for x in report["findings"]
                   if x["rule"] == "PDN_DEF_STRAPS_NOT_MEASURED")
    assert finding["severity"] == "NOT_MEASURED"
    assert rc == 2 and report["verdict"] == "NOT_MEASURED"


def test_deck_without_pdngen_census_reads_the_handed_over_floorplan(tmp_path):
    # LibreLane step 15: the consumer deck reads the tool's floorplan.def, which
    # already carries the grid; the deck itself runs no pdngen.
    project = _project(tmp_path, 6)
    empty = _project(tmp_path / "empty", 0)
    pnr = project / "phase3/stage3/pnr"
    (pnr / "placed.def").write_text(
        (empty / "phase3/stage3/pnr/floorplan.def").read_text())
    (pnr / "pnr.tcl").write_text(
        "read_def -floorplan_initialize /work/phase3/stage3/pnr/floorplan.def\n"
        "# step 15 (taps, supply connect, PDN): LibreLane state\n"
        "global_placement -density 0.5\n"
        "write_def /work/phase3/stage3/pnr/placed.def\n")
    rc, report = _check(project)
    assert Path(report["def_stripe_census"]["source"]).name == "floorplan.def"
    assert rc == 0 and report["verdict"] == "PASS"


def test_librelane_floorplan_refuses_an_applied_budget_override(tmp_path, monkeypatch):
    import librelane_contract as ll

    def stop(*_a, **_k):
        raise ll.Refusal("UNIT_IMAGE_STOP", "the unit stops at image resolution")
    monkeypatch.setattr(ll, "resolve_image", stop)
    override = {"Metal4": {"width": 1.6, "pitch": 20.0, "offset": 2.0}}
    both = {"15": "librelane", "15.5ic": "librelane"}
    result, consumer = runner._prepare_librelane_floorplan_for_route(
        tmp_path, None, "", tmp_path / "pnr", "", both, strap_override=override)
    assert consumer is None and result.status == "FAIL"
    assert result.extras["finding"] == "PDN_BUDGET_OVERRIDE_NOT_WIRED"
    # Without the override, or with step 15 direct (its deck draws the grid),
    # the same call proceeds to the chain.
    for modes, ov in ((both, None),
                      ({"15": "direct", "15.5ic": "librelane"}, override)):
        result, _ = runner._prepare_librelane_floorplan_for_route(
            tmp_path, None, "", tmp_path / "pnr", "", modes, strap_override=ov)
        assert result.extras["finding"] == "UNIT_IMAGE_STOP"


# ---------------------------------------------------------------------------
# The budget override never loosens the EM remedy, and the record says so.
# ---------------------------------------------------------------------------
_NEUTRAL_CELL_LEF = """\
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
"""


def _neutral_pdk(tmp_path: Path):
    tech = tmp_path / "neutral.tlef"
    tech.write_text("MANUFACTURINGGRID 0.005 ;\n" + "".join(
        f"LAYER M{i}\n TYPE ROUTING ;\n DIRECTION "
        f"{'HORIZONTAL' if i % 2 else 'VERTICAL'} ;\n"
        " PITCH 0.5 ;\n WIDTH 0.2 ;\n SPACING 0.3 ;\n"
        f"END M{i}\n" for i in range(1, 6)))
    cell = tmp_path / "cell.lef"
    cell.write_text(_NEUTRAL_CELL_LEF)
    return SimpleNamespace(tapcell_master=None, metal_prefix="M",
                           cell_lef=str(cell), tech_lef=str(tech),
                           pdn_straps={"stripes": [
                               {"layer": "M4", "width": 1.6,
                                "pitch": 153.58, "offset": 38.395}],
                               "connects": [["M1", "M4"]]},
                           pdn_ring=None)


def _more_stripes_floor():
    # 1.2 mA worst segment: 3x the stripes at the drawn width meet Jmax.
    return {"per_layer": {"m4": {"w_em_um": 3.98, "jmax_A_per_um": 0.00067}},
            "max_segment_current_A": 1.2e-3, "margin": 0.1,
            "safety_factor": 2.0, "manufacturing_grid_um": 0.005}


def _wider_strap_floor():
    # A measured total current: the conservation bound widens the strap.
    return {"per_layer": {"m4": {"w_em_um": 10.97, "jmax_A_per_um": 0.00067}},
            "max_segment_current_A": 0.003305, "i_total_A": 0.00724,
            "margin": 0.1, "safety_factor": 2.0,
            "manufacturing_grid_um": 0.005}


def _m4(floor):
    return next(a for a in floor["applied"] if a["layer"] == "M4")


def _m4_line(tcl):
    return next(ln for ln in tcl.splitlines() if "-layer M4 " in ln)


def test_sparser_budget_pitch_cannot_undo_the_em_stripe_remedy(tmp_path):
    pdk = _neutral_pdk(tmp_path)
    em_only = _more_stripes_floor()
    runner._build_pdn_tcl(pdk, em_floor=em_only)
    em = _m4(em_only)
    assert em["verdict"] == "MORE_STRIPES" and em["pitch_um"] < 100.0
    floor = _more_stripes_floor()
    tcl = runner._build_pdn_tcl(pdk, em_floor=floor, strap_override={
        "M4": {"width": 1.6, "pitch": 100.0, "offset": 25.0}})
    line = _m4_line(tcl)
    assert f"-pitch {em['pitch_um']} " in line and "-pitch 100.0" not in line
    row = _m4(floor)
    assert f"-width {row['width_um']} -pitch {row['pitch_um']} " in line
    assert row["pitch_source"] == "em_remedy"
    assert row["budget_override"]["pitch_um"] == 100.0


def test_denser_budget_pitch_is_recorded_as_drawn_beside_the_em_remedy(tmp_path):
    pdk = _neutral_pdk(tmp_path)
    em_only = _more_stripes_floor()
    runner._build_pdn_tcl(pdk, em_floor=em_only)
    floor = _more_stripes_floor()
    tcl = runner._build_pdn_tcl(pdk, em_floor=floor, strap_override={
        "M4": {"width": 1.6, "pitch": 30.0, "offset": 2.0}})
    assert "-layer M4 -width 1.6 -pitch 30.0 -offset 2.0" in _m4_line(tcl)
    row = _m4(floor)
    # pdn_em_sizing.json must name the pitch the deck drew, not the EM one.
    assert row["pitch_um"] == 30.0 and row["pitch_source"] == "budget_override"
    assert row["em_remedy"]["pitch_um"] == _m4(em_only)["pitch_um"]


def test_budget_override_keeps_the_em_width_or_refuses_the_pair(tmp_path):
    pdk = _neutral_pdk(tmp_path)
    em_only = _wider_strap_floor()
    runner._build_pdn_tcl(pdk, em_floor=em_only)
    em = _m4(em_only)
    assert em["verdict"] == "WIDER_STRAP"
    width, pitch = em["width_um"], em["pitch_um"]
    assert 4 * width < 100.0 < pitch      # legal at the EM width, and denser
    floor = _wider_strap_floor()
    tcl = runner._build_pdn_tcl(pdk, em_floor=floor, strap_override={
        "M4": {"width": 1.6, "pitch": 100.0, "offset": 25.0}})
    assert f"-layer M4 -width {width} -pitch 100.0 -offset 25.0" in _m4_line(tcl)
    assert _m4(floor)["width_um"] == width
    # A budget pitch the EM width cannot be built at is refused by name.
    with pytest.raises(ValueError, match="PDN_BUDGET_EM_CONFLICT"):
        runner._build_pdn_tcl(pdk, em_floor=_wider_strap_floor(), strap_override={
            "M4": {"width": 1.6, "pitch": round(2 * width, 3), "offset": 2.0}})


def test_tuned_grid_override_keeps_the_em_width(tmp_path):
    pdk = SimpleNamespace(tapcell_master="sky130_fd_sc_hd__tapvpwrvgnd_1",
                          tech_lef=None, cell_lef=None, pdn_straps=None)
    override = {"met4": {"width": 1.6, "pitch": 20.0, "offset": 2.0}}
    plain = runner._build_pdn_tcl(pdk, None, strap_override=override)
    assert "-layer met4 -width 1.6 -pitch 20.0 -offset 2.0" in plain
    widened = runner._build_pdn_tcl(pdk, None, strap_override=override, em_floor={
        "per_layer": {"met4": {"w_em_um": 3.23}}})
    assert "-layer met4 -width 3.23 -pitch 20.0 -offset 2.0" in widened
