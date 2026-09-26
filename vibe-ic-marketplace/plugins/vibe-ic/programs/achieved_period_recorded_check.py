#!/usr/bin/env python3
"""achieved_period_recorded_check.py — "asked" and "reached" must both be on disk.

ENFORCEMENT: advisory

WHY THIS GATE EXISTS (vibe-ic#1097, S8)
=======================================
The period a design is ASKED for reaches the sign-off SDC through a four-tier
precedence walk that `l8_sta_clock_period_design_owned_check` documents:

    1. L8.clock_mhz
    2. sdc_gen._clock_mhz_from_l8_domains(L8)
    3. sdc_constraints.primary_clock(project)   (the design's OWN staged SDC)
    4. sdc_gen._DEFAULT_MHZ  ................... FABRICATED

Every one of those is a number somebody REQUESTED. None is a measurement. The
period the design actually REACHES is the only measured member of the set, and
until it is written down beside the asked one, "did we get what we asked for"
is a question that can only be answered by reading a log.

`sta_achievable_fmax_report.achievable_from_slack` has computed it since v1.4;
`phase3_one_shot_runner` wired it. What was missing is that the wiring fired
only when `setup_wns < 0`, so the measurement was kept for exactly the runs
nobody needs convincing about.

MEASURED at `f9c13443` over this repo's published corpus:

    run roots that reached post-route STA          13
    of those carrying `achievable_fmax.json`        2   (both FAILING runs)

    caravel_user_project   asked 25.0 ns   reached  7.81 ns   headroom 17.19
    spm/v1.10.18_sky130A   asked 10.0 ns   reached  4.76 ns   headroom  5.24
    edge_llm_accel         asked 10.0 ns   reached  8.92 ns   headroom  1.08

Three designs shipped with the slack on disk and the achieved period nowhere.

WHAT THIS GATE ASSERTS, AND WHAT IT DELIBERATELY DOES NOT
=========================================================
It asserts ONE implication: **if the run produced a post-route setup slack, the
achieved period is recorded.** It does not judge the number. A design with
17 ns of headroom is not thereby wrong — it may be pad-limited, or waiting on a
faster PDK corner, or simply asked for a period with margin on purpose. The
defect this gate names is the SILENCE, not the gap.

It is ADVISORY on purpose, and the reason is the same one that keeps this repo's
NOT_CHECKED tiers advisory: a run whose STA never produced a slack has nothing
to record and must not be failed for it, and telling those two states apart is
the whole content of the check. VACUOUS_PASS (rc 2) when no post-route setup
slack exists; rc 1 only when a slack EXISTS and the achieved period does not.

§4.05: this gate reads the run's OWN artefacts. It never reads a golden, never
writes an SDC, and never proposes a period — `relaxation_applied` staying False
is asserted here as well, so a future edit that turned the report into a
relaxation would redden this gate rather than pass through it.

STEP 23 ON THE TOOL (F15)
=========================
When step 23 runs `librelane` or `dual` (`phase3/librelane_switch.json`), the
slack is the TOOL's: the worst `worst slack max` over every corner
`OpenROAD.STAPostPNR` declared, read from each corner's own
`vibeic_signoff.rpt` through `librelane_signoff.step23_tool_arm` (each file
bound to the sha256 the tool wrote, each timed view still current). A corner
report that is missing, unbound, stale or carries no setup slack REFUSES
(rc 1, verdict REFUSED): it is never read as "no slack" and never replaced by
the direct deck's report. The record's `worst_setup_slack_ns` must then be
that slack (to the two decimals it prints): a reached period computed from
any other number is not the one this run signed off.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))  # so the sibling import below resolves however this is invoked
from _atomic_artefact import write_text as atomic_write_text  # vibe-ic#1082 (helper from PR #1094)
import librelane_signoff as _ls  # noqa: E402 — step 23 on the tool (F15)
from librelane_contract import Refusal  # noqa: E402

try:
    import _vacuous_exit as _vx
except Exception:                                          # pragma: no cover
    _vx = None

RC_PASS, RC_FAIL, RC_VACUOUS = 0, 1, 2

#: Where the runner writes the measurement.
ACHIEVED_REL = "reports/phase3/achievable_fmax.json"

#: Post-route STA reports that carry a worst setup slack, most-canonical first.
#: SPEF-based is canonical when present (#527), so it is asked first.
_STA_RPTS = (
    "reports/phase3/sta_spef_based.rpt",
    "reports/phase3/sta_spef_multicorner.rpt",
    "reports/phase3/sta_mcorner_ocv.rpt",
    "phase3/reports/sta.rpt",
)

#: `worst slack max <float>` — OpenSTA's own spelling, and the ONLY one of the
#: two that can express headroom.
#:
#: WNS IS NOT AN ALTERNATIVE SPELLING, and treating it as one is a domain error
#: this check made and had to be corrected. WNS is the worst NEGATIVE slack, so
#: by convention it is clamped at 0 when nothing violates. Measured on the
#: published spm cell, the same report carries both:
#:
#:     wns max          0.00      <- clamped: says "nothing violates"
#:     worst slack max  5.24      <- the measurement: 5.24 ns of headroom
#:
#: Reading `wns` would make `achievable_period = asked - 0 = asked` on EVERY
#: passing design — the artefact would exist, be perfectly self-consistent, and
#: report that every design exactly reaches what it asked for. That is a worse
#: outcome than the silence this gate exists to remove, so `wns` is accepted
#: ONLY as a fallback and ONLY when it is negative, where the two coincide.
_WORST_SLACK_RE = re.compile(
    r"^\s*worst\s+slack\s+max\s+([-+]?\d+(?:\.\d+)?)\s*$",
    re.MULTILINE | re.IGNORECASE)
_WNS_RE = re.compile(
    r"^\s*wns\s+max\s+([-+]?\d+(?:\.\d+)?)\s*$",
    re.MULTILINE | re.IGNORECASE)


def measured_setup_slack(project: Path):
    """``(slack_ns, source_rel)`` from the run's own STA, or ``(None, None)``."""
    for rel in _STA_RPTS:
        p = project / rel
        if not p.is_file():
            continue
        try:
            text = p.read_text(errors="replace")
        except OSError:                                    # pragma: no cover
            continue
        m = _WORST_SLACK_RE.search(text)
        if m:
            try:
                return float(m.group(1)), rel
            except ValueError:                             # pragma: no cover
                continue
        # Fallback, negative only — see the note on _WNS_RE.
        m = _WNS_RE.search(text)
        if m:
            try:
                v = float(m.group(1))
            except ValueError:                             # pragma: no cover
                continue
            if v < 0:
                return v, rel
    return None, None


#: The achieved-period record prints its slack to two decimals.
TOOL_SLACK_TOLERANCE_NS = 0.006


def tool_setup_slack(arm: dict):
    """``(slack_ns, source, per_corner)``: the worst setup slack over every
    corner the tool declared, each from that corner's own report."""
    per_corner = {}
    for corner, row in arm["corners"].items():
        found = _WORST_SLACK_RE.search(row["files"][_ls.CORNER_REPORT]["text"])
        if not found:
            raise Refusal("LL_STA_CORNER_SLACK_MISSING",
                          row["files"][_ls.CORNER_REPORT]["path"])
        per_corner[corner] = float(found.group(1))
    worst = min(per_corner, key=per_corner.get)
    return (per_corner[worst], f"{worst}/{_ls.CORNER_REPORT} (STAPostPNR)",
            per_corner)


def evaluate(project: Path) -> dict:
    """The whole verdict, as data, so the test can assert on it directly."""
    try:
        arm = _ls.step23_tool_arm(project)
        tool = tool_setup_slack(arm) if arm is not None else None
    except Refusal as exc:
        return {
            "program": "achieved_period_recorded_check",
            "verdict": "REFUSED", "rc": RC_FAIL, "refusal": exc.code,
            "setup_slack_ns": None, "slack_source": None, "findings": [],
            "note": (f"step 23 runs on the tool and its sign-off cannot be "
                     f"read: {exc} -- a refusal, never a vacuous pass"),
        }
    if tool is not None:
        slack, source, per_corner = tool
    else:
        slack, source = measured_setup_slack(project)
    rec = project / ACHIEVED_REL
    present = rec.is_file()
    payload = None
    if present:
        try:
            payload = json.loads(rec.read_text())
        except Exception:
            payload = None

    findings = []
    if slack is None:
        return {
            "program": "achieved_period_recorded_check",
            "verdict": "VACUOUS_PASS",
            "rc": RC_VACUOUS,
            "setup_slack_ns": None,
            "slack_source": None,
            "achieved_recorded": present,
            "findings": [],
            "note": ("no post-route setup slack on disk, so there is nothing to "
                     "record an achieved period AGAINST — this is the absence of "
                     "a measurement, not a missing disclosure"),
        }

    if not present:
        findings.append({
            "rule": "ACHIEVED_PERIOD_NOT_RECORDED",
            "severity": "ERROR",
            "message": (
                f"post-route STA measured a worst setup slack of {slack} ns in "
                f"{source}, but {ACHIEVED_REL} does not exist. The period this "
                f"design was ASKED for is on disk and the period it REACHED is "
                f"not, so the two cannot be diffed (vibe-ic#1097 S8)."),
        })
    elif not isinstance(payload, dict):
        findings.append({
            "rule": "ACHIEVED_PERIOD_UNREADABLE",
            "severity": "ERROR",
            "message": f"{ACHIEVED_REL} exists but is not a readable JSON object",
        })
    else:
        for key in ("spec_period_ns", "achievable_period_ns",
                    "worst_setup_slack_ns"):
            if payload.get(key) is None:
                findings.append({
                    "rule": "ACHIEVED_PERIOD_INCOMPLETE",
                    "severity": "ERROR",
                    "message": (
                        f"{ACHIEVED_REL} omits `{key}` — a record that does not "
                        f"carry both the asked and the reached number answers "
                        f"neither half of the question"),
                })
        # The honesty invariant, asserted rather than trusted: this artefact is
        # a MEASUREMENT. If it ever starts reporting that it relaxed something,
        # it has become the ORFS `update_ok` shape vibe-ic#1083 refuses.
        if payload.get("relaxation_applied") is not False:
            findings.append({
                "rule": "ACHIEVED_PERIOD_IS_NOT_A_RELAXATION",
                "severity": "ERROR",
                "message": (
                    "`relaxation_applied` is not False — the achieved-period "
                    "artefact must never become a clock relaxation (#1083 "
                    "records ORFS's `update_ok`/`--failing` as NOT adopted)"),
            })
        recorded = payload.get("worst_setup_slack_ns")
        if (tool is not None and isinstance(recorded, (int, float))
                and abs(float(recorded) - slack) > TOOL_SLACK_TOLERANCE_NS):
            findings.append({
                "rule": "ACHIEVED_PERIOD_NOT_FROM_SIGNOFF",
                "severity": "ERROR",
                "message": (
                    f"{ACHIEVED_REL} records a worst setup slack of {recorded} "
                    f"ns but the step-23 sign-off (STAPostPNR) measured {slack} "
                    f"ns at {source}: the reached period is not the one this "
                    f"run signed off"),
            })

    return {
        "program": "achieved_period_recorded_check",
        "verdict": "FAIL" if findings else "PASS",
        "rc": RC_FAIL if findings else RC_PASS,
        "setup_slack_ns": slack,
        "slack_source": source,
        "achieved_recorded": present,
        "asked_period_ns": (payload or {}).get("spec_period_ns"),
        "reached_period_ns": (payload or {}).get("achievable_period_ns"),
        "findings": findings,
        **({"basis": _ls.tool_arm_basis(arm), "tool_setup_slack_ns": per_corner}
           if tool is not None else {}),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project", nargs="?", default=".")
    ap.add_argument("--json", help="write the structured report to this path")
    ns = ap.parse_args(argv)

    rep = evaluate(Path(ns.project).resolve())
    if ns.json:
        out = Path(ns.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(out, json.dumps(rep, indent=2) + "\n")

    print(json.dumps(rep, indent=2))
    if rep["rc"] == RC_VACUOUS:
        if _vx is not None:
            _vx.announce_vacuous("achieved_period_recorded_check", rep["note"])
        return RC_VACUOUS
    if rep["rc"] == RC_PASS:
        print(f"[PASS] asked {rep['asked_period_ns']} ns -> reached "
              f"{rep['reached_period_ns']} ns "
              f"(setup slack {rep['setup_slack_ns']} ns, {rep['slack_source']})")
    return rep["rc"]


if __name__ == "__main__":                                 # pragma: no cover
    sys.exit(main())
