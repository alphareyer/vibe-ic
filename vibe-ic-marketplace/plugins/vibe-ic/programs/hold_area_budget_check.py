#!/usr/bin/env python3
"""
hold_area_budget_check.py — enforce the hold-buffer area-budget guardrail.

From `skills/hold-fix/SKILL.md` "Constraints and Guardrails" #2:
  "Area budget: hold buffers should not exceed 5% of total cell area. If
   exceeded, investigate why — possible CTS imbalance."

This is a numeric threshold check on the area added by hold fixing relative to
the total cell area:

    overhead_pct = 100 * hold_buffer_area / total_cell_area
    overhead_pct <= 5.0   => PASS  (within budget)
    overhead_pct  > 5.0   => FAIL  (budget exceeded — investigate CTS imbalance)

The hold-buffer area can be supplied directly, OR derived from a before/after
total-area pair (after_total - before_total). The percentage is taken against
the TOTAL post-fix cell area (the SKILL says "5% of total cell area").

HARD honesty rules (no vacuous PASS):
  - Missing / non-finite / non-positive total area  => FAIL (rc=1). A 0 or
    absent denominator cannot be certified within budget.
  - Negative hold-buffer area (a fix that REMOVED area) => FAIL (rc=1): hold
    fixing only ADDS cells; a negative delta means the wrong baseline.
  - hold_buffer_area == 0 (nothing inserted) is reported as PASS only when a
    total area is present AND the caller explicitly allows a no-op
    (`allow_zero=True`); by default a 0-overhead claim with no inserted area is
    a FAIL because a hold-fix step that inserted nothing did no work (mirrors
    hold_closure_check). Use --allow-zero for the "design had no hold
    violations" path.

Input forms (JSON or CLI flags):
  {"hold_buffer_area": 1234.5, "total_cell_area": 100000.0}
  {"before_total_area": 98765.5, "after_total_area": 100000.0}   # delta derived

PROJECT-DIRECTORY MODE
----------------------
The LibreLane CTS/hold adapter supplies the resizer's current before/after
standard-cell State metrics. The delta remains an upper bound including setup
and hold. Its source hashes and values are checked again in project mode.
Missing evidence is NOT_MEASURED (rc=4), never a vacuous partial PASS beside
the hold gate. Explicit JSON/flags retain the unchanged numeric evaluator.

chip-AGNOSTIC: the only constant is the SKILL's universal 5% guardrail; no
PDK / cell / design literal is hard-coded.

Usage
-----
    python3 hold_area_budget_check.py <input.json> [--json <out>] [--allow-zero]
    python3 hold_area_budget_check.py <project_dir> [--json <out>]
    python3 hold_area_budget_check.py --hold-buffer-area 1200 \\
        --total-cell-area 100000 [--json <out>]

Exit codes
----------
    0 — PASS (within budget)
    1 — FAIL (budget exceeded, or an input that exists but cannot be certified)
    4 — NOT_MEASURED: project mode lacks current bound evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Optional, Tuple


_TOOL = "hold_area_budget_check"
AREA_BUDGET_PCT = 5.0   # SKILL guardrail: hold buffers <= 5% of total cell area


def _finite_positive(x) -> bool:
    return (isinstance(x, (int, float)) and not isinstance(x, bool)
            and math.isfinite(x) and x > 0)


def evaluate(hold_buffer_area: Optional[float],
             total_cell_area: Optional[float],
             before_total_area: Optional[float] = None,
             after_total_area: Optional[float] = None,
             allow_zero: bool = False) -> Tuple[str, int, dict]:
    report = {"tool": _TOOL, "budget_pct": AREA_BUDGET_PCT}

    # Derive hold_buffer_area / total from a before/after pair if given.
    if hold_buffer_area is None and \
            before_total_area is not None and after_total_area is not None:
        if not (_finite_positive(before_total_area) and
                _finite_positive(after_total_area)):
            report.update(verdict="FAIL", reason="BEFORE_AFTER_NOT_POSITIVE",
                          message="before/after total area must be finite "
                                  "positive numbers")
            return "FAIL", 1, report
        hold_buffer_area = after_total_area - before_total_area
        if total_cell_area is None:
            total_cell_area = after_total_area

    report["hold_buffer_area"] = hold_buffer_area
    report["total_cell_area"] = total_cell_area

    # Denominator must be finite positive.
    if not _finite_positive(total_cell_area):
        report.update(verdict="FAIL", reason="TOTAL_AREA_MISSING_OR_ZERO",
                      message="total cell area absent / non-positive — cannot "
                              "certify hold-buffer area within budget "
                              "(missing input is an honest FAIL)")
        return "FAIL", 1, report

    if hold_buffer_area is None or not isinstance(hold_buffer_area, (int, float)) \
            or not math.isfinite(hold_buffer_area):
        report.update(verdict="FAIL", reason="HOLD_AREA_MISSING",
                      message="hold-buffer area absent / non-finite — supply it "
                              "directly or as before/after totals")
        return "FAIL", 1, report

    if hold_buffer_area < 0:
        report.update(verdict="FAIL", reason="NEGATIVE_HOLD_AREA",
                      message=f"hold-buffer area {hold_buffer_area:g} is "
                              "negative — hold fixing only ADDS cells; check "
                              "the before/after baseline")
        return "FAIL", 1, report

    if hold_buffer_area == 0:
        if allow_zero:
            report.update(verdict="PASS", reason="NO_HOLD_BUFFERS_NEEDED",
                          overhead_pct=0.0,
                          message="no hold buffers inserted (design had no hold "
                                  "violations) — within budget by --allow-zero")
            return "PASS", 0, report
        report.update(verdict="FAIL", reason="ZERO_HOLD_AREA_NO_WORK",
                      overhead_pct=0.0,
                      message="hold-buffer area is 0 — the hold-fix step "
                              "inserted nothing (use --allow-zero only if the "
                              "design genuinely had no hold violations)")
        return "FAIL", 1, report

    overhead_pct = 100.0 * hold_buffer_area / total_cell_area
    report["overhead_pct"] = overhead_pct

    if overhead_pct > AREA_BUDGET_PCT:
        report.update(verdict="FAIL", reason="AREA_BUDGET_EXCEEDED",
                      message=f"hold-buffer overhead {overhead_pct:.3f}% exceeds "
                              f"the {AREA_BUDGET_PCT:g}% guardrail — investigate "
                              f"(possible CTS imbalance / over-skewed clock tree)")
        return "FAIL", 1, report

    report.update(verdict="PASS", reason="WITHIN_BUDGET",
                  message=f"hold-buffer overhead {overhead_pct:.3f}% within the "
                          f"{AREA_BUDGET_PCT:g}% guardrail")
    return "PASS", 0, report


def _load_input(args) -> Tuple[Optional[float], Optional[float],
                               Optional[float], Optional[float]]:
    hba = args.hold_buffer_area
    tca = args.total_cell_area
    bta = args.before_total_area
    ata = args.after_total_area
    if args.input_json:
        p = Path(args.input_json)
        if not p.is_file():
            return None, None, None, None  # honest FAIL downstream
        try:
            data = json.loads(p.read_text(errors="replace"))
        except (json.JSONDecodeError, OSError):
            return None, None, None, None
        if isinstance(data, dict):
            hba = data.get("hold_buffer_area", hba)
            tca = data.get("total_cell_area", tca)
            bta = data.get("before_total_area", bta)
            ata = data.get("after_total_area", ata)
    return hba, tca, bta, ata


#: Where a producer WOULD write this gate's input, most canonical first. Probed
#: in project-directory mode; none of them exists in the corpus today, which is
#: the finding this mode reports.
_PRODUCER_CANDIDATES = (
    "reports/phase3/pnr/hold_area.json",
    "reports/phase3/hold_area.json",
    "reports/phase3/pnr/hold_buffer_area.json",
)
#: Any one of these keys makes a JSON judgeable by this gate.
_AREA_KEYS = ("hold_buffer_area", "total_cell_area",
              "before_total_area", "after_total_area")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def state_pair_document(project: Path, before: Path, after: Path) -> dict:
    """The actual resizer State pair; no hold-only reinterpretation of delta."""
    states = {k: json.loads(p.read_text()) for k, p in
              (("before", before), ("after", after))}
    metrics = {k: s.get("metrics", {}) for k, s in states.items()}
    return {
        "program": "librelane_cts_hold.execute", "schema": "hold-area-state-pair-v1",
        "before_total_area": metrics["before"].get("design__instance__area__stdcell"),
        "after_total_area": metrics["after"].get("design__instance__area__stdcell"),
        "before_instance_count": metrics["before"].get("design__instance__count"),
        "after_instance_count": metrics["after"].get("design__instance__count"),
        "hold_buffer_count": metrics["after"].get("design__instance__count__hold_buffer"),
        "setup_buffer_count": metrics["after"].get("design__instance__count__setup_buffer"),
        "numerator_basis": "ResizerTimingPostCTS total stdcell after - before; "
                           "setup-plus-hold delta is an upper bound on hold area",
        "sources": {k: str(p.relative_to(project)) for k, p in
                    (("before", before), ("after", after))},
        "sources_sha256": {k: _sha(p) for k, p in
                           (("before", before), ("after", after))},
    }


def project_area_result(project: Path, path: Path) -> Tuple[str, int, dict]:
    """Validate current numeric inputs instead of trusting an old verdict."""
    numeric = None
    try:
        doc = json.loads(path.read_text())
        if doc.get("schema") != "hold-area-state-pair-v1":
            raise ValueError("no bound current State pair")
        sources = {}
        for key in ("before", "after"):
            rel = Path(doc["sources"][key])
            source = (project / rel).resolve()
            if rel.is_absolute() or not source.is_relative_to(project.resolve()):
                raise ValueError("State source escapes project")
            if _sha(source) != doc["sources_sha256"][key]:
                raise ValueError(f"{key} State bytes changed")
            sources[key] = source
        actual = state_pair_document(project.resolve(), sources["before"], sources["after"])
        for field in ("before_total_area", "after_total_area"):
            # JSON booleans are not numeric measurements, even when equal to 1.
            value = doc.get(field)
            if isinstance(value, bool) or value != actual[field]:
                raise ValueError(f"{field} differs from current State")
            if not _finite_positive(value):
                raise ValueError(f"{field} is unmeasured")
        numeric = evaluate(None, None, doc["before_total_area"], doc["after_total_area"],
                           allow_zero=_tool_counted_zero(str(path)))
        numeric[2].update(input_source=str(path.relative_to(project)),
                          input_sha256=_sha(path), sources=doc["sources"],
                          sources_sha256=doc["sources_sha256"],
                          numerator_basis=doc["numerator_basis"])
        for field in ("before_instance_count", "after_instance_count", "hold_buffer_count",
                      "setup_buffer_count"):
            value = doc.get(field)
            if (isinstance(value, bool) or value != actual[field]
                    or not isinstance(value, (int, float)) or not math.isfinite(value)
                    or value < 0):
                raise ValueError(f"{field} is missing or differs from current State")
        # The selected lane owns these two sources. A foreign valid pair must
        # not replace a current failure through a copied producer document.
        import _librelane_cts_hold_evidence as ev
        if set(ev.modes(project).values()) != {"direct"}:
            receipt = json.loads((project / ev.RECEIPT_REL).read_text())
            if receipt.get("selected") != "librelane":
                raise ValueError("selected arm has no matching area State pair")
            chain = receipt["chain"]
            expected = {"before": chain["Vibeic.ExternalCaptureLaunchRetap"] + "/state_out.json",
                        "after": chain["OpenROAD.ResizerTimingPostCTS"] + "/state_out.json"}
            if doc["sources"] != expected:
                raise ValueError("State pair differs from selected lane")
            if receipt.get("hold_area_sources_sha256") != doc["sources_sha256"]:
                raise ValueError("area hashes differ from current handoff")
        return numeric
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        if numeric is not None and numeric[0] == "FAIL":
            numeric[2]["binding_problem"] = str(exc)
            return numeric
        return "NOT_MEASURED", 4, {
            "tool": _TOOL, "verdict": "NOT_MEASURED", "budget_pct": AREA_BUDGET_PCT,
            "reason": "HOLD_AREA_CURRENT_EVIDENCE_MISSING", "reason_class": "input_absent",
            "message": str(exc)}


def check_state_pair(project: Path, before: Path, after: Path) -> Tuple[dict, dict, int]:
    """Owning adoption gate, before any shipped view is copied."""
    doc = state_pair_document(project, before, after)
    path = project / _PRODUCER_CANDIDATES[0]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n")
    verdict, rc, report = evaluate(None, None, doc["before_total_area"],
                                  doc["after_total_area"],
                                  allow_zero=_tool_counted_zero(str(path)))
    report.update(input_source=str(path.relative_to(project)), input_sha256=_sha(path),
                  sources=doc["sources"], sources_sha256=doc["sources_sha256"],
                  numerator_basis=doc["numerator_basis"])
    (path.parent / "hold_area_budget.json").write_text(json.dumps(report, indent=2) + "\n")
    return doc, report, rc


def find_producer(project: Path) -> Optional[Path]:
    """First existing producer artefact under a project root, else None."""
    for rel in _PRODUCER_CANDIDATES:
        p = project / rel
        if p.is_file():
            return p
    return None


def _tool_counted_zero(path: Optional[str]) -> bool:
    """True when the input JSON carries ``hold_buffer_count`` == 0 (an int)."""
    if not path or not Path(path).is_file():
        return False
    try:
        count = json.loads(Path(path).read_text(errors="replace")).get(
            "hold_buffer_count")
    except (OSError, ValueError, AttributeError):
        return False
    return isinstance(count, int) and not isinstance(count, bool) and count == 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Enforce the 5% hold-buffer area-budget guardrail")
    ap.add_argument("input_json", nargs="?",
                    help="JSON with hold_buffer_area+total_cell_area OR "
                         "before_total_area+after_total_area, or a project "
                         "directory to probe for a producer")
    ap.add_argument("--hold-buffer-area", type=float)
    ap.add_argument("--total-cell-area", type=float)
    ap.add_argument("--before-total-area", type=float)
    ap.add_argument("--after-total-area", type=float)
    ap.add_argument("--allow-zero", action="store_true",
                    help="treat 0 inserted area as PASS (no hold violations)")
    ap.add_argument("--json", help="write JSON report to this path")
    args = ap.parse_args(argv)

    if not (args.input_json or args.hold_buffer_area is not None or
            args.before_total_area is not None):
        print(f"[{_TOOL}] supply <input.json> or area flags", file=sys.stderr)
        return 1

    if args.input_json and Path(args.input_json).is_dir():
        project = Path(args.input_json)
        found = find_producer(project)
        if found is None:
            verdict, rc, report = "NOT_MEASURED", 4, {
                "tool": _TOOL, "budget_pct": AREA_BUDGET_PCT,
                "verdict": "NOT_MEASURED", "reason": "NO_AREA_PRODUCER",
                "reason_class": "input_absent", "probed": list(_PRODUCER_CANDIDATES),
                "message": "no current before/after area producer"}
        else:
            verdict, rc, report = project_area_result(project, found)
        if args.json:
            outp = Path(args.json)
            outp.parent.mkdir(parents=True, exist_ok=True)
            outp.write_text(json.dumps(report, indent=2) + "\n")
        print(f"=== {_TOOL} === verdict: {verdict}")
        if verdict == "NOT_MEASURED":
            print(f"INCOMPLETE: {_TOOL} — {report['reason']}: {report['message']}")
        elif verdict == "FAIL":
            print(f"FAIL [{report['reason']}]: {report['message']}")
        return rc

    hba, tca, bta, ata = _load_input(args)
    # A producer that also records the TOOL's own hold-buffer count (T98: the
    # LibreLane ResizerTimingPostCTS metric) settles the zero case by
    # measurement: 0 buffers inserted is "nothing to budget", the same fact
    # --allow-zero states by hand.
    allow_zero = args.allow_zero or _tool_counted_zero(args.input_json)
    verdict, rc, report = evaluate(hba, tca, bta, ata, allow_zero=allow_zero)

    if args.json:
        outp = Path(args.json)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(report, indent=2) + "\n")
    print(f"=== {_TOOL} === verdict: {verdict}")
    if "overhead_pct" in report:
        print(f"  overhead: {report['overhead_pct']:.3f}% "
              f"(budget {AREA_BUDGET_PCT:g}%)")
    if verdict == "FAIL":
        print(f"  FAIL [{report.get('reason')}]: {report.get('message')}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
