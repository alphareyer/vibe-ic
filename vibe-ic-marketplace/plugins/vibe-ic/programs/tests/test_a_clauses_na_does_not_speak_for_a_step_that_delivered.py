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


# ===========================================================================
# ROUND-2 REVIEW, 2026-09-23 — through the REAL entry point this time.
#
# Round 1's acceptance drove two pure functions and asserted the branch order
# by reading source. That could not see what the reviewer saw: the demotion
# removes the clause's SKIP hint and leaves the RAN hint the advisory branch
# appended for the SAME clause, so `ran_hints` still counts it as a clause
# that examined something. These drive `check_step` itself.
# ===========================================================================

import json as _json  # noqa: E402

_T_NOT_MEASURED = F._T.Verdict.NOT_MEASURED.value
import textwrap as _tw  # noqa: E402


def _gate_program(tmp_path, name, body):
    """A real program on PROGRAMS_DIR that check_step will actually invoke."""
    p = PROGRAMS / f"{name}.py"
    p.write_text(_tw.dedent(body))
    return p


def _project(tmp_path, sid=None):
    (tmp_path / "phase2" / "stage2" / "constraints").mkdir(parents=True,
                                                           exist_ok=True)
    (tmp_path / "phase2" / "stage2" / "constraints" / "spm.sdc").write_text(
        "create_clock -period 10 [get_ports clk]\n")
    (tmp_path / "phase2" / "stage2" / "constraints"
     / "pvt_matrix.json").write_text('{"corners": ["tt"]}\n')
    if sid:
        # THE STEP-WRITE LEDGER, which is what makes the outputs
        # STEP-ATTRIBUTED rather than merely present. Without it the step is
        # not "delivered" and the demotion correctly never fires -- so the
        # round-2 input needs it, and building it here is the difference
        # between exercising the defect and exercising its guard.
        folder = f"phase2/stage2/{sid}_round2_fixture"
        d = tmp_path / "steps" / folder
        d.mkdir(parents=True, exist_ok=True)
        (tmp_path / "steps" / "index.json").write_text(_json.dumps(
            {"steps": [{"id": sid, "folder": folder}]}))
        (d / "written.json").write_text(_json.dumps({
            "id": sid,
            "produced": [
                {"spec": "phase2/stage2/constraints/*.sdc",
                 "rel": "phase2/stage2/constraints/spm.sdc"},
                {"spec": "phase2/stage2/constraints/pvt_matrix.json",
                 "rel": "phase2/stage2/constraints/pvt_matrix.json"},
            ]}))
    return tmp_path


#: The reviewer's input, verbatim in shape: declared outputs present and
#: satisfied; one ADVISORY clause self-reporting SKIP / DESIGN_DECLARED_NA;
#: one program clause exiting 0 with a --json report declaring NOT_APPLICABLE.
def _step(g1, g2):
    return {
        "id": "T7",
        "name": "round-2 fixture",
        "stage": "stage2",
        "required_outputs": ["phase2/stage2/constraints/*.sdc",
                             "phase2/stage2/constraints/pvt_matrix.json"],
        "gate": {"all_of": [
            {"advisory_program_exit_zero": {
                "command": f"{g1} . --json reports/t7_advisory.json",
                "advisory_reason": "round-2 fixture: a declared-N/A clause"}},
            {"program_exit_zero": f"{g2} . --json reports/t7.json"},
        ]},
    }


def test_a_demoted_na_clause_does_not_count_as_examination(tmp_path):
    """THE ROUND-2 HIGH.

    The demoted clause examined NOTHING -- it declared itself N/A -- and the
    only other clause is vacuous. A step where nothing was examined must not
    be published as an executed PASS, and it must not lose the disclosure
    either. On the round-2 tip the RAN hint for the demoted clause survives,
    `ran_hints` is non-empty, the vacuous branches are bypassed, and the step
    lands in the final `else` as a bare PASS with no disclosure at all."""
    # The STRUCTURED channel, which is what `_advisory_execution_record`
    # reads: a --json report whose verdict is SKIP and whose reason_class is
    # DESIGN_DECLARED_NA. Printing the word alone is prose and is not read.
    g1 = _gate_program(tmp_path, "_t7_advisory_na", '''
        import json, sys
        i = sys.argv.index("--json")
        open(sys.argv[i + 1], "w").write(json.dumps(
            {"verdict": "SKIP", "reason_class": "DESIGN_DECLARED_NA",
             "examined": 0,
             "message": "no macro in this design; nothing to contract-check"}))
        print("SKIP: nothing here is applicable to this design")
        sys.exit(0)
        ''')
    g2 = _gate_program(tmp_path, "_t7_vacuous", '''
        import json, sys
        i = sys.argv.index("--json")
        open(sys.argv[i + 1], "w").write(json.dumps(
            {"verdict": "NOT_APPLICABLE", "examined": 0,
             "reason_class": "DESIGN_DECLARED_NA"}))
        print("VACUOUS_PASS: examined nothing (reason: no_subject)")
        sys.exit(0)
        ''')
    try:
        (tmp_path / "reports").mkdir(exist_ok=True)
        r = F.check_step(_project(tmp_path, "T7"),
                         _step(g1.stem, g2.stem), {}, None)
        joined = " ".join(r.reasons)

        # THE DENOMINATOR IS THE ASSERTION. On the round-2 tip this step read
        #   PASS | partial_vacuity | "1 of 2 gate clause(s) examined nothing"
        # because the demoted N/A clause kept its RAN hint and so counted as a
        # clause that examined the design. Only ONE clause ran, and it
        # examined nothing, so the honest reading is
        #   NOT_MEASURED | vacuity | "1 of 1 gate clause(s) that ran here".
        assert r.status == _T_NOT_MEASURED, (
            f"a step where nothing was examined was published {r.status}; "
            f"reasons={r.reasons}")
        # AMENDED at round 3, and the review is right: this pinned
        # "1 of 1 gate clause(s) that ran here" as correct. g1 DID dispatch
        # and DID examine nothing, so the truth is 2 of 2. What the assertion
        # was protecting -- the step must not read PARTIALLY-VACUOUS, because
        # every clause that ran examined nothing -- is unchanged and asserted
        # below; the count beside it is now true as well.
        assert "2 of 2 gate clause(s) that ran here" in joined, joined
        assert "PARTIALLY-VACUOUS" not in joined, (
            "the demoted clause was counted in the denominator: " + joined)
        # and the skip is still disclosed, because it is true.
        assert "DISCLOSED-SKIP" in joined, r.reasons
    finally:
        g1.unlink(missing_ok=True)
        g2.unlink(missing_ok=True)


def test_n_satisfied_counts_satisfaction_not_the_resolution_mode(tmp_path):
    """ROUND-2 LOW. `n_satisfied` counted only step_attributed specs, so every
    run without a step-write ledger -- which is every benchmark cell --
    published 0 satisfied outputs for a step whose outputs are all present."""
    g2 = _gate_program(tmp_path, "_t7_ok", '''
        import sys
        print("PASS")
        sys.exit(0)
        ''')
    try:
        step = {"id": "T7b", "name": "no-ledger", "stage": "stage2",
                "required_outputs": ["phase2/stage2/constraints/*.sdc",
                                     "phase2/stage2/constraints/pvt_matrix.json"],
                "gate": {"program_exit_zero": f"{g2.stem} ."}}
        r = F.check_step(_project(tmp_path), step, {}, None)
        b = r.output_binding or {}
        assert b.get("n_specs") == 2, b
        assert b.get("n_satisfied") == 2, (
            "both declared outputs are on disk; a run with no write-ledger "
            f"must still report them satisfied: {b}")
    finally:
        g2.unlink(missing_ok=True)


# ===========================================================================
# ROUND-3 REVIEW, 2026-09-23 — two LOWs. Both are the same mistake in two
# fields: a number published beside a fact it no longer matches.
# ===========================================================================

def test_the_published_clause_counts_are_true(tmp_path):
    """ROUND-3 L1. The demoted clause LEAVES the 'clauses that ran'
    denominator but never JOINS the 'examined nothing' numerator, so the
    sentence the step prints is arithmetically false about the step.

    My round-2 test pinned exactly the wrong sentence: it asserted
    "1 of 1 gate clause(s) that ran here" as the correct reading. g1 DID
    dispatch and DID examine nothing, so the truth is 2 of 2. Removing the
    clause from the tier is right; erasing it from the count is not -- it
    examined nothing, which is a fact worth counting, not a fact to hide."""
    g1 = _gate_program(tmp_path, "_t7r3_na", '''
        import json, sys
        i = sys.argv.index("--json")
        open(sys.argv[i + 1], "w").write(json.dumps(
            {"verdict": "SKIP", "reason_class": "DESIGN_DECLARED_NA",
             "examined": 0}))
        print("SKIP: n/a"); sys.exit(0)
        ''')
    g2 = _gate_program(tmp_path, "_t7r3_vac", '''
        import json, sys
        i = sys.argv.index("--json")
        open(sys.argv[i + 1], "w").write(json.dumps(
            {"verdict": "NOT_APPLICABLE", "examined": 0,
             "reason_class": "DESIGN_DECLARED_NA"}))
        print("VACUOUS_PASS: examined nothing (reason: no_subject)")
        sys.exit(0)
        ''')
    try:
        (tmp_path / "reports").mkdir(exist_ok=True)
        r = F.check_step(_project(tmp_path, "T7"),
                         _step(g1.stem, g2.stem), {}, None)
        joined = " ".join(r.reasons)
        assert r.status == _T_NOT_MEASURED, (r.status, r.reasons)
        # BOTH clauses dispatched and BOTH examined nothing.
        assert "2 of 2 gate clause(s)" in joined, joined
        assert "1 of 1 gate clause(s)" not in joined, joined
    finally:
        g1.unlink(missing_ok=True)
        g2.unlink(missing_ok=True)


def test_n_satisfied_follows_the_audit_created_retype(tmp_path):
    """ROUND-3 L2. `n_satisfied` is computed once, BEFORE the gate runs. The
    audit_created retype afterwards sets `specs[].satisfied = False` for an
    output that turns out to be the step's own gate `--json` target, and never
    recomputes the count. So a pass-2 row publishes `n_satisfied: 2` beside a
    spec marked audit_created/unsatisfied and a FAIL reading 'AUDIT-CREATED
    OUTPUT REFUSED'. The count contradicts the list it is a count of."""
    g = _gate_program(tmp_path, "_t7r3_auditcreated", '''
        import json, sys
        i = sys.argv.index("--json")
        open(sys.argv[i + 1], "w").write(json.dumps({"verdict": "PASS"}))
        print("PASS"); sys.exit(0)
        ''')
    try:
        (tmp_path / "reports").mkdir(exist_ok=True)
        step = {"id": "T7c", "name": "audit-created", "stage": "stage2",
                "required_outputs": ["phase2/stage2/constraints/*.sdc",
                                     "reports/t7c_gate.json"],
                "gate": {"program_exit_zero":
                         f"{g.stem} . --json reports/t7c_gate.json"}}
        # TWO PASSES, which is the reviewer's scenario and the only way the
        # defect shows. Pass 1 creates the gate's --json target and RECORDS it
        # as audit-created. Pass 2 then resolves it PRESENT (so
        # `_resolve_required_output`, which runs before the gate, counts it
        # satisfied and `n_satisfied` is computed as 2) and the audit_created
        # retype AFTER the tier chain marks it unsatisfied -- without
        # recomputing the count.
        F.check_step(_project(tmp_path), step, {}, None)
        r = F.check_step(_project(tmp_path), step, {}, None)
        b = r.output_binding or {}
        specs = b.get("specs") or []
        n_sat_listed = sum(1 for d in specs if d.get("satisfied"))
        assert b.get("n_satisfied") == n_sat_listed, (
            f"the count contradicts the list it counts: "
            f"n_satisfied={b.get('n_satisfied')} but {n_sat_listed} of "
            f"{len(specs)} specs are satisfied; specs={specs}")
    finally:
        g.unlink(missing_ok=True)
