#!/usr/bin/env python3
"""R-0915-162 — a POST-LAYOUT SPICE correlation is built on a POST-ROUTE,
SPEF-bearing STA basis, or it refuses by name.

MEASURED on spm run23 (plugin ec313cde7). `_pick_sta_report` scores
`(stitch, states_operating_point)` and stitch dominates, so:

    report                     stitch  op_pt  STA_BASIS            LIB
    post_route_timing.rpt           2      0  POST_ROUTE_SPEF        6
    pre_pnr_timing.rpt              3      0  PRE_LAYOUT_ESTIMATE    0   <- PICKED
    sta_mcorner_ocv.rpt             2      4  POST_ROUTE_SPEF        2
    sta_spef_based.rpt              2      0  POST_ROUTE_SPEF        6
    sta_spef_multicorner.rpt        2      4  POST_ROUTE_SPEF        2

One extra combinational stage put a 1,860-byte PRE-LAYOUT estimate ahead of
four post-route reports. Step 30 then refused with "pre_pnr_timing.rpt declares
no corner liberty" -- correct, but for the SECOND reason. The first is that a
post-layout deck must never be correlated against a pre-layout, no-parasitics
basis at all: `pre_pnr_timing.rpt`'s own header says it "timed the pre-PnR
synthesis netlist + SDC, emitted BEFORE PnR; interconnect is a pre-floorplan
estimate".

Stamping the pre-layout report with a liberty would have made the refusal go
away by making the gate wrong. The basis is the fix.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import spice_correlation_check as S  # noqa: E402


_LIB = ("/foss/pdks/gf180mcuD/libs.ref/gf180mcu_fd_sc_mcu7t5v0/lib/"
        "gf180mcu_fd_sc_mcu7t5v0__ss_125C_4v50.lib")

#: A post-route report in the shape run23 writes: corner banner, basis block,
#: then an OpenSTA path whose rows name real cells.
_POST_ROUTE = f"""=== SETUP corner: process=SS liberty={_LIB} ===
OCV_DERATE_APPLIED early=0.95 late=1.05 flat-OCV
STA_BASIS: POST_ROUTE_SPEF
STA_BASIS_LIBERTY: {_LIB}
STA_BASIS_NETLIST: spm_pnr.v
STA_BASIS_SPEF: spm.max.spef
STA_BASIS_CORNER: max
Startpoint: u_core/_416_ (rising edge-triggered flip-flop clocked by clk)
Endpoint: p (output port clocked by clk)
Path Group: clk
Path Type: max

   Cap    Slew   Delay    Time   Description
---------------------------------------------------------
  0.00    0.00    0.00    0.00   clock clk (rise edge)
  0.40    5.44    3.55    6.23 ^ u_core/a/Z (gf180mcu_fd_sc_mcu7t5v0__buf_2)
  0.30    4.10    1.20    7.43 ^ u_core/b/Z (gf180mcu_fd_sc_mcu7t5v0__inv_1)
                          7.43   data arrival time
"""

#: The PRE-LAYOUT estimate, with one MORE stitchable stage than any post-route
#: report -- the run23 shape, and the whole reason the basis must decide.
_PRE_LAYOUT = """# PRE-LAYOUT STA (Step 10) — genuine OpenSTA on the synth
# netlist + SDC, emitted BEFORE PnR; interconnect is a pre-floorplan estimate.
STA_BASIS: PRE_LAYOUT_ESTIMATE
Startpoint: _417_ (rising edge-triggered flip-flop clocked by clk)
Endpoint: _416_ (rising edge-triggered flip-flop clocked by clk)
Path Group: clk
Path Type: max

   Cap    Slew   Delay    Time   Description
---------------------------------------------------------
  0.00    0.00    0.00    0.00   clock clk (rise edge)
  0.40    5.44    3.55    6.23 ^ a/Z (gf180mcu_fd_sc_mcu7t5v0__buf_2)
  0.30    4.10    1.20    7.43 ^ b/Z (gf180mcu_fd_sc_mcu7t5v0__inv_1)
  0.20    3.10    0.90    8.33 ^ c/Z (gf180mcu_fd_sc_mcu7t5v0__nand2_1)
                          8.33   data arrival time
"""

_SUBCKT = {"gf180mcu_fd_sc_mcu7t5v0__buf_2",
           "gf180mcu_fd_sc_mcu7t5v0__inv_1",
           "gf180mcu_fd_sc_mcu7t5v0__nand2_1"}


def _project(tmp_path: Path, **reports: str) -> Path:
    root = tmp_path / "run23"
    (root / "phase3" / "stage3" / "sta").mkdir(parents=True, exist_ok=True)
    for name, text in reports.items():
        (root / "phase3" / "stage3" / "sta"
         / f"{name}.rpt").write_text(text)
    return root


# ------------------------------------------------------- the basis predicate

def test_only_a_post_route_spef_basis_is_eligible():
    """Both directions, over the whole declared vocabulary."""
    for basis in ("POST_ROUTE_SPEF", "POST_ROUTE_MCF_SPEF"):
        assert S.is_post_route_spef_basis(
            f"STA_BASIS: {basis}\n") is True, basis
    for basis in ("PRE_LAYOUT_ESTIMATE", "POST_ROUTE_NO_SPEF"):
        assert S.is_post_route_spef_basis(
            f"STA_BASIS: {basis}\n") is False, basis
    # a report that declares NO basis is not eligible either: "I did not say"
    # is not "post-route with parasitics".
    assert S.is_post_route_spef_basis("Startpoint: a\n") is False
    assert S.sta_basis("STA_BASIS: POST_ROUTE_SPEF\n") == "POST_ROUTE_SPEF"
    assert S.sta_basis("no basis here") == ""


# ------------------------------------------------------------- the run23 case

def test_a_richer_pre_layout_path_does_not_win_the_pick(tmp_path):
    """THE run23 SHAPE. The pre-layout report out-stitches the post-route one
    and must still lose, because the score cannot answer 'is this the right
    document'."""
    root = _project(tmp_path, pre_pnr_timing=_PRE_LAYOUT,
                    sta_mcorner_ocv=_POST_ROUTE)
    # the fixture really does reproduce the inversion, or it proves nothing
    assert (S.sta_path_stitch_score(_PRE_LAYOUT, _SUBCKT)
            > S.sta_path_stitch_score(_POST_ROUTE, _SUBCKT)), (
        "fixture does not reproduce run23: the pre-layout report must expose "
        "MORE stitchable stages than the post-route one")
    picked = S._pick_sta_report(root, _SUBCKT)
    assert picked is not None and picked.name == "sta_mcorner_ocv.rpt", picked


def test_the_picked_post_route_report_binds_one_real_corner(tmp_path):
    """What the pick is FOR. The chosen report must yield exactly one declared
    corner liberty, or the correlation swaps one refusal for another."""
    root = _project(tmp_path, pre_pnr_timing=_PRE_LAYOUT,
                    sta_mcorner_ocv=_POST_ROUTE)
    picked = S._pick_sta_report(root, _SUBCKT)
    basis = S.parse_sta_corner_basis(picked.read_text())
    assert basis["liberty"] == _LIB, basis
    assert len(basis["declared_liberties"]) == 1, basis["declared_liberties"]


def test_the_best_post_route_report_still_wins_on_the_existing_score(tmp_path):
    """The basis filter REPLACES no part of the score. Among eligible reports
    the established rule still decides, including the operating-point
    tie-break that the 34 %-error note exists for."""
    silent = _POST_ROUTE.replace("   Cap    Slew   Delay    Time   Description",
                                 "  Delay    Time   Description")
    root = _project(tmp_path, sta_spef_based=silent,
                    sta_mcorner_ocv=_POST_ROUTE)
    picked = S._pick_sta_report(root, _SUBCKT)
    assert picked is not None and picked.name == "sta_mcorner_ocv.rpt", picked


# ------------------------------------------------------------ named refusals

def test_no_eligible_basis_refuses_by_name_and_names_what_it_saw(tmp_path):
    """NEVER a silent fallback. The refusal names the reports and the basis
    each declared, so a reader can act on it."""
    root = _project(tmp_path, pre_pnr_timing=_PRE_LAYOUT)
    assert S._pick_sta_report(root, _SUBCKT) is None
    why = S.post_route_basis_refusal(root, _SUBCKT)
    assert why and why.startswith("no post-route STA basis:"), why
    assert "pre_pnr_timing.rpt [PRE_LAYOUT_ESTIMATE]" in why, why
    assert "POST_ROUTE_SPEF" in why


def test_a_project_with_no_sta_report_says_that_instead(tmp_path):
    """Two different facts must not arrive wearing the same sentence."""
    root = _project(tmp_path)
    why = S.post_route_basis_refusal(root, _SUBCKT)
    assert why and "holds no STA report at all" in why, why


def test_a_post_route_basis_with_no_stitchable_path_says_that_instead(
        tmp_path):
    """Right document, nothing to correlate — a third distinct sentence."""
    bare = ("STA_BASIS: POST_ROUTE_SPEF\n"
            f"STA_BASIS_LIBERTY: {_LIB}\n"
            "Startpoint: _1_ (rising edge-triggered flip-flop)\n"
            "Endpoint: q (output port)\n\n"
            "  Delay    Time   Description\n"
            "-----------------------------\n"
            "   0.00    0.00   clock clk (rise edge)\n"
            "                  data arrival time\n")
    root = _project(tmp_path, sta_spef_based=bare)
    assert S._pick_sta_report(root, _SUBCKT) is None
    why = S.post_route_basis_refusal(root, _SUBCKT)
    assert why and why.startswith(
        "no post-route STA basis with a stitchable path:"), why
    assert "none exposes a combinational stage" in why, why


def test_an_eligible_pick_refuses_nothing(tmp_path):
    """The refusal is None exactly when a report was picked."""
    root = _project(tmp_path, sta_mcorner_ocv=_POST_ROUTE)
    assert S._pick_sta_report(root, _SUBCKT) is not None
    assert S.post_route_basis_refusal(root, _SUBCKT) is None


def test_the_basis_refusal_and_the_corner_refusal_stay_distinct(tmp_path):
    """TWO CHECKS, TWO SENTENCES, IN ORDER. The basis question comes first --
    "is this the right document" -- and only an eligible report is asked "which
    corner did you run at". Collapsing them would let a pre-layout report be
    refused for having no corner, which is what run23 reported and is the
    SECOND reason, not the first."""
    # the post-route report the run23 tree writes, minus the liberty stamp
    no_corner = "\n".join(
        ln for ln in _POST_ROUTE.splitlines()
        if not ln.startswith("STA_BASIS_LIBERTY:")
        and not ln.startswith("=== SETUP corner:")) + "\n"
    root = _project(tmp_path, sta_spef_based=no_corner)
    # eligible: the basis refusal does NOT fire
    assert S._pick_sta_report(root, _SUBCKT) is not None
    assert S.post_route_basis_refusal(root, _SUBCKT) is None
    # ...and the corner question is then asked, and answered honestly
    basis = S.parse_sta_corner_basis(no_corner)
    assert basis["liberty"] == "", basis
