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
            f"print({BANNER!r})\n"
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
    assert "top=top" in log.with_name("enclosing_phase3.stdout.log").read_text()


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


#: What `main` prints once admission and PDK resolution are done (the run
#: banner), spelled as the runner prints it.
BANNER = "=== phase3_one_shot_runner — pdk=x top=top ==="


def test_the_run_banner_is_the_one_main_prints():
    src = Path(p3.__file__).read_text()
    assert BANNER.startswith(p3._PHASE3_RUN_BANNER)
    assert "_print_run_banner(pdk.name, effective_top, args.top_name)" in src


def _enclose_with(project, monkeypatch, code, run_id, steps=("23",)):
    """A stand-in unit: `code` runs with the private copy as argv[1]."""
    monkeypatch.setattr(p3, "_phase3_enclosing_cmd",
                        lambda iso, *a: [sys.executable, "-c", code, str(iso)])
    monkeypatch.setenv("VIBEIC_PHASE3_WINDOW_RUN_ID", run_id)
    return p3._phase3_window_enclosing(
        project, "top", SimpleNamespace(name="gf180mcuD"),
        SimpleNamespace(container="vibeic-eda"), set(steps), unit="phase3")


def _unit(report=None, rc=1, banner=True, outputs=()):
    """A unit that got past admission (its banner), wrote `outputs` and its
    own report in the copy it was handed, then exited `rc`."""
    rel = str(p3._pl.report_path(Path("ISO"), "phase3_one_shot.json")
              .relative_to("ISO"))
    code = "import json, pathlib, sys\niso = pathlib.Path(sys.argv[1])\n"
    if banner:
        code += f"print({BANNER!r})\n"
    for out in outputs:
        code += (f"q = iso / {out!r}; q.parent.mkdir(parents=True, exist_ok=True);"
                 f" q.write_text('produced')\n")
    if report is not None:
        code += (f"q = iso / {rel!r}; q.parent.mkdir(parents=True, exist_ok=True)\n"
                 f"q.write_text(json.dumps({report!r}))\n")
    return code + f"sys.exit({rc})\n"


def _rows(*rows):
    return {"verdict": "FAIL", "steps": [
        dict(zip(("name", "status", "detail", "reason_class"), r)) for r in rows]}


def test_a_fail_in_the_windows_own_step_is_fail_and_names_it(project, monkeypatch):
    report = _rows(("pnr", "FAIL", "global route diverged", ""),
                   ("drc", "NOT_MEASURED", "upstream", "upstream_failed"))
    row = _enclose_with(project, monkeypatch, _unit(report), "ownfail",
                        steps=("15", "16"))
    assert row.status == "FAIL", row.detail
    assert "pnr (15,15.5ic,16,17,18,19,20,21,22) FAIL: global route diverged" in row.detail
    # The row outside the window is disclosed, not blamed.
    assert "rows of steps outside the window: drc (31) NOT_MEASURED" in row.detail


def test_a_fail_outside_the_window_is_not_the_windows_fail(project, monkeypatch):
    """The unit ran every site. Its PnR FAILed and an unplaceable row
    FAILed, but both rows of the window's own step 31 passed: the window
    PASSes and the other rows are named."""
    outputs = ["reports/phase3/drc_signoff.rpt"]
    report = _rows(("pnr", "FAIL", "x", ""), ("drc", "PASS", "", ""),
                   ("lvs", "PASS", "", ""), ("sta_signoff", "FAIL", "y", ""))
    row = _enclose_with(project, monkeypatch, _unit(report, outputs=outputs),
                        "outfail", steps=("31",))
    assert row.status == "PASS", row.detail
    assert "rows of steps outside the window: pnr (15,15.5ic,16,17,18,19,20,21,22) FAIL" in row.detail
    assert "sta_signoff (23) FAIL" in row.detail


def test_a_fail_that_names_no_step_cannot_decide_an_unplaced_window(
        project, monkeypatch):
    """A window step with no row of its own (39: no Phase-3 row runs it) and
    a failing row mapped to no step: the row may be this window's."""
    report = _rows(("mystery_gate", "FAIL", "y", ""))
    row = _enclose_with(project, monkeypatch, _unit(report), "unplaced",
                        steps=("39",))
    assert row.status == "NOT_MEASURED", row.detail
    assert row.reason_class == p3._V.ReasonClass.INCONCLUSIVE
    assert "steps 39 have no row of their own" in row.detail
    assert ("rows mapped to no step (they may be this window's): "
            "mystery_gate (no step) FAIL") in row.detail


def test_a_sign_off_row_of_the_windows_step_decides_it(project, monkeypatch):
    """Window 22..23 (enclosing): post-route STA is step 23's own gate. Its
    FAIL is the window's FAIL, named, never "no row of their own"."""
    assert p3._phase3_window_sites("22", "23") == ["enclosing_phase3"]
    report = _rows(("pnr", "PASS", "", ""),
                   ("sta_signoff", "FAIL", "WNS -0.42ns", ""))
    row = _enclose_with(project, monkeypatch, _unit(report), "sta23",
                        steps=p3._phase3_window_steps("22", "23"))
    assert row.status == "FAIL", row.detail
    assert "sta_signoff (23) FAIL: WNS -0.42ns" in row.detail


def test_an_out_of_window_drc_fail_does_not_decide_window_9_to_30(
        project, monkeypatch):
    """The front door's `--exit-step 30` window: every row of steps 9..30
    passes and only DRC (31, outside it) fails. The window PASSes."""
    steps = p3._phase3_window_steps("9", "30")
    assert p3._phase3_window_sites("9", "30") == ["enclosing_phase3"]
    names = ["synth", "pad_ring_gen", "pnr", "canonicalize_artefacts",
             "sta_signoff", "sta_corner", "sta_record", "em_signoff",
             "ir_drop_final", "antenna_final", "si_final", "gds", "lvs"]
    report = _rows(*[(n, "PASS", "", "") for n in names],
                   ("drc", "FAIL", "12 violations", ""))
    row = _enclose_with(project, monkeypatch,
                        _unit(report, outputs=["reports/phase3/sta/post_route_summary.json"]),
                        "win930", steps=steps)
    assert row.status == "PASS", row.detail
    assert "rows of steps outside the window: drc (31) FAIL" in row.detail


def _rows_main_emits():
    """Every StepResult row name `phase3_one_shot_runner` spells literally,
    plus its declared gate tables: the rows a report can carry."""
    import ast
    tree = ast.parse(Path(p3.__file__).read_text())
    names = {node.args[0].value for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and getattr(node.func, "id", None) == "StepResult"
             and node.args and isinstance(node.args[0], ast.Constant)
             and isinstance(node.args[0].value, str)}
    return names | {g[0] for g in p3._PHASE3_GATE_TABLES()}


def test_every_phase3_step_is_answered_for_by_a_row_main_emits():
    """One map: dispatch and verdict read the same canonicalizer set, and
    every canonical Phase-3 step 9..38 has a row family that answers for
    it. Step 39 (FPGA sign-off) has none -- no Phase-3 row runs it -- so a
    window over it can only be INCONCLUSIVE, which the test above pins."""
    assert p3._phase3_steps_of_row("canonicalize_artefacts") == \
        p3._PHASE3_CANONICALIZER_IDS
    assert p3._phase3_window_sites("24", "25") == ["enclosing_canonicalize"]
    covered = set().union(*(p3._phase3_steps_of_row(n) for n in _rows_main_emits()))
    ids = p3._phase3_window_steps("9", "39")
    assert [i for i in ids if i not in covered] == ["39"]


def test_the_child_is_asked_the_operators_questions():
    """Not the parent's resolved `custom:<dir>` name (refused by
    `_assert_pdk_name_resolvable`), and the window's own geometry."""
    args = SimpleNamespace(pdk="auto", container="c", die_um="600", util=0.4,
                           spare_density=0.02, ic_name="chip",
                           allow_oss_pdk_fallback=True,
                           allow_pdk_target_mismatch=False)
    cmd = p3._phase3_enclosing_cmd(Path("/x/p"), "top",
                                   SimpleNamespace(name="custom:pdk"), args)
    assert cmd[cmd.index("--pdk") + 1] == "auto" and "custom:pdk" not in cmd
    assert cmd[cmd.index("--die-um") + 1] == "600"
    assert cmd[cmd.index("--util") + 1] == "0.4"
    assert cmd[cmd.index("--spare-density") + 1] == "0.02"
    assert cmd[cmd.index("--ic-name") + 1] == "chip"
    assert "--allow-oss-pdk-fallback" in cmd
    assert "--allow-pdk-target-mismatch" not in cmd


def test_a_window_step_not_measured_keeps_its_own_reason(project, monkeypatch):
    report = _rows(("drc", "NOT_MEASURED", "", "tool_absent"),
                   ("lvs", "PASS", "", ""))
    row = _enclose_with(project, monkeypatch, _unit(report), "ownnm",
                        steps=("31",))
    assert row.status == "NOT_MEASURED", row.detail
    assert row.reason_class == "tool_absent"


def test_a_previous_report_in_the_copy_does_not_speak_for_this_run(
        project, monkeypatch):
    """The copy carries the project's last report (PASS). A unit that ran
    and died without writing one must not be read as that PASS."""
    old = p3._pl.report_path(project, "phase3_one_shot.json")
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text(json.dumps({"verdict": "PASS"}))
    row = _enclose_with(project, monkeypatch, _unit(rc=9), "stale")
    assert row.status == "FAIL", row.detail
    assert "verdict=None" in row.detail


@pytest.mark.parametrize("rc", [0, 1, 2, 3, 4])
def test_a_unit_that_never_started_a_step_is_not_measured_whatever_its_rc(
        project, monkeypatch, rc):
    """No run banner, no report: every refusal before the first step,
    including rc 1 (a PDK ValueError/SystemExit) and rc 0 (the no-PDK
    `[SKIP]`), is a unit that did not run."""
    code = ("import sys; sys.stderr.write('REFUSED: test\\n'); "
            f"sys.exit({rc})")
    row = _enclose_with(project, monkeypatch, code, f"norun{rc}")
    assert row.status == "NOT_MEASURED", row.detail
    assert row.reason_class == p3._V.ReasonClass.INPUT_ABSENT
    assert "stopped before running a step" in row.detail
    assert "REFUSED: test" in row.detail


def test_the_real_pdk_refusal_is_a_unit_that_never_ran(project, monkeypatch):
    """`_detect_pdk`'s own refusal, run for real in the child on a project
    with a staged input/pdk: a named PDK it cannot resolve raises
    ValueError, the child exits 1 with a traceback and no report."""
    (project / "input" / "pdk" / "lib").mkdir(parents=True)
    (project / "input" / "pdk" / "lib" / "cells.lib").write_text("library(x){}\n")
    code = ("import sys, pathlib\n"
            f"sys.path.insert(0, {str(Path(p3.__file__).parent)!r})\n"
            "import phase3_one_shot_runner as p\n"
            "p._detect_pdk(pathlib.Path(sys.argv[1]), 'no_such_pdk_name')\n"
            f"print({BANNER!r})\n")
    row = _enclose_with(project, monkeypatch, code, "pdkrefusal")
    assert row.status == "NOT_MEASURED", row.detail
    assert "enclosing rc=1" in row.detail
    assert "ValueError" in row.detail and "no_such_pdk_name" in row.detail


def test_the_real_runner_refusing_an_unadmitted_copy_stays_not_measured(
        project, monkeypatch):
    """No stand-in: the real phase-3 CLI on an empty project refuses at
    admission, which is a unit that never ran a step."""
    monkeypatch.setattr(p3, "_WATCHDOG_STALL_GRACE_S", 120)
    monkeypatch.setenv("VIBEIC_PHASE3_WINDOW_RUN_ID", "realrefusal")
    monkeypatch.delenv(_runner_lock.REENTRANCY_ENV, raising=False)
    row = _enclose(project)
    assert row.status == "NOT_MEASURED", row.detail
    assert "stopped before running a step" in row.detail


@pytest.mark.parametrize("sig, status", [("SIGTERM", "NOT_MEASURED"),
                                         ("SIGKILL", "NOT_MEASURED"),
                                         ("SIGSEGV", "FAIL")])
def test_a_unit_killed_by_a_signal(project, monkeypatch, sig, status):
    """Stopped from outside (TERM/KILL): an environment stop, NOT_MEASURED
    naming the signal. A crash of the unit's own (SEGV) after it ran: FAIL."""
    # The banner is printed by main's own `_print_run_banner` -- NOT flushed
    # here -- so a crash right after it keeps the banner only if main does.
    code = ("import os, signal, sys\n"
            f"sys.path.insert(0, {str(Path(p3.__file__).parent)!r})\n"
            "import phase3_one_shot_runner as p\n"
            "p._print_run_banner('x', 'top', 'top')\n"
            f"os.kill(os.getpid(), signal.{sig})\n")
    row = _enclose_with(project, monkeypatch, code, f"sig{sig}")
    assert row.status == status, row.detail
    assert sig in row.detail
    if status == "NOT_MEASURED":
        assert row.reason_class == p3._V.ReasonClass.EXECUTION_ERROR


def test_a_stall_inside_the_unit_is_stalled_not_a_refusal(project, monkeypatch):
    """rc 2 is also RC_UNDETERMINED: the real `exit_undetermined_on_stall`
    around a step that stalled after the unit started."""
    code = ("import sys\n"
            f"sys.path.insert(0, {str(Path(p3.__file__).parent)!r})\n"
            "import _progress_run as pr\n"
            "def main():\n"
            f"    print({BANNER!r})\n"
            "    raise pr.Stalled(['tool'], 3, 1.0, 3.0, {'cpu': True})\n"
            "sys.exit(pr.exit_undetermined_on_stall(main))\n")
    row = _enclose_with(project, monkeypatch, code, "undetermined")
    assert row.status == "NOT_MEASURED", row.detail
    assert row.reason_class == p3._V.ReasonClass.STALLED
    assert "enclosing rc=2" in row.detail and "stalled" in row.detail


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
