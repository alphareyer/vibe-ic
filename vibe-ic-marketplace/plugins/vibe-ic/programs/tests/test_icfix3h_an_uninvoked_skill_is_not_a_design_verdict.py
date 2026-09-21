"""(5) R-0915-125 — a FAIL is reserved for a proof that RAN and FAILED.

MEASURED on spm run13 AND run8 (8HD-4, `_lane_icspm5`), both digital DIEs,
`phase2/stage1/formal/results.json`:

    expert_fallback_required = True
    expert_fallback_invoked  = False
    expert_fallback_receipt  = None
    5 unresolved obligation(s) against a denominator of 6

and on run13's REAL 51-file formal tree, the same `audit()` both ways:

    pre-fix   verdict FAIL        EXPERT_FALLBACK_NOT_INVOKED
    fixed     verdict INCOMPLETE  EXPERT_FALLBACK_OUTSTANDING + the 5 ids

The old clause asked whether an expert had been INVOKED and made the answer a
contract violation. In a headless, program-only front-door run nobody invokes
`/formal-verify`, so it could never be satisfied: the gate was unpassable by
construction rather than by evidence. "Nobody invoked the skill" is a fact about
the runner's OPERATOR, not about the design.

THE OBLIGATIONS DO NOT DISAPPEAR. They are enumerated by id and still keep the
claim out of COMPLETE. R-0915-125(b): enumeration only — they stay visible and
block their own layer; nothing is re-routed and nothing is laundered.

R-0915-125(a): TWO invocation statuses, kept DISTINCT — `INVOKED` for a
human/skill answer and `INVOKED_BY_PROGRAM` for one the runner discharged
itself. An AI answer and a program answer must never be indistinguishable in the
record.

chip-AGNOSTIC: generic module and property names throughout.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

import test_v0_2_80_formal_evidence_chain as H  # noqa: E402

FPC = H.FPC
_formal = H._formal
_SBY_PASS_LOG = H._SBY_PASS_LOG

_OBLIGATIONS = [
    {"id": "L8.clock_and_reset_waveform.clocks.0.edge", "layer": "L8"},
    {"id": "L8.clock_and_reset_waveform.resets.0.polarity", "layer": "L8"},
]


def _claim(tmp_path, **results):
    f = _formal(tmp_path)
    (f / "assertions.sv").write_text("module asserts; endmodule\n")
    (f / "constraints.sby").write_text(
        "[script]\nread -formal assertions.sv\nprep -top asserts\n")
    (f / "constraints.sby.log").write_text(_SBY_PASS_LOG)
    payload = {
        "verdict": "PASS", "all_proved": True,
        "property_denominator": 1, "authored_property_count": 1,
        "unresolved_obligations": [],
        "bounded_vs_unbounded_scope": ["unbounded prove"],
        "sby": "phase2/stage1/formal/constraints.sby",
        "elaborated_sby": "phase2/stage1/formal/constraints.sby",
        "evidence": "phase2/stage1/formal/constraints.sby.log",
        "proof_transcript": "phase2/stage1/formal/constraints.sby.log",
    }
    payload.update(results)
    (f / "results.json").write_text(json.dumps(payload))
    return f


def test_an_uninvoked_expert_fallback_does_not_fail_the_claim(tmp_path):
    """THE DEFECT, in one assertion — the spm shape."""
    _claim(tmp_path, expert_fallback_required=True,
           expert_fallback_invoked=False, expert_fallback_receipt=None,
           unresolved_obligations=_OBLIGATIONS)
    rep = FPC.audit(tmp_path)
    assert rep["verdict"] != "FAIL", rep
    assert not any("EXPERT_FALLBACK_NOT_INVOKED" in r
                   for r in rep["findings"]), rep


def test_the_outstanding_obligations_are_enumerated_by_id(tmp_path):
    """R-0915-125(b). Enumeration only — the ids stay VISIBLE. A fix that
    merely stopped failing, without saying what is outstanding, would have
    laundered the gap into silence."""
    _claim(tmp_path, expert_fallback_required=True,
           expert_fallback_invoked=False, expert_fallback_receipt=None,
           unresolved_obligations=_OBLIGATIONS)
    rep = FPC.audit(tmp_path)
    finding = [r for r in rep["findings"] if "EXPERT_FALLBACK_OUTSTANDING" in r]
    assert finding, rep
    for row in _OBLIGATIONS:
        assert row["id"] in finding[0], (row["id"], finding[0])


def test_the_claim_is_still_not_complete(tmp_path):
    """The obligation still COSTS something. Not failing is not passing."""
    _claim(tmp_path, expert_fallback_required=True,
           expert_fallback_invoked=False, expert_fallback_receipt=None,
           unresolved_obligations=_OBLIGATIONS)
    rep = FPC.audit(tmp_path)
    assert rep["verdict"] != "PASS", rep


def test_a_program_authored_receipt_is_accepted_under_its_own_name(tmp_path):
    """R-0915-125(a). `INVOKED_BY_PROGRAM` is honoured…"""
    f = _claim(tmp_path, expert_fallback_required=True,
               expert_fallback_invoked=True,
               expert_fallback_receipt="phase2/stage1/formal/formal_expert_review.json",
               expert_fallback_invocation_status="INVOKED_BY_PROGRAM",
               unresolved_obligations=[])
    (f / "formal_expert_review.json").write_text(json.dumps(
        {"invocation_status": "INVOKED_BY_PROGRAM", "dispositions": []}))
    rep = FPC.audit(tmp_path)
    assert not any("EXPERT_" in r for r in rep["findings"]), rep


def test_a_human_receipt_is_still_accepted(tmp_path):
    """…and so is the skill's own `INVOKED`. Neither displaces the other."""
    f = _claim(tmp_path, expert_fallback_required=True,
               expert_fallback_invoked=True,
               expert_fallback_receipt="phase2/stage1/formal/formal_expert_review.json",
               expert_fallback_invocation_status="INVOKED",
               unresolved_obligations=[])
    (f / "formal_expert_review.json").write_text(json.dumps(
        {"invocation_status": "INVOKED", "dispositions": []}))
    rep = FPC.audit(tmp_path)
    assert not any("EXPERT_" in r for r in rep["findings"]), rep


def test_an_unknown_invocation_status_is_still_refused(tmp_path):
    """THE OTHER DIRECTION. A receipt that exists is still held to its
    contract: a status that is neither of the two recognised kinds is not a
    third quiet way to pass."""
    f = _claim(tmp_path, expert_fallback_required=True,
               expert_fallback_invoked=True,
               expert_fallback_receipt="phase2/stage1/formal/formal_expert_review.json",
               expert_fallback_invocation_status="RUBBER_STAMPED",
               unresolved_obligations=[])
    (f / "formal_expert_review.json").write_text(json.dumps(
        {"invocation_status": "RUBBER_STAMPED", "dispositions": []}))
    rep = FPC.audit(tmp_path)
    assert rep["verdict"] == "FAIL", rep
    assert any("EXPERT_RECEIPT_STATUS_UNKNOWN" in r for r in rep["findings"]), rep


def test_the_gate_can_still_fail_for_a_real_reason(tmp_path):
    """A gate that cannot fail is not a gate. Deleting one FAIL cause must not
    have deleted the gate's ability to refuse: a proof whose evidence does not
    dereference still FAILs."""
    _claim(tmp_path, expert_fallback_required=True,
           expert_fallback_invoked=False, expert_fallback_receipt=None,
           unresolved_obligations=_OBLIGATIONS,
           evidence="phase2/stage1/formal/does_not_exist.log")
    rep = FPC.audit(tmp_path)
    assert rep["verdict"] == "FAIL", rep
    assert any("EVIDENCE_MISSING" in r for r in rep["findings"]), rep
