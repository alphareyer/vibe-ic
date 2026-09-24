"""The first-pass EM floor must reach the one bounded PnR re-dispatch.

Only OpenROAD's writes are faked. The floor calculator, PnR Tcl builder,
DEF width reader and Jmax segment screen run their production paths.
"""
import json
import inspect
import re

import em_current_density_check as EMC
import phase3_one_shot_runner as R
from test_issue1215_pdn_em_derived_sizing import _TLEF
from test_issue2108_pnr_approach_log_survives_the_next_approach import (
    _build_project, _pdk,
)


def test_measured_floor_is_drawn_and_clears_the_same_jmax_rule(tmp_path,
                                                               monkeypatch):
    project = _build_project(tmp_path, "widget")
    pnr = R._pl.pnr_dir(project)
    pnr.mkdir(parents=True, exist_ok=True)
    (pnr / R._ppa_power._PDN_EM_SUBJECT_DEF).write_text(
        "DESIGN widget ;\nEND DESIGN\n")
    rpt = R._pl.reports_phase3_dir(project)
    rpt.mkdir(parents=True, exist_ok=True)
    current = 1.189e-3
    (rpt / "em.json").write_text(json.dumps({
        "max_segment_current_A": current,
        "segments_analysed": 1,
        "subject_def_sha256": R._ppa_power._pdn_em_subject_digest(project),
    }))
    tech = project / "tech.tlef"
    tech.write_text(_TLEF.replace("MetalA", "met4").replace("MetalB", "met5"))
    pdk = _pdk()
    pdk.tech_lef = str(tech)
    floor = R._pdn_em_width_floor(project, pdk)
    assert floor and floor["per_layer"]["met4"]["w_em_um"] > 1.6

    def openroad_writes(_container, command, timeout=None, **_kw):
        if "openroad -no_init" not in command:
            return 0, "", ""
        deck = (pnr / "pnr.tcl").read_text()
        widths = dict(re.findall(
            r"add_pdn_stripe[^\n]*-layer (met[45]) -width ([0-9.]+)", deck))
        # This DEF is the tool's output, reflecting its input Tcl widths.
        (pnr / "widget.def").write_text(
            "VERSION 5.8 ;\nUNITS DISTANCE MICRONS 1000 ;\n"
            "SPECIALNETS 1 ;\n- VDD + USE POWER\n"
            + "".join(f"  + ROUTED {layer} {int(float(width)*1000)} "
                      "+ SHAPE STRIPE ( 1000 1000 ) ( 1000 9000 )\n"
                      for layer, width in widths.items())
            + ";\nEND SPECIALNETS\nEND DESIGN\n")
        (pnr / "openroad.log").write_text(
            "[INFO DRT-0199] Number of violations = 0.\n"
            "PG_NET_OWNERSHIP_AUDIT: total=600 no_net=0 masters=\n")
        return 0, (pnr / "openroad.log").read_text(), ""

    monkeypatch.setattr(R, "_docker_exec", openroad_writes)
    # On main the one-shot caller has no handoff argument, so exercise the
    # existing PnR path and observe its actual narrow DEF instead of merely
    # failing at Python's keyword-argument check.
    handoff = ({"em_floor_for_resize": floor}
               if "em_floor_for_resize" in inspect.signature(R.step_pnr).parameters
               else {})
    R.step_pnr(project, "widget", pdk, "iic", "200x200", 0.30, **handoff)
    drawn = EMC._def_pg_widths_of(pnr / "widget.def")
    assert drawn["met4"] >= floor["per_layer"]["met4"]["w_em_um"]
    assert json.loads((rpt / "pdn_em_sizing.json").read_text())["sizing_basis"] == \
        "measured_max_segment"
    screened = EMC._screen_segment(
        {"layer0": "met4", "layer1": "met4", "net": "VDD",
         "current_A": current, "width_um": None},
        EMC.parse_lef_jmax(tech.read_text()), 0.1, 2.0, drawn)
    assert screened["status"] == "ok", screened
