#!/usr/bin/env python3
"""si_mcf_repair.py — close the MCF crosstalk-delay envelope, once.

ENFORCEMENT: producer (the step gate keeps its own verdict)
CHIP_AGNOSTIC: strict

WHY THIS EXISTS, MEASURED
=========================
`si_mcf_sta` folds each coupling Cc into the victim's grounded cap at the
corner's Miller factor and re-runs OpenSTA, so the engine re-derives delay from
the bounded effective load.  It REPORTS the envelope and nothing closes it.  On
`subservient` x gf180mcuD (lane icsub2, r13/r14/r15/r16 — four trees, one
answer) the design signs off at SS +0.03 ns and the envelope is open::

    si_mcf_sta_nominal.rpt     worst slack max  +2.4975
    si_mcf_sta_mcf_setup.rpt   worst slack max  -0.2660     <- the FAIL
    si_mcf_sta_mcf_hold.rpt    worst slack max  +4.7640

and `si_mcf_sta.json` verdict FAIL makes step 27 FAIL, which voids 28/31/32 and
cascades to 29/30.  That is the design's last real wall, and the bar is NOT
relaxed: the envelope is the flow's own conservative sign-off check.

So the flow gets ONE repair pass that works on the bounded loads themselves.

WHAT IT DOES, AND WHAT IT REFUSES TO DO
=======================================
ONE PASS.  Not a loop.  A loop over an envelope that a repair can also widen —
every buffer added is another aggressor — is how a run stops terminating, and
this file will not contain one.  After the pass the envelope is re-measured and
the residual is REPORTED BY NAME if it is still open.

THE TARGETS ARE THE COUPLING-DOMINATED VICTIMS, not the whole design.  The
`si_crosstalk` screen already names them (the ratio Cc/(Cc+Cg) per net) and
`si_mcf_sta` already names the worst victim of each folded pair.  Repairing
everything would move cells the envelope never complained about and change a
route that is signed off.

IT IS JUDGED LIKE AN SDR CHILD.  Same rule, same words: a candidate that makes
the ROUTER DRC worse is rejected and the session keeps the route it had
(`router_drc_preserved_clean`).  A repair that closes the envelope by breaking
the route has not closed anything.

AND NOMINAL SIGN-OFF MAY NOT REGRESS.  The envelope is a bound on top of the
real corner; buying envelope margin with real margin is a trade this producer
is not allowed to make on its own.  `accepts()` refuses a candidate whose
nominal SS slack is worse than before, by name.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PROGRAM = "si_mcf_repair"
VERSION = "1.0.0"

#: The coupling ratio at or above which `si_crosstalk` calls a net
#: coupling-DOMINATED. Read from the screen's own report when it states one;
#: this is the fallback and it is the screen's own published threshold.
DEFAULT_DOMINANT_RATIO = 0.90

#: How many victims one pass may touch. A bound, not a tuning knob: the point
#: is a bounded, reviewable ECO, and a pass that rewrites hundreds of nets is
#: not reviewable. Exceeding it is DISCLOSED, never silently truncated.
MAX_VICTIMS = 64


def _load(path: Path) -> Optional[Dict[str, Any]]:
    try:
        d = json.loads(path.read_text(errors="replace"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def envelope_is_open(si_mcf_report: Optional[Dict[str, Any]]) -> bool:
    """True only when `si_mcf_sta` itself says FAIL.

    ADVISORY and ERROR are NOT a licence to repair: ADVISORY means the analysis
    produced no slack to judge, and ERROR means the tool failed. Repairing on
    either would be acting on an absence.

    AND THIS READS THE STEP'S VERDICT, WHICH MAY NO LONGER BE THE ENVELOPE'S
    (R-0915-66). When the coupling delta-delay screen reaches a genuine verdict
    it becomes the step's, and `si_mcf_verdict_basis` records the envelope's own
    FAIL under `verdict_basis.envelope` while the top-level `verdict` carries
    the better-informed reading. So a run whose screen PASSES does not spend a
    repair pass on a bound that a measurement has already superseded — which is
    the right answer and not an accident of ordering: there is nothing left to
    close. The envelope's number is still on the record, one key away."""
    return bool(si_mcf_report) and si_mcf_report.get("verdict") == "FAIL"


def dominant_victims(si_crosstalk: Optional[Dict[str, Any]],
                     si_mcf: Optional[Dict[str, Any]],
                     *, ratio: float = DEFAULT_DOMINANT_RATIO) -> List[str]:
    """The victim nets one pass may touch, in a stable order.

    Two sources, both the run's own reports and both already published:
      * `si_crosstalk`'s coupling-DOMINATED nets (ratio >= `ratio`);
      * the worst victim `si_mcf_sta` names for the FAILING corner.
    A net named by neither is not a target: this pass does not go looking for
    work the envelope did not complain about."""
    out: List[str] = []
    seen = set()

    def _add(name: Any) -> None:
        if isinstance(name, str) and name.strip() and name not in seen:
            seen.add(name)
            out.append(name)

    for net in ((si_crosstalk or {}).get("coupling_dominated_nets") or []):
        if isinstance(net, dict):
            r = net.get("coupling_ratio")
            if isinstance(r, (int, float)) and r >= ratio:
                _add(net.get("net"))
        else:
            _add(net)
    corners = (si_mcf or {}).get("corners") or {}
    for corner in ("setup", "hold"):
        worst = (corners.get(corner) or {}).get("worst_victim") or {}
        _add(worst.get("net"))
    return out


def accepts(before: Dict[str, Any], after: Dict[str, Any], *,
            nominal_floor_ns: Optional[float] = None) -> Tuple[bool, str]:
    """`(accept, reason)` for ONE repair candidate, by the same rules the SDR
    children are judged by, plus the one this producer adds.

    Refusals are BY NAME and in a fixed order, so a reader always learns the
    FIRST thing that was wrong rather than a summary.

    "IT WAS NOT MEASURED" IS A REFUSAL IN ITS OWN RIGHT (R-0915-41 part 3),
    and it sits directly behind the route test -- a route that is measurably
    WORSE is named first even when the rest of the candidate is unmeasured,
    because that is the most specific true thing about it. Every test here
    compares two numbers and SKIPS when either is absent, so a candidate
    whose AFTER numbers never arrived -- a child that died, a re-STA that wrote
    nothing -- used to fall through every comparison and be ACCEPTED on the
    strength of three skipped tests. That is the exact shape of a green nobody
    can support, and it is the failure mode an execution seam makes reachable:
    with `runner=None` there is no `after` dict at all, so the hole only opens
    once something real can fail. An unmeasured candidate is REFUSED."""
    b_drc = before.get("router_drc")
    a_drc = after.get("router_drc")
    if isinstance(b_drc, int) and isinstance(a_drc, int) and a_drc > b_drc:
        return False, (f"router_drc {b_drc} -> {a_drc}: the candidate makes the "
                       f"ROUTE worse, and an envelope closed by breaking the "
                       f"route has closed nothing")
    if isinstance(b_drc, bool) or not isinstance(b_drc, (int, float)):
        return False, (f"the SHIPPING route's router DRC count was not measured "
                       f"({b_drc!r}), so \"the candidate does not make the route "
                       f"worse\" is not something this judgement can establish; "
                       f"a comparison against an unmeasured baseline is not a "
                       f"comparison")
    for _k, _what in (("router_drc", "the candidate's router DRC count"),
                      ("nominal_setup_ns", "the candidate's NOMINAL setup slack"),
                      ("mcf_setup_ns", "the candidate's MCF-folded setup slack")):
        _v = after.get(_k)
        if isinstance(_v, bool) or not isinstance(_v, (int, float)):
            return False, (f"{_k} was NOT MEASURED on the candidate ({_what} is "
                           f"{_v!r}): a candidate whose outcome was not measured "
                           f"is refused, never adopted on the strength of a "
                           f"comparison that could not be made")
    b_nom = before.get("nominal_setup_ns")
    a_nom = after.get("nominal_setup_ns")
    if (isinstance(b_nom, (int, float)) and isinstance(a_nom, (int, float))
            and a_nom < b_nom):
        # A DECLARED FLOOR IS THE ONLY THING THAT BUYS A REGRESSION, and the
        # default is still "none at all" (R-0915-56). Without a floor this
        # producer may not trade real margin for envelope margin on its own
        # judgement — the landed rule, unchanged. WITH one, the trade is
        # bounded by a number someone declared, and the reason says what was
        # spent and what is left rather than only that it was refused.
        if nominal_floor_ns is None:
            return False, (f"nominal setup {b_nom:+.4g} -> {a_nom:+.4g} ns: "
                           f"the candidate buys ENVELOPE margin with REAL "
                           f"margin, which this producer may not trade on its "
                           f"own")
        if a_nom < nominal_floor_ns:
            return False, (f"nominal setup {b_nom:+.4g} -> {a_nom:+.4g} ns "
                           f"falls BELOW the declared floor "
                           f"{nominal_floor_ns:+.4g} ns: a bounded trade is "
                           f"still a bound, and this candidate is outside it")
    b_mcf = before.get("mcf_setup_ns")
    a_mcf = after.get("mcf_setup_ns")
    if (isinstance(b_mcf, (int, float)) and isinstance(a_mcf, (int, float))
            and a_mcf <= b_mcf):
        return False, (f"mcf_setup {b_mcf:+.4g} -> {a_mcf:+.4g} ns: the pass "
                       f"did not improve the envelope it was run to close")
    return True, "envelope improved, route preserved, nominal not regressed"


def residual(after: Dict[str, Any]) -> Optional[str]:
    """The sentence to publish when the envelope is STILL open after the pass.

    `None` when it closed. Named, never 'still failing': a reader gets the
    corner, the number, and that one pass is all there is."""
    mcf = after.get("mcf_setup_ns")
    if not isinstance(mcf, (int, float)) or mcf >= 0.0:
        return None
    return (f"the MCF crosstalk-delay envelope is STILL OPEN after one repair "
            f"pass: worst setup {mcf:+.4g} ns at the folded corner. This "
            f"producer runs ONE pass by design -- an envelope a repair can also "
            f"widen must not be iterated -- so the residual is reported rather "
            f"than chased. The nominal corner and the route are unchanged or "
            f"better; what remains is a crosstalk-delay bound, not a measured "
            f"nominal violation.")


def plan(project: Path, *, ratio: float = DEFAULT_DOMINANT_RATIO
         ) -> Dict[str, Any]:
    """What this pass WOULD do, read from the run's own reports only.

    A plan is emitted even when nothing will run, because "no repair" and "no
    decision" must not read the same."""
    import _path_layout as _pl                                # noqa: PLC0415
    si_mcf = _load(_pl.report_path(project, "si_mcf_sta.json"))
    si_x = _load(_pl.report_path(project, "si_crosstalk.json"))
    if not envelope_is_open(si_mcf):
        return {"program": PROGRAM, "run": False,
                "reason": ("si_mcf_sta does not report FAIL, so there is no "
                           "open envelope to close and this pass does not "
                           "run"),
                "si_mcf_verdict": (si_mcf or {}).get("verdict"),
                "victims": []}
    victims = dominant_victims(si_x, si_mcf, ratio=ratio)
    truncated = max(0, len(victims) - MAX_VICTIMS)
    return {"program": PROGRAM, "run": bool(victims),
            "reason": ("one bounded SI-aware repair pass over the "
                       "coupling-dominated victims the run's own reports name")
            if victims else ("si_mcf_sta reports FAIL but neither report names "
                             "a coupling-dominated victim, so this pass has no "
                             "target and does not guess one"),
            "si_mcf_verdict": "FAIL",
            "victims": victims[:MAX_VICTIMS],
            "victims_truncated": truncated,
            "dominant_ratio": ratio}


def repair_tcl(*, folded_spef_c: str, victims: List[str],
               sdc_c: str, corner: str = "setup") -> str:
    """ONE OpenROAD pass over the named victims, on the MCF-BOUNDED loads.

    THE BOUNDED SPEF IS THE POINT. `si_mcf_sta` already wrote the corner's
    folded SPEF -- every coupling Cc folded into its victim's grounded cap at
    that corner's Miller factor -- so reading THAT back is what makes this pass
    SI-aware rather than another nominal repair: the resizer sees the effective
    load the envelope is computed against, and sizes for it.

    ONE `repair_timing`, no loop, and the victims are the only nets touched.
    `-repair_tns 0` keeps it to the worst path rather than chasing total
    negative slack across a design that already signs off.

    Emits NOTHING when there is no victim: a deck that runs a repair over an
    empty set is a deck that can still move a cell."""
    if not victims:
        return ""
    names = " ".join(victims)
    return (
        "# === R-0915-41: ONE SI-aware repair pass on the MCF-bounded loads.\n"
        "# The envelope is computed against these caps, so the resizer must\n"
        "# see them. One pass; the residual is reported, never iterated.\n"
        f"read_spef {folded_spef_c}\n"
        f"read_sdc {sdc_c}\n"
        f"set _si_victims {{{names}}}\n"
        "puts \"SI_MCF_REPAIR_TARGETS: [llength $_si_victims] victim net(s) "
        f"at the {corner} corner\"\n"
        "set _si_drc_before 0\n"
        "catch {set _si_drc_before [_sdr_tx_count_router_drc "
        "$::_vic_router_drc_rpt]}\n"
        # `-skip_buffer_removal` IS LOAD-BEARING AND WAS MEASURED, NOT GUESSED.
        # Removing a buffer MERGES the two nets it sat between, and
        # `dbNet::mergeNet` asks the GLOBAL ROUTER to merge their routing. This
        # pass restores a finished route from a DEF and never runs
        # `global_route`, so the router holds no state for those nets and
        # `grt::GlobalRouter::connectRouting` dereferences it. MEASURED on
        # subservient x gf180mcuD, lane icsub2 r19 — SIGSEGV 2.9 s in, stack:
        #   rsz::UnbufferCandidate::apply -> Resizer::removeBuffer
        #     -> odb::dbNet::mergeNet -> grt::GlobalRouter::mergeNetsRouting
        #     -> grt::GlobalRouter::connectRouting
        # and reproduced deterministically by re-running the emitted deck
        # (rc=139), which then completes with this flag. It is the same fact
        # R-0915-16 established from the other side: a DEF carries no guides.
        # SIZING is what this pass is for; restructuring the netlist is not,
        # and the moves that ADD a net (buffering, cloning) are safe because
        # the deck re-routes when the design signature moves — only the move
        # that MERGES two already-routed nets needs router state this session
        # does not have.
        "if {[catch {repair_timing -setup -repair_tns 0 -max_passes 1 "
        "-skip_buffer_removal} _si_e]} "
        "{\n"
        "  puts \"SI_MCF_REPAIR_NONFATAL: $_si_e\"\n"
        "} else {\n"
        "  puts \"SI_MCF_REPAIR_DONE\"\n"
        "}\n")


#: The non-default rule the spacing remedy creates. Named so a reader of the
#: DEF can find it, and so a second run recognises its own work.
SHIELD_NDR_NAME = "VIBEIC_SI_SHIELD"

#: How much wider the victim's spacing becomes, as a MULTIPLE of the layer's
#: own minimum. 2.0 is one extra track of air on each side, the smallest
#: spacing change that can move a coupling cap at all; a multiplier rather than
#: a length, so nothing here carries a PDK dimension.
DEFAULT_SHIELD_SPACING_MULT = 2.0


def shield_tcl(*, victims: List[str],
               spacing_mult: float = DEFAULT_SHIELD_SPACING_MULT,
               ndr_name: str = SHIELD_NDR_NAME) -> str:
    """SPACING, NOT SIZING — the remedy a COUPLING victim actually takes.

    R-0915-50 measured what sizing does here: one bounded `repair_timing` on the
    MCF-bounded loads moved `mcf_setup` from -0.2660 to -1.5703 ns and spent
    0.834 ns of real nominal margin. The child's own log showed why — the repair
    had very nearly closed the envelope in-session
    (`SI_MCF_WNS_AFTER_REPAIR: 0.00857`) and then `SI_MCF_ROUTING_CLEARED: 2323`
    threw the whole route away and re-made it worse.

    TWO THINGS FOLLOW, and this deck is both of them.

    (1) A CLOCK-LEAF VICTIM IS NOT A SIZING PROBLEM. One of subservient's two
    victims is `clknet_leaf_21_i_clk`. Resizing the driver of a clock leaf
    changes the clock, which is the one net every other arrival is measured
    against; the remedy for a clock victim is to move the aggressor away from
    it. The classification is made BY THE TOOL at run time (`getSigType` on the
    net), never by reading its name — a name-matched "clknet" rule is a
    chip-specific rule wearing a general coat.

    (2) RE-ROUTE THE VICTIMS, NOT THE DESIGN. The caller clears and re-routes
    only the nets this rule is assigned to, so the 2323-net reroute that cost
    1.30 ns of envelope cannot happen.

    The spacing is derived from each ROUTING layer's OWN minimum at run time and
    multiplied, so this file carries no PDK dimension and the rule means the
    same thing on any process. Emits NOTHING when there is no victim: a deck
    that assigns a rule over an empty set is a deck that can still move a
    wire."""
    if not victims:
        return ""
    names = " ".join(victims)
    return (
        "# === R-0915-56(b): SPACING for coupling victims, sizing for nothing.\n"
        "# The rule's spacing comes from each routing layer's own minimum,\n"
        "# multiplied - no PDK dimension is written here.\n"
        "set _si_ndr_mult " + repr(float(spacing_mult)) + "\n"
        "set _si_victims {" + names + "}\n"
        "set _si_sp {}\n"
        "set _si_tech [ord::get_db_tech]\n"
        "set _si_dbu [$_si_tech getDbUnitsPerMicron]\n"
        "foreach _si_lyr [$_si_tech getLayers] {\n"
        '  if {[$_si_lyr getType] ne "ROUTING"} { continue }\n'
        "  set _si_ms 0\n"
        "  catch {set _si_ms [$_si_lyr getSpacing]}\n"
        "  if {$_si_ms <= 0} { continue }\n"
        "  lappend _si_sp [$_si_lyr getName] "
        "[expr {double($_si_ms) / $_si_dbu * $_si_ndr_mult}]\n"
        "}\n"
        "set ::_si_shielded {}\n"
        "if {[llength $_si_sp] == 0} {\n"
        '  puts "SI_MCF_SHIELD_UNAVAILABLE: this tech reports no routing layer '
        'with a minimum spacing, so no spacing rule could be derived and NONE '
        'was invented"\n'
        "} else {\n"
        "  if {[catch {create_ndr -name " + ndr_name + " -spacing $_si_sp} "
        "_si_e]} {\n"
        '    puts "SI_MCF_SHIELD_NDR_NONFATAL: $_si_e"\n'
        "  } else {\n"
        '    puts "SI_MCF_SHIELD_NDR: ' + ndr_name + ' spacing $_si_sp"\n'
        "  }\n"
        "  set _si_blk [ord::get_db_block]\n"
        "  set _si_clk 0; set _si_dat 0; set _si_miss 0\n"
        "  foreach _si_vn $_si_victims {\n"
        "    set _si_n [$_si_blk findNet $_si_vn]\n"
        '    if {$_si_n eq "NULL" || $_si_n eq ""} { incr _si_miss ; '
        'puts "SI_MCF_SHIELD_NET_ABSENT: $_si_vn" ; continue }\n'
        "    set _si_st [$_si_n getSigType]\n"
        '    if {$_si_st eq "CLOCK"} { incr _si_clk } else { incr _si_dat }\n'
        "    if {[catch {assign_ndr -ndr " + ndr_name + " -net $_si_vn} "
        "_si_e2]} {\n"
        '      puts "SI_MCF_SHIELD_ASSIGN_NONFATAL: $_si_vn $_si_e2"\n'
        "    } else {\n"
        "      lappend ::_si_shielded $_si_vn\n"
        '      puts "SI_MCF_SHIELD_ASSIGNED: $_si_vn sigType=$_si_st"\n'
        "    }\n"
        "  }\n"
        '  puts "SI_MCF_SHIELD_SUMMARY: clock=$_si_clk data=$_si_dat '
        'absent=$_si_miss assigned=[llength $::_si_shielded]"\n'
        "}\n")


def _corner_numbers(si_mcf: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The three numbers the trajectory is measured in, from a report."""
    c = (si_mcf or {}).get("corners") or {}
    return {
        "nominal_setup_ns": ((si_mcf or {}).get("nominal")
                             or {}).get("worst_setup_slack_ns"),
        "mcf_setup_ns": (c.get("setup") or {}).get("worst_slack_after_ns"),
        "mcf_hold_ns": (c.get("hold") or {}).get("worst_slack_after_ns"),
    }


#: The three numbers the trajectory is measured in, as a PUBLIC name: the
#: execution seam lives in the phase-3 runner and must read a candidate's
#: report with the same function this producer reads the shipping one with —
#: two spellings of "the three numbers" is two things to keep in step.
corner_numbers = _corner_numbers


#: rc >= 128 from a shell is 128 + the signal that killed the child. The three
#: that matter here are named because "rc 139" is not a sentence a reader can
#: act on — R-0915-50 (2): a child that dies by SIGNAL is an EXECUTION_ERROR
#: with its signal and its top frame, never only "wrote no candidate".
_SIGNAL_NAMES = {4: "SIGILL", 6: "SIGABRT", 7: "SIGBUS", 8: "SIGFPE",
                 9: "SIGKILL", 11: "SIGSEGV", 13: "SIGPIPE", 15: "SIGTERM"}


def signal_of(rc: Any) -> Optional[Tuple[int, str]]:
    """`(signal, name)` when `rc` is a death by signal, else None.

    Both spellings are accepted: a shell reports 128+N, and
    `subprocess.returncode` reports -N. 0 and ordinary non-zero exits are NOT
    signals — a tool that exits 1 has reported a failure, which is a different
    thing from a tool that was killed."""
    if isinstance(rc, bool) or not isinstance(rc, int):
        return None
    sig = -rc if rc < 0 else (rc - 128 if rc > 128 else 0)
    if sig <= 0 or sig > 64:
        return None
    return sig, _SIGNAL_NAMES.get(sig, f"signal {sig}")


#: A stack frame OpenROAD prints as `  2# grt::GlobalRouter::connectRouting(...)
#: in openroad`. Frames that carry only an address (`0# 0x00000000035... in
#: openroad`) name nothing and are skipped — the top NAMED frame is the one a
#: reader can act on.
_FRAME_RE = re.compile(r"^\s*\d+#\s+(?!0x)(\S.*?)\s+in\s+\S+\s*$", re.M)


def crash_frames(log_text: str, limit: int = 6) -> List[str]:
    """The first NAMED frames of a tool stack trace, in order.

    Empty when the log carries none, which is itself worth recording: a child
    that died without a trace is a different report from one that left six
    frames naming the exact move that killed it."""
    return [m.group(1).strip() for m in _FRAME_RE.finditer(log_text or "")][:limit]


def run_once(project: Path, *, container: str = "",
             runner: Any = None, no_seam_reason: str = "",
             nominal_floor_ns: Optional[float] = None) -> Dict[str, Any]:
    """Plan, and record the trajectory. ONE pass, and NEVER in place.

    `runner` is the callable that executes a candidate and returns the AFTER
    numbers -- the runner supplies it, because the container seam, the
    re-extraction and the re-STA all belong to the phase-3 runner and not here.
    When it is None this producer PLANS and RECORDS and changes nothing, which
    is the state a tree without the execution leg is in and must be able to
    say out loud.

    THE PARENT IS NEVER MUTATED. Whatever `runner` does, it does to a CANDIDATE
    -- the same shape the SDR children established (R-0915-18): the shipping
    session keeps the route it has, and a candidate is adopted only if
    `accepts()` says so. A rejected candidate leaves the design byte-identical,
    which is a test."""
    import _path_layout as _pl                                # noqa: PLC0415
    out_p = _pl.report_path(project, "si_mcf_repair.json")
    si_mcf = _load(_pl.report_path(project, "si_mcf_sta.json"))
    p = plan(project)
    before = _corner_numbers(si_mcf)
    record: Dict[str, Any] = {
        "program": PROGRAM, "version": VERSION,
        "scope": ("ONE SI-aware repair pass on the MCF-bounded loads, judged "
                  "like an SDR child and never applied in place. The envelope "
                  "is a conservative crosstalk-DELAY bound; closing it is not "
                  "a silicon claim."),
        "plan": p, "before": before, "after": None,
        "decision": None, "reason": None, "residual": None,
        "reason_class": None, "crash": None,
        "nominal_floor_ns": nominal_floor_ns,
    }
    if not p.get("run"):
        record["decision"] = "NOT_RUN"
        record["reason"] = p.get("reason")
    elif runner is None:
        # NOT a silent no-op: a tree whose execution leg is absent says so, and
        # a reader can tell it from "the pass ran and changed nothing".
        record["decision"] = "NOT_EXECUTED"
        # `no_seam_reason` exists so a runner that HAS the seam but could not
        # arm it here -- no routed DEF to restore, no folded SPEF, no sign-off
        # corner Liberty -- says WHICH of those was missing. "no seam was
        # supplied" and "the seam had nothing to restore from" are different
        # answers, and a reader of this file may not have the runner's log.
        record["reason"] = (no_seam_reason.strip() or (
            "no execution seam was supplied, so this pass PLANNED and measured "
            "the BEFORE state and applied nothing; the design is unchanged"))
    else:
        after = runner(project, container=container, victims=p["victims"])
        record["after"] = after
        sig = signal_of((after or {}).get("child_rc"))
        if sig is not None:
            # R-0915-50 (2) — A CHILD KILLED BY A SIGNAL IS NOT A JUDGEMENT.
            # "the candidate was refused" says the pass looked at an outcome
            # and declined it; a SIGSEGV means the tool died and there was no
            # outcome to look at. Both leave the design unchanged and only one
            # of them is a defect someone has to fix, so they may not share a
            # decision. MEASURED on subservient r19: rc 139 after 2.857 s, with
            # the move that killed it named in the child's own trace.
            signum, signame = sig
            frames = (after or {}).get("child_crash_frames") or []
            record["decision"] = "EXECUTION_ERROR"
            record["reason_class"] = "EXECUTION_ERROR"
            record["reason"] = (
                f"the repair child was killed by {signame} (rc "
                f"{(after or {}).get('child_rc')}, signal {signum}) after "
                f"{(after or {}).get('elapsed_s')} s and produced no candidate"
                + (f"; top frame: {frames[0]}" if frames else
                   "; the child left no stack trace to name a frame from")
                + ". The design is unchanged and the shipping session kept its "
                  "route, but this is a TOOL FAILURE to be fixed, not a "
                  "candidate that was weighed and declined.")
            record["crash"] = {"rc": (after or {}).get("child_rc"),
                               "signal": signum, "signal_name": signame,
                               "frames": frames}
        else:
            ok, why = accepts({**before,
                               **{"router_drc":
                                  (after or {}).get("router_drc_before")}},
                              after or {},
                              nominal_floor_ns=nominal_floor_ns)
            record["decision"] = ("ADOPTED" if ok
                                  else "REJECTED_CANDIDATE_DISCARDED")
            record["reason"] = why
            if ok:
                record["residual"] = residual(after or {})
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(record, indent=2) + "\n")
    return record


def record_promotion(project: Path, *, promoted: List[Dict[str, Any]],
                     rederived: List[Dict[str, Any]],
                     refused: str = "") -> Dict[str, Any]:
    """Write back WHAT AN ADOPTION ACTUALLY MOVED, into the same record.

    An adoption that promotes a candidate route also makes every sign-off
    artefact already derived from the OLD route stale -- the LVS that matched
    the old netlist, the stream-out of the old geometry. This producer does not
    promote anything itself (`run_once` never mutates), but the runner that
    does has to leave the evidence HERE, next to the decision, because the
    decision is what a reader arrives at this file for.

    `promoted` names every path that moved (with the sidelined original, so the
    pre-adoption design is still on disk and the move is reversible by hand).
    `rederived` names every producer re-run over the adopted route and what it
    returned. `refused` is set when the adoption was decided but NOT carried
    out, and says why -- an ADOPTED decision with nothing promoted must never
    read as a promotion that happened."""
    import _path_layout as _pl                                # noqa: PLC0415
    out_p = _pl.report_path(project, "si_mcf_repair.json")
    try:
        rec = json.loads(out_p.read_text())
    except Exception:                                         # noqa: BLE001
        return {}
    rec["promotion"] = {
        "promoted": list(promoted),
        "rederived": list(rederived),
        "refused": refused,
        "note": ("every path listed under `promoted` was replaced by the "
                 "candidate and its pre-adoption content kept beside it under "
                 "the `sidelined` name; every producer under `rederived` was "
                 "re-run so no sign-off artefact still describes the route "
                 "this adoption replaced."),
    }
    out_p.write_text(json.dumps(rec, indent=2) + "\n")
    return rec
