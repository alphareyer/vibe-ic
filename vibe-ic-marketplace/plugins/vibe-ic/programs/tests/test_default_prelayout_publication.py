from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import execution_policy as policy


def _fixture(tmp_path):
    project = tmp_path / "project"
    netlist = project / "phase2/stage2/synth/spm_synth.v"
    sdc = project / "phase2/stage2/constraints/spm.asic.sdc"
    record = project / "phase2/stage2/constraints/asic_sdc.json"
    for path in (netlist, sdc, record):
        path.parent.mkdir(parents=True, exist_ok=True)
    netlist.write_text("module spm; endmodule\n")
    sdc.write_text("create_clock -period 10 [get_ports clk]\n")
    record.write_text(json.dumps({"path": str(sdc.relative_to(project))}))
    gate = project / "phase3/librelane/prelayout/gates/step8_sdc_opensta.json"
    gate.parent.mkdir(parents=True, exist_ok=True)
    return project, netlist, sdc, gate


def _fake_phase3(netlist, sdc, gate, *, status="PASS"):
    digest = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
    calls = []

    def publish(project, top, pdk, container):
        calls.append((project, top, pdk, container))
        gate.write_text(json.dumps({
            "verdict": "PASS" if status == "PASS" else "FAIL",
            "netlist_sha256": digest(netlist), "sdc_sha256": digest(sdc),
        }))
        return types.SimpleNamespace(status=status, detail="fake prelayout")

    return types.SimpleNamespace(
        _pl=types.SimpleNamespace(synth_dir=lambda project: project / "phase2/stage2/synth"),
        _llc=types.SimpleNamespace(digest=digest),
        step_prelayout_signoff=publish,
    ), calls


def _runtime(project):
    return {"project": project, "policy": {"mode": "default"},
            "parameters": {"top": "spm", "pdk": object(), "container": "vibeic-eda"}}


def test_default_step15_refreshes_stale_gate_before_consumer(tmp_path, monkeypatch):
    project, netlist, sdc, gate = _fixture(tmp_path)
    fake, calls = _fake_phase3(netlist, sdc, gate)
    monkeypatch.setitem(sys.modules, "phase3_one_shot_runner", fake)

    policy._ensure_default_prelayout(_runtime(project), "15")

    assert len(calls) == 1
    assert json.loads(gate.read_text())["verdict"] == "PASS"


def test_default_step15_reuses_current_gate(tmp_path, monkeypatch):
    project, netlist, sdc, gate = _fixture(tmp_path)
    fake, calls = _fake_phase3(netlist, sdc, gate)
    digest = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
    gate.write_text(json.dumps({"verdict": "PASS",
                                "netlist_sha256": digest(netlist),
                                "sdc_sha256": digest(sdc)}))
    monkeypatch.setitem(sys.modules, "phase3_one_shot_runner", fake)

    policy._ensure_default_prelayout(_runtime(project), "15")

    assert calls == []


def test_default_step15_stops_on_native_publication_failure(tmp_path, monkeypatch):
    project, netlist, sdc, gate = _fixture(tmp_path)
    fake, _ = _fake_phase3(netlist, sdc, gate, status="FAIL")
    monkeypatch.setitem(sys.modules, "phase3_one_shot_runner", fake)

    with pytest.raises(policy.Refusal, match="PRELAYOUT_PUBLICATION_FAILED"):
        policy._ensure_default_prelayout(_runtime(project), "15")
