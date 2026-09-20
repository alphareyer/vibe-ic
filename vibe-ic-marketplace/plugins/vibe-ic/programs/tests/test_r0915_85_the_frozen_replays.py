#!/usr/bin/env python3
"""The R-0915-85 acceptance, over the step table subservient r26 actually produced.

WHY THIS FILE EXISTS. R-0915-85's two defects — the r26 cascade and the run16
laundering — were invisible to the flow-matrix suite and to the 3,700 fixture
tests, because neither exists in a fixture: both appear only when a REAL run
reaches a later step carrying an earlier step's word. The fixture here is
therefore not invented; it is the step table the frozen r26 project produced
under this branch, ids and verdicts only (provenance in the file).

WHAT IT PINS, and both halves are stated because the dispatcher asked for both
(2026-09-16 22:47):

  * AS MEASURED TODAY the run reads NOT_MEASURED — not PASS, not FAIL. Three
    steps measured nothing (D1, P0, FS1) and the vocabulary can now say so.
  * ONCE THOSE ROOTS ARE MEASURED the same table reads PASS_WITH_WAIVERS, with
    the three open rows being steps 6, 36 and 39 — the FPGA prototype, the
    tapeout checklist and the FPGA final sign-off, which is the row shape
    R-0915-59 accepts.

D1's root is a REGRESSION landed 2026-09-16 (R-0915-88, owned by lane
icspmgate): `phase1_expert_parse_track --check-report` reports its input was
applicable and was NOT examined. It is deliberately NOT re-tiered here. The
point of the second assertion is precisely that the cascade rule leaves the
rest of the run intact while it is open.
"""
from __future__ import annotations

import json
import sys

import pytest

from _plugin_tree import plugin_path

sys.path.insert(0, str(plugin_path() / "programs"))

import verdict as V  # noqa: E402

FIXTURE = (plugin_path() / "programs" / "tests" / "fixtures"
           / "r0915_85_subservient_r26_step_table.json")

#: The three steps whose gates measured nothing. Named, not derived, because
#: naming them is the finding: everything else in this run passed.
UNMEASURED_ROOTS = {"D1", "P0", "FS1"}

#: The four whose ONLY non-green sub-gate is a nested `flow_compliance_check
#: --stage-id <stage>` whose own scope holds one of the roots above. They
#: inherit honestly — the gate asked "did this stage comply" and one of its
#: steps was never measured — which is NOT the cascade the ruling deletes:
#: nothing downstream of THEM is voided, and that is what the third test shows.
INHERITORS = {"2", "7", "15", "37"}


@pytest.fixture(scope="module")
def table():
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert doc["steps"], "the fixture carries no step table"
    return doc["steps"]


def _mk(row, override=None):
    st = override or row["status"]
    kw = {}
    if st == "NOT_MEASURED":
        kw["reason_class"] = row.get("reason_class") or "not_executed"
    if st == "NOT_APPLICABLE":
        kw["declared_by"] = row.get("declared_by") or "the input declares it"
    if st == "PASS_WITH_WAIVERS":
        kw["waiver_rows"] = [V.WaiverRow(row["id"], row["name"])]
    return V.StepVerdict(V.parse(st), row["id"], row["name"], **kw)


def test_every_word_in_the_real_table_is_one_of_the_five(table):
    """The vocabulary reached a real run, not only the unit tests."""
    worn = {r["status"] for r in table}
    assert worn <= {v.value for v in V.Verdict}, worn


def test_the_run_reads_not_measured_while_the_roots_are_open(table):
    """NOT PASS and NOT FAIL. Zero steps FAILED — nothing about this chip did."""
    assert V.run_verdict(_mk(r) for r in table) is V.Verdict.NOT_MEASURED
    assert not [r for r in table if r["status"] == "FAIL"], (
        "r26 has no FAILing step; a FAIL here would mean the replay changed")


def test_the_same_table_reads_pass_with_waivers_once_the_roots_are_measured(
        table):
    """The other direction, and the dispatcher's second half.

    Substituting PASS for the three roots AND the four steps that inherit from
    them through a nested stage audit — and NOTHING else — moves the run word to
    PASS_WITH_WAIVERS. So the NOT_MEASURED above is carried by exactly those
    rows and by no other part of the run.
    """
    fixed = {*UNMEASURED_ROOTS, *INHERITORS}
    got = V.run_verdict(
        _mk(r, "PASS" if r["id"] in fixed else None) for r in table)
    assert got is V.Verdict.PASS_WITH_WAIVERS, got


def test_fixing_only_d1_is_not_enough_and_the_test_says_which_rows_remain(
        table):
    """Negative control for the test above: the substitution is load-bearing.

    P0 (6 of 246 structural sub-gates returned no verdict) and FS1 (both FMEDA
    clauses examined nothing) are separate roots. A test that passed on D1
    alone would be asserting a property of the substitution, not of the rule.
    """
    got = V.run_verdict(
        _mk(r, "PASS" if r["id"] in {"D1", "2"} else None) for r in table)
    assert got is V.Verdict.NOT_MEASURED, got


def test_the_open_rows_are_the_ones_r0915_59_accepts(table):
    """WHICH rows remain open, by name. A `PASS_WITH_WAIVERS` whose rows nobody
    can enumerate is the defect the bare `WAIVED` word permitted."""
    rows = sorted(r["id"] for r in table
                  if r["status"] == V.Verdict.PASS_WITH_WAIVERS.value)
    assert rows == ["36", "39", "6"], rows


def test_no_step_in_the_real_table_is_voided_by_an_unmeasured_upstream(table):
    """THE CASCADE CLAUSE, on real data.

    In the lane's own stage-4 audit of this project, steps 37.4 / 37.5ip / 38
    wore `PASS_VOIDED_BY_DEPENDENCY` and were the run's ONLY named causes, each
    having produced and verified its own artefact. Here they are plain PASS,
    downstream of a step (37) that is NOT_MEASURED.
    """
    by = {r["id"]: r for r in table}
    for sid in ("37.4", "37.5ip", "38"):
        assert by[sid]["status"] == V.Verdict.PASS.value, (sid, by[sid])
    assert by["37"]["status"] == V.Verdict.NOT_MEASURED.value
    upstream = _mk(by["37"])
    for sid in ("37.4", "37.5ip", "38"):
        assert V.cascade_to_dependent(upstream, sid) is None


def test_a_failed_upstream_still_takes_its_dependents_down(table):
    """The other direction of the same clause, so the fix is not a hole."""
    by = {r["id"]: r for r in table}
    failed = V.StepVerdict.fail("37", by["37"]["name"])
    for sid in ("37.4", "37.5ip", "38"):
        got = V.cascade_to_dependent(failed, sid)
        assert got is not None and got.verdict is V.Verdict.NOT_MEASURED
        assert got.reason_class is V.ReasonClass.UPSTREAM_FAILED
