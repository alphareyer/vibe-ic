#!/usr/bin/env python3
"""`programs/verdict.py` — every rule, exercised in BOTH directions.

R-0915-85 reduced the flow's step vocabulary to five words at its PRODUCERS.
The two runs that made the case are in the module's own DESIGN section; this
file is the executable half of it.

"Both directions" is meant literally and is why the file is long: for each rule
there is a case the rule must ACCEPT and a case it must REFUSE. A rule tested
only in the direction it was written for is a rule that will be satisfied by a
constant.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from _plugin_tree import plugin_path

sys.path.insert(0, str(plugin_path() / "programs"))

import verdict as V  # noqa: E402


# ── the vocabulary is closed ─────────────────────────────────────────────

def test_there_are_exactly_five_verdicts():
    """The count is load-bearing: a sixth word is a schema change (module rule)."""
    assert [v.value for v in V.Verdict] == [
        "PASS", "PASS_WITH_WAIVERS", "FAIL", "NOT_MEASURED", "NOT_APPLICABLE"]


@pytest.mark.parametrize("word", [v.value for v in V.Verdict])
def test_parse_accepts_each_of_the_five(word):
    assert V.parse(word).value == word


@pytest.mark.parametrize("word", [
    # The deleted vocabulary, spelled exactly as the producers used to write it.
    "SKIP", "SKIPPED", "SKIPPED-CONDITION", "SKIPPED-SETUP-REQUIRED",
    "SKIPPED-BY-ENTRY", "SKIPPED-BY-EXIT", "OUT-OF-SCOPE-BY-ENTRY",
    "INCOMPLETE", "INCONCLUSIVE", "NOT_CHECKED", "NOT_EXECUTED", "NOT-MEASURED",
    "NO_TOOL", "ENV_UNAVAILABLE", "BLOCKED", "BLOCKED_BY_UPSTREAM",
    "DEFERRED-BY-UPSTREAM", "DEFERRED", "WAIVED", "WAIVED-DEFERRED",
    "PASS_VOIDED_BY_DEPENDENCY", "PASS-VOIDED-BY-DEPENDENCY",
    "PASS_WITH_ATTRIBUTION", "REFUSED", "ERROR", "MISSING", "STALLED",
    "VACUOUS_PASS", "VACUOUS-PASS", "PARTIALLY-VACUOUS", "STRUCTURE-ONLY",
    "ADVISORY", "RTL_REPAIR_RETRY", "FAIL_RTL_REPAIR_INERT",
    "STALE_BOARD_DETECTED", "LEC_BUDGET_EXHAUSTED",
])
def test_parse_refuses_every_deleted_word(word):
    """No alias, no fallback, no 'legacy' branch — a schema refusal.

    The REFUSAL is the design: a reader that translated `SKIP` would be the
    疊床架屋 the ruling forbids, and would reproduce run16's laundering inside
    the very module written to stop it.
    """
    with pytest.raises(V.UnknownVerdictWord) as e:
        V.parse(word)
    assert word in str(e.value)


def test_parse_does_not_normalise_spelling():
    """The old classifier upper-cased and swapped `_`/`-`. Tolerating a second
    spelling is how a third arrives; `parse` takes the word as written."""
    with pytest.raises(V.UnknownVerdictWord):
        V.parse("pass")
    with pytest.raises(V.UnknownVerdictWord):
        V.parse("NOT-MEASURED")     # the five spell it NOT_MEASURED
    assert V.parse("NOT_MEASURED") is V.Verdict.NOT_MEASURED


# ── the required fields are required ─────────────────────────────────────

def test_not_measured_without_a_reason_class_is_refused():
    with pytest.raises(ValueError, match="reason_class"):
        V.StepVerdict(V.Verdict.NOT_MEASURED, "11", "lec")


def test_not_measured_with_a_reason_class_is_accepted():
    s = V.StepVerdict.not_measured(
        "11", "lec", reason_class=V.ReasonClass.INCONCLUSIVE,
        reason="481 unproven of 846")
    assert s.verdict is V.Verdict.NOT_MEASURED
    assert s.reason_class is V.ReasonClass.INCONCLUSIVE


def test_not_applicable_without_declared_by_is_refused():
    """N/A is a claim about the INPUT and must name the line that makes it."""
    with pytest.raises(ValueError, match="declared_by"):
        V.StepVerdict(V.Verdict.NOT_APPLICABLE, "A3", "analog sim")


def test_not_applicable_with_declared_by_is_accepted():
    s = V.StepVerdict.not_applicable(
        "A3", "analog sim", declared_by="L5: no analog blocks declared")
    assert s.declared_by


def test_pass_with_waivers_with_no_row_is_refused():
    """A waived step with no row reaches no must-close list."""
    with pytest.raises(ValueError, match="waiver_rows"):
        V.StepVerdict(V.Verdict.PASS_WITH_WAIVERS, "31", "DRC")


def test_pass_with_waivers_accepts_a_row_or_an_attribution():
    assert V.StepVerdict.pass_with_waivers(
        "31", "DRC", waiver_rows=[V.WaiverRow("fpga-board", "no board here")])
    assert V.StepVerdict.pass_with_waivers(
        "31", "DRC", attribution="deck owned by the integrator")


def test_pass_needs_no_field_at_all():
    """The other direction: the required-field rules must not make a plain PASS
    harder to state than it is."""
    assert V.StepVerdict.pass_("34", "metal fill").verdict is V.Verdict.PASS


# ── THE CASCADE RULE, both directions on every clause ────────────────────

def test_a_dependent_of_a_fail_is_not_measured_never_a_pass():
    up = V.StepVerdict.fail("31", "DRC", reason="2 li1 spacing violations")
    got = V.cascade_to_dependent(up, "37", "GDSII output")
    assert got is not None
    assert got.verdict is V.Verdict.NOT_MEASURED
    assert got.reason_class is V.ReasonClass.UPSTREAM_FAILED
    assert "31" in got.reason


def test_a_dependent_of_a_fail_is_not_itself_called_a_failure():
    """The other direction. One defect must not be counted as two."""
    up = V.StepVerdict.fail("31", "DRC")
    assert V.cascade_to_dependent(up, "37").verdict is not V.Verdict.FAIL


def test_a_dependent_of_a_not_measured_step_is_untouched():
    """THE r26 FIX. Three steps that had produced and verified their own
    artefacts were voided because a review gate upstream declined to look."""
    up = V.StepVerdict.not_measured(
        "37", "GDSII output",
        reason_class=V.ReasonClass.PARTIAL_POPULATION,
        reason="stage_on_pass_review declined")
    for dep in ("37.4", "37.5ip", "38"):
        assert V.cascade_to_dependent(up, dep) is None


def test_a_dependent_of_a_not_applicable_step_is_untouched():
    up = V.StepVerdict.not_applicable("A3", declared_by="L5: no analog blocks")
    assert V.cascade_to_dependent(up, "A4") is None


def test_a_dependent_of_a_pass_is_untouched():
    assert V.cascade_to_dependent(V.StepVerdict.pass_("30"), "31") is None
    assert V.cascade_to_dependent(
        V.StepVerdict.pass_with_waivers(
            "30", attribution="integrator deck"), "31") is None


def test_only_fail_cascades():
    """Stated as a property over the whole enum, so a sixth word cannot arrive
    and quietly inherit cascading."""
    cascading = {v for v in V.Verdict
                 if _example(v).cascades}
    assert cascading == {V.Verdict.FAIL}


def test_a_required_artefact_that_does_not_exist_is_a_fail():
    s = V.required_artefact_absent("37", "GDSII", artefact="gds/top.gds")
    assert s.verdict is V.Verdict.FAIL
    assert s.reason_class is V.ReasonClass.MISSING_ARTEFACT
    assert "gds/top.gds" in s.reason


def test_a_missing_artefact_cascades_like_any_other_fail():
    """The other direction of the same clause: MISSING used to sit in the same
    bag as SKIP, where it voided nothing."""
    up = V.required_artefact_absent("37", "GDSII", artefact="gds/top.gds")
    assert V.cascade_to_dependent(up, "38").reason_class is (
        V.ReasonClass.UPSTREAM_FAILED)


# ── the review-gate rule ─────────────────────────────────────────────────

def test_a_review_gate_runs_whenever_its_inputs_exist():
    assert V.review_gate_verdict("37", "stage3 review",
                                 inputs_present=True) is None


def test_a_review_gate_whose_inputs_are_absent_is_not_measured_with_a_reason():
    got = V.review_gate_verdict("37", "stage3 review", inputs_present=False)
    assert got.verdict is V.Verdict.NOT_MEASURED
    assert got.reason_class is V.ReasonClass.INPUT_ABSENT


# ── the run-level roll-up ────────────────────────────────────────────────

def _example(v: V.Verdict) -> V.StepVerdict:
    if v is V.Verdict.NOT_MEASURED:
        return V.StepVerdict.not_measured(
            "x", reason_class=V.ReasonClass.NOT_EXECUTED)
    if v is V.Verdict.NOT_APPLICABLE:
        return V.StepVerdict.not_applicable("x", declared_by="input line")
    if v is V.Verdict.PASS_WITH_WAIVERS:
        return V.StepVerdict.pass_with_waivers("x", attribution="integrator")
    if v is V.Verdict.FAIL:
        return V.StepVerdict.fail("x")
    return V.StepVerdict.pass_("x")


def test_a_run_of_passes_is_a_pass():
    assert V.run_verdict([_example(V.Verdict.PASS)] * 3) is V.Verdict.PASS


def test_one_fail_makes_the_run_fail():
    assert V.run_verdict([_example(V.Verdict.PASS),
                          _example(V.Verdict.FAIL)]) is V.Verdict.FAIL


def test_one_not_measured_keeps_the_run_off_pass():
    """R-0915-85: a run with any NOT_MEASURED sign-off step is at best
    NOT_MEASURED at the top, never PASS. run16's phase 2 is this case."""
    assert V.run_verdict([_example(V.Verdict.PASS),
                          _example(V.Verdict.NOT_MEASURED)]) is (
        V.Verdict.NOT_MEASURED)


def test_not_measured_does_not_outrank_a_fail():
    """The other direction: a measured defect outranks a hole."""
    assert V.run_verdict([_example(V.Verdict.NOT_MEASURED),
                          _example(V.Verdict.FAIL)]) is V.Verdict.FAIL


def test_not_measured_outranks_a_waived_pass():
    assert V.run_verdict([_example(V.Verdict.PASS_WITH_WAIVERS),
                          _example(V.Verdict.NOT_MEASURED)]) is (
        V.Verdict.NOT_MEASURED)


def test_not_applicable_blocks_nothing():
    assert V.run_verdict([_example(V.Verdict.PASS),
                          _example(V.Verdict.NOT_APPLICABLE)]) is V.Verdict.PASS


def test_a_run_with_no_contributing_step_is_not_a_pass():
    """The catch-all `return "PASS"` at the bottom of both runners' aggregators
    is the most-cited hazard in their own comments. An empty numerator is not a
    pass."""
    assert V.run_verdict([]) is V.Verdict.NOT_MEASURED
    assert V.run_verdict([_example(V.Verdict.NOT_APPLICABLE)]) is (
        V.Verdict.NOT_MEASURED)


def test_a_red_run_names_its_cause():
    rec = V.run_verdict_record([
        V.StepVerdict.pass_("34", "metal fill"),
        V.StepVerdict.not_measured("11", "lec",
                                   reason_class=V.ReasonClass.INCONCLUSIVE,
                                   reason="481 unproven of 846"),
    ])
    assert rec["verdict"] == "NOT_MEASURED"
    assert [c["step_id"] for c in rec["causes"]] == ["11"]
    assert rec["counts"]["PASS"] == 1
    assert rec["step_status_schema_version"] == V.SCHEMA_VERSION


def test_a_green_run_names_no_cause():
    rec = V.run_verdict_record([V.StepVerdict.pass_("34")])
    assert rec["verdict"] == "PASS"
    assert rec["causes"] == []


# ── disclosures are informational and change no arithmetic ───────────────

@pytest.mark.parametrize("d", list(V.Disclosure))
def test_a_disclosure_never_moves_a_verdict(d):
    """The inversion `verdict` recorded — a tree that DISCLOSED its
    content came from a library default scored below one that said nothing —
    is not expressible any more."""
    plain = V.StepVerdict.pass_("14")
    disclosed = V.StepVerdict.pass_("14", disclosures=[d])
    assert V.run_verdict([plain]) is V.run_verdict([disclosed])
    assert disclosed.is_green and not disclosed.blocks_run_pass
    assert V.cascade_to_dependent(disclosed, "15") is None


def test_the_reused_record_disclosure_keeps_the_verdict_it_had():
    """sha256 run16 pass 2, in one assertion: the second pass returned the same
    INCONCLUSIVE record in 2 s instead of 8306 s and booked it SKIP."""
    pass1 = V.StepVerdict.not_measured(
        "11", "lec_equivalence", reason_class=V.ReasonClass.INCONCLUSIVE,
        reason="481 unproven of 846 in 8306 s")
    pass2 = V.StepVerdict.not_measured(
        "11", "lec_equivalence", reason_class=V.ReasonClass.INCONCLUSIVE,
        reason="the kept record, returned in 2 s",
        disclosures=[V.Disclosure.REUSED_RECORD])
    assert pass2.verdict is pass1.verdict
    assert V.run_verdict([pass2]) is V.run_verdict([pass1])
    assert V.Disclosure.REUSED_RECORD in pass2.disclosures


# ── round trip ───────────────────────────────────────────────────────────

def test_a_record_round_trips_through_json():
    s = V.StepVerdict.pass_with_waivers(
        "31", "DRC",
        waiver_rows=[V.WaiverRow("fpga-board", "no board on this host", "lane")],
        attribution="integrator deck",
        disclosures=[V.Disclosure.PARTIAL_VACUITY])
    back = V.StepVerdict.from_dict(json.loads(json.dumps(s.to_dict())))
    assert back.to_dict() == s.to_dict()


def test_from_dict_refuses_a_record_written_by_an_unmigrated_producer():
    with pytest.raises(V.UnknownVerdictWord):
        V.StepVerdict.from_dict({"status": "INCOMPLETE", "id": "37"})


# ── scope predicates ─────────────────────────────────────────────────────

def test_only_not_applicable_is_out_of_scope():
    assert not V.scoped_into_verdict({"status": "NOT_APPLICABLE"})
    for w in ("PASS", "PASS_WITH_WAIVERS", "FAIL", "NOT_MEASURED"):
        assert V.scoped_into_verdict({"status": w}), w


def test_scope_refuses_an_old_word_rather_than_guessing():
    with pytest.raises(V.UnknownVerdictWord):
        V.scoped_into_verdict({"status": "SKIPPED-CONDITION"})


def test_the_analog_track_is_read_from_the_step_not_a_step_id_list():
    assert V.in_analog_track({"stage": "stage_analog"})
    assert not V.in_analog_track({"stage": "stage_mixed_signal"})
    assert not V.in_analog_track({"stage": "stage3"})


def test_the_track_predicate_reads_objects_and_dicts_alike():
    class _S:
        stage = "stage_analog"
    assert V.in_analog_track(_S()) and V.in_analog_track({"stage": "stage_analog"})


# ── the module's own CLI, so the vocabulary is readable without importing ──

def test_the_module_prints_its_vocabulary():
    out = subprocess.run(
        [sys.executable, str(plugin_path() / "programs" / "verdict.py")],
        capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    assert d["verdicts"] == [v.value for v in V.Verdict]
    assert d["cascade"]["NOT_MEASURED"].startswith("dependent unaffected")


# ── the DESIGN text is part of the deliverable ───────────────────────────

def test_the_module_carries_the_why_where_the_design_lives():
    """R-0915-85 clause 5. The next person cannot add a 24th word without
    meeting this text, so the text has to be there."""
    src = (plugin_path() / "programs" / "verdict.py").read_text(encoding="utf-8")
    assert "不要設計的疊床架屋" in src, "the owner's sentence"
    assert "subservient r26" in src, "the cascade evidence"
    assert "run16" in src, "the laundering evidence"
    assert "status word is a schema change, not a string" in src


# ── the three table-driven words the AST ratchet cannot see ──────────────

def test_the_analog_provenance_stamps_are_gone_from_the_producer_table():
    """`analog_one_shot_runner._A1_A3_PRODUCERS` holds a step's status in a
    DICT, which reaches a row through `prod["status"]`.

    Three words lived there — `PASS_WITH_REAL_EXTRACT`,
    `PASS_WITH_DERIVED_TOPOLOGY`, `PASS_WITH_REAL_NETLIST` — each a `PASS` that
    also named its producer. They survived the first migration pass and the
    tree-wide ratchet could not see them: judging every `{"status": ...}` in a
    producer file reports twenty per-gate JSON payload fields that are not step
    verdicts at all (the ratchet's docstring records that experiment).

    So they are pinned BY NAME here, which is the honest device when a scan
    cannot reach a shape. The producer is still on the row — in
    `extras["producer"]`, where a consumer reads provenance.
    """
    src = (plugin_path() / "programs"
           / "analog_one_shot_runner.py").read_text(encoding="utf-8")
    code = "\n".join(l.split("#")[0] for l in src.splitlines())
    for w in ("PASS_WITH_REAL_EXTRACT", "PASS_WITH_DERIVED_TOPOLOGY",
              "PASS_WITH_REAL_NETLIST"):
        assert f'"{w}"' not in code, (
            f"{w} is back in the producer table; the status carries the "
            f"outcome and extras['producer'] carries the provenance")
    import analog_one_shot_runner as A  # noqa: PLC0415
    for name, prod in A._A1_A3_PRODUCERS.items():
        assert V.parse(prod["status"]) is V.Verdict.PASS, (name, prod["status"])
