"""The incremental CIFB coefficient must be bounded by the CLOSED-loop
excursion, not by an open-loop ramp that the feedback never lets happen.

WHAT WENT WRONG, MEASURED (lane icadc, 2026-09-16).
`_incremental_cifb_coefficients` returned

    a = (swing * factorial(order) / (vref * osr ** order)) ** (1 / order)

which for the real declaration (order 2, osr 256, vref 1.0, vdd 1.2) is
**0.005523**, i.e. `ci/cs = 181`. The emitted deck carries `ci/cs = 183.5`.

That expression bounds the ramp an integrator would make with NO FEEDBACK. In a
closed single-bit loop the DAC removes charge every cycle, so the ramp never
happens. Measured on an ideal-element harness of the very topology this library
emits — same nets, phases, DAC polarity, 256-clock reset and clock, ideal
switches and amplifiers — the open-loop expression overestimates the real
excursion by more than three orders of magnitude:

    a         formula predicts    MEASURED closed-loop swing
    0.00552   0.9996 V            0.0797 V      12.5x over
    0.25      2048 V              0.3577 V      5700x over

So it shrank the coefficient by orders of magnitude to protect a swing that was
never at risk, and the shrunken coefficient collapsed the converter's transfer.
Sweeping `ci/cs` on that harness with everything else identical, density at
vin 0.700 / 0.600 against an ideal 0.6000 / 0.5000:

    ci/cs    a         slope     vo1 swing
      2      0.5000    1.0291    1.2446 V   <- OVERFLOWS a 1.2 V rail
      4      0.2500    1.0105    0.3577 V   <- correct, 30 % of the rail
      8      0.1250    0.9381    0.2066 V
     16      0.0625    0.9472    0.1411 V
     32      0.0313    0.8652    0.1400 V
     64      0.0156    0.6922    0.1326 V
    128      0.0078    0.5647    0.0940 V
    183.5    0.0055    0.3846    0.0797 V   <- what the old formula returned

THE FUNCTION'S OWN DOCSTRING ALREADY SAID THE BOUND WAS NOT ENOUGH — "an
overflow bound is NECESSARY and is not SUFFICIENT, and a design that satisfies
it is not thereby a converter" — and the generator shipped its output as the
final coefficient anyway. The missing half is what this fixes.

BOTH DIRECTIONS. The derived coefficient must land in the band the sweep shows
converting, AND it must never reach the value the sweep shows overflowing; the
old expression is kept and REPORTED so the two bounds can be compared, and the
test asserts the returned value is not it.

No chip / SKU / foundry literal.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analog_a2_topology_emit as A2        # noqa: E402

CONSTS = {"integrator_swing_fraction_of_vdd": 0.833}
DECL = {"osr": 256, "vref": 1.0, "vdd": 1.2}

# The band the ideal-harness sweep shows converting (slope >= 0.93), and the
# value it shows overflowing. Both are MEASUREMENTS, quoted in the docstring.
CONVERTS_MIN, CONVERTS_MAX = 0.0625, 0.25
OVERFLOWS_AT = 0.5


def _a(order=2, **over):
    d = dict(DECL); d.update(over)
    return A2._incremental_cifb_coefficients(order, d, CONSTS)[0]


def _openloop(order=2, **over):
    d = dict(DECL); d.update(over)
    swing = d["vdd"] * CONSTS["integrator_swing_fraction_of_vdd"]
    return (swing * math.factorial(order)
            / (d["vref"] * float(d["osr"]) ** order)) ** (1.0 / order)


# ── the defect arm ─────────────────────────────────────────────────────────
def test_the_returned_coefficient_is_not_the_open_loop_ramp_bound():
    """THE DEFECT ARM. The old expression is what shipped; it must no longer be
    the answer."""
    assert _a() != pytest.approx(_openloop(), rel=1e-6)
    assert _openloop() < CONVERTS_MIN / 10          # and it is far below the band


def test_the_open_loop_bound_is_still_computed_and_reported():
    """It is kept, not deleted: a future order or OSR outside what was measured
    needs to see how far apart the two bounds are."""
    A2._COEFFICIENT_BOUNDS_SEEN.clear()
    _a()
    rec = A2._COEFFICIENT_BOUNDS_SEEN[-1]
    assert rec["a_openloop_ramp_bound"] == pytest.approx(_openloop(), rel=1e-9)
    assert rec["a_returned"] == pytest.approx(_a(), rel=1e-9)
    assert rec["a_closed_loop_bound"] == pytest.approx(_a(), rel=1e-9)


# ── the converting band ────────────────────────────────────────────────────
def test_the_coefficient_lands_in_the_band_the_harness_shows_converting():
    a = _a()
    assert CONVERTS_MIN <= a <= CONVERTS_MAX, a


def test_it_lands_on_the_measured_sweet_spot_for_this_declaration():
    """`ci/cs = 4` is the largest ratio the sweep shows converting without
    overflow — slope 1.0105 at 30 % of the rail."""
    assert 1.0 / _a() == pytest.approx(4.0, abs=0.01)


def test_it_never_reaches_the_value_the_harness_shows_overflowing():
    """THE OTHER DIRECTION, and it must hold for declarations far from this
    one: `a = 0.5` swings 1.2446 V on a 1.2 V rail. A rail-derived bound alone
    would let a 5 V declaration ask for `a = 2`, which a single-bit loop does
    not carry, so the derivation also carries the measured converting ceiling.
    """
    for vdd in (1.0, 1.2, 1.8, 3.3, 5.0):
        for vref in (0.5, 1.0, 1.8):
            a = _a(vdd=vdd, vref=vref)
            assert a < OVERFLOWS_AT, (vdd, vref, a)
            assert a <= CONVERTS_MAX, (vdd, vref, a)


# ── the rule follows the declaration, not a constant ───────────────────────
def test_a_smaller_declared_swing_gives_a_proportionally_smaller_coefficient():
    assert _a(vdd=0.6) == pytest.approx(_a(vdd=1.2) / 2.0, rel=1e-9)


def test_a_larger_reference_gives_a_proportionally_smaller_coefficient():
    assert _a(vref=2.0) == pytest.approx(_a(vref=1.0) / 2.0, rel=1e-9)


def test_the_coefficient_no_longer_collapses_with_OSR():
    """The old expression fell as `osr**-1`; that is what made a high-OSR
    declaration unconvertible. The closed-loop bound does not depend on OSR at
    all, which is the point: the feedback, not the window length, bounds the
    excursion."""
    assert _a(osr=64) == pytest.approx(_a(osr=1024), rel=1e-12)
    assert _openloop(osr=64) > 10 * _openloop(osr=1024)


# ── the refusals are preserved ─────────────────────────────────────────────
@pytest.mark.parametrize("bad", [{"osr": 0}, {"vref": 0}, {"vdd": 0}])
def test_a_degenerate_declaration_is_refused_not_defaulted(bad):
    with pytest.raises(A2.LibraryEntryError):
        _a(**bad)


def test_order_below_one_is_refused():
    with pytest.raises(A2.LibraryEntryError):
        _a(order=0)


def test_every_stage_gets_the_same_coefficient_as_before():
    assert A2._incremental_cifb_coefficients(2, DECL, CONSTS) == [_a(), _a()]
    assert len(A2._incremental_cifb_coefficients(1, DECL, CONSTS)) == 1
