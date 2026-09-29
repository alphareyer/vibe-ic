"""Step-23 / Step-32 sign-off verdicts for tape-out fixtures (U17).

The tape-out timing slot credits the sign-off VERDICTS, not a timing report's
existence. A fixture that stands for a timing-clean project therefore carries
what a timing-clean run writes: step 23's declared STA summary saying
``passed: true`` and step 32's declared no-repair outcome with its decision
record. A fixture that omits them now reads the timing slot NOT_MEASURED.
"""
import json
from pathlib import Path

STEP23_SUMMARY = "reports/phase3/sta/post_route_summary.json"
STEP32_DIR = "phase3/stage3/postroute_timing_repair"


def write_step23(proj: Path, passed: bool = True, findings=None) -> Path:
    path = Path(proj) / STEP23_SUMMARY
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "program": "eda_report_audit:sta", "passed": passed,
        "findings": list(findings or []), "summary": {}}))
    return path


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
