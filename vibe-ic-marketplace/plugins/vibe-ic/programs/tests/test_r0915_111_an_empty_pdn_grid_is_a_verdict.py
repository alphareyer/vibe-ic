"""R-0915-111: a pdngen that builds no grid is a NAMED refusal at the PDN step.

MEASURED on spm x gf180mcuD (run10, 2026-09-21, front door, live main): the EM
floor widened the Metal5 strap from 1.6 to 1.96 um, the macro-grid plan kept the
4.655 um pitch it had derived for 1.6, and pdngen refused the deck outright:

    [ERROR PDN-0108] Spacing (0.3650 um) specified for layer Metal5 is less
                     than minimum spacing (0.4600 um)
    PDN_NONFATAL: PDN-0108

0.3650 = 4.655/2 - 1.96. pdngen emitted NOTHING — the DEF that run carried has
no Metal4 SPECIALNETS geometry at all — the note scrolled past, and the flow
discovered it two steps and 143 s later as

    PG_UNROUTED_SUPPLY: 2 POWER/GROUND net(s) carry real terminals and no
    special-net geometry — pdngen did not build the grid

with no GDS, `drc` NOT_MEASURED, `lvs` skipped and `tapeout_precheck` FAIL.

TWO DEFECTS, AND THIS FILE PINS BOTH.

  1. THE ARITHMETIC. `_macro_pdn_grid_plan` floored the pitch at
     `2*width + spacing`. pdngen lays the power AND the ground strap inside one
     pitch, so the gap it measures is `pitch/2 - width`: the floor is
     `2*(width + spacing)`. The two agree while the strap is narrow (at 1.6 um:
     3.66 vs 4.12, and the 4.655 um pitch clears both), which is why this held
     until an EM floor widened one.

  2. THE SILENCE. A `catch` printed `PDN_NONFATAL: <err>` and walked on. Now
     every supply net that carries a real terminal must own special-net
     geometry after pdngen, and a net that does not raises PDN_GRID_EMPTY
     carrying pdngen's OWN reason.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import phase3_one_shot_runner as R  # noqa: E402

#: run10's own numbers.
W_EM, SPACING, PORT = 1.96, 0.46, 4.655


# ------------------------------------------------------------ the arithmetic

def test_the_pitch_floor_holds_two_straps_not_one():
    """`pitch/2 - width >= spacing`, i.e. `pitch >= 2*(width+spacing)`."""
    src = inspect.getsource(R._macro_pdn_grid_outcome)
    assert "2.0 * (sw + s_minw)" in src, "the floor must hold two straps"
    assert "2.0 * sw + s_minw" not in src, "the one-strap floor is the defect"


def test_run10s_width_is_refused_by_the_corrected_floor():
    """The measured case, as arithmetic: 1.96 um straps cannot live on a
    4.655 um pitch, and the old floor said they could."""
    old_floor = 2.0 * W_EM + SPACING                 # 4.38  -> 4.655 "fits"
    new_floor = 2.0 * (W_EM + SPACING)               # 4.84  -> 4.655 refused
    assert old_floor < PORT < new_floor
    # and pdngen's own measure of the gap that pitch would give
    assert round(PORT / 2 - W_EM, 4) == 0.3675       # < 0.46, i.e. PDN-0108


def test_the_narrow_strap_that_worked_still_works():
    """The other direction: at the width the flow drew before any EM floor,
    the corrected floor still admits the same pitch, so a design that never
    widens is untouched."""
    assert 2.0 * (1.6 + SPACING) == 4.12 <= PORT


def test_the_refusal_names_the_two_strap_arithmetic():
    src = inspect.getsource(R._macro_pdn_grid_outcome)
    assert "2*(width+spacing)" in src
    assert "pitch/2-width" in src


# ------------------------------------------------------------- the silence

def test_an_empty_grid_raises_by_name_and_quotes_pdngen():
    t = R._pdn_grid_built_tcl()
    assert "PDN_GRID_EMPTY" in t
    assert 'error "PDN_GRID_EMPTY' in t, "it must be a verdict, not a note"
    assert "pdngen's own reason" in t and "$_why" in t
    # the predicate: a supply net with terminals and no special wires
    assert "getSigType" in t and "getSWires" in t
    assert "getITerms" in t and "getBTerms" in t


def test_a_built_grid_says_so_and_does_not_raise():
    t = R._pdn_grid_built_tcl()
    assert "PDN_GRID_PRESENT" in t
    # the success line is inside the same catch body as the verdict, so the
    # slice is bounded by the end of that body, not by the file
    ok = t[t.index("PDN_GRID_PRESENT"):t.index("_pdn_chk_err]")]
    assert "error " not in ok


def test_the_check_is_wired_into_every_pdn_emitter():
    """Both PDN paths — the sky130 one and the generic one — must carry it,
    or the swallow survives on whichever path the next PDK takes."""
    src = (PROGRAMS / "phase3_one_shot_runner.py").read_text()
    assert src.count("+ _pdn_grid_built_tcl()") >= 2
    # and it sits AFTER pdngen in both, never before
    for chunk in src.split("_pdn_grid_built_tcl()")[:-1]:
        assert "pdngen" in chunk


def test_a_check_failure_that_is_not_the_verdict_stays_nonfatal():
    """Degrade loudly, not silently: if the check itself cannot run (an old
    ODB binding, say), that is a note — only the measured empty grid is the
    verdict."""
    t = R._pdn_grid_built_tcl()
    assert "PDN_GRID_CHECK_NONFATAL" in t
    assert "string match {PDN_GRID_EMPTY:*}" in t, (
        "the verdict must survive the nonfatal wrapper")


# ------------------------------------------- stripes first, width second (2)

def _plan(**kw):
    base = dict(i_seg_A=1.322e-3, drawn_width_um=1.6, pitch_um=153.18,
                jmax_A_per_um=6.7e-4, margin=0.1, safety=2.0,
                min_spacing_um=0.46, min_pitch_um=3.84, concentration_k=10.0)
    base.update(kw)
    return R._pdn_em_stripe_plan(**base)


def test_an_em_demand_that_fits_is_answered_with_stripes_not_width():
    """run10's own measurement: 1.322 mA on a 1.6 um Metal5 strap at a 153.18
    um pitch. The old path widened to 1.96 um and pdngen built nothing; more
    stripes carry the same current at the width already drawn."""
    p = _plan()
    assert p["verdict"] == "MORE_STRIPES"
    assert p["stripe_multiplier"] == 3
    assert p["new_width_um"] == 1.6, "no track may be widened when stripes do"
    assert p["new_pitch_um"] == 51.06
    # and the answer actually satisfies Jmax at the new per-stripe current
    assert (p["i_seg_A"] / p["stripe_multiplier"]) * 2.0 / (6.7e-4 * 0.9) <= 1.6


def test_a_design_already_within_jmax_is_byte_identical():
    p = _plan(i_seg_A=1e-5)
    assert p["verdict"] == "ALREADY_MET"
    assert p["stripe_multiplier"] == 1
    assert p["new_pitch_um"] == 153.18 and p["new_width_um"] == 1.6


def test_stripes_stop_at_the_no_abut_floor_and_then_width_is_tried():
    """The floor is the corrected one — `2*(width+spacing)` — so the planner
    cannot walk the pitch into the geometry pdngen refuses."""
    p = _plan(i_seg_A=2.0e-2, min_pitch_um=0.0)
    assert p["verdict"] in ("WIDER_STRAP", "INFEASIBLE")
    floor = 2.0 * (1.6 + 0.46)
    assert p["new_pitch_um"] >= floor - 1e-9


def test_a_demand_no_geometry_can_carry_is_refused_by_name_with_numbers():
    p = _plan(i_seg_A=5.0, max_width_um=2.0)
    assert p["verdict"] == "INFEASIBLE"
    for token in ("no (stripes, width) this technology can build",
                  "pitch floor", "PDK minimum", "pdngen emit no grid"):
        assert token in p["reason"], token
    assert p["required_width_um"] > p["buildable_width_cap_um"]


def test_the_width_answer_never_exceeds_what_the_pitch_can_hold():
    """`pitch/2 - width >= spacing` is the same rule the macro-grid floor
    enforces; the planner may not propose a width that breaks it."""
    p = _plan(i_seg_A=3.0e-3, min_pitch_um=0.0)
    if p["verdict"] == "WIDER_STRAP":
        assert p["new_width_um"] <= p["new_pitch_um"] / 2 - 0.46 + 1e-9


def test_the_concentration_caveat_is_carried_not_hidden():
    """k is measured (10.0 on three spm runs) and REPORTED, never used to
    inflate the promise that I/m is what a stripe will carry."""
    p = _plan()
    assert p["concentration_k"] == 10.0
    assert "concentration_k" in p
    # the docstring wraps, so normalise before matching its sentences
    doc = " ".join((R._pdn_em_stripe_plan.__doc__ or "").split())
    assert "k is REPORTED beside the plan, never used to inflate a promise" in doc
    assert "re-measures after the build" in doc


# --------------------------------- the geometry it hands pdngen must be legal

def test_the_pitch_it_returns_lands_on_the_manufacturing_grid():
    """MEASURED on run11: the first cut halved the pitch to 76.59 um, the
    caller took its quarter as the offset — 19.148 um — and pdngen refused the
    deck: `[ERROR PDN-0191] Offset of 19.1480 um does not fit the manufacturing
    grid of 0.0050 um`. The PDN_GRID_EMPTY check in this same file is what
    caught it, quoting pdngen, at the PDN step."""
    g = 0.005
    p = _plan(grid_um=g)
    assert p["verdict"] == "MORE_STRIPES"
    q = p["new_pitch_um"] / g
    assert abs(q - round(q)) < 1e-6, (p["new_pitch_um"], "pitch off grid")
    # the offset the caller derives is a quarter of it, and must also land
    off = p["new_pitch_um"] / 4.0
    assert abs(off / g - round(off / g)) < 1e-6, (off, "offset off grid")


def test_snapping_never_crosses_the_no_abut_floor():
    """Snapping moves the pitch DOWN — toward more stripes — so it must not be
    allowed to land under the floor that keeps pdngen able to build at all."""
    p = _plan(i_seg_A=8.0e-3, grid_um=0.5, min_pitch_um=0.0)
    if p["verdict"] == "MORE_STRIPES":
        assert p["new_pitch_um"] >= 2.0 * (1.6 + 0.46) - 1e-9


def test_without_a_grid_the_answer_is_what_it_was():
    """A technology that declares no manufacturing grid is not a reason to
    invent one: the planner returns the unsnapped pitch, as before."""
    assert _plan(grid_um=0.0)["new_pitch_um"] == 51.06
