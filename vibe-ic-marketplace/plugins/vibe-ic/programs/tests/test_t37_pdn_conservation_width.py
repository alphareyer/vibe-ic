"""A one-shot EM repair must carry the measured supply current on any segment."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as R  # noqa: E402
from test_pdn_straps_and_connectivity_gate import CELL_LEF


def _pdk(tmp_path, max_width=""):
    tech = tmp_path / "neutral.tlef"
    tech.write_text("MANUFACTURINGGRID 0.005 ;\n" + "".join(
        f"LAYER M{i}\n TYPE ROUTING ;\n DIRECTION "
        f"{'HORIZONTAL' if i % 2 else 'VERTICAL'} ;\n"
        " PITCH 0.5 ;\n WIDTH 0.2 ;\n SPACING 0.3 ;\n"
        " THICKNESS 0.54 ;\n DCCURRENTDENSITY AVERAGE 0.67 ;\n"
        + (f" MAXWIDTH {max_width} ;\n" if i == 4 and max_width else "")
        + f"END M{i}\n" for i in range(1, 6)))
    cell = tmp_path / "cell.lef"
    cell.write_text(CELL_LEF)
    return SimpleNamespace(tapcell_master=None, metal_prefix="M",
                           cell_lef=str(cell), tech_lef=str(tech),
                           pdn_straps={"stripes": [
                               {"layer": "M4", "width": 1.6,
                                "pitch": 153.58, "offset": 38.395}],
                               "connects": [["M1", "M4"]]},
                           pdn_ring=None)


def _floor():
    return {"per_layer": {"m4": {"w_em_um": 10.97,
                                   "jmax_A_per_um": 0.00067}},
            "max_segment_current_A": 0.003305,
            "i_total_A": 0.00724,
            "margin": 0.1, "safety_factor": 2.0,
            "manufacturing_grid_um": 0.005}


def test_measured_supply_current_draws_a_width_that_carries_any_segment(tmp_path):
    floor = _floor()
    tcl = R._build_pdn_tcl(_pdk(tmp_path), em_floor=floor)
    applied = floor["applied"][0]
    width = applied["width_um"]
    assert width > floor["i_total_A"] / (0.00067 * 0.9)
    assert width > 10.97  # measured-segment floor alone is insufficient
    assert f"-layer M4 -width {width}" in tcl
    assert applied["verdict"] == "WIDER_STRAP"
    assert applied["stripe_multiplier"] == 1


def test_technology_maxwidth_refuses_infeasible_current_by_name(tmp_path):
    with pytest.raises(ValueError, match="PDN_EM_INFEASIBLE.*MAXWIDTH"):
        R._build_pdn_tcl(_pdk(tmp_path, max_width="5.0"), em_floor=_floor())


def test_lef_comments_and_denied_statements_cannot_set_spacing_or_maxwidth(tmp_path):
    """Only LEF grammar statements may constrain the measured PDN resize."""
    pdk = _pdk(tmp_path, max_width="20.0")
    baseline = R._build_pdn_tcl(pdk, em_floor=_floor())
    tech = Path(pdk.tech_lef)
    original = tech.read_text()
    for denial in ("not", "no", "without", "非", "不"):
        tech.write_text(original.replace(
            " SPACING 0.3 ;\n",
            " SPACING 0.3 ;\n"
            f" # {denial} SPACING 90 ;\n"
            f" # {denial} MAXWIDTH 1 ;\n"
            f" {denial} MAXWIDTH 1 ;\n"
            " MAXWIDTH 1 ; denied\n"))
        assert R._build_pdn_tcl(pdk, em_floor=_floor()) == baseline


def test_second_pass_is_blocked_by_its_own_segment_result(tmp_path, monkeypatch):
    pnr = R._pl.pnr_dir(tmp_path)
    pnr.mkdir(parents=True)
    (pnr / "unit.def").write_text("DESIGN unit ;\nEND DESIGN\n")
    peak = {"current": 1.0e-3}
    def fake_psm(project, _top, _pdk, _container, _ir, em, _notes):
        p = peak["current"]
        em.write_text(
            "segments_analysed: 2\nmax segment current: %s A\n"
            "Net : PWRNET\nTotal power : 1.0 W\nSupply voltage : 1.0 V\n"
            "Maximum current : %s A\n" % (p, p))
        rpt = R._pl.reports_phase3_dir(project)
        (rpt / "em.json").write_text(
            '{"segments_analysed":2,"max_segment_current_A":%s}' % p)
        (rpt / "em_segments.csv").write_text(
            "Node0 Layer,Node0 X location,Node0 Y location,"
            "Node1 Layer,Node1 X location,Node1 Y location,Current\n"
            "M4,0,0,M4,1,0,%s\nM4,1,0,M4,2,0,1.0e-6\n" % p)
        return False, True  # IR is not emitted; this segment EM run succeeded.
    monkeypatch.setattr(R, "_emit_ir_em_reports", fake_psm)
    status, detail = R._ppa_power._pdn_em_post_resize_check(
        tmp_path, "unit", _pdk(tmp_path), "unused",
        R._emit_ir_em_reports, R._emit_em_current_authority)
    assert status == "FAIL" and "PDN_EM_JMAX_UNCLOSED" in detail
    peak["current"] = 1.0e-6
    status, detail = R._ppa_power._pdn_em_post_resize_check(
        tmp_path, "unit", _pdk(tmp_path), "unused",
        R._emit_ir_em_reports, R._emit_em_current_authority)
    assert status == "PASS" and "PDN_EM_JMAX_CLOSED" in detail


def test_the_postcheck_is_a_flow_stop_not_just_a_report():
    pnr = R.StepResult("pnr", "PASS", 0.0, "routed DEF written")
    assert R._ppa_power._pdn_em_resize_chain_continues(
        pnr, "PASS", R._pnr_chain_continues)
    assert not R._ppa_power._pdn_em_resize_chain_continues(
        pnr, "FAIL", R._pnr_chain_continues)
    assert not R._ppa_power._pdn_em_resize_chain_continues(
        pnr, "NOT_MEASURED", R._pnr_chain_continues)
    source = (Path(R.__file__)).read_text()
    assert '"pdn_em_postcheck", _rz_post_status' in source
    assert '_ppa_power._pdn_em_resize_chain_continues(' in source
