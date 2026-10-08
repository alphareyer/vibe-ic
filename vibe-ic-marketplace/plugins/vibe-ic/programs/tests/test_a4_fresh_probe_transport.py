import json
import subprocess

import pytest

import _container_exec as transport


def selected_container():
    return {
        "Image": "sha256:" + "a" * 64,
        "Config": {"Image": "mutable:tag", "Env": ["PDK_ROOT=/foss/pdks"],
                   "WorkingDir": "/project", "User": "1000"},
        "HostConfig": {"Memory": 4 * 1024 ** 3},
        "Mounts": [{"Type": "bind", "Source": "/retained", "Destination": "/input", "RW": False},
                   {"Type": "bind", "Source": "/output", "Destination": "/project", "RW": True}],
    }


def install_inspect(monkeypatch, selected):
    monkeypatch.setenv("VIBEIC_A4_FRESH_PROBES", "1")

    def inspect(argv, **kwargs):
        assert argv == ["docker", "inspect", "selected-id"]
        return subprocess.CompletedProcess(argv, 0, json.dumps([selected]), "")

    monkeypatch.setattr(transport.subprocess, "run", inspect)


def test_unselected_callers_keep_existing_transport(monkeypatch):
    monkeypatch.delenv("VIBEIC_A4_FRESH_PROBES", raising=False)
    assert transport._unguarded_exec_argv("old", "bash", "-c", "true", opts=("-w", "/project")) == [
        "docker", "exec", "-w", "/project", "old", "bash", "-c", "true"]


def test_a4_probe_uses_selected_bytes_paths_limits_and_deadline(monkeypatch):
    selected = selected_container()
    install_inspect(monkeypatch, selected)
    command = ("timeout", "-k", "5", "120", "bash", "-lc", "test -s /input/deck.sp")
    argv = transport._unguarded_exec_argv("selected-id", *command)
    assert argv[:4] == ["docker", "run", "--rm", "--init"]
    assert "exec" not in argv
    assert argv[argv.index("--memory") + 1] == str(4 * 1024 ** 3)
    assert argv[argv.index("--memory-swap") + 1] == str(4 * 1024 ** 3)
    assert "/retained:/input:ro" in argv
    assert "/output:/project" in argv
    assert "IIC_OSIC_TOOLS_QUIET=1" in argv
    image_index = argv.index(selected["Image"])
    assert argv[image_index + 1:] == ["--skip", *command]
    assert "mutable:tag" not in argv


@pytest.mark.parametrize("invalid", ["zero_memory", "implicit_volume", "unsupported_option"])
def test_a4_probe_refuses_unbounded_or_unmapped_context(monkeypatch, invalid):
    selected = selected_container()
    opts = ()
    if invalid == "zero_memory":
        selected["HostConfig"]["Memory"] = 0
    elif invalid == "implicit_volume":
        selected["Mounts"][0]["Type"] = "volume"
    else:
        opts = ("--privileged",)
    install_inspect(monkeypatch, selected)
    with pytest.raises(ValueError):
        transport._unguarded_exec_argv("selected-id", "true", opts=opts)
