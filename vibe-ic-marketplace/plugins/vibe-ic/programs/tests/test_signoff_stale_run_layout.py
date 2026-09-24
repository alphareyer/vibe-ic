"""Sign-off must be bound to the layout produced by this invocation."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as runner  # noqa: E402


def _layout(project, top, mtime):
    pnr = runner._pl.pnr_dir(project)
    pnr.mkdir(parents=True, exist_ok=True)
    for name in ("routed.def", f"{top}.def", f"{top}.gds"):
        path = pnr / name
        path.write_text(name)
        os.utime(path, (mtime, mtime))


def _rows():
    return (runner.StepResult("pnr", "PASS", 0, "route written"),
            runner.StepResult("gds", "PASS", 0, "streamed"))


def _clock_gate_project(project):
    import clock_target_record_agreement_check as clock_check
    p = project / clock_check.L19_REL
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"fields": {clock_check._CT_KEY:
                                   {"status": "NOT_STATED"}}}))
    return next(g for g in runner._DECLARED_SIGNOFF_GATES
                if g[0] == "clock_target_agreement")


def test_old_report_cannot_turn_stale_layout_green_via_existing_gate_api(
        tmp_path, monkeypatch):
    _layout(tmp_path, "unit", 100)
    monkeypatch.setattr(runner, "_RUN_STARTED_AT", 200)
    report = tmp_path / "reports" / "phase3" / "em.rpt"
    report.parent.mkdir(parents=True)
    report.write_text("prior run PASS\n")
    os.utime(report, (100, 100))
    monkeypatch.setattr(runner, "_DECLARED_SIGNOFF_GATES",
                        (_clock_gate_project(tmp_path),))
    rows = runner.step_declared_signoff_gates(tmp_path)
    assert rows[0].status == "NOT_MEASURED"
    assert [row.status for row in rows] == ["NOT_MEASURED"] * len(rows)
    assert [row.reason_class for row in rows] == [
        runner._V.ReasonClass.UPSTREAM_FAILED] * len(rows)


def test_failed_pnr_refuses_every_signoff_gate_without_invoking_old_reports(tmp_path,
                                                                              monkeypatch):
    _layout(tmp_path, "unit", 100)
    stale_report = tmp_path / "reports" / "phase3" / "em.rpt"
    stale_report.parent.mkdir(parents=True)
    stale_report.write_text("prior run PASS\n")
    os.utime(stale_report, (100, 100))
    pnr = runner.StepResult("pnr", "FAIL", 0, "GPL-0305")
    refusal = runner._layout_signoff_refusal(tmp_path, "unit", pnr, None, 200)
    assert "pnr step is FAIL" in refusal
    gate = _clock_gate_project(tmp_path)
    monkeypatch.setattr(runner, "_DECLARED_SIGNOFF_GATES", (gate,))
    rows = runner.step_declared_signoff_gates(tmp_path, upstream_refusal=refusal)
    assert {r.name for r in rows} == {g[0] for g in runner._DECLARED_SIGNOFF_GATES}
    assert all(r.status == "NOT_MEASURED" and
               r.reason_class == runner._V.ReasonClass.UPSTREAM_FAILED
               for r in rows)


def test_old_layout_is_not_current_even_when_steps_claim_pass(tmp_path):
    _layout(tmp_path, "unit", 100)
    refusal = runner._layout_signoff_refusal(tmp_path, "unit", *_rows(), 200)
    assert "not written in this run" in refusal


def test_fresh_layout_allows_real_gate_verdicts(tmp_path, monkeypatch):
    _layout(tmp_path, "unit", 300)
    monkeypatch.setattr(runner, "_RUN_STARTED_AT", 200)
    fresh_report = tmp_path / "reports" / "phase3" / "em.rpt"
    fresh_report.parent.mkdir(parents=True)
    fresh_report.write_text("current run PASS\n")
    os.utime(fresh_report, (300, 300))
    assert runner._layout_signoff_refusal(tmp_path, "unit", *_rows(), 200) == ""
    gate = _clock_gate_project(tmp_path)
    monkeypatch.setattr(runner, "_DECLARED_SIGNOFF_GATES", (gate,))
    rows = runner.step_declared_signoff_gates(tmp_path)
    assert rows[0].status == "PASS"
