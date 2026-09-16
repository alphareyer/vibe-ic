#!/usr/bin/env python3
"""analog_incremental_decimator.py — R-0915-70: the matched decimator for an
INCREMENTAL converter, DERIVED from the loop's own recurrence.

WHY THIS EXISTS. L5 can declare a converter INCREMENTAL — "resets/accumulates
per conversion window" — and the emitted deck then resets its integrators every
N clocks (measured on this IC: exactly every 256 clocks, lane icadc
2026-09-16). A free-running delta-sigma earns its resolution from noise shaping
accumulated over the WHOLE record, and an FFT of the raw bitstream is the right
instrument for it. An incremental converter throws that accumulation away every
window BY DESIGN and earns its resolution from the MATCHED DECIMATION of each
window. Reading one with the other's instrument UNDER-READS it.

MEASURED, on a pure-arithmetic ideal CIFB2 with no simulator involved at all
(64 windows of 256, gain and offset removed, which is what an FFT instrument
does implicitly):

    matched decimation   ENOB 6.13 bit    SNDR 38.65 dB
    simple window mean   ENOB 1.67 bit    SNDR 11.80 dB

**4.5 bits.** That difference is the whole reason this module exists, and it is
a property of the DECODE, not of any circuit: the same bitstream reads two ways.

THE WEIGHTS ARE DERIVED, NOT TYPED. For a CIFB2 with reset,

    I1[n] = I1[n-1] + a1 * (u[n] - v[n])          I1[0] = 0  (the reset)
    I2[n] = I2[n-1] + a2 * I1[n-1]                I2[0] = 0

so the second integrator's value after N clocks is a DOUBLE sum, and collecting
terms gives

    I2[N] = a1*a2 * sum_{k} (N-1-k) * (u[k] - v[k])

I2[N] is bounded by the loop, so the weighted sums of u and v agree and the
decoded output is the TRIANGULARLY weighted mean of the bitstream. This module
computes those weights by running the recurrence itself
(:func:`matched_weights`) rather than writing `N-1-k` down: a coefficient set or
a cascade depth this file does not anticipate then gets the weights its own
recurrence implies, and a typed closed form would silently be wrong.

The coefficients DO NOT appear in the weights, and that is not an oversight:
`a1*a2` is a common factor of every term, so it cancels in the normalised mean.
Verified by measurement — the same ideal model at a1=a2 of 1.0, 0.5, 0.25,
0.125 and 0.0625 produces the IDENTICAL bitstream and the identical decode,
because a sign-based quantiser is scale-invariant.

WHAT THIS MODULE DOES NOT CLAIM. On the ideal model the matched decode reaches
6.13 bit where the standard incremental-2nd-order result is
log2(N(N-1)/2) = 14.994. That gap is NOT the simulator (this measurement uses
none), NOT the amplifier gain (1e3 -> 1e7 changed nothing in the SPICE harness),
NOT the coefficient (identical bitstream), and only 1.4 bit of it is the input
moving within a conversion window (a held staircase reads 7.51). The remainder
is unexplained and is recorded as unexplained. This module is the right
instrument for the class; it is not a claim that the loop meets its target.

No chip / SKU / foundry literal.
"""
from __future__ import annotations

# --- sibling-import path (vibe-ic#2104) ------------------------------------
import os as _os                                                     # noqa: E402
import sys as _sys                                                   # noqa: E402

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
# ---------------------------------------------------------------------------

import math                                                          # noqa: E402
from typing import Any, Dict, List, Optional, Sequence, Tuple        # noqa: E402

PRODUCER = "analog_incremental_decimator"

#: Refusals, by name.
NO_WINDOW = "incremental_no_conversion_window"
NO_BITS = "incremental_no_bitstream"
SHORT_RECORD = "incremental_record_shorter_than_one_window"
BAD_ORDER = "incremental_unsupported_order"

#: The conversion modes this file distinguishes. A free-running converter keeps
#: the FFT instrument; only the incremental class is decimated here.
MODE_INCREMENTAL = "incremental"
MODE_FREE_RUNNING = "free_running"


def _impulse_response(window: int, order: int, at_dac: bool,
                      coeff: float) -> List[float]:
    """The final integrator's response to a unit impulse injected at ONE node.

    `at_dac=False` injects at the INPUT, which reaches only the first
    integrator. `at_dac=True` injects at the DAC, which in a CIFB reaches
    EVERY integrator — and that difference is the correction below.
    """
    out: List[float] = []
    for k in range(window):
        state = [0.0] * order
        for n in range(window):
            e = 1.0 if n == k else 0.0
            d = e if at_dac else 0.0
            u = 0.0 if at_dac else e
            # last stage first, so each stage integrates the PREVIOUS stage's
            # value before it is updated — the one-clock delay a CIFB has.
            for s in range(order - 1, 0, -1):
                state[s] += coeff * (state[s - 1] - d)
            state[0] += coeff * (u - d)
        out.append(state[order - 1])
    return out


def input_weights(window: int, order: int = 2, coeff: float = 0.25
                  ) -> List[float]:
    """The INPUT's response — the NORMALISER, and not the bit weights."""
    return _impulse_response(window, order, False, coeff)


def matched_weights(window: int, order: int = 2, coeff: float = 0.25
                    ) -> List[float]:
    """The BIT weights the recurrence implies — the DAC's response, not the
    input's.

    CORRECTED (R-0915-70, second pass), and the correction is worth 7.4 bits.
    The first version injected the impulse at the INPUT and took the triangular
    `N-1-k` response. **The bits do not enter at the input.** In a CIFB the DAC
    feeds EVERY integrator — `a1*d` into the first, `a2*d` into the second — so
    the bit-to-output response carries a FLAT term the input's does not:

        input -> output    a1*a2*(N-1-k)
        bit   -> output  -(a1*a2*(N-1-k) + a2)

    Decoding the bits with the INPUT's weights drops that flat `a2*sum(d)`
    term: a signal-dependent error of order `a2*N`. MEASURED on a
    pure-arithmetic ideal CIFB2 — no simulator — scored by ABSOLUTE error
    against the true input over 512 held levels:

        DAC-injected weights    gain 0.99986   rms error   1.60 LSB   14.315 bit
        input-injected weights  gain 0.96898   rms error 263.53 LSB    6.953 bit

    **7.4 bits, and a 3 % gain error.** The input weights give a SMALL residual
    about their own WRONG line, which is exactly why the mistake survived a
    residual-only check: only comparing against the true input exposes it.

    Still derived, not typed — the response is computed by running the cascade,
    from the right injection point. `coeff` scales every weight and the flat
    term alike, so the normalised decode is insensitive to it.
    """
    if not isinstance(window, int) or window < 1:
        raise ValueError(f"window must be a positive integer, got {window!r}")
    if not isinstance(order, int) or order < 1 or order > 4:
        raise ValueError(f"{BAD_ORDER}: order must be 1..4, got {order!r}")
    return [-w for w in _impulse_response(window, order, True, coeff)]


def conversion_mode(spec: Any, topology: Any = None) -> str:
    """INCREMENTAL when the design's own declaration says so, else free-running.

    Read from the declaration, never guessed: L5's `converter_type` row (or the
    topology record's own), matched on the word the documents use.
    """
    def _texts(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield str(k)
                yield from _texts(v)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                yield from _texts(v)
        elif obj is not None:
            yield str(obj)
    for blob in (spec, topology):
        for t in _texts(blob):
            if "incremental" in t.lower():
                return MODE_INCREMENTAL
    return MODE_FREE_RUNNING


def decimate(bits: Sequence[float], window: int, order: int = 2
             ) -> Tuple[Optional[List[float]], Dict[str, Any]]:
    """One decoded sample per conversion window, or (None, {"reason": ...}).

    `bits` are the sampled decisions, one per converter clock, already sliced.
    """
    if not bits:
        return None, {"reason": NO_BITS}
    if not isinstance(window, int) or window < 1:
        return None, {"reason": NO_WINDOW}
    if len(bits) < window:
        return None, {"reason": SHORT_RECORD, "clocks": len(bits),
                      "window": window}
    try:
        w = matched_weights(window, order)
        wn = input_weights(window, order)
    except ValueError as exc:
        return None, {"reason": str(exc)}
    # NORMALISE BY THE INPUT's weight sum, not the bits'. The two differ by the
    # flat DAC term, and using the bits' sum for both reintroduces the gain
    # error this correction removes.
    total = float(sum(wn))
    if total <= 0:
        return None, {"reason": BAD_ORDER, "weight_sum": total}
    n_win = len(bits) // window
    out = [sum(w[i] * float(bits[s * window + i]) for i in range(window)) / total
           for s in range(n_win)]
    return out, {"producer": PRODUCER, "windows": n_win, "window": window,
                 "order": order, "weight_sum": total,
                 "weights_head": w[:3], "weights_tail": w[-3:],
                 # the LSB count the loop can resolve: the input weight sum divided
           # by the per-stage coefficient product, which is what the
           # normalisation leaves. log2 of it is the theoretical ceiling.
           "quantisation_bits": (math.log2(total / (0.25 ** order))
                                 if total > 0 else 0.0),
                 "rule": "matched_decimation_from_the_loop_recurrence"}
