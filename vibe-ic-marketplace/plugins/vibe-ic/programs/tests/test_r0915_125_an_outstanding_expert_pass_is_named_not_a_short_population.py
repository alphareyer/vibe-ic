"""An outstanding EXPERT pass is a stated wait, not a partial population.

MEASURED on spm run22 (lane icspm5, 2026-09-23). Step 5's
`formal_proof_evidence_check` exits 0, prints

    INCOMPLETE: applicable formal declaration/property work remains

and publishes a report whose own findings are

    EXPERT_FALLBACK_OUTSTANDING (R-0915-125): the deterministic floor requested
      formal authoring and no expert receipt is present. This is NOT a failed
      proof -- it is work not yet done, and it is reported by id rather than as
      a verdict
    PROOF_CHAIN_PARTIAL (#1974): authored properties have an elaborated .sby +
      PASS transcript, but the declaration denominator remains open

with `property_denominator` 6, five `expert_fallback_outstanding` ids
(L8.clock_and_reset_waveform.*) and `fallback_skill` "formal-verify".

The step read NOT_MEASURED / partial_population. THE POPULATION IS NOT PARTIAL:
it is fully enumerated -- six properties, five obligations open -- and what is
outstanding is the WORK. The flow already has the word for that state, and the
rc-4 branch already synthesises it at column 0 for exactly this reason: a token
is believed only where it begins a line, and stdout is the channel a snippet
cut can delete. This reads the declaration from the REPORT and writes the line
itself.

NOT PROMOTED. The step stays NOT_MEASURED, because the work really has not been
done; what changes is that it now says WHICH pass is owed, so someone can make
it. The INCOMPLETE sentinel the gate prints still raises the INCOMPLETE tier.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import flow_compliance_check as F  # noqa: E402

SRC = (PROGRAMS / "flow_compliance_check.py").read_text()


def _report(outstanding=("a", "b"), skill="formal-verify", **extra):
    r = {"program": "formal_proof_evidence_check", "verdict": "INCOMPLETE",
         "fallback_skill": skill}
    if outstanding is not None:
        r["expert_fallback_outstanding"] = list(outstanding)
    r.update(extra)
    return r


# ------------------------------------------------------------------ POSITIVE

def test_the_enumerated_obligations_are_what_declares_the_wait():
    assert F._report_declares_expert_pass_outstanding(_report()) is True


def test_the_awaiting_line_is_written_at_column_zero():
    """`_stdout_signals_token` believes a token only where it begins a line, so
    a synthesised line that is not at column 0 is the same as no line."""
    i = SRC.index("_report_declares_expert_pass_outstanding(\n")
    seg = SRC[i:i + 900]
    assert "_AWAITING_STDOUT_TOKEN" in seg
    assert 'out = (f"{_AWAITING_STDOUT_TOKEN}: ' in seg, (
        "the token must open the synthesised line, not sit mid-sentence")
    assert '+ out' in seg, "the producer's own stdout must be kept below it"


def test_the_synthesised_line_passes_the_readers_own_token_test():
    line = ("AWAITING_AGENT_PASS: the gate states an EXPERT pass is "
            "outstanding (5 obligation(s), skill formal-verify) — stated by "
            "formal_proof_evidence_check\nINCOMPLETE: applicable formal work "
            "remains\n")
    assert F._stdout_signals_token(line, F._AWAITING_STDOUT_TOKEN)
    assert F._stdout_signals_token(line, F._INCOMPLETE_STDOUT_TOKEN), (
        "the INCOMPLETE tier must survive: this is never a bare PASS")


# ------------------------------------------------------------------ NEGATIVE

def test_an_empty_list_declares_nothing():
    """A gate that ran the fallback and has nothing outstanding must not be
    read as awaiting one."""
    assert F._report_declares_expert_pass_outstanding(
        _report(outstanding=[])) is False


def test_a_missing_key_declares_nothing():
    assert F._report_declares_expert_pass_outstanding(
        _report(outstanding=None)) is False


def test_a_skill_name_alone_is_not_the_claim():
    """The LIST is the claim, because it enumerates what the second pass owes.
    A `fallback_skill` with no outstanding obligations is a gate naming its
    fallback route, not one declaring a wait."""
    assert F._report_declares_expert_pass_outstanding(
        {"fallback_skill": "formal-verify"}) is False


def test_a_non_list_value_declares_nothing():
    for bad in (5, "formal-verify", {"a": 1}, True):
        assert F._report_declares_expert_pass_outstanding(
            _report(outstanding=None, expert_fallback_outstanding=bad)) is False


def test_a_non_mapping_report_declares_nothing():
    for bad in (None, [], "report", 7):
        assert F._report_declares_expert_pass_outstanding(bad) is False


def test_awaiting_outranks_partial_population_in_the_class_computation():
    """The precedence this change relies on is the flow's own, not a new one:
    an awaiting hint must beat PARTIAL_POPULATION, or the step would still be
    named for a population that is not short."""
    i = SRC.index("AWAITING keeps its")
    seg = SRC[i - 600:i + 600]
    assert "PARTIAL_POPULATION" in seg
    assert "awaiting_hints" in seg
