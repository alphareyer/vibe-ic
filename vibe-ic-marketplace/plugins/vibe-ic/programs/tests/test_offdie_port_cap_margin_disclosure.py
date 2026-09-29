"""DRV standard section 4 on a DIE: a bond-pad port is not a std-cell net.

Measured (spmic5 fresh DIE front door v5c, 2026-09-29): Step 32 stopped at
LL_PRR_DRV_VIOLATION with 35 residual (pin, check) rows, every one a top-level
signal port of the pad-ring chip_top (clk, rst, x[*], y) against the
integrator's 0.2 pF std-cell capacitance margin; each port net is a ~2.9 pF
bond pad.  The standard applies that margin only to std-cell-driven nets, so
on a DIE those rows are disclosed beside the census, never gated and never
counted as clean evidence.  Instance-pin rows and every non-DIE run are judged
exactly as before.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))
TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import _tapeout_declaration as TD  # noqa: E402
import librelane_postroute_repair as prr  # noqa: E402
from _route_fixture import stage_owner_route  # noqa: E402


def _die(tmp_path: Path) -> Path:
    project = tmp_path / "die"
    stage_owner_route(project, "ic")
    marker = project / TD.SELF_TAPEOUT_REL
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("self tape-out\n")
    assert TD.requests_pad_ring(project)
    return project


def _core(tmp_path: Path) -> Path:
    project = tmp_path / "core"
    stage_owner_route(project, "ip")
    assert not TD.requests_pad_ring(project)
    return project


def _report(pairs):
    report = {"corners": ["tt"], "final": {
        "drv": {"fanout": {"tt": 0}}, "drv_pin_checks_state": "PASS",
        "drv_pin_checks": [list(p) for p in pairs], "drv_count": len(pairs)}}
    prr._set_final_fanout_verdict(report)
    return report


def test_die_port_cap_margin_rows_are_disclosed_not_gated(tmp_path):
    report = _report([("clk", "cap"), ("x[0]", "cap"), ("y", "cap")])
    prr._set_final_drv_verdict(report, _die(tmp_path))
    assert report["final_drv"] == {"verdict": "PASS", "violations": 0}
    assert report["verdict"] == "PASS"
    assert report["offdie_port_cap_disclosures"] == [
        ["clk", "cap"], ["x[0]", "cap"], ["y", "cap"]]


def test_die_instance_pin_cap_row_still_fails(tmp_path):
    report = _report([("clk", "cap"), ("u_core/buf1/Z", "cap")])
    prr._set_final_drv_verdict(report, _die(tmp_path))
    assert report["final_drv"]["violations"] == 1
    assert report["code"] == "LL_PRR_DRV_VIOLATION"
    assert report["offdie_port_cap_disclosures"] == [["clk", "cap"]]


def test_die_port_slew_row_is_not_a_cap_disclosure(tmp_path):
    report = _report([("clk", "slew")])
    prr._set_final_drv_verdict(report, _die(tmp_path))
    assert report["code"] == "LL_PRR_DRV_VIOLATION"
    assert "offdie_port_cap_disclosures" not in report


def test_core_route_port_cap_rows_stay_gated(tmp_path):
    report = _report([("clk", "cap"), ("x[0]", "cap")])
    prr._set_final_drv_verdict(report, _core(tmp_path))
    assert report["final_drv"]["violations"] == 2
    assert report["code"] == "LL_PRR_DRV_VIOLATION"


def test_census_verdict_threads_the_project(tmp_path):
    report = _report([("rst", "cap")])
    report["verdict"] = "PASS"
    measured = {"antenna_nets": 0, "antenna_pins": 0}
    prr._set_census_verdict(report, measured, report["final"],
                            project=_die(tmp_path))
    assert report["final_drv"]["violations"] == 0
    assert report["offdie_port_cap_disclosures"] == [["rst", "cap"]]


# --- the DRV judge: the same rows, the same rule --------------------------
import drv_signoff_judge as drv  # noqa: E402
from test_drv_signoff_judge import (  # noqa: E402,F401
    _bundle, _file, _violate, _synthetic_scene_profile)


def _port_cap_bundle(root: Path) -> dict:
    """The fixture's one cap violator moved from an instance pin onto a
    top-level port, exactly as the tool reports a bond-pad net."""
    bundle = _bundle(root)
    _violate(bundle, root, "max_capacitance", value=2.9, limit=.2,
             net_class="IO", cell_class="port")
    scene = bundle["scenes"][0]
    for key, name in (("report", "violators.rpt"),
                      ("all_limits_report", "all.rpt")):
        body = Path(scene[key]["path"]).read_text().replace("Pin u/Y", "Pin clk")
        scene[key] = _file(root, name, body)
    meta = bundle["pins"].pop("u/Y")
    meta.update(cell=None, cell_pin=None, liberty=None, driver_pin="clk",
                driver_cell=None, cell_class="port", net_class="IO")
    bundle["pins"]["clk"] = meta
    return bundle


def _make_die(root: Path) -> None:
    stage_owner_route(root, "ic")
    marker = root / TD.SELF_TAPEOUT_REL
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("self tape-out\n")
    assert TD.requests_pad_ring(root)


def test_judge_discloses_a_die_port_row_against_the_std_cell_margin(tmp_path):
    _make_die(tmp_path)
    result = drv.judge(_port_cap_bundle(tmp_path))
    assert not result["findings"], result["findings"]
    assert [r["failed_tier"] for r in result["io_margin_disclosures"]] == [
        "OFFDIE_PORT_STD_CELL_MARGIN_DISCLOSURE"]
    assert result["io_margin_disclosures"][0]["pin"] == "clk"


def test_judge_keeps_a_core_route_port_row_gated(tmp_path):
    result = drv.judge(_port_cap_bundle(tmp_path))
    assert [r["failed_tier"] for r in result["findings"]] == ["T3_MARGIN"]
    assert result["verdict"] == "FAIL"
    assert not result["io_margin_disclosures"]
