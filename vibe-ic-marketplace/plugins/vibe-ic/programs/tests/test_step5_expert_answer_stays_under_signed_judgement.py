"""Step 5's expert answer keeps its signed-judgement hand-off after it closes.

MEASURED 2026-09-29 (lane s5formal, 8hd-3) on a copy of the spm DIE run
`spmic5/run_v5c`: Step 5 read NOT_MEASURED `awaiting_signed_judgement: Step 5
has no AI review output to sign`. The IC-expert agent then did what the flow
asks — authored `formal_expert_properties.svh` and `formal_expert_review.json`
(`invocation_status: INVOKED`, one disposition per request id) — and reran
`formal_harness_gen` + `formal_property_run`. Both producers DELETE
`formal_authoring_request.json` once no obligation is left open, and
`ai_signed_judgement._requested("5")` keyed on that file alone. So the same
copy, with NO receipt under `reports/audit/ai_judgements/`, then read Step 5 =
PASS: the AI-authored properties were credited without the signed review the
hand-off exists to demand.

The rule the code follows now: an expert answer (the review receipt or the
property fragment) is AI output that stays under the hand-off until a receipt
signs it, whether or not a request is still open; and the fragment itself is
part of the signed evidence, so a signature cannot outlive the properties it
reviewed. A program-only closure (no expert output) still needs no signature.

chip-AGNOSTIC: generic module / property names; only the flow's own file names.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import flow_compliance_check as audit  # noqa: E402
import formal_harness_gen  # noqa: E402

import _ai_judgement_fixture  # noqa: E402

FORMAL = "phase2/stage1/formal"
RESULTS = f"{FORMAL}/results.json"
REQUEST = f"{FORMAL}/formal_authoring_request.json"
REVIEW = f"{FORMAL}/formal_expert_review.json"
FRAGMENT = f"{FORMAL}/formal_expert_properties.svh"
OID = "L8.clock_and_reset_waveform.resets.0.sync"


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _stage_expert_answer(root: Path) -> None:
    """A run whose floor asked the expert for one obligation, and the answer."""
    _write(root, "phase1/generated_docs/L8_TIMING_WAVEFORM.json",
           json.dumps({"clock_and_reset_waveform": {"resets": [
               {"name": "rst", "sync": "synchronous"}]}}))
    _write(root, REQUEST, json.dumps({
        "program": "formal_harness_gen", "verdict": "INCOMPLETE",
        "invocation_status": "REQUIRED_NOT_INVOKED",
        "unresolved_obligations": [{"id": OID, "status": "UNAUTHORED"}]}))
    _write(root, FRAGMENT,
           "property p_x_sync;\n  @(posedge clk) 1'b1;\nendproperty\n"
           "a_x_sync: assert property (p_x_sync);\n")
    _write(root, REVIEW, json.dumps({
        "fallback_skill": "formal-verify", "invocation_status": "INVOKED",
        "dispositions": [{"id": OID, "status": "AUTHORED",
                          "property": "p_x_sync"}]}))
    _write(root, RESULTS, json.dumps({"verdict": "PASS", "all_proved": True}))


def _close_request_like_the_producer(root: Path) -> None:
    """The real producer code path that runs once nothing is left open."""
    formal_harness_gen._write_property_contract(root, {
        "program": "formal_harness_gen", "verdict": "AUTHORED",
        "property_denominator": 1, "authored_property_count": 1,
        "covered_obligations": [{"id": OID, "property": "p_x_sync",
                                 "author": "formal-verify"}],
        "unresolved_obligations": []})


def _judge(root: Path):
    step = {"id": "5", "name": "formal", "stage": "stage1",
            "required_outputs": [RESULTS], "gate": {"files_exist": [RESULTS]}}
    return audit.check_step(root, step, {})


def test_closing_the_request_does_not_drop_the_signature_requirement(tmp_path):
    _stage_expert_answer(tmp_path)
    assert _judge(tmp_path).reason_class == "awaiting_signed_judgement"
    _close_request_like_the_producer(tmp_path)
    assert not (tmp_path / REQUEST).exists(), "producer kept the request"

    unsigned = _judge(tmp_path)
    assert unsigned.status == "NOT_MEASURED", unsigned.reasons
    assert unsigned.reason_class == "awaiting_signed_judgement", unsigned.reasons

    _ai_judgement_fixture.sign(tmp_path, "5")
    signed = _judge(tmp_path)
    assert signed.status == "PASS", signed.reasons


def test_the_signature_binds_the_expert_properties(tmp_path):
    _stage_expert_answer(tmp_path)
    _ai_judgement_fixture.sign(tmp_path, "5")
    assert _judge(tmp_path).status == "PASS"

    # A weaker property under the SAME name: the receipt still names it and
    # the request still lists it, so only the fragment's bytes can tell.
    _write(tmp_path, FRAGMENT,
           "property p_x_sync;\n  @(posedge clk) 1'b1 || 1'b0;\nendproperty\n"
           "a_x_sync: assert property (p_x_sync);\n")
    stale = _judge(tmp_path)
    assert stale.status == "NOT_MEASURED", stale.reasons
    assert stale.reason_class == "awaiting_signed_judgement", stale.reasons


def test_a_fragment_without_a_review_is_still_unsignable(tmp_path):
    _stage_expert_answer(tmp_path)
    (tmp_path / REVIEW).unlink()
    _close_request_like_the_producer(tmp_path)
    row = _judge(tmp_path)
    assert row.status == "NOT_MEASURED", row.reasons
    assert any("has no AI review output to sign" in r for r in row.reasons), row.reasons


def test_a_program_only_closure_needs_no_signature(tmp_path):
    """Control: no expert output at all → no hand-off, as before."""
    _write(tmp_path, RESULTS, json.dumps({"verdict": "PASS", "all_proved": True}))
    _write(tmp_path, f"{FORMAL}/formal_program_discharge.json",
           json.dumps({"invocation_status": "INVOKED_BY_PROGRAM"}))
    row = _judge(tmp_path)
    assert row.status == "PASS", row.reasons
