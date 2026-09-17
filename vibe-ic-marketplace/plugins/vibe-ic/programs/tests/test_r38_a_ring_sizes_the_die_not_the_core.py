"""r38 (subservient x gf180mcuD as a DIE): a pad ring sizes the DIE by its
perimeter; it does not decide how much area the cells need.

MEASURED on r37: 33 pads forced a 1962 um die, the core was taken as
`die - 2*393` = 1176 um at 10.4 % utilisation, and the same netlist that closed
setup at +0.03 ns as a 413 um hardmacro missed by 13.1 ns at SS — on paths
whose delay is wire. The auto-sizer had already answered the area question and
its answer was thrown away the moment the ring grew the die.

Both directions: the core-sized rectangle is centred when it is SMALLER than
the ring's inner rectangle; the ring inset stays the floor (the core can never
reach under the pads); and nothing moves when nobody auto-sized a core, when
the core-sized answer is not smaller, or when it is degenerate.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as R  # noqa: E402

RING_DIE, RING_PAD = 1962, 393      # r37's own numbers


def test_the_core_sized_rectangle_is_centred_in_the_ring_die():
    pad = R.ring_die_core_pad(RING_DIE, RING_DIE, RING_PAD, (500, 500))
    assert pad == (RING_DIE - 500) // 2 == 731
    assert RING_DIE - 2 * pad == 500                      # the core it asked for
    assert pad > RING_PAD                                 # further in than the ring


def test_the_ring_inset_is_always_the_floor():
    # a core-sized answer WIDER than the ring's inner rectangle must not pull
    # the core out under the pads
    assert R.ring_die_core_pad(RING_DIE, RING_DIE, RING_PAD, (1900, 1900)) == RING_PAD
    assert R.ring_die_core_pad(RING_DIE, RING_DIE, RING_PAD,
                               (RING_DIE - 2 * RING_PAD, RING_DIE - 2 * RING_PAD)) == RING_PAD


@pytest.mark.parametrize("sized", [None, (0, 0), (-5, -5)])
def test_without_an_auto_sized_core_nothing_moves(sized):
    assert R.ring_die_core_pad(RING_DIE, RING_DIE, RING_PAD, sized) == RING_PAD


def test_a_rectangular_answer_uses_its_larger_side():
    # one inset serves both axes, so the larger side is what must fit
    pad = R.ring_die_core_pad(RING_DIE, RING_DIE, RING_PAD, (400, 900))
    assert RING_DIE - 2 * pad >= 900 and pad == (RING_DIE - 900) // 2


def test_a_smaller_die_dimension_decides_the_fit():
    pad = R.ring_die_core_pad(2400, 1962, RING_PAD, (500, 500))
    assert pad == (1962 - 500) // 2


# ---- and when that core turns out too small, the CORE is what grows --------

def test_an_over_utilised_core_grows_inside_the_ring_die():
    """r40: the 225 um core measured 114.217 % and the retry tried to grow the
    1962 um RING die, hit the 2000 um cap, and FAILed telling the operator to
    raise a --die-um the ring had decided."""
    pad = R.ring_core_pad_for_util(RING_DIE, RING_DIE, 225, 225,
                                   114.217, 40.0, RING_PAD)
    assert pad is not None and pad < (RING_DIE - 225) // 2      # the core grew
    core = RING_DIE - 2 * pad
    assert core > 225 and pad >= RING_PAD                       # never under the pads
    # it grew far enough to reach the target, not further than the ring allows
    assert core * core >= 225 * 225 * (114.217 / 40.0) * 0.98


def test_growth_stops_at_the_rings_own_inset():
    # a core that still misses the target but is already at the ring floor
    assert R.ring_core_pad_for_util(RING_DIE, RING_DIE,
                                    RING_DIE - 2 * RING_PAD,
                                    RING_DIE - 2 * RING_PAD,
                                    114.2, 40.0, RING_PAD) is None
    # one that needs more than the ring leaves is clamped to the floor
    assert R.ring_core_pad_for_util(RING_DIE, RING_DIE, 700, 700,
                                    114.2, 40.0, RING_PAD) == RING_PAD


def test_a_core_already_inside_the_target_does_not_move():
    assert R.ring_core_pad_for_util(RING_DIE, RING_DIE, 900, 900,
                                    30.0, 40.0, RING_PAD) is None


# ---- and a clock tree that had to be downsized means the core is too tight --

def test_one_loosen_rung_of_core_growth():
    """r42: the legalizer's last resort swapped 43 clkbuf_16 down to clkbuf_4;
    placement went legal and setup fell from +0.32 ns to -12.27 ns."""
    pad = R.ring_core_pad_one_loosen_rung(RING_DIE, RING_DIE, 382, 382, RING_PAD)
    assert pad is not None
    core = RING_DIE - 2 * pad
    assert 382 < core <= RING_DIE - 2 * RING_PAD
    # the ladder's own first ratio, not a number typed here
    r0, r1 = R._ROUTE_LOOSEN_UTIL_LADDER[0], R._ROUTE_LOOSEN_UTIL_LADDER[1]
    assert core == int(382 * (r0 / r1) ** 0.5)


def test_growth_after_a_downsize_stops_at_the_ring_inset():
    assert R.ring_core_pad_one_loosen_rung(
        RING_DIE, RING_DIE, RING_DIE - 2 * RING_PAD, RING_DIE - 2 * RING_PAD,
        RING_PAD) is None
    # a core one rung from the floor is clamped to the floor, never past it
    assert R.ring_core_pad_one_loosen_rung(
        RING_DIE, RING_DIE, 900, 900, RING_PAD) >= RING_PAD


def test_a_ladder_that_does_not_loosen_grows_nothing():
    assert R.ring_core_pad_one_loosen_rung(RING_DIE, RING_DIE, 382, 382,
                                           RING_PAD, ladder=(0.5,)) is None
    assert R.ring_core_pad_one_loosen_rung(RING_DIE, RING_DIE, 382, 382,
                                           RING_PAD, ladder=(0.2, 0.5)) is None
