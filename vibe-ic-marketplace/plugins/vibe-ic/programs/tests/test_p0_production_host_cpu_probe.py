"""Exact container lifetime and cumulative host accounting at the shared probe."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _docker_watchdog as DWD
import _watchdog as WD

CID = "a" * 64
NAME = "vibeic_probe_owned"


def _host_fixture(module, monkeypatch, tmp_path):
    proc = tmp_path / "proc"
    cg = tmp_path / "cgroup"
    pid = proc / "12345"
    pid.mkdir(parents=True)
    relative = f"/system.slice/docker-{CID}.scope"
    scope = cg / relative.lstrip("/")
    scope.mkdir(parents=True)
    # Field 22 is starttime, including a comm containing spaces and ')'.
    fields = ["0"] * 30
    fields[0] = "S"
    fields[19] = "700"
    (pid / "stat").write_text("12345 (eda worker) child) " + " ".join(fields))
    (pid / "cgroup").write_text("0::" + relative + "\n")
    (scope / "cpu.stat").write_text("usage_usec 3000000\nuser_usec 2000000\nsystem_usec 1000000\n")
    doc = {"Id": CID, "Name": "/" + NAME,
           "State": {"Running": True, "Pid": 12345}}
    calls = []
    def runner(argv, **kw):
        calls.append(argv)
        if argv[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps([doc]), "")
        # The parent performs the forbidden exec and observes no host counter.
        return subprocess.CompletedProcess(argv, 0, "", "")
    real_path = Path
    monkeypatch.setattr(module, "Path", lambda p: proc if str(p) == "/proc" else
                        cg if str(p) == "/sys/fs/cgroup" else real_path(p))
    return {"proc": proc, "pid": pid, "cg": cg, "scope": scope,
            "relative": relative, "doc": doc, "calls": calls, "runner": runner}


def _no_exec(fixture):
    assert fixture["calls"] and all(a[:2] == ["docker", "inspect"]
                                    for a in fixture["calls"])


def test_cumulative_cpu_keeps_work_of_exited_children(monkeypatch, tmp_path, record_property):
    f = _host_fixture(DWD, monkeypatch, tmp_path)
    probe = DWD.ephemeral_container_cpu_probe(NAME, runner=f["runner"])
    first = probe(None)
    # A completed child's CPU remains in the kernel aggregate; no child /proc
    # entry exists. A process census could lose it, but cpu.stat cannot.
    (f["scope"] / "cpu.stat").write_text("usage_usec 4000000\n")
    second = probe(None)
    record_property("expected", json.dumps([3.0, 4.0]))
    record_property("actual", json.dumps([first, second]))
    record_property("argv", json.dumps(f["calls"]))
    assert [first, second] == [3.0, 4.0]
    _no_exec(f)
    assert f["calls"][0][-1] == NAME and f["calls"][1][-1] == CID


@pytest.mark.parametrize("change", ["id", "name", "pid", "start", "cgroup_path", "cgroup_inode", "counter_regression"])
def test_recycled_lifetime_is_refused(change, monkeypatch, tmp_path, record_property):
    f = _host_fixture(DWD, monkeypatch, tmp_path)
    probe = DWD.ephemeral_container_cpu_probe(NAME, runner=f["runner"])
    first = probe(None)
    if change == "id": f["doc"]["Id"] = "b" * 64
    elif change == "name": f["doc"]["Name"] = "/recycled_name"
    elif change == "pid": f["doc"]["State"]["Pid"] = 12346
    elif change == "start":
        (f["pid"] / "stat").write_text((f["pid"] / "stat").read_text().replace("700", "701"))
    elif change == "cgroup_path":
        other = f["cg"] / ("alternate/docker-" + CID + ".scope")
        other.mkdir(parents=True); (other / "cpu.stat").write_text("usage_usec 4000000\n")
        (f["pid"] / "cgroup").write_text("0::/alternate/docker-" + CID + ".scope\n")
    elif change == "cgroup_inode":
        f["scope"].rename(f["scope"].with_name("retired"))
        f["scope"].mkdir(); (f["scope"] / "cpu.stat").write_text("usage_usec 4000000\n")
    else: (f["scope"] / "cpu.stat").write_text("usage_usec 2000000\n")
    second = probe(None)
    record_property("expected", json.dumps([3.0, None]))
    record_property("actual", json.dumps([first, second]))
    assert [first, second] == [3.0, None]
    _no_exec(f)
    assert probe.last_observation["status"] == "NOT_MEASURED"
    assert "identity" in probe.last_observation["reason"] or "regressed" in probe.last_observation["reason"]


@pytest.mark.parametrize("missing", ["stopped", "pid", "v1", "foreign_cgroup", "cpu_missing", "cpu_malformed", "cpu_negative"])
def test_unavailable_cpu_is_none_and_never_exec(missing, monkeypatch, tmp_path, record_property):
    f = _host_fixture(DWD, monkeypatch, tmp_path)
    if missing == "stopped": f["doc"]["State"] = {"Running": False, "Pid": 0}
    elif missing == "pid": (f["pid"] / "stat").unlink()
    elif missing == "v1": (f["pid"] / "cgroup").write_text("2:cpu:/legacy\n")
    elif missing == "foreign_cgroup": (f["pid"] / "cgroup").write_text("0::/system.slice/foreign.scope\n")
    elif missing == "cpu_missing": (f["scope"] / "cpu.stat").unlink()
    elif missing == "cpu_malformed": (f["scope"] / "cpu.stat").write_text("usage_usec nonsense\n")
    else: (f["scope"] / "cpu.stat").write_text("usage_usec -1\n")
    value = DWD.ephemeral_container_cpu_probe(NAME, runner=f["runner"])(None)
    record_property("expected", "None; inspect only")
    record_property("actual", json.dumps({"value":value, "argv":f["calls"]}))
    assert value is None
    _no_exec(f)


def test_short_container_disappearance_and_timeout_keep_none(monkeypatch, tmp_path):
    f = _host_fixture(DWD, monkeypatch, tmp_path)
    gone = False
    def runner(argv, **kw):
        if gone: return subprocess.CompletedProcess(argv, 1, "", "no such object")
        return f["runner"](argv, **kw)
    probe = DWD.ephemeral_container_cpu_probe(NAME, runner=runner)
    assert probe(None) == 3.0
    gone = True
    assert probe(None) is None
    assert probe.last_observation["status"] == "NOT_MEASURED"
    def expired(argv, **kw): raise subprocess.TimeoutExpired(argv, 15)
    assert DWD.ephemeral_container_cpu_probe(NAME, runner=expired)(None) is None


def test_valid_zero_is_measured_and_new_invocation_has_new_binding(monkeypatch, tmp_path):
    f = _host_fixture(DWD, monkeypatch, tmp_path)
    probe = DWD.ephemeral_container_cpu_probe(NAME, runner=f["runner"])
    (f["scope"] / "cpu.stat").write_text("usage_usec 0\n")
    assert probe(None) == 0.0
    assert probe.last_observation["status"] == "MEASURED"
    assert DWD.ephemeral_container_cpu_probe(NAME, runner=f["runner"])(None) == 0.0


def test_missing_sample_does_not_reset_progress_meter(monkeypatch, tmp_path):
    f = _host_fixture(DWD, monkeypatch, tmp_path)
    probe = DWD.ephemeral_container_cpu_probe(NAME, runner=f["runner"])
    meter = WD.ProgressMeter(cpu_fn=lambda: probe(None))
    first = meter.sample()
    (f["scope"] / "cpu.stat").unlink()
    unavailable = meter.sample()
    (f["scope"] / "cpu.stat").write_text("usage_usec 4000000\n")
    final = meter.sample()
    assert [first, unavailable, final] == [3.0, 3.0, 4.0]
