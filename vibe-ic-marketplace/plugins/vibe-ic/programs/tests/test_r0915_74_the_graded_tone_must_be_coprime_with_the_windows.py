"""R-0915-74 — the tone an INCREMENTAL converter is graded at must be COPRIME
with the number of conversion windows it is decoded over.

WHAT WAS WRONG, MEASURED (lane icadc, F167). The stimulus rule picked
`band_bins // HARMONICS_IN_BAND` — one third of the decoded Nyquist, BY
CONSTRUCTION. At OSR 256 over a 13824-sample record that is bin 9 of 54
conversion windows: exactly ONE TONE CYCLE PER SIX WINDOWS, `gcd(9, 54) = 9`.

An incremental converter's decoded error is DETERMINISTIC and periodic with the
tone. When the tone's period in conversion windows is a small integer, so is
the error's, and the error becomes a component AT THE SIGNAL'S OWN FREQUENCY —
which an SNDR removes as signal.

BOTH DIRECTIONS, on a pure-arithmetic ideal CIFB2 (no simulator), same loop,
same graded span of 48 decoded windows, tone bin swept:

    every bin from 2 to 15 ............ 10.7 - 11.6 bit
    bin 8, which is 48/6 .............. 13.37 bit

and against the METHOD-FREE truth — the decoded value against the input the
loop actually saw, no spectrum anywhere — bin 8 is the WORST of the set
(rms 3.35e-4 where the others are 2.7e-4 to 3.0e-4, i.e. 10.54 bit).
**The instrument read the worst placement as 2.8 bit the best.** That is a
false green in a graded metric, and it is systematic: one third of a Nyquist is
a simple rational of the decoded record for every OSR whose band edge is
divisible by three.

No chip / SKU / foundry literal.
"""
from __future__ import annotations

import math
import statistics
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analog_a2_topology_emit as a2                # noqa: E402
import analog_incremental_decimator as D            # noqa: E402
import analog_resolution_stimulus as S              # noqa: E402
import analog_transient_record as R                 # noqa: E402

N = 256
COEFF = 0.2499
PHASES = [2 * math.pi * i / 12 for i in range(12)]


# ── the RULE cannot produce the bad placement ─────────────────────────────
@pytest.mark.parametrize("windows", list(range(56, 140)))
def test_the_rule_never_returns_a_cycle_count_sharing_a_factor(windows):
    """THE NEGATIVE CONTROL, over every record length the rule can be asked
    about: `gcd(cycles, graded_windows)` is 1 or the tone is not emitted."""
    tone = S.incremental_tone(windows)
    if tone is None:
        return
    assert math.gcd(tone["cycles"], tone["graded_windows"]) == 1, tone


def test_the_old_placement_is_exactly_what_the_rule_now_refuses():
    """The defect, stated as the arithmetic it was: 54 conversion windows, the
    old rule's bin 9, `gcd(9, 53) = 1` on the GRADED span but the emitted tone
    was 9 cycles per 54 — and 54 windows admits no gradable tone at all now,
    so the record has to grow."""
    assert S.incremental_tone(54) is None
    assert S.incremental_tone(55) is None
    assert S.incremental_tone(56) == {
        "graded_windows": 55, "cycles": 9,
        "rule": "coprime_with_the_conversion_window_count"}
    assert math.gcd(9, 54) == 9           # what the old rule emitted
    assert math.gcd(9, 55) == 1           # what this one does


def test_the_rule_keeps_the_harmonics_in_band_and_the_cycle_floor():
    """Nothing about the coprimality is allowed to loosen the two properties
    the old rule had: the tone is ODD, and its harmonics are graded."""
    for windows in range(56, 200):
        tone = S.incremental_tone(windows)
        if tone is None:
            continue
        assert tone["cycles"] % 2 == 1
        assert tone["cycles"] >= S._MIN_SIGNAL_CYCLES
        assert (tone["cycles"] * S.HARMONICS_IN_BAND
                <= tone["graded_windows"] / 2)


# ── the record floor follows the domain ───────────────────────────────────
def test_the_floor_is_counted_in_windows_only_when_the_entry_says_so():
    """BOTH DIRECTIONS on the floor itself. A converter graded on its raw
    bitstream keeps the number it always had, to the byte."""
    raw = S.coherent_record_samples(256.0)
    dec = S.coherent_record_samples(256.0, 256.0)
    assert raw["samples"] == 13824
    assert raw["rule"] == "coherent_over_the_raw_record"
    assert dec["samples"] == 56 * 256
    assert dec["conversion_windows"] == S.incremental_record_windows()
    assert dec["graded_windows"] == dec["conversion_windows"] - 1


def test_the_entry_declares_which_domain_it_is_graded_in():
    """Publishing a conversion window is NOT that declaration — a converter can
    have one and still be graded on its raw bitstream — so the entry says it."""
    ir = a2.build_ir("mod", "delta_sigma", {}, a2.LIBRARY["delta_sigma"],
                     {"order": 2.0, "vdd": 1.2, "osr": 256.0, "enob": 14.0,
                      "vref": 1.0, "fclk": 1.0, "fclk_max": 1.0},
                     None, Path("."), "sky130", {},
                     measured_params={"cap_area_ff_per_um2": 1.5,
                                      "rsheet_ohm_per_sq": 260.0,
                                      "vth_n_extracted_v": 0.42,
                                      "cap_perim_ff_per_um": 0.1})
    assert ir["constants"][R.DECODED_IN_WINDOWS_CONSTANT] == 1.0
    # and the record it derives is the windowed floor, not the raw one
    assert ir["constants"]["record_clocks"] == float(
        S.coherent_record_samples(256.0, ir["constants"]["window_clocks"]
                                  )["samples"])


# ── and the MEASUREMENT the rule exists for ───────────────────────────────
def _decode(phase, m, c):
    w = D.matched_weights(N, 2, COEFF, 1)
    wn = sum(D.input_weights(N, 2, COEFF))
    f_sig = c / (m * N)
    out = []
    for win in range(m + 1):
        i1 = i2 = 0.0
        pipe = [0.0]
        acc = 0.0
        for k in range(N):
            n = win * N + k
            u = 0.20 + 0.72 * math.sin(2 * math.pi * f_sig * n + phase)
            v = 1.0 if i2 > 0.0 else -1.0
            d = pipe[0]
            i2 += COEFF * (i1 - d)
            i1 += COEFF * (u - d)
            pipe = [v]
            acc += w[k] * v
        out.append(acc / wn)
    return out[1:]


def _power(x, top):
    n = len(x)
    mean = sum(x) / n
    xs = [v - mean for v in x]
    out = []
    for k in range(1, top + 1):
        re = im = 0.0
        ww = -2.0 * math.pi * k / n
        for i, v in enumerate(xs):
            a = ww * i
            re += v * math.cos(a)
            im += v * math.sin(a)
        out.append(re * re + im * im)
    return out


def _enob(samples, c):
    n = len(samples)
    top = n // 2 - 1
    p = _power(samples, top)
    sig = sum(p[b - 1] for b in (c - 1, c, c + 1) if 1 <= b <= top)
    noise = sum(p) - sig
    return (10.0 * math.log10(sig / noise) - 1.76) / 6.02


def _worst(m, c):
    return min(_enob(_decode(ph, m, c), c) for ph in PHASES)


@pytest.fixture(scope="module")
def sweep():
    """The same loop read at every tone bin the old rule could have produced
    over one graded span. 48 windows is what the old rule's record trimmed to."""
    return {c: _worst(48, c) for c in (5, 6, 7, 8, 9, 10, 11)}


def test_the_bin_that_shares_a_factor_reads_the_loop_several_bits_too_high(sweep):
    """THE DEFECT DIRECTION, and its size."""
    bad = sweep[8]                      # gcd(8, 48) = 8
    others = [v for c, v in sweep.items() if c != 8]
    assert bad > max(others) + 1.5, (
        f"bin 8 {bad:.3f} against {sorted(round(v, 3) for v in others)} — the "
        f"whole finding is that this placement reads several bits too high")


def test_every_coprime_bin_agrees_within_half_a_bit(sweep):
    """THE FIX DIRECTION. Once the shared factor is gone the measurement is a
    property of the LOOP and not of where the tone was put."""
    good = [v for c, v in sweep.items() if math.gcd(c, 48) == 1]
    assert len(good) >= 3, sorted(sweep)
    assert max(good) - min(good) < 0.5, sorted(round(v, 3) for v in good)


def test_the_rules_own_placement_is_one_of_the_agreeing_ones():
    """End to end: the tone the rule emits reads the loop the same as every
    other honest placement, not like the one it used to emit."""
    tone = S.incremental_tone(56)
    got = _worst(tone["graded_windows"], tone["cycles"])
    honest = statistics.median(
        [_worst(48, c) for c in (5, 7, 11)])
    assert abs(got - honest) < 0.6, f"rule {got:.3f} vs honest {honest:.3f}"
