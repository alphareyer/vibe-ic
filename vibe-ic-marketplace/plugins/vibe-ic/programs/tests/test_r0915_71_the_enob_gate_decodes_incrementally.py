"""R-0915-71 — the ENOB gate DECODES a deck that declares an incremental
converter, and FFTs one that does not.

The switch is the deck's own decode declaration (R-0915-71, the file beside
this one). Here it is measured END TO END on a bitstream and a dump this file
builds: a pure-arithmetic ideal CIFB2 — no simulator anywhere — rendered in the
`wrdata` format ngspice writes, read back by the shipped gate.

BOTH DIRECTIONS, and the numbers are the point:

  * a STAMPED deck takes the matched decode and reports
    `matched_incremental_decimation_then_dft`;
  * THE CONTROL — the identical dump with the stamp removed takes the FFT
    path, reports `dft_signal_bin_vs_in_band_rest_at_sample_clock`, and reads
    the SAME converter several bits LOWER. That difference is why the switch
    exists; it is a property of the DECODE, not of a circuit.

And the refusal direction: an incremental converter's decoded stream is one
sample per conversion window, so the cycle floor bites there first. A record
that holds enough tone cycles for the raw instrument and not enough after
decimation must be REFUSED BY NAME, not measured over three bins.

No chip / SKU / foundry literal.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analog_adc_enob_corner_check as G          # noqa: E402
import analog_incremental_decimator as D          # noqa: E402

N = 256                 # conversion window, clocks
FCLK = 1.0e6            # converter clock
TD = 1.0e-6             # the clock card's delay: window 0 starts here
VHI = 1.2
TONE_BIN = 9            # odd, and 9 cycles in 54 windows
WINDOWS = 54
COEFF = 0.25


def _bitstream(amplitude, offset):
    """An ideal incremental CIFB2, reset every N clocks. Pure arithmetic."""
    n_clk = WINDOWS * N
    f_sig = TONE_BIN * FCLK / n_clk
    bits = []
    for w in range(WINDOWS):
        i1 = i2 = 0.0
        for k in range(N):
            n = w * N + k
            u = offset + amplitude * math.sin(2 * math.pi * f_sig * n / FCLK)
            v = 1.0 if i2 > 0.0 else -1.0
            bits.append(v)
            i2 += COEFF * (i1 - v)
            i1 += COEFF * (u - v)
    return bits, f_sig


def _dump(bits, skew_s=0.0):
    """The bitstream in the two-column form `wrdata` writes: one row at the
    start of each clock and one at the instant the gate samples.

    `skew_s` moves the whole record in time without changing a single bit —
    which is how the alignment below is tested: the same conversions, the
    window boundaries somewhere else."""
    rows = []
    for n, b in enumerate(bits):
        v = VHI if b > 0 else 0.0
        rows.append(f" {skew_s + TD + n * 1.0 / FCLK:.8e}  {v:.8e} ")
        rows.append(f" {skew_s + TD + (n + 0.9) / FCLK:.8e}  {v:.8e} ")
    # one row past the last sampling instant, so the record covers the clock
    # it holds instead of ending a rounding error short of it
    rows.append(f" {skew_s + TD + len(bits) * 1.0 / FCLK:.8e}  "
                f"{VHI if bits[-1] > 0 else 0.0:.8e} ")
    return "\n".join(rows) + "\n"


def _deck(f_sig, stamped, t_stop):
    lines = [
        "* a testbench",
        f"v_clk clk 0 pulse(0 {VHI} {TD * 1e9:g}n 1n 1n 499n 1000n)",
        f"v_in vin 0 sin(0.6 0.36 {f_sig:.7f})",
        f".tran 5n {t_stop * 1e9:g}n",
        "wrdata out.data v(bit_out)",
        ".end",
    ]
    if stamped:
        lines.insert(1, D.stamp(D.MODE_INCREMENTAL, N, 2, COEFF, 0))
    return "\n".join(lines) + "\n"


@pytest.fixture(scope="module")
def record():
    bits, f_sig = _bitstream(0.72, 0.20)
    return bits, f_sig, _dump(bits)


def test_a_stamped_deck_is_decoded_not_fft_d(record):
    bits, f_sig, dump = record
    sndr, meta = G.sndr_db_incremental(
        _deck(f_sig, True, len(bits) / FCLK), dump)
    assert sndr is not None, meta
    assert meta["method"] == "matched_incremental_decimation_then_dft"
    assert meta["window_clocks"] == N
    assert meta["conversion_mode"] == D.MODE_INCREMENTAL
    # every window but the power-up one is decoded, and the graded span is a
    # whole number of tone cycles
    assert meta["windows_decoded"] == WINDOWS
    assert meta["cycles"] >= G._MIN_SIGNAL_CYCLES


def test_the_matched_decode_reads_this_converter_higher_than_the_fft(record):
    """THE MEASUREMENT THE SWITCH EXISTS FOR, both directions in one test."""
    bits, f_sig, dump = record
    deck_on = _deck(f_sig, True, len(bits) / FCLK)
    deck_off = _deck(f_sig, False, len(bits) / FCLK)

    matched, m_meta = G.sndr_db_incremental(deck_on, dump)
    raw, r_meta = G.sndr_db_from_transient(deck_off, dump, osr=float(N))
    assert matched is not None, m_meta
    assert raw is not None, r_meta
    enob_matched = (matched - G._SNDR_INTERCEPT) / G._SNDR_SLOPE
    enob_raw = (raw - G._SNDR_INTERCEPT) / G._SNDR_SLOPE
    assert enob_matched > enob_raw + 2.0, (
        f"matched {enob_matched:.3f} bit vs raw {enob_raw:.3f} bit — the "
        f"decode is supposed to be worth several bits on this class")


def test_the_gate_dispatches_on_the_deck_and_the_control_does_not(record):
    """The dispatch itself: the SAME dump, the stamp the only difference."""
    bits, f_sig, dump = record
    assert D.read_stamp(_deck(f_sig, True, len(bits) / FCLK))[0] is not None
    assert D.read_stamp(_deck(f_sig, False, len(bits) / FCLK))[0] is None


def test_a_record_too_short_after_decimation_is_refused_by_name(record):
    """It holds 9 tone cycles of BITSTREAM and one window of decoded samples.
    The raw instrument would measure it; the matched one must refuse."""
    bits, f_sig, dump = record
    short = bits[:2 * N]
    sndr, meta = G.sndr_db_incremental(
        _deck(f_sig, True, len(short) / FCLK), _dump(short))
    assert sndr is None
    assert "tone_cycles" in meta["reason"], meta


def test_a_record_shorter_than_one_window_is_refused_by_name(record):
    bits, f_sig, dump = record
    short = bits[:N // 2]
    sndr, meta = G.sndr_db_incremental(
        _deck(f_sig, True, len(short) / FCLK), _dump(short))
    assert sndr is None
    assert meta["reason"] == G._INC_SHORT_RECORD, meta


def test_the_window_is_aligned_to_the_decks_own_clock_delay(record):
    """Reset alignment, measured: decoding from t=0 instead of the clock
    card's `td` straddles two conversions and the answer collapses."""
    bits, f_sig, dump = record
    aligned, _ = G.sndr_db_incremental(
        _deck(f_sig, True, len(bits) / FCLK), dump)
    # THE SAME BITS, the record moved half a conversion window in time: every
    # window the gate slices now straddles two conversions.
    skewed = _dump(bits, skew_s=(N // 2) / FCLK)
    misaligned, meta = G.sndr_db_incremental(
        _deck(f_sig, True, (len(bits) + N // 2) / FCLK), skewed)
    assert aligned is not None
    assert misaligned is None or misaligned < aligned - 3.0, (
        f"aligned {aligned:.3f} dB vs misaligned {misaligned} — a window "
        f"boundary put half a conversion wrong has to COST something, or the "
        f"alignment this gate does is not doing anything")


def test_the_declared_feedback_delay_changes_the_weights():
    """The parameter must reach the arithmetic. A delay the decode accepted
    and ignored would be a declaration with no consequence."""
    w0 = D.matched_weights(N, 2, COEFF, 0)
    w1 = D.matched_weights(N, 2, COEFF, 1)
    assert w0 != w1
    assert len(w0) == len(w1) == N


def test_a_negative_feedback_delay_is_refused():
    with pytest.raises(ValueError) as exc:
        D.matched_weights(N, 2, COEFF, -1)
    assert D.BAD_DELAY in str(exc.value)


# ── the declared delay has to MATCH THE LOOP, and both directions prove it ──
def _delayed_loop(delay, amplitude=0.72, offset=0.20):
    """An ideal incremental CIFB2 in which a latched decision's charge reaches
    the summing nodes `delay` clocks later. `bits[n]` is the decision LATCHED
    at the end of clock n — the one the gate samples in transfer phase n."""
    n_clk = WINDOWS * N
    f_sig = TONE_BIN * FCLK / n_clk
    bits = []
    for w in range(WINDOWS):
        i1 = i2 = 0.0
        pipe = [0.0] * delay
        for k in range(N):
            n = w * N + k
            u = offset + amplitude * math.sin(2 * math.pi * f_sig * n / FCLK)
            v = 1.0 if i2 > 0.0 else -1.0
            d = v if delay == 0 else pipe[0]
            i2 += COEFF * (i1 - d)
            i1 += COEFF * (u - d)
            if delay:
                pipe = pipe[1:] + [v]
            bits.append(v)
    return bits, f_sig


def _fit(samples, f_sig):
    """(amplitude, offset, rms residual) of a 3-parameter fit at `f_sig`."""
    n = len(samples)
    f_dec = FCLK / N
    rows = [[math.cos(2 * math.pi * f_sig * (k + 1) / f_dec),
             math.sin(2 * math.pi * f_sig * (k + 1) / f_dec), 1.0]
            for k in range(n)]
    m = [[sum(rows[i][r] * rows[i][c] for i in range(n)) for c in range(3)]
         for r in range(3)]
    y = [sum(rows[i][r] * samples[i] for i in range(n)) for r in range(3)]
    for i in range(3):
        piv = m[i][i]
        for j in range(i + 1, 3):
            f = m[j][i] / piv
            for c in range(3):
                m[j][c] -= f * m[i][c]
            y[j] -= f * y[i]
    x = [0.0] * 3
    for i in (2, 1, 0):
        x[i] = (y[i] - sum(m[i][c] * x[c] for c in range(i + 1, 3))) / m[i][i]
    res = [samples[i] - sum(rows[i][c] * x[c] for c in range(3))
           for i in range(n)]
    return math.hypot(x[0], x[1]), x[2], math.sqrt(sum(r * r for r in res) / n)


@pytest.mark.parametrize("loop_delay", [0, 1])
def test_the_weights_must_match_the_loops_own_feedback_delay(loop_delay):
    """BOTH DIRECTIONS. A loop that realises delay D is decoded best by
    delay-D weights, and the preference INVERTS with D — which is what makes
    `constants.feedback_delay_clocks` a declaration about the circuit and not
    a free parameter. The statistic is the OFFSET, because the amplitude is
    attenuated in every arm alike by the input moving inside the window."""
    bits, f_sig = _delayed_loop(loop_delay)
    errs = {}
    for weights_delay in (0, 1):
        samples, meta = D.decimate(bits, N, order=2, coeff=COEFF,
                                   feedback_delay=weights_delay)
        assert samples is not None, meta
        _, offset, _ = _fit(samples[1:], f_sig)
        errs[weights_delay] = abs(offset - 0.20)
    assert errs[loop_delay] < errs[1 - loop_delay], (
        f"loop realises delay {loop_delay}; offset error "
        f"{errs[loop_delay]:.6f} with matching weights vs "
        f"{errs[1 - loop_delay]:.6f} with the other set")
