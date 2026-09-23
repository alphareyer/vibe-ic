"""The front door's roll-up, at its edges — through main() and through the guard.

R-0915-145. The conjunction rule itself stands (a FAILING completion audit over
passing phases is a FAIL). These are the four edges a pre-landing review confirmed,
each driven through the entry point rather than through a re-implementation.

H1  #505's DEMOTED ROW. The standalone shape (`--skip-phase3`, a phase1 failure
    that is purely doc-extraction coverage) demotes phase1 to COVERAGE-INCOMPLETE
    and KEEPS its rc, because a coverage-only phase1 always exits 1 -- the rc
    belongs to the verdict the row had BEFORE the demotion. The roll-up's "claims
    a pass but exited non-zero" rule is right for an UNDEMOTED row and turned this
    shape into FAIL/exit 1, where the base published PASS_WITH_WAIVERS/exit 0.
    The demotion now records itself, and only the #505 branch may mint the token.

M2  THE AUDIT AXIS MUST NOT FAIL OPEN. `if ca == "FAIL"` meant NOT_MEASURED, an
    unparseable value and an ABSENT audit all read as satisfied -- so a phase3 run
    whose --strict completion refresh raised inside a swallowed try published PASS
    at exit 0. Only PASS satisfies the axis now; waivers cap at the waiver tier;
    anything else, absence included, is NOT_MEASURED with the reason stated.

M3  AND IT MUST BE THIS RUN'S AUDIT. The reader took
    `reports/orchestrator/phase3_one_shot.json` unconditionally, so a leftover FAIL
    from an earlier full run gated a later `--skip-phase3` invocation that never
    touched phase3. A document counts only when phase3 ran in THIS invocation and
    the document was written at or after it started; an older one is named as
    ignored, not used.

L4  ONE IMPLEMENTATION. `vibe_ic_entry_guard` re-derived the verdict with its own
    copy of the old rule and its verdict set had no NOT_MEASURED, so a GENUINE
    report was discarded as forged and `benchmark_dispatch --score` could refuse a
    real run. The guard now replays the runner's own roll-up.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[2]
PROGRAMS = PLUGIN / "programs"
sys.path.insert(0, str(PROGRAMS))

import _path_layout as _pl                                   # noqa: E402
import vibe_ic_entry_guard as GUARD                          # noqa: E402
import vibe_ic_one_shot_runner as V                          # noqa: E402


@pytest.fixture()
def project(tmp_path_factory):
    p = Path(tempfile.mkdtemp(prefix="fdedge_",
                              dir=str(tmp_path_factory.mktemp("fd"))))
    rtl = p / "phase2/stage1/rtl"
    rtl.mkdir(parents=True)
    (rtl / "d.v").write_text("module d(); endmodule\n")
    return p


#: WHERE EACH PRODUCER REALLY WRITES, measured on spm run22 rather than assumed:
#:   reports/phase1_one_shot.json                 <- phase1's runner, NOT routed
#:   reports/orchestrator/phase2_one_shot.json
#:   reports/orchestrator/phase3_one_shot.json
#: `_pl.report_path` routes everything to `reports/orchestrator/`, and phase1's runner
#: does not use it. My earlier fixtures wrote through the router, so they staged phase1's
#: report where NO producer puts it — the tests passed while every real phase1-inclusive
#: run came out NOT_MEASURED. A fixture that writes somewhere no producer writes proves
#: nothing about the reader.
_FLAT_REPORTS = {"phase1_one_shot.json", "phase1_exit_reason.json"}


def _report(project: Path, name: str, payload: dict) -> Path:
    if name in _FLAT_REPORTS:
        path = project / "reports" / name
    else:
        path = _pl.report_path(project, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n")
    return path


def _drive_main(project: Path, monkeypatch, *, phase1, phase2, phase3=None,
                extra_argv=()) -> tuple:
    """Run the REAL main() with the phase subprocesses stubbed.

    Only `_run_phase` and the phase-1 entry decision are staged; every line of the
    roll-up, the axis reader and the report writer is the shipped one.
    """
    def fake_run_phase(label, runner, args, env=None):
        if "phase1" in runner.name:
            _report(project, "phase1_one_shot.json", {"verdict": phase1[0]})
            if phase1[2] is not None:
                # THE SIDECAR'S OWN PATH AND KEY, read from the consumer:
                # `_phase1_failure_is_coverage_only` opens
                # reports/phase1/phase1_exit_reason.json and asks for
                # `coverage_only_failure`. Writing it anywhere else, or under any
                # other key, stages a fixture the demotion branch cannot see.
                side = project / "reports" / "phase1" / "phase1_exit_reason.json"
                side.parent.mkdir(parents=True, exist_ok=True)
                side.write_text(json.dumps(phase1[2]) + "\n")
            return phase1[1]
        if "phase2" in runner.name:
            _report(project, "phase2_one_shot.json", {"verdict": phase2[0]})
            return phase2[1]
        if "phase3" in runner.name and phase3 is not None:
            _report(project, "phase3_one_shot.json", phase3[0])
            return phase3[1]
        return 0

    monkeypatch.setattr(V, "_run_phase", fake_run_phase)
    monkeypatch.setattr(V, "_phase1_decision", lambda *a, **k: (True, "docs"))
    monkeypatch.setattr(sys, "argv",
                        ["vibe_ic_one_shot_runner", str(project),
                         "--no-dashboard", "--skip-hardware", *extra_argv])
    rc = V.main()
    out = _pl.report_path(project, "vibe_ic_one_shot.json")
    return rc, json.loads(out.read_text())


# ── H1: the #505 standalone shape ──────────────────────────────────────────

def test_the_505_standalone_shape_is_pass_with_waivers_at_exit_zero(
        project, monkeypatch):
    """THE REGRESSION, through main(): coverage-only phase1 FAIL at rc 1, demoted,
    plus a passing phase2. On 99ae0fa6b this published FAIL and exited 1."""
    rc, doc = _drive_main(
        project, monkeypatch,
        phase1=("FAIL", 1, {"coverage_only_failure": True,
                            "coverage_pct": 71.0, "total_todo": 0}),
        phase2=("PASS", 0),
        extra_argv=("--skip-phase3",))
    assert doc["verdict"] == "PASS_WITH_WAIVERS", (
        doc["verdict"], doc.get("verdict_reasons"))
    assert rc == 0, rc
    phases = {r["name"]: r for r in doc["phases"]}
    assert phases["phase1"]["verdict"] == "COVERAGE-INCOMPLETE"
    assert phases["phase1"]["rc"] == 1, (
        "the row must KEEP the rc its process really returned; the demotion is "
        "recorded beside it, not by rewriting history")
    dem = doc["demoted_phases"]["phase1"]
    assert dem["token"] == V.DEMOTION_COVERAGE_ONLY
    assert dem["original_rc"] == 1 and dem["original_verdict"] == "FAIL"
    assert any("pre-demotion" in r for r in doc["verdict_reasons"]), (
        doc["verdict_reasons"])


def test_an_undemoted_row_claiming_a_pass_at_nonzero_rc_still_fails(
        project, monkeypatch):
    """The rule the demotion exemption must not launder: phase2 reports PASS and
    exits 1, with no demotion record. That is a report disagreeing with its own
    process and it still fails."""
    rc, doc = _drive_main(project, monkeypatch,
                          phase1=("PASS", 0, None), phase2=("PASS", 1),
                          extra_argv=("--skip-phase3",))
    assert doc["verdict"] == "FAIL", (doc["verdict"], doc["verdict_reasons"])
    assert rc == 1
    assert doc["demoted_phases"] == {}
    assert any("rc=1" in r for r in doc["verdict_reasons"])


def test_only_the_505_token_exempts_an_rc():
    """The exemption is keyed on ONE token minted at ONE site. A row carrying any
    other demotion record is not exempt — demotion is not a laundering path."""
    rows = [("phase1", "COVERAGE-INCOMPLETE", 1)]
    assert V._roll_up(rows, ["PASS"],
                      demoted={"phase1": {"token": V.DEMOTION_COVERAGE_ONLY}},
                      audit_axis={"state": "PASS"})[0] == "PASS_WITH_WAIVERS"
    for bogus in ({"token": "something_else"}, {}, {"token": ""}):
        assert V._roll_up(rows, ["PASS"], demoted={"phase1": bogus},
                          audit_axis={"state": "PASS"})[0] == "FAIL", bogus


# ── M2: the audit axis never passes on a token nobody taught it ────────────

def test_the_audit_axis_is_fail_closed_for_every_token():
    assert V._audit_axis_from_verdicts(["PASS"])["state"] == "PASS"
    assert V._audit_axis_from_verdicts(["FAIL"])["state"] == "FAIL"
    assert V._audit_axis_from_verdicts(["FAIL", "PASS"])["state"] == "FAIL"
    assert V._audit_axis_from_verdicts(
        ["PASS_WITH_WAIVERS"])["state"] == "PASS_WITH_WAIVERS"
    for token in ("NOT_MEASURED", "INSUFFICIENT_DATA", "", "greenish", None):
        axis = V._audit_axis_from_verdicts([token] if token is not None else [])
        assert axis["state"] == "NOT_MEASURED", (token, axis)
        assert axis["reason"], token
    assert V._audit_axis_from_verdicts(None)["state"] == "NOT_MEASURED"


def test_a_phase3_run_with_no_readable_audit_is_not_measured(project,
                                                             monkeypatch):
    """THE SWALLOWED-REFRESH CASE, through main(): phase3 ran and reported PASS,
    and no completion audit exists. On 99ae0fa6b that published PASS at exit 0."""
    rc, doc = _drive_main(project, monkeypatch,
                          phase1=("PASS", 0, None), phase2=("PASS", 0),
                          phase3=({"verdict": "PASS"}, 0))
    assert doc["completion_audit_axis"]["state"] == "NOT_MEASURED", (
        doc["completion_audit_axis"])
    assert doc["verdict"] == "NOT_MEASURED", (
        doc["verdict"], doc["verdict_reasons"])
    assert rc == 1
    assert any("completion audit" in r for r in doc["verdict_reasons"])


def test_a_failing_audit_still_fails_a_fully_passing_run(project, monkeypatch):
    """The refutation that stands: the conjunction includes the audit."""
    def _phase3(payload, code):
        return payload, code
    monkeypatch.setattr(V, "_phase1_decision", lambda *a, **k: (True, "docs"))
    # the audit document is written by phase3's stub so it belongs to this run
    def fake_run_phase(label, runner, args, env=None):
        if "phase1" in runner.name:
            _report(project, "phase1_one_shot.json", {"verdict": "PASS"})
            return 0
        if "phase2" in runner.name:
            _report(project, "phase2_one_shot.json", {"verdict": "PASS"})
            return 0
        if "phase3" in runner.name:
            _report(project, "phase3_one_shot.json", {"verdict": "PASS"})
            _report(project, "phase23_completion_audit.json", {"verdict": "FAIL"})
            return 0
        return 0
    monkeypatch.setattr(V, "_run_phase", fake_run_phase)
    monkeypatch.setattr(sys, "argv", ["vibe_ic_one_shot_runner", str(project),
                                      "--no-dashboard", "--skip-hardware"])
    rc = V.main()
    doc = json.loads(_pl.report_path(project, "vibe_ic_one_shot.json").read_text())
    assert doc["completion_audit_axis"]["state"] == "FAIL"
    assert doc["verdict"] == "FAIL" and rc == 1
    assert any("completion audit" in r for r in doc["verdict_reasons"])


# ── M3: the axis belongs to THIS invocation ────────────────────────────────

def test_a_leftover_audit_from_an_earlier_run_does_not_gate_this_one(
        project, monkeypatch):
    """A `--skip-phase3` invocation over a tree that still carries an earlier full
    run's FAILING audit. On 99ae0fa6b that published FAIL; the base read PASS. This
    run has no audit axis at all, and says so."""
    old = _report(project, "phase23_completion_audit.json", {"verdict": "FAIL"})
    _report(project, "phase3_one_shot.json",
            {"verdict": "FAIL", "completion_audit_verdict": "FAIL"})
    import os
    stale = os.stat(old).st_mtime_ns - 10 ** 10
    for rel in ("phase23_completion_audit.json", "phase3_one_shot.json"):
        os.utime(_pl.report_path(project, rel), ns=(stale, stale))

    rc, doc = _drive_main(project, monkeypatch,
                          phase1=("PASS", 0, None), phase2=("PASS", 0),
                          extra_argv=("--skip-phase3",))
    assert doc["completion_audit_axis"]["state"] == "NOT_APPLICABLE", (
        doc["completion_audit_axis"])
    assert doc["verdict"] == "PASS", (doc["verdict"], doc["verdict_reasons"])
    assert rc == 0
    assert any("no phase2/3 completion audit axis" in r
               for r in doc["verdict_reasons"]), doc["verdict_reasons"]


def test_a_document_older_than_this_invocation_is_named_as_ignored(project):
    """The axis reader, directly: phase3 ran, but the only audit on disk predates
    this invocation. It is IGNORED and the axis is NOT_MEASURED — never PASS, and
    never that document's verdict either."""
    import os
    _report(project, "phase23_completion_audit.json", {"verdict": "PASS"})
    path = _pl.report_path(project, "phase23_completion_audit.json")
    stale = os.stat(path).st_mtime_ns - 10 ** 10
    os.utime(path, ns=(stale, stale))
    axis = V._completion_audit_axis(project, phase3_ran=True,
                                    started_at=os.stat(path).st_mtime + 5)
    assert axis["state"] == "NOT_MEASURED", axis
    assert axis["ignored"] == [
        "reports/audit/phase23_completion_audit.json"], axis
    assert "earlier run" in axis["reason"], axis
    assert axis["sources"] == []


def test_no_phase3_means_no_axis_not_a_satisfied_one(project):
    _report(project, "phase23_completion_audit.json", {"verdict": "PASS"})
    axis = V._completion_audit_axis(project, phase3_ran=False, started_at=0.0)
    assert axis["state"] == "NOT_APPLICABLE", axis
    assert "did not run in this invocation" in axis["reason"]


# ── L4: the guard replays the runner's roll-up ─────────────────────────────

def _orchestrator_report(verdict: str, phases: list, **extra) -> dict:
    doc = {"phase": "vibe-ic", "verdict": verdict, "phases": phases}
    doc.update(extra)
    return doc


def test_the_guard_accepts_a_genuine_new_rule_report():
    """A report that is FAIL because its own completion audit failed, with every
    phase passing. The guard's old copy of the rule derived PASS and discarded it
    as forged."""
    phases = [{"name": "phase1", "verdict": "PASS", "rc": 0},
              {"name": "phase2", "verdict": "PASS", "rc": 0},
              {"name": "analog", "verdict": "SKIPPED", "rc": 0},
              {"name": "phase3", "verdict": "PASS", "rc": 0},
              {"name": "mixed_signal", "verdict": "SKIPPED", "rc": 0}]
    data = _orchestrator_report(
        "FAIL", phases, completion_audit_axis={"state": "FAIL", "reason": "x"},
        completion_audit_verdicts=["FAIL"], demoted_phases={})
    assert GUARD._expected_front_door_verdict(data, phases) == "FAIL"


def test_the_guard_rejects_a_forged_pass_over_a_failing_audit():
    phases = [{"name": "phase1", "verdict": "PASS", "rc": 0},
              {"name": "phase2", "verdict": "PASS", "rc": 0},
              {"name": "analog", "verdict": "SKIPPED", "rc": 0},
              {"name": "phase3", "verdict": "PASS", "rc": 0},
              {"name": "mixed_signal", "verdict": "SKIPPED", "rc": 0}]
    data = _orchestrator_report(
        "PASS", phases, completion_audit_axis={"state": "FAIL", "reason": "x"},
        completion_audit_verdicts=["FAIL"], demoted_phases={})
    assert GUARD._expected_front_door_verdict(data, phases) != "PASS"


def test_not_measured_is_a_front_door_verdict_the_guard_knows():
    assert "NOT_MEASURED" in GUARD._ORCHESTRATOR_VERDICTS, (
        "a genuine NOT_MEASURED report is discarded before any derivation runs")


def test_the_guard_holds_a_legacy_report_to_the_rule_it_was_published_under():
    """A report from before these fields existed carries no axis. It is judged by
    the old derivation — exactly as trusted as it was, and no more — because a
    guard cannot fairly hold a document to a rule it could not have followed."""
    phases = [{"name": "phase1", "verdict": "PASS", "rc": 0},
              {"name": "phase2", "verdict": "PASS", "rc": 0},
              {"name": "analog", "verdict": "SKIPPED", "rc": 0},
              {"name": "phase3", "verdict": "PASS", "rc": 0},
              {"name": "mixed_signal", "verdict": "SKIPPED", "rc": 0}]
    legacy = _orchestrator_report("PASS", phases)
    assert "completion_audit_axis" not in legacy
    assert GUARD._expected_front_door_verdict(legacy, phases) == "PASS"


def test_the_guard_does_not_keep_its_own_copy_of_the_rule():
    """SOURCE PIN, on the thing that drifted: the guard must delegate to the
    runner's roll-up for a report carrying its inputs."""
    src = (PROGRAMS / "vibe_ic_entry_guard.py").read_text()
    assert "_roll_up(" in src, (
        "the guard no longer calls the runner's roll-up; a second copy of the "
        "conjunction rule is what discarded genuine reports as forged")
    assert "import vibe_ic_one_shot_runner" in src


def test_main_always_supplies_the_axis():
    """THE PIN THAT KEEPS THE STRICT PATH FROM BEING BYPASSED.

    `_roll_up` applies the fail-closed audit rule only when an axis is SUPPLIED,
    because a caller passing none is not talking about the completion audit (#505's
    properties are stated that way). That scoping is only safe while the real
    front door always supplies one — so this asserts main's call site does, by AST,
    on the keyword rather than on surrounding text.
    """
    import ast
    tree = ast.parse((PROGRAMS / "vibe_ic_one_shot_runner.py").read_text())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "_roll_up"]
    assert calls, "main no longer rolls up through `_roll_up`"
    kwargs = [{k.arg for k in c.keywords} for c in calls]
    assert any("audit_axis" in kw and "demoted" in kw for kw in kwargs), (
        f"main's roll-up call does not supply `audit_axis`/`demoted`, so the "
        f"fail-closed audit rule and the #505 demotion exemption are both "
        f"unreachable on a real run; keywords seen: {kwargs}")
    # and the axis it supplies is the one tied to this invocation
    src = (PROGRAMS / "vibe_ic_one_shot_runner.py").read_text()
    assert "_completion_audit_axis(" in src and "phase3_ran=_phase3_ran" in src
    # PHASE 3's start, not the run's: a document written before phase3 began
    # cannot be phase3's refresh of it.
    assert 'started_at=_phase_started.get("phase3", t0)' in src


# ── R-0915-151: freshness on the CARRIED verdict, and on the rows ───────────

def _audit(project: Path, verdict: str, *, scope: dict = None,
           phase: str = "all") -> Path:
    doc = {"verdict": verdict, "phase": phase}
    if scope is not None:
        doc["scope"] = scope
    return _report(project, "phase23_completion_audit.json", doc)


WHOLE = {"whole_flow": True, "step_count": 70, "flow_step_total": 70}


def test_a_stale_audit_pass_repeated_by_a_fresh_carrier_is_not_measured(
        project, monkeypatch):
    """THE HIGH, through main(). phase3's finalize refresh swallows a TimeoutExpired,
    and `_derive_headline_verdict` then copies the OLD audit's verdict into a FRESH
    phase3_one_shot.json. My round-2 reader put the audit FILE in `ignored` and then
    accepted the SAME verdict from that carrier — axis PASS, exit 0.

    Here the audit says PASS and predates phase3; the carrier repeats it and is
    today's. The axis must be NOT_MEASURED, and the carrier must appear as a POINTER
    that was not used as evidence.
    """
    import os
    stale = _audit(project, "PASS", scope=dict(WHOLE))
    old = os.stat(stale).st_mtime_ns - 10 ** 10
    os.utime(stale, ns=(old, old))

    def fake_run_phase(label, runner, args, env=None):
        if "phase1" in runner.name:
            _report(project, "phase1_one_shot.json", {"verdict": "PASS"})
            return 0
        if "phase2" in runner.name:
            _report(project, "phase2_one_shot.json", {"verdict": "PASS"})
            return 0
        if "phase3" in runner.name:
            # the swallowed refresh: a FRESH report carrying the OLD audit's verdict
            _report(project, "phase3_one_shot.json",
                    {"verdict": "PASS", "completion_audit_verdict": "PASS"})
            return 0
        return 0

    monkeypatch.setattr(V, "_run_phase", fake_run_phase)
    monkeypatch.setattr(V, "_phase1_decision", lambda *a, **k: (True, "docs"))
    monkeypatch.setattr(sys, "argv", ["vibe_ic_one_shot_runner", str(project),
                                      "--no-dashboard", "--skip-hardware"])
    rc = V.main()
    doc = json.loads(_pl.report_path(project, "vibe_ic_one_shot.json").read_text())
    axis = doc["completion_audit_axis"]
    assert axis["state"] == "NOT_MEASURED", axis
    assert axis["sources"] == [], axis
    assert "reports/audit/phase23_completion_audit.json" in axis["ignored"], axis
    assert any(p["carries"] == "PASS" and p["used_as_evidence"] is False
               for p in axis["pointers"]), axis
    assert doc["verdict"] == "NOT_MEASURED" and rc == 1


def test_a_stale_audit_fail_does_not_gate_a_good_run(project, monkeypatch):
    """THE MIRROR, which matters just as much: a stale FAIL must not gate a run it
    never measured. It is still NOT_MEASURED — never FAIL, and never PASS."""
    import os
    stale = _audit(project, "FAIL", scope=dict(WHOLE))
    old = os.stat(stale).st_mtime_ns - 10 ** 10
    os.utime(stale, ns=(old, old))

    def fake_run_phase(label, runner, args, env=None):
        for name in ("phase1", "phase2", "phase3"):
            if name in runner.name:
                _report(project, f"{name}_one_shot.json", {"verdict": "PASS"})
        return 0

    monkeypatch.setattr(V, "_run_phase", fake_run_phase)
    monkeypatch.setattr(V, "_phase1_decision", lambda *a, **k: (True, "docs"))
    monkeypatch.setattr(sys, "argv", ["vibe_ic_one_shot_runner", str(project),
                                      "--no-dashboard", "--skip-hardware"])
    V.main()
    doc = json.loads(_pl.report_path(project, "vibe_ic_one_shot.json").read_text())
    assert doc["completion_audit_axis"]["state"] == "NOT_MEASURED"
    assert doc["verdict"] == "NOT_MEASURED", doc["verdict"]


def test_a_scoped_audit_is_not_this_runs_axis(project):
    """THE MEDIUM: who wrote the audit, over what population. R-0915-147 gives the
    document a scope, so a phase-2-scoped audit or a pre-Step-29 snapshot is no
    longer accepted as the phase2/3 axis."""
    _audit(project, "PASS", scope={"whole_flow": False, "step_count": 13,
                                   "flow_step_total": 70, "stage_id": "stage2"})
    axis = V._completion_audit_axis(project, phase3_ran=True, started_at=0.0)
    assert axis["state"] == "NOT_MEASURED", axis
    assert "13 of 70" in axis["reason"], axis["reason"]


def test_a_whole_flow_fresh_audit_is_the_axis(project):
    """THE OTHER DIRECTION: the document that IS this run's whole-flow audit is
    accepted, and says which signal established that."""
    _audit(project, "PASS", scope=dict(WHOLE))
    axis = V._completion_audit_axis(project, phase3_ran=True, started_at=0.0)
    assert axis["state"] == "PASS", axis
    assert axis["scope_signal"] == "scope.whole_flow"
    assert axis["sources"][0]["verdict"] == "PASS"


def test_an_audit_without_a_scope_block_is_taken_on_the_weaker_signal(project):
    """A document predating R-0915-147 cannot state its population. It is accepted
    only on `phase == "all"` AND the weaker signal is DISCLOSED, because a weak
    signal read silently is the same mistake in a quieter voice."""
    _audit(project, "PASS", phase="all")
    axis = V._completion_audit_axis(project, phase3_ran=True, started_at=0.0)
    assert axis["state"] == "PASS", axis
    assert "weaker signal" in axis["scope_signal"], axis["scope_signal"]
    _audit(project, "PASS", phase="2")
    axis2 = V._completion_audit_axis(project, phase3_ran=True, started_at=0.0)
    assert axis2["state"] == "NOT_MEASURED", axis2


def test_a_phase_that_left_no_report_is_not_measured(project, monkeypatch):
    """THE ROW SIDE of the same rule. phase3's `[SKIP] no usable PDK` returns 0
    WITHOUT writing a report, and the front door then read a stale
    phase3_one_shot.json or invented PASS from rc 0 — for a phase that measured
    nothing."""
    def fake_run_phase(label, runner, args, env=None):
        if "phase3" in runner.name:
            return 0                      # rc 0 and NO report, the shipped shape
        for name in ("phase1", "phase2"):
            if name in runner.name:
                _report(project, f"{name}_one_shot.json", {"verdict": "PASS"})
        return 0

    monkeypatch.setattr(V, "_run_phase", fake_run_phase)
    monkeypatch.setattr(V, "_phase1_decision", lambda *a, **k: (True, "docs"))
    monkeypatch.setattr(sys, "argv", ["vibe_ic_one_shot_runner", str(project),
                                      "--no-dashboard", "--skip-hardware"])
    rc = V.main()
    doc = json.loads(_pl.report_path(project, "vibe_ic_one_shot.json").read_text())
    rows = {r["name"]: r["verdict"] for r in doc["phases"]}
    assert rows["phase3"] == "NOT_MEASURED", (rows, doc["verdict"])
    assert doc["verdict"] == "NOT_MEASURED" and rc == 1
    assert any("left no phase3_one_shot.json" in a for a in doc["advisories"]), (
        doc["advisories"])


def test_a_stale_phase_report_is_not_this_runs_row(project, monkeypatch):
    """And a report from an EARLIER run does not become this phase's verdict."""
    import os
    stale = _report(project, "phase3_one_shot.json", {"verdict": "PASS"})
    old = os.stat(stale).st_mtime_ns - 10 ** 10
    os.utime(stale, ns=(old, old))

    def fake_run_phase(label, runner, args, env=None):
        if "phase3" in runner.name:
            return 0                      # leaves the stale report in place
        for name in ("phase1", "phase2"):
            if name in runner.name:
                _report(project, f"{name}_one_shot.json", {"verdict": "PASS"})
        return 0

    monkeypatch.setattr(V, "_run_phase", fake_run_phase)
    monkeypatch.setattr(V, "_phase1_decision", lambda *a, **k: (True, "docs"))
    monkeypatch.setattr(sys, "argv", ["vibe_ic_one_shot_runner", str(project),
                                      "--no-dashboard", "--skip-hardware"])
    V.main()
    doc = json.loads(_pl.report_path(project, "vibe_ic_one_shot.json").read_text())
    rows = {r["name"]: r["verdict"] for r in doc["phases"]}
    assert rows["phase3"] == "NOT_MEASURED", rows
    assert any("predates phase3's start" in a for a in doc["advisories"]), (
        doc["advisories"])


def test_a_stale_coverage_sidecar_does_not_demote(project, monkeypatch):
    """M/L: my H1 exemption rests on `_phase1_failure_is_coverage_only`, whose
    sidecar is not tied to this invocation and is written BEFORE three later
    non-coverage blocking returns. A stale sidecar must not demote a phase1 FAIL."""
    import os
    side = project / "reports" / "phase1" / "phase1_exit_reason.json"
    side.parent.mkdir(parents=True, exist_ok=True)
    side.write_text(json.dumps({"coverage_only_failure": True,
                                "coverage_pct": 71.0, "total_todo": 0}) + "\n")
    old = os.stat(side).st_mtime_ns - 10 ** 10
    os.utime(side, ns=(old, old))

    rc, doc = _drive_main(project, monkeypatch,
                          phase1=("FAIL", 1, None), phase2=("PASS", 0),
                          extra_argv=("--skip-phase3",))
    assert doc["demoted_phases"] == {}, doc["demoted_phases"]
    assert doc["verdict"] == "FAIL" and rc == 1
    assert any("predates phase1's start" in a for a in doc["advisories"]), (
        doc["advisories"])


def test_only_rc_1_is_exempt_from_the_demotion(project, monkeypatch):
    """The coverage-only path returns rc 1. Any other rc is a different failure, and
    a fresh sidecar does not make it one."""
    rc, doc = _drive_main(
        project, monkeypatch,
        phase1=("FAIL", 2, {"coverage_only_failure": True, "coverage_pct": 71.0,
                            "total_todo": 0}),
        phase2=("PASS", 0), extra_argv=("--skip-phase3",))
    assert doc["demoted_phases"] == {}, doc["demoted_phases"]
    assert doc["verdict"] == "FAIL" and rc == 1
    assert any("not the rc the" in a for a in doc["advisories"]), doc["advisories"]


def test_a_blocking_return_the_classifier_did_not_classify_drops_the_sidecar():
    """THE OTHER HALF OF THE DEMOTION, in phase1's own runner.

    The sidecar is written BEFORE three later blocking returns — the L8 clock-period
    conflict, the extraction gap, the semantic layer gates — and its classifier knows
    nothing about any of them. All three `return 1`, the same rc the coverage-only path
    returns, so no reader can separate them by exit code: the #505 demotion would take
    a NON-coverage phase1 failure and publish PASS_WITH_WAIVERS at exit 0. My rc==1
    guard alone does NOT close that, which is why this half is here.

    Each of those returns now removes the sidecar it would otherwise leave. Pinned by
    AST on the call, and by position: the drop must precede the return it guards.
    """
    import ast
    src = (PROGRAMS / "phase1_doc_one_shot_runner.py").read_text()
    assert src.count("_drop_v0_3_7_exit_reason(project)") >= 3, (
        "fewer than three unclassified blocking returns drop the sidecar")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef)
              and n.name == "_drop_v0_3_7_exit_reason")
    assert "unlink" in ast.unparse(fn), "the drop does not remove anything"
    # every drop sits immediately before a `return 1`, never after it
    lines = src.splitlines()
    for i, line in enumerate(lines):
        if "_drop_v0_3_7_exit_reason(project)" in line and "def " not in line:
            nxt = next((l.strip() for l in lines[i + 1:i + 3] if l.strip()), "")
            assert nxt.startswith("return"), (i + 1, nxt)


def test_phase1s_report_is_read_where_its_producer_writes(project, monkeypatch):
    """THE ARM THAT WOULD HAVE CAUGHT MY ROUND-3 REGRESSION.

    `_pl.report_path` routes to `reports/orchestrator/`; phase1's runner writes
    `reports/phase1_one_shot.json` and does not use the router. Reading the routed path
    meant EVERY phase1-inclusive run came out NOT_MEASURED at exit 1 — and a real phase1
    FAIL became NOT_MEASURED, so the halt never fired and phase2/3 ran on bad L
    documents. The base hid the same mismatch behind the rc fallback.

    This stages phase1's report at the producer's OWN path and requires the row to be
    the verdict in it.
    """
    def fake_run_phase(label, runner, args, env=None):
        if "phase1" in runner.name:
            # the producer's real path, not the router's
            flat = project / "reports" / "phase1_one_shot.json"
            flat.parent.mkdir(parents=True, exist_ok=True)
            flat.write_text(json.dumps({"verdict": "PASS"}) + "\n")
            return 0
        if "phase2" in runner.name:
            _report(project, "phase2_one_shot.json", {"verdict": "PASS"})
            return 0
        return 0

    monkeypatch.setattr(V, "_run_phase", fake_run_phase)
    monkeypatch.setattr(V, "_phase1_decision", lambda *a, **k: (True, "docs"))
    monkeypatch.setattr(sys, "argv", ["vibe_ic_one_shot_runner", str(project),
                                      "--no-dashboard", "--skip-hardware",
                                      "--skip-phase3"])
    rc = V.main()
    doc = json.loads(_pl.report_path(project, "vibe_ic_one_shot.json").read_text())
    rows = {r["name"]: r["verdict"] for r in doc["phases"]}
    assert rows["phase1"] == "PASS", (rows, doc["verdict"], doc["advisories"])
    assert doc["verdict"] == "PASS" and rc == 0, (doc["verdict"], doc["verdict_reasons"])


def test_the_reader_finds_each_report_where_it_actually_lives(project):
    """MEASURED on spm run22, and it is not one convention: phase1's report is at
    `reports/`, phase2's and phase3's under `reports/orchestrator/`. The reader checks
    both and the newer wins, so neither layout is assumed."""
    flat = project / "reports" / "phase1_one_shot.json"
    flat.parent.mkdir(parents=True, exist_ok=True)
    flat.write_text("{}\n")
    assert V._phase_report_path(project, "phase1_one_shot.json") == flat

    routed = _pl.report_path(project, "phase2_one_shot.json")
    routed.parent.mkdir(parents=True, exist_ok=True)
    routed.write_text("{}\n")
    assert V._phase_report_path(project, "phase2_one_shot.json") == routed

    # with neither present, the canonical place is named so the message points there
    assert V._phase_report_path(project, "phase3_one_shot.json") == \
        _pl.report_path(project, "phase3_one_shot.json")


def test_phase1s_producer_still_writes_the_flat_path():
    """The premise of the two arms above, pinned against the producer itself: if phase1
    moves its report under the router, this fixture and that reader must move with it."""
    src = (PROGRAMS / "phase1_one_shot_runner.py").read_text()
    assert 'out = reports / "phase1_one_shot.json"' in src, (
        "phase1's runner no longer writes reports/phase1_one_shot.json; re-measure "
        "where it writes and update `_phase_report_path` and these fixtures together")
