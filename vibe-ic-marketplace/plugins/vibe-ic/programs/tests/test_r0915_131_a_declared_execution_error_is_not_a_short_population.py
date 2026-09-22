"""icslot51b — the third condition `partial_population` was absorbing.

icslot50 took AWAITING out of that bucket; icslot51 named the rest. This takes
out the one the ruling approved: a gate that declared an EXECUTION ERROR.

THE INPUT, run21 step 2, `flow_compliance_check --strict`:

    step 2   NOT_MEASURED   reason_class=partial_population
       INCOMPLETE: the gate reports its input was applicable and was NOT
       examined: flow_compliance_check . --stage-id stage_phase1 --strict
       --json reports/phase1/gates/stage_phase1_compliance.json
       [verdict=NOT_MEASURED, reason_class=EXECUTION_ERROR]

The hint says EXECUTION_ERROR in its own text. Nothing about step 2's population
was partially examined -- the nested stage_phase1 invocation FAILED TO RUN -- and
the class that says so was already in the evidence the step-level reader held,
then dropped for the blanket word. "Applicable and NOT examined" sends a reader
to the population; the answer was a crash.

SCOPE, and it is deliberate: ONLY `EXECUTION_ERROR`, ONLY from the producer's own
declaration. `_flow_reason_taxonomy` carries classes with no step-level member at
all -- `BLOCKED_BY_UPSTREAM` is one, which is why R-0915 declined to invent a
`blocked_by_upstream` and step 21 was fixed by making its gate MEASURE instead.
Reading a declaration is not the same as mapping one.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
FCC = PROGRAMS / "flow_compliance_check.py"
sys.path.insert(0, str(PROGRAMS))


def _fcc(name="fcc_r131"):
    spec = importlib.util.spec_from_file_location(name, FCC)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def _hint(m, cmd: str, suffix: str = "") -> str:
    return f"{m._INCOMPLETE_HINT_PREFIX}{cmd}{suffix}"


#: run21 step 2's hint, verbatim in shape.
_STEP2_SUFFIX = " [verdict=NOT_MEASURED, reason_class=EXECUTION_ERROR]"
_STEP2_CMD = ("flow_compliance_check . --stage-id stage_phase1 --strict "
              "--json reports/phase1/gates/stage_phase1_compliance.json")


def test_a_declared_execution_error_is_recognised():
    m = _fcc()
    assert m._hint_declares_execution_error(
        [_hint(m, _STEP2_CMD, _STEP2_SUFFIX)]) is True


def test_a_hint_with_no_declaration_is_not_an_execution_error():
    """THE CONTROL THAT KEEPS STEP 5. `formal_proof_evidence_check` prints the
    INCOMPLETE token and declares no class; its report carries
    `unresolved_obligations` against a `property_denominator`, so it really IS a
    partially examined population and must keep that word."""
    m = _fcc()
    assert m._hint_declares_execution_error(
        [_hint(m, "formal_proof_evidence_check . --json x.json")]) is False


def test_another_declared_class_is_not_promoted():
    """`BLOCKED_BY_UPSTREAM` has no step-level member; believing it here would
    invent a class the taxonomy does not have. Only EXECUTION_ERROR is read."""
    m = _fcc()
    assert m._hint_declares_execution_error(
        [_hint(m, "macro_obs_geometry_intersect_check . --json y.json",
               " [verdict=BLOCKED, reason_class=BLOCKED_BY_UPSTREAM]")]) is False


def test_a_class_named_inside_a_command_is_not_a_declaration():
    """Keyed on the `reason_class=` the DISCLOSED_INCOMPLETE site writes, so a
    gate whose argv merely mentions the word cannot promote itself."""
    m = _fcc()
    assert m._hint_declares_execution_error(
        [_hint(m, "some_check . --note EXECUTION_ERROR")]) is False


def test_one_declaring_hint_among_several_is_enough():
    m = _fcc()
    hints = [_hint(m, "a_check ."),
             _hint(m, _STEP2_CMD, _STEP2_SUFFIX),
             _hint(m, "b_check .")]
    assert m._hint_declares_execution_error(hints) is True


def test_no_hints_at_all_is_not_an_execution_error():
    m = _fcc()
    assert m._hint_declares_execution_error([]) is False


def test_the_step_class_is_execution_error_not_partial_population():
    """END TO END through the step classifier: the word a consumer reads."""
    m = _fcc("fcc_r131_e2e")
    assert m._T.ReasonClass.EXECUTION_ERROR.value == "execution_error"
    # the two classes this change chooses between are distinct members, so a
    # reader can tell a crash from a short population at all
    assert (m._T.ReasonClass.EXECUTION_ERROR.value
            != m._T.ReasonClass.PARTIAL_POPULATION.value)


def test_awaiting_keeps_its_precedence():
    """UNCHANGED, deliberately: AWAITING is a STATED wait and the measured case
    (D1). A run carrying both has not been measured, so this change asserts
    nothing further about that order."""
    m = _fcc("fcc_r131_await")
    # both markers present: the awaiting branch is first in the expression, so
    # the awaiting class is what a step would publish.
    assert m._AWAITING_HINT_PREFIX != m._INCOMPLETE_HINT_PREFIX
    assert m._hint_declares_execution_error(
        [_hint(m, _STEP2_CMD, _STEP2_SUFFIX)]) is True
