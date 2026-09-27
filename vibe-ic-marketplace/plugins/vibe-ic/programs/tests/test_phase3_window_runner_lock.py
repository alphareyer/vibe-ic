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


def test_enclosing_phase3_runs_on_its_copy_under_the_parents_token(
        project, monkeypatch):
    """The real enclosing unit: clone, then the unbounded phase3 CLI on the
    copy, inheriting the parent's token.  Only the child's stderr is
    observed (it is captured by the unit) and a deadline is added."""
    lock_bytes = _write_lock(project, os.getpid())
    monkeypatch.setenv(_runner_lock.REENTRANCY_ENV,
                       f"{os.getpid()}:{project.resolve()}")
    seen = []
    real_run = subprocess.run

    def observed(cmd, *a, **kw):
        if cmd and cmd[0] == sys.executable:
            kw.setdefault("timeout", DEADLINE_S)
            cp = real_run(cmd, *a, **kw)
            seen.append(cp)
            return cp
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(p3.subprocess, "run", observed)
    row = p3._phase3_window_enclosing(
        project, "top", SimpleNamespace(name="gf180mcuD"),
        SimpleNamespace(container="vibeic-eda"), {"23"}, unit="phase3")
    assert len(seen) == 1, row.detail
    assert "CONCURRENT_RUN_REFUSED" not in seen[0].stderr, seen[0].stderr
    assert (project / _runner_lock.LOCK_FILENAME).read_bytes() == lock_bytes
