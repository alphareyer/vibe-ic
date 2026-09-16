#!/usr/bin/env python3
"""E1 of vibe-ic#2092 — a run published as red NAMES what made it red.

MEASURED DEFECT
===============
The `opentitan_aes` run of lane icaes (8HD-4, v1.17.38), in its own
`reports/audit/phase23_completion_audit.json`, R2 and R3 on one tree:

    verdict            FAIL
    run_status         FAIL
    failed_gates       []
    failed_gate_count  0

and NO other field in the artefact names a cause. Every list a consumer keys
on says nothing failed, beside a verdict that says the run is red.

The FAIL was not uncaused: the same file carries 35 non-green steps in
`steps[]` — 12 FAIL, 16 MISSING, 7 PASS_VOIDED_BY_DEPENDENCY — and that is
what `overall` was computed from. What was missing was the SENTENCE joining
the two, so the natural reading of "FAIL with failed_gates []" was the wrong
one.

WHAT IS ENFORCED HERE
=====================
`verdict_causes` states the join, from the SAME objects the artefact
publishes. It never invents a gate name and never moves `run_status`: a
step-caused FAIL stays a step-caused FAIL and says so.

WHAT IS DELIBERATELY NOT ASSERTED — the equation this file does NOT write is
"verdict FAIL ⇔ failed_gates non-empty". `overall` is a STEP-level verdict;
demanding a failed GATE for it would force this audit either to fabricate a
gate name or to refuse on every legitimately step-caused red. The enforceable
sentence is the issue's own: a FAIL must name what failed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import verdict as _T  # noqa: E402
import flow_compliance_check as F  # noqa: E402


def _step(i, status, name="a step"):
    return {"id": i, "name": name, "status": status}


#: The icaes R3 step tally, by status, exactly as the artefact carries it.
_ICAES_R3 = (
    [_step(i, "FAIL") for i in range(12)]
    + [_step(100 + i, "FAIL") for i in range(16)]
    + [_step(200 + i, "NOT_MEASURED") for i in range(7)]
    + [_step(300 + i, "NOT_APPLICABLE") for i in range(22)]
    + [_step(400 + i, "NOT_MEASURED") for i in range(5)]
    + [_step(500 + i, "PASS") for i in range(3)]
    + [_step(600 + i, "PASS_WITH_WAIVERS") for i in range(3)]
    + [_step(700, "PASS")]
)


def test_the_measured_icaes_red_now_names_its_cause():
    c = F.verdict_causes("FAIL", [], _ICAES_R3, [], [])
    assert c["run_is_red"] is True
    assert c["names_its_cause"] is True
    assert c["failed_gates"] == [], "no gate failed, and that stays true"
    assert len(c["non_green_steps"]) == 35
    assert c["named_cause_count"] == 35
    statuses = {s["status"] for s in c["non_green_steps"]}
    assert statuses == {"FAIL", "FAIL", "NOT_MEASURED"}


def test_a_red_that_names_nothing_at_all_is_reported_as_a_defect():
    """THE FABRICATION DIRECTION. When a run really is red over nothing, this
    must say so — not manufacture a cause to make the equation hold."""
    c = F.verdict_causes("FAIL", [], [_step(1, "PASS")], [], [])
    assert c["run_is_red"] is True
    assert c["names_its_cause"] is False
    assert c["named_cause_count"] == 0


def test_a_failing_gate_is_a_cause_on_its_own():
    c = F.verdict_causes("FAIL", ["l8_clock_domains_typed_check"],
                         [_step(1, "PASS")], [], [])
    assert c["names_its_cause"] is True
    assert c["failed_gates"] == ["l8_clock_domains_typed_check"]
    assert c["non_green_steps"] == []


def test_a_structural_or_step_artifact_line_is_a_cause_on_its_own():
    for kw in ("structural", "step_artifact"):
        kwargs = {"structural_fail_lines": [], "step_artifact_fail_lines": []}
        kwargs[f"{kw}_fail_lines"] = ["gate x failed"]
        c = F.verdict_causes("FAIL", [], [_step(1, "PASS")],
                             kwargs["structural_fail_lines"],
                             kwargs["step_artifact_fail_lines"])
        assert c["names_its_cause"] is True, kw
        assert c["named_cause_count"] == 1


def test_a_gating_ordering_violation_is_a_cause_on_its_own():
    """THE FOURTH `forced_fail` SITE, and the one that is neither a gate name
    nor a step status. A hand-off step marked done while a step it `blocks_on`
    had not delivered FAILs the run and can name no failed gate and no
    non-green step. A cause set that stopped at the other three would call
    that legitimately-caused FAIL uncaused — and the frame canary would then
    redden a correct run over the audit's own blind spot."""
    c = F.verdict_causes(
        "FAIL", [], [_step(1, "PASS"), _step(2, "NOT_MEASURED")], [], [],
        ordering_gating_lines=[
            "[37] GDSII = PASS marked done while dependency [31] DRC = MISSING"])
    assert c["run_is_red"] is True
    assert c["names_its_cause"] is True
    assert c["failed_gates"] == []
    assert c["non_green_steps"] == []
    assert c["ordering_gating_line_count"] == 1
    assert c["named_cause_count"] == 1


def test_a_self_skipped_signoff_step_is_a_cause_on_its_own():
    """THE FIFTH SOURCE, and it wears an EXCUSED word. `ok` is false when
    `oss_blocked_skipped` is non-empty, and those rows are SKIPPED-CONDITION —
    which `verdict` puts in EXCUSED, not NON_GREEN. So this run is
    red with no failed gate, no non-green step and no failure line."""
    class _R:
        id, name = "DT1", "Transition-delay-fault ATPG"
    c = F.verdict_causes("FAIL", [], [_step(1, "NOT_APPLICABLE")], [], [],
                         self_skipped_signoff_steps=[_R()])
    assert c["run_is_red"] is True
    assert c["names_its_cause"] is True
    assert c["non_green_steps"] == []
    assert c["self_skipped_signoff_step_count"] == 1
    assert c["named_cause_count"] == 1


#: Every input to the two red decisions in `main`, and HOW each reaches the
#: cause set. Read out of the source below rather than remembered — a sixth
#: input added later has no entry here and reddens this row.
_RED_INPUTS = {
    # `ok = ...` — the statuses
    "failing": "status FAIL is in verdict.NON_GREEN",
    "missing": "status MISSING is in verdict.NON_GREEN",
    "setup_required_skipped":
        "status SKIPPED-SETUP-REQUIRED is in verdict.NON_GREEN",
    # `ok = ...` — the one that is NOT a status
    "oss_blocked_skipped": "self_skipped_signoff_steps",
    # `forced_fail = True` — the lists
    "structural_fail_lines": "structural_fail_lines",
    "step_artifact_fail_lines": "step_artifact_fail_lines",
    "ordering_gating_lines": "ordering_gating_lines",
}


def test_every_red_input_in_main_has_a_route_into_the_cause_set():
    """DERIVED FROM THE SOURCE, not remembered. `main` turns a run red from
    two places — the `ok` computation and the `forced_fail` block — and every
    list either of them reads must reach `verdict_causes`, or a run can be red
    for a reason this field cannot name and E5's canary will redden it."""
    src = Path(F.__file__).read_text(encoding="utf-8")
    # The two decisions, each read from the source between its own anchors.
    ok_expr = src.split("        ok = (", 1)[1].split("\n\n", 1)[0]
    forced = src.split("    forced_fail = False", 1)[1].split(
        "    if not ok or forced_fail:", 1)[0]
    red_region = ok_expr + "\n" + forced

    named = {name for name in _RED_INPUTS if name in red_region}
    assert named == set(_RED_INPUTS), (
        "the red inputs `main` reads are not the ones this file maps: "
        f"unmapped={sorted(set(_RED_INPUTS) - named)}")
    # And nothing ELSE in those two decisions is a list this map does not know:
    # every `len(<name>)` in the `ok` expression must be a mapped input.
    import re
    for name in re.findall(r"len\((\w+)\)", ok_expr):
        assert name in _RED_INPUTS, (
            f"`ok` reads `{name}`, which has no route into the cause set")

    sig = src.split("def verdict_causes(", 1)[1].split("\n) ->", 1)[0]
    tiers = set(_T.NON_GREEN)
    for source, route in _RED_INPUTS.items():
        if route.startswith("status "):
            word = route.split()[1]
            assert word in tiers, (source, route)
        else:
            assert route in sig, (
                f"{source} reaches the verdict red and `{route}` is not a "
                f"parameter of verdict_causes")


def test_every_green_tier_asserts_nothing_about_causes():
    for word in F.GREEN_RUN_STATUSES:
        c = F.verdict_causes(word, [], [_step(1, "PASS")], [], [])
        assert c["run_is_red"] is False, word
        assert c["names_its_cause"] is None, word


def test_a_refusal_names_nothing_on_purpose_and_says_not_measured():
    """`completion_audit_verdict` writes INSUFFICIENT_DATA when a run measured
    nothing. That audit reports no finding, so requiring it to name one would
    invert the refusal it just made."""
    c = F.verdict_causes("FAIL", [], [], [], [],
                         verdict_refusal_reason="REFUSED, not FAILED: …")
    assert c["run_is_red"] is True
    assert c["names_its_cause"] is None
    assert "NOT_MEASURED is not a pass" in c["note"]


def test_the_non_green_tiers_are_read_from_the_module_that_owns_them():
    """DERIVED, not a literal. A tier added to `verdict.NON_GREEN`
    becomes a cause the day it is added, not the day someone remembers."""
    import verdict as tiers
    for word in tiers.NON_GREEN:
        c = F.verdict_causes("FAIL", [], [_step(1, word)], [], [])
        assert c["names_its_cause"] is True, word
        assert c["non_green_steps"][0]["status"] == word


def test_an_excused_step_is_never_counted_as_a_cause():
    import verdict as tiers
    for word in tiers.EXCUSED:
        c = F.verdict_causes("FAIL", [], [_step(1, word)], [], [])
        assert c["non_green_steps"] == [], word


def test_the_green_tiers_are_the_ones_main_exits_zero_on():
    """Spelt once. If the exit-code decision at the bottom of `main` and this
    tuple ever disagree, a run could be green to the shell and red here."""
    src = (Path(F.__file__).read_text(encoding="utf-8")
           .split('if overall in ("PASS", "PASS_WITH_WAIVERS",')[1]
           .split("return 0")[0])
    assert "PASS_WITH_OPEN_SOURCE_CONSTRAINTS" in src
    assert set(F.GREEN_RUN_STATUSES) == {
        "PASS", "PASS_WITH_WAIVERS", "PASS_WITH_OPEN_SOURCE_CONSTRAINTS"}
