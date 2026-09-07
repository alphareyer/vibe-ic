#!/usr/bin/env python3
"""E4 of vibe-ic#2092 — a "reported, NOT gating" sentence becomes a record.

MEASURED DEFECT
===============
The `opentitan_aes` run of lane icaes (8HD-4), in its own
`reports/audit/flow_compliance_check.log` at line 215:

    Step-level gates (informational, not gating --strict-structural):
    3 step(s) FAIL/MISSING
      • step2 …: MISSING — …
      • step4 …: FAIL — …
      • step5 …: FAIL — …

printed beside ``Overall: PASS_WITH_WAIVERS``. The three steps ARE rows in
that log's own step table (lines 119 / 136 / 145), and the sentence reached
nothing else:

  * it is in NO field of `phase23_completion_audit.json`, so no consumer of
    the machine-readable artefact can read it;
  * it is printed 130+ lines before the end of stdout, while
    `design_one_shot_runner.step_final_audit` keeps only the FINAL 25 lines as
    that step's detail — measured on the same run;
  * `reports/final_summary.md` for that run contains the word
    "informational" ZERO times.

WHAT IS ENFORCED
================
Every disclosure becomes a named record carrying the step ids it names, and
the record CHECKS that each of those ids is a row in the step table this same
artefact publishes. The same sentence is printed again in the final stdout
block, after the gate ledger, where the tail-keeping consumer looks.

`every_disclosed_step_is_a_row` is None when there is nothing to disclose —
there was no question, and that is not a pass.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import flow_compliance_check as F  # noqa: E402

_BASE = dict(
    strict_structural_only=False,
    step_artifact_fail_lines=[],
    step_artifact_fail_step_ids=[],
    ordering_fail_lines=[],
    ordering_gating_lines=[],
    ordering_informational_step_ids=[],
    self_skipped_signoff_step_ids=[],
    step_ids_in_table=[],
)


def _d(**kw):
    return F.informational_disclosures(**dict(_BASE, **kw))


#: The icaes log's own disclosure, with its step table.
_ICAES = dict(
    strict_structural_only=True,
    step_artifact_fail_lines=[
        "step2 (RTL validation): MISSING — INCOMPLETE: …",
        "step4 (Simulation): FAIL — program failed: …",
        "step5 (Formal verification): FAIL — program failed: …"],
    step_artifact_fail_step_ids=["2", "4", "5"],
    step_ids_in_table=["P0", "0.5ic", "1", "2", "4", "5", "FS1", "DT1"],
)


def test_the_measured_icaes_disclosure_is_a_record_and_every_step_is_a_row():
    b = _d(**_ICAES)
    assert len(b["disclosures"]) == 1
    d = b["disclosures"][0]
    assert d["kind"] == "step_level_gates_not_gating_strict_structural"
    assert d["count"] == 3
    assert d["step_ids"] == ["2", "4", "5"]
    assert d["gating"] is False
    assert b["every_disclosed_step_is_a_row"] is True
    assert b["steps_missing_from_the_step_table"] == []


def test_a_disclosure_naming_a_step_that_is_in_no_table_is_reported():
    """THE FABRICATION DIRECTION. A step a reader cannot look up is the same
    defect one layer down, and it must be named rather than smoothed over."""
    b = _d(**dict(_ICAES, step_ids_in_table=["P0", "2"]))
    assert b["every_disclosed_step_is_a_row"] is False
    assert b["steps_missing_from_the_step_table"] == ["4", "5"]
    assert b["disclosures"][0]["steps_missing_from_the_step_table"] == \
        ["4", "5"]


def test_the_same_sentence_is_emitted_in_the_final_stdout_block():
    """`step_final_audit` keeps the FINAL 25 lines. A disclosure that only
    exists 130 lines earlier reaches nobody."""
    lines = F.informational_disclosure_lines(_d(**_ICAES))
    assert lines, "the disclosure must be printed, not only serialised"
    joined = "\n".join(lines)
    assert "INFORMATIONAL DISCLOSURES (reported, NOT gating)" in joined
    assert "3 step(s) FAIL/MISSING" in joined
    assert "steps: 2, 4, 5" in joined
    assert "phase23_completion_audit.json" in joined
    assert len(lines) <= 25, (
        "the block must fit inside the 25-line tail it exists to reach")


def test_a_flow_graph_reference_outside_the_table_is_recorded_not_broken():
    """THE FALSE POSITIVE THIS EQUATION FIRST HAD, and the input that measured
    it. `test_issue1429_ordering_guard_is_scoped_not_disabled.py::test_an_out_
    of_scope_violation_alone_does_not_red` — whose whole subject is a
    violation OUTSIDE the verdict scope — was turned RED by the first draft,
    for naming step `9` under a `--phase 2` run whose step table has no row
    for it.

    The step-level and self-skip disclosures are built by walking `results`,
    so their ids are rows BY CONSTRUCTION and one missing is a defect. The
    ordering disclosure reports on the FLOW GRAPH: it names ids from
    `blocks_on` edges, and under a narrowed scope an edge legitimately reaches
    a step this run has no row for. The id is RECORDED either way; only a kind
    that claims its ids project the table can be wrong about it."""
    b = _d(ordering_fail_lines=["a"], ordering_gating_lines=[],
           ordering_informational_step_ids=["9"],
           step_ids_in_table=["P0", "1"])
    d = b["disclosures"][0]
    assert d["ids_project_the_step_table"] is False
    assert d["step_ids_not_in_the_step_table"] == ["9"]
    assert d["steps_missing_from_the_step_table"] == []
    assert b["every_disclosed_step_is_a_row"] is None, (
        "no disclosure asserted against the table, so there is no question")
    assert b["step_ids_not_in_the_step_table"] == ["9"]
    # And it is SAID, not swallowed.
    joined = "\n".join(F.informational_disclosure_lines(b))
    assert "flow-graph reference outside this run's step table: ['9']" in joined


def test_a_kind_that_projects_the_table_is_still_asserted_against_it():
    """The break did not go away — it narrowed to the kinds that can be wrong.
    A step-level disclosure naming a step in no row is still a defect."""
    b = _d(**dict(_ICAES, step_ids_in_table=["P0", "2"]))
    d = b["disclosures"][0]
    assert d["ids_project_the_step_table"] is True
    assert d["steps_missing_from_the_step_table"] == ["4", "5"]
    assert b["every_disclosed_step_is_a_row"] is False


def test_an_ordering_violation_outside_the_verdict_scope_is_disclosed():
    b = _d(ordering_fail_lines=["a", "b", "c"],
           ordering_gating_lines=["a"],
           ordering_informational_step_ids=["31", "32"],
           step_ids_in_table=["31", "32"])
    kinds = [d["kind"] for d in b["disclosures"]]
    assert "step_ordering_violation_outside_the_verdict_scope" in kinds
    kind = "step_ordering_violation_outside_the_verdict_scope"
    d = next(x for x in b["disclosures"] if x["kind"] == kind)
    assert d["count"] == 2
    assert d["step_ids"] == ["31", "32"]
    assert d["gating"] is False


def test_a_self_skipped_signoff_step_is_disclosed():
    b = _d(self_skipped_signoff_step_ids=["DT1", "DT2"],
           step_ids_in_table=["DT1", "DT2"])
    d = b["disclosures"][0]
    assert d["kind"] == "signoff_step_self_skipped"
    assert d["step_ids"] == ["DT1", "DT2"]


def test_the_step_level_disclosure_only_fires_in_the_mode_that_declares_it():
    """`--strict-structural` alone is the ONE mode that declares step-level
    FAIL/MISSING informational. In every other mode those steps GATE, and
    calling them a disclosure would be the inversion."""
    b = _d(**dict(_ICAES, strict_structural_only=False))
    assert b["disclosures"] == []
    assert b["every_disclosed_step_is_a_row"] is None


def test_nothing_disclosed_is_not_a_pass():
    b = _d()
    assert b["every_disclosed_step_is_a_row"] is None
    assert F.informational_disclosure_lines(b) == []


def test_the_record_names_the_disclosure_as_not_gating():
    for b in (_d(**_ICAES),
              _d(ordering_fail_lines=["a"], ordering_gating_lines=[],
                 ordering_informational_step_ids=["9"],
                 step_ids_in_table=["9"]),
              _d(self_skipped_signoff_step_ids=["DT1"],
                 step_ids_in_table=["DT1"])):
        assert all(d["gating"] is False for d in b["disclosures"])
