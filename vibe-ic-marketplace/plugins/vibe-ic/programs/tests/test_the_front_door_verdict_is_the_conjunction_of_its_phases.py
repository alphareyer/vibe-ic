"""The front door's verdict is the conjunction of every phase AND the completion
audit. A NOT_MEASURED phase can never roll up to PASS.

MEASURED on spm run22, READ-ONLY, `reports/orchestrator/vibe_ic_one_shot.json`,
byte-exact:

    phases : phase1 PASS rc=0
             phase2 NOT_MEASURED rc=1
             phase3 NOT_MEASURED rc=1
             analog SKIPPED rc=0 | mixed_signal SKIPPED rc=0
    verdict: PASS                          <- and the process exited 0

while, in the same run tree:

    reports/audit/phase23_completion_audit.json          verdict FAIL
    reports/orchestrator/phase3_one_shot.json            completion_audit_verdict FAIL

So the one line a reader looks at first said PASS about a run in which neither
digital phase was measured and the completion audit had already said FAIL. THREE
independent holes produced it, and the fix closes each; this file holds one arm
per hole plus the mutation that proves the arm bites.

1. NOT_MEASURED had no branch. The old body was `if any FAIL -> FAIL`, then the
   waiver-ish tier, then `return "PASS"` -- so every token outside those two sets
   reached PASS by falling off the end of the function. The fix inverts the
   polarity: PASS is a MEMBERSHIP test against `_PHASE_PASS` /
   `_PHASE_PASS_WITH_NOTE`, so a token nobody taught the roll-up is not measured,
   never a pass. That is the difference between a list of failures to enumerate
   (never complete) and a list of successes to admit (checkable).

2. `rc` was carried in the plan and in the published report and read by nothing.
   A phase reporting PASS while exiting non-zero is a report disagreeing with its
   own process; it is now a FAIL naming both halves.

3. The completion audit was outside the conjunction. It is now read from its own
   document AND from whichever phase report carries `completion_audit_verdict`:
   either saying FAIL fails the roll-up, so a stale disagreement can only make the
   front door stricter. An ABSENT audit is disclosed and NOT gating -- a
   phase1-only or phase2-only run legitimately has none, and failing those would
   be a different lie.

The verdict also stopped being silent about itself: `verdict_reasons` in the
report and a `because` line per reason on stdout, because a roll-up that moves a
verdict without naming the phase that moved it has only relocated the silence.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
PROGRAMS = PLUGIN / "programs"
sys.path.insert(0, str(PROGRAMS))

import vibe_ic_one_shot_runner as V                            # noqa: E402

#: run22's own rows, as its published report carries them.
RUN22_ROWS = [("phase1", "PASS", 0),
              ("phase2", "NOT_MEASURED", 1),
              ("phase3", "NOT_MEASURED", 1)]


# ── hole 1: NOT_MEASURED never rolls up to PASS ────────────────────────────

def test_run22s_own_rows_do_not_roll_up_to_pass():
    verdict, why = V._roll_up(RUN22_ROWS)
    assert verdict != "PASS", (verdict, why)
    assert verdict == "NOT_MEASURED", (verdict, why)
    assert len(why) == 2, why
    assert any("phase2" in r for r in why) and any("phase3" in r for r in why), why


def test_run22s_rows_with_its_own_completion_audit_are_a_fail():
    """The whole truth about run22: the audit had already said FAIL."""
    verdict, why = V._roll_up(RUN22_ROWS, ["FAIL"])
    assert verdict == "FAIL", (verdict, why)
    assert any("completion audit" in r for r in why), why


def test_an_unknown_verdict_token_is_not_a_pass():
    """FAIL-CLOSED POLARITY, and it is the hole itself: the old body enumerated
    the ways to fail and passed everything else."""
    for token in ("NOT_MEASURED", "NOT_DETERMINED", "IN_PROGRESS", "INCOMPLETE",
                  "ERROR", "VACUOUS_PASS", "", "greenish", None):
        verdict, why = V._roll_up([("phase2", token, 0)])
        assert verdict != "PASS", (token, verdict)
        assert verdict != "PASS_WITH_WAIVERS", (token, verdict)
        assert why, token


def test_the_passing_sets_are_the_only_way_to_pass():
    """The two admitted sets, pinned. Adding a token here is a deliberate act;
    that is the point of asserting them."""
    assert V._PHASE_PASS == frozenset({"PASS"}), V._PHASE_PASS
    assert V._PHASE_PASS_WITH_NOTE == frozenset(
        {"PASS_WITH_WAIVERS", "WAIVED", "COVERAGE-INCOMPLETE"}
    ), V._PHASE_PASS_WITH_NOTE


def test_a_clean_run_still_passes_and_says_nothing():
    """The other direction, so the fix is not just "everything fails now": a run
    whose phases all passed is a PASS with an EMPTY reason list."""
    verdict, why = V._roll_up([("phase1", "PASS", 0), ("phase2", "PASS", 0),
                                 ("phase3", "PASS", 0)], ["PASS"])
    assert (verdict, why) == ("PASS", []), (verdict, why)


def test_waivers_still_travel_as_waivers():
    """Rule 11: PASS_WITH_WAIVERS is passing but not clean, and must not be
    collapsed onto a bare PASS."""
    verdict, why = V._roll_up([("phase1", "COVERAGE-INCOMPLETE", 0),
                                 ("phase2", "PASS", 0)])
    assert verdict == "PASS_WITH_WAIVERS", (verdict, why)
    assert why, "a non-clean pass must say why it is not clean"


def test_a_fail_outranks_an_unmeasured_phase():
    verdict, why = V._roll_up([("phase2", "FAIL", 1),
                                 ("phase3", "NOT_MEASURED", 1)])
    assert verdict == "FAIL", (verdict, why)
    # and the unmeasured phase is still disclosed, not swallowed by the FAIL
    assert any("phase3" in r for r in why), why


# ── hole 2: rc is read ─────────────────────────────────────────────────────

def test_a_phase_reporting_pass_while_exiting_nonzero_is_a_fail():
    verdict, why = V._roll_up([("phase2", "PASS", 1)])
    assert verdict == "FAIL", (verdict, why)
    assert any("rc=1" in r for r in why), why


def test_a_nonzero_rc_under_a_waiver_tier_also_fails():
    verdict, why = V._roll_up([("phase1", "PASS_WITH_WAIVERS", 3)])
    assert verdict == "FAIL", (verdict, why)


def test_a_non_integer_rc_does_not_crash_the_rollup():
    """A report is JSON someone else wrote; `rc` can be null. A roll-up that
    raises here would take the whole front-door report with it."""
    verdict, _ = V._roll_up([("phase2", "PASS", None)])
    assert verdict == "PASS"


# ── hole 3: the completion audit is in the conjunction ─────────────────────

def test_a_failing_completion_audit_fails_a_fully_passing_run():
    verdict, why = V._roll_up([("phase1", "PASS", 0), ("phase3", "PASS", 0)],
                               ["PASS", "FAIL"])
    assert verdict == "FAIL", (verdict, why)


def test_an_absent_completion_audit_is_not_gating(tmp_path):
    """A phase1-only run has no phase2/3 completion audit, and failing it for
    that would be a different false statement."""
    assert V._completion_audit_verdicts(tmp_path) == []
    verdict, why = V._roll_up([("phase1", "PASS", 0)], [])
    assert (verdict, why) == ("PASS", []), (verdict, why)


def test_the_audit_document_and_the_phase_report_are_both_read(tmp_path):
    """Either source saying FAIL is enough, so a stale disagreement can only make
    the front door stricter — never greener."""
    import _path_layout as _pl
    a = _pl.report_path(tmp_path, "phase23_completion_audit.json")
    a.parent.mkdir(parents=True, exist_ok=True)
    a.write_text(json.dumps({"verdict": "PASS"}) + "\n")
    p3 = _pl.report_path(tmp_path, "phase3_one_shot.json")
    p3.parent.mkdir(parents=True, exist_ok=True)
    p3.write_text(json.dumps({"verdict": "NOT_MEASURED",
                              "completion_audit_verdict": "FAIL"}) + "\n")
    got = V._completion_audit_verdicts(tmp_path)
    assert "PASS" in got and "FAIL" in got, got
    assert V._roll_up([("phase3", "PASS", 0)], got)[0] == "FAIL"


# ── the exit code, and the reasons reaching the report ─────────────────────

def test_only_the_passing_tiers_exit_zero():
    """The process exit is the half a CI job reads. Taken from the source by AST
    rather than by a literal slice, because the comparison has been reformatted
    before and an anchor on its text is an anchor that breaks for no reason."""
    src = (PROGRAMS / "vibe_ic_one_shot_runner.py").read_text()
    tree = ast.parse(src)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        if not isinstance(node.ops[0], ast.In):
            continue
        if not (isinstance(node.left, ast.Name) and node.left.id == "overall"):
            continue
        cmp = node.comparators[0]
        if isinstance(cmp, (ast.Tuple, ast.List, ast.Set)):
            found.append({c.value for c in cmp.elts
                          if isinstance(c, ast.Constant)})
    assert found, "no `overall in (...)` comparison found; the exit mapping moved"
    for tokens in found:
        assert tokens <= (V._PHASE_PASS | V._PHASE_PASS_WITH_NOTE), tokens
        assert "NOT_MEASURED" not in tokens and "FAIL" not in tokens, tokens


def test_the_reasons_are_published_and_printed():
    """A verdict that moved must name the phase that moved it, in the report a
    reader opens and on the stdout a reader greps."""
    src = (PROGRAMS / "vibe_ic_one_shot_runner.py").read_text()
    assert '"verdict_reasons": _rollup_why,' in src
    assert '"completion_audit_verdicts": _ca_verdicts,' in src
    assert 'print(f"    because         : {_why}")' in src
    assert 'advisories.append(f"verdict {overall}: {_why}")' in src


# ── and the arm can fail ───────────────────────────────────────────────────

def test_the_old_body_would_call_run22_a_pass():
    """MUTATION, the honest kind: re-implement the OLD aggregation verbatim and
    show it returns PASS for run22's rows. Without this, every arm above could be
    passing because the rows are wrong rather than because the fix works."""
    def old_aggregate(verdicts):
        if any(v == "FAIL" for v in verdicts):
            return "FAIL"
        if any(v in ("PASS_WITH_WAIVERS", "WAIVED", "COVERAGE-INCOMPLETE")
               for v in verdicts):
            return "PASS_WITH_WAIVERS"
        return "PASS"

    assert old_aggregate([v for _n, v, _rc in RUN22_ROWS]) == "PASS"
    assert V._roll_up(RUN22_ROWS)[0] != "PASS"


# ── the older calling convention, which #505's properties are stated in ────

def test_the_plain_verdict_list_contract_is_kept():
    """`_aggregate(List[str]) -> str` predates this change and #505's
    coverage-axis properties are stated directly against it. Changing the
    signature under them broke all three with "too many values to unpack
    (expected 3)" — a four-character verdict string unpacked as a row. The
    adapter keeps the contract; this arm keeps the adapter."""
    assert V._aggregate(["COVERAGE-INCOMPLETE", "PASS"]) == "PASS_WITH_WAIVERS"
    assert V._aggregate(["COVERAGE-INCOMPLETE", "FAIL"]) == "FAIL"
    assert V._aggregate(["PASS", "PASS"]) == "PASS"
    # and the new rule is reachable through the same door
    assert V._aggregate(["PASS", "NOT_MEASURED"]) == "NOT_MEASURED"


def test_rows_of_every_shape_normalise():
    """`plan` rows are 3-tuples; a longer tuple keeps its first three fields, and
    a bare verdict has no rc to disagree with."""
    assert V._as_rows(["PASS"]) == [("phase1", "PASS", 0)]
    assert V._as_rows([("phase2", "PASS", 1)]) == [("phase2", "PASS", 1)]
    assert V._as_rows([("phase2", "PASS", 0, "extra")]) == [("phase2", "PASS", 0)]
    assert V._as_rows([("phase2", "PASS")]) == [("phase2", "PASS", 0)]
    assert V._as_rows([("PASS",)]) == [("phase1", "PASS", 0)]
    # a non-integer rc must not crash the normaliser
    assert V._as_rows([("phase2", "PASS", None)]) == [("phase2", "PASS", 0)]
