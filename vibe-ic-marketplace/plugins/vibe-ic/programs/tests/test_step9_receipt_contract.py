"""Focused Step 9 producer/consumer contract and mutation controls."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from programs.tests import test_execution_synthesis_engines as fixture

provider = fixture.provider
production = fixture.production
em = fixture.em


def test_consumer_rejects_legacy_filename_only_receipt(tmp_path, monkeypatch):
    output, binding = fixture._native_output(tmp_path, area=False)
    producer_path = output / "producer.json"
    producer = json.loads(producer_path.read_text())
    producer.pop("schema", None)
    producer_path.write_text(json.dumps(producer) + "\n")
    monkeypatch.setattr(production, "_run_gate", lambda *args: ("PASS", Path("gate")))
    with pytest.raises(em.Refusal, match="PRODUCTION_SYNTH_RECEIPT_SCHEMA_INVALID"):
        production.validate_synthesis(output, binding)


def test_consumer_accepts_real_producer_trace(tmp_path, monkeypatch):
    output, binding = fixture._native_output(tmp_path, area=False)
    monkeypatch.setattr(production, "_run_gate", lambda *args: ("PASS", Path("gate")))
    evidence = production.validate_synthesis(output, binding)
    assert evidence.verdict == "PASS"
    producer = json.loads((output / "producer.json").read_text())
    assert producer["native_trace"]
    trace = producer["native_trace"][0]
    assert trace["path"].startswith("phase3/")
    assert "project/" + trace["path"] in producer["output_hashes"]


@pytest.mark.parametrize("mutation", ["remove", "rename", "stale"],
                         ids=["remove-canonical", "rename-native", "stale-output-hash"])
def test_consumer_rejects_receipt_output_mutations(tmp_path, monkeypatch, mutation):
    output, binding = fixture._native_output(tmp_path, area=False)
    producer_path = output / "producer.json"
    producer = json.loads(producer_path.read_text())
    native = output / "project/phase2/stage2/synth/top_synth.v"
    if mutation == "remove":
        (output / "project/phase2/stage2/synth/netlist.v").unlink()
    elif mutation == "rename":
        native.rename(native.with_suffix(".legacy.v"))
    else:
        producer["output_hashes"][provider.NETLIST] = "0" * 64
        producer_path.write_text(json.dumps(producer) + "\n")
    monkeypatch.setattr(production, "_run_gate", lambda *args: ("PASS", Path("gate")))
    with pytest.raises(em.Refusal, match="PRODUCTION_SYNTH_OUTPUT_HASH"):
        production.validate_synthesis(output, binding)


def test_consumer_rejects_trace_path_traversal_when_receipt_is_co_mutated(
        tmp_path, monkeypatch):
    output, binding = fixture._native_output(tmp_path, area=False)
    producer_path = output / "producer.json"
    producer = json.loads(producer_path.read_text())
    outside = output.parent / "outside.trace"
    original = output / "project" / producer["native_trace"][0]["path"]
    outside.write_bytes(original.read_bytes())
    row = dict(producer["native_trace"][0])
    row["path"] = "../../outside.trace"
    row["sha256"] = fixture.em.digest(outside)
    producer["native_trace"] = [row]
    producer["tool"]["native_trace"] = [row]
    producer["tool_sha256"] = provider.stable_digest(producer["tool"])
    producer["consumer_binding"]["tool_sha256"] = producer["tool_sha256"]
    producer_path.write_text(json.dumps(producer) + "\n")
    monkeypatch.setattr(production, "_run_gate", lambda *args: ("PASS", Path("gate")))
    with pytest.raises(em.Refusal, match="PRODUCTION_NATIVE_TRACE_INVALID"):
        production.validate_synthesis(output, binding)


def test_consumer_rejects_missing_trace_field_when_receipt_is_co_mutated(
        tmp_path, monkeypatch):
    output, binding = fixture._native_output(tmp_path, area=False)
    producer_path = output / "producer.json"
    producer = json.loads(producer_path.read_text())
    producer.pop("native_trace")
    producer["tool"]["native_trace"] = None
    producer["tool_sha256"] = provider.stable_digest(producer["tool"])
    producer["consumer_binding"]["tool_sha256"] = producer["tool_sha256"]
    producer_path.write_text(json.dumps(producer) + "\n")
    monkeypatch.setattr(production, "_run_gate", lambda *args: ("PASS", Path("gate")))
    with pytest.raises(em.Refusal, match="PRODUCTION_NATIVE_TRACE_INVALID"):
        production.validate_synthesis(output, binding)


def test_worker_does_not_promote_a_legacy_mapped_filename():
    source = Path(fixture.native_worker.__file__).read_text()
    assert "shutil.copyfile(mapped, canonical)" not in source
