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
    """AMENDED after the pre-landing review: `n_step_attributed == n_specs`
    is no longer enough on its own. It counts the resolution MODE, and a spec
    that was RECORDED AS WRITTEN AND IS ABSENT resolves step_attributed with
    satisfied=False. The delivery claim now needs `n_satisfied` too."""
    assert F._step_produced_every_declared_output(
        _R({"n_specs": 2, "n_step_attributed": 2, "n_satisfied": 2})) is True


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

def test_the_skip_branch_is_reached_by_its_own_condition_again():
    """RETRACTED AND REVERSED by the pre-landing review.

    This asserted that the guard sat ON the skip branch -- that the branch was
    SKIPPED when the step delivered. That is precisely the defect: every
    branch after it requires `not skip_hints`, so skipping it dropped the step
    past the waiver, substantive, vacuous and json-vacuous tiers into a bare
    PASS. The branch condition is restored to what it was, and the decision
    moved UPSTREAM of the chain -- see
    `test_the_tier_chain_sees_the_demotion_not_a_bypass`."""
    src = (PROGRAMS / "flow_compliance_check.py").read_text()
    i = src.index("elif passed and skip_hints and not non_hint_reasons:")
    seg = src[i:i + 120]
    assert "_step_produced_every_declared_output" not in seg, (
        "the decision must not be a condition on this branch")


# ===========================================================================
# PRE-LANDING REVIEW, 2026-09-23 — one CONFIRMED high and two mediums. The
# guard was a BYPASS of the skip branch, and a bypass is not a decision.
# ===========================================================================

_NA = "DESIGN_DECLARED_NA"


def _hint(cmd, cls=_NA, verdict="SKIP"):
    return f"{F._SKIP_HINT_PREFIX}{cmd} [verdict={verdict}, reason_class={cls}]"


def test_only_a_declared_na_skip_is_demoted(tmp_path=None):
    """MEDIUM. The guard never read `reason_class`. Advisory DISCLOSED_SKIP
    covers every SKIP_ELIGIBLE class, and CAPABILITY_ABSENT / EXTERNAL emit
    the SAME `__SKIP_HINT__` as DESIGN_DECLARED_NA. A capability gap that
    stops speaking for its step leaves `oss_blocked_skipped`, loses
    `self_skip_disclosed`, and the run can publish PASS with the gap
    invisible -- which is the opposite of what this change is for."""
    delivered = _R({"n_specs": 2, "n_step_attributed": 2, "n_satisfied": 2})
    kept, demoted = F._skips_that_do_not_speak_for_the_step(
        [_hint("macro_non_seq_arc_contract_check")], delivered)
    assert kept == [] and len(demoted) == 1

    for cls in ("CAPABILITY_ABSENT", "EXTERNAL", "ASKED_BEFORE_PRODUCER"):
        kept, demoted = F._skips_that_do_not_speak_for_the_step(
            [_hint("analog_corner_lib_realism_lint", cls)], delivered)
        assert demoted == [] and len(kept) == 1, cls


def test_a_hint_that_names_no_class_is_never_demoted():
    """Fail closed: not every skip-hint site writes `reason_class=`, and a
    hint whose class cannot be read is not evidence that it is an N/A."""
    delivered = _R({"n_specs": 1, "n_step_attributed": 1, "n_satisfied": 1})
    kept, demoted = F._skips_that_do_not_speak_for_the_step(
        [f"{F._SKIP_HINT_PREFIX}some_gate: artifact self-reports a skip"],
        delivered)
    assert demoted == [] and len(kept) == 1


def test_a_mixed_set_is_not_demoted_at_all():
    """If ANY skip is a real capability gap, the step is still skipped and the
    tier must keep coming from the clauses."""
    delivered = _R({"n_specs": 1, "n_step_attributed": 1, "n_satisfied": 1})
    kept, demoted = F._skips_that_do_not_speak_for_the_step(
        [_hint("a"), _hint("b", "CAPABILITY_ABSENT")], delivered)
    assert demoted == [] and len(kept) == 2


def test_step_attributed_is_not_satisfied():
    """MEDIUM. `n_step_attributed` counts the resolution MODE, not whether the
    output is there. `_resolve_required_output` returns mode
    `step_attributed` with satisfied=False for wildcard_unbound,
    recorded_but_absent and not_produced -- so a step whose declared output
    was RECORDED AS WRITTEN AND IS ABSENT read k==n and was called delivered.
    On the formal step that turned an honest SKIPPED-CONDITION into
    FAIL(missing_artefact), which is the #675 cascade this tree removed."""
    assert F._step_produced_every_declared_output(
        _R({"n_specs": 3, "n_step_attributed": 3, "n_satisfied": 2})) is False
    assert F._step_produced_every_declared_output(
        _R({"n_specs": 3, "n_step_attributed": 3, "n_satisfied": 3})) is True
    # A binding with no satisfaction count at all cannot answer the question.
    assert F._step_produced_every_declared_output(
        _R({"n_specs": 3, "n_step_attributed": 3})) is False


def test_the_demoted_skip_is_still_disclosed_on_the_row():
    """MEDIUM. The clause's skip is TRUE and a reader must see it. The guard
    skipped the only branch that appends those lines, so the disclosure
    vanished with the tier."""
    delivered = _R({"n_specs": 1, "n_step_attributed": 1, "n_satisfied": 1})
    _kept, demoted = F._skips_that_do_not_speak_for_the_step(
        [_hint("macro_non_seq_arc_contract_check")], delivered)
    lines = F._demoted_skip_disclosures(demoted)
    assert lines and "macro_non_seq_arc_contract_check" in lines[0]
    assert "DESIGN_DECLARED_NA" in lines[0]
    assert "did not set this step's tier" in lines[0], lines


def test_a_step_that_did_not_deliver_keeps_every_skip():
    """The negative arm, and the common case."""
    for binding in ({"n_specs": 0}, {"n_specs": 2, "n_step_attributed": 1,
                                     "n_satisfied": 1}, {}):
        kept, demoted = F._skips_that_do_not_speak_for_the_step(
            [_hint("x")], _R(binding))
        assert demoted == [] and len(kept) == 1, binding


def test_the_tier_chain_sees_the_demotion_not_a_bypass():
    """THE HIGH, by source shape, and stated as such.

    Every branch after the skip branch requires `not skip_hints` -- waiver,
    substantive-vacuous, vacuous, json-vacuous. So SKIPPING the skip branch
    dropped the step past all of them into the final `else`, which sets a bare
    PASS: an all-vacuous step with one N/A clause was RAISED from
    NOT_MEASURED to an executed PASS, and a step with a waiver lost
    PASS_WITH_WAIVERS. The fix cannot be a condition on that one branch; the
    hints must be REMOVED from `skip_hints` before the chain runs, so the
    remaining tiers judge the step by its other clauses."""
    src = (PROGRAMS / "flow_compliance_check.py").read_text()
    i = src.index("_skips_that_do_not_speak_for_the_step(")
    j = src.index("elif passed and skip_hints and not non_hint_reasons:")
    assert i < j, ("the demotion must happen BEFORE the tier chain, or the "
                   "later tiers still see the skip hints")
    assert "skip_hints, _demoted_skips = " in src[i - 200:j], (
        "the demotion must rebind skip_hints, not merely compute a boolean")
