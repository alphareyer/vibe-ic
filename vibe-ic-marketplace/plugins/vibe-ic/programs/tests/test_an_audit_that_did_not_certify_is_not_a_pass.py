"""test_an_audit_that_did_not_certify_is_not_a_pass.py

TWO VERDICT DEFECTS IN THE PHASE-2/3 CHAIN, both about reading a word and ignoring
everything else the producer said.

(a) `design_one_shot_runner.step_final_audit` read its verdict as
    `"Overall: PASS" in out` -- a SUBSTRING of the whole captured stream -- and consulted
    the audit's exit code only for 124 (timeout). `flow_compliance_check` has a DESIGNED
    state where those two disagree: its vibe-ic#2092 reconciliation canary prints the run
    word, then prints "THIS REPORT DOES NOT RECONCILE ... do not quote its counts", says
    "(the run's own status is unchanged and still PASS.)" and returns 1. Its own contract
    spells it out -- "the report is still WRITTEN ... and the run exits non-zero.
    `run_status` is untouched -- what changes is that the artefact stops CERTIFYING its own
    arithmetic." So the audit withdraws its certification and the orchestrator wrote PASS.

(b) `phase23_one_shot_runner._aggregate_verdict._TIERS` knew three of the four words
    `verdict.run_verdict` can hand up. The missing one is NOT_MEASURED, which phase 2 can
    publish (`step_final_audit`'s own R-0915-159 branch produces a NOT_MEASURED step), and
    a phase that measured nothing came back as `UNKNOWN_PHASE_VERDICT` -- loud and
    non-zero, so nothing was laundered, but it is the wrong answer: that word is not
    unrecognised, it is a state this repo has a tier for.

THE RULE, with no tool or step name in it:

    A producer has as many channels as it chooses to use, and a consumer that reads one
    of them has not read the producer. An exit code is an account of the run; a word
    printed beside a refusal to certify is not a certificate. And a vocabulary with one
    owner must be ASKED, never retyped -- a fourth copy of a list drifts the way the
    first three did.

Fixtures quote the producers' OWN print statements and the OWN vocabulary module; nothing
here restates a path or a word that the code does not itself produce.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import verdict as _V                              # noqa: E402
import design_one_shot_runner as D                # noqa: E402
import _audit_verdict as _AV                      # noqa: E402 — the rule's owner
import phase23_one_shot_runner as P23             # noqa: E402


# ═══ (a) the audit's two channels ═════════════════════════════════════════

def _canary_stdout(word: str = "PASS") -> str:
    """`flow_compliance_check`'s stdout when its own report does not reconcile.

    Shape taken from the audit's own prints: the `Overall:` line it emits once at its
    tail, then the canary block, then the line that says the run word is unchanged.
    """
    return (
        "=== Vibe-IC phase1_phase2_phase3 compliance ===\n"
        "  PASS=44  PASS_WITH_WAIVERS=0  FAIL=0  NOT_MEASURED=0\n"
        f"\nOverall: {word}  (strict=True)\n"
        "\nflow_compliance_check: THIS REPORT DOES NOT RECONCILE — 1 of 7 equation(s) "
        "over its own numbers are false. The report was still written, with "
        "`reconciled: false`; do not quote its counts.\n"
        "  ✗ [tally_sums] executed == PASS+FAIL+… — 44 != 43\n"
        f"  (the run's own status is unchanged and still {word}.)\n"
    )


def _final_audit(monkeypatch, tmp_path, rc: int, out: str):
    (tmp_path / "reports").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(D, "_run", lambda *a, **k: (rc, out, ""))
    return D.step_final_audit(tmp_path, phase=3)


def test_a_green_word_with_a_nonzero_exit_is_not_a_pass(monkeypatch, tmp_path):
    """THE REPRODUCTION. On main this records PASS for a run whose audit exited 1 while
    refusing to certify its own arithmetic."""
    r = _final_audit(monkeypatch, tmp_path, 1, _canary_stdout("PASS"))
    assert r.status == _V.Verdict.NOT_MEASURED.value, (
        f"an audit that printed `Overall: PASS` and exited 1 was recorded as "
        f"{r.status!r}: the audit withdrew its certification in the same breath")
    assert (r.extras or {}).get("finding") == "AUDIT_DID_NOT_CERTIFY", r.extras
    assert (r.extras or {}).get("audit_rc") == 1, r.extras
    assert "did not certify" in r.detail.lower(), r.detail


def test_the_same_holds_for_the_qualified_word(monkeypatch, tmp_path):
    """PASS_WITH_WAIVERS is a green word too, and the canary leaves it standing just the
    same. Without this the fix could be read as being about one literal."""
    r = _final_audit(monkeypatch, tmp_path, 1, _canary_stdout("PASS_WITH_WAIVERS"))
    assert r.status == _V.Verdict.NOT_MEASURED.value, r.status
    assert (r.extras or {}).get("audit_word") == "PASS_WITH_WAIVERS", r.extras


def test_a_clean_audit_is_still_a_pass(monkeypatch, tmp_path):
    """NEGATIVE CONTROL, and the one that matters most: the ordinary case must not move.
    Same stream, same word, rc 0 -- nothing to withdraw, so it is a PASS."""
    clean = _canary_stdout("PASS").split("\nflow_compliance_check: THIS REPORT")[0] + "\n"
    r = _final_audit(monkeypatch, tmp_path, 0, clean)
    assert r.status == _V.Verdict.PASS.value, (r.status, r.detail[:200])


def test_a_clean_waived_audit_is_still_waived(monkeypatch, tmp_path):
    """SECOND NEGATIVE CONTROL. The waiver word survives, which is what R-0915-85 put
    here: the audit's own word, straight through, not translated."""
    clean = (_canary_stdout("PASS_WITH_WAIVERS")
             .split("\nflow_compliance_check: THIS REPORT")[0] + "\n")
    r = _final_audit(monkeypatch, tmp_path, 0, clean)
    assert r.status == _V.Verdict.PASS_WITH_WAIVERS.value, (r.status, r.detail[:200])


def test_a_failing_audit_is_still_a_fail(monkeypatch, tmp_path):
    """THIRD NEGATIVE CONTROL: the refusal must not swallow a real FAIL into
    `cannot certify`. A FAIL word with a non-zero exit is the audit agreeing with itself."""
    out = _canary_stdout("FAIL").split("\nflow_compliance_check: THIS REPORT")[0] + "\n"
    r = _final_audit(monkeypatch, tmp_path, 1, out)
    assert r.status == _V.Verdict.FAIL.value, (r.status, r.detail[:200])


def test_the_verdict_is_read_from_the_line_not_from_the_stream(monkeypatch, tmp_path):
    """THE SUBSTRING HAZARD ITSELF, on a stream that quotes the phrase without owning it.

    A substring search cannot tell the audit's verdict line from the same phrase appearing
    anywhere else; equality against a LINE-ANCHORED parse can.

    THE DECOY IS AFTER THE VERDICT LINE, AND THAT POSITION IS THE POINT. My first cut put
    it before, and a mutation proved the arm worthless there: with the decoy first,
    "take the last match" alone gets the right answer and the anchor carries no weight. The
    audit prints its `Overall:` line and THEN its blocker list, whose `observed:` rows quote
    gate output — so a quoted phrase genuinely arrives last, and only the anchor tells the
    indented quotation from the verdict line. Mutating the anchor away now reddens this.
    """
    out = ("=== Vibe-IC phase1_phase2_phase3 compliance ===\n"
           "\nOverall: FAIL  (strict=True)\n"
           "\nBlocker list (classified) — 1 non-PASS step(s).\n"
           "  [UNCLASSIFIED      ] Step 12: a nested gate\n"
           "       observed : program failed | output: === compliance === "
           "Overall: PASS  (strict=True)\n")
    assert "Overall: PASS" in out                      # the hazard is present ...
    assert out.rindex("Overall: PASS") > out.rindex("Overall: FAIL")   # ... and it is LAST
    r = _final_audit(monkeypatch, tmp_path, 1, out)
    assert r.status == _V.Verdict.FAIL.value, (        # ... and still not read
        f"a quoted `Overall: PASS` inside a blocker-list row was read as the run's "
        f"verdict: {r.status!r}")


def test_the_parser_takes_the_verdict_line_and_nothing_else():
    """The parser, directly: anchored, last-match-wins, and `None` for no verdict line.

    THE HELPER MOVED, and this arm followed it rather than being deleted. #2572 put the rule
    in `design_one_shot_runner`; two SIBLING readers turned out to have the same defect, so
    the rule moved to `_audit_verdict` — one owner, asked by all three — and it was MOVED, not
    copied. The property is unchanged; only its address is.
    """
    assert _AV.verdict_word("\nOverall: PASS  (strict=True)\n") == "PASS"
    # the decoy LAST, which is where the blocker list puts it — see the arm above
    assert _AV.verdict_word(
        "\nOverall: NOT_MEASURED  (strict=True)\n"
        "       observed : ... | output: Overall: PASS  (strict=True)\n") == "NOT_MEASURED"
    # and the last VERDICT LINE wins when there really are two of them
    assert _AV.verdict_word(
        "Overall: PASS  (strict=True)\nOverall: FAIL  (strict=True)\n") == "FAIL"
    assert _AV.verdict_word("no verdict here at all\n") is None
    assert _AV.verdict_word("") is None


def test_the_timeout_tier_is_untouched(monkeypatch, tmp_path):
    """#525's TIMEOUT is its own name and must not be absorbed by the new refusal: rc 124
    is a budget that ran out, not an audit that declined to certify."""
    r = _final_audit(monkeypatch, tmp_path, 124, _canary_stdout("PASS"))
    assert r.status == _V.Verdict.FAIL.value, r.status
    assert (r.extras or {}).get("finding") == "AUDIT_TIMEOUT", r.extras


# ═══ (b) one vocabulary, asked rather than retyped ═════════════════════════

def test_every_word_a_phase_can_hand_up_is_classified():
    """THE DEFECT, stated as the invariant it breaks. `verdict.run_verdict` returns one of
    `RUN_PRECEDENCE`; every one of those words must be a tier here, or a phase that used
    it is refused as unrecognised."""
    known = P23._known_phase_verdicts()
    missing = [v.value for v in _V.RUN_PRECEDENCE if v.value not in known]
    assert not missing, (
        f"phase verdict word(s) `verdict.run_verdict` can return and this aggregator "
        f"would refuse as unknown: {missing}")


def test_a_phase_that_measured_nothing_is_neither_green_nor_a_failure():
    """THE REPRODUCTION for (b), and the doctrine, in one table. The precedence is the
    front door's: FAIL > NOT_MEASURED > PASS_WITH_WAIVERS > PASS."""
    nm, ok = {"verdict": "NOT_MEASURED"}, {"verdict": "PASS"}
    assert P23._aggregate_verdict(nm, ok, True, True) == "NOT_MEASURED"
    assert P23._aggregate_verdict(ok, nm, True, True) == "NOT_MEASURED"
    # a found defect outranks an absence -- the front door's own order
    assert P23._aggregate_verdict({"verdict": "FAIL"}, nm, True, True) == "FAIL"
    # and an absence outranks a waiver, so it cannot be softened into one
    assert P23._aggregate_verdict({"verdict": "PASS_WITH_WAIVERS"}, nm,
                                  True, True) == "NOT_MEASURED"


def test_the_clean_and_qualified_answers_are_unchanged():
    """NEGATIVE CONTROLS for (b): the three answers that already worked still work,
    including both legacy spellings this aggregator carries for compatibility."""
    ok = {"verdict": "PASS"}
    assert P23._aggregate_verdict(ok, ok, True, True) == "PASS"
    for legacy in ("WAIVED", "PASS_WITH_OPEN_SOURCE_CONSTRAINTS"):
        assert P23._aggregate_verdict({"verdict": legacy}, ok,
                                      True, True) == "PASS_WITH_WAIVERS", legacy
    assert P23._aggregate_verdict({"verdict": "FAIL"}, ok, True, True) == "FAIL"


def test_an_unrecognised_word_is_still_refused_by_name():
    """THE GUARD THE FIX MUST NOT SOFTEN. Adding a tier must not turn the catch-all back
    on: a word nobody classified is still refused, and the refusal still says which phase
    produced it."""
    out = P23._aggregate_verdict({"verdict": "SOMETHING_NEW"}, {"verdict": "PASS"},
                                 True, True)
    assert out.startswith("UNKNOWN_PHASE_VERDICT:"), out
    assert "phase2=SOMETHING_NEW" in out, out


def test_not_applicable_is_not_given_a_tier_it_cannot_reach():
    """The review that found (b) named "NOT_MEASURED/INCOMPLETE". `INCOMPLETE` is not a
    phase-level word at all -- it is not in `verdict.Verdict` -- and `NOT_APPLICABLE` is
    in the enum but `run_verdict` never returns it. A tier for either would have no
    member, which is its own defect, so this pins that neither was invented."""
    assert "INCOMPLETE" not in {v.value for v in _V.Verdict}
    known = P23._known_phase_verdicts()
    assert "INCOMPLETE" not in known
    assert _V.Verdict.NOT_APPLICABLE.value not in {v.value for v in _V.RUN_PRECEDENCE}
    assert _V.Verdict.NOT_APPLICABLE.value not in known
