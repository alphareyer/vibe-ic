#!/usr/bin/env python3
"""vibe-ic#2134 — one design, one STA answer in the sign-off summary.

THE OBSERVED DEFECT, reproduced here on the #544 fixture rather than described.
On one run's sign-off timing, three declared sign-off gates gave three answers:
`sta_signoff` PASSed while its OWN report disclosed `STA_SINGLE_CORNER_ONLY`,
and `sta_corner` and `sta_record` FAILed on the multi-corner axis that PASS
never covered. The roll-up then counted the tiered PASS in its numerator, so
the published record said the same design both passed and failed sign-off STA.

THE CONTRACT CHOICE IS (a) — `sta_signoff`'s verdict becomes a non-PASS tier
when its coverage is single-corner and a declared multi-corner sign-off gate
FAILs — and the reason it is (a) rather than (b) is argued at the fix, above
`_reconcile_sta_verdict`. What is asserted here is the behaviour, both
directions:

  * the demotion fires on the measured shape, and the roll-up numerator drops;
  * NOTHING ELSE MOVES: `sta_corner` and `sta_record` still FAIL with their own
    reasons, the gate still writes its own report, and a clean run — where the
    multi-corner gates agree — still PASSes with the full numerator.

THE MUTATION #2134 ASKS FOR is `test_mutation_*`: with the reconciliation
removed, `sta_signoff` PASSes beside a FAILing `sta_corner` and the roll-up
line counts it. That test asserts the pre-fix state IS reachable when the fix
is disabled, so a fix that could not fail is not mistaken for one that holds.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROGRAMS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# The #544 fixture is REUSED, not re-authored: it is the tool-authentic
# post-route project this defect was measured on, and a second copy of it
# would be a second thing to keep true.
import test_issue544_declared_signoff_gate_not_checked as _f    # noqa: E402

runner = _f.runner            # noqa: F811  (the fixture, by reference)

_SIGNOFF = "sta_signoff"


def _run(runner, tmp_path, **kw):
    proj = _f._project(tmp_path / "proj", **kw)
    rows = runner.step_declared_signoff_gates(proj)
    return proj, rows, {r.name: r for r in rows}


# ===========================================================================
# (1) THE DEMOTION — the measured shape
# ===========================================================================
def test_a_disclosed_single_corner_pass_defers_to_a_failing_multicorner_gate(
        tmp_path, runner):
    proj, rows, res = _run(runner, tmp_path, violated_corner=True)

    # The premise: the multi-corner authorities really did refuse, with their
    # own reasons, unchanged by this fix.
    assert res["sta_corner"].status == "FAIL", res["sta_corner"]
    assert res["sta_record"].status == "FAIL", res["sta_record"]
    assert "VIOLATED" in res["sta_corner"].detail, res["sta_corner"].detail
    assert "VIOLATED" in res["sta_record"].detail, res["sta_record"].detail

    # The gate's own report still discloses its coverage, and still exists.
    doc = json.loads(
        (proj / "reports/phase3/sta/post_route_summary.json").read_text())
    assert any(f.get("rule") == runner._STA_SINGLE_CORNER_RULE
               for f in (doc.get("findings") or [])), doc.get("findings")

    row = res[_SIGNOFF]
    assert row.status == "BLOCKED", (
        "a disclosed SINGLE-CORNER result was rendered as a sign-off STA PASS "
        "beside two gates refusing the same timing", row)
    assert runner._SIGNOFF_NOT_CHECKED in row.detail, row.detail
    assert "DEFERRED-TO-" in row.detail, row.detail
    for name in ("sta_corner", "sta_record"):
        assert name in row.detail, (name, row.detail)


def test_the_rollup_numerator_cannot_count_a_tiered_pass(tmp_path, runner):
    """The `N of M declared gates PASSED` line is the consumer #2134 names."""
    _proj, rows, res = _run(runner, tmp_path, violated_corner=True)
    rollup = runner.declared_signoff_rollup(rows)
    assert _SIGNOFF not in rollup["passed"], rollup
    assert _SIGNOFF in rollup["not_checked"], rollup
    assert _SIGNOFF not in rollup["failed"], rollup
    assert runner._SIGNOFF_NOT_CHECKED in rollup["line"], rollup["line"]
    assert f"{len(rollup['passed'])} of {rollup['declared']}" in rollup["line"]


def test_the_deferred_row_is_not_green_to_the_aggregate(tmp_path, runner):
    """BLOCKED, not SKIP: `_aggregate_verdict` folds SKIP into
    PASS_WITH_WAIVERS, which is the whole of #544."""
    _proj, rows, _res = _run(runner, tmp_path, violated_corner=True)
    assert runner._aggregate_verdict(rows) == "FAIL", [
        (r.name, r.status) for r in rows]


def test_the_deferred_gate_still_wrote_its_own_report(tmp_path, runner):
    """A demotion that short-circuited the gate would deliver less evidence,
    not more. The gate RAN; only the verdict slot it occupies changed."""
    proj, _rows, res = _run(runner, tmp_path, violated_corner=True)
    out = proj / "reports/phase3/sta/post_route_summary.json"
    assert out.is_file(), out
    assert str(out) in res[_SIGNOFF].output_files, res[_SIGNOFF].output_files


# ===========================================================================
# (2) THE CONTROLS — what must NOT move
# ===========================================================================
def test_a_clean_run_still_passes_with_the_full_numerator(tmp_path, runner):
    """No multi-corner gate refuses, so nothing defers. A rule that demoted
    here would make every single-corner run unreleasable, which #2134 does not
    ask for and this flow does not mean."""
    _proj, rows, res = _run(runner, tmp_path)
    assert res[_SIGNOFF].status == "PASS", res[_SIGNOFF]
    rollup = runner.declared_signoff_rollup(rows)
    assert rollup["not_checked"] == [], rollup
    assert _SIGNOFF in rollup["passed"], rollup


def test_a_real_signoff_fail_is_still_a_fail_never_a_deferral(tmp_path,
                                                              runner):
    """FAIL and NOT-CHECKED are different words about a design. A `sta_signoff`
    that RAN and found a violation in its own report keeps saying so."""
    _proj, _rows, res = _run(runner, tmp_path, violated_corner=True,
                             violated_own_report=True)
    assert res[_SIGNOFF].status == "FAIL", res[_SIGNOFF]


def test_the_reconciliation_never_promotes_anything(runner):
    """Direction check on the transform itself: over every status this module
    plans, no row's status improves and only `sta_signoff` can change."""
    R = runner
    mk = R.StepResult
    for status in ("PASS", "FAIL", "BLOCKED", "SKIP", "ENV_UNAVAILABLE"):
        rows = [mk("sta_signoff", status), mk("sta_corner", "FAIL"),
                mk("sta_record", "FAIL")]
        out = {r.name: r.status for r in R._reconcile_sta_verdict(rows)}
        assert out["sta_corner"] == "FAIL" and out["sta_record"] == "FAIL", out
        # No output json on these synthetic rows, so the disclosure is absent
        # and nothing may be demoted on a guess.
        assert out["sta_signoff"] == status, out


def test_an_unreadable_report_is_not_evidence_of_partial_coverage(tmp_path,
                                                                  runner):
    """"Could not read it" is not "read it and it disclosed single-corner"."""
    R = runner
    row = R.StepResult("sta_signoff", "PASS", 0.0, "",
                       [str(tmp_path / "absent.json")])
    rows = [row, R.StepResult("sta_corner", "FAIL")]
    assert R._reconcile_sta_verdict(rows)[0].status == "PASS"


# ===========================================================================
# (3) THE MUTATION — the pre-fix state must be reachable
# ===========================================================================
def test_mutation_without_the_reconciliation_the_defect_returns(
        tmp_path, runner, monkeypatch):
    """#2134's mutation, run as a mutation: disable the reconciliation and the
    contradictory PASS comes back and is counted. A check that cannot fail is
    not a check."""
    monkeypatch.setattr(runner, "_reconcile_sta_verdict", lambda rows: rows)
    _proj, rows, res = _run(runner, tmp_path, violated_corner=True)
    assert res[_SIGNOFF].status == "PASS", (
        "the mutation did not reach the code under test — this test proves "
        "nothing until it does", res[_SIGNOFF])
    rollup = runner.declared_signoff_rollup(rows)
    assert _SIGNOFF in rollup["passed"], rollup
    assert rollup["failed"] == ["sta_corner", "sta_record"], rollup


# ===========================================================================
# (4) THE CONSUMERS OF THE SIGN-OFF SUMMARY LINE
# ===========================================================================
def test_every_consumer_of_the_signoff_line_reads_the_reconciled_rows():
    """The roll-up has exactly two consumers — the published
    `phase3_one_shot.json` key and the console line — and both are computed
    from `plan`, which carries the reconciled rows because
    `step_declared_signoff_gates` returns them. Asserted on the source so a
    third consumer, or a call site that bypasses the reconciliation, is caught
    here rather than in a run."""
    src = (_PROGRAMS / "phase3_one_shot_runner.py").read_text(errors="replace")
    assert "return _reconcile_sta_verdict(out)" in src, (
        "step_declared_signoff_gates no longer returns reconciled rows")
    assert src.count("step_declared_signoff_gates(project") >= 1
    assert '"declared_signoff_gates": signoff_rollup' in src
    assert 'print(f"sign-off: {signoff_rollup[\'line\']}")' in src
