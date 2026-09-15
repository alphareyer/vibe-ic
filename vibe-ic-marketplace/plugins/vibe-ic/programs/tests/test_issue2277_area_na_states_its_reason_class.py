"""The area gate answered correctly and the flow booked step 9 INCOMPLETE.

vibe-ic#2277. MEASURED on `spm` x gf180mcuD, live main 79506306d (lane icspm4,
run1). `area_total_vs_budget_check` exits 0 and writes
`{"verdict": "NOT_APPLICABLE", "disposition": {...}}`, citing the design's own
`input/submission_template/tapeout_declaration.json#/synthesis_area_budget`
(the input declines a die size twice and declares deliverable=HARDMACRO). The
step was still booked INCOMPLETE, because the report stated no reason CLASS:

  * `_flow_reason_taxonomy.report_reason_class` reads only `reason_class`,
    `not_measured_class` and `skip_reason_class` -- the report had none;
  * `flow_compliance_check._report_reason_text` reads only `reason`,
    `explanation`, `message` and `skipped_reason` -- this branch's own
    explanation sits under `disposition.rationale`, which it never reads;
  * so the rc-0 branch of `_check_program_exit_zero` inferred with
    `explicit=None` and `message=""`, fail-closed to `EXECUTION_ERROR`, which
    is not skip-eligible, and rewrote the row as
    `INCOMPLETE: ... reason_class=EXECUTION_ERROR`.

The power sibling has stated its class since #2147. This is that one line, on
this branch ONLY -- the L19-conflict INCOMPLETE, the UNSET/ceiling/unit
INCOMPLETE and every real comparison keep their own tiers.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROGRAMS = HERE.parent
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(HERE))

import _flow_reason_taxonomy as taxonomy                 # noqa: E402
import area_total_vs_budget_check as area_gate           # noqa: E402
import flow_compliance_check as F                        # noqa: E402
# The measured shape is already fixtured: LIB_A is the technology the design's
# own L7 baseline is attributed to, LIB_B the one the run actually built on.
import test_issue2147_signoff_rows_are_consumed as S     # noqa: E402

_CLAUSE = ("area_total_vs_budget_check . --json "
           "reports/phase2/gates/area_budget.json")


def _na_project(tmp_path):
    """The spm shape: the design disposes the die, and no L19 ceiling states one."""
    return S._project(tmp_path, built_on=S.LIB_B,
                      area_declaration="not_applicable")


def test_the_design_declared_NA_branch_states_its_reason_class(tmp_path):
    v, rep = area_gate.evaluate(_na_project(tmp_path), None, False)
    assert v == "NOT_APPLICABLE"
    assert rep.get("reason_class") == taxonomy.DESIGN_DECLARED_NA
    assert taxonomy.report_reason_class(rep) == taxonomy.DESIGN_DECLARED_NA


def test_the_report_proves_the_declared_NA_it_claims(tmp_path):
    """A reason token alone is not evidence: the reader re-reads the
    declaration's own bytes through `applicability_evidence`."""
    proj = _na_project(tmp_path)
    _, rep = area_gate.evaluate(proj, None, False)
    assert F._report_proves_executed_design_na(proj, rep, _CLAUSE) is True


def test_the_flow_no_longer_books_this_step_INCOMPLETE(tmp_path):
    """The end of the chain, driven through the reader's own clause runner."""
    proj = _na_project(tmp_path)
    passed, snippet = F._check_program_exit_zero(proj, _CLAUSE)
    assert passed is True
    assert not snippet.startswith("INCOMPLETE:")
    assert "EXECUTION_ERROR" not in snippet


# --------------------------------------------------------------------------- #
# negative controls: every other answer keeps its own tier
# --------------------------------------------------------------------------- #
def test_a_conflicting_L19_ceiling_is_still_INCOMPLETE_and_unclassed(tmp_path):
    """The design says NOT_APPLICABLE while L19 still declares a rectangle.
    Two design-owned authorities disagree and neither may silently win -- that
    is outstanding work, not a design-declared absence."""
    proj = S._project(tmp_path, built_on=S.LIB_B,
                      area_declaration="not_applicable",
                      die_budget="300x300")
    v, rep = area_gate.evaluate(proj, None, False)
    assert v == "INCOMPLETE"
    assert rep.get("reason_class") is None
    assert "conflict" in rep["missing_authority"]


def test_a_real_comparison_is_untouched(tmp_path):
    """The design's own L7 row, on its own technology: a live FAIL, no class."""
    proj = S._project(tmp_path, built_on=S.LIB_A,
                      area_declaration="not_applicable")
    v, rep = area_gate.evaluate(proj, None, False)
    assert v == "FAIL"
    assert rep.get("reason_class") is None


def test_a_declared_LIMIT_is_untouched(tmp_path):
    """A design that DID state a ceiling keeps the gate live."""
    proj = S._project(tmp_path, built_on=S.LIB_B, area_declaration="limit")
    v, rep = area_gate.evaluate(proj, None, False)
    assert v not in ("NOT_APPLICABLE",)
    assert rep.get("reason_class") is None


def test_the_evidence_is_refused_if_the_declaration_stops_saying_so(tmp_path):
    """The validator re-reads the file, so a report divorced from its
    declaration must not be accepted. One property changed: the bytes."""
    proj = _na_project(tmp_path)
    _, rep = area_gate.evaluate(proj, None, False)
    decl = proj / "input" / "submission_template" / "tapeout_declaration.json"
    doc = json.loads(decl.read_text())
    doc["synthesis_area_budget"]["status"] = "LIMIT"
    doc["synthesis_area_budget"]["max_die_dimensions_um"] = [300.0, 300.0]
    decl.write_text(json.dumps(doc))
    assert F._report_proves_executed_design_na(proj, rep, _CLAUSE) is False
