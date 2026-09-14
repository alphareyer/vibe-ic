#!/usr/bin/env python3
"""analog_poweron_sequence.py — bring a CLOCKED analog block up through a
power-on sequence instead of demanding a DC operating point at full rail.

WHY THIS MODULE EXISTS (MEASURED, lane icadc, 2026-09-15)
=========================================================
`analog_a3_netlist_emit` renders a clocked block's testbench with the supply
already at its final value at t=0 and the clock already toggling from t=0:

    v_vdd vdd 0 {supply}
    v_clk clk 0 pulse(0 {supply} 0n 1n 1n {thigh_ns}n {tper_ns}n)

ngspice must therefore SOLVE a DC operating point for a block whose bias is
established THROUGH its clocked feedback loop.  At the nominal corner that
operating point exists and the deck runs.  At a slow/cold corner it need not,
and when it does not the run yields NOTHING — measured on the nine-corner set
this design's own input declares (TT/SS/FF x -40/27/125 C), corner SS/-40 C,
the second-order incremental modulator `delta_sigma`, inside the pinned image:

    Note: Starting dynamic gmin stepping / Warning: Dynamic gmin stepping failed
    Note: Starting true gmin stepping    / Warning: True gmin stepping failed
    Note: Starting source stepping       / Warning: source stepping failed
    Note: Transient op started           / Error: Transient op failed, timestep too small
    Note: Starting gshunt (rshunt) stepping / Warning: gshunt stepping failed
    Error: The operating point could not be simulated successfully.
    doAnalyses: TRAN:  Timestep too small; initial timepoint: trouble with node "xdut.nbias"
    tran simulation(s) aborted
    Last Node Voltages:  xdut.nbias = -nan

ALL FIVE homotopies ngspice has were exhausted in 20.7 s.  This is not a
solver option away from converging, and it is not a defect of the netlist:
`xdut.nbias` is the bias node the clocked loop itself establishes.

WHAT THIS CHANGES, AND ONLY THIS
================================
For a deck that RUNS A TRANSIENT and DRIVES A CLOCK:

  1. the DC source on the block's supply rail becomes a `pwl` that rises from
     0 V to the SAME value over one tenth of a clock period and then holds it
     for the whole record — so every measurement the deck already takes over
     that supply keeps its expected value;
  2. the clock's first edge is held back by exactly ONE clock period, so it
     arrives after the rail is stable;
  3. the transient stop time and every `meas tran ... from=/to=` window are
     moved out by that same one period, so the record still holds exactly the
     number of converter samples it held before and every window still covers
     the same samples of the same phase.

It does NOT change a device, a model, a corner, a tolerance, an analysis, the
number of samples, or any measurement's meaning.  It is the tool working
HARDER: a corner that could not be measured at all becomes one that can.

THIS IS NOT `uic`, AND THE DISTINCTION IS THE WHOLE POINT
=========================================================
The `delta_sigma` entry in `analog_a2_topology_emit` records, from its own
measurement, why the deck must NOT carry `uic`: "with `uic` the latch decides
on whatever the UNSOLVED initial node voltages happen to be and the set-reset
latch then HOLDS that decision ... an apparent 42.5 mV input-referred offset
that is entirely an artefact of the initial condition".  That reasoning is
kept here, not overturned.  A rail that starts at 0 V is not an unsolved
initial condition: the operating point at t=0 is the fully-solved zero state,
and the latch's decision is then made by the physical power-up the deck
simulates rather than by whatever the solver left in memory.  `uic` skips the
operating point; this establishes one that is trivially solvable.

REFUSAL BY NAME, NEVER A GUESS
==============================
Every input is read off the deck the block is about to run.  When one is not
there the transform is NOT applied and the record says which, so a reader is
told what to change rather than being handed a deck that quietly differs:

    no_transient                the deck runs no `tran`
    no_clock_source             no top-level pulse source: the block is not
                                clocked, so it has no clocked loop to bring up
                                and a ramp would change its stimulus for nothing
    supply_source_not_dc        the rail is already driven by something other
                                than a plain DC value — already sequenced, or
                                driven in a way this transform must not rewrite
    supply_source_absent        no top-level source drives the declared rail
    supply_source_ambiguous     more than one does
    clock_period_unreadable     the pulse card's period is not a readable time
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

PROGRAM = "analog_poweron_sequence"

#: The rail is ramped over this fraction of ONE clock period. A tenth is short
#: enough that it costs no measured sample (the first edge is a whole period
#: away) and long enough that the ramp itself is not a step the integrator has
#: to resolve. Derived from the deck's own clock, never a wall-clock constant:
#: a block clocked ten times faster ramps ten times faster.
RAMP_FRACTION_OF_PERIOD = 0.1

#: `v<name> <n+> <n-> <value>` where the value is a plain number — a DC source.
_DC_SOURCE = re.compile(
    r"(?im)^(\s*)(v\w+)(\s+)(\S+)(\s+)(\S+)(\s+)([-+]?[0-9][0-9eE.+-]*)(\s*)$")
#: `v<name> <n+> <n-> pulse(v1 v2 td tr tf pw per)`
_PULSE = re.compile(
    r"(?im)^(\s*)(v\w+)(\s+)(\S+)(\s+)(\S+)(\s+)pulse\(([^)]*)\)(\s*)$")
_TRAN = re.compile(r"(?im)^(\s*tran\s+)(\S+)(\s+)(\S+)(\s*)$")
_MEAS_WINDOW = re.compile(r"(?i)\b(from|to)=(\S+?)(n?)\b")

_SI = {"f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3,
       "": 1.0, "k": 1e3, "meg": 1e6, "g": 1e9}


def si_seconds(tok: str) -> Optional[float]:
    """A SPICE time token as seconds, or None. `1000n`, `13.824m`, `5e-9`."""
    m = re.fullmatch(r"\s*([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)"
                     r"\s*(meg|[fpnumkg]?)\s*s?\s*", tok or "",
                     flags=re.IGNORECASE)
    if not m:
        return None
    try:
        return float(m.group(1)) * _SI[m.group(2).lower()]
    except (KeyError, ValueError):
        return None


def _ns(value_s: float) -> str:
    """Seconds as the `<x>n` token these decks are written in.

    Integer nanoseconds where the value IS one: the windows this shifts are
    whole clock periods of a deck already written in whole nanoseconds, and a
    `262120.0n` where the deck says `262120n` is a diff nobody can read.
    """
    ns = value_s * 1e9
    return f"{int(round(ns))}n" if abs(ns - round(ns)) < 1e-6 else f"{ns:.6g}n"


def deck_facts(deck_text: str, rail_node: str) -> Dict[str, Any]:
    """What the deck ITSELF declares. No defaults and no table."""
    supplies = [m for m in _DC_SOURCE.finditer(deck_text)
                if m.group(4) == rail_node or m.group(6) == rail_node]
    pulses = list(_PULSE.finditer(deck_text))
    tran = _TRAN.search(deck_text)
    period = None
    if pulses:
        args = [a for a in re.split(r"[\s,]+", pulses[0].group(8).strip()) if a]
        # pulse(v1 v2 td tr tf pw per) — the PERIOD is the last of the seven.
        if len(args) >= 7:
            period = si_seconds(args[6])
    return {
        "rail_node": rail_node,
        "supply_sources": [m.group(2) for m in supplies],
        "supply_match": supplies[0] if len(supplies) == 1 else None,
        "clock_sources": [m.group(2) for m in pulses],
        "clock_match": pulses[0] if pulses else None,
        "clock_period_s": period,
        "tran_match": tran,
        "tran_tstop_s": si_seconds(tran.group(4)) if tran else None,
        "meas_windows": len(_MEAS_WINDOW.findall(deck_text)),
    }


def plan(deck_text: str, rail_node: str) -> Dict[str, Any]:
    """`{applied: bool, ...}` — the decision and the arithmetic behind it."""
    f = deck_facts(deck_text, rail_node)
    rec: Dict[str, Any] = {"producer": PROGRAM, "applied": False,
                           "rail_node": rail_node}
    if f["tran_match"] is None:
        rec["refused"] = "no_transient"
        return rec
    if not f["clock_sources"]:
        rec["refused"] = "no_clock_source"
        return rec
    if f["clock_period_s"] is None:
        rec["refused"] = "clock_period_unreadable"
        return rec
    if not f["supply_sources"]:
        # Distinguish "nothing drives the rail" from "something does, but not
        # with a plain DC value": the second is a deck that is ALREADY
        # sequenced, and reporting it as an absence would send a reader
        # looking for a source that is right there.
        already = re.search(
            rf"(?im)^\s*v\w+\s+(?:{re.escape(rail_node)}\s+\S+|\S+\s+"
            rf"{re.escape(rail_node)})\s+\S", deck_text)
        rec["refused"] = ("supply_source_not_dc" if already
                          else "supply_source_absent")
        return rec
    if len(f["supply_sources"]) > 1:
        rec["refused"] = "supply_source_ambiguous"
        rec["supply_sources"] = f["supply_sources"]
        return rec
    if f["tran_tstop_s"] is None:
        rec["refused"] = "tran_stop_time_unreadable"
        return rec

    period = f["clock_period_s"]
    rec.update(
        applied=True,
        supply_source=f["supply_sources"][0],
        supply_value=f["supply_match"].group(8),
        clock_source=f["clock_sources"][0],
        clock_period_s=period,
        ramp_s=period * RAMP_FRACTION_OF_PERIOD,
        clock_delay_s=period,
        tstop_before_s=f["tran_tstop_s"],
        tstop_after_s=f["tran_tstop_s"] + period,
        meas_window_endpoints_shifted=f["meas_windows"],
        samples_before=round(f["tran_tstop_s"] / period),
        samples_after=round((f["tran_tstop_s"] + period) / period) - 1,
    )
    return rec


def apply(deck_text: str, rail_node: str) -> Tuple[str, Dict[str, Any]]:
    """`(deck, record)`. The deck is returned UNCHANGED when `plan` refuses."""
    rec = plan(deck_text, rail_node)
    if not rec.get("applied"):
        return deck_text, rec

    period = rec["clock_period_s"]
    ramp = rec["ramp_s"]
    tstop_after = rec["tstop_after_s"]
    value = rec["supply_value"]
    out = deck_text

    # (1) the rail. It ends at the SAME value, so nothing the deck divides by
    # the supply changes — `let dens = vavg / {supply}` reads the same rail.
    sm = _DC_SOURCE.search(out)
    sm = next((m for m in _DC_SOURCE.finditer(out)
               if m.group(2) == rec["supply_source"]), None)
    assert sm is not None                                    # plan found it
    out = (out[:sm.start()]
           + f"{sm.group(1)}{sm.group(2)}{sm.group(3)}{sm.group(4)}"
             f"{sm.group(5)}{sm.group(6)}{sm.group(7)}"
             f"pwl(0n 0 {_ns(ramp)} {value} {_ns(tstop_after)} {value})"
           + out[sm.end():])

    # (2) the clock's first edge, held back by exactly one period.
    cm = next((m for m in _PULSE.finditer(out)
               if m.group(2) == rec["clock_source"]), None)
    assert cm is not None
    args = [a for a in re.split(r"[\s,]+", cm.group(8).strip()) if a]
    td_before = si_seconds(args[2])
    if td_before is None:
        # The card is readable enough to give a period and not enough to give
        # a delay: refuse rather than write a delay over something unread.
        return deck_text, {"producer": PROGRAM, "applied": False,
                           "refused": "clock_delay_unreadable"}
    args[2] = _ns(td_before + period)
    rec["clock_delay_before"] = f"{td_before * 1e9:g}n"
    rec["clock_delay_after"] = args[2]
    out = (out[:cm.start()]
           + f"{cm.group(1)}{cm.group(2)}{cm.group(3)}{cm.group(4)}"
             f"{cm.group(5)}{cm.group(6)}{cm.group(7)}"
             f"pulse({' '.join(args)})"
           + out[cm.end():])

    # (3) the transient and EVERY measurement window, moved out by that same
    # one period — so the record holds the same number of samples and each
    # window still covers the same samples of the same clock phase. Shifting
    # the stop time without the windows would measure a different part of the
    # record and call it the same measurement.
    tm = _TRAN.search(out)
    assert tm is not None
    out = (out[:tm.start()]
           + f"{tm.group(1)}{tm.group(2)}{tm.group(3)}{_ns(tstop_after)}"
             f"{tm.group(5)}"
           + out[tm.end():])

    def _shift(m: "re.Match[str]") -> str:
        t = si_seconds(m.group(2) + m.group(3))
        if t is None:
            return m.group(0)
        return f"{m.group(1)}={_ns(t + period)}"

    out = _MEAS_WINDOW.sub(_shift, out)

    # THE DECK SAYS IT ITSELF. A deck that differs from the one a reader
    # expects and does not say why is read as a deck somebody edited; the
    # `* condition:` lines above already describe this deck's stimulus, and
    # leaving the rail's own sequencing out of them would make those lines
    # describe a deck that is not this one.
    rec["provenance"] = (
        f"poweron_sequence=applied: the rail rises from 0 to {value} over "
        f"{_ns(ramp)} and the first clock edge is held to "
        f"{rec['clock_delay_after']}, so the clocked loop is brought up "
        f"through a physical power-on instead of a DC operating point solved "
        f"at full rail with the clock already running; the transient and "
        f"every measurement window moved out by one {_ns(period)} clock "
        f"period so the record holds the same samples "
        f"(see `{PROGRAM}`)")
    sm2 = next((m for m in re.finditer(
        rf"(?m)^\s*{re.escape(rec['supply_source'])}\s", out)), None)
    if sm2 is not None:
        out = (out[:sm2.start()]
               + f"* condition: {rec['provenance']}\n"
               + out[sm2.start():])
    return out, rec
