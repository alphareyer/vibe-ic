"""R-0915-45 — the density pass condition the harness DECLARES must be RUN.

WHAT WENT WRONG, MEASURED ON A REAL RUN (lane icadc, 2026-09-16).

`analog_a2_topology_emit` drives a DC input at `supply/2 + vref/10` and states
its own pass condition in the deck it writes: "A DC input one tenth of full
scale above mid-scale, for which a modulator that converts MUST return a
bitstream density of 0.6 - neither 0, nor 1, nor 1/2."

`analog_resolution_stimulus` then replaces that DC input with a coherent tone
and writes into the same deck that it is "centred on the level the design's own
deck held it at, SO EVERY MEASUREMENT THAT DECK ALREADY TAKES KEEPS ITS
MEANING". It does not:

  * the deck's check averages `v(bit_out)` over 251 samples at a 1 MHz clock;
  * the tone period is 1536 samples;
  * 251/1536 = 0.163 of ONE period, and the window sits on the tone's POSITIVE
    PEAK (mean of sin over it = +0.9566).

So the window reports where the tone is, not what the converter does. Least
squares over the whole record (13824 samples = exactly 9 whole tone periods)
gave DC 0.4259 against the declared 0.6000 and amplitude 0.1742 against 0.3600:
a depressed baseline and a halved gain, two real errors that CANCEL at the
peak, and the deck reported 0.6056. All NINE PVT corners read 0.5618..0.6556
and looked healthy; the honest figure for the same nine is 0.3803..0.4723 and
none of them converts.

A declared pass condition that never runs is worse than no condition at all.

BOTH DIRECTIONS. A bitstream that MEETS the declared density must PASS, and the
grader must REFUSE by name rather than guess when the deck does not give it
what it needs — including the one case it genuinely cannot decide, an input
exactly at mid-scale, where density cannot tell a converter from a loop that
ignores its input.

No chip / SKU / foundry literal.
"""
from __future__ import annotations

import math
import sys

import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analog_converter_density_grade as G          # noqa: E402
import analog_real_corner_sweep as S                # noqa: E402

FS = 1_000_000.0            # sample clock
PERIOD = 1536               # tone period, in samples
N = PERIOD * 9              # a whole number of periods
F_SIG = FS / PERIOD


def _deck(vin_dc: float = 0.7, amp: float = 0.36,
          vrefp: float = 1.1, vrefn: float = 0.1, vdd: float = 1.2) -> str:
    return (
        f"v_vdd vdd 0 pwl(0n 0 100n {vdd} {int(N)}000n {vdd})\n"
        f"v_vrefp vrefp 0 {vrefp}\n"
        f"v_vrefn vrefn 0 {vrefn}\n"
        f"v_clk clk 0 pulse(0 {vdd} 1000n 1n 1n 499n 1000n)\n"
        f"v_in vin 0 sin({vin_dc} {amp} {F_SIG})\n"
        "xdut vdd 0 vin vrefp vrefn clk bit_out blk\n"
        ".control\n"
        f"tran 5n {N}000n\n"
        ".endc\n"
    )


def _dump(dc: float, amp: float, vdd: float = 1.2) -> str:
    """A 1-bit stream whose density follows `dc + amp*sin`, one row per sample."""
    out = []
    acc = 0.0
    # N + 1 rows so the record SPANS exactly N samples end to end, the way a
    # real `tran` dump does: with N rows the span is one sample short and the
    # grader honestly reports 8 whole periods instead of 9.
    for k in range(N + 1):
        want = dc + amp * math.sin(2.0 * math.pi * k / PERIOD)
        acc += max(0.0, min(1.0, want))
        bit = 1.0 if acc >= 1.0 else 0.0          # first-order density coder
        if bit:
            acc -= 1.0
        out.append(f" {k / FS:.8e}  {bit * vdd:.8e} ")
    return "\n".join(out) + "\n"


# ── the expectation comes from the deck's own levels ───────────────────────
def test_declared_density_is_read_from_the_decks_own_cards():
    expected, detail = G.declared_density(_deck())
    assert abs(expected - 0.6) < 1e-12                     # (0.7-0.1)/(1.1-0.1)
    assert detail["vin_dc_v"] == 0.7
    assert detail["reference_span_v"] == 1.0


def test_declared_density_follows_the_deck_rather_than_a_table():
    """Chip-AGNOSTIC: move the levels and the expectation moves with them."""
    expected, _ = G.declared_density(_deck(vin_dc=0.9, vrefp=1.4, vrefn=0.4))
    assert abs(expected - 0.5) < 1e-12                     # (0.9-0.4)/(1.4-0.4)


def test_a_deck_without_a_reference_pair_is_refused_by_name():
    deck = _deck().replace("v_vrefp vrefp 0 1.1\n", "")
    expected, detail = G.declared_density(deck)
    assert expected is None and detail["reason"] == G.NO_REFERENCE_PAIR


def test_a_deck_without_an_input_level_is_refused_by_name():
    deck = "\n".join(l for l in _deck().splitlines() if not l.startswith("v_in "))
    expected, detail = G.declared_density(deck)
    assert expected is None and detail["reason"] == G.NO_INPUT_LEVEL


# ── the measurement window is whole tone periods ───────────────────────────
def test_measured_density_uses_whole_tone_periods_at_the_sample_clock():
    d, detail = G.measured_density(_deck(), _dump(0.4259, 0.1742))
    assert detail["whole_tone_periods"] == 9
    assert detail["samples"] == N
    assert detail["sample_clock_hz"] == pytest.approx(FS)
    assert abs(d - 0.4259) < 0.01


def test_the_peak_window_the_deck_averages_over_reads_something_else():
    """THE DEFECT ARM, reproduced: the deck's own 251-sample window sits on the
    tone's peak, so it reports ~the declared 0.6 while the honest figure is
    0.43. The two must not agree — if they did, this whole ruling would be
    about nothing."""
    dump = _dump(0.4259, 0.1742)
    rows = dump.splitlines()[262:513]
    peak = sum(1 for r in rows if float(r.split()[1]) > 0.6) / float(len(rows))
    honest, _ = G.measured_density(_deck(), dump)
    assert peak > 0.55, peak                 # looks like the declared 0.6
    assert honest < 0.47, honest             # the truth
    assert peak - honest > 0.1


def test_a_dump_with_no_samples_is_refused_by_name():
    d, detail = G.measured_density(_deck(), "")
    assert d is None and detail["reason"] == G.NO_SAMPLES


def test_a_deck_with_no_tone_is_refused_by_name():
    deck = _deck().replace(f"sin(0.7 0.36 {F_SIG})", "0.7")
    d, detail = G.measured_density(deck, _dump(0.6, 0.0))
    assert d is None and detail["reason"] == G.NO_TONE_WINDOW


# ── the verdict, and the tolerance that is derived rather than chosen ──────
def test_a_converter_that_meets_the_declared_density_PASSES():
    """THE GREEN DIRECTION. Without this the grader could be a constant FAIL."""
    r = G.grade(_deck(), _dump(0.6, 0.36))
    assert r["status"] == "PASS", r
    assert abs(r["density_measured"] - 0.6) < 0.02


def test_the_measured_shortfall_FAILS():
    r = G.grade(_deck(), _dump(0.4259, 0.1742))
    assert r["status"] == "FAIL", r
    assert r["tolerance"] == pytest.approx(0.1)     # |0.6 - 0.5|
    assert r["error"] > r["tolerance"]


def test_the_tolerance_is_the_distance_to_the_degenerate_half():
    """Not a magic number: the declared condition distinguishes the expectation
    from the 1/2 of 'a loop that is ignoring its input', so the pass band is
    exactly the interval that stays strictly closer to the expectation."""
    assert G.grade(_deck(), _dump(0.55, 0.30))["status"] == "PASS"   # err 0.05
    assert G.grade(_deck(), _dump(0.49, 0.30))["status"] == "FAIL"   # err 0.11


def test_an_input_exactly_at_mid_scale_is_NOT_MEASURED_not_a_pass():
    """The one case density genuinely cannot decide. Refusing by name beats
    inventing a bound — a converter and a loop that ignores its input produce
    the same 0.5 here."""
    deck = _deck(vin_dc=0.6)
    r = G.grade(deck, _dump(0.5, 0.36))
    assert r["status"] == "NOT_MEASURED"
    assert r["reason"] == G.AT_MID_SCALE


# ── the rows the sweep publishes ───────────────────────────────────────────
def _spec(enob_min=14.0, osr=256.0):
    rows = [{"name": "osr", "target": osr}]
    if enob_min is not None:
        rows.append({"name": "enob", "min": enob_min, "unit": "bit"})
    return {"specs": rows}


def test_the_block_spec_supplies_the_enob_minimum_and_the_osr():
    assert S._enob_min_from_spec(_spec()) == 14.0
    assert S._osr_of(_spec()) == 256.0
    assert S._enob_min_from_spec(_spec(enob_min=None)) is None


def test_an_sndr_minimum_is_converted_the_standard_way():
    got = S._enob_min_from_spec({"specs": [{"name": "sndr_db", "min": 86.0}]})
    assert abs(got - (86.0 - 1.76) / 6.02) < 1e-9


def test_a_block_that_declares_no_resolution_gets_no_new_rows():
    """Nothing changes for a non-converter: the settle metric stays the only
    row, exactly as before."""
    rows, summary = S.grade_converter_resolution([], Path("."), _spec(enob_min=None))
    assert rows == []
    assert summary["enob_min"] is None
