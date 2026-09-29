"""R-0929-U14-OWNER-WAIVER — step 36's gate READS its declared checklist.

`tapeout_signoff_check` (the step-36 `program_exit_zero` clause) judges
`reports/audit/tapeout_checklist.json`, the document its producer
`tapeout_checklist_gen` assembles. The sign-off is refused when the checklist
is absent, was written by anything but its producer, or reports a blocker
missing. It never passes on the checklist's absence."""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROG))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import tapeout_signoff_check as T  # noqa: E402
from test_report_wrappers import _write_ready_checklist  # noqa: E402


def _write(project: Path, doc) -> None:
    p = project / T.CHECKLIST_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc))


def test_the_ready_checklist_its_producer_wrote_passes(tmp_path):
    _write_ready_checklist(tmp_path)
    judged = T.judge_checklist(tmp_path)
    assert judged["verdict"] == "PASS", judged
    assert judged["blockers_missing"] == 0


def test_an_absent_checklist_is_refused_never_passed(tmp_path):
    judged = T.judge_checklist(tmp_path)
    assert judged["verdict"] == "FAIL" and "unreadable" in judged["why"], judged


def test_a_checklist_not_from_its_producer_is_refused(tmp_path):
    _write(tmp_path, {"program": "tapeout_signoff_check",
                      "verdict": "READY_FOR_TAPEOUT",
                      "summary": {"blockers_missing": 0}, "items": []})
    judged = T.judge_checklist(tmp_path)
    assert judged["verdict"] == "FAIL" and "producer" in judged["why"], judged


def test_a_missing_blocker_refuses_the_signoff(tmp_path):
    _write(tmp_path, {"program": "tapeout_checklist_gen",
                      "verdict": "BLOCKER_MISSING",
                      "summary": {"blockers_total": 2, "blockers_missing": 1},
                      "items": [{"name": "gds", "severity": "blocker",
                                 "present": True},
                                {"name": "lvs", "severity": "blocker",
                                 "present": False}]})
    judged = T.judge_checklist(tmp_path)
    assert judged["verdict"] == "FAIL", judged
    assert judged["missing_blockers"] == ["lvs"], judged


def test_the_gate_run_fails_on_a_refused_checklist_and_records_why(tmp_path):
    """End to end through the shim: whatever signoff_audit says, a refused
    checklist makes the step-36 clause exit 1 and the report says why."""
    out = tmp_path / "reports/audit/tapeout_signoff.json"
    rc = T.run([str(tmp_path), "--mode", "tapeout", "--json", str(out)])
    assert rc == 1
    report = json.loads(out.read_text())
    assert report["tapeout_checklist"]["verdict"] == "FAIL"
    assert report["passed"] is False
