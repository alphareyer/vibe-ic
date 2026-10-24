"""Focused positive, wrong-value, restoration and stale-readset controls."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pytest

import flow109_mixed_native as mixed


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def context(step):
    return {"requested_step": step, "native_step_id": "flow109." + step,
            "context_sha256": "c" * 64,
            "resolved_row": {"id": step, "obligations": [
                {"id": item} for item in mixed.ROWS[step]["obligations"]],
                "profile": {"dispatch": {"adapter": "flow109_mixed_native"}}}}


def project(tmp_path, step, *, wrong=False):
    root = tmp_path / step
    source = root / "input.bin"
    geometry = root / "phase3/mixed_signal/top_merged.gds"
    authority = root / "input/flow109" / (step + ".json")
    source.parent.mkdir(parents=True)
    geometry.parent.mkdir(parents=True)
    authority.parent.mkdir(parents=True)
    source.write_bytes(b"current input\n")
    geometry.write_bytes(b"current geometry\n")
    authority.write_text(json.dumps({
        "schema": mixed.INPUT_SCHEMA, "step_id": step,
        "scope": mixed.ROWS[step]["scope"], "top": "chip_top", "pdk": "gf180mcuD",
        "image": mixed.PINNED_IMAGE, "geometry_sha256": sha(geometry),
        "input_sha256": {"input.bin": ("0" * 64 if wrong else sha(source))},
        **({"required_scenarios": ["functional"]} if step in ("M06", "M08") else {})
    }))
    return root


def install_fakes(monkeypatch):
    monkeypatch.setattr(mixed, "_current_inputs",
                        lambda project, _step, _top: {"input.bin": sha(project / "input.bin")})
    monkeypatch.setattr(mixed, "operation_sources", lambda: {"programs/fake.py": "f" * 64})
    monkeypatch.setattr(mixed, "_semantic_pass",
                        lambda project, step, manifest: {
                            "step": step, "scope": manifest["scope"],
                            "geometry_sha256": sha(project / "phase3/mixed_signal/top_merged.gds")})
    monkeypatch.setattr(mixed, "_command_plan", lambda project, step, manifest, attempt: (
        [sys.executable, "-c", "print('producer')"],
        [[sys.executable, "-c", "print('consumer')"]]))


@pytest.mark.parametrize("step", tuple(mixed.ROWS))
def test_each_registered_row_needs_native_chain_then_current_consumer(monkeypatch, tmp_path, step):
    install_fakes(monkeypatch)
    root = project(tmp_path, step)
    component = mixed.execute(root, context(step))
    assert component["component_status"] == "PASS"
    result = mixed.consume_default_row_completion(root, context(step), component)
    assert result["default_row_status"] == "PASS"
    assert result["completed"] is True
    assert result["flow109_acceptance"] == "NOT_MEASURED"
    assert result["whole_ic_acceptance"] == "NOT_MEASURED"


def test_wrong_value_refuses_before_any_native_command_then_restoration_passes(monkeypatch, tmp_path):
    install_fakes(monkeypatch)
    calls = []
    original = mixed._run
    monkeypatch.setattr(mixed, "_run", lambda *args, **kwargs: calls.append(args) or original(*args, **kwargs))
    root = project(tmp_path, "M06", wrong=True)
    refused = mixed.execute(root, context("M06"))
    assert refused["component_status"] == "NOT_MEASURED"
    assert refused["reason"] == "MIXED_ROW_INPUT_STALE_OR_INCOMPLETE"
    assert calls == []
    authority = root / "input/flow109/M06.json"
    value = json.loads(authority.read_text())
    value["input_sha256"]["input.bin"] = sha(root / "input.bin")
    authority.write_text(json.dumps(value))
    restored = mixed.execute(root, context("M06"))
    assert restored["component_status"] == "PASS"
    assert len(calls) == 2


def test_changed_consumed_input_refuses_completion_without_replay(monkeypatch, tmp_path):
    install_fakes(monkeypatch)
    root = project(tmp_path, "M07")
    component = mixed.execute(root, context("M07"))
    (root / "input.bin").write_bytes(b"changed after native\n")
    result = mixed.consume_default_row_completion(root, context("M07"), component)
    assert result["default_row_status"] == "NOT_MEASURED"
    assert result["reason"] == "MIXED_ROW_INPUT_STALE_OR_INCOMPLETE"


def test_changed_native_log_refuses_completion(monkeypatch, tmp_path):
    install_fakes(monkeypatch)
    root = project(tmp_path, "M09")
    component = mixed.execute(root, context("M09"))
    log = next((root / "reports/flow109/M09").glob("*/command-00.log"))
    log.write_text("changed\n")
    result = mixed.consume_default_row_completion(root, context("M09"), component)
    assert result["default_row_status"] == "NOT_MEASURED"
    assert result["reason"] == "MIXED_ROW_COMPLETION_EVIDENCE_STALE"


def test_registry_dispatch_is_narrowly_scoped_to_assigned_rows():
    registry = json.loads((Path(__file__).parents[2] / "flow/flow109.json").read_text())
    rows = {row["id"]: row for row in registry["rows"]}
    for step in mixed.ROWS:
        dispatch = rows[step]["profiles"][0]["dispatch"]
        assert dispatch["adapter"] == "flow109_mixed_native"
        assert dispatch["native_step_id"] == "flow109." + step
    assert rows["M03"]["profiles"][0].get("dispatch", {}).get("adapter") != "flow109_mixed_native"
