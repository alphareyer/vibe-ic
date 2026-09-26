#!/usr/bin/env python3
"""htol_attestation_check.py — Step 43 reliability qualification (HTOL)
attestation gate (v2.3).

HTOL (High-Temperature Operating Life) is the long-duration stress
qual — distinct from the Step-42 burn-in infant-mortality screen. The
stress itself runs in external chambers; this gate verifies the
attestation artifact is SUBSTANTIVE and arithmetically self-consistent,
never fabricating reliability numbers:

  * required numeric fields: units_tested > 0, stress_hours > 0,
    failures >= 0 (integer);
  * device_hours: provided value must equal units_tested×stress_hours
    (±1%) — else derived and disclosed;
  * failures > 0 → FAIL (qualification stress demands zero fails;
    AEC-Q100-style 77/3-lot zero-failure criterion) — a documented
    re-qual is a NEW artifact, not a waived old one;
  * FIT: `fit_point_estimate` = max(failures, 0.5)/device_hours×1e9 is a
    POINT estimate, not a bound. The published bound is
    `fit_upper_bound` = χ²(CL, 2f+2) / (2 × device_hours × AF) × 1e9 at
    the `confidence_level` the attestation DECLARES (a fraction in
    (0, 1)). With 0 failures χ²(CL, 2)/2 = -ln(1-CL): 0.916 at 60 %,
    2.303 at 90 % -- 1.8x and 4.6x the 0.5 floor (migration 44). No CL
    is assumed: absent, the bound is NOT_MEASURED and says why. A FIT
    the artefact claims must not be lower than that bound.
    An `acceleration_factor` (>1) is applied when provided, else the FIT
    is labelled unaccelerated — disclosed, never silently assumed.
  * sample plan: a declared `sample_plan` {lots, units_per_lot} must
    reconcile with units_tested, and an attestation that claims a
    `qual_standard` must declare the plan it ran. No standard's plan is
    encoded here (none is available locally to cite).

Exit codes: 0 PASS, 1 FAIL, 2 htol_results.json absent → BLOCKED
(vibe-ic#220 — an unperformed reliability qual on shipped silicon is an
unanswered question, not a benign SKIP; the flow step is scoped by the
silicon-intake declaration, so it REACHES this gate and reports BLOCKED
rather than self-disabling on its own missing output).
chip-AGNOSTIC: numeric/structural rules only.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path


def _poisson_cdf(k: int, lam: float) -> float:
    term = total = math.exp(-lam)
    for i in range(1, k + 1):
        term *= lam / i
        total += term
    return total


def chi2_upper_half(cl: float, failures: int) -> float:
    """chi2(cl, 2f+2) / 2: the Poisson upper limit on the expected failure
    count at confidence `cl` given `failures` observed. Solved from the
    exact identity P(Poisson(lam) <= f) = 1 - cl by bisection, so no
    statistics library is needed and f = 0 reduces to -ln(1 - cl)."""
    lo, hi = 0.0, max(1.0, 2.0 * (failures + 1))
    while _poisson_cdf(failures, hi) > 1.0 - cl:
        hi *= 2.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if _poisson_cdf(failures, mid) > 1.0 - cl:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _declared_plan(d: dict):
    plan = d.get("sample_plan")
    if not isinstance(plan, dict):
        plan = {k: d[k] for k in ("lots", "units_per_lot") if k in d}
    return plan or None


def audit(project: Path) -> dict:
    src = project / "phase3" / "stage5_manufacturing" / "htol_results.json"
    if not src.is_file():
        # #220 — name the missing input and call it BLOCKED, never SKIP.
        # Silicon reached this step, so reliability qualification was owed.
        # "SKIP" reads as "nothing to do here"; an unperformed HTOL is not
        # nothing to do, it is an unanswered question. rc stays non-zero.
        return {"verdict": "BLOCKED", "rc": 2,
                "missing_input": str(src),
                "reason": ("phase3/stage5_manufacturing/htol_results.json "
                           "absent — HTOL was never run or never recorded, so "
                           "no FIT/reliability claim can be substantiated. "
                           "Produce the HTOL result, or record an explicit "
                           "waiver stating why this silicon ships without "
                           "reliability qualification.")}
    try:
        d = json.loads(src.read_text(errors="replace"))
    except (OSError, ValueError):
        return {"verdict": "FAIL", "rc": 1,
                "reason": "htol_results.json unparseable"}

    findings = []
    units = d.get("units_tested")
    hours = d.get("stress_hours", d.get("duration_hours"))
    fails = d.get("failures")
    if not (isinstance(units, int) and units > 0):
        findings.append("UNITS_MISSING: units_tested must be a positive int")
    if not (isinstance(hours, (int, float)) and hours > 0):
        findings.append("HOURS_MISSING: stress_hours must be > 0")
    if not (isinstance(fails, int) and fails >= 0):
        findings.append("FAILURES_MISSING: failures must be an int >= 0")
    if findings:
        return {"verdict": "FAIL", "rc": 1,
                "reason": "attestation not substantive: "
                          + "; ".join(findings)}

    derived_dh = units * float(hours)
    dh = d.get("device_hours")
    dh_note = None
    if isinstance(dh, (int, float)) and dh > 0:
        if abs(dh - derived_dh) > 0.01 * derived_dh:
            return {"verdict": "FAIL", "rc": 1,
                    "reason": (f"DEVICE_HOURS_INCONSISTENT: stated "
                               f"{dh} vs units×hours={derived_dh:.0f} "
                               f"(>1% off) — numbers must reconcile")}
    else:
        dh = derived_dh
        dh_note = "derived from units_tested × stress_hours"

    af = d.get("acceleration_factor")
    accelerated = isinstance(af, (int, float)) and af > 1
    eff_hours = dh * (af if accelerated else 1.0)
    # 0.5-failure floor: a POINT estimate kept for continuity. The bound
    # is `fit_upper_bound` below, at the declared confidence level.
    fit = (max(fails, 0.5) / eff_hours) * 1e9 if eff_hours > 0 else None

    cl = d.get("confidence_level")
    ub = None
    ub_status = "MEASURED"
    if cl is None:
        ub_status = ("NOT_MEASURED: the attestation declares no "
                     "confidence_level, and none is assumed")
    elif not (isinstance(cl, (int, float)) and not isinstance(cl, bool)
              and 0.0 < cl < 1.0):
        return {"verdict": "FAIL", "rc": 1, "reason": (
            f"CONFIDENCE_LEVEL_INVALID: confidence_level={cl!r} must be a "
            f"fraction in (0, 1)")}
    elif eff_hours > 0:
        ub = chi2_upper_half(float(cl), fails) / eff_hours * 1e9

    claimed = d.get("fit", d.get("fit_claimed"))
    if claimed is not None:
        if not isinstance(claimed, (int, float)) or isinstance(claimed, bool):
            return {"verdict": "FAIL", "rc": 1, "reason": (
                f"FIT_CLAIM_INVALID: fit={claimed!r} is not a number")}
        if ub is None:
            return {"verdict": "FAIL", "rc": 1, "reason": (
                f"FIT_CLAIM_WITHOUT_CONFIDENCE: the attestation claims "
                f"FIT={claimed} but declares no confidence_level, so the "
                f"claim cannot be checked against its chi-square bound")}
        if claimed < ub * 0.99:
            return {"verdict": "FAIL", "rc": 1, "reason": (
                f"FIT_CLAIM_UNDERSTATED: claimed FIT={claimed} is below the "
                f"chi-square upper bound {ub:.3f} at CL={cl} for {fails} "
                f"failure(s) over {eff_hours:.0f} effective device-hours")}

    plan = _declared_plan(d)
    if plan is not None:
        lots, per_lot = plan.get("lots"), plan.get("units_per_lot")
        if not (isinstance(lots, int) and lots > 0 and
                isinstance(per_lot, int) and per_lot > 0):
            return {"verdict": "FAIL", "rc": 1, "reason": (
                f"SAMPLE_PLAN_INVALID: sample_plan={plan!r} needs positive "
                f"integer lots and units_per_lot")}
        if lots * per_lot != units:
            return {"verdict": "FAIL", "rc": 1, "reason": (
                f"SAMPLE_PLAN_INCONSISTENT: declared plan {lots} lot(s) x "
                f"{per_lot} unit(s) = {lots * per_lot}, but units_tested="
                f"{units}")}
    elif d.get("qual_standard"):
        return {"verdict": "FAIL", "rc": 1, "reason": (
            f"SAMPLE_PLAN_UNDECLARED: the attestation claims qual_standard="
            f"{d.get('qual_standard')!r} but declares no sample_plan "
            f"{{lots, units_per_lot}} to hold it to")}

    rep = {
        "units_tested": units, "stress_hours": hours, "failures": fails,
        "confidence_level": cl,
        "fit_upper_bound": round(ub, 3) if ub is not None else None,
        "fit_upper_bound_status": ub_status,
        "sample_plan": plan,
        "device_hours": dh, "device_hours_note": dh_note,
        "acceleration_factor": af if accelerated else None,
        "fit_point_estimate": round(fit, 3) if fit is not None else None,
        "fit_basis": ("accelerated (AF applied)" if accelerated else
                      "UNACCELERATED raw stress-hours — apply the "
                      "Arrhenius AF for use-condition FIT"),
    }
    if fails > 0:
        rep.update(verdict="FAIL", rc=1, reason=(
            f"{fails} failure(s) during HTOL — qualification requires "
            f"zero failures; root-cause and re-qual with a new lot"))
    else:
        rep.update(verdict="PASS", rc=0, reason=(
            f"0 failures over {dh:.0f} device-hours"
            + (f" (AF={af})" if accelerated else "")))
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("project_dir", type=Path)
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)
    if not args.project_dir.is_dir():
        print(f"ERROR: not a directory: {args.project_dir}", file=sys.stderr)
        return 1
    rep = audit(args.project_dir.resolve())
    rc = rep.pop("rc")
    rep = {"program": "htol_attestation_check", "version": "1.0.0", **rep}
    out = json.dumps(rep, indent=2, ensure_ascii=False)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(out)
    print(out)
    return rc


if __name__ == "__main__":
    sys.exit(main())
