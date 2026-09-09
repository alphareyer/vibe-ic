#!/usr/bin/env python3
"""
iterative_recurrence_timing_diagnosis.py — chip-AGNOSTIC diagnosis of a
setup-limited ITERATIVE datapath whose worst path is a single-register
self-recurrence (a self-loop with exactly one register in the cycle), for which
register retiming is provably ineffective and the in-spec fix is a MULTI-CYCLE
microarchitecture (split the recurrence's combinational cone over N>=2 clocked
sub-stages) — NEVER a relaxed clock.

Why this is a real, general capability (not a chip trick)
--------------------------------------------------------
An iterative datapath computes one "iteration" per clock by feeding a state
register back to itself through a combinational cone: ``X <= f(..., X, ...)``.
The state->state cycle then contains **exactly one register**. Two consequences
that hold for ANY such loop, on ANY PDK:

  * **Loop-bound period.** The minimum clock period of a cyclic path is
    (sum of combinational delay on the cycle) / (number of registers on the
    cycle). With one register the floor is the *whole* cone delay — you cannot
    clock it faster than one full iteration, full stop.
  * **Retiming cannot help.** Retiming preserves the register count on every
    cycle (it only slides registers across combinational logic). A cycle with
    one register keeps one register, so its delay/period bound is invariant.
    Empirically this shows up as a retiming pass (e.g. ``abc`` dretime) that
    returns a byte-identical netlist or ~0 ns WNS improvement.

So when an iterative datapath misses a HARD target period at the slow sign-off
corner, the moves that DO work are:

  * If the design spec authorises the microarch as a free choice AND does not
    fix the latency-cycle count -> **multi-cycle round**: split the recurrence
    cone across N>=2 registers/cycles so the per-cycle depth is ~1/N. The loop
    now spans N registers; each cycle is a genuine single-cycle path at the SAME
    period. (Latency in cycles grows ~N x; throughput/period is what closes.)
  * If the spec forbids a microarch change -> this is an HONEST floor: report it;
    do NOT relax the clock and do NOT dress a violated corner as a pass.

Anti-patterns this encodes against (both measured):
  * *"Just retime it."* — ineffective on a single-register self-loop (above).
  * *"Restructure the logic (dch/dc2) to break the ripple."* — can break the
    ripple but inflates area/die; on a routed slow corner the added wire delay
    can make WNS WORSE, not better. Restructuring is not a substitute for
    reducing the per-cycle register-to-register depth.

Worked example (kept for context, not used in detection logic)
--------------------------------------------------------------
sha256 x sky130A, 25.907 ns HARD period: the reference iterative single-cycle
round (66 cycles/block) is a a_reg->a_reg / e_reg->e_reg self-loop whose ~5-deep
carry-propagate add cone does not close at ss_100C_1v60 (post-route WNS ~ -3.85
ns). ``abc`` dretime was byte-identical (loop-bound). The in-spec fix was a
2-cycle round (128 cycles/block; L5 authorises the round microarch and does not
constrain the cycle count) — per-cycle depth ~halved. The clock was never
relaxed.

What this program does
----------------------
Given an STA ``report_checks`` text (the worst setup path) and, optionally, the
WNS delta from a retiming experiment plus the spec's microarch/latency freedom,
it classifies the situation and routes to the correct remedy. It has NO chip /
vendor / PDK / corner literal in its detection logic; every input is a generic
timing/spec fact.

Signals
-------
  * self_loop_by_name : the worst path's start and end registers are the SAME
    register bank (identical instance path once the bit-index ``[n]`` is
    stripped) -> the state feeds itself. Strong, direct evidence.
  * names_anonymized  : the register names are synthesis-anonymised (``_1234_``
    / ``$auto$...``) so the name test cannot see the bank identity -> fall back
    to the retiming signal or ask for a name-preserving netlist / the RTL.
  * retiming_ineffective : a retiming experiment that is READABLE established
    the pass could not help -> corroborates loop-bound. A dWNS is readable ONLY
    against a floor measured at the same granularity, or not at all when the
    pass returned a byte-identical netlist (#2220). retiming_effective is the
    opposite and OVERRIDES the name heuristic (a measured improvement is ground
    truth: the path was not a pure single-register loop) — which is exactly why
    an unreadable dWNS must never reach it: MEASURED, a full-flow dWNS of
    1.569724 ns was produced with the netlist held BYTE-IDENTICAL, and that
    number alone used to answer "apply retiming".
    States, reported as ``retiming_datapoint_state``:
      INEFFECTIVE_BYTE_IDENTICAL     the pass returned the same netlist.
      EFFECTIVE                      delta >= the MEASURED noise floor.
      REGRESSED                      delta <= -floor; worse, above the noise.
      NOT_MEASURED_BELOW_NOISE_FLOOR |delta| < floor — indistinguishable from
                                     the flow's own spread; corroborates
                                     NOTHING, in either direction.
      NOT_MEASURED_NO_NOISE_FLOOR    a delta arrived with no declared floor.

Verdicts (exit code in brackets)
--------------------------------
  TIMING_MET                     [0] worst slack >= 0; no diagnosis needed.
  LOOP_BOUND_RECURRENCE          [0] single-register self-loop, retiming can't
                                     help; carries a REMEDY:
                                       - RECOMMEND_MULTICYCLE_SPLIT  (spec free)
                                       - SPEC_BLOCKS_MICROARCH_CHANGE (honest floor)
  RETIMING_EFFECTIVE_APPLY       [0] measured retiming improvement -> not a pure
                                     self-loop; apply retiming / pipelining.
  NOT_SELF_LOOP                  [0] feed-forward path (start bank != end bank);
                                     standard sizing/useful-skew/pipelining.
  INCONCLUSIVE_NAMES_ANONYMIZED  [0] cannot see bank identity and no retiming
                                     datapoint -> guidance to supply one.
  CLOCK_RELAX_FORBIDDEN          [1] a clock-period relaxation was proposed —
                                     hard tripwire; the target period is HARD.

  When the worst path is dominated by ONE carry-propagate add and the caller has
  NOT attested `--timing-driven-synth`, the remedy is
  VERIFY_TIMING_DRIVEN_SYNTHESIS_FIRST: a long carry chain is far more often an
  area-mode MAPPING than an architectural floor (yosys techmaps $lcu to a
  Brent-Kung parallel-prefix adder; an untargeted mapper spends that structure
  back down). Measured: a delay target the mapper silently ignored produced a
  BYTE-IDENTICAL netlist, and wiring it up properly recovered 22% of the path
  for +1.3% area. Never send an author to RTL surgery for a flow defect.
  error                          [2] missing / unreadable / no STA path parsed.

CLI::

    python3 iterative_recurrence_timing_diagnosis.py --sta-report worst.rpt \\
        [--retiming-wns-delta 0.0 \\
         (--retiming-wns-noise-floor-ns 0.35 | --retiming-netlist-byte-identical)] \\
        [--spec-microarch-free] \\
        [--spec-latency-unconstrained] [--target-period-ns 25.907] \\
        [--timing-driven-synth] [--relax-clock-proposed] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── vibe-ic#2220 — THE FIXED THRESHOLD WAS WITHDRAWN, NOT RAISED ───────────
#
# There used to be a module constant here:
#
#     RETIMING_EPS_NS = 0.10   # "well inside routing/characterisation noise
#                              #  for any sign-off corner"
#
# and `diagnose` decided `retiming_effective` / `retiming_ineffective` from it.
# Its own comment is the refutation: a quantity documented as lying INSIDE the
# noise cannot be the thing that separates two verdicts drawn from that noise.
#
# MEASURED (issue #2220, sky130A x sha256, 25.9 ns SDC, ss_100C_1v60 + extracted
# max SPEF, propagated clock, derate 0.95/1.05, one sign-off script for every
# row): with the netlist held BYTE-IDENTICAL (`post_dft_netlist.v` sha256
# `fcaf73e3...`) and only `set_thread_count` changed 32 -> 10, global WNS moved
# **1.569724 ns** — 15.7x the withdrawn constant. REPRODUCED here at the program
# level (lane cy2183, 8HD-4, 2026-09-10, main 9c653d47f, image content id
# da2314d4100c...): feeding that very number made the program answer
# RETIMING_EFFECTIVE_APPLY and send the author to apply retiming, while +0.09 ns
# — two hundredths less than the constant — answered LOOP_BOUND_RECURRENCE with
# "loop-bound corroborated". The verdict, and the architectural remedy behind
# it, turned on 0.02 ns of a quantity whose own noise is 1.57 ns.
#
# The repair is NOT a bigger constant: the right floor is flow- and
# granularity-dependent, which is the whole point. A delta must arrive WITH the
# thing that makes it readable —
#
#   * `--retiming-netlist-byte-identical` — the retiming pass returned the same
#     netlist. Definitive, threshold-free: nothing changed, so nothing can have
#     improved. This is the loop-bound signature the docstring already names.
#   * `--retiming-wns-noise-floor-ns F` — a MEASURED control-vs-control dWNS
#     from the SAME flow at the SAME granularity (re-run the identical netlist
#     and record the spread). |delta| < F is then NOT_MEASURED, never
#     "ineffective".
#
# A delta with neither is reported as NOT_MEASURED and decides nothing. That is
# strictly less claim than before and never a greener verdict: no exit code
# moves, and the name heuristic keeps deciding exactly what it decided.

#: What a retiming datapoint was found to be. `None` = no datapoint supplied.
RETIMING_INEFFECTIVE_BYTE_IDENTICAL = "INEFFECTIVE_BYTE_IDENTICAL"
RETIMING_EFFECTIVE = "EFFECTIVE"
RETIMING_REGRESSED = "REGRESSED"
RETIMING_NOT_MEASURED_NO_FLOOR = "NOT_MEASURED_NO_NOISE_FLOOR"
RETIMING_NOT_MEASURED_BELOW_FLOOR = "NOT_MEASURED_BELOW_NOISE_FLOOR"

# A register whose base name is purely a synthesis tag (yosys ``_1234_`` /
# ``$auto$...`` / ``$abc$...``) carries no RTL bank identity.
_ANON_RE = re.compile(r"^\\?(_\d+_|\$[a-zA-Z].*)$")


@dataclass
class PathEndpoint:
    raw: str          # token as printed (e.g. "a_reg[5]" or "_1234_")
    bank: str         # instance path with trailing [n] bit-index stripped
    anonymized: bool


@dataclass
class CarryChain:
    """Evidence that the worst path is dominated by ONE carry-propagate add."""
    carry_cell_stages: int      # cells matching a carry-generate signature
    total_stages: int           # all cells on the path
    carry_delay_ns: float       # delay summed over the carry-cell stages
    total_delay_ns: float       # arrival time (last cumulative time on path)
    carry_delay_fraction: float # carry_delay / total_delay


@dataclass
class WorstPath:
    start: PathEndpoint
    end: PathEndpoint
    slack_ns: Optional[float]
    path_type: Optional[str]   # "max" (setup) | "min" (hold) | None
    carry: Optional[CarryChain] = None


def _bank_of(token: str) -> str:
    """Register bank identity: full instance path minus the trailing bit-index.

    ``\\a_reg[5]`` -> ``a_reg`` ; ``core/dp/a_reg[27]`` -> ``core/dp/a_reg`` ;
    ``_1234_`` -> ``_1234_``. Leading Verilog escape ``\\`` is dropped. Only a
    trailing ``[..]`` index is removed (bus bit); interior brackets are kept.
    """
    t = token.strip()
    if t.startswith("\\"):
        t = t[1:]
    t = re.sub(r"\[[0-9]+\]$", "", t)
    return t


def _mk_endpoint(token: str) -> PathEndpoint:
    bank = _bank_of(token)
    return PathEndpoint(raw=token, bank=bank, anonymized=bool(_ANON_RE.match(token)))


# A carry-propagate adder's chain is built from majority (carry-generate) and
# xor (sum) cells; every standard-cell library spells them with these stems.
# Matched on the CELL-TYPE token only (never an instance name), so no PDK /
# vendor literal is required — `maj3`, `xnor2`, `xor3`, `fa`/`ha` (full/half
# adder), and carry-in/out cells all count.
_CARRY_CELL_RE = re.compile(
    r"(?:^|_)(?:maj\d*|fa|ha|adder|carry|xnor\d*|xor\d*)(?:_|$|\d)", re.IGNORECASE)

# A path line: "   0.929    5.637 v _09131_/X (sky130_fd_sc_hd__maj3_2)"
_PATH_LINE_RE = re.compile(
    r"^\s*(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+[\^v]?\s*\S+\s*\(([^)]+)\)\s*$")

# Fraction of the arrival time that must sit on carry cells before we call the
# path CPA-dominated. 0.45 is deliberately well above "a few adders on the way"
# and below the ~0.85 a pure ripple shows.
CARRY_DOMINANT_FRACTION = 0.45


def _analyse_carry_chain(block: str) -> Optional[CarryChain]:
    """Sum the delay contributed by carry-generate/sum cells on one path block."""
    total_stages = 0
    carry_stages = 0
    carry_delay = 0.0
    last_time = 0.0
    for line in block.splitlines():
        m = _PATH_LINE_RE.match(line)
        if not m:
            continue
        delay = float(m.group(1))
        cum = float(m.group(2))
        cell = m.group(3).strip()
        total_stages += 1
        last_time = max(last_time, cum)
        # cell type is the trailing token of a lib cell name
        if _CARRY_CELL_RE.search(cell.split("__")[-1] if "__" in cell else cell):
            carry_stages += 1
            carry_delay += delay
    if total_stages == 0 or last_time <= 0:
        return None
    return CarryChain(
        carry_cell_stages=carry_stages,
        total_stages=total_stages,
        carry_delay_ns=round(carry_delay, 3),
        total_delay_ns=round(last_time, 3),
        carry_delay_fraction=round(carry_delay / last_time, 3),
    )


def _parse_worst_path(text: str) -> Optional[WorstPath]:
    """Parse OpenSTA/OpenROAD ``report_checks`` output; return the WORST
    (most-negative-slack) setup path block. Robust to multiple path blocks and
    to the two common slack-line orderings.
    """
    # Split into per-path blocks on the Startpoint header.
    blocks = re.split(r"(?=^\s*Startpoint:)", text, flags=re.MULTILINE)
    candidates: List[WorstPath] = []
    for blk in blocks:
        m_start = re.search(r"^\s*Startpoint:\s+(\S+)", blk, re.MULTILINE)
        m_end = re.search(r"^\s*Endpoint:\s+(\S+)", blk, re.MULTILINE)
        if not (m_start and m_end):
            continue
        m_type = re.search(r"^\s*Path Type:\s+(\w+)", blk, re.MULTILINE)
        # slack line: "slack (VIOLATED) -3.85" OR "-3.85 slack (VIOLATED)"
        slack: Optional[float] = None
        m_sl = re.search(r"slack\s*\([^)]*\)\s*(-?\d+\.?\d*)", blk)
        if not m_sl:
            m_sl = re.search(r"(-?\d+\.?\d*)\s+slack\s*\(", blk)
        if not m_sl:
            m_sl = re.search(r"^\s*slack\s+(-?\d+\.?\d*)\s*$", blk, re.MULTILINE)
        if m_sl:
            try:
                slack = float(m_sl.group(1))
            except ValueError:
                slack = None
        candidates.append(WorstPath(
            start=_mk_endpoint(m_start.group(1)),
            end=_mk_endpoint(m_end.group(1)),
            slack_ns=slack,
            path_type=(m_type.group(1) if m_type else None),
            carry=_analyse_carry_chain(blk),
        ))
    if not candidates:
        return None
    # Worst = most negative slack (None slack sorts last / least informative).
    def _key(w: WorstPath) -> float:
        return w.slack_ns if w.slack_ns is not None else float("inf")
    return min(candidates, key=_key)


def classify_retiming_datapoint(
    retiming_wns_delta: Optional[float],
    noise_floor_ns: Optional[float],
    netlist_byte_identical: bool,
) -> Optional[str]:
    """What, if anything, a retiming experiment established (vibe-ic#2220).

    Returns one of the ``RETIMING_*`` states, or ``None`` when no datapoint was
    supplied at all. The ONE rule: a dWNS reading is readable only against a
    floor that was MEASURED at the same granularity, or not at all when the
    pass returned the same netlist.
    """
    if netlist_byte_identical:
        # Threshold-free and definitive: the pass changed nothing, so no dWNS
        # it is paired with can be an improvement. Any reading beside it is the
        # measurement's own spread.
        return RETIMING_INEFFECTIVE_BYTE_IDENTICAL
    if retiming_wns_delta is None:
        return None
    if noise_floor_ns is None:
        return RETIMING_NOT_MEASURED_NO_FLOOR
    if abs(retiming_wns_delta) < noise_floor_ns:
        return RETIMING_NOT_MEASURED_BELOW_FLOOR
    if retiming_wns_delta > 0:
        return RETIMING_EFFECTIVE
    # <= -floor: an above-noise DEGRADATION. Not "effective", and emphatically
    # not "ineffective" — the old code called this band ">= EPS -> effective"
    # in its evidence line while taking neither branch in its verdict.
    return RETIMING_REGRESSED


def diagnose(
    worst: WorstPath,
    retiming_wns_delta: Optional[float],
    spec_microarch_free: bool,
    spec_latency_unconstrained: bool,
    relax_clock_proposed: bool,
    timing_driven_synth: bool = False,
    retiming_wns_noise_floor_ns: Optional[float] = None,
    retiming_netlist_byte_identical: bool = False,
) -> Dict[str, Any]:
    evidence: List[str] = []

    # Hard tripwire first: relaxing a HARD target period is never a remedy.
    if relax_clock_proposed:
        return {
            "verdict": "CLOCK_RELAX_FORBIDDEN",
            "remedy": None,
            "evidence": ["a clock-period relaxation was proposed; the target "
                         "period is HARD and is never relaxed to close timing"],
            "self_loop_by_name": None,
            "names_anonymized": None,
            "retiming_ineffective": None,
            "retiming_datapoint_state": None,
        }

    self_loop = (worst.start.bank == worst.end.bank)
    both_anon = worst.start.anonymized or worst.end.anonymized

    state = classify_retiming_datapoint(
        retiming_wns_delta, retiming_wns_noise_floor_ns,
        retiming_netlist_byte_identical)
    # `retiming_ineffective` is TRUE only where it was established, FALSE only
    # where it was ruled out, and None wherever the datapoint could not say —
    # NOT_MEASURED, never a defaulted zero (#2220).
    retiming_ineffective: Optional[bool] = None
    retiming_effective = False
    delta_txt = ("no dWNS supplied" if retiming_wns_delta is None
                 else f"dWNS {retiming_wns_delta:+.6f} ns")
    if state == RETIMING_INEFFECTIVE_BYTE_IDENTICAL:
        retiming_ineffective = True
        evidence.append(
            f"retiming returned a BYTE-IDENTICAL netlist ({delta_txt}) -> "
            f"ineffective (loop-bound corroborated); no threshold is involved, "
            f"the pass changed nothing")
    elif state == RETIMING_EFFECTIVE:
        retiming_ineffective = False
        retiming_effective = True
        evidence.append(
            f"retiming {delta_txt} >= measured noise floor "
            f"{retiming_wns_noise_floor_ns:.6f} ns -> effective")
    elif state == RETIMING_REGRESSED:
        retiming_ineffective = False
        evidence.append(
            f"retiming {delta_txt} <= -{retiming_wns_noise_floor_ns:.6f} ns "
            f"(measured noise floor) -> the pass made WNS WORSE by more than "
            f"the noise; that is neither effective nor loop-bound "
            f"corroboration, and it is not evidence about the recurrence")
    elif state == RETIMING_NOT_MEASURED_BELOW_FLOOR:
        evidence.append(
            f"retiming {delta_txt} is inside the measured noise floor "
            f"{retiming_wns_noise_floor_ns:.6f} ns -> NOT_MEASURED: this "
            f"reading cannot tell an ineffective pass from the flow's own "
            f"run-to-run spread, so it corroborates nothing")
    elif state == RETIMING_NOT_MEASURED_NO_FLOOR:
        evidence.append(
            f"retiming {delta_txt} arrived with NO declared noise floor -> "
            f"NOT_MEASURED: a dWNS is readable only against a floor measured "
            f"at the SAME granularity. Supply "
            f"--retiming-wns-noise-floor-ns <control-vs-control dWNS from this "
            f"flow>, or --retiming-netlist-byte-identical if the pass returned "
            f"the same netlist. MEASURED (#2220): with the netlist held "
            f"byte-identical, a thread-count change alone moved full-flow WNS "
            f"1.569724 ns")

    # Timing already met -> nothing to diagnose.
    if worst.slack_ns is not None and worst.slack_ns >= 0:
        return {
            "verdict": "TIMING_MET",
            "remedy": None,
            "evidence": [f"worst slack {worst.slack_ns:+.3f} ns >= 0"],
            "self_loop_by_name": self_loop,
            "names_anonymized": both_anon,
            "retiming_ineffective": retiming_ineffective,
            "retiming_datapoint_state": state,
        }

    # A measured retiming improvement is ground truth and OVERRIDES the name
    # heuristic (the path was not a pure single-register self-loop).
    if retiming_effective:
        return {
            "verdict": "RETIMING_EFFECTIVE_APPLY",
            "remedy": "APPLY_RETIMING_OR_PIPELINE",
            "evidence": evidence + ["measured retiming improvement overrides the "
                                    "self-loop name heuristic"],
            "self_loop_by_name": self_loop,
            "names_anonymized": both_anon,
            "retiming_ineffective": retiming_ineffective,
            "retiming_datapoint_state": state,
        }

    # Is the path dominated by ONE carry-propagate add? If so, splitting the
    # round further cannot help -- every cycle still costs one CPA.
    cpa_bound = bool(worst.carry
                     and worst.carry.carry_delay_fraction >= CARRY_DOMINANT_FRACTION)
    if worst.carry:
        evidence.append(
            f"carry-chain analysis: {worst.carry.carry_cell_stages}/"
            f"{worst.carry.total_stages} stages are carry-generate/sum cells "
            f"contributing {worst.carry.carry_delay_ns} ns of "
            f"{worst.carry.total_delay_ns} ns arrival "
            f"({worst.carry.carry_delay_fraction:.0%})"
            + (" -> CPA-DOMINATED: the per-cycle floor is ONE carry-propagate "
               "add; further round-splitting cannot go below it"
               if cpa_bound else ""))

    if self_loop:
        evidence.insert(0, f"worst-path start and end share register bank "
                           f"'{worst.start.bank}' -> single-register self-recurrence")
        remedy: str
        if cpa_bound and not timing_driven_synth:
            # A carry chain is far more often an AREA-MODE MAPPING than an
            # architectural floor: yosys techmaps $lcu to a Brent-Kung
            # parallel-prefix adder, and an untargeted mapper spends that
            # structure back down into a deep chain. Never send an author to
            # RTL surgery for a synthesis-flow defect.
            remedy = "MEASURE_SYNTH_KNOBS_POST_ROUTE_FIRST"
            evidence.append(
                "remedy: synthesis QoR was NOT attested as measured "
                "(--timing-driven-synth absent). Before ANY RTL change, settle "
                "the flow side -- but settle it POST-ROUTE, on the shipped "
                "SPEF, never on a pre-PnR number. Measured warnings, same "
                "design: (1) a delay target can be silently INERT (byte-"
                "identical netlist with and without it -- a cheap conclusive "
                "diff); (2) ENGAGING that ignored target improved pre-PnR "
                "arrival 22% and made post-route WORSE by 1.87 ns at +4.6% "
                "cells, because PnR already resizes after placement, so "
                "synth-time sizing keeps its area cost and loses the wire "
                "delay. A pre-PnR delta is NOT evidence and its sign can flip.")
        elif cpa_bound and spec_microarch_free:
            remedy = "BREAK_CARRY_CHAIN"
            evidence.append(
                "remedy: synthesis is already timing-driven and the path is "
                "still one full-width carry-propagate add -- break the carry "
                "chain itself: register the carry across sub-cycles (split the "
                "add into half-width slices; a register in the middle is the "
                "one split synthesis cannot merge back) or use a "
                "carry-select/carry-lookahead adder. More round cycles will "
                "NOT help: every cycle still costs one CPA.")
        elif spec_microarch_free and spec_latency_unconstrained:
            remedy = "RECOMMEND_MULTICYCLE_SPLIT"
            evidence.append("spec authorises the microarch as a free choice and "
                            "does not fix the latency-cycle count -> split the "
                            "recurrence cone over N>=2 clocked sub-stages "
                            "(per-cycle depth ~1/N; same HARD period)")
        else:
            remedy = "SPEC_BLOCKS_MICROARCH_CHANGE"
            miss = []
            if not spec_microarch_free:
                miss.append("microarch not declared free")
            if not spec_latency_unconstrained:
                miss.append("latency-cycle count is constrained")
            evidence.append("cannot retime (loop-bound), cannot relax the clock "
                            "(HARD), and " + " and ".join(miss) +
                            " -> HONEST floor; report the violated corner, do NOT "
                            "dress it as a pass")
        return {
            "verdict": "LOOP_BOUND_RECURRENCE",
            "remedy": remedy,
            "evidence": evidence,
            "self_loop_by_name": True,
            "names_anonymized": both_anon,
            "retiming_ineffective": retiming_ineffective,
            "retiming_datapoint_state": state,
        }

    # Not a same-bank self loop by name.
    #
    # #2220 — the gate asks whether there is a USABLE retiming datapoint, not
    # whether a number arrived. A dWNS that the program has just reported as
    # NOT_MEASURED is not a datapoint, and letting it suppress this branch is
    # how a noise reading came to stand in for the bank identity nobody could
    # see.
    _usable_datapoint = (retiming_ineffective is not None) or retiming_effective
    if both_anon and not _usable_datapoint:
        return {
            "verdict": "INCONCLUSIVE_NAMES_ANONYMIZED",
            "remedy": "SUPPLY_RETIMING_DELTA_OR_NAME_PRESERVING_NETLIST",
            "evidence": evidence + [
                f"register names are synthesis-anonymised "
                f"(start='{worst.start.raw}', end='{worst.end.raw}'); cannot "
                f"read bank identity — re-run STA on a name-preserving netlist "
                f"or provide the RTL recurrence, or supply a retiming-experiment "
                f"WNS delta"],
            "self_loop_by_name": False,
            "names_anonymized": True,
            "retiming_ineffective": retiming_ineffective,
            "retiming_datapoint_state": state,
        }

    return {
        "verdict": "NOT_SELF_LOOP",
        "remedy": "STANDARD_SIZING_SKEW_OR_PIPELINE",
        "evidence": evidence + [
            f"worst-path start bank '{worst.start.bank}' != end bank "
            f"'{worst.end.bank}' -> feed-forward path; apply cell sizing / "
            f"useful skew / add a pipeline stage"],
        "self_loop_by_name": False,
        "names_anonymized": both_anon,
        "retiming_ineffective": retiming_ineffective,
        "retiming_datapoint_state": state,
    }


_EXIT = {
    "TIMING_MET": 0,
    "LOOP_BOUND_RECURRENCE": 0,
    "RETIMING_EFFECTIVE_APPLY": 0,
    "NOT_SELF_LOOP": 0,
    "INCONCLUSIVE_NAMES_ANONYMIZED": 0,
    "CLOCK_RELAX_FORBIDDEN": 1,
}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Chip-agnostic diagnosis of a setup-limited iterative "
                    "datapath: detect a single-register self-recurrence "
                    "(retiming-ineffective, loop-bound) and route to the "
                    "in-spec multi-cycle-split remedy. Never relaxes the clock.")
    ap.add_argument("--sta-report", required=True,
                    help="OpenSTA/OpenROAD report_checks text (worst setup path)")
    ap.add_argument("--retiming-wns-delta", type=float, default=None,
                    help="WNS improvement (ns) from a retiming experiment; "
                         "omit if no retiming experiment was run. On its own "
                         "this decides NOTHING (#2220): pair it with "
                         "--retiming-wns-noise-floor-ns or "
                         "--retiming-netlist-byte-identical")
    ap.add_argument("--retiming-wns-noise-floor-ns", type=float, default=None,
                    help="MEASURED control-vs-control dWNS for THIS flow at "
                         "the SAME granularity as --retiming-wns-delta (re-run "
                         "the identical netlist and record the spread). A "
                         "|delta| below it is NOT_MEASURED, never "
                         "'ineffective'. There is no default: the right floor "
                         "is flow- and granularity-dependent, which is why the "
                         "old fixed 0.10 ns constant was withdrawn (#2220 "
                         "measured 1.569724 ns on a byte-identical netlist)")
    ap.add_argument("--retiming-netlist-byte-identical", action="store_true",
                    help="the retiming pass returned a BYTE-IDENTICAL netlist. "
                         "Definitive loop-bound corroboration and threshold-"
                         "free: nothing changed, so nothing improved")
    ap.add_argument("--spec-microarch-free", action="store_true",
                    help="the design spec authorises the microarch "
                         "(iterative/unrolled/pipelined/multi-cycle) as a free "
                         "choice")
    ap.add_argument("--spec-latency-unconstrained", action="store_true",
                    help="the design spec does NOT fix the latency-cycle count")
    ap.add_argument("--target-period-ns", type=float, default=None,
                    help="the HARD target clock period (informational; never "
                         "relaxed)")
    ap.add_argument("--relax-clock-proposed", action="store_true",
                    help="tripwire: someone proposed relaxing the clock -> FAIL")
    ap.add_argument("--timing-driven-synth", action="store_true",
                    help="attest that the synthesis QoR knobs have ALREADY "
                         "been settled by POST-ROUTE measurement on the "
                         "shipped SPEF (not by a pre-PnR delta). Without this, "
                         "a carry-dominated path is reported as an unsettled "
                         "FLOW question rather than an architectural floor.")
    ap.add_argument("--json", help="write JSON report to this path")
    args = ap.parse_args(argv)

    if (args.retiming_wns_noise_floor_ns is not None
            and args.retiming_wns_noise_floor_ns <= 0):
        print("error: --retiming-wns-noise-floor-ns must be > 0 — a floor of "
              "zero would readmit the defect #2220 withdrew, by making every "
              "reading sit above it", file=sys.stderr)
        return 2

    path = Path(args.sta_report)
    if not path.is_file():
        print(f"error: STA report not found: {path}", file=sys.stderr)
        return 2
    text = path.read_text(errors="replace")
    if not text.strip():
        print(f"error: empty STA report: {path}", file=sys.stderr)
        return 2

    worst = _parse_worst_path(text)
    if worst is None:
        # A relax-clock tripwire must fire even without a parseable path.
        if args.relax_clock_proposed:
            res = diagnose(WorstPath(_mk_endpoint("?"), _mk_endpoint("?"), None, None),
                           args.retiming_wns_delta, args.spec_microarch_free,
                           args.spec_latency_unconstrained, True,
                           args.timing_driven_synth,
                           args.retiming_wns_noise_floor_ns,
                           args.retiming_netlist_byte_identical)
        else:
            print(f"error: no Startpoint/Endpoint path parsed in {path}",
                  file=sys.stderr)
            return 2
    else:
        res = diagnose(worst, args.retiming_wns_delta, args.spec_microarch_free,
                       args.spec_latency_unconstrained, args.relax_clock_proposed,
                       args.timing_driven_synth,
                       args.retiming_wns_noise_floor_ns,
                       args.retiming_netlist_byte_identical)

    report = {
        "gate": "iterative_recurrence_timing_diagnosis",
        "verdict": res["verdict"],
        "remedy": res["remedy"],
        "target_period_ns": args.target_period_ns,
        "worst_path": (asdict(worst) if worst else None),
        "signals": {
            "self_loop_by_name": res["self_loop_by_name"],
            "names_anonymized": res["names_anonymized"],
            "retiming_ineffective": res["retiming_ineffective"],
            # #2220 — WHY it is True/False/None, in one word the reader can act
            # on. None with a state of NOT_MEASURED_* is a datapoint that
            # arrived and could not be read; it is not a zero.
            "retiming_datapoint_state": res.get("retiming_datapoint_state"),
        },
        "evidence": res["evidence"],
    }
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + "\n")

    print(f"{res['verdict']}: iterative_recurrence_timing_diagnosis"
          + (f" — remedy={res['remedy']}" if res["remedy"] else ""))
    for line in res["evidence"]:
        print(f"  - {line}")
    return _EXIT.get(res["verdict"], 0)


if __name__ == "__main__":
    sys.exit(main())
