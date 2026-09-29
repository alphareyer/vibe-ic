"""R-0929-LECNP-STATE review (L48H_final): a bounded window never turns an
owner-WAIVED DRV residual -- or any word outside PASS / PASS_WITH_WAIVERS /
NOT_APPLICABLE -- into PASS.

`_bounded_window_verdict` combines the window's runner verdict with the gates
it refreshed. Only PASS, PASS_WITH_WAIVERS and NOT_APPLICABLE may yield a pass
word; WAIVED stays WAIVED (non-green, DRV standard), anything else is
NOT_MEASURED, and FAIL / NOT_PROVEN keep their precedence."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import phase3_one_shot_runner as p3
import vibe_ic_one_shot_runner as frontdoor


@pytest.mark.parametrize("step,gates,expected", [
    ("PASS", ["WAIVED"], "WAIVED"),
    ("WAIVED", ["PASS"], "WAIVED"),
    ("WAIVED", [], "WAIVED"),
    ("PASS", ["PASS_WITH_ATTRIBUTION"], "NOT_MEASURED"),
    ("PASS_WITH_ATTRIBUTION", ["PASS"], "NOT_MEASURED"),
    ("PASS", ["PASS", "NOT_APPLICABLE"], "PASS"),
    ("PASS", ["PASS_WITH_WAIVERS"], "PASS_WITH_WAIVERS"),
    ("WAIVED", ["NOT_PROVEN"], "NOT_PROVEN"),
    ("WAIVED", ["FAIL"], "FAIL"),
    ("WAIVED", ["NOT_MEASURED"], "NOT_MEASURED"),
    ("PASS", ["SOMETHING_UNREGISTERED"], "NOT_MEASURED"),
])
def test_only_the_three_pass_words_can_yield_a_pass(step, gates, expected):
    assert p3._bounded_window_verdict(step, gates) == expected


def test_the_front_door_keeps_the_windows_waived_word():
    # vibe_ic_one_shot.json's `verdict` is this function of the Phase-3
    # window report's verdict; it must not upgrade WAIVED either.
    assert frontdoor._bounded_window_verdict("WAIVED", {}) == "WAIVED"
    assert frontdoor._bounded_window_verdict("WAIVED", {"37": "stale"}) == (
        "NOT_MEASURED")


def test_a_step32_window_on_an_owner_waived_drv_does_not_write_pass(
        tmp_path, monkeypatch):
    """--entry-step 32 --exit-step 32 through the real window runner: the
    child reports PASS and step 32's refreshed gate is the owner-WAIVED DRV
    state. phase3_one_shot.json must carry WAIVED, not PASS."""
    project = tmp_path / "spm"
    (project / "phase3/stage3/postroute_timing_repair").mkdir(parents=True)
    # Same harness as test_spmic_window_step32_attribution: a prior write
    # record, then a child that writes step 32's declared outputs.
    assert p3._pl.emit_steps_view(
        project, p3.PROGRAMS_DIR, runner="phase3_one_shot_runner",
        only_steps={"32"})["status"] == "OK"

    def eda_writes(_project, isolated, _cmd):
        # `_phase3_enclosing_supervised(project, isolated, cmd)`: the child
        # writes into the ISOLATED copy the window publishes from.
        target = isolated / "phase3/stage3/postroute_timing_repair"
        target.mkdir(parents=True, exist_ok=True)
        (target / "postroute_timing_repair_decision.json").write_text(
            json.dumps({"repair_needed": True,
                        "action": "input_route_kept"}) + "\n")
        (target / "repair_log.json").write_text(
            json.dumps({"changes": [], "re_verified": False}) + "\n")
        report = p3._pl.report_path(isolated, "phase3_one_shot.json")
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps({"verdict": "PASS"}) + "\n")
        return SimpleNamespace(rc=0, stalled=False, out="", err="",
                               elapsed_s=1.0, outcome="natural"), Path("eda.stderr")

    monkeypatch.setattr(p3, "_phase3_enclosing_supervised", eda_writes)
    monkeypatch.setattr(p3, "_phase3_window_full_gate_audit",
                        lambda project, ids: {"32": {
                            "status": "WAIVED",
                            "reason": "DRV owner waiver: residual max_fanout"}})
    args = SimpleNamespace(entry_step="32", exit_step="32", container="")
    p3._run_phase3_window(project, "spm", SimpleNamespace(name="gf180mcuD"),
                          args, ["enclosing_phase3"])
    # The window's OWN report (what the front door reads as reports["phase3"]).
    report = json.loads(next((project / "reports/orchestrator/windows").glob(
        "*/phase3_one_shot.json")).read_text())
    assert report["verdict"] == "WAIVED", report.get("verdict")
    assert report["audit_verdict"] == "WAIVED"
    # ...and the front door's bounded summary is built from exactly that word.
    assert frontdoor._bounded_window_verdict(report["verdict"], {}) != "PASS"
