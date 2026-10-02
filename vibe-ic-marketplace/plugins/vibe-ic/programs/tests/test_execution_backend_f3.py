from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import execution_modes as em
from execution_adapters_backend import (
    _step30_simulators,
    _step37_route_specs,
    register_backend_adapters,
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
