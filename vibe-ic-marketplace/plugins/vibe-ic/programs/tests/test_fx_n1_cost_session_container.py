"""FX-N1 cost — one long-lived container of the image per process, not one per call.

MEASURED 2026-09-28: the 595-file related selection took 44 min on main and 70
min on the first cut of `_eda_tool_route` (8HD-8). Part of that was an unfair
comparison (8HD-8 still has a host iverilog, which main used), but part was
real: with a container route and NO named container, every tool call was a
fresh `docker run --rm` (~360 ms each on an idle 8HD-9, more under load)
instead of a `docker exec` (~75 ms).

THE FIX UNDER TEST: the image route starts ONE session container per process
per image (same-path mounts by root, the memory ceiling, a label naming this
host/pid/start time, `--pull never`, and an owner-scoped lifetime) and `docker exec`s every
call into it; a new mount root replaces it with the union; a container that
died is recreated once, then refused; atexit removes it and a later process
reaps an orphan whose owner is gone. The host PATH is never the fallback.

HOW THE MACHINE IS KEPT OUT: PATH is ONE directory holding a fake docker that
logs every argv (and a fake host yosys that writes a marker if it is ever run),
exactly as in `test_fx_n1_host_tool_locality`.
"""
from __future__ import annotations

import os
import shutil
import socket
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_fx_n1_host_tool_locality import (  # noqa: E402  the same fixtures
    _execs, _fake_docker, _fake_yosys, _session_runs, farm)  # noqa: F401


#: The subject of every test here IS where an EDA tool runs and what is said
#: when it cannot, so a failure naming a tool this host lacks is still a red
#: about the route, never a NOT_VERIFIED about the host (`_outcome_states`).
pytestmark = pytest.mark.outcome_state_exempt(
    "subject: the EDA tool route itself (where a tool runs, and the refusal)")


@pytest.fixture
def docker_farm(farm, monkeypatch):
    """The farm, with a docker client and a stated pinned image."""
    from _stated_eda_image import state_the_image
    _fake_docker(farm.bin, farm.docker_log)
    assert shutil.which("docker") == str(farm.bin / "docker")
    farm.image = state_the_image(monkeypatch)
    import _eda_tool_route as T
    # `raising=False`: the tree before the fix has no session state, and the
    # tests below must then fail on THEIR assertion (one run per call), not here.
    monkeypatch.setattr(T, "_SESSIONS", {}, raising=False)
    monkeypatch.setattr(T, "_RETIRED", [], raising=False)
    monkeypatch.setattr(T, "_SESSION_STATE",
                        {"reaped": False, "atexit": True, "starts": 0,
                         "recreated": 0}, raising=False)
    return farm


def _iv(T, cwd, *extra):
    return T.run(["iverilog", "-V", *extra], cwd=cwd, capture_output=True,
                 text=True, timeout=60)


# ── the cost ────────────────────────────────────────────────────────────────

def test_one_session_container_serves_every_call(docker_farm, tmp_path):
    """RED before the fix: three calls were three `docker run --rm`."""
    import _eda_tool_route as T
    for _ in range(3):
        cp = _iv(T, tmp_path)
        assert cp.returncode == 0
    runs = [c for c in docker_farm.docker_calls() if c.startswith("run ")]
    assert len(runs) == 1 and " -d " in f" {runs[0]} ", docker_farm.docker_calls()
    assert len(_execs(docker_farm, " iverilog ")) == 3, docker_farm.docker_calls()


def test_concurrent_calls_in_one_process_share_one_session(docker_farm, tmp_path):
    """RED before the fix: each thread's call started its own container."""
    import _eda_tool_route as T
    errors = []

    def work():
        try:
            assert _iv(T, tmp_path).returncode == 0
        except Exception as exc:                          # noqa: BLE001
            errors.append(exc)
    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors, errors
    assert len([c for c in docker_farm.docker_calls() if c.startswith("run ")]) == 1


# ── what the session container is ───────────────────────────────────────────

def test_the_session_carries_the_memory_ceiling_owner_label_and_never_pulls(
        docker_farm, tmp_path, monkeypatch):
    import _eda_tool_route as T
    monkeypatch.setenv("VIBEIC_DOCKER_MEMORY", "2g")
    _iv(T, tmp_path)
    run = _session_runs(docker_farm)[0].split()
    img = run.index(docker_farm.image)
    assert run[run.index("--memory") + 1] == "2g" and run.index("--memory") < img
    assert run[run.index("--memory-swap") + 1] == "2g"
    assert "--rm" in run and "--pull" in run and run[run.index("--pull") + 1] == "never"
    assert f"{T.SESSION_LABEL}=1" in run
    owner = run[run.index(f"{T.SESSION_LABEL}=1") + 2]
    assert owner.startswith(f"{T.SESSION_OWNER_LABEL}={socket.gethostname()}:{os.getpid()}:")
    assert run[run.index("--entrypoint") + 1] == "sleep"
    assert run[img + 1] == "infinity"
    assert f"{T._tmp_root()}:{T._tmp_root()}" in run      # the temp root, up front


def test_a_path_under_a_new_root_replaces_the_session_with_the_union_once(
        docker_farm, tmp_path):
    import _eda_tool_route as T
    home = str(Path.home())
    assert T.session_root(home) != T._tmp_root()        # precondition: two roots
    _iv(T, tmp_path)                                     # temp root only
    _iv(T, tmp_path, f"{home}/fx_n1_cost_probe.v")        # a NEW root
    _iv(T, tmp_path, f"{home}/other.v")                   # same roots again
    runs = _session_runs(docker_farm)
    assert len(runs) == 2, docker_farm.docker_calls()
    root = T.session_root(home)
    assert f"{root}:{root}" in runs[1] and f"{T._tmp_root()}:{T._tmp_root()}" in runs[1]
    assert len(T._RETIRED) == 1                          # retired, not killed


def test_named_container_liveness_and_mounts_are_rechecked_between_calls(
        docker_farm, tmp_path, monkeypatch):
    """A container stopping after call one must route call two elsewhere."""
    import _eda_tool_route as T
    monkeypatch.setattr(T._pin, "container_attach_refusal", lambda *_a, **_k: "")
    monkeypatch.setattr(T, "_container_image", lambda _name: docker_farm.image)
    inspect = Path(f"{docker_farm.docker_log}.inspect")
    inspect.write_text(f"true\n{tmp_path}|{tmp_path}\n")

    first = T.run(["iverilog", "-V"], cwd=tmp_path, container="lane-eda",
                  capture_output=True, text=True)
    assert first.returncode == 0 and first.eda_route["route"] == T.ROUTE_CONTAINER
    inspect.write_text("false\n")

    second = T.run(["iverilog", "-V"], cwd=tmp_path, container="lane-eda",
                   capture_output=True, text=True)
    assert second.returncode == 0 and second.eda_route["route"] == T.ROUTE_IMAGE
    inspect.write_text(f"true\n/other/path|/other/path\n")
    third = T.run(["iverilog", "-V"], cwd=tmp_path, container="lane-eda",
                  capture_output=True, text=True)
    assert third.returncode == 0 and third.eda_route["route"] == T.ROUTE_IMAGE
    calls = docker_farm.docker_calls()
    assert len(_execs(docker_farm, "lane-eda")) == 1, calls
    assert len(_session_runs(docker_farm)) == 1, calls


def test_named_container_image_identity_is_rechecked_between_calls(monkeypatch):
    import _eda_tool_route as T
    digest = "a" * 64
    other = "b" * 64
    reads = []
    values = iter([(digest, ""), (other, "")])
    monkeypatch.setattr(T._pin, "reference_digest", lambda _ref: digest)
    monkeypatch.setattr(T._pin, "local_repo_digests", lambda _ref: ([], ""))
    monkeypatch.setattr(T._pin, "container_image_digest",
                        lambda _name: reads.append(_name) or next(values))
    image = f"repo/tool@sha256:{digest}"
    assert T._container_runs_image("lane-eda", image) == (True, "")
    same, why = T._container_runs_image("lane-eda", image)
    assert same is False and "not the explicit image" in why
    assert reads == ["lane-eda", "lane-eda"]


def test_a_session_remains_usable_after_the_old_24_hour_boundary(
        docker_farm, tmp_path, monkeypatch):
    import _eda_tool_route as T
    now = [100.0]
    monkeypatch.setattr(T.time, "monotonic", lambda: now[0])
    first = _iv(T, tmp_path)
    assert first.returncode == 0
    now[0] += 25 * 60 * 60
    second = _iv(T, tmp_path)
    assert second.returncode == 0
    runs = _session_runs(docker_farm)
    assert len(runs) == 1, docker_farm.docker_calls()
    run = runs[0].split()
    assert run[run.index(docker_farm.image) + 1] == "infinity"
    assert len(_execs(docker_farm, " iverilog ")) == 2


def test_the_replacement_keeps_every_earlier_root(docker_farm):
    """UNION, not swap: after roots A then B, a call under A again must reuse
    the container (a swap would start a third one without A)."""
    import _eda_tool_route as T
    a1 = T.session_container(docker_farm.image, ["/fxn1root/a/x"], "iverilog")
    b = T.session_container(docker_farm.image, ["/fxn1root/b/y"], "iverilog")
    a2 = T.session_container(docker_farm.image, ["/fxn1root/a/z"], "iverilog")
    assert a1 != b and a2 == b
    runs = _session_runs(docker_farm)
    assert len(runs) == 2, docker_farm.docker_calls()
    assert "/fxn1root/a:/fxn1root/a" in runs[1] and "/fxn1root/b:/fxn1root/b" in runs[1]


# ── when it goes wrong ──────────────────────────────────────────────────────

def test_a_dead_session_is_recreated_once_and_the_call_rerun(docker_farm, tmp_path):
    import _eda_tool_route as T
    _iv(T, tmp_path)
    Path(f"{docker_farm.docker_log}.die").write_text("1\n")   # the next exec: gone
    cp = _iv(T, tmp_path)
    assert cp.returncode == 0
    assert len(_session_runs(docker_farm)) == 2, docker_farm.docker_calls()
    assert T._SESSION_STATE["recreated"] == 1


def test_a_session_that_dies_again_is_refused_never_hosted(docker_farm, tmp_path):
    import _eda_tool_route as T
    _fake_yosys(docker_farm.bin, docker_farm.marker)
    _iv(T, tmp_path)
    Path(f"{docker_farm.docker_log}.die").write_text("1\n2\n")
    with pytest.raises(T.ToolRouteRefused) as exc:
        T.run(["yosys", "-V"], cwd=tmp_path, capture_output=True, text=True)
    assert exc.value.code == T.SESSION_DIED and "died again" in exc.value.reason
    assert isinstance(exc.value, FileNotFoundError)
    assert not docker_farm.marker.exists(), "the host yosys was run"


def test_a_session_that_cannot_start_is_refused_with_the_reason(docker_farm, tmp_path):
    import _eda_tool_route as T
    _fake_yosys(docker_farm.bin, docker_farm.marker)
    d = docker_farm.bin / "docker"
    d.write_text(d.read_text().replace(
        "  run) ", "  run) echo 'docker: Error response from daemon: stated failure.' >&2; exit 125;; x) "))
    with pytest.raises(T.ToolRouteRefused) as exc:
        T.run(["yosys", "-V"], cwd=tmp_path, capture_output=True, text=True)
    assert exc.value.code == T.SESSION_START_FAILED and "stated failure" in exc.value.reason
    assert not docker_farm.marker.exists(), "the host yosys was run"


# ── lifetime ────────────────────────────────────────────────────────────────

def test_close_sessions_removes_every_container_this_process_started(docker_farm, tmp_path):
    import _eda_tool_route as T
    home = str(Path.home())
    _iv(T, tmp_path)
    _iv(T, tmp_path, f"{home}/x.v")                       # one current, one retired
    names = [c.split("--name ")[1].split()[0] for c in _session_runs(docker_farm)]
    T.close_sessions()
    removed = [c for c in docker_farm.docker_calls() if c.startswith("rm -f ")]
    assert sorted(r.split()[-1] for r in removed) == sorted(names), removed
    assert T._SESSIONS == {} and T._RETIRED == []


def test_orphans_are_reaped_only_when_their_owner_on_this_host_is_gone(docker_farm):
    import _eda_tool_route as T
    host = socket.gethostname()
    me = f"{host}:{os.getpid()}:{T._proc_start(os.getpid())}"
    dead_pid = 2 ** 22 + 12345                             # beyond pid_max: never alive
    assert not Path(f"/proc/{dead_pid}").exists()
    Path(f"{docker_farm.docker_log}.ps").write_text(
        f"deadbeef0001\t{host}:{dead_pid}:1\n"
        f"livebeef0002\t{me}\n"
        f"elsewhere0003\tsome-other-host:{dead_pid}:1\n"
        f"unlabelled0004\t\n")
    assert T.reap_orphan_sessions() == ["deadbeef0001"]
    removed = [c for c in docker_farm.docker_calls() if c.startswith("rm -f ")]
    assert removed == ["rm -f deadbeef0001"], removed


def test_a_reused_pid_is_not_mistaken_for_the_owner(docker_farm):
    """Same pid, different start time: the owner is gone and the pid was
    reused, so the container is an orphan."""
    import _eda_tool_route as T
    host = socket.gethostname()
    assert T._owner_alive(f"{host}:{os.getpid()}:{T._proc_start(os.getpid())}") is True
    assert T._owner_alive(f"{host}:{os.getpid()}:1") is False
    assert T._owner_alive(f"not-{host}:{os.getpid()}:1") is None


# ── the single exec builder, verified once ──────────────────────────────────

def test_an_owned_container_is_exec_d_without_a_digest_inspect_per_call(monkeypatch):
    import _container_exec as CE
    import _eda_pin
    calls = []
    monkeypatch.setattr(_eda_pin, "container_attach_refusal",
                        lambda c, env=None: calls.append(c) or "")
    monkeypatch.setattr(CE, "_OWNED_VERIFIED", set())
    assert CE.register_owned_container("mine") == ""
    for _ in range(3):
        assert CE.docker_exec_argv("mine", "true")[:3] == ["docker", "exec", "mine"]
    assert calls == ["mine"]                               # verified ONCE
    CE.docker_exec_argv("not-mine", "true")
    CE.docker_exec_argv("not-mine", "true")
    assert calls == ["mine", "not-mine", "not-mine"]       # still checked per call


def test_a_mismatched_container_is_never_registered(monkeypatch):
    import _container_exec as CE
    import _eda_pin
    monkeypatch.setattr(_eda_pin, "container_attach_refusal",
                        lambda c, env=None: "CONTAINER_IMAGE_MISMATCH: stated")
    monkeypatch.setattr(CE, "_OWNED_VERIFIED", set())
    assert "MISMATCH" in CE.register_owned_container("impostor")
    with pytest.raises(CE.ContainerImageMismatch):
        CE.docker_exec_argv("impostor", "true")


# ── a stalled call is killed WHERE IT LIVES (review wave 4c, MAJOR) ─────────
#
# A host-side supervisor that stops watching a routed call kills the `docker`
# client. `docker exec` does not forward SIGKILL, so the tool keeps running in
# the container. The fix launches the tool through the identity stamp prelude
# and, on a stall, runs `_docker_watchdog.kill_supervised_job` INSIDE the
# container against that stamp -- the path `run_in_container_supervised` uses.

_PIDFILE_RE = r"/tmp/\.vibeic-job-[0-9a-f]+\.pid"


def _stalling_docker(F):
    """The farm's docker, whose `exec` of a command naming `stallme` never
    returns (a still tool). `/bin/sleep` by absolute path: PATH is the farm."""
    d = F.bin / "docker"
    d.write_text(d.read_text().replace(
        "  exec) ", "  exec) case \" $* \" in *\" stallme \"*) /bin/sleep 60;; esac; "))


def _no_grace(monkeypatch):
    import types
    import time as _time
    import _docker_watchdog as DW
    monkeypatch.setattr(DW, "time", types.SimpleNamespace(
        sleep=lambda s: None, monotonic=_time.monotonic, time=_time.time))


def _assert_reaped_inside(F):
    import re
    calls = F.docker_calls()
    tool = [c for c in calls if c.startswith("exec ") and " stallme" in c]
    assert tool, calls
    m = re.search(_PIDFILE_RE, tool[0])
    assert m, f"the tool was not launched through the identity stamp: {tool[0]}"
    pidfile = m.group(0)
    assert 'exec "$@" sh iverilog stallme' in tool[0], tool[0]
    reaps = [c for c in calls if c.startswith("exec ") and pidfile in c
             and "kill -" in c]
    assert any("kill -TERM" in c for c in reaps), calls
    assert any("kill -KILL" in c for c in reaps), calls
    # ... in the SAME container the tool was exec'd into
    tool_container = tool[0].split()[1:]
    reap_container = reaps[0].split()[1:]
    assert _container_of(tool_container) == _container_of(reap_container)
    return pidfile


def _container_of(exec_args):
    """The container name in `docker exec [opts] NAME cmd...` (opts that take
    a value: -w -e -u)."""
    i = 0
    while i < len(exec_args):
        a = exec_args[i]
        if a in ("-w", "-e", "-u", "--workdir", "--env", "--user"):
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        return a
    return None


def test_a_stalled_supervised_run_is_killed_inside_the_container(
        docker_farm, tmp_path, monkeypatch):
    """RED before the fix: the stall killed the `docker` client and nothing
    was ever sent into the container."""
    import _eda_tool_route as T
    import _progress_run as PR
    _stalling_docker(docker_farm)
    _no_grace(monkeypatch)
    with pytest.raises(PR.Stalled):
        T.supervised_run(["iverilog", "stallme"], cwd=tmp_path,
                         stall_looks=2, poll_s=0.3)
    _assert_reaped_inside(docker_farm)


def test_a_stalled_watchdog_run_is_killed_inside_the_container(
        docker_farm, tmp_path, monkeypatch):
    import _eda_tool_route as T
    _stalling_docker(docker_farm)
    _no_grace(monkeypatch)
    res = T.watchdog_run(["iverilog", "stallme"], cwd=tmp_path,
                         stall_grace_s=0.9, poll_s=0.3)
    assert res.outcome == "stalled", res
    _assert_reaped_inside(docker_farm)


def test_a_call_that_finishes_is_never_reaped(docker_farm, tmp_path, monkeypatch):
    """The reap is for a stall only: a normal call leaves no kill behind
    (the stamp file is dropped, nothing is signalled)."""
    import _eda_tool_route as T
    _no_grace(monkeypatch)
    cp = T.supervised_run(["iverilog", "-V"], cwd=tmp_path, stall_looks=2,
                          poll_s=0.3)
    assert cp.returncode == 0
    res = T.watchdog_run(["iverilog", "-V"], cwd=tmp_path, stall_grace_s=0.9,
                         poll_s=0.3)
    assert res.outcome == "natural", res
    assert not [c for c in docker_farm.docker_calls() if "kill -" in c]
    # ... and each call's identity stamp is dropped afterwards
    import re
    stamps = {m.group(0) for c in _execs(docker_farm, "iverilog -V")
              for m in [re.search(_PIDFILE_RE, c)] if m}
    dropped = {m.group(0) for c in _execs(docker_farm, "rm -f --")
               for m in [re.search(_PIDFILE_RE, c)] if m}
    assert len(stamps) == 2 and stamps <= dropped, (stamps, dropped)


def test_a_stalled_call_on_the_named_container_route_is_killed_in_that_container(
        docker_farm, tmp_path, monkeypatch):
    import _eda_tool_route as T
    import _eda_pin
    import _progress_run as PR
    monkeypatch.setattr(_eda_pin, "container_attach_refusal", lambda c, env=None: "")
    monkeypatch.setattr(T, "_container_state",
                        lambda c: (True, "", [(str(tmp_path), str(tmp_path))]))
    _stalling_docker(docker_farm)
    _no_grace(monkeypatch)
    with pytest.raises(PR.Stalled):
        T.supervised_run(["iverilog", "stallme"], cwd=tmp_path,
                         container="lane-eda", stall_looks=2, poll_s=0.3)
    _assert_reaped_inside(docker_farm)
    reaps = [c for c in docker_farm.docker_calls() if "kill -TERM" in c]
    assert _container_of(reaps[0].split()[1:]) == "lane-eda"
    assert not _session_runs(docker_farm)                 # no image session needed
