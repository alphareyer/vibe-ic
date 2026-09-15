#!/usr/bin/env python3
"""analog_converter_density_grade.py — R-0915-45: EVALUATE the density pass
condition the analog harness already DECLARES, on the run's own bitstream.

NOT A GATE, AND IT DELIBERATELY DECLARES NO ENFORCEMENT INTENT. This is a
library: it computes, `analog_real_corner_sweep` publishes the rows it returns,
and `analog_a4_corner_sweep_check` is the gate that decides. An
`ENFORCEMENT:` line here would be a declaration with nothing behind it —
`flow_gate_enforcement_audit` names exactly that case `orphan::` and fails the
run, and it is right to: a program that says it can block while sitting outside
the flow definition is a claim no reader can check. (Measured: adding the line
produced `orphan::analog_converter_density_grade  (declared advisory)` and
`1 NEW gate(s) declare an intent they are not wired for`.)

WHAT WENT WRONG, MEASURED ON A REAL RUN (lane icadc, 2026-09-16).
`analog_a2_topology_emit` emits a DC input at `supply/2 + vref/10` and states
its own pass condition in the deck it writes:

    "A DC input one tenth of full scale above mid-scale, for which a modulator
     that converts MUST return a bitstream density of 0.6 - neither 0, nor 1,
     nor 1/2."

`analog_resolution_stimulus` then REPLACES that DC input with a coherent tone,
and writes into the same deck that it is "centred on the level the design's own
deck held it at, SO EVERY MEASUREMENT THAT DECK ALREADY TAKES KEEPS ITS
MEANING". That claim is false and the arithmetic is not close:

  * the deck's check is `meas tran vavg avg v(bit_out) from=262120n to=513000n`
    -- 251 samples at a 1 MHz sample clock;
  * the tone period is 1536 samples;
  * 251/1536 = 0.163 of ONE period, so the window averages the tone's LOCAL
    VALUE, not its mean -- and it sits on the POSITIVE PEAK (mean of sin over
    the window = +0.9566).

Least squares over the whole record (13824 samples = exactly 9 whole tone
periods) gave DC 0.4259 against the declared 0.6000, and tone amplitude 0.1742
against 0.3600. Two real errors -- a depressed baseline and a halved gain --
cancelled at the tone's peak and the deck reported 0.6056. All NINE PVT corners
reported 0.5618..0.6556 and looked like a converter that works; on the honest
whole-record figure the same nine read 0.3803..0.4723 and it works at none.

A declared pass condition that never runs is worse than no condition: it is a
green nobody can support.

WHAT THIS MODULE DOES. Two pure functions over the deck the corner actually ran
and the waveform it actually produced -- never a table of chip values:

  * `declared_density(deck)`  -- the expectation, from the deck's OWN source
    cards: `(vin_dc - vrefn) / (vrefp - vrefn)`. Chip-AGNOSTIC: the levels are
    read from the cards, and a deck that does not drive them is REFUSED by name.
  * `measured_density(deck, dump)` -- the honest figure: the output resampled
    at the converter's own sample clock over a WHOLE NUMBER of tone periods,
    hard-sliced at the midpoint of the deck's own supply.

THE TOLERANCE IS DERIVED, NOT CHOSEN. The declared condition distinguishes the
expected density from the degenerate 1/2 of "a loop that is ignoring its input"
-- the doctrine says so in those words. So the pass band is the half-open
interval that stays strictly closer to the expectation than to 1/2:

    FAIL when |measured - expected| >= |expected - 0.5|

and when `expected == 0.5` there is no such interval, so this REFUSES to grade
(`density_indistinguishable_at_mid_scale`) instead of inventing a bound. A
mid-scale input cannot be told from an ignoring loop by density alone, and
saying so is the honest answer.

No chip / SKU / foundry literal anywhere in this file.
"""
from __future__ import annotations

# --- sibling-import path (vibe-ic#2104) ------------------------------------
# `programs/` is a flat directory whose modules import each other by BARE name.
# Python puts a file's own directory on `sys.path` only when that file is run
# as `__main__`; under `importlib.util.spec_from_file_location` — how the
# gates, the wiring audit and much of the suite load a program — it does not,
# so the bare sibling import below raises ModuleNotFoundError. This shim MUST
# stay ABOVE that import: I wrote it below the first time and
# `test_every_shipped_program_loads_by_path` caught it by name
# ("1 program(s) cannot resolve a sibling when loaded by path"). Idempotent,
# and the same shape every sibling that already carries it uses.
import os as _os                                                     # noqa: E402
import sys as _sys                                                   # noqa: E402

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
# ---------------------------------------------------------------------------

import re                                                            # noqa: E402
from typing import Any, Dict, List, Optional, Tuple                  # noqa: E402

import analog_adc_enob_corner_check as _enob                         # noqa: E402
import analog_pdk_deck_context as _dc                                # noqa: E402

PRODUCER = "analog_converter_density_grade"

# Refusals, by name. A reader told WHICH property is missing can fix the deck;
# a reader told "no result" cannot.
NO_INPUT_LEVEL = "density_no_input_dc_level"
NO_REFERENCE_PAIR = "density_no_reference_pair"
NO_SUPPLY = "density_no_supply_level"
ZERO_SPAN = "density_reference_span_is_zero"
AT_MID_SCALE = "density_indistinguishable_at_mid_scale"
NO_SAMPLES = "density_no_samples"
NO_CLOCK = "density_no_sample_clock"
NO_TONE_WINDOW = "density_no_whole_tone_window"

_DEGENERATE = 0.5          # "the 1/2 of a loop that is ignoring its input"

# A top-level independent source: `<name> <node> 0 <rest>`.
_SRC = re.compile(r"^(v[\w.$]*)\s+(\S+)\s+0\s+(.+?)\s*$", re.M | re.I)
_SIN = re.compile(r"sin\s*\(\s*([-\d.eE+]+)", re.I)


def _sources(deck_text: str) -> Dict[str, float]:
    """Every top-level source's DC level, keyed by the node it drives.

    A `sin(offset amp freq)` contributes its OFFSET, a `pwl(...)` its final
    value, and a bare number itself — which is what a DC level means for each
    of the three shapes this flow emits.
    """
    # THE POLARITY QUESTION HAS NO REFERENT HERE, because no sentence reaches
    # the regex. `spice_code_only` blanks every `*` comment line and every `;`
    # / `$` inline comment before the first match, which is where English
    # lives in a SPICE deck — and this deck is FULL of it: the power-on
    # sequence, the vector-retention note and the resolution-stimulus note are
    # all `*` lines that name `v_in`, `vrefp` and their values in prose.
    # Without the strip, `_sources` would be reading a value out of a sentence
    # and writing it as a declaration, which is exactly what
    # `prose_polarity_consulted_check --ratchet` refuses — it named this
    # function (`analog_converter_density_grade::_sources`) the first time I
    # pushed it without the strip.
    out: Dict[str, float] = {}
    for _name, node, rest in _SRC.findall(_dc.spice_code_only(deck_text or "")):
        rest = rest.strip()
        m = _SIN.search(rest)
        if m:
            try:
                out[node] = float(m.group(1))
            except ValueError:
                pass
            continue
        if rest.lower().startswith("pwl"):
            nums = re.findall(r"[-\d.eE+]+", rest)
            if nums:
                try:
                    out[node] = float(nums[-1])
                except ValueError:
                    pass
            continue
        if rest.lower().startswith(("pulse", "sffm", "exp", "am")):
            continue                      # not a DC level
        try:
            out[node] = float(rest.split()[0])
        except (ValueError, IndexError):
            pass
    return out


def declared_density(deck_text: str) -> Tuple[Optional[float], Dict[str, Any]]:
    """The density the deck's own levels say a converting modulator must give."""
    src = _sources(deck_text)
    vin = src.get("vin")
    vrefp, vrefn = src.get("vrefp"), src.get("vrefn")
    if vin is None:
        return None, {"reason": NO_INPUT_LEVEL}
    if vrefp is None or vrefn is None:
        return None, {"reason": NO_REFERENCE_PAIR}
    span = vrefp - vrefn
    if span == 0:
        return None, {"reason": ZERO_SPAN}
    expected = (vin - vrefn) / span
    return expected, {"vin_dc_v": vin, "vrefp_v": vrefp, "vrefn_v": vrefn,
                      "reference_span_v": span}


def measured_density(deck_text: str, dump_text: str, column: int = 1
                     ) -> Tuple[Optional[float], Dict[str, Any]]:
    """Bitstream density over a WHOLE number of tone periods, at the sample clock.

    The window rule is the one `sndr_db_from_transient` uses, and for the same
    reason: a window that is not a whole number of tone periods reports where
    the tone happens to be, not what the converter does.
    """
    m = _enob._SIN_RE.search(deck_text or "")
    if not m:
        return None, {"reason": NO_TONE_WINDOW}
    f_sig = _enob._si(m.group(3))
    if not f_sig or f_sig <= 0:
        return None, {"reason": NO_TONE_WINDOW}
    f_clk = _enob.sample_clock_hz(deck_text)
    if not f_clk:
        return None, {"reason": NO_CLOCK}
    src = _sources(deck_text)
    vdd = src.get("vdd")
    if vdd is None:
        return None, {"reason": NO_SUPPLY}

    times: List[float] = []
    vals: List[float] = []
    for line in (dump_text or "").splitlines():
        parts = line.split()
        if len(parts) <= column:
            continue
        try:
            t = float(parts[0]); v = float(parts[column])
        except ValueError:
            continue
        times.append(t); vals.append(v)
    if len(times) < 64:
        return None, {"reason": NO_SAMPLES, "rows": len(times)}

    span = times[-1] - times[0]
    cycles = int(span * f_sig)
    if cycles < 1:
        return None, {"reason": NO_TONE_WINDOW, "cycles_available": cycles}
    t1 = times[-1]
    t0 = t1 - cycles / f_sig
    grid = _enob._resample_at(times, vals, t0, t1, f_clk)
    if not grid:
        return None, {"reason": NO_SAMPLES}
    mid = vdd / 2.0
    ones = sum(1 for v in grid if v > mid)
    return ones / float(len(grid)), {
        "samples": len(grid), "whole_tone_periods": cycles,
        "sample_clock_hz": f_clk, "signal_hz": f_sig,
        "slice_level_v": mid,
        "rule": "whole_tone_periods_at_the_sample_clock"}


def grade(deck_text: str, dump_text: str, column: int = 1) -> Dict[str, Any]:
    """The declared condition, EVALUATED. `status` is PASS, FAIL or NOT_MEASURED."""
    expected, edet = declared_density(deck_text)
    measured, mdet = measured_density(deck_text, dump_text, column)
    row: Dict[str, Any] = {"producer": PRODUCER,
                           "density_expected": expected,
                           "density_measured": measured}
    row.update({k: v for k, v in edet.items() if k != "reason"})
    row.update({k: v for k, v in mdet.items() if k != "reason"})
    if expected is None:
        return {**row, "status": "NOT_MEASURED", "reason": edet["reason"]}
    if measured is None:
        return {**row, "status": "NOT_MEASURED", "reason": mdet["reason"]}
    bound = abs(expected - _DEGENERATE)
    if bound == 0:
        return {**row, "status": "NOT_MEASURED", "reason": AT_MID_SCALE,
                "tolerance": 0.0}
    err = abs(measured - expected)
    return {**row, "status": "PASS" if err < bound else "FAIL",
            "error": err, "tolerance": bound,
            "tolerance_rule": ("strictly closer to the declared density than to "
                               "the %.3f of a loop ignoring its input" % _DEGENERATE)}
