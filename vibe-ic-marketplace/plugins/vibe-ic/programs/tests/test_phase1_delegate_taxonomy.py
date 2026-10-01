"""Focused D1 producer/outer taxonomy boundary checks.

These are source/component fixture checks.  They prove propagation and
fail-closed identity handling; they are not native D1 or whole-IC evidence.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import phase1_doc_one_shot_runner as doc_runner  # noqa: E402
import phase1_one_shot_runner as outer  # noqa: E402


def _row(status: str, *, step_id: str = "D1", reason_class: str = "",
         outputs: object = None, detail: str = "current producer observation") -> dict:
    row = {
        "step_id": step_id,
        "status": status,
        "detail": detail,
        "output_files": ["/tmp/current-receipt.json"] if outputs is None else outputs,
    }
    if reason_class:
        row["reason_class"] = reason_class
    return row


def _run_outer_docs_policy_row(tmp_path, monkeypatch, row):
    """Call outer docs delegation through the real nested docs.main branch."""
    import execution_production

    (tmp_path / "input" / "docs").mkdir(parents=True)
    observed = {}

    def current_component(*args, **kwargs):
        parameters = args[2] if len(args) >= 3 else kwargs
        observed["call_id"] = parameters.get("invocation_id")
        return row

    monkeypatch.setattr(execution_production, "dispatch_site", current_component)
    return outer._run_docs_mode(tmp_path, "TST_CHIP", []), observed


def test_valid_not_measured_row_preserves_real_reason_without_valid_branch_typo(tmp_path):
    """The live valid-row branch must execute and retain execution_error."""
    result = doc_runner.Phase1DocDispatchResult.from_policy_row(
        _row("NOT_MEASURED", reason_class="execution_error"), tmp_path,
        invocation_id="current-call")

    assert int(result) == 1
    assert result.status == "NOT_MEASURED"
    assert result.reason_class == "execution_error"
    assert result.policy_row["reason_class"] == "execution_error"
    assert result.invocation["call_id"] == "current-call"


@pytest.mark.parametrize(
    "status,reason,detail",
    [
        ("FAIL", "", "producer failed before writing outputs"),
        ("NOT_MEASURED", "tool_absent", "native broker unavailable"),
    ],
)
def test_real_docs_main_policy_row_reaches_outer_with_status_reason_detail(
        tmp_path, monkeypatch, status, reason, detail):
    """The actual docs producer branch reaches the outer taxonomy intact."""
    result, observed = _run_outer_docs_policy_row(
        tmp_path, monkeypatch,
        _row(status, reason_class=reason, outputs=[], detail=detail))

    assert isinstance(result, doc_runner.Phase1DocDispatchResult)
    step = outer._docs_dispatch_step(result, tmp_path, 0.01)
    assert step is not None
    assert step.status == status
    assert step.reason_class == reason
    assert step.detail == detail
    assert observed["call_id"]
    assert step.extras["producer_invocation"]["call_id"] == observed["call_id"]


def test_measured_fail_remains_fail_at_outer_boundary(tmp_path):
    """A measured producer FAIL is not converted into NOT_MEASURED."""
    result = doc_runner.Phase1DocDispatchResult.from_policy_row(
        _row("FAIL"), tmp_path, invocation_id="current-call")

    step = outer._docs_dispatch_step(result, tmp_path, 0.01)

    assert step is not None
    assert step.status == "FAIL"
    assert step.extras["producer_policy_row"]["status"] == "FAIL"
    assert step.extras["producer_validation"]["status"] == "validated"


def test_current_not_measured_without_outputs_preserves_reason_and_detail(tmp_path):
    result = doc_runner.Phase1DocDispatchResult.from_policy_row(
        _row("NOT_MEASURED", reason_class="tool_absent", outputs=[],
             detail="native broker unavailable"), tmp_path,
        invocation_id="current-call")

    step = outer._docs_dispatch_step(result, tmp_path, 0.01)

    assert result.validated is True
    assert result.status == "NOT_MEASURED"
    assert result.reason_class == "tool_absent"
    assert result.detail == "native broker unavailable"
    assert step.status == "NOT_MEASURED"
    assert step.reason_class == "tool_absent"
    assert step.detail == "native broker unavailable"


def test_current_measured_fail_without_outputs_stays_fail(tmp_path):
    result = doc_runner.Phase1DocDispatchResult.from_policy_row(
        _row("FAIL", outputs=[], detail="producer failed before writing outputs"),
        tmp_path, invocation_id="current-call")

    step = outer._docs_dispatch_step(result, tmp_path, 0.01)

    assert result.validated is True
    assert step.status == "FAIL"
    assert step.detail == "producer failed before writing outputs"


def test_source_owned_legacy_rc1_is_measured_fail(tmp_path):
    result = doc_runner.Phase1DocLegacyResult.from_exit(
        1, tmp_path, "current-call", "legacy extraction measured FAIL")

    step = outer._docs_dispatch_step(result, tmp_path, 0.01)

    assert step.status == "FAIL"
    assert step.extras["producer_validation"]["status"] == "validated"


@pytest.mark.parametrize(
    "producer_row",
    [
        _row("PASS", outputs=[]),
        _row("PASS", step_id="other-request", outputs=["/tmp/other.json"]),
        {"step_id": "D1", "status": "UNKNOWN", "detail": "bad", "output_files": ["x"]},
    ],
)
def test_missing_malformed_or_other_request_row_refuses(producer_row, tmp_path):
    """Unbound producer evidence remains NOT_MEASURED with execution_error."""
    result = doc_runner.Phase1DocDispatchResult.from_policy_row(
        producer_row, tmp_path, invocation_id="current-call")

    assert int(result) == 1
    assert result.status == "NOT_MEASURED"
    assert result.reason_class == "execution_error"
    assert result.validated is False


def test_outer_rejects_typed_result_from_a_different_live_invocation(tmp_path, monkeypatch):
    """The opaque call id is threaded through the direct producer boundary."""
    (tmp_path / "input" / "docs").mkdir(parents=True)
    observed = {}

    def fake_current_producer():
        marker = "--_phase1-invocation-id"
        argv = list(sys.argv)
        observed["call_id"] = argv[argv.index(marker) + 1]
        return doc_runner.Phase1DocDispatchResult.from_policy_row(
            _row("NOT_MEASURED", reason_class="execution_error"),
            tmp_path, invocation_id="stale-" + observed["call_id"])

    monkeypatch.setattr(outer._phase1_doc, "main", fake_current_producer)
    result = outer._run_docs_mode(tmp_path, "TST_CHIP", [])

    assert observed["call_id"]
    assert result.status == "NOT_MEASURED"
    assert result.reason_class == "execution_error"
    assert result.invocation["call_id"] == observed["call_id"]
    assert result.validated is False
    assert "not bound to this current invocation" in result.detail


def test_outer_refuses_unmarked_integer_as_missing_production_result(tmp_path, monkeypatch):
    (tmp_path / "input" / "docs").mkdir(parents=True)
    monkeypatch.setattr(outer._phase1_doc, "main", lambda: 1)

    result = outer._run_docs_mode(tmp_path, "TST_CHIP", [])

    assert result.status == "NOT_MEASURED"
    assert result.reason_class == "execution_error"
    assert result.validated is False
    assert "no structured result" in result.detail
