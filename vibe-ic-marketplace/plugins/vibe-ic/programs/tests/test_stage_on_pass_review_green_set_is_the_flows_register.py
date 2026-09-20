"""`stage_on_pass_review`'s green set is the flow's own verdict register.

MEASURED 2026-09-15 (lane icspm3) on `spm` x gf180mcuD. The set was a literal
tuple and it carried `WAIVED-DEFERRED` while OMITTING its sibling `WAIVED` —
which is the word the completion audit actually writes. Steps 6 and 39 carry
the machinery-sanctioned ENV_UNAVAILABLE fpga-board cap-gap deferral, the audit
records both as `WAIVED`, and this program answered::

    rc=2 NOT CHECKED — stage stage1 did not pass (7 row(s) for stage1;
    non-green: INCOMPLETE, WAIVED)

This program is the gate of steps 2, 7, 15 and 37, so each of them went
INCOMPLETE, which kept stage1/2/3 from being green, which kept this program
declining. `verdict.EXCUSED` registers BOTH spellings; one was
registered here and one was not.

BOTH DIRECTIONS. A sanctioned deferral does not stop a stage being reviewable;
a FAIL, a MISSING, a PASS-VOIDED-BY-DEPENDENCY and a SKIPPED-SETUP-REQUIRED
still do — the fix must not turn the reviewer into one that fires on anything.

chip-AGNOSTIC: the green set is re-derived from `verdict` here, so
a word that leaves that register fails this file rather than living on.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import pytest  # noqa: E402

import verdict as T  # noqa: E402
import stage_on_pass_review as S  # noqa: E402


def _compliance(tmp_path, statuses, stage="stage1"):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"steps": [
        {"id": str(i), "stage": stage, "status": st}
        for i, st in enumerate(statuses)]}))
    return p


# ── the set is DERIVED, not retyped ───────────────────────────────────────

def test_every_EXCUSED_word_in_the_flows_register_is_green_here():
    """The register is the source. A word registered EXCUSED that this
    program does not accept is the defect this file exists for."""
    assert T.EXCUSED, "the flow's EXCUSED register is empty; nothing measured"
    missing = [w for w in T.EXCUSED if S._norm_status(w) not in S._STAGE_GREEN]
    assert not missing, missing


def test_the_deferral_has_exactly_one_spelling_and_it_is_green():
    """R-0915-85 answered the half-registered pair by DELETING the pair.

    This file was written because `WAIVED` was registered green here and its
    sibling `WAIVED-DEFERRED` was not, so the same tier answered two ways.
    There is now ONE word for a sanctioned deferral, and the other spellings
    are not "unregistered" — they are refused at the producer.
    """
    assert S._norm_status("PASS_WITH_WAIVERS") in S._STAGE_GREEN
    for gone in ("WAIVED", "WAIVED-DEFERRED", "WAIVED_DEFERRED"):
        with pytest.raises(T.UnknownVerdictWord):
            T.parse(gone)


def test_a_full_pass_is_green_and_an_unmeasured_row_is_not():
    for word in (T.FULL_PASS, "PASS", "NOT_APPLICABLE"):
        assert S._norm_status(word) in S._STAGE_GREEN, word
    # NOT_MEASURED is not green -- "nobody measured it" is not "it passed", and
    # it blocks the run-level PASS, which is why it is in NON_GREEN. What it
    # does NOT do is void anything downstream: that is the cascade rule, and it
    # is asserted where it lives rather than inferred from this membership.
    assert S._norm_status("NOT_MEASURED") not in S._STAGE_GREEN
    assert "NOT_MEASURED" in T.NON_GREEN
    assert T.cascade_to_dependent(
        T.StepVerdict.not_measured("9", "x", reason_class=T.ReasonClass.STALLED,
                                   reason="nobody looked")) is None


def test_punctuation_is_not_the_answer():
    """The five words have ONE spelling each, and `parse` refuses the rest.

    Normalisation survives only for the ~450 gate programs whose own verdict
    vocabulary still arrives in two spellings; it now folds toward the
    underscore, which is how every one of the five is written.
    """
    assert S._norm_status("pass_with_waivers") == "PASS_WITH_WAIVERS"
    assert S._norm_status("Not-Measured") == "NOT_MEASURED"
    assert S._norm_status(None) == "?"


# ── direction 1: a sanctioned deferral does not stop the review ───────────

def test_a_stage_whose_only_non_PASS_row_is_WAIVED_is_reviewable(tmp_path):
    got = S.stage_passed(_compliance(tmp_path, ["PASS", "PASS", "PASS_WITH_WAIVERS"]),
                         "stage1", None)
    assert got["passed"] is True, got
    assert "all green" in got["why"]


def test_the_measured_shape_stops_naming_WAIVED(tmp_path):
    """spm's own stage1 shape: a deferral plus a real INCOMPLETE. The stage is
    still not reviewable — but WAIVED is no longer one of the reasons, which
    is the whole of what this change does."""
    got = S.stage_passed(
        _compliance(tmp_path, ["PASS", "PASS_WITH_WAIVERS", "NOT_MEASURED"]), "stage1",
        None)
    assert got["passed"] is False
    assert "WAIVED" not in got["why"], got["why"]
    assert "NOT_MEASURED" in got["why"]


# ── direction 2: a stage that really failed still is not reviewed ─────────

@pytest.mark.parametrize("word", sorted(T.NON_GREEN))
def test_every_NON_GREEN_word_still_stops_the_review(tmp_path, word):
    got = S.stage_passed(_compliance(tmp_path, ["PASS", word]), "stage1", None)
    assert got["passed"] is False, (word, got)
    assert word in got["why"]


def test_an_unregistered_word_still_stops_the_review(tmp_path):
    """Conservative direction unchanged: a verdict word nobody registered is
    not quietly green."""
    got = S.stage_passed(_compliance(tmp_path, ["PASS", "BANANA"]), "stage1",
                         None)
    assert got["passed"] is False and "BANANA" in got["why"]


def test_an_unestablished_verdict_is_still_not_a_pass(tmp_path):
    assert S.stage_passed(None, "stage1", None)["passed"] is None
    assert S.stage_passed(_compliance(tmp_path, ["PASS"]), "stageZ",
                          None)["passed"] is None
