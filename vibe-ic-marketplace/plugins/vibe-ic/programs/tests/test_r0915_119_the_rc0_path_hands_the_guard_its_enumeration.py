"""R-0915-119's guard is only as good as the evidence it is handed.

MEASURED on spm run22 (lane icspm5, plugin 182083d3d, 2026-09-23). Step 2's
`internal_vs_external_timing_check` exits 0, prints NOTHING, and publishes a
report that says, in the taxonomy's own vocabulary:

    verdict             VACUOUS_PASS
    reason_class        NOT_APPLICABLE_BY_STRUCTURE
    skip_kind           class-not-applicable
    structural_absence  population "L8_TIMING_WAVEFORM container(s) and group
                        key(s) that could carry half-duplex protocol symbol
                        timing", scanned 13, found 0

It enumerated thirteen containers and found the class inapplicable. The ledger
row read

    verdict INCOMPLETE, reason_class EXECUTION_ERROR
             (structured_verdict VACUOUS_PASS, exit_code 0)

and step 2 read NOT_MEASURED / partial_population, disclosed as "the gate
reports its input was applicable and was NOT examined" -- over a checker that
had examined its input and said so.

THE CAUSE IS ONE MISSING ARGUMENT, and R-0915-119 is not what is wrong.
`_guard_structural` accepts NOT_APPLICABLE_BY_STRUCTURE only when the record
carries a valid structural-absence enumeration, and fail-closes to
EXECUTION_ERROR without one -- deliberately, so a checker cannot reach the
decided state by writing a word into its report. The rc-2 branch of
`_check_program_exit_zero` hands the guard that enumeration; the rc-0 branch
passed only `explicit=` and the guard, asked to honour a token with nothing
behind it, correctly refused.

So this file pins the SYMMETRY, not a new rule: both exit codes must give the
guard the same evidence, and the guard must keep refusing a bare token.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import _flow_reason_taxonomy as _t  # noqa: E402
import _structural_absence as _sa  # noqa: E402

SRC = (PROGRAMS / "flow_compliance_check.py").read_text()


def _report(scanned: int = 13, found: int = 0,
            reason_class: str = "NOT_APPLICABLE_BY_STRUCTURE"):
    """A report shaped like the one the measured gate publishes."""
    return {
        "verdict": "VACUOUS_PASS",
        "reason_class": reason_class,
        "skip_kind": "class-not-applicable",
        "total_findings": 0,
        "findings": [],
        _sa.EVIDENCE_KEY: {
            "population": "L8_TIMING_WAVEFORM container(s) that could carry "
                          "half-duplex protocol symbol timing",
            "scanned": scanned,
            "found": found,
            "scanned_names": [f"c{i}" for i in range(scanned)],
        },
    }


# ---------------------------------------------------------------- POSITIVE

def test_the_enumerated_absence_is_classified_as_absence_not_as_a_crash():
    """The whole point: hand the guard the enumeration and it decides."""
    rep = _report()
    got = _t.infer_nonverdict_reason(
        verdict="VACUOUS_PASS", message="",
        evidence={_sa.EVIDENCE_KEY: _sa.evidence_of(rep)},
        explicit=_t.report_reason_class(rep))
    assert got == _t.NOT_APPLICABLE_BY_STRUCTURE
    assert got in _t.SKIP_ELIGIBLE
    assert got not in _t.INCOMPLETE


def test_the_rc0_branch_passes_the_enumeration_the_rc2_branch_already_did():
    """Both exit codes, one argument list. This is the change.

    Matched on the SOURCE because the divergence was a missing keyword at one
    of two sibling call sites; a behavioural test alone would not say that the
    two branches now ask the same question.
    """
    # SCOPED TO THE SITES THAT CLASSIFY FROM A GATE'S REPORT. Other callers
    # pass a class they already hold (`explicit=reason_class`) and have no
    # report to enumerate from; they are not this rule's subject, and widening
    # the match to them would assert something the code never claimed.
    calls = [m.start() for m in re.finditer(
        r"_reason_taxonomy\.infer_nonverdict_reason\(", SRC)]
    report_sites = [SRC[s:s + 420] for s in calls
                    if "explicit=report_cls" in SRC[s:s + 420]]
    assert len(report_sites) >= 2, (
        "the rc-0 and rc-2 report-classifying sites must both exist; found "
        f"{len(report_sites)}")
    for segment in report_sites:
        assert "_sa.EVIDENCE_KEY" in segment, (
            "a site that classifies from a report without handing over that "
            "report's enumeration asks the guard to honour a bare token: "
            + segment[:220])


# ---------------------------------------------------------------- NEGATIVE

def test_a_bare_token_with_no_enumeration_is_still_refused():
    """R-0915-119's guard is NOT weakened: the word alone still fails closed."""
    got = _t.infer_nonverdict_reason(
        verdict="VACUOUS_PASS", message="",
        evidence=None, explicit="NOT_APPLICABLE_BY_STRUCTURE")
    assert got == _t.EXECUTION_ERROR


def test_an_enumeration_that_found_its_subject_is_refused():
    """`found` non-zero means the gate DID have a subject and examined none of
    it -- that is not an absence, and the guard must keep saying so."""
    rep = _report(scanned=13, found=4)
    got = _t.infer_nonverdict_reason(
        verdict="VACUOUS_PASS", message="",
        evidence={_sa.EVIDENCE_KEY: _sa.evidence_of(rep)},
        explicit=_t.report_reason_class(rep))
    assert got == _t.EXECUTION_ERROR


def test_a_report_declaring_execution_error_stays_incomplete():
    """The other direction of the same call: a gate that declares a real
    execution error is classified as one, enumeration or not."""
    rep = _report(reason_class="EXECUTION_ERROR")
    got = _t.infer_nonverdict_reason(
        verdict="VACUOUS_PASS", message="",
        evidence={_sa.EVIDENCE_KEY: _sa.evidence_of(rep)},
        explicit=_t.report_reason_class(rep))
    assert got == _t.EXECUTION_ERROR
    assert got in _t.INCOMPLETE


def test_a_report_declaring_no_class_at_all_is_unchanged():
    """No explicit class, no prose: the fail-closed default is untouched."""
    got = _t.infer_nonverdict_reason(
        verdict="VACUOUS_PASS", message="", evidence=None, explicit=None)
    assert got == _t.EXECUTION_ERROR


def test_the_guard_helper_itself_is_not_edited_by_this_change():
    """The fix is at the CALL SITE. `_guard_structural`'s own refusal is the
    negative arm this change is worth nothing without, so it must still be the
    thing that decides."""
    tax = (PROGRAMS / "_flow_reason_taxonomy.py").read_text()
    assert "def _guard_structural(" in tax
    body = tax[tax.index("def _guard_structural("):]
    body = body[:body.index("\n\ndef ")]
    assert "_sa.is_valid" in body
    assert "EXECUTION_ERROR" in body
