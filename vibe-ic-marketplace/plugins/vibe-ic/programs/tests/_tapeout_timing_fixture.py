"""Step-23 / Step-32 sign-off verdicts for tape-out fixtures (U17).

The tape-out timing slot credits the sign-off VERDICTS, not a timing report's
existence. A fixture that stands for a timing-clean project therefore carries
what a timing-clean run writes: a record for EVERY blocking Step-23 clause the
flow yaml declares (the nominal STA summary written by `eda_report_audit:sta`
and bound by content digest to the report it audited, plus the corner, record,
hold, DRV and residual clauses) and step 32's declared no-repair outcome with
its decision record. A fixture that omits them reads the timing slot
NOT_MEASURED.
"""
import json
import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import _audit_receipt  # noqa: E402
import signoff_audit  # noqa: E402

STEP23_SUMMARY = "reports/phase3/sta/post_route_summary.json"
STEP32_DIR = "phase3/stage3/postroute_timing_repair"
#: What the fixture's nominal summary is bound to. Its name matches no
#: tape-out timing glob, so it can never credit or re-rank the timing slot.
NOMINAL_SUBJECT = "reports/phase3/sta/nominal_subject.txt"


def bound_summary(proj: Path, subject_rel: str, passed: bool = True,
                  findings=None) -> dict:
    """A summary in the shape `eda_report_audit:sta` writes, bound to
    ``subject_rel``'s CURRENT bytes."""
    proj = Path(proj)
    return {"program": "eda_report_audit:sta", "passed": passed,
            "findings": list(findings or []), "summary": {},
            "subject": _audit_receipt.subject_of([proj / subject_rel],
                                                 relative_to=proj)}


def write_step23(proj: Path, passed: bool = True, findings=None) -> Path:
    """Every blocking Step-23 clause record, PASS (or the summary FAIL)."""
    proj = Path(proj)
    subj = proj / NOMINAL_SUBJECT
    subj.parent.mkdir(parents=True, exist_ok=True)
    if not subj.is_file():
        subj.write_text("nominal post-route STA subject\n")
    for _program, rel, cond in signoff_audit._step_clauses("23"):
        if rel is None or cond is not None:
            continue            # conditional clauses: their condition is absent
        path = proj / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if rel == STEP23_SUMMARY:
            path.write_text(json.dumps(
                bound_summary(proj, NOMINAL_SUBJECT, passed, findings)))
        else:
            path.write_text(json.dumps({"verdict": "PASS"}))
    return proj / STEP23_SUMMARY


def write_step23_other_clauses(proj: Path) -> None:
    """The non-summary Step-23 clause records, PASS — for a test that
    produces the summary itself with the real `sta_report_check`."""
    proj = Path(proj)
    for _program, rel, cond in signoff_audit._step_clauses("23"):
        if rel is None or cond is not None or rel == STEP23_SUMMARY:
            continue
        path = proj / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"verdict": "PASS"}))


def write_step32_no_repair(proj: Path) -> Path:
    out = Path(proj) / STEP32_DIR
    out.mkdir(parents=True, exist_ok=True)
    (out / "postroute_timing_repair_decision.json").write_text(json.dumps({
        "repair_needed": False, "action": "no_repair_needed",
        "reason": "post-route setup and hold met at every corner"}))
    (out / "no_repair_needed.flag").write_text("timing met\n")
    return out


def write_timing_signoff_pass(proj: Path) -> Path:
    """Both sign-off verdicts PASS: the state of a timing-clean run."""
    write_step23(proj)
    write_step32_no_repair(proj)
    return Path(proj)
