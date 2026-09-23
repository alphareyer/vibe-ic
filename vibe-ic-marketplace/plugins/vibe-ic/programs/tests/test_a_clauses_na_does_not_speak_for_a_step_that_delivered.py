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
    """The binding the rule reads."""
    def __init__(self, binding):
        self.output_binding = binding


def _delivered(n=2, sat=None, k=None):
    """A step that DELIVERED, in the shape the real producer publishes.

    `_disclose_output_binding` always emits a per-spec list beside the counts
    (`output_binding["specs"]`), and the predicate reads it to answer the one
    question the counts cannot: is any satisfied output blocked by the step's
    own binding policy. A fixture that omits it is not a delivered step, it is
    an unanswerable binding -- so the fixtures below say what they mean.
    """
    sat = n if sat is None else sat
    k = n if k is None else k
    return _R({"n_specs": n, "n_step_attributed": k, "n_satisfied": sat,
               "specs": [{"mode": "step_attributed", "code": "step_record",
                          "satisfied": i < sat} for i in range(n)]})


# ------------------------------------------------------------------ POSITIVE

def test_every_declared_output_produced_by_this_step_is_the_only_yes():
    """AMENDED after the pre-landing review: `n_step_attributed == n_specs`
    is no longer enough on its own. It counts the resolution MODE, and a spec
    that was RECORDED AS WRITTEN AND IS ABSENT resolves step_attributed with
    satisfied=False. The delivery claim now needs `n_satisfied` too."""
    _ok = {"mode": "step_attributed", "code": "step_record",
           "satisfied": True}
    assert F._step_produced_every_declared_output(
        _R({"n_specs": 2, "n_step_attributed": 2, "n_satisfied": 2,
            "specs": [dict(_ok), dict(_ok)]})) is True
    # AMENDED AGAIN by the final review: without a spec list covering every
    # declared output the blocking question cannot be answered, and an
    # unanswerable binding is refused rather than assumed clean.
    assert F._step_produced_every_declared_output(
        _R({"n_specs": 2, "n_step_attributed": 2, "n_satisfied": 2})) is False


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


def test_the_standard_is_the_steps_own_binding_not_step_attribution():
    """REPLACED by the final review, and the replacement is the RULING.

    The deleted version asserted that a project-glob resolution never speaks
    for a step, and made `n_step_attributed == n_specs` a condition. That is
    STRICTER than the standard the step itself is held to, which R-0915-152
    forbids: `check_step` blocks a satisfied project-glob output only when
    `_binding_code_blocks` says so -- always for `no_step_record`, and for
    `no_binding` only under `--strict-step-binding`. A run with no step-write
    ledger at all (every benchmark cell, and run22) resolves every spec
    project-glob/`no_binding`, so the old condition made this branch INERT on
    exactly the runs it exists for while the clause-deleted step passed.

    So: satisfied and non-blocking is a yes, whatever the mode."""
    _glob = {"mode": "project_glob", "code": "no_binding", "satisfied": True}
    assert F._step_produced_every_declared_output(
        _R({"n_specs": 2, "n_step_attributed": 0, "n_satisfied": 2,
            "specs": [dict(_glob), dict(_glob)]})) is True


def test_a_blocking_binding_code_is_not_a_step_that_delivered():
    """The other direction, one spec at a time. `no_step_record` means this
    run DID emit a ledger and it does not mention this spec -- always
    blocking, so never a delivery. `no_binding` blocks only under the
    producer-migration flag, and the predicate must read that flag rather
    than guess."""
    def _b(code, **kw):
        sp = {"mode": "project_glob", "code": code, "satisfied": True}
        return _R({"n_specs": 2, "n_step_attributed": 0, "n_satisfied": 2,
                   "specs": [{"mode": "step_attributed",
                              "code": "step_record", "satisfied": True},
                             sp]}), kw
    r, _ = _b("no_step_record")
    assert F._step_produced_every_declared_output(r) is False
    r, _ = _b("no_binding")
    assert F._step_produced_every_declared_output(r) is True
    assert F._step_produced_every_declared_output(
        r, strict_step_binding=True) is False


def test_an_unanswerable_binding_refuses_to_demote():
    """FAIL CLOSED, and the direction is deliberate. `output_binding["specs"]`
    is capped at 16 for report size and verdict logic must never inherit a
    display cap, so a spec list that does not cover every declared output
    cannot answer "is any of them blocked". Refusing leaves the N/A clause
    speaking for the step, which makes the run look WORSE, never better."""
    _ok = {"mode": "step_attributed", "code": "step_record",
           "satisfied": True}
    for specs in ([dict(_ok)], [], None, "2", [dict(_ok), "x"]):
        assert F._step_produced_every_declared_output(
            _R({"n_specs": 2, "n_step_attributed": 2, "n_satisfied": 2,
                "specs": specs})) is False, specs


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
    delivered = _delivered(2)
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
    delivered = _delivered(1)
    kept, demoted = F._skips_that_do_not_speak_for_the_step(
        [f"{F._SKIP_HINT_PREFIX}some_gate: artifact self-reports a skip"],
        delivered)
    assert demoted == [] and len(kept) == 1


def test_a_mixed_set_is_not_demoted_at_all():
    """If ANY skip is a real capability gap, the step is still skipped and the
    tier must keep coming from the clauses."""
    delivered = _delivered(1)
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
        _delivered(3, sat=2)) is False
    assert F._step_produced_every_declared_output(
        _delivered(3, sat=3)) is True
    # A binding with no satisfaction count at all cannot answer the question.
    assert F._step_produced_every_declared_output(
        _R({"n_specs": 3, "n_step_attributed": 3})) is False


def test_the_demoted_skip_is_still_disclosed_on_the_row():
    """MEDIUM. The clause's skip is TRUE and a reader must see it. The guard
    skipped the only branch that appends those lines, so the disclosure
    vanished with the tier."""
    delivered = _delivered(1)
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
    assert "reasons = [r for r in reasons if r not in _demoted_reasons]" \
        in src[i - 3000:j], (
            "the demotion must remove the clause from the REASON STREAM, so "
            "every channel derived from it is covered at once")


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
        # ROUND 4: the demoted clause is now ABSENT FROM THE GATE, so the
        # step is judged exactly as it would be with that clause deleted from
        # the YAML -- one clause, vacuous, "1 of 1". Round 3 asserted "2 of 2"
        # because the clause was then in the denominator's world but not the
        # numerator's; removing it from both is what makes one sentence true.
        assert "1 of 1 gate clause(s) that ran here" in joined, joined
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
        assert "1 of 1 gate clause(s)" in joined, joined
        assert "PARTIALLY-VACUOUS" not in joined, joined
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


# ===========================================================================
# ROUND-4 — THE INVARIANT, not another per-shape test.
#
# Rounds 2, 3 and 4 each closed ONE channel and the clause leaked through the
# next: the tier (via skip_hints), the legacy count sentences (via ran_hints),
# then the json-vacuous branch, the post-hoc PARTIALLY-VACUOUS sentence and
# the NOT-APPLICABLE(declared) sentence. Three rounds of per-shape tests never
# found the next channel, because each one asserted the shape it was written
# for.
#
# The property is simple and covers all of them at once:
#
#   A STEP WITH A DEMOTED CLAUSE MUST BE JUDGED EXACTLY AS THE SAME STEP WITH
#   THAT CLAUSE DELETED FROM THE FLOW YAML.
#
# Same status, same reason_class, same disclosures, same count sentences. The
# only permitted difference is the DISCLOSED-SKIP line, which exists precisely
# to say the clause was there.
# ===========================================================================

import itertools as _it  # noqa: E402

_NA_ADVISORY = '''
    import json, sys
    i = sys.argv.index("--json")
    open(sys.argv[i + 1], "w").write(json.dumps(
        {"verdict": "SKIP", "reason_class": "DESIGN_DECLARED_NA",
         "examined": 0}))
    print("SKIP: n/a"); sys.exit(0)
    '''

#: The other clause, in each channel it can speak through.
_CHANNELS = {
    "legacy_vacuous": ('''
        import sys
        print("VACUOUS_PASS: examined nothing (reason: no_subject)")
        sys.exit(0)
        ''', False),
    "json_vacuous": ('''
        import json, sys
        i = sys.argv.index("--json")
        open(sys.argv[i + 1], "w").write(json.dumps(
            {"verdict": "NOT_APPLICABLE", "examined": 0,
             "reason_class": "DESIGN_DECLARED_NA"}))
        print("PASS"); sys.exit(0)
        ''', True),
    "substantive": ('''
        import json, sys
        i = sys.argv.index("--json")
        open(sys.argv[i + 1], "w").write(json.dumps(
            {"verdict": "PASS", "examined": 7}))
        print("PASS"); sys.exit(0)
        ''', True),
}


def _judge(tmp_path, other_body, other_json, with_na, tag):
    """check_step over a delivered step, with or without the N/A clause."""
    progs = []
    clauses = []
    if with_na:
        g1 = _gate_program(tmp_path, f"_p_{tag}_na", _NA_ADVISORY)
        progs.append(g1)
        clauses.append({"advisory_program_exit_zero": {
            "command": f"{g1.stem} . --json reports/{tag}_na.json",
            "advisory_reason": "property fixture"}})
    g2 = _gate_program(tmp_path, f"_p_{tag}_other", other_body)
    progs.append(g2)
    clauses.append({"program_exit_zero": (
        f"{g2.stem} . --json reports/{tag}_o.json" if other_json
        else f"{g2.stem} .")})
    root = tmp_path / ("with" if with_na else "without")
    root.mkdir(parents=True, exist_ok=True)
    (root / "reports").mkdir(exist_ok=True)
    _project(root, "PT")
    step = {"id": "PT", "name": "property", "stage": "stage2",
            "required_outputs": ["phase2/stage2/constraints/*.sdc",
                                 "phase2/stage2/constraints/pvt_matrix.json"],
            "gate": {"all_of": clauses}}
    try:
        r = F.check_step(root, step, {}, None)
        # THE ONLY PERMITTED DIFFERENCE IS DISCLOSURE *OF THAT CLAUSE*. Both
        # the `DISCLOSED-SKIP` line and the clause's own `GATE EVIDENCE` line
        # exist to say it was there, which is the point -- a demoted clause
        # must not vanish from the row. Everything else, including every count
        # sentence, must be identical to the step without it.
        # NARROW, so a future leak cannot hide behind the filter. Only the
        # two lines that EXIST to disclose the clause are excluded -- the
        # `DISCLOSED-SKIP` line and the clause's own `GATE EVIDENCE` line. Any
        # OTHER line naming the demoted program is a leak and must fail the
        # comparison. (Round 5: the filter used to drop every line mentioning
        # the program, which would have hidden exactly that.)
        _na_name = f"_p_{tag}_na"
        _disclosure = ("DISCLOSED-SKIP", "GATE EVIDENCE: ")
        lines = [x for x in r.reasons
                 if not (str(x).startswith(_disclosure)
                         and _na_name in str(x))
                 and "DISCLOSED-SKIP" not in str(x)]
        # The fixture's own program names differ between arms only by the
        # clause that is meant to be absent; normalise so the comparison is
        # about the JUDGEMENT, not the spelling.
        lines = [str(x).replace(f"_p_{tag}_", "_p_") for x in lines]
        return (r.status, r.reason_class, tuple(r.disclosures or ()),
                tuple(sorted(lines)))
    finally:
        for g in progs:
            g.unlink(missing_ok=True)


def test_a_demoted_clause_is_judged_as_if_it_were_not_declared(tmp_path):
    """THE INVARIANT. Every channel, one assertion."""
    for name, (body, uses_json) in _CHANNELS.items():
        with_na = _judge(tmp_path / name, body, uses_json, True, name)
        without = _judge(tmp_path / (name + "_x"), body, uses_json, False, name)
        assert with_na == without, (
            f"channel {name}: a demoted N/A clause changed the judgement.\n"
            f"  with the clause : {with_na}\n"
            f"  clause deleted  : {without}")


def test_an_audit_created_output_is_not_a_step_that_delivered(tmp_path):
    """ROUND-4 MEDIUM. "Produced every declared output" means produced by the
    RUN. An output that is this step's own gate `--json` target was written by
    the AUDIT, and the tree refuses it as run evidence a few lines later --
    shipped step 14's `stage_analog_compliance.json` is exactly that shape.

    Deciding the demotion from the pre-gate count let one row say both
    "produced every output it declares" and `n_satisfied 1/2`, with a FAIL
    reading "AUDIT-CREATED OUTPUT REFUSED". Such a step is not delivered, so
    its N/A clause keeps speaking for it and the tier stays where the base
    puts it."""
    g1 = _gate_program(tmp_path, "_t7r4_na", _NA_ADVISORY)
    g2 = _gate_program(tmp_path, "_t7r4_gate", '''
        import json, sys
        i = sys.argv.index("--json")
        open(sys.argv[i + 1], "w").write(json.dumps({"verdict": "PASS"}))
        print("PASS"); sys.exit(0)
        ''')
    try:
        (tmp_path / "reports").mkdir(exist_ok=True)
        step = {"id": "T7d", "name": "audit-created + N/A", "stage": "stage2",
                "required_outputs": ["phase2/stage2/constraints/*.sdc",
                                     "reports/t7d_gate.json"],
                "gate": {"all_of": [
                    {"advisory_program_exit_zero": {
                        "command": f"{g1.stem} . --json reports/t7d_na.json",
                        "advisory_reason": "round-4 fixture"}},
                    {"program_exit_zero":
                     f"{g2.stem} . --json reports/t7d_gate.json"}]}}
        proj = _project(tmp_path)
        # A LEDGER THAT ATTRIBUTES BOTH SPECS, so the ONLY thing that can stop
        # the demotion is the audit-created one. Without this the predicate is
        # already False for an unrelated reason and the test passes without
        # exercising anything -- which is how my first version of it passed
        # against the defect.
        folder = "phase2/stage2/T7d_f"
        (proj / "steps" / folder).mkdir(parents=True, exist_ok=True)
        (proj / "steps" / "index.json").write_text(_json.dumps(
            {"steps": [{"id": "T7d", "folder": folder}]}))
        (proj / "steps" / folder / "written.json").write_text(_json.dumps({
            "id": "T7d", "produced": [
                {"spec": "phase2/stage2/constraints/*.sdc",
                 "rel": "phase2/stage2/constraints/spm.sdc"},
                {"spec": "reports/t7d_gate.json",
                 "rel": "reports/t7d_gate.json"}]}))
        F.check_step(proj, step, {}, None)          # pass 1 records it
        r = F.check_step(proj, step, {}, None)      # pass 2 retypes it
        joined = " ".join(str(x) for x in r.reasons)
        assert "DISCLOSED-SKIP" not in joined, (
            "a step whose declared output was written by its own gate did not "
            f"produce every output it declares: {r.reasons}")
        b = r.output_binding or {}
        assert b.get("n_satisfied") == sum(
            1 for d in (b.get("specs") or []) if d.get("satisfied")), b
    finally:
        g1.unlink(missing_ok=True)
        g2.unlink(missing_ok=True)



def test_an_entry_step_run_judges_the_step_as_the_clause_deleted_one(tmp_path):
    """R-0915-152, and round 5's MEDIUM answered by the ruling rather than by
    a run window.

    An `--entry-step` run enters mid-flow over outputs an earlier run wrote.
    Rounds 5-7 tried to make the demotion refuse that case, through a run
    window and then a run identity, and each attempt opened a new hole. The
    ruling says the demotion may not impose a standard the STEP ITSELF is not
    held to: a clause-deleted step judges those same outputs through the same
    binding and passes, so the demoted step must too. Cross-run freshness is
    the flow's business -- the entry manifest and run admission -- and it
    applies to every step's PASS equally.

    So the invariant is the same one the property test asserts, measured on
    the shape that drove eight rounds: outputs on disk from an earlier run,
    no ledger window anywhere."""
    g1 = _gate_program(tmp_path, "_t7r8_na", _NA_ADVISORY)
    g2 = _gate_program(tmp_path, "_t7r8_vac", '''
        import json, sys
        i = sys.argv.index("--json")
        open(sys.argv[i + 1], "w").write(json.dumps(
            {"verdict": "NOT_APPLICABLE", "examined": 0,
             "reason_class": "DESIGN_DECLARED_NA"}))
        print("VACUOUS_PASS: examined nothing (reason: no_subject)")
        sys.exit(0)
        ''')
    try:
        import os as _os, time as _time
        # An earlier run's outputs: present, step-attributed, and OLD.
        with_na = _project(tmp_path / "with", "T7")
        (with_na / "reports").mkdir(exist_ok=True)
        without = _project(tmp_path / "without", "T7")
        (without / "reports").mkdir(exist_ok=True)
        old = _time.time() - 12 * 86400
        for root in (with_na, without):
            for f in (root / "phase2" / "stage2" / "constraints").iterdir():
                _os.utime(f, (old, old))

        step_with = _step(g1.stem, g2.stem)
        step_without = {**step_with,
                        "gate": {"all_of": [step_with["gate"]["all_of"][1]]}}
        r_with = F.check_step(with_na, step_with, {}, None)
        r_without = F.check_step(without, step_without, {}, None)

        assert r_with.status == r_without.status, (
            r_with.status, r_without.status, r_with.reasons)
        assert r_with.reason_class == r_without.reason_class
        assert tuple(r_with.disclosures or ()) == tuple(
            r_without.disclosures or ())
        kept = [x for x in r_with.reasons
                if "DISCLOSED-SKIP" not in str(x) and g1.stem not in str(x)]
        assert any("1 of 1 gate clause(s)" in str(x) for x in kept), kept
    finally:
        g1.unlink(missing_ok=True)
        g2.unlink(missing_ok=True)


# ===========================================================================
# FINAL REVIEW, 2026-09-23 — the branch was INERT on the runs it exists for.
#
# `_step_produced_every_declared_output` demanded `n_step_attributed ==
# n_specs`. A run with no step-write ledger resolves every spec project-glob
# with code `no_binding`, so k=0 and the demotion never fired -- while the
# same step with the clause DELETED passed, because `no_binding` blocks only
# under `--strict-step-binding`. run22 is exactly that shape. These two drive
# `check_step` on a project with NO ledger, which is the only fixture that
# can tell the two standards apart.
# ===========================================================================

def _no_ledger_arms(tmp_path, tag, strict):
    """(with the N/A clause, with it deleted) on a project that has no
    step-write ledger anywhere -- outputs on disk, nothing attributing them."""
    g1 = _gate_program(tmp_path, f"_{tag}_na", _NA_ADVISORY)
    g2 = _gate_program(tmp_path, f"_{tag}_vac", _CHANNELS["legacy_vacuous"][0])
    try:
        step_with = {
            "id": "NL", "name": "no-ledger fixture", "stage": "stage2",
            "required_outputs": ["phase2/stage2/constraints/*.sdc",
                                 "phase2/stage2/constraints/pvt_matrix.json"],
            "gate": {"all_of": [
                {"advisory_program_exit_zero": {
                    "command": f"{g1.stem} . --json reports/{tag}_na.json",
                    "advisory_reason": "no-ledger fixture"}},
                {"program_exit_zero": f"{g2.stem} ."}]}}
        step_without = {**step_with,
                        "gate": {"all_of": [step_with["gate"]["all_of"][1]]}}
        out = []
        for name, step in (("with", step_with), ("without", step_without)):
            root = tmp_path / f"{tag}_{name}"
            root.mkdir(parents=True, exist_ok=True)
            # sid=None: NO steps/index.json, NO written.json, NO run ledger.
            _project(root)
            (root / "reports").mkdir(exist_ok=True)
            out.append(F.check_step(root, step, {}, None,
                                    strict_step_binding=strict))
        return out
    finally:
        g1.unlink(missing_ok=True)
        g2.unlink(missing_ok=True)


def test_a_run_without_a_step_write_ledger_judges_as_the_clause_deleted_one(
        tmp_path):
    """THE RUN22 SHAPE, and the invariant that was false on it.

    Measured before the fix: `NOT_APPLICABLE / missing_artefact` with the
    clause, `NOT_MEASURED / no_population` with it deleted. The clause spoke
    for the step on every run that emits no ledger -- which is every run the
    demotion was written for."""
    r_with, r_without = _no_ledger_arms(tmp_path, "nl1", False)
    assert (r_with.status, r_with.reason_class) == (
        r_without.status, r_without.reason_class), (
            f"a demoted N/A clause changed the judgement on a run with no "
            f"step-write ledger.\n  with the clause : "
            f"{r_with.status}/{r_with.reason_class} {r_with.reasons}\n"
            f"  clause deleted  : "
            f"{r_without.status}/{r_without.reason_class}")
    # AND THE BINDING IS THE DEGRADED ONE, so the arm cannot pass by
    # accidentally acquiring attribution and testing the other branch.
    b = r_with.output_binding or {}
    assert b.get("n_step_attributed") == 0 and b.get("codes") == ["no_binding"]
    assert b.get("n_satisfied") == b.get("n_specs") == 2, b


def test_strict_step_binding_blocks_the_demotion_as_it_blocks_the_step(
        tmp_path):
    """THE FLAG, BOTH DIRECTIONS, one project. Under `--strict-step-binding`
    a satisfied `no_binding` output stops certifying its step, so the step has
    NOT produced every declared output by its own standard and its N/A clause
    keeps speaking for it.

    The two arms are NOT asserted equal here, and deliberately: the
    clause-deleted step takes the `UNATTRIBUTED OUTPUT` FAIL, which a
    `NOT_APPLICABLE` row is structurally exempt from (`_credits_its_outputs`
    excludes it). What must hold is that the demotion does not fire -- keyed
    on the flag alone, with the non-strict arm above as the control."""
    r_with, r_without = _no_ledger_arms(tmp_path, "nl2", True)
    joined = " ".join(str(x) for x in r_with.reasons)
    assert "SKIPPED-CONDITION" in joined, (
        "under --strict-step-binding the N/A clause must still set the tier: "
        f"{r_with.reasons}")
    assert r_with.status == F._T.Verdict.NOT_APPLICABLE.value, r_with.status
    assert r_without.status == F._T.Verdict.FAIL.value, r_without.status
    assert "UNATTRIBUTED OUTPUT" in " ".join(
        str(x) for x in r_without.reasons), r_without.reasons


def test_the_one_reader_survives_a_two_line_vacuous_payload():
    """REBASE RE-CHECK, and it found a latent leak the rebase introduced.

    R-0915-135 gave a vacuous hint a payload of `<clause>` plus an optional
    DIAGNOSTIC line. `_hint_command` only strips a trailing `[verdict=...]`
    tail, so `_reason_names_command` returned `clause + "\\n" + diagnostic` for
    those two channels -- a string no demoted clause's command can ever equal.
    A demoted N/A clause that also spoke through the vacuous channel would
    therefore keep its vacuous hint, and `all_vacuous_cmds` (which is keyed on
    the CLAUSE, via `split_vacuous_payload`) would still count it. That is the
    per-channel leak round 4 ruled out by making this the ONE reader; the fix
    is the same one `all_vacuous_cmds` already uses.

    BOTH DIRECTIONS: with and without the diagnostic, and against the SKIP
    hint for the same clause, which is the equality the demotion performs."""
    cmd = "macro_non_seq_arc_contract_check ."
    skip = F._reason_names_command(
        f"{F._SKIP_HINT_PREFIX}{cmd} [verdict=SKIP, reason_class={_NA}]")
    assert skip == cmd
    for pref in (F._VACUOUS_HINT_PREFIX, F._JSON_VACUOUS_HINT_PREFIX):
        assert F._reason_names_command(f"{pref}{cmd}") == cmd, pref
        assert F._reason_names_command(
            f"{pref}{cmd}\nstated_class=NO_POPULATION; nothing to examine"
        ) == cmd, pref
        # ...and it is the SAME handle the tier count is keyed on, so the
        # demotion and `all_vacuous_cmds` can never disagree about a clause.
        assert F.split_vacuous_payload(f"{cmd}\ndiag")[0] == cmd
