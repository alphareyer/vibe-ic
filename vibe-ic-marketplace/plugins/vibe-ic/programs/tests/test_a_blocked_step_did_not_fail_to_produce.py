"""R-0915-140 — a step whose blocker FAILED reads the cascade, not missing_artefact.

THE DEFECT, and it printed both tiers in one line. On a frozen FAIL-path tree
(subservient r33, step 31 FAIL) step 37 "GDSII output (only if Step 31 PV fully
clean)" read:

    ✗ [FAIL] Step 37: … (stage4) (missing_artefact) [blocked-by-upstream(2)]

The audit KNEW the step was blocked -- it wrote the attribution itself -- and
published FAIL anyway. Before #2514 the same row read
"[NOT_MEASURED] … (upstream_failed)".

THE MECHANISM, NAMED. Two passes decide this row and they disagreed:

  * the VOID rule (`cascade_tier_for_dependent`'s caller in `main`) converts a
    dependent's PASS to NOT_MEASURED(upstream_failed) and explicitly lets every
    FAIL through -- "a FAIL never converts, real counter-evidence survives";
  * the #503 pass (`_attribute_cascade_verdicts`) annotates every MISSING step
    after the first mid-chain FAIL with `blocked-by-upstream(<root>)` and, in its
    own docstring, "Status stays MISSING … only the ATTRIBUTION changes".

Before #2514 step 37 declared two outputs, both present on such a tree, so it
arrived PASS and the VOID rule caught it. #2514 gave it four -- two of which a
blocked step cannot have -- so it arrived FAIL(missing_artefact) and only the
#503 pass touched it: the attribution moved and the tier did not.

WHY THE TIER IS THE ONE THAT IS WRONG. `FAIL(missing_artefact)` says "this step
was owed an artefact and did not produce it". A step after the first mid-chain
FAIL was never owed anything -- it never ran. `FAIL(missing_artefact)` is reserved
for a step whose blockers PASSED and which still produced nothing, and that is the
negative arm below.

STRICT MODE DOES NOT GO QUIET: NOT_MEASURED is not a pass, the run's own verdict
still refuses, the cascade is still counted, and the root cause is still named on
the row.

MEASURED, main f2516dfeb vs this tip, same frozen FAIL-path tree:
    step 37   FAIL/missing_artefact  ->  NOT_MEASURED/upstream_failed
    rows that moved: exactly 1        overall: FAIL -> FAIL
    still FAIL/missing_artefact: step 37.3 -- its blocker (21) did NOT fail, so it
        WAS owed its output and did not produce it. The negative arm, live on the
        same run rather than constructed.
And on the unmodified run21 copy: PASS 30 -> 30, PASS_WITH_WAIVERS 2 -> 2,
NOT_APPLICABLE 26 -> 26, with one further row moving -- step 38, itself
`blocked-by-upstream(36)`, i.e. the same ruling applying to a second cascade row.
"""
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
PROGRAMS = PLUGIN / "programs"
sys.path.insert(0, str(PROGRAMS))

import flow_compliance_check as FCC                          # noqa: E402

_T = FCC._T
_PASS = _T.Verdict.PASS.value
_FAIL = _T.Verdict.FAIL.value
_NM = _T.Verdict.NOT_MEASURED.value
_NA = _T.Verdict.NOT_APPLICABLE.value
_MISSING = _T.ReasonClass.MISSING_ARTEFACT.value
_UPSTREAM = _T.ReasonClass.UPSTREAM_FAILED.value


# ── the rule, driven in every direction ────────────────────────────────────

def test_a_missing_output_under_a_failed_blocker_becomes_the_cascade():
    """THE RULING."""
    got = FCC.cascade_tier_for_dependent(_FAIL, _MISSING, [31])
    assert got == (_NM, _UPSTREAM, "not_owed"), got


def test_a_pass_under_a_failed_blocker_is_still_voided():
    """The rule this narrows is not replaced: a PASS resting on a broken chain
    certifies nothing and still converts."""
    got = FCC.cascade_tier_for_dependent(_PASS, "", [31])
    assert got == (_NM, _UPSTREAM, "pass_voided"), got


def test_a_real_refusal_still_survives_a_failed_blocker():
    """THE ARM THAT KEEPS THIS FROM BEING AN AMNESTY. A gate that RAN and refused
    is counter-evidence, and counter-evidence survives — that is the existing
    doctrine, narrowed here and not repealed."""
    for rc in ("", "execution_error", "no_population", "partial_population"):
        assert FCC.cascade_tier_for_dependent(_FAIL, rc, [31]) is None, rc


def test_a_step_that_already_measured_nothing_keeps_its_own_word():
    """R-0915-85's half: there is no PASS here to void, and the disclosure is not
    the status."""
    assert FCC.cascade_tier_for_dependent(_NM, "partial_population", [31]) is None
    assert FCC.cascade_tier_for_dependent(_NA, "", [31]) is None


def test_the_rule_is_one_function_both_passes_call():
    """Two passes decided this row and disagreed; one of them is why the defect
    shipped. A second copy of the rule is a second place for them to disagree
    again, so the source is asserted to hold exactly one definition, called by
    the VOID rule and by the ONE applier every blocked-by-upstream writer uses
    (lane ictier1 rework: the #503 pass, `_resolve_dependency_condition_results`
    and `_attribute_condition_owner_blocks`)."""
    src = (PROGRAMS / "flow_compliance_check.py").read_text()
    assert src.count("def cascade_tier_for_dependent(") == 1
    assert src.count("cascade_tier_for_dependent(") == 3, (
        "expected one definition and two call sites — the void rule and "
        "`_demote_not_owed_rows`")
    assert src.count("def _demote_not_owed_rows(") == 1
    assert src.count("_demote_not_owed_rows(") == 4, (
        "expected one definition and three writers")


def test_the_cascade_row_names_the_root_and_says_it_was_not_owed():
    """The attribution is not lost when the tier moves: the row must still name
    the root cause, and must now say WHY its outputs are absent."""
    src = (PROGRAMS / "flow_compliance_check.py").read_text()
    i = src.index("blocked-by-upstream(step {root}): its blocks_on closure")
    window = src[i:i + 700]
    assert "never owed its declared outputs" in window, window[:300]
    assert "R-0915-140" in window


def test_the_voided_sentence_is_not_printed_over_a_row_that_never_passed():
    """"PASS voided" names a verdict the row never had. The VOID rule only ever
    meets a done-claim (analyze() raises violations for PASS/PASS_WITH_WAIVERS
    terminals), so its sentence is the PASS-voided one and the not-owed tier is
    decided -- and worded -- by the blocked-by-upstream writers. The dead
    'NOT OWED' arm the VOID loop carried (review of next/icslot66, finding 3)
    is gone."""
    src = (PROGRAMS / "flow_compliance_check.py").read_text()
    assert "_was_missing_only" not in src
    assert 'f"NOT OWED: dependency' not in src
    assert 'f"PASS voided: dependency [{_v.get(\'signoff_id\')}] "' in src
