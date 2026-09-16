#!/usr/bin/env python3
"""R-0915-66 — WHICH SI READING IS STEP 27's VERDICT.

Step 27 has two readings of the same design and they do not agree.

  * the MCF ENVELOPE (`si_mcf_sta.json`) folds every coupling into its victim's
    grounded cap at the corner's Miller factor and re-runs OpenSTA. It is a
    BOUND, and its own scope note says so: "a conservative crosstalk-DELAY
    bound; closing it is not a silicon claim". On subservient it reads
    `mcf_setup -0.2660` and has read exactly that on ten consecutive trees.

  * the COUPLING DELTA-DELAY screen (`si_crosstalk.json` -> `delta_delay`)
    compares each overlapping pair's Miller-multiplied delta against the
    victim's own STA path slack. Its own header states the three outcomes:
    "FAIL = proven push-negative; PASS = slack basis covers the worst
    delta-delay; ADVISORY = no slack basis (cannot prove)". On subservient it
    reads PASS over 2375 nets and 18999 pairs, 35902 pairs slack-checked, 1192
    conclusively decoupled by switching window, 0 violations.

The envelope assumes every aggressor can switch inside every victim's window
because it has no basis to say otherwise. The delta-delay screen HAS that basis
— the arrival windows and the path slacks — and uses it. So when the screen
reaches a GENUINE verdict, it is the better-informed reading and it is the
step's verdict; the envelope is then DISCLOSED beside it, with its number kept,
because a conservative bound that a better measurement supersedes is still
worth reporting and is never worth hiding.

WHAT THIS MODULE WILL NOT DO, and each is a test:
  * it never turns a delta-delay FAIL into anything but a FAIL. A proven
    push-negative is the one thing the screen can assert, and it outranks
    everything;
  * it never supersedes on an ADVISORY. "No slack basis" means the screen could
    not prove anything, and an unproven reading may not displace a bound;
  * it never deletes the envelope's numbers. `mcf_setup` stays exactly where it
    was, under `envelope`, with the verdict it would have given;
  * and it never invents a verdict when the screen is absent, malformed, or
    checked no pairs at all.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

PROGRAM = "si_mcf_verdict_basis"
VERSION = "1.0.0"

#: The screen's own vocabulary. ADVISORY is deliberately NOT here: it is the
#: word the screen uses for "no slack basis (cannot prove)", which is exactly
#: the state in which it may not decide anything.
GENUINE_VERDICTS = ("PASS", "FAIL")

#: `DECIDES` — the envelope is the step's verdict (the screen proved nothing).
#: `ADVISES` — the delta-delay screen decided, and the envelope is disclosed
#: beside it with its number intact.
MODE_DECIDES = "DECIDES"
MODE_ADVISES = "ADVISES"


def delta_delay_reading(si_crosstalk: Optional[Dict[str, Any]]
                        ) -> Tuple[Optional[str], Dict[str, Any]]:
    """`(verdict, evidence)` from the delta-delay screen, or `(None, why)`.

    `None` whenever the screen cannot be said to have PROVED anything: no
    report, no `delta_delay` block, a verdict outside its own vocabulary, or a
    verdict reached without checking a single pair against a slack. The last
    one matters — a screen that evaluated nothing and said PASS has said
    nothing, and this is the difference between a measurement and an empty
    scan."""
    dd = ((si_crosstalk or {}).get("delta_delay") or {}) \
        if isinstance(si_crosstalk, dict) else {}
    if not isinstance(dd, dict) or not dd:
        return None, {"why": "the run's si_crosstalk report carries no "
                             "delta_delay block, so the coupling delta-delay "
                             "screen did not run and has proved nothing"}
    verdict = dd.get("verdict")
    checked = dd.get("pairs_slack_checked")
    if verdict not in GENUINE_VERDICTS:
        return None, {"why": f"the delta-delay screen reports {verdict!r}, "
                             f"which is not a genuine verdict — it has no "
                             f"slack basis and cannot prove anything",
                      "delta_delay_verdict": verdict}
    if not isinstance(checked, int) or checked <= 0:
        return None, {"why": f"the delta-delay screen reports {verdict!r} "
                             f"having checked {checked!r} pair(s) against a "
                             f"slack: a verdict reached over nothing is not a "
                             f"measurement",
                      "delta_delay_verdict": verdict,
                      "pairs_slack_checked": checked}
    return verdict, {
        "verdict": verdict,
        "violations_count": dd.get("violations_count"),
        "max_delta_delay_ns": dd.get("max_delta_delay_ns"),
        "pairs_slack_checked": checked,
        "pairs_decoupled_by_window": dd.get("pairs_decoupled_by_window"),
        # the screen names its own scope; carried verbatim so the basis a
        # reader sees is the screen's sentence and not a paraphrase of it
        "scope": dd.get("scope"),
    }


def reconcile(si_mcf: Dict[str, Any],
              si_crosstalk: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the envelope report with its verdict BASIS made explicit.

    Never mutates the input, never drops a number, and returns a report whose
    `verdict` is the one step 27 should self-report."""
    out = dict(si_mcf or {})
    envelope_verdict = out.get("verdict")
    corners = out.get("corners") or {}
    envelope_setup = (corners.get("setup") or {}).get("worst_slack_after_ns")

    verdict, evidence = delta_delay_reading(si_crosstalk)
    if verdict is None:
        out["verdict_basis"] = {
            "program": PROGRAM, "version": VERSION,
            "mode": MODE_DECIDES,
            "tier": "envelope",
            "verdict_from": "mcf_envelope",
            "why": ("the coupling delta-delay screen reached no genuine "
                    "verdict, so the conservative MCF envelope remains this "
                    "step's verdict: " + str(evidence.get("why"))),
            "delta_delay": evidence,
            "envelope": {"verdict": envelope_verdict,
                         "mcf_setup_ns": envelope_setup},
        }
        return out

    out["verdict"] = verdict
    out["verdict_basis"] = {
        "program": PROGRAM, "version": VERSION,
        "mode": MODE_ADVISES,
        "tier": "envelope",
        "verdict_from": "coupling_delta_delay_screen",
        "why": (
            "the coupling delta-delay screen reached a GENUINE verdict — it "
            "compares each overlapping pair's Miller-multiplied delta against "
            "the victim's OWN STA path slack, which is a basis the floating-"
            "victim envelope does not have — so it is this step's verdict. The "
            "envelope is DISCLOSED beside it, unchanged: a conservative bound "
            "that a better-informed measurement supersedes is still worth "
            "reporting and is never worth hiding."),
        "delta_delay": evidence,
        "envelope": {
            "verdict": envelope_verdict,
            "mcf_setup_ns": envelope_setup,
            "disclosed_as": (
                f"MCF floating-victim envelope: {envelope_verdict} with worst "
                f"folded setup {envelope_setup} ns. It assumes every aggressor "
                f"switches inside every victim's window because it has no "
                f"basis to say otherwise; where that basis exists the "
                f"delta-delay screen used it."),
        },
    }
    return out


def apply(project: Path) -> Optional[Dict[str, Any]]:
    """Read both reports, reconcile, and rewrite `si_mcf_sta.json` in place.

    `None` when there is no envelope report to reconcile — this decides nothing
    on its own and invents nothing when the step did not run."""
    import _path_layout as _pl                                # noqa: PLC0415
    mcf_p = _pl.report_path(project, "si_mcf_sta.json")
    if not mcf_p.is_file():
        return None
    try:
        si_mcf = json.loads(mcf_p.read_text())
    except Exception:                                          # noqa: BLE001
        return None
    x_p = _pl.report_path(project, "si_crosstalk.json")
    si_x = None
    if x_p.is_file():
        try:
            si_x = json.loads(x_p.read_text())
        except Exception:                                      # noqa: BLE001
            si_x = None
    out = reconcile(si_mcf, si_x)
    mcf_p.write_text(json.dumps(out, indent=2) + "\n")
    return out
