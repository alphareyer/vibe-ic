from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from dataclasses import replace

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import execution_modes as em
from execution_adapters_backend import (
    _step30_simulators,
    _step37_route_specs,
    _site_path,
    register_backend_adapters,
    validate,
    validate_streamout_receipt,
)
from execution_provider_catalog import BACKEND_IDS, coverage_rows

BASE = "59cd75606885b71ca7d11bbe34baf2541671be68"


def test_pre_fix_base_could_not_reach_backend_factory(tmp_path):
    path = "vibe-ic-marketplace/plugins/vibe-ic/programs/execution_adapters_backend.py"
    assert subprocess.run(["git", "cat-file", "-e", f"{BASE}:{path}"]).returncode != 0
    proc = subprocess.run([sys.executable, "-c", "import execution_adapters_backend"],
                          cwd=tmp_path, env={"PATH": "/usr/bin:/bin", "PYTHONPATH": ""},
                          text=True, capture_output=True)
    assert proc.returncode != 0
    assert "ModuleNotFoundError" in proc.stderr


def test_all_backend_rows_register_one_reachable_provider():
    registry = register_backend_adapters(source_sha=BASE, available=True)
    assert tuple(a.step_id for a in registry._adapters.values()) == BACKEND_IDS
    assert all(len(registry.adapters(step)) == 1 for step in BACKEND_IDS)
    assert all(a.role == "producer" for a in registry._adapters.values())
    assert all(a.qualification_evidence.endswith(".py") or "producer site=" in a.qualification_evidence
               for a in registry._adapters.values())


def test_default_call_path_is_source_only_and_unmeasured():
    registry = register_backend_adapters(source_sha=BASE, available=False)
    adapter = registry.adapters("15")[0]
    assert adapter.available is False
    assert adapter.availability_reason == "NATIVE_EXECUTION_NOT_MEASURED"
    assert adapter.tool_id == "librelane"
    assert adapter.engine_families == ("openroad",)


def test_backend_component_calls_real_worker_and_records_gate_boundary(tmp_path):
    source = tmp_path / "seed.txt"
    source.write_text("source-only\n")
    row = next(r for r in coverage_rows() if r["step_id"] == "15")
    context = em.Context("15", BASE, {"project/input/seed.txt": source},
                         {"metric": "canonical_evidence", "direction": "max"},
                         tuple(row["consumer_gates"]), "librelane")
    registry = register_backend_adapters(em.Registry(), source_sha=BASE, available=True)
    controller = em.Controller(registry, em.Budget(cpus=1, ram_mb=512, workers=1))
    plan = controller.plan(context, "default-mode")
    root = tmp_path / "run"
    assert plan["arms"] == ["backend_15"]
    summary = controller.run(context, root, "default-mode")
    assert summary["candidate_statuses"]["backend_15"] in {"NOT_MEASURED", "FAIL"}
    adapter = registry.adapters("15")[0]
    result = json.loads((root / adapter.arm_id / "outputs" / "backend_result.json").read_text())
    assert result["step_id"] == "15"
    assert "gate_ledger" in result and result["verdict"] in {"NOT_MEASURED", "FAIL"}
    assert result["canonical_receipts"][0]["producer"] == "execution_backend_producers.produce"


def test_public_controller_refuses_changed_actual_producer_call(tmp_path):
    source = tmp_path / "seed.txt"
    source.write_text("source-only\n")
    row = next(r for r in coverage_rows() if r["step_id"] == "15")
    context = em.Context("15", BASE, {"project/input/seed.txt": source},
                         {"metric": "canonical_evidence", "direction": "max"},
                         tuple(row["consumer_gates"]), "librelane")
    original_registry = register_backend_adapters(source_sha=BASE, available=True)
    original_adapter = original_registry.adapters("15")[0]
    producer = next(Path(name) for name in original_adapter.source_files
                    if Path(name).name == "execution_backend_worker.py")
    original = producer.read_text()
    mutated = original.replace("producer = produce(project, params)",
                               "producer = {'verdict': 'PASS', 'canonical_receipts': []}", 1)
    assert mutated != original
    mutant = tmp_path / "execution_backend_worker.py"
    mutant.write_text(mutated.replace(
        "from __future__ import annotations",
        "from __future__ import annotations\nimport sys\nsys.path.insert(0, " + repr(str(PROGRAMS)) + ")", 1))
    mutant_adapter = replace(
        original_adapter,
        source_files={**{k: v for k, v in original_adapter.source_files.items() if k != str(producer)},
                      str(mutant.resolve()): em.digest(mutant)},
        components=(replace(original_adapter.components[0],
                            argv=tuple(str(mutant.resolve()) if x == str(producer) else x
                                       for x in original_adapter.components[0].argv)),))
    registry = em.Registry()
    registry.register(mutant_adapter)
    root = tmp_path / "mutated-run"
    summary = em.Controller(registry, em.Budget(cpus=1, ram_mb=512, workers=1)).run(
        context, root, "default-mode")
    assert summary["candidate_statuses"]["backend_15"] in {"NOT_MEASURED", "FAIL"}
    result = json.loads((root / mutant_adapter.arm_id / "outputs" / "backend_result.json").read_text())
    assert result["producer_verdict"] == "PASS"
    assert result["canonical_receipts"] == []


def test_step30_default_and_explicit_xyce_are_parameters_of_one_provider():
    assert _step30_simulators() == ("ngspice",)
    assert _step30_simulators({"simulators": "xyce"}) == ("xyce",)
    with pytest.raises(em.Refusal):
        _step30_simulators({"simulators": ["ngspice", "ngspice"]})
    assert len([r for r in coverage_rows() if r["step_id"] == "30"]) == 1


def test_step37_has_one_engine_family_and_refuses_direct_magic_fallback():
    assert len(_step37_route_specs()) == 1
    with pytest.raises(em.Refusal):
        validate_streamout_receipt({}, route="direct_magic")
    assert validate_streamout_receipt({"status": "FAIL"}, route="direct_magic") == "FAIL"


def test_step37_receipt_requires_actual_streamout_engine():
    receipt = {"schema": "vibeic/step37-streamout-engine/1", "step_id": "37",
               "arm_id": "backend_37_librelane", "route": "librelane",
               "allowed_streamout_engines": ["magic", "klayout"],
               "streamout_engine": "magic", "status": "PASS"}
    assert validate_streamout_receipt(receipt) == "PASS"
    broken = dict(receipt, streamout_engine="toy")
    with pytest.raises(em.Refusal):
        validate_streamout_receipt(broken)


def test_reverse_mutations_refuse_missing_producer_symbol_and_output(tmp_path):
    with pytest.raises(em.Refusal):
        _site_path("execution_backend_producers.py:missing_row_producer")
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("mutated\n")
    binding = {"required_gates": ["canonical"]}
    receipt = {"binding": binding, "producer_verdict": "PASS",
               "gates": {"canonical": "PASS"}, "native_receipts": [{"tool": "native"}],
               "outputs": {"artifact.txt": "0" * 64}}
    (tmp_path / "backend_result.json").write_text(json.dumps(receipt))
    assert validate(tmp_path, binding).verdict == "NOT_MEASURED"
    receipt["producer_verdict"] = "FAIL"
    (tmp_path / "backend_result.json").write_text(json.dumps(receipt))
    assert validate(tmp_path, binding).verdict == "FAIL"
