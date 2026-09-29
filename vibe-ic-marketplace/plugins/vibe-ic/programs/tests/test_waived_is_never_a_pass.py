"""WAIVED is never PASS -- at every reader that rolls a verdict up.

The DRV sign-off standard (owner-approved 2026-09-28, verdict rule 7): a run
whose only deviation is a valid owner waiver is WAIVED, "counted separately,
never PASS", and "neither a flow nor an orchestrator may downgrade a violation
automatically". `verdict.Verdict.WAIVED` carries that word (schema 3). The
readers below each held a copy of the OLDER meaning of the same spelling -- a
waiver-tier pass -- and so turned the owner's non-green word into a green one.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROGRAMS = HERE.parent
sys.path.insert(0, str(PROGRAMS))

import verdict as T                                           # noqa: E402
import vibe_ic_one_shot_runner as V                           # noqa: E402


# ── (1) the top-level runner ────────────────────────────────────────────────

def test_a_waived_phase_is_not_rolled_up_as_a_waiver_pass():
    verdict, why = V._roll_up([("phase1", "PASS", 0), ("phase2", "PASS", 0),
                               ("phase3", "WAIVED", 1)],
                              audit_axis={"state": "PASS"})
    assert verdict == "WAIVED", (verdict, why)
    assert not T.is_done_claim(verdict)
    assert any("phase3 WAIVED" in w for w in why), why


def test_a_waived_row_with_the_wrong_rc_does_not_hide_a_crash():
    # Review wave 58: every phase runner exits 1 on WAIVED. rc 0 or a
    # signal/crash code beside WAIVED is a report disagreeing with its
    # process -- rule (2) makes that FAIL, never WAIVED.
    for rc in (0, 2, 137):
        verdict, why = V._roll_up([("phase3", "WAIVED", rc)],
                                  audit_axis={"state": "PASS"})
        assert verdict == "FAIL", (rc, verdict, why)
        assert any(f"rc={rc}" in w for w in why), why


def test_the_not_measured_demotions_reach_waived():
    # FAIL > NOT_MEASURED > WAIVED: a run that did not measure part of the
    # flow cannot publish WAIVED (the DRV standard's WAIVED is "no FAIL and
    # no NOT_MEASURED").
    reasons = []
    assert V._demote_for_stale_generated_docs("WAIVED", "stale", reasons) \
        == "NOT_MEASURED"
    assert reasons == ["stale"]
    assert V._demote_for_stale_generated_docs("FAIL", "stale", []) == "FAIL"
    assert "WAIVED" in V._DEMOTABLE_TO_NOT_MEASURED
    assert "FAIL" not in V._DEMOTABLE_TO_NOT_MEASURED
    # the AI-pending and the per-phase demotions read the same set
    src = (PROGRAMS / "vibe_ic_one_shot_runner.py").read_text()
    assert "_ai_pending and overall in _DEMOTABLE_TO_NOT_MEASURED" in src
    assert '_phase["verdict"] in _DEMOTABLE_TO_NOT_MEASURED' in src


def test_waived_ranks_below_fail_and_not_measured_and_above_the_passes():
    axis = {"state": "PASS"}
    assert V._roll_up([("a", "WAIVED", 1), ("b", "FAIL", 1)],
                      audit_axis=axis)[0] == "FAIL"
    assert V._roll_up([("a", "WAIVED", 1), ("b", "NOT_MEASURED", 1)],
                      audit_axis=axis)[0] == "NOT_MEASURED"
    assert V._roll_up([("a", "WAIVED", 1), ("b", "PASS_WITH_WAIVERS", 0)],
                      audit_axis=axis)[0] == "WAIVED"
    # The runner's order is the vocabulary's own order.
    order = [w.value for w in T.RUN_PRECEDENCE]
    assert order.index("NOT_MEASURED") < order.index("WAIVED") < order.index(
        "PASS_WITH_WAIVERS")


def test_a_waived_completion_audit_is_not_a_waiver_pass():
    axis = V._audit_axis_from_verdicts(["WAIVED"])
    assert axis["state"] == "WAIVED", axis
    assert V._audit_axis_from_verdicts(["PASS", "WAIVED"])["state"] == "WAIVED"
    assert V._audit_axis_from_verdicts(["FAIL", "WAIVED"])["state"] == "FAIL"
    verdict, why = V._roll_up([("phase3", "PASS", 0)], audit_axis=axis)
    assert verdict == "WAIVED", (verdict, why)
    # the waiver-tier pass is untouched
    assert V._audit_axis_from_verdicts(
        ["PASS_WITH_WAIVERS"])["state"] == "PASS_WITH_WAIVERS"


def test_the_exit_code_refuses_waived():
    # `main` exits 0 only for the words in its `overall in (...)` test; the
    # AST pin in test_the_front_door_verdict_is_the_conjunction_of_its_phases
    # holds that tuple inside the pass sets, which WAIVED is no longer in.
    assert "WAIVED" not in (V._PHASE_PASS | V._PHASE_PASS_WITH_NOTE)
    src = (PROGRAMS / "vibe_ic_one_shot_runner.py").read_text()
    assert 'return 0 if overall in ("PASS", "PASS_WITH_WAIVERS") else 1' in src


# ── (2) the roll-up consistency gate's audit-JSON reader ────────────────────

import json                                                   # noqa: E402

import final_summary_rollup_consistency_check as C            # noqa: E402

_CURRENT_SUMMARY = """# Final summary

```
=== Vibe-IC phase1_phase2_phase3 compliance ===
Steps: 6 total
  PASS=3  PASS_WITH_WAIVERS=1  WAIVED=1  WAIVED-DEFERRED=1  FAIL=0  NOT_MEASURED=1  NOT_APPLICABLE=0
```

### Verdict roll-up

| Verdict | Count |
|---|---:|
| ✅ PASS | 3 |
| PASS_WITH_WAIVERS | 1 |
| WAIVED | 1 |
| NOT_MEASURED | 1 |
| **Total** | **6** |
"""


def _current_project(tmp_path, step_counts, schema=T.SCHEMA_VERSION):
    (tmp_path / "reports" / "audit").mkdir(parents=True)
    (tmp_path / "reports" / "final_summary.md").write_text(
        _CURRENT_SUMMARY, encoding="utf-8")
    doc = {"step_counts": step_counts}
    if schema is not None:
        doc["step_status_schema_version"] = schema
    (tmp_path / "reports" / "audit" / "phase23_completion_audit.json"
     ).write_text(json.dumps(doc))
    return tmp_path


def test_a_current_audit_json_keeps_waived_in_its_own_bucket(tmp_path):
    counts = {"PASS": 3, "PASS_WITH_WAIVERS": 1, "PASS_WITH_ATTRIBUTION": 0,
              "WAIVED": 1, "FAIL": 0, "NOT_MEASURED": 1, "NOT_APPLICABLE": 0}
    js = C._audit_json_buckets({"step_counts": counts,
                                "step_status_schema_version": T.SCHEMA_VERSION})
    assert js["WAIVED"] == 1 and js["PASS_WITH_WAIVERS"] == 1, js
    assert "WAIVED-DEFERRED" not in js, js
    ok, notes = C.check_project(_current_project(tmp_path, counts),
                                check_audit_json=True)
    assert ok, notes
    assert "PASS: roll-up table == audit JSON step_counts." in notes


def test_a_waived_count_cannot_hide_in_the_waiver_pass_bucket(tmp_path):
    # The audit says 2 WAIVED and 0 PASS_WITH_WAIVERS; the table says the
    # opposite. Read as the old waiver bucket, the two were never compared.
    counts = {"PASS": 3, "PASS_WITH_WAIVERS": 0, "WAIVED": 2,
              "NOT_MEASURED": 1}
    ok, notes = C.check_project(_current_project(tmp_path, counts),
                                check_audit_json=True)
    blob = "\n".join(notes)
    assert not ok, blob
    assert "WAIVED: table=1 audit_json=2" in blob, blob


def test_a_pre_rename_audit_json_is_still_read_in_its_own_words():
    js = C._audit_json_buckets({"step_counts": {"PASS": 4, "WAIVED": 1,
                                                "VACUOUS_PASS": 2}})
    assert js == {"PASS": 4, "WAIVED-DEFERRED": 1, "VACUOUS-PASS": 2}, js


# ── (3) a gate's own step-waiver WAIVED, read by the flow ───────────────────

import flow_compliance_check as F                             # noqa: E402

_GATE_REPORT = "reports/analog/mixed_signal/level_shifter_check.json"


def _self_waiving_project(tmp_path, gate_waiver=True):
    """A project whose ONLY evidence for the step is a gate's own waiver
    lookup: `level_shifter_required_check` finds `waived_steps` keyed by its
    label, writes `verdict: WAIVED` and exits 0. The flow's own waiver record
    (`check_step(..., waivers)`) does not cover the step."""
    project = tmp_path / "p"
    project.mkdir()
    if gate_waiver:
        (project / "waivers.json").write_text(json.dumps({"waived_steps": [{
            "id": "level_shifter", "ticket": "T-1",
            "reason": "no level-shifter list for this design"}]}))
    return project


def _step():
    return {"id": 94, "name": "level-shifter audit", "stage": "stage3",
            "required_outputs": [],
            "gate": {"all_of": [{"program_exit_zero":
                f"level_shifter_required_check . --json {_GATE_REPORT}"}]}}


def test_a_gate_self_waiver_is_not_a_pass(tmp_path):
    project = _self_waiving_project(tmp_path)
    row = F.check_step(project, _step(), {})
    receipt = json.loads((project / _GATE_REPORT).read_text())
    assert receipt["verdict"] == "WAIVED"          # the gate did waive itself
    assert row.status != "PASS", (row.status, row.reasons)
    assert not T.is_done_claim(row.status), (row.status, row.reasons)
    assert row.status == "NOT_MEASURED", (row.status, row.reasons)
    assert any("says WAIVED" in r for r in row.reasons), row.reasons


def test_the_same_gate_without_a_waiver_keeps_its_own_answer(tmp_path):
    # Control: no waiver -> the gate's honest SKIP (rc 2), not the new branch.
    project = _self_waiving_project(tmp_path, gate_waiver=False)
    row = F.check_step(project, _step(), {})
    assert not any("says WAIVED" in r for r in row.reasons), row.reasons


# ── (3b) the stage review's per-gate green set ──────────────────────────────

import stage_on_pass_review as S                              # noqa: E402


def _declined_row(other_gate_verdict):
    return {"status": "NOT_MEASURED", "advisory_gate_records": [
        {"gate": "stage_on_pass_review", "verdict": "NOT_CHECKED",
         "reason_class": "blocked_by_upstream"},
        {"gate": "some_gate", "verdict": other_gate_verdict,
         "reason_class": ""}]}


def test_a_waived_gate_record_does_not_exempt_a_row():
    # Control: a PASS sibling leaves the declined review as the only cause.
    assert S.blocked_only_by_a_declined_review(_declined_row("PASS"))
    # A WAIVED sibling is not green, so the row is NOT only-declined.
    assert not S.blocked_only_by_a_declined_review(_declined_row("WAIVED"))
    assert "WAIVED" not in S._GATE_VERDICT_GREEN


# ── (3c) the entry guard replays the front door's roll-up ───────────────────

import vibe_ic_entry_guard as G                               # noqa: E402


def _front_door_report(tmp_path, verdict):
    project = tmp_path / "proj"
    project.mkdir(exist_ok=True)
    phases = [{"name": "phase1", "verdict": "PASS", "rc": 0},
              {"name": "phase2", "verdict": "PASS", "rc": 0},
              {"name": "analog", "verdict": "SKIPPED", "rc": 0},
              {"name": "phase3", "verdict": "WAIVED", "rc": 1},
              {"name": "mixed_signal", "verdict": "SKIPPED", "rc": 0}]
    path = tmp_path / "vibe_ic_one_shot.json"
    path.write_text(json.dumps({
        "phase": "vibe-ic", "project": str(project), "verdict": verdict,
        "phases": phases, "completion_audit_axis": {"state": "PASS"},
        "completion_audit_verdicts": ["PASS"]}))
    return path, project


def test_the_guard_accepts_waived_and_refuses_the_downgrade(tmp_path):
    good, project = _front_door_report(tmp_path, "WAIVED")
    assert G._is_orchestrator_report(good, project)
    forged, project = _front_door_report(tmp_path, "PASS_WITH_WAIVERS")
    assert not G._is_orchestrator_report(forged, project)


def test_a_waived_phase1_step_is_not_a_waiver_pass():
    rows = [{"status": "PASS"}, {"status": "WAIVED"}]
    assert G._phase1_steps_verdict(rows) == "WAIVED"
    assert G._phase1_steps_verdict([{"status": "SKIP"}]) == "PASS_WITH_WAIVERS"


# ── (3d) the mixed-signal sign-off's upstream roll-up ───────────────────────

import mixed_signal_signoff_check as M4                       # noqa: E402


def test_a_waived_upstream_merge_does_not_roll_up_as_pass(tmp_path):
    spec = next(s for s in M4._UPSTREAM if s["verdict_based"])
    target = tmp_path / spec["paths"][0]
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"verdict": "PASS"}))
    assert M4._check_upstream(tmp_path, spec)["status"] == "PASS"   # control
    target.write_text(json.dumps({"verdict": "WAIVED"}))
    assert M4._check_upstream(tmp_path, spec)["status"] != "PASS"


def test_a_gate_self_waiver_over_measured_defects_fails_the_step(tmp_path):
    # analog_a6_block_pv_check MEASURES 3 DRC violations and an LVS mismatch,
    # then waives them under its own `waived_steps` lookup (label match, no
    # approver) and exits 0 with verdict WAIVED. No flow record covers the
    # step, so the measured defects stand.
    project = tmp_path / "p"
    d = project / "phase3" / "analog" / "ldo"
    d.mkdir(parents=True)
    (project / "phase3" / "analog" / "analog_block_list.json").write_text(
        json.dumps({"blocks": ["ldo"]}))
    (d / "drc_clean.flag").write_text("violations: 3\n")
    (d / "lvs_match.flag").write_text("lvs: mismatch\n")
    (project / "phase3" / "analog" / "waivers.json").write_text(json.dumps({
        "waived_steps": [{"id": "analog_block_pv", "ticket": "ECO-123",
                          "reason": "PV deferred to top-level signoff"}]}))
    report = "reports/analog/a6_block_pv.json"
    step = {"id": 95, "name": "block PV", "stage": "stage3",
            "required_outputs": [],
            "gate": {"all_of": [{"program_exit_zero":
                f"analog_a6_block_pv_check . --json {report}"}]}}
    row = F.check_step(project, step, {})
    assert json.loads((project / report).read_text())["verdict"] == "WAIVED"
    assert row.status == "FAIL", (row.status, row.reasons)
    blob = "\n".join(row.reasons)
    assert "MEASURED" in blob and "A6_PV_DRC_VIOLATIONS" in blob, blob


# ── (3e) the analog runner reads A6 by rc ───────────────────────────────────

import analog_one_shot_runner as AOSR                         # noqa: E402


def _a6_project(tmp_path, waived):
    proj = tmp_path / "proj"
    d = proj / "phase3" / "analog" / "ldo"
    d.mkdir(parents=True)
    (proj / "phase3" / "analog" / "analog_block_list.json").write_text(
        json.dumps({"blocks": ["ldo"]}))
    (d / "drc_clean.flag").write_text("violations: 3\n")
    (d / "lvs_match.flag").write_text("lvs: mismatch\n")
    if waived:
        (proj / "phase3" / "analog" / "waivers.json").write_text(json.dumps({
            "waived_steps": [{"id": "analog_block_pv", "ticket": "ECO-123",
                              "reason": "PV deferred to top-level signoff"}]}))
    return proj


def test_the_analog_runner_does_not_pass_a_self_waived_a6(tmp_path, monkeypatch):
    monkeypatch.setattr(AOSR, "_try_native_a6_pv", lambda *a, **k: {"ran": False})
    row = AOSR.step_for_block(_a6_project(tmp_path, True), {"name": "ldo"},
                              "A6_block_pv", None)
    assert row.status == "FAIL", (row.status, row.detail)
    assert "A6_SELF_WAIVER_REFUSED" in row.detail, row.detail
    # Control: the same measured defects without a waiver are the gate's FAIL.
    other = tmp_path / "o"
    other.mkdir()
    row = AOSR.step_for_block(_a6_project(other, False), {"name": "ldo"},
                              "A6_block_pv", None)
    assert row.status == "FAIL" and "A6_SELF_WAIVER_REFUSED" not in row.detail


def test_the_self_waiver_reader_is_the_gates_verdict_line():
    assert AOSR._gate_self_waived("=== g ===\n  verdict: WAIVED (0/1 block(s))\n")
    assert not AOSR._gate_self_waived("  verdict: PASS (1/1)\n  waiver:  T\n")
    assert not AOSR._gate_self_waived("note: the verdict: WAIVED rule\n")


def test_the_native_pv_rerun_does_not_pass_a_self_waived_a6(tmp_path, monkeypatch):
    # Review wave 58 MAJOR: the first A6 run has no DRC/LVS evidence (its own
    # FAIL), native per-block PV then MEASURES 3 DRC violations and an LVS
    # mismatch, and the gate re-runs. Under its own waiver lookup it exits 0
    # with verdict WAIVED -- the re-run was judged by rc alone and read PASS.
    proj = tmp_path / "proj"
    d = proj / "phase3" / "analog" / "ldo"
    d.mkdir(parents=True)
    (proj / "phase3" / "analog" / "analog_block_list.json").write_text(
        json.dumps({"blocks": ["ldo"]}))
    (proj / "phase3" / "analog" / "waivers.json").write_text(json.dumps({
        "waived_steps": [{"id": "analog_block_pv", "ticket": "ECO-123",
                          "reason": "PV deferred to top-level signoff"}]}))

    def native(project, block, container):
        # fake only the EDA tools' file writes
        (d / "drc_clean.flag").write_text("violations: 3\n")
        (d / "lvs_match.flag").write_text("lvs: mismatch\n")
        return {"ran": True, "drc": {"verdict": "FAIL"},
                "lvs": {"verdict": "FAIL"}}

    monkeypatch.setattr(AOSR, "_try_native_a6_pv", native)
    row = AOSR.step_for_block(proj, {"name": "ldo"}, "A6_block_pv", None)
    assert row.status == "FAIL", (row.status, row.detail)
    assert "A6_SELF_WAIVER_REFUSED" in row.detail, row.detail


def test_a_self_waived_handoff_keeps_its_measured_chip_gds_defect(tmp_path):
    # Review wave 58 MINOR: foundry_handoff_package_check dropped its measured
    # chip-GDS finding from the waived report, so the flow read the waiver as
    # "examined nothing" (NOT_MEASURED) instead of a waived defect (FAIL).
    import test_signoff_medlow_backlog_gaps as H
    proj = H._anti_scribe_kit(tmp_path, chip_gds="scribe")
    (proj / "waivers.json").write_text(json.dumps({"waived_steps": [
        {"id": "foundry_handoff", "ticket": "TKT-1",
         "reason": "kit assembler not shipped"}]}) + "\n")
    report = "reports/phase3/foundry_handoff_selfwaiver.json"
    step = {"id": 96, "name": "handoff", "stage": "stage3",
            "required_outputs": [],
            "gate": {"all_of": [{"program_exit_zero":
                f"foundry_handoff_package_check . --json {report}"}]}}
    row = F.check_step(proj, step, {})
    rep = json.loads((proj / report).read_text())
    assert rep["verdict"] == "WAIVED"
    assert "FOUNDRY_HANDOFF_SCRIBE_ONLY" in {f.get("rule") for f in rep["findings"]}
    assert row.status == "FAIL", (row.status, row.reasons)
