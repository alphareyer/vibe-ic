"""A clause's DESIGN_DECLARED_NA is that clause's, not the step's.

MEASURED on spm run22 (lane icspm5, 2026-09-23). Step 7, "Constraint setup
(SDC + PVT matrix)", declares

    required_outputs: phase2/stage2/constraints/*.sdc
                      phase2/stage2/constraints/pvt_matrix.json

and BOTH were on disk -- spm.sdc 2,761 B and pvt_matrix.json 1,163 B -- which
the step's own row states in the flow's own words:

    OUTPUT ATTRIBUTION: step-attributed (2/2 declared output(s) resolved
    against THIS step's own write record in steps/<phase>/<stage>/<id>_<slug>/
    written.json, re-verified live)

One of its nineteen clauses, `macro_non_seq_arc_contract_check`, honestly
self-reported [verdict=SKIP, reason_class=DESIGN_DECLARED_NA]. That single
clause set the tier of the WHOLE STEP to NOT_APPLICABLE, and the stage-2
classifier then published it as a stage-BLOCKING MISSING_CAPABILITY /
disclosed-capability-gap, whose text reads "the runner disclosed a named
capability gap in place of the sign-off artefact this step declares" -- over a
step that had produced that artefact.

A step that produced everything it declared was not skipped. The clause's skip
stays DISCLOSED on the row, because it is true and a reader must see it; what
it no longer does is speak for the step.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import flow_compliance_check as F  # noqa: E402


class _R:
    """The one field the rule reads."""
    def __init__(self, binding):
        self.output_binding = binding


# ------------------------------------------------------------------ POSITIVE

def test_every_declared_output_produced_by_this_step_is_the_only_yes():
    assert F._step_produced_every_declared_output(
        _R({"n_specs": 2, "n_step_attributed": 2})) is True


# ------------------------------------------------------------------ NEGATIVE

def test_a_step_that_declares_no_outputs_cannot_have_produced_them():
    """Most steps are here, and their tier must keep coming from their
    clauses."""
    for binding in ({"n_specs": 0, "n_step_attributed": 0},
                    {"n_specs": 0}, {}):
        assert F._step_produced_every_declared_output(_R(binding)) is False


def test_a_partially_delivered_step_is_not_a_delivered_one():
    assert F._step_produced_every_declared_output(
        _R({"n_specs": 2, "n_step_attributed": 1})) is False
    assert F._step_produced_every_declared_output(
        _R({"n_specs": 2, "n_step_attributed": 0})) is False


def test_project_wide_resolution_never_speaks_for_a_step():
    """`n_step_attributed` answers "THIS step produced it". The project-wide
    glob answers only "a file matching this pattern exists somewhere under the
    project", and the flow's own OUTPUT ATTRIBUTION line says so. A step whose
    outputs were resolved that way has not been shown to have produced
    anything, so its clauses keep the tier."""
    assert F._step_produced_every_declared_output(
        _R({"n_specs": 2, "n_step_attributed": 0, "mode": "project_wide"})
    ) is False


def test_a_missing_or_malformed_binding_changes_nothing():
    """Fail-safe: with no binding the rule does not fire and the pre-existing
    branch is reached exactly as before."""
    for binding in (None, [], "2/2", 2):
        assert F._step_produced_every_declared_output(_R(binding)) is False
    class _NoField:
        pass
    assert F._step_produced_every_declared_output(_NoField()) is False


def test_non_integer_counts_are_refused():
    for binding in ({"n_specs": "2", "n_step_attributed": 2},
                    {"n_specs": 2, "n_step_attributed": "2"},
                    {"n_specs": 2, "n_step_attributed": None}):
        assert F._step_produced_every_declared_output(_R(binding)) is False


# ------------------------------------------------- the branch, by source shape

def test_the_skip_branch_asks_the_question_before_it_sets_the_tier():
    """The guard has to sit ON the branch that assigns NOT_APPLICABLE, not
    beside it -- a check that runs after the assignment would report a
    contradiction rather than prevent one."""
    src = (PROGRAMS / "flow_compliance_check.py").read_text()
    i = src.index("elif (passed and skip_hints and not non_hint_reasons")
    seg = src[i:i + 260]
    assert "_step_produced_every_declared_output(result)" in seg, seg[:200]
    assert "not _step_produced_every_declared_output" in seg, (
        "the branch must be SKIPPED when the step delivered, not entered")
