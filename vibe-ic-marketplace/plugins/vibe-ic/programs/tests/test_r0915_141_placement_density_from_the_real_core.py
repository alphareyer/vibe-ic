#!/usr/bin/env python3
"""R-0915-141 — the placer's density comes from the core it will actually use.

THE MEASUREMENT this deck is written from (subservient x gf180mcuD as a DIE,
lane icsub5, run2 on main 1f537b5c0, 2026-09-23):

    [phase3] die-um=auto → 258x258 (cells=2510, avg_cell=13.17µm² [site-LEF],
             target_util=0.5 [L9-declared]; ... cell-area-dominated)
    [phase3] core := the die interior, 1176x1176 um inside the 1962x1962 um
             ring die ... about 4.81% of this core: REPORTED as the density it
             is, never a lever on the rectangle
    pnr.tcl: global_placement -routability_driven -timing_driven -density 0.3

`-density` is an UPPER BOUND on bin occupancy and the wirelength + timing
objective packs right up to it. 2510 cells x 13.17 µm² = 33,057 µm² in a
1176x1176 µm core is a natural density of 2.39 %, but the placer was told 0.30
-- a number whose own `--util` help justifies it on a "spm 200x200 die" -- so it
compressed the design into 33,057/0.30 = 110,190 µm². MEASURED island: 42 tiles
of 50x50 µm = 105,000 µm², within 5 % of that prediction, and ALL 3808
post-repair router violations plus all 41 of the shipped route's fell inside it.

R-0915-103 is right that the core is the ring interior and that the auto-sizer's
rectangle is "never a lever". What survived the ring adoption was the DENSITY
belonging to a die nobody placed into.

The rules this deck pins, in both directions:
  * a core the auto-sizer did NOT size derives its density from THAT core;
  * a core the auto-sizer DID size is byte-identical to today;
  * the rule may only ever SPREAD -- it is capped by the caller's own `--util`,
    so a design that is already dense keeps today's number exactly;
  * an unmeasurable cell area yields NO density and the caller keeps today's
    behaviour -- not a guess;
  * the chosen density and its BASIS are disclosed, including every clamp.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parents[1]
if str(PROG) not in sys.path:
    sys.path.insert(0, str(PROG))

from _ppa import area as P  # the density lives with the area logic  # noqa: E402

#: The design's own measured numbers, transcribed from the run above.
SUB_CELL_AREA = 2510 * 13.17          # 33,056.7 µm²
SUB_CORE = (1176, 1176)               # the R-0915-103 ring interior
SUB_AUTO_CORE = (258, 258)            # what the auto-sizer sized
UTIL_TODAY = 0.30                     # the --util default


# ── 1. the ring-pinned core: density comes from the REAL core ─────────────
def test_a_ring_pinned_core_does_not_keep_the_auto_sizers_density():
    d, basis = P.real_core_placement_density(
        SUB_CELL_AREA, *SUB_CORE, UTIL_TODAY)
    assert d is not None
    assert d < UTIL_TODAY, (d, basis)
    # and it is the SPREAD direction by a real margin, not a rounding
    assert d <= UTIL_TODAY / 2


def test_the_basis_states_the_natural_density_it_derived_from():
    _d, basis = P.real_core_placement_density(
        SUB_CELL_AREA, *SUB_CORE, UTIL_TODAY)
    assert "1176x1176" in basis
    assert "natural 2.39%" in basis            # the measured figure
    assert "33057um^2" in basis or "33056um^2" in basis


def test_the_floor_is_applied_and_DISCLOSED_never_silently():
    d, basis = P.real_core_placement_density(
        SUB_CELL_AREA, *SUB_CORE, UTIL_TODAY)
    assert d == P._PLACEMENT_DENSITY_FLOOR
    assert "placer floor" in basis and "asked" in basis


def test_the_derived_density_actually_spreads_the_design(): 
    """The point of the change, stated as area: the placer may now occupy
    several times the area it was being compressed into."""
    d, _ = P.real_core_placement_density(SUB_CELL_AREA, *SUB_CORE, UTIL_TODAY)
    before = SUB_CELL_AREA / UTIL_TODAY        # 110,190 µm² — the island
    after = SUB_CELL_AREA / d
    assert after > 5 * before
    assert after < SUB_CORE[0] * SUB_CORE[1]   # and still fits the core


# ── 2. the auto-sized core: byte-identical to today ───────────────────────
def test_an_auto_sized_core_keeps_todays_number_exactly():
    """THE NEGATIVE ARM. A core the auto-sizer produced is already at its
    target, so the cap returns the caller's own --util untouched."""
    d, basis = P.real_core_placement_density(
        SUB_CELL_AREA, *SUB_AUTO_CORE, UTIL_TODAY)
    assert d == UTIL_TODAY, (d, basis)
    assert "only ever SPREADS, never packs" in basis


@pytest.mark.parametrize("util", [0.20, 0.30, 0.40, 0.45])
def test_a_dense_core_is_never_PACKED_further_whatever_the_util(util):
    """The rule is one-directional. However the caller sets --util, a core
    whose natural density already exceeds it comes back at --util, never above."""
    d, _ = P.real_core_placement_density(SUB_CELL_AREA, 300, 300, util)
    assert d == util


def test_the_cap_is_the_callers_util_so_the_rule_can_only_spread():
    for util in (0.05, 0.10, 0.30):
        d, _ = P.real_core_placement_density(SUB_CELL_AREA, *SUB_CORE, util)
        assert d <= util


# ── 3. refusals — an unmeasurable density is not a guess ──────────────────
@pytest.mark.parametrize("area,w,h", [(0.0, 1176, 1176), (-1.0, 1176, 1176),
                                      (SUB_CELL_AREA, 0, 1176),
                                      (SUB_CELL_AREA, 1176, 0)])
def test_an_unmeasurable_input_returns_no_density_and_says_why(area, w, h):
    d, why = P.real_core_placement_density(area, w, h, UTIL_TODAY)
    assert d is None
    assert why and ("cell area" in why or "core area" in why)


# ── 4. the constants are the ones the docstring argues for ────────────────
def test_the_headroom_and_floor_are_pinned():
    """A silent change to either would move every ring-pinned design's density
    without a test noticing."""
    assert P._PLACEMENT_SPREAD_HEADROOM == 1.5
    assert P._PLACEMENT_DENSITY_FLOOR == 0.05


# ── 5. the MUTATION arm: reusing --util again must go red ─────────────────
def test_MUTATION_reusing_the_auto_sizers_util_is_caught():
    """Re-plant the defect: a 'density' that ignores the core and returns the
    caller's --util. Every ring-pinned assertion above must fail on it, or they
    pin nothing."""
    def defect(cell_area_um2, core_w, core_h, ceiling):
        return ceiling, "reused --util"          # the pre-R-0915-141 behaviour

    d, _ = defect(SUB_CELL_AREA, *SUB_CORE, UTIL_TODAY)
    assert d == UTIL_TODAY                        # the defect, reproduced
    real, _ = P.real_core_placement_density(SUB_CELL_AREA, *SUB_CORE, UTIL_TODAY)
    assert real != d, ("the fix must differ from the defect on the very case "
                       "that was measured")


# ── 6. spm is NOT the auto-sized case — pin what this change does to it ───
#: MEASURED on 192.168.1.120, lane icspm5 run9 (the spm that PASSED: DRC 0,
#: LVS PASS, STA PASS):
#:   die-um=auto → 85x85 (cells=273, avg_cell=13.17µm² [site-LEF])
#:   adopting it over the core-sized 85x85
#:   core := the die interior, 2376x2376 um inside the 3162x3162 um ring die
#:   pnr.tcl: global_placement -routability_driven -timing_driven -density 0.4
#: So spm is RING-PINNED too, and more extremely than subservient: its natural
#: density is 0.064 % against a 0.4 target. The dispatch brief assumed spm was
#: the auto-sized case; it is not, and this deck records that rather than
#: asserting an "unchanged" that is not true.
SPM_CELL_AREA = 273 * 13.17          # 3,595 µm²
SPM_CORE = (2376, 2376)              # the ring interior
SPM_UTIL_TODAY = 0.40                # what run9 actually emitted


def test_spm_is_ring_pinned_so_its_density_DOES_change_and_by_how_much():
    """HONEST PIN, not a claim of no-change. If this number is ever to move,
    a reader sees it here beside the run it came from."""
    d, basis = P.real_core_placement_density(
        SPM_CELL_AREA, *SPM_CORE, SPM_UTIL_TODAY)
    assert d == P._PLACEMENT_DENSITY_FLOOR       # 0.4 -> 0.05
    assert "natural 0.06%" in basis
    assert d < SPM_UTIL_TODAY


def test_spm_shows_compression_alone_does_not_predict_DRVs():
    """The counter-evidence this change must not pretend away: spm ran at a
    LARGER compression ratio than subservient and still signed off DRC 0.
    So deriving the density from the real core is a principled fix, NOT a
    proven cure for subservient's 41 -- that is what the copy-validation
    measures, and it is reported whatever it says."""
    sub_ratio = SUB_CELL_AREA / SUB_CORE[0] / SUB_CORE[1]
    spm_ratio = SPM_CELL_AREA / SPM_CORE[0] / SPM_CORE[1]
    assert spm_ratio < sub_ratio          # spm is the sparser of the two
    assert UTIL_TODAY / sub_ratio < SPM_UTIL_TODAY / spm_ratio
