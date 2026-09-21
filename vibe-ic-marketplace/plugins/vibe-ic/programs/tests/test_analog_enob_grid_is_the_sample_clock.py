"""test_analog_enob_grid_is_the_sample_clock.py — an oversampled converter is
graded on a grid COMMENSURATE with its own sample clock (lane icadc,
2026-09-15).

THE DEFECT THIS COVERS, MEASURED.  `sndr_db_from_transient` resampled the
corner's transient onto "the largest power of two not exceeding the number of
rows in the dump", placed over a window of `cycles / f_sig` seconds.  For an
oversampled converter that is two errors at once:

  * THE RATE IS THE ROW COUNT, NOT THE CLOCK.  A 13824-row bitstream of a
    1 MHz modulator became an 8192-point grid over 12.29 ms — **666.7 kHz**,
    below the modulator's own clock.  A delta-sigma pushes its quantisation
    noise up towards fs/2 on purpose; sampling below fs folds every bit of it
    back into the band being graded.
  * THE GRID IS NOT COMMENSURATE WITH THE BITSTREAM'S LATTICE.  A bitstream is
    piecewise constant on a 1/fs lattice; a grid whose step is not that lattice
    beats against it, and the beat lands in-band as noise the circuit never
    made.

MEASURED, one waveform, everything else held:

    last 8 cycles, grid = fs exactly (12288 pts)    SNDR  96.129 dB
    last 8 cycles, grid = 16384 pts  (1333 kHz)     SNDR  21.706 dB

and the same waveform written at 1 / 2 / 4 rows per clock — identical samples,
only the row count differs — measured 21.700 / 28.431 / 33.989 dB.  A figure of
merit that moves 12 dB with how many rows the solver happened to write is not a
measurement of the circuit.

BOTH DIRECTIONS ARE PROVED HERE.  The oversampled path must become
row-count-independent and land on the physics; the NYQUIST path must not move
at all, because there the full span IS the band and the grid rate was never the
thing that was wrong.

Signal, port and net names are synthetic.  No chip / SKU / foundry literal.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analog_adc_enob_corner_check as gate      # noqa: E402

FS = 1.0e6
N = 13824
BIN = 9
OSR = 256
F_SIG = BIN * FS / N


def _deck(fs=FS, f_sig=F_SIG, stop_ns=13824000):
    per = int(round(1e9 / fs))
    return (f"v_in ain 0 sin(0.7 0.36 {f_sig:.7f})\n"
            f"v_clk clk 0 pulse(0 1.2 0n 1n 1n {per // 2 - 1}n {per}n)\n"
            ".control\n"
            f"tran 5n {stop_ns}n\n"
            "wrdata c.resolution.wrdata v(bit_out)\n"
            ".endc\n")


def _bitstream(jitter=0.0, seed=1, n=N):
    """A second-order delta-sigma bitstream: what the graded node carries."""
    import random
    rnd = random.Random(seed)
    i1 = i2 = 0.0
    y = 0.0
    out = []
    for k in range(n):
        u = 0.36 * math.sin(2.0 * math.pi * F_SIG * k / FS)
        if jitter:
            u += rnd.gauss(0.0, jitter)
        i1 += u - y
        i2 += i1 - y
        y = 1.0 if i2 >= 0 else -1.0
        out.append((k / FS, 1.2 if y > 0 else 0.0))
    return out


def _dump(rows, per_clock=1):
    """The same piecewise-constant waveform, written with `per_clock` rows per
    clock period. The SAMPLES do not change; only how many times the solver
    wrote each one down."""
    lines = []
    for k in range(len(rows) * per_clock):
        t, v = rows[k // per_clock]
        lines.append(f"{t + (k % per_clock) / (FS * per_clock):.12e}\t{v:.6f}")
    return "\n".join(lines) + "\n"


# ── the defect arm ─────────────────────────────────────────────────────────
def test_the_answer_does_not_depend_on_how_many_rows_the_solver_wrote():
    """THE DEFECT ARM, and the sharpest one: the SAMPLES are identical in all
    three dumps. On the old grid rule these measured 21.700 / 28.431 / 33.989
    dB — a 12 dB spread, two bits, decided by the row count."""
    rows = _bitstream()
    got = []
    for per_clock in (1, 2, 4):
        sndr, meta = gate.sndr_db_from_transient(
            _deck(), _dump(rows, per_clock), osr=float(OSR))
        assert sndr is not None, meta
        assert meta["rows_read"] == N * per_clock, meta
        got.append(sndr)
    assert max(got) - min(got) < 0.5, got


def test_the_oversampled_grid_IS_the_sample_clock():
    sndr, meta = gate.sndr_db_from_transient(
        _deck(), _dump(_bitstream()), osr=float(OSR))
    assert sndr is not None, meta
    assert meta["grid_rule"] == "sample_clock_commensurate", meta
    assert meta["grid_hz"] == meta["sample_clock_hz"], meta
    assert abs(meta["sample_clock_hz"] - FS) / FS < 1e-6, meta
    assert meta["method"] == "dft_signal_bin_vs_in_band_rest_at_sample_clock"


def test_a_working_modulator_does_not_read_as_three_bits():
    """The physics the old grid destroyed. An ideal second-order modulator at
    OSR 256 cannot be a 3-bit converter; on the old grid this record measured
    21.700 dB = 3.312 bit."""
    sndr, _ = gate.sndr_db_from_transient(
        _deck(), _dump(_bitstream()), osr=float(OSR))
    assert (sndr - 1.76) / 6.02 > 12.0, sndr


def test_the_gate_still_separates_a_good_bitstream_from_a_degraded_one():
    """An instrument that cannot tell them apart reports a constant. On the old
    grid the separation was 2 dB; the two bitstreams differ only in how noisy
    their input was."""
    clean, _ = gate.sndr_db_from_transient(
        _deck(), _dump(_bitstream()), osr=float(OSR))
    dirty, _ = gate.sndr_db_from_transient(
        _deck(), _dump(_bitstream(jitter=0.05)), osr=float(OSR))
    assert clean - dirty > 30.0, (clean, dirty)


def test_a_window_start_a_few_ulps_off_a_sample_boundary_costs_nothing():
    """THE MUTATION THAT WOULD PASS QUIETLY. `t1 - cycles / f_sig` lands a few
    times 1e-19 s below the sample it is meant to start on. A strict hold then
    takes the previous sample for most points and the right one for the rest —
    one-sample JITTER, not a delay. MEASURED cost: 110.758 dB sliced exactly
    against 52.392 dB through the jittered hold."""
    step = 2.0 * 0.45 / (2 ** 16)
    rows = [(k / FS, round((0.9 + 0.45 * math.sin(2 * math.pi * 32 * k / 4096))
                           / step) * step) for k in range(4096)]
    dump = "\n".join(f"{t:.12e} {v:.12e}" for t, v in rows)
    deck = _deck(f_sig=32 * FS / 4096, stop_ns=4096000)
    sndr, meta = gate.sndr_db_from_transient(deck, dump, osr=8.0)
    assert sndr is not None, meta
    # A 16-bit record oversampled 8x cannot be a 7-bit one.
    assert (sndr - 1.76) / 6.02 > 15.0, (sndr, meta)


# ── the negative control: the Nyquist path must not move ───────────────────
def test_the_nyquist_path_is_untouched():
    """OSR 1 means the full span IS the band: there the power-of-two grid was
    never wrong, and a change that moved it would be a change nobody measured a
    reason for."""
    sndr, meta = gate.sndr_db_from_transient(
        _deck(), _dump(_bitstream()), osr=1.0)
    assert sndr is not None, meta
    assert meta["method"] == "fft_signal_bin_vs_rest", meta
    assert meta["fft_points"] == 8192, meta      # the old rule, unchanged
    # THE VALUE, pinned, because a method name is a mechanism and the claim
    # here is a PROPERTY: `_resample_pow2` is byte-identical to the one on
    # main, so this number must not move. It is compared WITHIN A MEASURED
    # SPREAD rather than for exact equality, and that is not a relaxation of
    # the claim -- it is the only way to state it truthfully, because the last
    # bit of this number is an ENVIRONMENT fact and the suite runs in two
    # environments.
    #
    # MEASURED, same tree, same input, same `sndr_db_from_transient`:
    #
    #   inside the pinned EDA image (Python 3.12.3)   -10.20218060203778
    #     -- identical on FOUR images: 0.3.67, 0.3.63, 0.3.47, 0.3.41
    #   on the bare host 8HD-6      (Python 3.10.12)  -10.202180602037778
    #
    # The two differ by 1.7763568394002505e-15 -- EXACTLY ONE ULP at this
    # magnitude (2**-49). It is a libm/interpreter build difference, not a
    # change in this repo: an AST diff of `analog_adc_enob_corner_check` across
    # the only producer commit since this test landed (d61937aaf) changes
    # `_measure_corner_from_transient` and ADDS `sample_clock_card` /
    # `sndr_db_incremental`, while `_resample_pow2` and
    # `sndr_db_from_transient` -- the two this test calls -- are UNCHANGED.
    #
    # The original `==` therefore could not be right in both places: it was
    # green on hosts and RED IN THE IMAGE since the day it landed (measured at
    # its own landing commit 2a918dd3a: 1 failed, 7 passed).
    #
    # `abs_tol=1e-14` is ~5.6 ULP -- wide enough for the measured 1-ULP spread
    # and nothing else. `rel_tol=0.0` so the bound is exactly this and does not
    # scale. FOR SCALE, this is not a weakened check: the strict-hold mutation
    # in `tools/ci/mutation_arm/` moves this number to -12.77681304371659, a
    # gap of 2.57 dB -- 2.6e14 times the tolerance. Any real movement of the
    # Nyquist path fails this line; only its last bit may vary.
    assert math.isclose(sndr, -10.20218060203778,
                        rel_tol=0.0, abs_tol=1e-14), sndr


def test_an_oversampled_deck_with_no_clock_still_refuses_by_name():
    """The band cannot be established without the clock, and now neither can
    the grid — so the refusal must still fire, not fall through to a grid taken
    from the row count."""
    deck = "\n".join(ln for ln in _deck().splitlines()
                     if not ln.startswith("v_clk")) + "\n"
    value, meta = gate.sndr_db_from_transient(
        deck, _dump(_bitstream()), osr=float(OSR))
    assert value is None
    assert meta["reason"] == gate._UNMEASURABLE_NO_CLOCK, meta


def test_a_record_too_long_to_grid_at_the_clock_is_refused_by_name():
    """NOT measured on a coarser grid — a coarser grid is the defect. The
    refusal names the cap and what the record needed."""
    rows = _bitstream()
    # A clock 512x faster makes the same window need 512x the grid points.
    deck = _deck(fs=FS * 512, f_sig=F_SIG)
    value, meta = gate.sndr_db_from_transient(deck, _dump(rows),
                                              osr=float(OSR))
    assert value is None, (value, meta)
    assert meta["reason"] == gate._UNMEASURABLE_GRID_TOO_LARGE, meta
    assert meta["grid_points_required"] > meta["grid_cap"], meta
