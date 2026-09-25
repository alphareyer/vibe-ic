"""The rail and its perpendicular strap must share one buildable EM plan."""
import os
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as R  # noqa: E402
from _ppa import power as P  # noqa: E402
from test_t37_pdn_conservation_width import _pdk  # noqa: E402


def _floor(peak):
    return {
        "per_layer": {"m4": {"w_em_um": 50.12,
                              "jmax_A_per_um": 0.00067}},
        "max_segment_current_A": 0.01511, "i_drive_A": 0.03022,
        "i_total_A": 0.025, "margin": 0.1, "safety_factor": 2.0,
        "manufacturing_grid_um": 0.005, "width_quantum_um": 0.01,
        "rail_pitch_plan": {"applied": "DENSER_STRAPS", "rail_layer": "m1",
                            "strap_layer": "m4", "old_pitch_um": 153.58,
                            "new_pitch_um": 28.86, "strap_peak_A": peak},
    }


def test_joint_plan_refuses_routing_budget_on_real_tcl_path(tmp_path):
    # A sufficiently hot strap cannot fit the rail's required pitch while
    # reserving half of the layer for signal routing.
    with pytest.raises(ValueError, match="PDN_EM_COMBINED_PLAN_INFEASIBLE"):
        R._build_pdn_tcl(_pdk(tmp_path), em_floor=_floor(0.012))


def test_joint_plan_refuses_missing_strap_measurement(tmp_path):
    with pytest.raises(ValueError, match="PDN_EM_COMBINED_PLAN_NOT_MEASURED"):
        R._build_pdn_tcl(_pdk(tmp_path), em_floor=_floor(None))


def test_joint_plan_uses_strap_peak_and_records_one_pair(tmp_path):
    floor = _floor(0.00729)
    tcl = R._build_pdn_tcl(_pdk(tmp_path), em_floor=floor)
    rows = [a for a in floor["applied"] if a["layer"] == "M4"]
    assert len(rows) == 1
    row = rows[0]
    assert row["strap_drive_A"] == pytest.approx(0.00729 * 2 * 28.86 / 153.58)
    assert row["width_um"] < row["pitch_um"] - row["spacing_um"]
    assert f"-layer M4 -width {row['width_um']} -pitch 28.86" in tcl
    assert row["routing_fraction"] <= row["routing_budget"]


def test_strap_peak_excludes_hot_ring():
    d = ("UNITS DISTANCE MICRONS 1000 ;\nSPECIALNETS 1 ;\n"
         "- SUP + ROUTED M4 1600 + SHAPE RING ( 10000 0 ) ( 10000 20000 )\n"
         "NEW M4 1600 + SHAPE STRIPE ( 5000 0 ) ( 5000 20000 ) ;\n"
         "END SPECIALNETS\n")
    segs = [{"layer0": "M4", "layer1": "M4", "current_A": cur,
             "points_um": ((x, 5), (x, 8))}
            for x, cur in ((10, 0.01511), (5, 0.003335))]
    assert P.pdn_strap_segment_peak(d, segs, "M4") == pytest.approx(0.003335)


def test_measured_def_pitch_does_not_borrow_newer_tcl_pitch():
    d = ("UNITS DISTANCE MICRONS 1000 ;\nSPECIALNETS 1 ;\n"
         "- SUP + ROUTED M4 1600 + SHAPE STRIPE ( 5000 0 ) ( 5000 20000 )\n"
         "NEW M4 1600 + SHAPE STRIPE ( 158600 0 ) ( 158600 20000 ) ;\n"
         "END SPECIALNETS\n")
    assert P.pdn_def_stripe_pitch(d, "M4") == pytest.approx(153.6)


def test_second_pnr_cannot_measure_an_old_def(tmp_path):
    pnr = R._pl.pnr_dir(tmp_path)
    pnr.mkdir(parents=True)
    old_def = pnr / "unit.def"
    old_def.write_text("DESIGN unit ;\nEND DESIGN\n")
    os.utime(old_def, ns=(1, 1))

    def fake_eda(*args, **kwargs):
        raise AssertionError("stale DEF must stop before EDA")

    status, reason = P._pdn_em_post_resize_check(
        tmp_path, "unit", object(), "unused", fake_eda, fake_eda,
        rerun_started_ns=2)
    assert status == "NOT_MEASURED"
    assert reason == "PDN_EM_POSTCHECK_STALE_DEF"


def test_next_run_binds_the_applied_pair_not_the_discarded_global_floor(tmp_path):
    pnr = R._pl.pnr_dir(tmp_path)
    pnr.mkdir(parents=True)
    routed = pnr / "routed.def"
    routed.write_text("UNITS DISTANCE MICRONS 1000 ;\n"
                      "SPECIALNETS 1 ;\n- SUP + ROUTED M4 4550 "
                      "+ SHAPE STRIPE ( 0 0 ) ( 0 10000 ) ;\n"
                      "END SPECIALNETS\n")
    sentinel = pnr / P._PDN_EM_RESIZE_SENTINEL
    sentinel.write_text(json.dumps({
        "spent_on_def": hashlib.sha256(routed.read_bytes()).hexdigest(),
        "short": [{"layer": "M4", "w_em_um": 50.12}],
        "applied": [{"layer": "M4", "width_um": 4.55,
                     "pitch_um": 28.86}],
    }))
    binds, reason = P._pdn_em_sentinel_binds(sentinel, tmp_path)
    assert binds, reason
