"""A bounded Phase-3 window re-enters its parent's project lock (#588).

The orchestrator (``vibe_ic_one_shot_runner --entry-step 15 --exit-step
38``) holds ``<project>/.runner.lock`` and hands its child the
re-entrancy token.  The window child must make the SAME re-entrancy
decision ``_runner_lock.acquire_or_reenter`` makes -- read-only, since a
window never writes into the project -- and a private copy of the
project must not inherit the parent's live lock and refuse its own
enclosing run.  A genuinely concurrent holder must still be refused.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import _runner_lock
import phase3_one_shot_runner as p3

DEADLINE_S = 300


def _write_lock(project: Path, pid: int) -> bytes:
    lock = project / _runner_lock.LOCK_FILENAME
    lock.write_text(json.dumps({"pid": pid, "timestamp": "x",
                                "runner": "vibe_ic_one_shot_runner"}))
    return lock.read_bytes()


def _run_window(project: Path, token):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    env.pop(_runner_lock.REENTRANCY_ENV, None)
    if token is not None:
        env[_runner_lock.REENTRANCY_ENV] = token
    return subprocess.run(
        [sys.executable, str(Path(p3.__file__)), str(project),
         "--entry-step", "15", "--exit-step", "38"],
        env=env, capture_output=True, text=True, check=False,
        timeout=DEADLINE_S)


@pytest.fixture
def project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    return project


@pytest.fixture
def stranger():
    proc = subprocess.Popen(["sleep", "120"])
    yield proc.pid
    proc.kill()
    proc.wait(timeout=30)


def test_window_child_reenters_its_parents_lock(project):
    lock_bytes = _write_lock(project, os.getpid())
    before = p3._phase3_file_manifest(project)
    cp = _run_window(project, f"{os.getpid()}:{project.resolve()}")
    assert "CONCURRENT_RUN_REFUSED" not in cp.stderr, cp.stderr
    assert "RUNNER_LOCK_REENTRANT" in cp.stderr, cp.stderr
    # Past the lock, the unadmitted empty project is refused by name later.
    assert "REFUSED" in cp.stderr
    assert (project / _runner_lock.LOCK_FILENAME).read_bytes() == lock_bytes
    assert p3._phase3_file_manifest(project) == before


def test_window_without_token_still_refuses_live_lock(project):
    lock_bytes = _write_lock(project, os.getpid())
    cp = _run_window(project, None)
    assert cp.returncode == 3
    assert "CONCURRENT_RUN_REFUSED" in cp.stderr
    assert (project / _runner_lock.LOCK_FILENAME).read_bytes() == lock_bytes


def test_window_token_for_other_project_still_refuses(project, tmp_path):
    _write_lock(project, os.getpid())
    cp = _run_window(project, f"{os.getpid()}:{(tmp_path / 'other').resolve()}")
    assert cp.returncode == 3
    assert "CONCURRENT_RUN_REFUSED" in cp.stderr


def test_window_token_not_naming_the_live_holder_still_refuses(project,
                                                                stranger):
    _write_lock(project, stranger)
    cp = _run_window(project, f"{os.getpid()}:{project.resolve()}")
    assert cp.returncode == 3
    assert "CONCURRENT_RUN_REFUSED" in cp.stderr


def test_reentrant_holder_pid_is_the_acquire_or_reenter_decision(
        project, stranger, monkeypatch):
    env = _runner_lock.REENTRANCY_ENV
    _write_lock(project, os.getpid())
    monkeypatch.setenv(env, f"{os.getpid()}:{project.resolve()}")
    assert _runner_lock.reentrant_holder_pid(project) == os.getpid()
    monkeypatch.setenv(env, f"{os.getpid()}:{project.parent.resolve()}")
    assert _runner_lock.reentrant_holder_pid(project) is None
    monkeypatch.delenv(env)
    assert _runner_lock.reentrant_holder_pid(project) is None
    monkeypatch.setenv(env, "garbage")
    assert _runner_lock.reentrant_holder_pid(project) is None
    (project / _runner_lock.LOCK_FILENAME).write_text('{"pid": "x"}')
    monkeypatch.setenv(env, f"{os.getpid()}:{project.resolve()}")
    assert _runner_lock.reentrant_holder_pid(project) is None
    _write_lock(project, stranger)
    assert _runner_lock.reentrant_holder_pid(project) is None


def test_window_clone_does_not_inherit_the_source_lock(project, tmp_path):
    (project / "input.txt").write_text("content")
    lock_bytes = _write_lock(project, os.getpid())
    clone = tmp_path / "clone" / "project"
    clone.parent.mkdir()
    p3._phase3_window_clone(project, clone)
    assert (clone / "input.txt").read_text() == "content"
    assert not (clone / _runner_lock.LOCK_FILENAME).exists()
    assert (project / _runner_lock.LOCK_FILENAME).read_bytes() == lock_bytes


def _window_log(project: Path, run_id: str) -> Path:
    return (project.parent / ".phase3_window_runs" / project.name / run_id
            / "enclosing_phase3.stderr.log")


def _enclose(project: Path):
    return p3._phase3_window_enclosing(
        project, "top", SimpleNamespace(name="gf180mcuD"),
        SimpleNamespace(container="vibeic-eda"), {"23"}, unit="phase3")


def test_enclosing_phase3_runs_on_its_copy_under_the_parents_token(
        project, monkeypatch):
    """The real enclosing unit: clone, then the unbounded phase3 CLI on the
    copy under the watchdog, inheriting the parent's token.  Its stderr is
    kept in the window's own run dir, which is how this is observed."""
    lock_bytes = _write_lock(project, os.getpid())
    before = p3._phase3_file_manifest(project)
    monkeypatch.setenv(_runner_lock.REENTRANCY_ENV,
                       f"{os.getpid()}:{project.resolve()}")
    monkeypatch.setenv("VIBEIC_PHASE3_WINDOW_RUN_ID", "lockrun")
    monkeypatch.setattr(p3, "_WATCHDOG_STALL_GRACE_S", 120)
    row = _enclose(project)
    log = _window_log(project, "lockrun")
    assert log.is_file(), row.detail
    assert str(log) in row.detail
    assert log.read_text().strip(), "the grandchild's stderr was not kept"
    assert "CONCURRENT_RUN_REFUSED" not in log.read_text(), log.read_text()
    assert (project / _runner_lock.LOCK_FILENAME).read_bytes() == lock_bytes
    assert p3._phase3_file_manifest(project) == before


def test_enclosing_phase3_hang_is_stopped_by_the_watchdog(
        project, monkeypatch, tmp_path):
    """A grandchild that makes no forward progress is killed with its whole
    process group and reported NOT_MEASURED (stalled) with its reason."""
    pidfile = tmp_path / "orphan.pid"
    hang = ("import subprocess, sys, time\n"
            "p = subprocess.Popen(['sleep', '90'])\n"
            f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
            "sys.stderr.write('hang-marker\\n'); sys.stderr.flush()\n"
            "time.sleep(60)\n")
    monkeypatch.setattr(p3, "_phase3_enclosing_cmd",
                        lambda *a: [sys.executable, "-c", hang])
    monkeypatch.setattr(p3, "_WATCHDOG_STALL_GRACE_S", 2)
    monkeypatch.setenv("VIBEIC_PHASE3_WINDOW_RUN_ID", "hangrun")
    before = p3._phase3_file_manifest(project)
    row = _enclose(project)
    assert row.status == "NOT_MEASURED", row.detail
    assert row.reason_class == p3._V.ReasonClass.STALLED
    assert "watchdog stopped as hung" in row.detail
    assert "> 2s" in row.detail and "Remedy" in row.detail
    assert "hang-marker" in row.detail
    log = _window_log(project, "hangrun")
    assert str(log) in row.detail and "hang-marker" in log.read_text()
    assert row.duration_s < 50
    orphan = int(pidfile.read_text())
    deadline = time.monotonic() + 10
    while _runner_lock._pid_alive(orphan) and time.monotonic() < deadline:
        time.sleep(0.2)
    alive = _runner_lock._pid_alive(orphan)
    if alive:
        os.kill(orphan, 9)
    assert not alive, "the grandchild's process group outlived the stop"
    assert p3._phase3_file_manifest(project) == before


def test_enclosing_phase3_failure_keeps_and_surfaces_stderr(
        project, monkeypatch, capsys):
    fail = ("import sys\n"
            "for i in range(30): sys.stderr.write(f'line-{i}\\n')\n"
            "sys.exit(7)\n")
    monkeypatch.setattr(p3, "_phase3_enclosing_cmd",
                        lambda *a: [sys.executable, "-c", fail])
    monkeypatch.setenv("VIBEIC_PHASE3_WINDOW_RUN_ID", "failrun")
    row = _enclose(project)
    assert row.status == "NOT_MEASURED", row.detail
    assert "enclosing rc=7" in row.detail
    assert "line-29" in row.detail and "line-5\n" not in row.detail
    log = _window_log(project, "failrun")
    assert log.read_text().splitlines() == [f"line-{i}" for i in range(30)]
    err = capsys.readouterr().err
    assert "ENCLOSING_PHASE3_RC=7" in err and "line-29" in err
    assert str(log) in err
