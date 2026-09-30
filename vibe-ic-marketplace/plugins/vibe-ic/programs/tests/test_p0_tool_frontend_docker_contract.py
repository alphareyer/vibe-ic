"""The admitted P0 caller payload and resources, using its original fixture."""
import json
from types import SimpleNamespace

import pytest
import test_p0_tool_frontend_migration as original
import _watchdog

frontend = original.frontend
CPU_ENV = "VIBEIC_P0_DOCKER_CPUS"


def _admitted_call(monkeypatch, tmp_path):
    project = original._project(tmp_path)
    scratch = tmp_path / (frontend._ELAB_SCRATCH_PREFIX + "contract")
    scratch.mkdir()
    args = ["-p", f"proc; write_json {scratch}/rtl_elab.json"]
    monkeypatch.setattr(frontend.shutil, "which",
                        lambda tool: "/usr/bin/docker" if tool == "docker" else None)
    monkeypatch.setenv("VIBEIC_DOCKER_MEMORY", "1g")
    launches = []

    def capture(command, **kw):
        launches.append((command, kw))
        return SimpleNamespace(outcome="complete", rc=0, out="", err="",
                               supervision={}, elapsed_s=0)

    monkeypatch.setattr(_watchdog, "run_host_supervised", capture)
    return project, scratch, args, launches


@pytest.mark.parametrize("setting,expected_cpu", [(None, 1.0), ("1", 1.0), ("0.5", 0.5)])
def test_the_original_docker_call_keeps_tool_args_and_admitted_resources(
        setting, expected_cpu, monkeypatch, tmp_path, record_property):
    project, scratch, args, launches = _admitted_call(monkeypatch, tmp_path)
    monkeypatch.delenv(CPU_ENV, raising=False)
    if setting is not None:
        monkeypatch.setenv(CPU_ENV, setting)
    frontend._invoke("yosys", args, project, "img")
    command, supervision = launches[0]
    image_at = command.index("img")
    facts = {
        "payload": command[image_at:],
        "entrypoint_override": "--entrypoint" in command,
        "cpu": command[command.index("--cpus") + 1] if "--cpus" in command else None,
        "memory": command[command.index("--memory") + 1],
        "memory_swap": command[command.index("--memory-swap") + 1],
    }
    expected = {"payload": ["img", "--skip", "yosys", *args],
                "entrypoint_override": False, "cpu": expected_cpu,
                "memory": "1g", "memory_swap": "1g"}
    record_property("requested_argv", json.dumps(command))
    record_property("expected", json.dumps(expected))
    record_property("actual", json.dumps(facts))
    assert facts["payload"] == expected["payload"]
    assert facts["entrypoint_override"] is False
    assert facts["cpu"] is not None and float(facts["cpu"]) == expected_cpu
    assert facts["memory"] == facts["memory_swap"] == "1g"
    mounts = [command[i + 1] for i, word in enumerate(command) if word == "-v"]
    root = str(project.resolve())
    assert mounts == [f"{root}:{root}:ro", f"{scratch}:{scratch}"]
    assert command[command.index("-u") + 1] == f"{frontend.os.getuid()}:{frontend.os.getgid()}"
    assert command[command.index("--network") + 1] == "none"
    assert callable(supervision["cpu_probe"]) and callable(supervision["kill"])


@pytest.mark.parametrize("setting", ["", "garbage", "0", "-1", "nan", "inf", "-inf", "1e309"])
def test_an_invalid_stated_cpu_cap_refuses_before_any_native_launch(
        setting, monkeypatch, tmp_path, record_property):
    project, _scratch, args, launches = _admitted_call(monkeypatch, tmp_path)
    monkeypatch.setenv(CPU_ENV, setting)
    refused = False
    why = None
    try:
        frontend._invoke("yosys", args, project, "img")
    except frontend.ToolNotMeasured as exc:
        refused, why = True, str(exc)
    actual = {"refused": refused, "launch_count": len(launches)}
    record_property("setting", setting)
    record_property("expected", json.dumps({"refused": True, "launch_count": 0}))
    record_property("actual", json.dumps(actual))
    record_property("requested_argv", json.dumps([x[0] for x in launches]))
    assert actual == {"refused": True, "launch_count": 0}
    assert CPU_ENV in why and "refused" in why
