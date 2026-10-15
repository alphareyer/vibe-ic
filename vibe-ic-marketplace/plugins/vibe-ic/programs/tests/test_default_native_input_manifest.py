"""Neutral caller/producer/receipt/consumer controls; no native IC execution."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import execution_backend_producers as backend
import phase3_one_shot_runner as runner
from test_backend_input_contract_sdc import _project


def _write(project, relative, content):
    path = project / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


@pytest.mark.parametrize("case", [
    "current", "updated_route", "directory", "missing", "changed", "measured_fail",
])
def test_default_current_native_manifest_reaches_the_consumer(tmp_path, monkeypatch, case):
    project = _project(tmp_path)
    _write(project, "input/step_0_5ic_answers.json", '{"route":"DIE"}\n')
    _write(project, "input/submission_template/tapeout_declaration.json", '{"route":"DIE"}\n')
    route = _write(project, "phase3/stage3/pnr/routed.def", "original routed layout\n")
    source = project / "provenance.jsonl"
    original_source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    child = "phase2/stage1/sim/tb/source_control.v"
    if case == "directory":
        _write(project, child, "module source_control; endmodule\n")
    missing = ["provenance.jsonl", "phase3/librelane/prelayout/gates/step8_sdc_opensta.json"]
    if case == "missing":
        for raw in missing:
            (project / raw).unlink()
    cfg = SimpleNamespace(name="synthetic")
    monkeypatch.setattr(runner, "_backend_parameter_values", lambda *_: {
        "pdk_root": str(project / "neutral_pdk"), "image_id": "SOURCE_CONTROL_ONLY"})
    monkeypatch.setattr(backend, "_parameters", lambda *_: (
        runner, None, cfg, "SOURCE_CONTROL_ONLY"))
    seen, calls = {}, []
    actual_produce = backend.produce

    def observed(root, params):
        seen["manifest"] = params["resolved_input_manifest"]
        result = actual_produce(root, params)
        seen["producer_result"] = result
        return result

    monkeypatch.setattr(backend, "produce", observed)

    def controlled_native(root, *_args):
        # Replace the native instrument, retaining the real caller, producer,
        # current receipt authority, output collector and canonical consumers.
        calls.append("native_instrument")
        if case == "changed":
            source.write_text("changed stable source\n")
        if case == "updated_route":
            route.write_text("producer-promoted routed layout\n")
            _write(root, "phase3/stage3/postroute_timing_repair/repair_log.json", json.dumps({
                "changes": [{"type": "buffer_insert", "net": "neutral"}],
                "re_verified": True, "affected_steps": [21]}))
        else:
            _write(root, "phase3/stage3/postroute_timing_repair/no_repair_needed.flag",
                   "neutral instrument found no repair needed\n")
        decision = json.dumps({"repair_needed": case == "updated_route"})
        for suffix in (".json", ".canonical.json"):
            _write(root, "phase3/stage3/postroute_timing_repair/postroute_timing_repair_decision" + suffix,
                   decision)
        return runner.StepResult("postroute_repair_librelane",
            "FAIL" if case == "measured_fail" else "PASS", 0,
            "controlled native failure" if case == "measured_fail" else "neutral instrument complete")

    monkeypatch.setattr(runner, "step_postroute_repair_librelane", controlled_native)
    row = runner._default_pnr_rows(project, "neutral", cfg, "SOURCE_CONTROL_ONLY", ["32"])
    receipt_path = project / "reports/phase3/default_steps/32/backend_result.json"
    receipt = json.loads(receipt_path.read_text()) if receipt_path.is_file() else None
    _write(project, "API_OBSERVATION.json", json.dumps({
        "case": case, "caller_status": row.status, "caller_detail": row.detail,
        "instrument_calls": calls, "seen": seen, "receipt": receipt,
        "route": route.read_text(), "original_source_sha256": original_source_sha,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest() if source.exists() else None,
        "scope": "SOURCE_CONTROL_ONLY; no native EDA or IC qualification"}, indent=2))
    if case == "missing":
        assert row.status == "NOT_MEASURED", row.detail
        assert calls == [], "missing current producer inputs must stop before the tool"
        assert all(raw in row.detail for raw in missing), row.detail
    else:
        assert calls == ["native_instrument"]
        expected = "NOT_MEASURED" if case == "changed" else "FAIL" if case == "measured_fail" else "PASS"
        assert row.status == expected, row.detail
        assert receipt["verdict"] == expected, receipt
        if case != "measured_fail":
            current = receipt["current_step_declared_producers"]
            assert seen["manifest"]["files"]["provenance.jsonl"] == original_source_sha
            if case == "changed":
                assert current["reason"] == "NATIVE_CURRENT_INPUT_CHANGED: provenance.jsonl"
            else:
                assert current["inputs"]["provenance.jsonl"]["sha256"] == original_source_sha
                assert current["verdict"] == "PRODUCED"
                assert receipt["canonical_step_result"]["status"] == "PASS", receipt
                assert {r["gate"] for r in receipt["gate_ledger"]} >= {
                    "flow_step_output_content_check", "postroute_timing_repair_audit"}
        if case == "directory":
            assert child in seen["manifest"]["files"], seen
        if case == "updated_route":
            assert route.read_text() == "producer-promoted routed layout\n"
            assert "phase3/stage3/pnr/routed.def" not in current["inputs"]
