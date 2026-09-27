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


# =========================================================================
# THE WRAPPER, DRIVEN. Held by review icspm5_s5: every test above drives
# `_report_declares_expert_pass_outstanding` or reads source, so DISABLING the
# feature passed all nine of them and the whole suite. A predicate that nothing
# calls is not a behaviour.
#
# This one runs a REAL gate program on the rc-0 path — exit 0, the INCOMPLETE
# sentinel on stdout, and a `--json` report carrying the enumerated obligations
# — and asks `check_step` what the step became.
# =========================================================================

import json as _json  # noqa: E402
import textwrap as _tw  # noqa: E402

#: A gate in the shape `formal_proof_evidence_check` really has on spm: it
#: EXITS 0, prints the INCOMPLETE sentence, and states the outstanding expert
#: obligations in its own report rather than on stdout.
_FORMAL_GATE = '''
    import json, sys
    i = sys.argv.index("--json")
    open(sys.argv[i + 1], "w").write(json.dumps({
        "verdict": "INCOMPLETE",
        "property_denominator": 6,
        "fallback_skill": "formal-verify",
        "expert_fallback_outstanding": [
            "L8.clock_and_reset_waveform.clocks.0.edge",
            "L8.clock_and_reset_waveform.resets.0.n",
            "L8.clock_and_reset_waveform.resets.0.sync",
            "L8.clock_and_reset_waveform.clocks.0.period_ns",
            "L8.clock_and_reset_waveform.clocks.0.duty",
        ],
        "findings": ["EXPERT_FALLBACK_OUTSTANDING (R-0915-125)"],
    }))
    print("INCOMPLETE: applicable formal declaration/property work remains")
    sys.exit(0)
    '''


import pytest  # noqa: E402


@pytest.fixture
def shadow_programs(tmp_path_factory, monkeypatch):
    """A PRIVATE programs dir for `check_step` to resolve gate names against.

    `_resolve_program_cmd` resolves a gate NAME against `F.PROGRAMS_DIR`.
    Planting the fixture gate in the shipped programs/ made it appear and
    vanish under every concurrent worker that lists programs/*.py. The shadow
    holds a symlink to every real entry of programs/ (every other PROGRAMS_DIR
    lookup resolves to the same bytes) and the fixture gate as a real file,
    which the real resolver still finds BY NAME."""
    shadow = tmp_path_factory.mktemp("programs_shadow")
    for entry in PROGRAMS.iterdir():
        (shadow / entry.name).symlink_to(entry)
    monkeypatch.setattr(F, "PROGRAMS_DIR", shadow)
    # check_step prepends PROGRAMS_DIR to sys.path unless it is already on
    # it. Listing the shadow LAST (per-test) keeps its lazy imports resolving
    # from the real programs/ ahead of it, so no module is cached under a
    # tmp path for later tests in this worker.
    monkeypatch.setattr(sys, "path", list(sys.path) + [str(shadow)])
    return shadow


def _formal_step(prog: str) -> dict:
    return {"id": "5", "name": "Formal verification", "stage": "stage1",
            "gate": {"all_of": [
                {"program_exit_zero":
                 f"{prog} . --json reports/phase2/gates/formal_evidence.json"}]}}


def test_the_wrapper_reads_step_five_as_an_awaiting_wait(tmp_path,
                                                          shadow_programs):
    """THE BEHAVIOUR, end to end on the rc-0 path.

    The gate exits 0 — so none of the rc-4 machinery applies — and says nothing
    about AWAITING on stdout. The declaration is in its report. The step must
    still land on NOT_MEASURED/awaiting_agent_pass, because the population is
    fully enumerated (6 properties) and what is outstanding is the WORK.

    Disable the synthesis and this goes red on `reason_class`: the step falls
    back to the undifferentiated short-population reading this change exists to
    replace.
    """
    prog = shadow_programs / "_t_r0915_125_formal.py"
    prog.write_text(_tw.dedent(_FORMAL_GATE))
    try:
        (tmp_path / "reports" / "phase2" / "gates").mkdir(parents=True,
                                                          exist_ok=True)
        r = F.check_step(tmp_path, _formal_step(prog.stem), {}, None)
        assert r.status == F._T.Verdict.NOT_MEASURED.value, (
            r.status, r.reasons)
        assert r.reason_class == "awaiting_agent_pass", (
            f"the step read {r.reason_class!r}; an enumerated population with "
            f"outstanding EXPERT work is a stated WAIT, not a short "
            f"population: {r.reasons}")
        # ...and the row says it is waiting on an AGENT, not that it measured
        # a short population.
        joined = " ".join(str(x) for x in r.reasons)
        assert "AWAITING an agent pass" in joined, joined
        assert "partial" not in joined.lower(), joined
        # OBSERVED, not asserted as a requirement: the synthesised line's
        # DETAIL (the obligation count and the skill name) does not survive
        # into `reasons`. The line is consumed as a TOKEN and the canonical
        # AWAITING sentence is what the row carries, so a reader of the step
        # table learns THAT the step waits on an agent but not how much is
        # outstanding. That is the existing awaiting tier's shape, not
        # something this change introduced; noted on the page rather than
        # pinned here, because pinning it would freeze a decision nobody made.
    finally:
        prog.unlink(missing_ok=True)


def test_a_gate_with_no_outstanding_obligations_is_not_made_to_wait(
        tmp_path, shadow_programs):
    """THE OTHER DIRECTION, through the same wrapper. An identical gate whose
    report enumerates NO outstanding obligations must NOT be turned into a
    wait — otherwise the synthesis would manufacture an AWAITING for every
    rc-0 INCOMPLETE and the tier would stop meaning anything."""
    prog = shadow_programs / "_t_r0915_125_formal_done.py"
    prog.write_text(_tw.dedent(_FORMAL_GATE).replace(
        '"expert_fallback_outstanding": [',
        '"expert_fallback_outstanding": [] or [').replace(
        '"L8.clock_and_reset_waveform.clocks.0.edge",', '').replace(
        '"L8.clock_and_reset_waveform.resets.0.n",', '').replace(
        '"L8.clock_and_reset_waveform.resets.0.sync",', '').replace(
        '"L8.clock_and_reset_waveform.clocks.0.period_ns",', '').replace(
        '"L8.clock_and_reset_waveform.clocks.0.duty",', ''))
    try:
        (tmp_path / "reports" / "phase2" / "gates").mkdir(parents=True,
                                                          exist_ok=True)
        r = F.check_step(tmp_path, _formal_step(prog.stem), {}, None)
        assert r.reason_class != "awaiting_agent_pass", (
            f"an empty obligation list was turned into a wait: {r.reasons}")
    finally:
        prog.unlink(missing_ok=True)
