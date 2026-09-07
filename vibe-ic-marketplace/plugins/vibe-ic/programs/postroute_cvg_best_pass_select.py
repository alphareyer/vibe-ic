#!/usr/bin/env python3
"""postroute_cvg_best_pass_select.py — WHICH pass of the post-route convergence
loop is the one that should SHIP, and whether the run shipped it.

THE DEFECT THIS EXISTS FOR (vibe-ic#2171)
=========================================
`phase3_one_shot_runner._SHIP_POSTROUTE_CVG_TCL` runs several
extract -> repair -> reroute passes and leaves whatever the LAST pass produced
in memory. Everything downstream — `routed_repaired.def`, the promoted
sign-off route, `SHIP_WNS_POSTROUTE` — describes that last state. A pass that
measured BETTER earlier in the same loop is simply gone.

MEASURED, over every phase-3 repair transcript on this host — 107 files, 67
DISTINCT transcript contents, of which 38 carry a convergence series. Under the
rule declared below, 7 of them shipped a state an earlier pass had already
beaten, and a further 3 measured a better setup that R2 correctly refuses:

    subservient x sky130A   pass2 -0.6197 ns, pass3 -0.9067 ns, SHIPPED -0.9067
                            -> 0.2870 ns measured and thrown away
    subservient x sky130A   pass1 -0.0923 ns, pass2 -0.2593 ns, SHIPPED -0.2593
                            -> 0.1669 ns, on 3 distinct transcripts
    caravel_user_project    pass6 +0.2911 ns MET, pass7 -0.9665 ns,
                            SHIPPED -0.7271 ns VIOLATED, on 3 transcripts
                            -> a run that had CLOSED setup shipped a violation

Every one of those seven is reported ``BEST_UNRESTORABLE`` rather than
``RESTORE``, and that is the debt stated exactly: the loop of the day kept no
geometry, so the better pass could not have been shipped even by a run that
knew it was better. Keeping the DEF is the fix; knowing the number never was.

Nothing goes red on any of these. The loop's plateau break fires on the pass
that got WORSE — it breaks *after* printing that pass's number, with that
pass's geometry in memory — so a series that dips is indistinguishable from one
that converged, from the verdict alone.

THE DECLARED RULE — this is the policy, stated once, here
=========================================================
A "state" is one measurement of the design: (setup WNS, DRV count, hold slack).
The loop produces one per pass, plus the FINAL state it would otherwise ship.

**SETUP WNS DECIDES.** It is the number the promotion gate keys on, the number
this loop exists to close, and the number the issue is about. TNS is
deliberately not the metric: the loop never measures it, and a metric nothing
measures cannot be a policy.

**TWO REFUSALS, and they are refusals rather than terms in a score**, because a
score that trades one sign-off axis for another makes that trade silently:

  R1  HOLD IS NEVER TRADED FOR SETUP. A state whose hold slack was MEASURED and
      is VIOLATED can never beat one that is not in that condition, however
      much setup it buys — and is always beaten by one that is not. UNMEASURED
      hold is NOT a violation: it neither disqualifies a pass nor certifies
      one, and drops straight through to the setup comparison. (This is the
      refusal lane cz2160 built into its sizing pass, applied to pass
      selection.)

  R2  A WINNER MAY NOT REGRESS DRV. Once setup has chosen a winner, it is
      REFUSED if its MEASURED design-rule violation count is HIGHER than the
      MEASURED count of the state it would displace. The loop's own closure
      test is `wns >= -0.001 && drv == 0`, so DRV is a sign-off axis here, not
      a nuisance; and this is the same per-axis non-regression shape
      `_ship_repair_refusals` already applies to the promotion decision.
      Comparable only when BOTH counts were measured — the emitter writes -1
      when the probe could not run, and UNMEASURED is not ZERO, so an
      unmeasured count neither refuses nor excuses.

A refused winner is reported as REFUSED_DRV_REGRESSION and the run ships what
it already had. That is a fact worth publishing: the loop measured a better
setup and could not take it without giving back design rules.

TIES LEAVE THE INCUMBENT. Two setup numbers within `TIE_TOL_NS` are the same
number, so an equal-scoring later pass never displaces an earlier one, the
winner is the EARLIEST state of its quality, and no restore is ever bought for
a difference the loop itself would not call a difference. The comparison is a
TOTAL ORDER on (hold-not-violated, setup WNS) — deliberately, so the winner
cannot depend on the order the states are scanned in. An earlier draft of this
module ranked DRV inside the comparison and was NOT a total order: on a real
caravel series a state won by a CHAIN (A beat the incumbent on setup, B beat A
on DRV) and ended up as "winner" while not itself beating the incumbent it
would displace. Refusals are applied to the winner, once, against the state it
displaces — never mixed into the ranking.

WHY A BEST NUMBER IS NOT ENOUGH, AND WHAT THE LOOP HAD TO GROW
==============================================================
Remembering the best NUMBER and shipping a different netlist is the defect, not
the fix: the report would then describe a tree that did not ship. Keeping the
best pass means keeping its GEOMETRY, so the loop now writes a DEF checkpoint
at every pass and the winner is RESTORED before sign-off.

The selection lives HERE and only here, in Python, and the loop checkpoints
EVERY pass unconditionally rather than implementing the rule a second time in
Tcl. Two implementations of one policy is how a drift starts; the emitter's job
is to keep the geometry, this module's job is to choose.

RESTORING IS A FRESH SESSION, AND THAT IS NOT A STYLE CHOICE
============================================================
A mid-session rollback is MEASURED to be unavailable on this toolchain. From
this repo's antenna loop, same image, against a control that survives:

    `odb::dbChip_destroy [[ord::get_db] getChip]` + `read_db` restores the
    routing (check_antennas agrees, 3 == 3) and then `report_worst_slack -max`
    dies with `[CRITICAL ORD-2008] unknown master term type`; WITHOUT the
    restore the same session answers 12.26 and finishes.

and ODB's ECO journal restores neither state. So the restore is a SECOND,
fresh OpenROAD invocation that reads the winning checkpoint DEF — a clean STA
network by construction, exactly like the repair session itself, which is
already a fresh session over `routed.def`.

AND THE RESTORE IS VERIFIED, NOT TRUSTED
========================================
`verify_restore` re-measures the restored design and requires the winning
pass's number back within `RESTORE_AGREEMENT_TOL_NS`. Trusting the loop's own
bookkeeping would leave exactly the hole this issue is about — a published
number that no one confirmed describes the tree that shipped. A restore whose
re-measurement DISAGREES is REFUSED: the run keeps the last-pass artefacts and
says the two numbers disagreed. It is never silently accepted, and the
disagreement is never explained away.

chip/PDK/vendor-AGNOSTIC: arithmetic over the loop's own marker vocabulary. No
design, PDK, corner or vendor literal appears in the logic.

CLI::

    # what would this run have shipped, and what did it ship?
    python3 postroute_cvg_best_pass_select.py <log-or-run-dir>

    # the census: every run tree under a root, membership not just a count
    python3 postroute_cvg_best_pass_select.py --census <root> [--json out.json]
"""
from __future__ import annotations

# --- sibling-import path (vibe-ic#2104) ------------------------------------
# `programs/` is a flat directory whose modules import each other by BARE
# name. Python puts a file's own directory on `sys.path` only when that file
# is run as `__main__`; under `importlib.util.spec_from_file_location` — how
# the gates, the wiring audit and much of the suite load a program — it does
# not, so every bare sibling import below raises ModuleNotFoundError. Restore
# the condition the file is written for. Idempotent, and the same shape the
# sibling programs that already carry it use.
import os as _os                                                    # noqa: E402
import sys as _sys                                                  # noqa: E402

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
# ---------------------------------------------------------------------------

import argparse                                                     # noqa: E402
import json                                                         # noqa: E402
import re                                                           # noqa: E402
from dataclasses import dataclass, asdict                           # noqa: E402
from pathlib import Path                                            # noqa: E402
from typing import List, Optional, Tuple                            # noqa: E402

import _atomic_artefact as _atomic                                  # noqa: E402
import _prose_polarity as _pp                                       # noqa: E402

__all__ = [
    "SETUP_MET_TOL_NS", "TIE_TOL_NS", "RESTORE_AGREEMENT_TOL_NS",
    "DECLARED_RULE",
    "State", "beats", "refuse_drv_regression", "select_best", "parse_states", "decide",
    "pass_route_violations", "TERMINAL_MARKERS",
    "verify_restore", "census_one", "census",
]

#: The loop's OWN closure tolerance, mirrored from `_SHIP_POSTROUTE_CVG_TCL`
#: (`$_cvg_wns >= -0.001`). Mirrored, not re-invented, so "met" means the same
#: thing to the loop, to the exhaustion audit and to this selector.
SETUP_MET_TOL_NS = 0.001

#: Two setup numbers closer than this are the same number, and the INCUMBENT
#: keeps the title. Same magnitude as the closure tolerance on purpose: a
#: difference the loop would not call a closure difference is not one here.
TIE_TOL_NS = 0.001

#: How closely the RESTORED design's re-measurement has to reproduce the
#: winning pass's own number before the restore may ship.
#:
#: MEASURED, not assumed. subservient x gf180mcuD in the pinned image on 8HD-9:
#: the loop checkpointed pass 0 at setup -0.47528204939182533 ns, hold
#: 0.042967852714256675 ns, DRV 0; a fresh session reading that checkpoint DEF
#: on the same basis answered -0.47528204939182533, 0.042967852714256675 and 0.
#: All three axes to every digit printed, divergence 0.000000 ns.
#:
#: The tolerance is nevertheless kept at the loop's OWN closure tolerance rather
#: than at the measured zero, so a last-place-digit difference is not read as a
#: failed restore. It is still three orders of magnitude below any gain this
#: selector acts on (the smallest on this fleet's corpus is 0.0297 ns), so it
#: cannot launder a restore that landed on a different design. What it is NOT is
#: a licence to disagree: a divergence over this is a REFUSAL, and the refusal
#: names both numbers.
RESTORE_AGREEMENT_TOL_NS = 0.001

#: The rule, in one string, so a reader who never opens this file still gets
#: the policy in the run's own report — and so a test can pin that the policy
#: a run PUBLISHES is the policy this module APPLIES.
DECLARED_RULE = (
    "setup WNS decides. Ranking is a TOTAL ORDER on (hold-not-violated, setup "
    "WNS): a MEASURED hold violation never beats a state without one, and "
    "UNMEASURED hold neither disqualifies nor certifies. More setup slack wins "
    "by more than 0.001 ns; a tie leaves the incumbent, so the winner is the "
    "EARLIEST state of its quality. The winner is then REFUSED if its MEASURED "
    "DRV count is higher than the MEASURED DRV count of the state it would "
    "displace — an unmeasured count neither refuses nor excuses."
)


@dataclass
class State:
    """One measurement of the design. `label` is the pass index as a string, or
    ``"final"`` for the state the loop would otherwise ship."""
    label: str
    wns: Optional[float]
    drv: Optional[int] = None
    hold: Optional[float] = None

    # --- the three predicates the rule is written in ----------------------
    @property
    def hold_violated(self) -> bool:
        """MEASURED and negative. `None` is UNMEASURED and is NOT a violation."""
        return self.hold is not None and self.hold < -SETUP_MET_TOL_NS

    @property
    def setup_met(self) -> bool:
        return self.wns is not None and self.wns >= -SETUP_MET_TOL_NS

    @property
    def drv_measured(self) -> bool:
        """The emitter writes -1 when the violation-count probe could not run.
        UNMEASURED is not ZERO, and a negative count is not a population."""
        return self.drv is not None and self.drv >= 0


def beats(cand: State, best: State) -> bool:
    """The RANKING half of the declared rule: a TOTAL ORDER on
    (hold-not-violated, setup WNS), incumbent-preserving on a tie.

    The DRV refusal is deliberately NOT here. Mixing it into the comparison is
    what cost the earlier draft its transitivity, and a non-transitive
    comparison makes the winner depend on the scan order — which is a coin, not
    a policy. See `refuse_drv_regression`, which is applied to the winner once,
    against the state it would displace.

    A candidate with no WNS at all cannot beat anything (an unmeasured state is
    not a better one) and is beaten by any state that has one."""
    # R1 — hold is never traded for setup
    if cand.hold_violated != best.hold_violated:
        return best.hold_violated
    if cand.wns is None:
        return False
    if best.wns is None:
        return True
    return cand.wns > best.wns + TIE_TOL_NS


def refuse_drv_regression(winner: State, incumbent: State) -> Optional[str]:
    """R2, applied ONCE to the chosen winner against the state it displaces.

    Returns the reason string when the winner must be refused, else None.
    Comparable only when BOTH counts were measured: the emitter writes -1 when
    the violation-count probe could not run, and an unmeasured count neither
    refuses a winner nor excuses one."""
    if not (winner.drv_measured and incumbent.drv_measured):
        return None
    if winner.drv <= incumbent.drv:
        return None
    return (f"pass {winner.label} measured {winner.wns} ns of setup against "
            f"{incumbent.wns} ns, but carries {winner.drv} design-rule "
            f"violations where the state it would displace carries "
            f"{incumbent.drv}. Setup is not bought with design rules: the "
            f"winner is REFUSED and the run ships what it already had.")


def select_best(states: List[State]) -> Optional[State]:
    """Left-to-right incumbent scan over the states in the loop's own order."""
    best: Optional[State] = None
    for s in states:
        if best is None or beats(s, best):
            best = s
    return best


# --- the loop's marker vocabulary -----------------------------------------
_WNS_RE = re.compile(r"^\s*SHIP_WNS_CVG_PASS(\d+):\s*(\S+)", re.M)
_DRV_RE = re.compile(r"^\s*SHIP_DRV_CVG_PASS(\d+):\s*(\S+)", re.M)
_HOLD_RE = re.compile(r"^\s*SHIP_HOLD_CVG_PASS(\d+):\s*(\S+)", re.M)
#: The checkpoint the loop wrote for a pass. A pass with a measurement but NO
#: checkpoint marker can still WIN — winning is about the numbers — but it
#: cannot be shipped, and `decide` says so under its own verdict rather than
#: pretending the last pass won. A best number without its geometry is the
#: defect this module exists for, not a fix for it.
_CKPT_RE = re.compile(r"^\s*SHIP_CVG_CKPT:\s*pass=(\d+)\s+def=(\S+)", re.M)
_FINAL_WNS_RE = re.compile(r"^\s*SHIP_WNS_POSTROUTE:\s*(\S+)", re.M)
_FINAL_UNROUTED_RE = re.compile(r"^\s*SHIP_WNS_UNROUTED:\s*(\S+)", re.M)
_FINAL_DRV_RE = re.compile(r"^\s*SHIP_CVG_FINAL_DRV:\s*(\S+)", re.M)
_FINAL_HOLD_RE = re.compile(r"^\s*SHIP_CVG_FINAL_HOLD:\s*(\S+)", re.M)
_RESTORE_WNS_RE = re.compile(r"^\s*SHIP_RESTORE_WNS:\s*(\S+)", re.M)
_RESTORE_DONE_RE = re.compile(r"^\s*SHIP_CVG_RESTORE_DONE\b", re.M)
_NOOP_RE = re.compile(r"^\s*SHIP_REPAIR_NOOP\b", re.M)

#: The terminal marker for the pass whose geometry the loop leaves in memory.
FINAL_LABEL = "final"


#: The markers by which the loop announces it broke on its OWN policy. Spelled
#: to match `ship_postroute_convergence_exhaustion_check.TERMINAL_MARKERS`; the
#: two audits must not disagree about what a policy exit is.
TERMINAL_MARKERS = (
    "SHIP_CVG_CLOSED_DRV_UNMEASURED",
    "SHIP_CVG_CLOSED",
    "SHIP_CVG_PLATEAU",
    "SHIP_CVG_NONNUMERIC",
)


def _terminal_break(raw: str) -> Optional[str]:
    """The loop's own break marker, if it announced one."""
    for marker in TERMINAL_MARKERS:
        if re.search(rf"^\s*{re.escape(marker)}\b", raw or "", re.M):
            return marker
    return None


def _f(tok) -> Optional[float]:
    try:
        return float(tok)
    except (TypeError, ValueError):
        return None


def _i(tok) -> Optional[int]:
    v = _f(tok)
    return None if v is None else int(v)


def parse_states(raw: str) -> Tuple[List[State], Optional[State], dict]:
    """`(pass states, final state, checkpoints)` from a repair transcript.

    A pass with a WNS but no DRV/HOLD line keeps the missing field None — the
    same choice `ship_postroute_convergence_exhaustion_check.parse_passes`
    makes, and for the same reason: dropping it would silently shorten the
    series."""
    wns = {int(i): _f(v) for i, v in _WNS_RE.findall(raw or "")}
    drv = {int(i): _i(v) for i, v in _DRV_RE.findall(raw or "")}
    hold = {int(i): _f(v) for i, v in _HOLD_RE.findall(raw or "")}
    ckpt = {int(i): p for i, p in _CKPT_RE.findall(raw or "")}
    passes = [State(label=str(i), wns=wns.get(i), drv=drv.get(i),
                    hold=hold.get(i))
              for i in sorted(set(wns) | set(drv) | set(hold))]

    m = _FINAL_WNS_RE.search(raw or "") or _FINAL_UNROUTED_RE.search(raw or "")
    final: Optional[State] = None
    if m:
        fd = _FINAL_DRV_RE.search(raw or "")
        fh = _FINAL_HOLD_RE.search(raw or "")
        f_drv = _i(fd.group(1)) if fd else None
        f_hold = _f(fh.group(1)) if fh else None
        # DERIVED, NOT GUESSED, and only where the emitter did not state it.
        # Every terminal marker breaks the loop IMMEDIATELY after the
        # measurement and before any repair, so on those exits the final
        # geometry IS the last measured pass's geometry and its DRV and hold
        # are that pass's own numbers. Without this a transcript from before
        # the emitter published the final axes (every archived run) would carry
        # an UNMEASURED final DRV, R2 would be inapplicable on every one of
        # them, and a winner that regresses design rules would be waved through
        # by an axis nobody looked at.
        # No terminal marker means the bound was exhausted: the loop rerouted
        # once more, the final state is NEW, and its axes stay UNMEASURED.
        if passes and _terminal_break(raw):
            if f_drv is None:
                f_drv = passes[-1].drv
            if f_hold is None:
                f_hold = passes[-1].hold
        final = State(label=FINAL_LABEL, wns=_f(m.group(1)),
                      drv=f_drv, hold=f_hold)
    return passes, final, ckpt


#: The router's own violation line. Attribution is POSITIONAL because that is
#: the only thing the transcript actually states: the router does not name the
#: pass, and inventing a name for it would be worse than reading the order it
#: was written in.
_VIOL_RE = re.compile(r"[Nn]umber of violations\s*=\s*(\d+)")


def pass_route_violations(raw: str) -> dict:
    """`{pass index: DRC violations of the route the checkpoint holds}`.

    The checkpoint marker is printed at the instant `write_def` froze that
    pass's geometry, so the router line immediately BEFORE it is the count the
    route in that DEF converged to. That makes the boundary exact and needs no
    separate fence: the last `Number of violations` before
    ``SHIP_CVG_CKPT: pass=k`` describes the geometry pass *k* checkpointed, and
    it stays correct even when an intervening checkpoint failed to be written
    (the most recent count is still the most recent route).

    The LAST line in the window, not the first — `detailed_route` prints one
    per optimisation iteration and the last is what it converged to, the same
    choice `_parse_ship_repair_log` makes for the run as a whole.

    A pass with NO violation line before it is ABSENT from the result, never 0:
    a route whose DRC count was never stated is not a route known to be clean.
    """
    # POLARITY (vibe-ic#712). This transcript is NOT pure machine syntax: the
    # flow writes its own English into it beside the router's output —
    # `SHIP_REPAIR_NOOP: 1 (repair changed no instance; base route kept rather
    # than re-routed for nothing)` and the antenna loop's multi-sentence
    # `..._NOT_RESTORED:` note are both `puts` of prose into the same stream. A
    # sentence CAN therefore reach this regex, and this lane measured the cost
    # of assuming otherwise one level up: a bare `write_def` scan over this same
    # deck matched the word inside a COMMENT and reddened a correct design.
    # So a count whose own RECORD denies it is not a measurement. Records here
    # are lines, which is what `extra_breaks` is for — the scoping rule stays in
    # the one module that owns it rather than being re-invented here.
    viols = []
    for _m in _VIOL_RE.finditer(raw or ""):
        _lo, _hi = _pp.sentence_scope(raw or "", _m.start(), _m.end(),
                                      extra_breaks=("\n",))
        if _pp.is_denied((raw or "")[_lo:_hi]):
            continue
        viols.append((int(_m.group(1)), _m.start()))
    out: dict = {}
    for m in _CKPT_RE.finditer(raw or ""):
        before = [n for n, at in viols if at < m.start()]
        if before:
            out[int(m.group(1))] = before[-1]
    return out


def decide(raw: str) -> dict:
    """Should this run RESTORE an earlier pass before sign-off, and which one?

    Returns a dict a caller can act on and a reader can audit. Verdicts:

      ``LOOP_NOT_ENTERED``  the emitter disclosed SHIP_REPAIR_NOOP — the base
                            route was kept and there was no loop. Not a pass,
                            not an error.
      ``NO_SERIES``         no convergence passes and no such disclosure. The
                            loop did not run here; there is nothing to select
                            from. NOT a "the last pass was best".
      ``LAST_IS_BEST``      the state the loop leaves in memory already wins
                            under the declared rule. Nothing to do.
      ``REFUSED_DRV_REGRESSION``
                            an earlier pass wins on setup and carries a HIGHER
                            measured DRV count than the state it would
                            displace, so it is refused (R2). Reported, never
                            folded into LAST_IS_BEST: the run measured a better
                            setup and could not take it.
      ``RESTORE``           an earlier pass wins AND its checkpoint exists.
      ``BEST_UNRESTORABLE`` an earlier pass wins and its checkpoint is MISSING.
                            Reported, never rounded down to LAST_IS_BEST: "the
                            winner has no geometry" and "the last pass won" are
                            different facts and must not read the same.
    """
    passes, final, ckpt = parse_states(raw)
    out = {
        "declared_rule": DECLARED_RULE,
        "passes": [asdict(p) for p in passes],
        "final": asdict(final) if final else None,
        "checkpoints": {str(k): v for k, v in sorted(ckpt.items())},
        "verdict": None, "winner": None, "gain_ns": None,
        "restore_def": None, "winner_route_violations": None,
        "detail": "",
    }
    if not passes:
        if _NOOP_RE.search(raw or ""):
            out["verdict"] = "LOOP_NOT_ENTERED"
            out["detail"] = ("the emitter disclosed SHIP_REPAIR_NOOP: the "
                             "repair changed no instance, the base route was "
                             "kept, and the convergence loop was never entered")
        else:
            out["verdict"] = "NO_SERIES"
            out["detail"] = ("no SHIP_WNS_CVG_PASS marker and no "
                             "SHIP_REPAIR_NOOP disclosure — this loop did not "
                             "run here, which is not a selection result")
        return out
    if final is None:
        out["verdict"] = "NO_SERIES"
        out["detail"] = ("the series was measured but the loop published no "
                         "final state (no SHIP_WNS_POSTROUTE / "
                         "SHIP_WNS_UNROUTED) — there is nothing to compare the "
                         "winner against")
        return out

    # The final state is the INCUMBENT: it is the one the run already holds, so
    # an earlier pass has to BEAT it to displace it, and a tie ships what is
    # already there rather than paying for a restore that buys nothing.
    best = final
    for p in passes:
        if beats(p, best):
            best = p
    out["winner"] = asdict(best)
    if best.label == FINAL_LABEL:
        out["verdict"] = "LAST_IS_BEST"
        out["detail"] = ("the state the loop leaves in memory is the winner "
                         "under the declared rule")
        return out

    # The setup the winner is worth over the state it would displace. Computed
    # BEFORE the refusal below, and published on the refusal path too: a
    # refusal that hides the number it refused is unreadable evidence.
    gain = (None if (best.wns is None or final.wns is None)
            else best.wns - final.wns)
    out["gain_ns"] = gain

    # R2 — the winner is refused if it buys setup with design rules. Applied
    # ONCE, here, against the state it would displace, and reported under its
    # own verdict: "an earlier pass was better on setup and could not be taken"
    # is a different fact from "the last pass was best", and the two must not
    # read the same.
    _refusal = refuse_drv_regression(best, final)
    if _refusal:
        out["verdict"] = "REFUSED_DRV_REGRESSION"
        out["detail"] = _refusal
        return out
    path = ckpt.get(int(best.label)) if best.label.isdigit() else None
    if not path:
        out["verdict"] = "BEST_UNRESTORABLE"
        out["detail"] = (
            f"pass {best.label} beats the state the loop leaves in memory"
            + (f" by {gain:+.4f} ns of setup" if gain is not None else "")
            + ", and NO checkpoint was recorded for it — the winning geometry "
              "does not exist, so the number cannot be shipped. Reported "
              "rather than ignored: a winner with no geometry is not the same "
              "fact as the last pass having won.")
        return out
    out["verdict"] = "RESTORE"
    out["restore_def"] = path
    # The DRC count that belongs to the RESTORED route, so the promotion gate
    # judges the tree that would ship. ABSENT when the transcript never stated
    # it — the caller must treat that as "not stated", not as clean.
    out["winner_route_violations"] = pass_route_violations(raw).get(
        int(best.label))
    out["detail"] = (
        f"pass {best.label} beats the state the loop leaves in memory"
        + (f" by {gain:+.4f} ns of setup" if gain is not None else "")
        + f"; its checkpoint is {path} and must be restored before sign-off")
    return out


def verify_restore(decision: dict, restore_log: str) -> Tuple[bool, str]:
    """Did the RESTORED design re-measure to the winning pass's own number?

    The restore is proven by re-measurement, never by the loop's bookkeeping.
    Returns `(ok, reason)`; `ok` False always carries a reason naming both
    numbers, and an unmeasurable re-measurement is a REFUSAL, never a pass."""
    win = (decision or {}).get("winner") or {}
    want = win.get("wns")
    if want is None:
        return False, ("the winning pass has no WNS to reproduce, so a "
                       "restore cannot be verified")
    if not _RESTORE_DONE_RE.search(restore_log or ""):
        return False, ("the restore session did not reach "
                       "SHIP_CVG_RESTORE_DONE — it is UNMEASURED whether the "
                       "restored design carries the winning numbers, which is "
                       "not the same as it not carrying them")
    m = _RESTORE_WNS_RE.search(restore_log or "")
    # POLARITY (vibe-ic#712), HERE, because this is where the restored number is
    # first read and every later reader keys on this decision. The transcript is
    # not pure machine syntax — the flow `puts` its own English into the same
    # stream — so a record can carry the marker AND deny it, and a denied
    # measurement must refuse the restore outright rather than be half-applied
    # downstream. Records here are lines, declared through `extra_breaks` so the
    # scoping rule stays in the module that owns it.
    if m is not None:
        _lo, _hi = _pp.sentence_scope(restore_log or "", m.start(), m.end(),
                                      extra_breaks=("\n",))
        _denial = _pp.is_denied((restore_log or "")[_lo:_hi])
        if _denial:
            return False, (
                f"the restore session's own record DENIES the number it "
                f"carries (denial word {_denial!r}): "
                f"{(restore_log or '')[_lo:_hi].strip()[:160]!r}. A value its "
                f"own sentence retracts is not a re-measurement, and the "
                f"restore is REFUSED rather than shipped on it.")
    got = _f(m.group(1)) if m else None
    if got is None:
        return False, ("the restore session published no SHIP_RESTORE_WNS — "
                       "the restored design was not re-measured")
    if abs(got - want) > RESTORE_AGREEMENT_TOL_NS:
        return False, (
            f"the restored design re-measures {got:+.6f} ns where pass "
            f"{win.get('label')} measured {want:+.6f} ns — a divergence of "
            f"{abs(got - want):.6f} ns, over the "
            f"{RESTORE_AGREEMENT_TOL_NS} ns agreement tolerance. The restore "
            f"is REFUSED: a number that does not reproduce does not describe "
            f"the tree that would ship.")
    return True, (f"restored design re-measures {got:+.6f} ns, reproducing "
                  f"pass {win.get('label')}'s {want:+.6f} ns within "
                  f"{RESTORE_AGREEMENT_TOL_NS} ns")


# --- census ----------------------------------------------------------------
#: The transcript this loop writes. Named here so the census and the runner
#: cannot disagree about which file carries the series.
REPAIR_LOG_NAME = "signoff_spef_repair.log"


def census_one(path: Path) -> dict:
    """`decide()` over one transcript, plus where it came from."""
    try:
        raw = path.read_text(errors="replace")
    except OSError as exc:
        # "could not read it" is not "read it and it was empty".
        return {"log": str(path), "verdict": "UNREADABLE",
                "detail": f"{type(exc).__name__}: {exc}"}
    row = decide(raw)
    row["log"] = str(path)
    return row


def census(root: Path) -> List[dict]:
    """Every repair transcript under `root`, newest path order irrelevant."""
    return [census_one(p) for p in sorted(root.rglob(REPAIR_LOG_NAME))]


def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("target", help="a repair transcript, a run directory, or "
                                   "(with --census) a root to walk")
    ap.add_argument("--census", action="store_true",
                    help="walk TARGET for every %s and report the membership"
                         % REPAIR_LOG_NAME)
    ap.add_argument("--json", help="also write the result here")
    args = ap.parse_args(argv)

    target = Path(args.target)
    if args.census:
        rows = census(target)
        acted = [r for r in rows
                 if r.get("verdict") in ("RESTORE", "BEST_UNRESTORABLE")]
        payload = {"root": str(target), "runs": len(rows),
                   "would_restore": len(acted), "rows": rows}
        for r in acted:
            g = r.get("gain_ns")
            print(f"{(f'{g:+.4f}' if g is not None else '     ?    ')} ns  "
                  f"{r['verdict']:<18} {r['log']}")
        print(f"{len(acted)} of {len(rows)} transcript(s) shipped a state an "
              f"earlier pass had already beaten")
    else:
        if target.is_dir():
            found = sorted(target.rglob(REPAIR_LOG_NAME))
            if not found:
                print(f"NOT_MEASURED: no {REPAIR_LOG_NAME} under {target}")
                return 2
            target = found[0]
        payload = census_one(target)
        print(json.dumps(payload, indent=2, default=str))
    if args.json:
        # ATOMIC (vibe-ic#1082). `--json` is a DECLARED report destination: a
        # direct `write_text` creates the final name first and fills it second,
        # so a writer that dies between the two leaves a truncated census under
        # the name a reader treats as "the census was produced".
        _atomic.write_json(args.json, payload, indent=2, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
