"""Declared knobs are compared with the values delivered to each consumer."""
import importlib
import inspect
import json
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))
import librelane_contract as LL  # noqa: E402


def _project(tmp_path):
    docs = tmp_path / "input" / "docs"
    docs.mkdir(parents=True)
    (docs / "L9_constraints.md").write_text(
        "| knob | value |\n|---|---|\n"
        "| SYNTH_MAX_FANOUT | 3 |\n| FP_CORE_UTIL | 35 |\n"
        "| PL_TARGET_DENSITY | 0.55 |\n| FP_PDN_VOFFSET | 11.11 |\n")
    return tmp_path


def _checker():
    path = PROGRAMS / "declared_knob_applied_parity_check.py"
    assert path.is_file(), "CR-1 checker absent from the shipped programs"
    return importlib.import_module("declared_knob_applied_parity_check")


@pytest.mark.parametrize("knob,applied", [
    ("SYNTH_MAX_FANOUT", 9), ("FP_CORE_UTIL", 0.60),
    ("PL_TARGET_DENSITY", 0.33), ("FP_PDN_VOFFSET", 23.45),
])
def test_each_independent_drift_is_named(tmp_path, knob, applied):
    C = _checker()
    project = _project(tmp_path)
    declared = C.collect_declared(project)
    got = C.compare(project, {knob: (applied, "synthetic consumer")},
                    declared=declared)
    assert got["rows"][knob]["status"] == "FAIL"
    assert knob in got["rows"][knob]["reason"]
    assert got["mode"] == "ADVISORY"


def test_matching_values_and_absent_read(tmp_path):
    C = _checker()
    project = _project(tmp_path)
    got = C.compare(project, {"PL_TARGET_DENSITY": (0.55, "deck")})
    assert got["rows"]["PL_TARGET_DENSITY"]["status"] == "PASS"
    missing = C.compare(project, {"FP_CORE_UTIL": (None, "DEF unread")})
    assert missing["rows"]["FP_CORE_UTIL"]["status"] == "NOT_MEASURED"


def test_owner_and_doc_overrides_are_attested_but_flow_record_cannot_waive(
        tmp_path):
    C = _checker()
    project = _project(tmp_path)
    owner = project / "input" / "step_0_5ic_answers.json"
    owner.write_text(json.dumps({"answers": {"FP_CORE_UTIL": 0.60},
                                  "answer_provenance": {
                                      "FP_CORE_UTIL": {"answered_by": "owner"}}}))
    rec = {"knob": "FP_CORE_UTIL", "declared": 0.35, "applied": 0.60,
           "reason": "owner chose the measured core size",
           "decided_by": "owner:FP_CORE_UTIL"}
    assert C.compare(project, {"FP_CORE_UTIL": (0.60, "DEF")},
                     overrides=[rec])["rows"]["FP_CORE_UTIL"]["status"] == "PASS"
    rec["decided_by"] = "flow:automatic"
    assert C.compare(project, {"FP_CORE_UTIL": (0.60, "DEF")},
                     overrides=[rec])["rows"]["FP_CORE_UTIL"]["status"] == "FAIL"
    (project / "input/docs/L9_override.md").write_text("| FP_CORE_UTIL | 60 |\n")
    rec["decided_by"] = "document:input/docs/L9_override.md:1"
    assert C.compare(project, {"FP_CORE_UTIL": (0.60, "DEF")},
                     overrides=[rec])["rows"]["FP_CORE_UTIL"]["status"] == "PASS"


def test_second_design_independent_positive_and_negative(tmp_path):
    C = _checker()
    project = tmp_path / "quartz"
    docs = project / "input" / "docs"
    docs.mkdir(parents=True)
    (docs / "L9_constraints.md").write_text(
        "| FP_CORE_UTIL | 42 |\n| PL_TARGET_DENSITY | 0.48 |\n")
    assert C.compare(project, {"FP_CORE_UTIL": (0.42, "DEF")})["rows"]["FP_CORE_UTIL"]["status"] == "PASS"
    assert C.compare(project, {"PL_TARGET_DENSITY": (0.29, "deck")})["rows"]["PL_TARGET_DENSITY"]["status"] == "FAIL"


def test_every_derived_step_config_emits_advisory_parity(tmp_path):
    project = _project(tmp_path)
    inp = project / "phase3" / "librelane" / "15" / "config.json"
    inp.parent.mkdir(parents=True)
    inp.write_text(json.dumps({"PL_TARGET_DENSITY": 0.55}))
    out = inp.with_name("derived.json")
    LL.derive_step_config(inp, out, {"PL_TARGET_DENSITY": (0.33, "flow test")})
    report = out.with_suffix(".parity.json")
    assert report.is_file(), "derive_step_config omitted CR-1 advisory"
    data = json.loads(report.read_text())
    assert data["rows"]["PL_TARGET_DENSITY"]["status"] == "FAIL"


def test_observers_read_emitted_sdc_deck_and_real_core_ratio(tmp_path):
    C = _checker()
    project = _project(tmp_path)
    sdc = project / "phase3" / "stage3" / "pnr" / "constraint.sdc"
    sdc.parent.mkdir(parents=True)
    sdc.write_text("set_max_fanout 9 [current_design]\n")
    assert C.observe_sdc(project, sdc)["rows"]["SYNTH_MAX_FANOUT"]["status"] == "FAIL"
    sdc.write_text("set_max_fanout 3 [current_design]\n")
    assert C.observe_sdc(project, sdc)["rows"]["SYNTH_MAX_FANOUT"]["status"] == "PASS"
    deck = sdc.with_name("pnr.tcl")
    tech = "LAYER vv\n TYPE ROUTING ;\n DIRECTION VERTICAL ;\nEND vv\n"
    deck.write_text("global_placement -density 0.33\n"
                    "add_pdn_stripe -layer vv -offset 23.45 -pitch 80\n")
    rows = C.observe_pnr(project, deck, cell_area_um2=60, core_area_um2=100,
                         tech_lef_text=tech)["rows"]
    assert rows["FP_CORE_UTIL"]["status"] == "FAIL"
    assert rows["PL_TARGET_DENSITY"]["status"] == "FAIL"
    assert rows["FP_PDN_VOFFSET"]["status"] == "FAIL"
    deck.write_text("global_placement -density 0.55\n"
                    "add_pdn_stripe -layer vv -offset 11.11 -pitch 80\n")
    rows = C.observe_pnr(project, deck, cell_area_um2=35, core_area_um2=100,
                         tech_lef_text=tech)["rows"]
    assert rows["FP_CORE_UTIL"]["status"] == "PASS"
    assert rows["PL_TARGET_DENSITY"]["status"] == "PASS"
    assert rows["FP_PDN_VOFFSET"]["status"] == "PASS"


def test_column_declarations_select_active_pdk_and_library(tmp_path):
    C = _checker()
    project = tmp_path / "scoped"
    docs = project / "input" / "docs"
    docs.mkdir(parents=True)
    (docs / "L9_constraints.md").write_text(
        "| PDK | FP_CORE_UTIL | PL_TARGET_DENSITY |\n"
        "| --- | --- | --- |\n"
        "| foreign | 60 | 0.30 |\n"
        "| quartzD | 40 | 0.50 |\n\n"
        "| library | MAX_FANOUT_CONSTRAINT |\n"
        "| --- | --- |\n"
        "| quartz_* | 7 |\n"
        "| quartz_fast | 3 |\n"
        "| foreign_* | 9 |\n")
    applied = {"FP_CORE_UTIL": (0.60, "final DEF"),
               "PL_TARGET_DENSITY": (0.30, "final deck"),
               "SYNTH_MAX_FANOUT": (9, "emitted SDC")}
    if "pdk" in inspect.signature(C.compare).parameters:
        got = C.compare(project, applied, pdk="quartzD", library="quartz_fast")
    else:
        # Reviewed tip has no scoped API; observe its actual report values.
        got = C.compare(project, applied)
    assert {k: got["rows"][k]["declared"] for k in applied} == {
        "FP_CORE_UTIL": 0.40, "PL_TARGET_DENSITY": 0.50,
        "SYNTH_MAX_FANOUT": 3.0}
    assert {got["rows"][k]["status"] for k in applied} == {"FAIL"}
    assert "L9_constraints.md:4" in got["rows"]["FP_CORE_UTIL"]["reason"]
    assert "L9_constraints.md:9" in got["rows"]["SYNTH_MAX_FANOUT"]["reason"]
    sdc = project / "phase3" / "stage3" / "pnr" / "constraint.sdc"
    sdc.parent.mkdir(parents=True)
    sdc.write_text("set_max_fanout 9 [current_design]\n")
    sdc_row = C.observe_sdc(project, sdc, pdk="quartzD",
                            library="quartz_fast")["rows"]["SYNTH_MAX_FANOUT"]
    assert sdc_row["declared"] == 3.0 and sdc_row["status"] == "FAIL"
    unknown = C.compare(project, applied, pdk="unknown", library="unknown")
    assert {unknown["rows"][k]["status"] for k in applied} == {"NOT_MEASURED"}


def test_final_report_uses_final_route_metrics_after_resize(tmp_path):
    C = _checker()
    project = _project(tmp_path)
    (project / "input" / "docs" / "L9_constraints.md").write_text(
        "| FP_CORE_UTIL | 40 |\n| PL_TARGET_DENSITY | 0.55 |\n")
    deck = project / "phase3" / "stage3" / "pnr" / "pnr.tcl"
    deck.parent.mkdir(parents=True)
    deck.write_text("global_placement -density 0.55\n")
    final_def = deck.with_name("chip.def")
    final_def.write_text("VERSION 5.8 ;\nEND DESIGN\n")
    metrics = deck.with_name("openroad.metrics.json")
    planned = C.write_pnr_report(
        project, deck, cell_area_um2=40, core_area_um2=100)
    plan_data = json.loads(planned.read_text())
    assert plan_data["rows"]["FP_CORE_UTIL"]["status"] == "PASS"
    if hasattr(C, "write_pending_pnr_report"):
        assert planned.name == "pnr.planned.parity.json"
        assert plan_data["evidence_stage"] == "PLANNED"
        C.write_pending_pnr_report(project, deck)
        pending = json.loads(deck.with_suffix(".parity.json").read_text())
        assert pending["rows"]["FP_CORE_UTIL"]["status"] == "NOT_MEASURED"
    metrics.write_text(json.dumps({
        "vibeic__pnr__applied__cell_area_um2": 40.0,
        "vibeic__pnr__applied__core_area_um2": 150.0}))
    report = (C.write_applied_pnr_report(project, deck, final_def, metrics)
              if hasattr(C, "write_applied_pnr_report")
              else deck.with_suffix(".parity.json"))
    data = json.loads(report.read_text())
    assert data["rows"]["FP_CORE_UTIL"]["declared"] == 0.40
    assert data["rows"]["FP_CORE_UTIL"]["applied"] == pytest.approx(40 / 150)
    assert data["evidence_stage"] == "APPLIED"
    assert data["rows"]["FP_CORE_UTIL"]["status"] == "FAIL"
    assert data["evidence"]["final_def"] == str(final_def)
    assert data["evidence"]["cell_area_um2"] == 40.0
    assert data["evidence"]["core_area_um2"] == 150.0
    metrics.unlink()
    C.write_applied_pnr_report(project, deck, final_def, metrics)
    absent = json.loads(report.read_text())
    assert absent["rows"]["FP_CORE_UTIL"]["status"] == "NOT_MEASURED"
