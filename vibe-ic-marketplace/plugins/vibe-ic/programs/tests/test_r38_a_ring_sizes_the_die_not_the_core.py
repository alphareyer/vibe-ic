"""r38 (subservient x gf180mcuD as a DIE): a pad ring sizes the DIE by its
perimeter; it does not decide how much area the cells need.

MEASURED on r37: 33 pads forced a 1962 um die, the core was taken as
`die - 2*393` = 1176 um at 10.4 % utilisation, and the same netlist that closed
setup at +0.03 ns as a 413 um hardmacro missed by 13.1 ns at SS — on paths
whose delay is wire. The auto-sizer had already answered the area question and
its answer was thrown away the moment the ring grew the die.

SUPERSEDED IN PART BY R-0915-103 (owner, 2026-09-21), and the r37 measurement
above is KEPT because it is real and it is the number at risk. The ruling
reverses the CENTRING half for a ring-pinned DIE only: the core is now the die
interior inside the ring floor, because spm x gf180mcuD measured the opposite
failure on the same PDK — an island core gave a pad->core net no placement ROW
to repeat in, 2057 buffers against synthesis's 273 cells and zero, and setup
-1.97 ns at SS, where the same design with the interior as its core closed at
+8.21 ns with 522 buffers. See
`test_r0915_103_a_ring_pinned_core_is_the_die_interior.py` for that control and
`ring_die_core_pad`'s own docstring for both measurements side by side.

What is NOT superseded, and is still pinned below: the ring inset is the floor
(the core can never reach under the pads), the growth helpers refuse past it,
and a run that auto-sized nothing is untouched.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as R  # noqa: E402

RING_DIE, RING_PAD = 1962, 393      # r37's own numbers


def test_the_core_sized_rectangle_is_no_longer_centred_r0915_103():
    """WAS: `pad == (RING_DIE - 500)//2 == 731`, the auto-sizer's rectangle
    centred in the ring's die. R-0915-103 makes the core the interior, so the
    inset is the ring floor and the core is `die - 2*floor`."""
    pad = R.ring_die_core_pad(RING_DIE, RING_DIE, RING_PAD, (500, 500))
    assert pad == RING_PAD
    assert RING_DIE - 2 * pad == RING_DIE - 2 * RING_PAD   # the interior
    assert pad < (RING_DIE - 500) // 2                     # not the island


def test_the_ring_inset_is_always_the_floor():
    # a core-sized answer WIDER than the ring's inner rectangle must not pull
    # the core out under the pads
    assert R.ring_die_core_pad(RING_DIE, RING_DIE, RING_PAD, (1900, 1900)) == RING_PAD
    assert R.ring_die_core_pad(RING_DIE, RING_DIE, RING_PAD,
                               (RING_DIE - 2 * RING_PAD, RING_DIE - 2 * RING_PAD)) == RING_PAD


@pytest.mark.parametrize("sized", [None, (0, 0), (-5, -5)])
def test_without_an_auto_sized_core_nothing_moves(sized):
    assert R.ring_die_core_pad(RING_DIE, RING_DIE, RING_PAD, sized) == RING_PAD


def test_a_rectangular_answer_changes_nothing_either_r0915_103():
    # WAS: the larger side decided the inset. The interior is larger than any
    # answer the auto-sizer can give here, so it holds both axes outright.
    pad = R.ring_die_core_pad(RING_DIE, RING_DIE, RING_PAD, (400, 900))
    assert pad == RING_PAD and RING_DIE - 2 * pad >= 900


def test_a_smaller_die_dimension_no_longer_decides_a_fit_r0915_103():
    # WAS: the shorter side chose the centring inset. There is no centring now.
    assert R.ring_die_core_pad(2400, 1962, RING_PAD, (500, 500)) == RING_PAD


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
