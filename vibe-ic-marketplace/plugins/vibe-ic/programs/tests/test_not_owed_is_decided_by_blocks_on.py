"""R-0915-140, reworked (lane ictier1) — "never owed" is decided by blocks_on.

THE RULING. A step whose blocker (a `blocks_on` dependency, transitively) FAILED
reads NOT_MEASURED/upstream_failed — it was never owed its outputs. FAIL/
missing_artefact is reserved for a step that WAS owed: every blocker passed and
it still produced nothing. A gate that ran and failed keeps its FAIL. Strict
mode never goes quiet.

THE THREE DEFECTS THE PRE-LANDING REVIEW CONFIRMED on next/icslot66 (08d63f6c8),
each pinned here by the review's own scenario, RED on that tip:

  1. The #503 pass picked its root by YAML POSITION per track and demoted every
     later missing row. Steps 3 and 7 (blocks_on [1]) behind root 2, and step 35
     (blocks_on [34]) behind root 28, were owed and got "never owed"; a demoted
     step 3 then stopped voiding step 8's PASS (the (8, 3) ordering violation
     vanished).
  2. The OS-constraints promotion reads `failing + missing`; a demoted row left
     both, so root 28 + owed-and-missing 35 went FAIL rc 1 -> PASS_WITH_WAIVERS
     rc 0, and M2-M4 fell off the promoted verdict's must-close list.
  3. The ruling was under-applied: off-track ids (37.3, 37.4, 15.5ic, ...) and
     the other two blocked-by-upstream writers still published
     "[FAIL] (missing_artefact) [blocked-by-upstream(X)]" with X failed; a root
     that is itself missing_artefact never counted as a root; and the VOID
     pass's "not owed" arm could never run.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

PLUGIN = Path(__file__).resolve().parents[2]
PROGRAMS = PLUGIN / "programs"
sys.path.insert(0, str(PROGRAMS))

import flow_compliance_check as FCC                          # noqa: E402
import flow_step_execution_coverage_check as COV              # noqa: E402

_T = FCC._T
_PASS = _T.Verdict.PASS.value
_FAIL = _T.Verdict.FAIL.value
_NM = _T.Verdict.NOT_MEASURED.value
_NA = _T.Verdict.NOT_APPLICABLE.value
_MISSING = _T.ReasonClass.MISSING_ARTEFACT.value
_UPSTREAM = _T.ReasonClass.UPSTREAM_FAILED.value
_FLOW = PLUGIN / "flow" / "phase1_phase2_phase3.yaml"


def _steps():
    return yaml.safe_load(_FLOW.read_text())["steps"]


def _row(sid, status, reason_class="", reasons=None):
    return FCC.StepResult(id=sid, name=f"step {sid}", stage="s",
                          status=status, reason_class=reason_class,
                          reasons=list(reasons or []))


def _gate_fail(sid):
    """A gate that RAN and refused: FAIL with no missing_artefact reason."""
    return _row(sid, _FAIL, "")


def _missing(sid):
    return _row(sid, _FAIL, _MISSING)


def _by_id(rows):
    return {r.id: r for r in rows}


# ── the decision function: fed the failed-blocker closure ──────────────────

def test_the_decision_is_fed_the_failed_blockers_and_an_owed_step_keeps_fail():
    """No failed blocker -> the step was owed -> no change, whatever its word."""
    assert FCC.cascade_tier_for_dependent(_FAIL, _MISSING, []) is None
    assert FCC.cascade_tier_for_dependent(_PASS, "", ()) is None
    assert FCC.cascade_tier_for_dependent(_FAIL, _MISSING, [2]) == (
        _NM, _UPSTREAM, "not_owed")
    assert FCC.cascade_tier_for_dependent(_PASS, "", [2]) == (
        _NM, _UPSTREAM, "pass_voided")
    # a gate that ran and refused keeps its FAIL even behind a failed blocker
    assert FCC.cascade_tier_for_dependent(_FAIL, "", [2]) is None


# ── finding 1: blocks_on decides, YAML order decides nothing ───────────────

def test_steps_owed_their_outputs_keep_fail_behind_an_unrelated_root():
    """Review scenario: root 2 FAILs its own gate, 1 PASS; 3 and 7 (blocks_on
    [1]) cannot reach 2, so both were owed and stay FAIL(missing_artefact) with
    no blocked-by-upstream note and no 'never owed' reason. Step 4 (blocks_on
    [2]) really is blocked and reads the cascade."""
    rows = [_row(1, _PASS), _gate_fail(2), _missing(3), _missing(4),
            _missing(7)]
    info = FCC._attribute_cascade_verdicts(rows, _steps(), waivers={})
    got = _by_id(rows)
    for sid in (3, 7):
        r = got[sid]
        assert (r.status, r.reason_class) == (_FAIL, _MISSING), (sid, r.status)
        assert r.cascade_note == "", (sid, r.cascade_note)
        assert not any("never owed" in x for x in r.reasons), (sid, r.reasons)
    assert (got[4].status, got[4].reason_class) == (_NM, _UPSTREAM)
    assert got[4].cascade_note == "blocked-by-upstream(2)"
    assert info["blocked_by_upstream"] == {2: 1}


def test_a_leaf_root_demotes_nothing_it_does_not_block():
    """Review scenario: root 28 (a leaf) FAILs; 35 (blocks_on [34], 34 PASS) is
    owed and missing -> it stays FAIL(missing_artefact)."""
    rows = [_row(34, _PASS), _gate_fail(28), _missing(35)]
    FCC._attribute_cascade_verdicts(rows, _steps(), waivers={})
    s35 = _by_id(rows)[35]
    assert (s35.status, s35.reason_class) == (_FAIL, _MISSING)
    assert s35.cascade_note == ""


def test_an_owed_missing_step_still_voids_its_pass_dependent():
    """Knock-on of the positional demotion: a demoted step 3 is NOT_MEASURED,
    which the ordering guard accepts as a vacuous process ancestor, so step 8
    (blocks_on [7, 3]) kept its PASS on a CDC report that was never produced.
    Step 3 was owed, so it must stay FAIL and the (8, 3) violation must fire."""
    steps = _steps()
    rows = [_row(1, _PASS), _gate_fail(2), _missing(3), _row(7, _PASS),
            _row(8, _PASS)]
    FCC._attribute_cascade_verdicts(rows, steps, waivers={})
    graph = {str(s["id"]): [str(e) for e in (s.get("blocks_on") or [])]
             for s in steps if s.get("id") is not None}
    report = {"steps": [{"id": r.id, "name": r.name, "status": r.status,
                         "stage": r.stage} for r in rows]}
    pairs = {(str(v["terminal_id"]), str(v["signoff_id"]))
             for v in COV.analyze(report, graph)["ordering_violations"]}
    assert ("8", "3") in pairs, pairs


# ── finding 3: every row with a failed blocker, every writer, any root ─────

def test_a_root_that_is_itself_missing_artefact_is_still_a_root():
    """Step 9 wrote nothing with every blocker PASS: it was owed, stays
    FAIL(missing_artefact), and IS the root of step 10 (blocks_on [9])."""
    rows = [_row("0.5ic", _PASS), _row(2, _PASS), _row(3, _PASS),
            _row(8, _PASS), _missing(9), _missing(10)]
    FCC._attribute_cascade_verdicts(rows, _steps(), waivers={})
    got = _by_id(rows)
    assert (got[9].status, got[9].reason_class) == (_FAIL, _MISSING)
    assert got[9].cascade_note == ""
    assert (got[10].status, got[10].reason_class) == (_NM, _UPSTREAM)
    assert got[10].cascade_note == "blocked-by-upstream(9)"


def test_an_off_track_id_behind_a_failed_blocker_reads_the_cascade():
    """Review scenario (2): root 31 FAILs, 37 and 37.4 (blocks_on [37]) are
    missing. 37.4 is a string id that `_track_of` put on no track, so it kept
    FAIL(missing_artefact) — harsher than the step it waits on."""
    rows = [_gate_fail(31), _missing(37), _missing("37.4"), _missing(38)]
    FCC._attribute_cascade_verdicts(rows, _steps(), waivers={})
    for sid in (37, "37.4", 38):
        r = _by_id(rows)[sid]
        assert (r.status, r.reason_class) == (_NM, _UPSTREAM), (sid, r.status)
        assert r.cascade_note == "blocked-by-upstream(31)", (sid, r.cascade_note)


def test_the_dependency_condition_writer_applies_the_same_rule(tmp_path):
    """Review scenario (1): step 21 FAILs its own gate and routed.def is absent.
    `_resolve_dependency_condition_results` writes 37.3 (blocks_on [21]) as a
    blocked-by-upstream(21) row; with 21 FAILED it must read the cascade, not
    "[FAIL] (missing_artefact) [blocked-by-upstream(21)]"."""
    steps = _steps()
    rows = [_gate_fail(21),
            _row("37.3", _NA, reasons=["condition not met: routed.def"])]
    FCC._resolve_dependency_condition_results(tmp_path, rows, steps)
    FCC._attribute_cascade_verdicts(rows, steps, waivers={})
    r = _by_id(rows)["37.3"]
    assert (r.status, r.reason_class) == (_NM, _UPSTREAM), (r.status,
                                                             r.reason_class)
    assert r.cascade_note == "blocked-by-upstream(21)"


def test_the_dependency_condition_writer_keeps_an_owed_row_fail(tmp_path):
    """Negative arm: producer 21 PASSED and routed.def is still absent — 37.3
    was owed its input's producer and stays FAIL(missing_artefact)."""
    steps = _steps()
    rows = [_row(21, _PASS),
            _row("37.3", _NA, reasons=["condition not met: routed.def"])]
    FCC._resolve_dependency_condition_results(tmp_path, rows, steps)
    FCC._attribute_cascade_verdicts(rows, steps, waivers={})
    r = _by_id(rows)["37.3"]
    assert (r.status, r.reason_class) == (_FAIL, _MISSING)


def test_the_condition_owner_writer_applies_the_same_rule(tmp_path):
    """15.5ic's condition owner 0.5ic FAILED: the owner-block writer must not
    publish FAIL(missing_artefact) + blocked-by-upstream(0.5ic)."""
    steps = _steps()
    rows = [_gate_fail("0.5ic"), _row(15, _PASS),
            _row("15.5ic", _NA, reasons=["condition not met: pad ring"])]
    FCC._attribute_condition_owner_blocks(tmp_path, rows, steps)
    r = _by_id(rows)["15.5ic"]
    assert (r.status, r.reason_class) == (_NM, _UPSTREAM), (r.status,
                                                             r.reason_class)
    assert r.cascade_note == "blocked-by-upstream(0.5ic)"


def test_the_condition_owner_writer_keeps_fail_when_no_blocker_failed(tmp_path):
    """Negative arm: 0.5ic PASSED but wrote no delivery-route declaration.
    Nothing 15.5ic waits on FAILED, so the row stays FAIL(missing_artefact)."""
    steps = _steps()
    rows = [_row("0.5ic", _PASS), _row(15, _PASS),
            _row("15.5ic", _NA, reasons=["condition not met: pad ring"])]
    FCC._attribute_condition_owner_blocks(tmp_path, rows, steps)
    r = _by_id(rows)["15.5ic"]
    assert (r.status, r.reason_class) == (_FAIL, _MISSING)


def test_the_void_pass_carries_no_dead_not_owed_arm():
    """`analyze()` raises violations only for PASS / PASS_WITH_WAIVERS
    terminals, so the VOID loop never receives a FAIL row and its 'NOT OWED'
    arm could never run. Dead arms are deleted, not left to be counted as
    coverage."""
    src = (PROGRAMS / "flow_compliance_check.py").read_text()
    assert "_was_missing_only" not in src
    assert 'f"NOT OWED: dependency' not in src


# ── finding 2: a demotion can never make a red run green ───────────────────

def _drive_main(tmp_path, monkeypatch, verdicts, extra=()):
    """Drive the real `main()` over the full canonical flow with every step's
    own verdict stubbed: PASS unless `verdicts` says otherwise."""
    proj = tmp_path / "proj"
    (proj / "rtl").mkdir(parents=True)
    (proj / "rtl" / "top.v").write_text(
        "module top(input a, output b); assign b = a; endmodule\n")
    tmpl = proj / "input" / "submission_template"
    tmpl.mkdir(parents=True)
    (tmpl / "NO_TEMPLATE.txt").write_text("IP/hardmacro delivery\n")

    def _check(_project, step, _waivers, **_kw):
        sid = step.get("id")
        status, rc = verdicts.get(str(sid), (_PASS, ""))
        return FCC.StepResult(id=sid, name=step.get("name", ""),
                              stage=step.get("stage", ""), status=status,
                              reason_class=rc,
                              reasons=([] if status == _PASS
                                       else [f"stub {status} {rc}"]))

    def _umbrella(_project, **kw):
        return (True, [], [], [])

    monkeypatch.setattr(FCC, "check_step", _check)
    monkeypatch.setattr(FCC, "_run_structural_rtl_gates", _umbrella)
    report = tmp_path / "report.json"
    rc = FCC.main([str(proj), "--json", str(report), "--strict", *extra])
    audit = json.loads((proj / "reports" / "audit" /
                        "phase23_completion_audit.json").read_text())
    return rc, json.loads(report.read_text()), audit


def test_an_owed_missing_step_behind_an_oss_root_keeps_the_run_red(
        tmp_path, monkeypatch, capsys):
    """Review scenario, the HIGH one: step 28 (OSS-blocked) fails its own gate,
    step 35 is owed and missing. At base the run is FAIL rc 1; on icslot66 the
    positional demotion removed 35 from `missing` and the promotion turned the
    run PASS_WITH_WAIVERS rc 0."""
    rc, report, _audit = _drive_main(tmp_path, monkeypatch, {
        "28": (_FAIL, ""), "35": (_FAIL, _MISSING)})
    capsys.readouterr()
    assert report["overall"] == _FAIL, report["overall"]
    assert rc == 1


def test_rows_demoted_behind_a_root_stay_on_the_must_close_list(
        tmp_path, monkeypatch, capsys):
    """Second shape: M1 fails its own gate, M2-M4 are not owed. The promotion
    may fire (every root is deferrable, as at base), and the promoted verdict's
    must-close list still names M2, M3 and M4."""
    rc, report, audit = _drive_main(tmp_path, monkeypatch, {
        "M1": (_FAIL, ""), "M2": (_FAIL, _MISSING), "M3": (_FAIL, _MISSING),
        "M4": (_FAIL, _MISSING)})
    capsys.readouterr()
    rows = {str(s["id"]): s for s in report["steps"]}
    for sid in ("M2", "M3", "M4"):
        assert rows[sid]["status"] == _NM, (sid, rows[sid]["status"])
    deferred = {str(d.get("step_id"))
                for d in audit["open_source_constraints_deferrals"]}
    assert {"M1", "M2", "M3", "M4"} <= deferred, deferred
    assert audit["verdict"] == _T.Verdict.PASS_WITH_WAIVERS.value


def test_a_demoted_row_the_promotion_cannot_defer_keeps_the_run_red(
        tmp_path, monkeypatch, capsys):
    """NEVER GREENER THAN BASE. A deferrable root (28, OSS-blocked) with a
    non-deferrable dependent that is not owed: at base that dependent sat in
    `missing` and refused the promotion, and a demotion must not change that.

    The canonical flow has no such dependent that is not ALSO an ancestor of a
    promotion prerequisite (6 / 39) -- those would be voided and refuse the
    promotion for an unrelated reason -- so the flow here is the canonical one
    plus ONE probe step, id 90, `blocks_on: [28]`."""
    flow = yaml.safe_load(_FLOW.read_text())
    stage28 = next(s for s in flow["steps"] if s.get("id") == 28)["stage"]
    flow["steps"].append({"id": 90, "name": "Probe consumer of step 28",
                          "stage": stage28, "blocks_on": [28]})
    flow_def = tmp_path / "flow_plus_probe.yaml"
    flow_def.write_text(yaml.safe_dump(flow, sort_keys=False))
    rc, report, _audit = _drive_main(tmp_path, monkeypatch, {
        "28": (_FAIL, ""), "90": (_FAIL, _MISSING)},
        extra=("--flow-def", str(flow_def)))
    capsys.readouterr()
    rows = {str(s["id"]): s for s in report["steps"]}
    assert rows["90"]["status"] == _NM, rows["90"]["status"]
    assert report["overall"] == _FAIL, report["overall"]
    assert rc == 1
