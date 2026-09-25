"""A measured hot fixed cell rail must change the perpendicular strap plan."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as R  # noqa: E402
from test_t37_pdn_conservation_width import _pdk  # noqa: E402
from _ppa import power as P  # noqa: E402


def test_rail_pitch_is_drawn_with_a_coupled_strap_width(tmp_path):
    floor = {
        "per_layer": {"m4": {"w_em_um": 50.12,
                               "jmax_A_per_um": 0.00067}},
        "max_segment_current_A": 0.01511, "i_drive_A": 0.03022,
        "i_total_A": 0.028, "margin": 0.1, "safety_factor": 2.0,
        "manufacturing_grid_um": 0.005, "width_quantum_um": 0.01,
        "rail_pitch_plan": {"applied": "DENSER_STRAPS",
                            "strap_layer": "m4", "rail_layer": "m1",
                            "old_pitch_um": 153.58,
                            "new_pitch_um": 50.4,
                            "rail_j_before_A_per_um": 0.001102 / 0.6,
                            "rail_j_predicted_after_A_per_um":
                                0.001102 * 50.4 / 153.58 / 0.6},
    }
    tcl = R._build_pdn_tcl(_pdk(tmp_path), em_floor=floor)
    applied = [a for a in floor["applied"] if a["layer"] == "M4"]
    assert len(applied) == 1
    assert applied[0]["verdict"] == "DENSER_STRAPS"
    assert applied[0]["pitch_um"] == 50.4
    assert applied[0]["width_um"] < 50.12
    assert f"-layer M4 -width {applied[0]['width_um']} -pitch 50.4" in tcl


def test_ring_current_that_cannot_fit_gap_is_named_refusal():
    def_text = ("UNITS DISTANCE MICRONS 1000 ;\nSPECIALNETS 1 ;\n"
                "- SUP + ROUTED M4 1600 + SHAPE RING "
                "( 10000 0 ) ( 10000 20000 ) ;\n"
                "END SPECIALNETS\n")
    segments = [{"layer0": "M4", "layer1": "M4",
                 "current_A": 0.01136,
                 "points_um": ((10, 5), (10, 8))}]
    peaks = P.pdn_ring_segment_peaks(def_text, segments)
    cfg = {"layers": ["M4", "M5"], "widths": [1.6, 1.6],
           "spacings": [1.7, 1.7], "connect_to_pad_layers": ["M2"],
           "connects": [["M2", "M4"]], "core_offset_um": 6,
           "min_clearance_um": 0.46}
    plan = P.pdn_ring_capacity_plan(
        cfg, gap_um=17.44, peaks_A=peaks,
        jmax_A_per_um={"m4": 0.00067, "m5": 0.0015},
        margin=0.1, grid_um=0.005)
    assert peaks == {"m4": 0.01136}
    assert plan["code"] == "PDN_EM_RING_CAPACITY_UNREACHABLE"
    assert plan["required_widths_um"][0] > 18.83
    assert plan["required_footprint_um"] > plan["available_footprint_um"]


def test_legal_measured_ring_width_is_emitted_to_pdngen():
    cfg = {"layers": ["M4", "M5"], "widths": [1.6, 1.6],
           "spacings": [1.7, 1.7], "connect_to_pad_layers": ["M2"],
           "connects": [["M2", "M4"]], "core_offset_um": 6,
           "min_clearance_um": 0.46}
    plan = P.pdn_ring_capacity_plan(
        cfg, gap_um=25, peaks_A={"m4": 0.004},
        jmax_A_per_um={"m4": 0.00067, "m5": 0.0015},
        margin=0.1, grid_um=0.005)
    assert plan["applied"] == "WIDER_RING"
    ring = R._pad_connected_ring_tcl(SimpleNamespace(pdn_ring=cfg), plan)
    widths = " ".join(str(x) for x in plan["required_widths_um"])
    assert f"-widths {{{widths}}}" in ring["grid"]


def test_pad_entry_current_needs_measured_width_not_recipe_assumption():
    rows = P.pdn_pad_entry_plan(
        [{"layer0": "M2", "layer1": "M2", "current_A": 0.01704}],
        pad_layers=["M2"], drawn_widths_um={"m2": 9.45},
        jmax_A_per_um={"m2": 0.00067}, margin=0.1, grid_um=0.005)
    assert rows[0]["status"] == "CAPACITY_NOT_PROVEN"
    assert rows[0]["required_width_um"] == 28.26
