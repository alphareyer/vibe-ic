"""R-0915-139 — a frozen replay measures HOW EXISTING ROWS ARE READ.

THE DEFECT, measured on SLT53E. Step 37.3 arrived with the GDS fidelity producer.
The spm end-to-end subject read it PASS (0 design-layer differences across 46
layers, producer in the run's own container, receipt judged). The FOUR FROZEN
REPLAY subjects read it `None -> FAIL/missing_artefact` -- and one comparator rule
booked those four as REGRESSIONS, for a step whose flow those trees predate. Its
siblings 37 and 37.5ip read FAIL on the same trees for the same reason.

WHY ONE RULE CANNOT SERVE BOTH SUBJECTS:

  real_ic_gate  runs the CANDIDATE TREE's own flow. Every step it declares is a
                step this tree owns, so a new row at NON_GREEN is a new red this
                change brought. That rule stands -- and it is the one that caught
                the NOT_DETERMINED -> FAIL defect in SLT53D, which is precisely
                why this change must not reach it.
  audit_replay  replays a FROZEN run tree. A step the frozen run's flow never
                declared has no reference and CANNOT have one; the row can only
                read missing there, whatever the candidate does. A finding that
                cannot be absent is not a measurement.

So on a replay subject a reference-None row is `ADDED_NO_REFERENCE`: recorded
under `added`, and in NONE of `regressions`, `improvements` or `laterals`. Its own
direction rather than LATERAL, because a lateral is a move between two measured
words and there was no first word to move from.

MEASURED THROUGH `diff_tables` ON BOTH SUBJECTS:

    existing row PASS -> FAIL      replay  regressions=1  gate  regressions=1
    added row at FAIL              replay  regressions=0  gate  regressions=1
    added row at PASS              replay  regressions=0  gate  regressions=0
    removed row that proved        replay  regressions=1  gate  regressions=1
    unknown subject kind           falls back to the STRICTER rule
"""
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
PROGRAMS = PLUGIN / "programs"
sys.path.insert(0, str(PROGRAMS))

import _step_verdict_table as SVT                            # noqa: E402

_REPLAY = SVT.SUBJECT_AUDIT_REPLAY
_GATE = SVT.SUBJECT_REAL_IC_GATE


#: `comparability` REFUSES a table with no `command_argv` -- "the scope it was
#: produced at is unknown and the two tables cannot be shown to answer the same
#: question". That refusal is right and is not worked around here: both fixtures
#: carry the SAME argv, so the tables are comparable for the reason the real ones
#: are, and every assertion below is about the diff and not about comparability.
_ARGV = ["flow_compliance_check", ".", "--strict"]


def _table(**steps) -> dict:
    return {"verdict": "PASS",
            "command_argv": list(_ARGV),
            # And the RUN SHAPE, for the same reason: `_run_shape_comparability`
            # refuses two tables whose gate execution ledger is unknown, because
            # "a program-only run and an agent-driven one disagree on every step
            # whose second pass is an agent's". Both fixtures declare the same
            # shape -- an empty unanswered set -- so they are the same experiment.
            "run_shape": {"unanswered_second_pass": []},
            "steps": {k: {"status": v, "name": f"step {k}", "stage": "stage4"}
                      for k, v in steps.items()}}


def _diff(ref: dict, cur: dict, kind: str) -> dict:
    d = SVT.diff_tables(ref, cur, subject_kind=kind)
    assert d["comparable"], d["comparability_reason"]
    return d


# ── the row that has no reference ──────────────────────────────────────────

def test_an_added_red_row_is_not_a_regression_on_a_replay():
    """THE RULING. Four SLT53E replays booked step 37.3 this way."""
    d = _diff(_table(**{"31": "PASS"}),
              _table(**{"31": "PASS", "37.3": "FAIL"}), _REPLAY)
    assert [e["id"] for e in d["added"]] == ["37.3"], d["added"]
    assert d["added"][0]["direction"] == SVT.ADDED_NO_REFERENCE
    assert d["added"][0]["reference"] is None
    assert d["regressions"] == [], d["regressions"]
    assert d["improvements"] == [] and d["laterals"] == []
    assert d["regressed"] is False, "an added replay row must not fail the arm"


def test_an_added_red_row_IS_a_regression_on_the_gate():
    """THE OTHER SUBJECT, UNTOUCHED. This is the rule that caught the
    NOT_DETERMINED -> FAIL defect in SLT53D; the replay exemption must not reach
    it."""
    d = _diff(_table(**{"31": "PASS"}),
              _table(**{"31": "PASS", "37.3": "FAIL"}), _GATE)
    assert [e["id"] for e in d["regressions"]] == ["37.3"], d["regressions"]
    assert d["added"][0]["direction"] == SVT.REGRESSION
    assert d["regressed"] is True


def test_an_added_green_row_is_no_regression_on_either_subject():
    """The control that keeps the arm above from passing for the wrong reason: a
    green arrival was never a regression, so a change that only silenced reds
    would look identical here."""
    for kind, want in ((_REPLAY, SVT.ADDED_NO_REFERENCE), (_GATE, SVT.LATERAL)):
        d = _diff(_table(**{"31": "PASS"}),
                  _table(**{"31": "PASS", "37.3": "PASS"}), kind)
        assert d["regressions"] == [], (kind, d["regressions"])
        assert d["added"][0]["direction"] == want, (kind, d["added"])
        assert d["regressed"] is False


# ── everything else the comparator does is unchanged ───────────────────────

def test_an_existing_row_that_went_red_is_still_a_regression_on_a_replay():
    """WHAT THE REPLAY TIER IS FOR. The exemption is about rows with NO
    reference; a row the frozen tree DID measure, now read worse, is exactly the
    finding this subject exists to make."""
    d = _diff(_table(**{"31": "PASS"}), _table(**{"31": "FAIL"}), _REPLAY)
    assert [e["id"] for e in d["regressions"]] == ["31"], d["regressions"]
    assert d["regressed"] is True
    assert d["added"] == [] and d["removed"] == []


def test_a_row_that_left_a_replay_table_is_still_a_regression():
    """The REMOVED direction, untouched: a step that used to prove something and
    is now not even in the table is the laundering shape from the other side."""
    for kind in (_REPLAY, _GATE):
        d = _diff(_table(**{"31": "PASS", "9": "PASS"}),
                  _table(**{"31": "PASS"}), kind)
        assert [e["id"] for e in d["regressions"]] == ["9"], (kind, d)
        assert d["regressed"] is True


def test_an_existing_row_that_improved_is_still_an_improvement():
    for kind in (_REPLAY, _GATE):
        d = _diff(_table(**{"31": "FAIL"}), _table(**{"31": "PASS"}), kind)
        assert [e["id"] for e in d["improvements"]] == ["31"], (kind, d)
        assert d["regressed"] is False


# ── the wiring, and the fail-closed default ───────────────────────────────

def test_the_default_is_the_stricter_rule():
    """A caller that says nothing gets the rule that can refuse. The exemption is
    opt-in by subject, so a new consumer cannot inherit it by accident."""
    d = _diff(_table(**{"31": "PASS"}),
              _table(**{"31": "PASS", "37.3": "FAIL"}), _GATE)
    default = SVT.diff_tables(_table(**{"31": "PASS"}),
                              _table(**{"31": "PASS", "37.3": "FAIL"}))
    assert default["regressions"] and default["subject_kind"] == _GATE
    assert len(default["regressions"]) == len(d["regressions"])


def test_an_unknown_subject_kind_falls_closed():
    """Fail CLOSED, not open: an unreviewed caller keeps the stricter rule rather
    than silently receiving the replay exemption."""
    d = SVT.diff_tables(_table(**{"31": "PASS"}),
                        _table(**{"31": "PASS", "37.3": "FAIL"}),
                        subject_kind="a_subject_nobody_reviewed")
    assert d["subject_kind"] == _GATE, d["subject_kind"]
    assert len(d["regressions"]) == 1


def test_the_diff_records_which_rule_ran():
    """A reader of a diff that books no regression for an added red must be able
    to see WHY without knowing which program produced it."""
    for kind in (_REPLAY, _GATE):
        assert _diff(_table(**{"31": "PASS"}), _table(**{"31": "PASS"}),
                     kind)["subject_kind"] == kind


def test_audit_replay_asks_for_the_replay_rule_and_the_gate_does_not():
    """THE WIRING, at source. The ruling is about which SUBJECT gets the
    exemption, so a correct `diff_tables` with the wrong caller would be no fix
    at all."""
    replay_src = (PROGRAMS / "audit_replay.py").read_text()
    assert "subject_kind=_svt.SUBJECT_AUDIT_REPLAY" in replay_src, (
        "audit_replay must ask for the replay rule by name")
    gate_src = (PROGRAMS / "real_ic_gate.py").read_text()
    assert "SUBJECT_AUDIT_REPLAY" not in gate_src, (
        "real_ic_gate must NOT take the replay exemption — it runs the "
        "candidate tree's own flow, where a new red is a new red")
