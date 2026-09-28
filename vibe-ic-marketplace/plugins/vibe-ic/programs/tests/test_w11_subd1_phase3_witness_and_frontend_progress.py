"""W11 SUBD1: preparation is not Phase 3 measurement; tool progress is not a clock.

Both cases are generic fixtures.  No EDA image or design oracle is needed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import _watchdog as watchdog  # noqa: E402
import l24_signoff_evidence_backed_check as l24  # noqa: E402
import p0_tool_frontend_check as frontend  # noqa: E402


def _at(path: Path, payload: dict, stamp: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    os.utime(path, (stamp, stamp))


def test_phase2_technology_preparation_is_not_phase3_measurement(tmp_path):
    netlist = tmp_path / "phase2/stage2/synth/top_synth.v"
    netlist.parent.mkdir(parents=True)
    netlist.write_text("module top; endmodule\n")
    os.utime(netlist, (1000, 1000))
    _at(tmp_path / "reports/phase3/technology_units.json", {
        "program": "phase3_one_shot_runner.publish_database_unit_declaration",
        "verdict": "PASS", "published": True,
    }, 2000)
    assert l24._phase3_has_run(tmp_path) is False

    # A different non-audit JSON is also not a producer witness merely
    # because it was written beneath reports/phase3 after synthesis.
    _at(tmp_path / "reports/phase3/unrelated.json", {
        "program": "input_preparation", "verdict": "PASS",
    }, 2100)
    assert l24._phase3_has_run(tmp_path) is False


def test_current_phase3_producer_witness_still_counts(tmp_path):
    netlist = tmp_path / "phase2/stage2/synth/top_synth.v"
    netlist.parent.mkdir(parents=True)
    netlist.write_text("module top; endmodule\n")
    os.utime(netlist, (1000, 1000))
    _at(tmp_path / "reports/phase3/lvs_verdict.json", {"status": "FAIL"}, 2000)
    assert l24._phase3_has_run(tmp_path) is True


def test_preparation_provenance_cannot_impersonate_a_signoff_path(tmp_path):
    netlist = tmp_path / "phase2/stage2/synth/top_synth.v"
    netlist.parent.mkdir(parents=True)
    netlist.write_text("module top; endmodule\n")
    os.utime(netlist, (1000, 1000))
    _at(tmp_path / "reports/phase3/lvs_verdict.json", {
        "program": "phase3_one_shot_runner.publish_database_unit_declaration",
        "status": "PASS",
    }, 2000)
    assert l24._phase3_has_run(tmp_path) is False


def test_p0_frontend_host_tool_has_progress_budget_without_clock_kill(
        tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(frontend.shutil, "which",
                        lambda name: "/usr/bin/" + name if name in ("yosys", "timeout")
                        else None)

    def fake_run(command, **kw):
        seen["command"] = command
        seen["kw"] = kw
        return watchdog.SupervisedResult(0, "healthy progress", "", "natural")

    monkeypatch.setattr(watchdog, "run_host_supervised", fake_run)
    result = frontend._invoke("yosys", ["-V"], tmp_path, None)
    assert result.returncode == 0
    assert seen["command"] == ["yosys", "-V"]
    assert seen["kw"]["stall_grace_s"] > 0
    assert seen["kw"]["hard_ceiling_s"] > 0  # recorded only by _watchdog


def test_p0_frontend_container_tool_has_memory_and_identity_reap_without_clock(
        tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(frontend.shutil, "which",
                        lambda name: "/usr/bin/docker" if name == "docker"
                        else None)
    monkeypatch.setattr(frontend, "default_image", lambda: "test-image")
    monkeypatch.setattr(frontend._dmem, "docker_memory_flags",
                        lambda: ["--memory", "4g", "--memory-swap", "4g"])

    def fake_run(command, **kw):
        seen["command"] = command
        seen["kw"] = kw
        return watchdog.SupervisedResult(0, "healthy progress", "", "natural")

    monkeypatch.setattr(watchdog, "run_host_supervised", fake_run)
    result = frontend._invoke("yosys", ["-V"], tmp_path, None)
    assert result.returncode == 0
    cmd = seen["command"]
    assert cmd[:2] == ["docker", "run"]
    assert cmd[cmd.index("--entrypoint") + 1] == "yosys"
    assert "timeout" not in cmd
    assert cmd[cmd.index("--memory") + 1] == "4g"
    assert seen["kw"]["kill"] is not None
    assert seen["kw"]["cpu_probe"] is not None
    assert seen["kw"]["hard_ceiling_s"] > 0


def test_p0_host_tool_child_has_an_address_space_ceiling(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(frontend.shutil, "which",
                        lambda name: "/usr/bin/" + name if name == "yosys"
                        else None)
    monkeypatch.setattr(frontend._dmem, "memory_limit", lambda: "2g")

    def fake_run(command, **kw):
        seen.update(kw)
        return watchdog.SupervisedResult(0, "", "", "natural")

    monkeypatch.setattr(watchdog, "run_host_supervised", fake_run)
    assert frontend._invoke("yosys", ["-V"], tmp_path, None).returncode == 0
    # If the reviewed implementation supplied no child limiter, run the same
    # command with the supervisor's default launch shape and observe its
    # numeric limit.  This is a value control, not a missing-key assertion.
    factory = seen.get("popen_factory", lambda command, **kw: subprocess.Popen(
        command, start_new_session=True, **kw))
    child = factory([sys.executable, "-c",
                     "import resource; print(resource.getrlimit(resource.RLIMIT_AS)[0])"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = child.communicate(timeout=15)
    assert child.returncode == 0, err
    assert int(out.strip()) == 2 * 1024 ** 3
