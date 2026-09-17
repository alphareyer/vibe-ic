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
