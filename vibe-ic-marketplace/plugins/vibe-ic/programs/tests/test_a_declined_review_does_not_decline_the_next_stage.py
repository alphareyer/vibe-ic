"""One declined review must not silence the review of every later stage.

MEASURED 2026-09-16 (lane icsub2) on the `subservient` tapeout run r26
(gf180mcuD). `stage_on_pass_review` is the gate of steps 2, 7, 14, 15, 37 and
39 — the blocking emit's own comment says so — and it declines, correctly, when
the stage under review is not green. R-0915-34(a) made that decline say
`BLOCKED_BY_UPSTREAM` instead of reading as a program fault. What it could not
change is what the decline does NEXT:

    P0 INCOMPLETE (6 of 246 sub-gates returned no verdict)
      -> stage1's review declines
      -> step 7 (Constraint setup) INCOMPLETE, though both its declared
         outputs resolved and its own `stage1_compliance` gate returned
         rc=0 verdict=PASS
      -> stage2 is now non-green BECAUSE OF THAT ROW
      -> stage2's review declines
      -> step 15 INCOMPLETE -> stage3's review declines
      -> step 37 INCOMPLETE -> steps 37.4, 37.5ip and 38 PASS_VOIDED_BY_DEPENDENCY

The run's verdict was FAIL with `failed_gates: []` — every design gate green,
three steps voided, and no red anywhere to point at. A later stage's rows are
not less measured because an earlier stage's review declined.

WHAT IS AND IS NOT REPAIRED. The decline at the HEAD is correct and is left
exactly as it was: stage1's review still declines on P0, because a stage with an
unexamined gate in it is genuinely unreviewed. Only the INHERITANCE stops. The
inherited row is not dropped either — it is published under
`proceeded_past_inherited_decline` and named in `why`, so the report says the
review proceeded and what it proceeded past.

MEASURED CONSEQUENCE, and the reason this is not cosmetic: with stage2's review
running on r26's real tree it did not rubber-stamp anything — it REJECTED, on a
contradiction that had been silenced for the whole life of the cascade (the
intent declares 9 external pins and the synthesised top carries 8; `i_gpio` is
not built). A review that declines finds nothing, and "nothing" had been
reading as a clean stage.

chip-AGNOSTIC: synthetic compliance registers in tmp_path.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import stage_on_pass_review as S  # noqa: E402
import _flow_reason_taxonomy as R  # noqa: E402


DECLINED_REVIEW = {
    "gate": "stage_on_pass_review",
    "verdict": "NOT_MEASURED",
    "reason_class": R.BLOCKED_BY_UPSTREAM,
    "exit_code": 2,
}
# The two other gates step 7 carried on r26, verbatim in shape.
A_PASSING_GATE = {"gate": "stage1_compliance", "verdict": "PASS",
                  "reason_class": None, "exit_code": 0}
A_ROSTER_NA_GATE = {"gate": "l21_to_upf_emit", "verdict": "VACUOUS_PASS",
                    "reason_class": R.DESIGN_DECLARED_NA, "exit_code": 2}


def _register(tmp_path, rows, stage="stage2", name="c.json"):
    """`rows` is a list of dicts already shaped like compliance step rows."""
    p = tmp_path / name
    p.write_text(json.dumps(
        {"steps": [dict(r, stage=r.get("stage", stage)) for r in rows]}))
    return p


def _row(sid, status, gates):
    return {"id": sid, "status": status, "advisory_gate_records": list(gates)}


# ── the repair ───────────────────────────────────────────────────────────────
def test_the_next_stage_is_reviewed_when_the_only_wound_is_the_last_review(
        tmp_path):
    """Step 7's exact r26 shape: everything green but the inherited decline."""
    reg = _register(tmp_path, [
        _row(7, "INCOMPLETE",
             [A_PASSING_GATE, DECLINED_REVIEW, A_ROSTER_NA_GATE]),
        {"id": "FS1", "status": "NOT_MEASURED"},
    ])
    got = S.stage_passed(reg, "stage2", None)
    assert got["passed"] is True, got["why"]
    # DISCLOSED, not dropped.
    assert [r["id"] for r in got["proceeded_past_inherited_decline"]] == ["7"]
    assert got["non_green_rows"] == []
    assert "proceeded past 7=INCOMPLETE" in got["why"]


# ── the negative controls: what must STILL block ─────────────────────────────
def test_a_genuinely_failing_gate_beside_the_decline_still_blocks(tmp_path):
    """The decline is not a blanket pardon for the row it appears in."""
    reg = _register(tmp_path, [
        _row(7, "INCOMPLETE", [
            DECLINED_REVIEW,
            {"gate": "sdc_sanity_check", "verdict": "FAIL",
             "reason_class": None, "exit_code": 1},
        ]),
    ])
    got = S.stage_passed(reg, "stage2", None)
    assert got["passed"] is False, got["why"]
    assert [r["id"] for r in got["non_green_rows"]] == ["7"]


def test_an_unexamined_gate_beside_the_decline_still_blocks(tmp_path):
    """EXECUTION_ERROR / ZERO_DENOMINATOR are the INCOMPLETE tier, not skips.

    This is the shape that keeps stage1 declining on r26: P0's six remaining
    records. A run must not reach green by walking past them.
    """
    for cls in (R.EXECUTION_ERROR, R.ZERO_DENOMINATOR):
        reg = _register(tmp_path, [
            _row(7, "INCOMPLETE", [
                DECLINED_REVIEW,
                {"gate": "some_structural_check", "verdict": "INCOMPLETE",
                 "reason_class": cls, "exit_code": 2},
            ]),
        ], name=f"c_{cls}.json")
        got = S.stage_passed(reg, "stage2", None)
        assert got["passed"] is False, f"{cls}: {got['why']}"


def test_a_failed_row_is_never_exempt_however_it_is_gated(tmp_path):
    """The exemption is reachable only from the NO_VERDICT_IN_SCOPE tier."""
    for status in ("FAIL", "FAIL", "NOT_MEASURED"):
        reg = _register(tmp_path,
                        [_row(7, status, [DECLINED_REVIEW])],
                        name=f"c_{status}.json")
        got = S.stage_passed(reg, "stage2", None)
        assert got["passed"] is False, f"{status}: {got['why']}"


def test_a_row_with_no_gate_records_is_never_exempt(tmp_path):
    """An exemption granted over an empty population is a vacuous pass."""
    reg = _register(tmp_path, [{"id": 7, "status": "NOT_MEASURED"}])
    got = S.stage_passed(reg, "stage2", None)
    assert got["passed"] is False, got["why"]


def test_some_other_programs_not_checked_is_not_this_exemption(tmp_path):
    """Only THIS program's own decline is inherited; nobody else's."""
    reg = _register(tmp_path, [
        _row(7, "INCOMPLETE", [dict(DECLINED_REVIEW, gate="lvs_check")]),
    ])
    got = S.stage_passed(reg, "stage2", None)
    assert got["passed"] is False, got["why"]


def test_the_head_of_the_cascade_still_declines(tmp_path):
    """P0's shape: a real gate returned no verdict. Nothing pardons that."""
    reg = _register(tmp_path, [
        {"id": "P0", "stage": "stage1", "status": "NOT_MEASURED",
         "gate_records": [
             {"name": "waiver_staleness_check", "verdict": "INCOMPLETE",
              "reason_class": R.ZERO_DENOMINATOR}]},
    ], stage="stage1")
    got = S.stage_passed(reg, "stage1", None)
    assert got["passed"] is False, got["why"]
    assert [r["id"] for r in got["non_green_rows"]] == ["P0"]
