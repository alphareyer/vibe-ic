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
    for rc in (0, 1):
        verdict, why = V._roll_up([("phase1", "PASS", 0), ("phase2", "PASS", 0),
                                   ("phase3", "WAIVED", rc)],
                                  audit_axis={"state": "PASS"})
        assert verdict == "WAIVED", (rc, verdict, why)
        assert not T.is_done_claim(verdict)
        assert any("phase3 WAIVED" in w for w in why), why


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
