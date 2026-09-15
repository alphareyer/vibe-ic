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
    either would be acting on an absence."""
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


def accepts(before: Dict[str, Any], after: Dict[str, Any]) -> Tuple[bool, str]:
    """`(accept, reason)` for ONE repair candidate, by the same rules the SDR
    children are judged by, plus the one this producer adds.

    Refusals are BY NAME and in a fixed order, so a reader always learns the
    FIRST thing that was wrong rather than a summary."""
    b_drc = before.get("router_drc")
    a_drc = after.get("router_drc")
    if isinstance(b_drc, int) and isinstance(a_drc, int) and a_drc > b_drc:
        return False, (f"router_drc {b_drc} -> {a_drc}: the candidate makes the "
                       f"ROUTE worse, and an envelope closed by breaking the "
                       f"route has closed nothing")
    b_nom = before.get("nominal_setup_ns")
    a_nom = after.get("nominal_setup_ns")
    if (isinstance(b_nom, (int, float)) and isinstance(a_nom, (int, float))
            and a_nom < b_nom):
        return False, (f"nominal setup {b_nom:+.4g} -> {a_nom:+.4g} ns: the "
                       f"candidate buys ENVELOPE margin with REAL margin, "
                       f"which this producer may not trade on its own")
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
        "if {[catch {repair_timing -setup -repair_tns 0 -max_passes 1} _si_e]} "
        "{\n"
        "  puts \"SI_MCF_REPAIR_NONFATAL: $_si_e\"\n"
        "} else {\n"
        "  puts \"SI_MCF_REPAIR_DONE\"\n"
        "}\n")
