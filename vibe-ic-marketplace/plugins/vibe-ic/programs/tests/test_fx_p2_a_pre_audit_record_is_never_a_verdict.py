#!/usr/bin/env python3
"""FX_P2 review fix (1) — the phase-2 record published BEFORE the final audit
must never read as phase 2's verdict.

`design_one_shot_runner._publish_record_before_audit` writes
`reports/orchestrator/phase2_one_shot.json` before `step_final_audit`, so the
audit judges this run's record rather than the previous run's. At 065ef1c45 that
copy carried `verdict: _aggregate_verdict(plan)` -- the PRE-audit aggregate,
without the tail's ai_judgements demotion. Both of its readers take a verdict
written in this invocation as final:

  * the front door's `_row_verdict` (vibe_ic_one_shot_runner.py) reads
    `verdict` from any record published here and never looks at rc;
  * `phase23_one_shot_runner` halts before phase 3 only on `verdict == "FAIL"`.

So a phase-2 process that died during the audit or the tail -- an uncaught
exception (rc 1), the stall watchdog (rc 2), an OOM or deadline kill (rc 137) --
was read as a PASS and phase 3 ran on a phase 2 whose audit never finished. On
main the same death left no fresh record and R-0915-160 made it FAIL.

Driven through the real publisher and the real front-door reader.
chip-AGNOSTIC: a synthetic project and step names.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import design_one_shot_runner as DOSR   # noqa: E402
import vibe_ic_one_shot_runner as V     # noqa: E402

REPORT = "phase2_one_shot.json"
#: Every step green: the pre-audit aggregate of this plan is PASS.
PLAN = [DOSR.StepResult("detect_ic_class", "PASS", 0.0, "a class"),
        DOSR.StepResult("rtl_gen", "PASS", 0.0, "rtl written"),
        DOSR.StepResult("lint", "PASS", 0.0, "clean")]


def _published_before_audit(tmp_path: Path) -> "tuple[Path, float]":
    proj = tmp_path / "proj"
    proj.mkdir()
    started = time.time() - 5.0
    DOSR._publish_record_before_audit(proj, PLAN, "a_class", {})
    return proj, started


def _record(proj: Path) -> dict:
    return json.loads(V._phase_report_path(proj, REPORT).read_text())


def test_the_plan_is_green_before_the_audit():
    """The control: without the fix this plan's aggregate IS what was written."""
    assert DOSR._aggregate_verdict(PLAN) == "PASS"


def test_a_death_during_the_audit_is_not_read_as_pass(tmp_path):
    """The audit raised (rc 1), stalled (rc 2) or was killed (rc 137) after
    the pre-audit publication: the front door must FAIL and halt."""
    proj, started = _published_before_audit(tmp_path)
    for rc in (1, 2, 137):
        verdict, why = V._row_verdict(proj, REPORT, rc, started, "phase2")
        assert verdict == "FAIL", (rc, verdict, why)
        assert why and "final audit" in why, why


def test_a_pending_record_with_rc_zero_is_still_not_a_verdict(tmp_path):
    """No runner path exits 0 before its tail, but if one did, the pending
    copy still is not phase 2's account of itself."""
    proj, started = _published_before_audit(tmp_path)
    verdict, _why = V._row_verdict(proj, REPORT, 0, started, "phase2")
    assert verdict == "FAIL", verdict


def test_the_record_itself_says_fail_for_the_phase23_reader(tmp_path):
    """phase23_one_shot_runner halts on `verdict == "FAIL"` and reads no rc:
    the pre-audit copy must say FAIL on its own, and say why."""
    proj, _started = _published_before_audit(tmp_path)
    rec = _record(proj)
    assert rec["final_audit_pending"] is True
    assert rec["verdict"] == "FAIL", rec["verdict"]
    assert "final audit pending" in rec["verdict_reason"]
    # the aggregate is still disclosed, under a name no reader takes as final
    assert rec["pre_audit_aggregate"] == "PASS"


def test_a_pending_record_carrying_a_green_verdict_is_still_fail(tmp_path):
    """The reader keys on `final_audit_pending`, not on the word the copy
    happens to carry -- a copy written in 065ef1c45's shape (verdict = the
    pre-audit aggregate) must not pass either."""
    proj = tmp_path / "proj"
    out = V._phase_report_path(proj, REPORT)
    out.parent.mkdir(parents=True)
    started = time.time() - 5.0
    out.write_text(json.dumps({"steps": [], "verdict": "PASS",
                               "final_audit_pending": True}))
    verdict, _why = V._row_verdict(proj, REPORT, 1, started, "phase2")
    assert verdict == "FAIL", verdict


def test_the_tails_record_is_read_as_before(tmp_path):
    """TEETH the other way: once the tail has written the real record (no
    pending flag), its verdict is phase 2's verdict, whatever the rc."""
    proj, started = _published_before_audit(tmp_path)
    out = V._phase_report_path(proj, REPORT)
    DOSR._write_phase2_report(out, {"steps": [], "verdict":
                                    "PASS_WITH_WAIVERS"}, proj)
    assert V._row_verdict(proj, REPORT, 0, started, "phase2") == \
        ("PASS_WITH_WAIVERS", None)
