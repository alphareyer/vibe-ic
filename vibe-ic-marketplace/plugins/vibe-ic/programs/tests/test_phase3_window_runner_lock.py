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


def _running(pid: int) -> bool:
    """A process that still runs. `kill(pid, 0)` (`_runner_lock._pid_alive`)
    also succeeds on a zombie -- a child the group kill took down that no
    one has reaped yet, as under a subreaper that reaps only at the end."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat.rsplit(")", 1)[-1].split()[0] not in ("Z", "X", "x")


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


def _assert_refusal_discloses(cp, project: Path, holder: int, why: str):
    assert cp.returncode == 3
    line = [l for l in cp.stderr.splitlines()
            if l.startswith("CONCURRENT_RUN_REFUSED")]
    assert line, cp.stderr
    line = line[0]
    assert f"pid={holder}" in line
    assert "runner=vibe_ic_one_shot_runner" in line and "since=x" in line
    assert str(project / _runner_lock.LOCK_FILENAME) in line
    assert why in line, line
    assert _runner_lock.REENTRANCY_ENV in line.split(why, 1)[1]


def test_window_without_token_still_refuses_live_lock(project):
    lock_bytes = _write_lock(project, os.getpid())
    cp = _run_window(project, None)
    _assert_refusal_discloses(cp, project, os.getpid(), "no "
                              + _runner_lock.REENTRANCY_ENV + " token")
    assert (project / _runner_lock.LOCK_FILENAME).read_bytes() == lock_bytes


def test_window_token_for_other_project_still_refuses(project, tmp_path):
    _write_lock(project, os.getpid())
    other = (tmp_path / 'other').resolve()
    cp = _run_window(project, f"{os.getpid()}:{other}")
    _assert_refusal_discloses(cp, project, os.getpid(),
                              f"names project {other}")


def test_window_token_not_naming_the_live_holder_still_refuses(project,
                                                                stranger):
    _write_lock(project, stranger)
    cp = _run_window(project, f"{os.getpid()}:{project.resolve()}")
    _assert_refusal_discloses(
        cp, project, stranger,
        f"names pid {os.getpid()}, but the lock is held by pid {stranger}")


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
    while _running(orphan) and time.monotonic() < deadline:
        time.sleep(0.2)
    alive = _running(orphan)
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
    # It RAN and failed: a plain FAIL (owner outcome-state ruling), never a
    # NOT_MEASURED about something that could not be run.
    assert row.status == "FAIL", row.detail
    assert not row.reason_class
    assert "enclosing rc=7" in row.detail
    assert "ran and exited rc=7 with no verdict" in row.detail
    assert "line-29" in row.detail and "line-5\n" not in row.detail
    log = _window_log(project, "failrun")
    assert log.read_text().splitlines() == [f"line-{i}" for i in range(30)]
    err = capsys.readouterr().err
    assert "ENCLOSING_PHASE3_RC=7" in err and "line-29" in err
    assert str(log) in err


def test_a_zombie_is_not_a_surviving_orphan():
    """The liveness read the hang test relies on: an exited, unreaped child
    is a zombie, which `kill(pid, 0)` still reports alive."""
    child = subprocess.Popen(["true"])
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            state = Path(f"/proc/{child.pid}/stat").read_text()
        except OSError:
            break
        if state.rsplit(")", 1)[-1].split()[0] == "Z":
            break
        time.sleep(0.05)
    try:
        assert _runner_lock._pid_alive(child.pid)
        assert not _running(child.pid)
    finally:
        child.wait(timeout=10)
    assert not _running(child.pid)
    sleeper = subprocess.Popen(["sleep", "30"])
    try:
        assert _running(sleeper.pid)
    finally:
        sleeper.kill()
        sleeper.wait(timeout=10)


def _enclose_with(project, monkeypatch, code, run_id):
    monkeypatch.setattr(p3, "_phase3_enclosing_cmd",
                        lambda *a: [sys.executable, "-c", code])
    monkeypatch.setenv("VIBEIC_PHASE3_WINDOW_RUN_ID", run_id)
    return _enclose(project)


def test_an_enclosing_unit_whose_own_verdict_is_fail_is_fail(project, monkeypatch):
    report = str(p3._pl.report_path(Path("ISOLATED"), "phase3_one_shot.json"))
    # The unit writes its own report in the copy it was handed (argv[1]).
    code = ("import json, pathlib, sys\n"
            f"rel = pathlib.Path({report!r}).relative_to('ISOLATED')\n"
            "p = pathlib.Path(sys.argv[1]) / rel\n"
            "p.parent.mkdir(parents=True, exist_ok=True)\n"
            "p.write_text(json.dumps({'verdict': 'FAIL'}))\n"
            "sys.exit(1)\n")
    monkeypatch.setattr(p3, "_phase3_enclosing_cmd",
                        lambda iso, *a: [sys.executable, "-c", code, str(iso)])
    monkeypatch.setenv("VIBEIC_PHASE3_WINDOW_RUN_ID", "verdictfail")
    row = _enclose(project)
    assert row.status == "FAIL", row.detail
    assert "verdict is FAIL" in row.detail


def test_a_previous_report_in_the_copy_does_not_speak_for_this_run(
        project, monkeypatch):
    """The copy carries the project's last report (PASS). A unit that dies
    without writing one must not be read as that PASS."""
    old = p3._pl.report_path(project, "phase3_one_shot.json")
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text(json.dumps({"verdict": "PASS"}))
    row = _enclose_with(project, monkeypatch, "import sys; sys.exit(9)", "stale")
    assert row.status == "FAIL", row.detail
    assert "verdict=None" in row.detail


@pytest.mark.parametrize("rc", [2, 3, 4])  # main()'s no-report refusals
def test_a_unit_refused_before_it_ran_stays_not_measured(project, monkeypatch, rc):
    row = _enclose_with(
        project, monkeypatch,
        f"import sys; sys.stderr.write('REFUSED: test\\n'); sys.exit({rc})",
        f"refused{rc}")
    assert row.status == "NOT_MEASURED", row.detail
    assert row.reason_class == p3._V.ReasonClass.INPUT_ABSENT
    assert "refused before running a step" in row.detail
    assert "REFUSED: test" in row.detail


def test_the_real_runner_refusing_an_unadmitted_copy_stays_not_measured(
        project, monkeypatch):
    """No stand-in: the real phase-3 CLI on an empty project refuses at
    admission, which is a unit that never ran a step."""
    monkeypatch.setattr(p3, "_WATCHDOG_STALL_GRACE_S", 120)
    monkeypatch.setenv("VIBEIC_PHASE3_WINDOW_RUN_ID", "realrefusal")
    monkeypatch.delenv(_runner_lock.REENTRANCY_ENV, raising=False)
    row = _enclose(project)
    assert row.status == "NOT_MEASURED", row.detail
    assert "refused before running a step" in row.detail


def test_a_unit_that_cannot_be_spawned_stays_not_measured(project, monkeypatch):
    monkeypatch.setattr(p3, "_phase3_enclosing_cmd",
                        lambda *a: [str(project / "no-such-interpreter")])
    monkeypatch.setenv("VIBEIC_PHASE3_WINDOW_RUN_ID", "nospawn")
    row = _enclose(project)
    assert row.status == "NOT_MEASURED", row.detail
    assert row.reason_class == p3._V.ReasonClass.EXECUTION_ERROR
    assert "could not be spawned" in row.detail


@pytest.mark.parametrize("status, expected", [("FAIL", "FAIL"),
                                              ("NOT_MEASURED", "NOT_MEASURED")])
def test_an_in_process_unit_keeps_what_it_found(project, monkeypatch, status,
                                                expected):
    monkeypatch.setattr(p3, "step_canonicalize_artefacts", lambda *a, **k:
                        p3.StepResult("canonicalize", status, 0.0, "x",
                                      reason_class=("" if status == "FAIL" else
                                                    p3._V.ReasonClass.TOOL_ABSENT)))
    row = p3._phase3_window_enclosing(
        project, "top", SimpleNamespace(name="gf180mcuD"),
        SimpleNamespace(container="vibeic-eda"), {"24"}, unit="canonicalize")
    assert row.status == expected, row.detail
    if expected == "NOT_MEASURED":
        assert row.reason_class == p3._V.ReasonClass.TOOL_ABSENT
