"""Measured late repair and post-edit DRV ordering on a neutral design."""
from __future__ import annotations

import sys
import inspect
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import librelane_postroute_repair as P
import librelane_contract as L
from _ppa import closure
import pnr_timing_repair_completeness_check as C
import phase3_one_shot_runner as R
import postroute_timing_repair_decision as D
import postroute_timing_repair_status_gen as S


def _sequence(body: str):
    checker = getattr(C, "drv_sequence", None)
    return checker(body) if checker else {"verdict": "NOT_MEASURED"}


def _trigger(**measurement):
    decide = getattr(P, "measured_repair_trigger", None)
    return decide(measurement) if decide else {"action": "NOT_MEASURED"}


def test_neutral_design_timing_edit_is_followed_by_drv_repair():
    deck = ("read_verilog neutral.v\nset_wire_rc -signal -layer M1\n"
            "repair_timing -setup\nrepair_design\n")
    assert _sequence(deck)["verdict"] == "PASS"
    # Reverse mutation: remove the final repair, leaving the changed netlist.
    assert _sequence(deck.replace("repair_design\n", ""))["verdict"] == "FAIL"


def test_antenna_edit_without_a_later_drv_census_is_disclosed():
    deck = ("repair_design\nrepair_timing -hold\n"
            "report_check_types -max_slew -violators\nrepair_antennas\n")
    result = _sequence(deck)
    assert result["verdict"] == "FAIL"
    assert result["last_unchecked_edit"] == "repair_antennas"
    assert _sequence(deck + "report_check_types -max_fanout -violators\n")[
        "verdict"] == "FAIL"
    assert _sequence(deck +
                     "report_check_types -max_slew -max_capacitance -violators\n"
                     "report_check_types -max_fanout -violators\n")["verdict"] == "PASS"


def test_drv_census_requires_executed_complete_three_axis_commands():
    edit = "repair_timing -setup\n"
    assert _sequence(edit + 'puts "report_check_types -max_slew -max_capacitance -max_fanout"\n')["verdict"] != "PASS"
    assert _sequence(edit + "report_check_types -max_fanout -violators\n")["verdict"] != "PASS"
    assert _sequence(edit + "# report_check_types -max_slew -max_capacitance -max_fanout\n")["verdict"] != "PASS"


def test_clean_input_skips_late_repair_and_one_violation_runs():
    clean = {"setup_ws_min": 0.3, "hold_ws_min": 0.2, "drv_count": 0}
    assert _trigger(**clean)["action"] == "SKIP"
    assert _trigger(**dict(clean, drv_count=1))["action"] == "RUN"
    assert _trigger(**dict(clean, hold_ws_min=-0.01))["action"] == "RUN"
    assert _trigger(setup_ws_min=0.3, hold_ws_min=0.2)["action"] == "NOT_MEASURED"


def test_invalid_numeric_measurements_cannot_certify_clean_route():
    clean = {"setup_ws_min": 0.3, "hold_ws_min": 0.2, "drv_count": 0}
    for key, value in (("setup_ws_min", float("nan")),
                       ("hold_ws_min", float("inf")),
                       ("drv_count", -1), ("drv_count", 0.5)):
        result = _trigger(**dict(clean, **{key: value}))
        assert result["action"] == "NOT_MEASURED", (key, result)
        assert key in result["missing"]


def test_direct_deck_emits_a_final_tool_drv_probe():
    builder = getattr(R, "_drv_after_edit_probe_tcl", lambda _m: "")
    body = builder("NEUTRAL_FINAL")
    assert "report_check_types -max_slew -max_capacitance -violators" in body
    assert "report_check_types -max_fanout -violators" in body
    assert "NEUTRAL_FINAL_DRV_CENSUS_UNMEASURED" in body


def test_real_arm_skips_clean_input_but_runs_on_injected_violation(tmp_path,
                                                                    monkeypatch):
    config = tmp_path / "step.json"
    config.write_text("{}")
    monkeypatch.setattr(L, "derive_step_config", lambda src, out, updates: src)
    calls = []

    def registry(*_args, **_kwargs):
        calls.append("controller")
        return object()

    class Controller:
        def __init__(self, *_args):
            pass

        def run_controller(self, _cid):
            class Result:
                def to_record(self):
                    return {"iterations": []}
            return Result()

    monkeypatch.setattr(closure, "load_registry", registry)
    monkeypatch.setattr(closure, "ClosureController", Controller)
    candidate = {"setup_ws_min": 0.3, "hold_ws_min": 0.2, "drv_count": 0}
    monkeypatch.setattr(P, "_candidate", lambda *_args: (tmp_path, dict(candidate)))

    def run(label):
        project = tmp_path / label
        state = project / "input.json"
        state.parent.mkdir()
        state.write_text("{}")
        return P.close_arm(project, "librelane", state, image="image", pdk="neutral",
                           configs={P.REPAIR_STEP: config}, corners=["tt"], mounts=[],
                           controllers=("postroute.repair_drv",))

    clean = run("clean")
    assert clean.get("repair_trigger", {}).get("action") == "SKIP"
    assert calls == []
    candidate["drv_count"] = 1
    violated = run("violated")
    assert violated.get("repair_trigger", {}).get("action") == "RUN"
    assert calls == ["controller"]


def test_missing_sta_is_not_a_measured_violation_or_a_clean_certificate():
    kw = ({"single_corner_evidence": "NOT_MEASURED"}
          if "single_corner_evidence" in inspect.signature(D.decide).parameters
          else {})
    result = D.decide(None, False, **kw)
    assert result["timing_repair_needed"] is False
    assert result["repair_needed"] is True
    assert S._parse_sta_for_violations("report header only").get(
        "timing_measurement") == "NOT_MEASURED"
    assert S._parse_sta_for_violations("tns -0.02\n")[
        "timing_measurement"] == "VIOLATED"


def test_explicit_single_corner_violation_outranks_zero_tns():
    report = "tns 0.00\nslack (VIOLATED) -0.05\n"
    parsed = S._parse_sta_for_violations(report)
    assert parsed["tns_zero"] is True
    assert parsed["timing_measurement"] == "VIOLATED"
    result = D.decide(None, parsed["tns_zero"], single_corner_evidence=parsed["timing_measurement"])
    assert result["timing_repair_needed"] is True
    assert result["repair_needed"] is True


def test_status_consumer_withholds_clean_flag_on_zero_tns_violated_path(
        tmp_path, monkeypatch):
    sta = tmp_path / "phase3/stage3/sta/sta_spef_based.rpt"
    sta.parent.mkdir(parents=True)
    sta.write_text("tns 0.00\nslack (VIOLATED) -0.05\n")
    monkeypatch.setattr(sys, "argv", ["status_gen", str(tmp_path)])
    assert S.main() == 0
    repair = tmp_path / "phase3/stage3/postroute_timing_repair"
    assert not (repair / "no_repair_needed.flag").exists()
    assert (repair / "repair_log.json").exists()
