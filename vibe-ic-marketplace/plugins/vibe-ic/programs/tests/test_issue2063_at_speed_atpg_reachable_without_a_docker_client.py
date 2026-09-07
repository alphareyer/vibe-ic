"""The AT-SPEED ATPG engine must be reachable when there is no docker client.

WHY THIS FILE EXISTS (vibe-ic#2063 RB2-07).  `transition_fault_atpg_run.
_run_in_docker` is the THIRD `docker run` surface in this repo, and it is the
ONLY route the at-speed producers have: DT1 calls it six times, and
`path_delay_fault_atpg_run` (DT2) calls it twice through `_tdf._run_in_docker`.
When the flow runs INSIDE the EDA image there is no docker client on PATH, so
every one of those calls returned

    127, "", "docker binary not found in PATH"

and NOTHING at-speed was graded — while `fault_atpg_run._run_docker`, the
stuck-at producer beside it, had been taught the LOCAL route on the same day
(2026-09-06) and routed on this filesystem instead.  Two producers of the same
family disagreeing about whether a step can run at all is the defect; the
coverage numbers downstream of it are the symptom.

THE IMAGE IT WOULD START IS THE IMAGE IT IS ALREADY IN, so the local route is
the same build, not a substitution — the identical argument the stuck-at
producer's own local branch records.

MUTATIONS THESE TESTS MUST KILL:
  * Deleting the `if _CE.no_container_route():` branch of `_run_in_docker`
    fails `test_the_local_route_is_taken_when_there_is_no_client` and
    `test_the_engine_actually_runs_on_this_filesystem`.
  * Deleting the CONTAINER branch fails
    `test_the_container_route_is_unchanged_when_a_client_exists`.
  * Dropping the path rewrite (running the /work-absolute command as written)
    fails `test_the_mounted_paths_are_rewritten_to_this_filesystem`.
  * Forgetting `extra_mounts` in the rewrite (the liberty mount the at-speed
    producer adds and the stuck-at producer never had) fails
    `test_the_private_liberty_mount_is_rewritten_too`.
  * Widening the mount-prefix match to a bare `str.replace` fails
    `test_a_token_that_merely_contains_a_mount_name_is_untouched`.
  * A second copy of the route predicate in this producer fails
    `test_one_definition_of_the_route_question_covers_the_at_speed_producer`.
  * A second copy of the path rewrite fails
    `test_one_definition_of_the_path_rewrite`.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import _container_exec as CE            # noqa: E402
import _watchdog as WD                  # noqa: E402
import fault_atpg_run as F              # noqa: E402
import transition_fault_atpg_run as T   # noqa: E402


class _Recorder:
    """Capture the argv the supervisor was asked to launch."""

    def __init__(self, rc=0, out="", err=""):
        self.argv = None
        self._r = (rc, out, err)

    def __call__(self, cmd, **kw):
        self.argv = list(cmd)
        rc, out, err = self._r
        return WD.SupervisedResult(rc=rc, out=out, err=err,
                                   outcome="natural")


@pytest.fixture
def route(monkeypatch):
    """Drive the ONE predicate, both ways, through `shutil.which`.

    Not by monkeypatching `no_container_route` itself: that would assert the
    branch given the answer, never that the real predicate produces it."""
    def _set(docker_path):
        real = CE.shutil.which
        monkeypatch.setattr(
            CE.shutil, "which",
            lambda n, *a, **k: (docker_path if n == "docker"
                                else real(n, *a, **k)))
        monkeypatch.setattr(T, "_LOCAL_TDF_ROUTE_ANNOUNCED", False,
                            raising=False)
    yield _set
    T._LOCAL_TDF_ROUTE_ANNOUNCED = False


@pytest.fixture
def supervised(monkeypatch):
    """Replace the watchdog so nothing is really executed."""
    rec = _Recorder()
    monkeypatch.setattr(T._wd, "run_host_supervised", rec)
    return rec


# ── the route itself ──────────────────────────────────────────────────────

def test_the_container_route_is_unchanged_when_a_client_exists(
        route, supervised, tmp_path):
    """THE HOST-SIDE CONTROL: with a client, this is what it always was."""
    route("/usr/bin/docker")
    T._run_in_docker(tmp_path, "yosys /work/batch.ys", timeout=60)
    argv = supervised.argv
    assert argv[:3] == ["docker", "run", "--rm"], argv
    assert f"{tmp_path}:/work" in argv, argv
    assert F.DOCKER_IMAGE in argv, argv
    # the command still speaks the CONTAINER's paths, untouched
    assert "/work/batch.ys" in argv[-1], argv[-1]
    assert str(tmp_path) not in argv[-1], argv[-1]


def test_the_local_route_is_taken_when_there_is_no_client(
        route, supervised, tmp_path):
    route(None)
    T._run_in_docker(tmp_path, "yosys /work/batch.ys", timeout=60)
    argv = supervised.argv
    assert argv[0] == "bash", argv
    assert "docker" not in argv, argv
    assert F.DOCKER_IMAGE not in argv, argv


def test_the_engine_actually_runs_on_this_filesystem(route, tmp_path):
    """THE REPRODUCTION, end to end and unmocked.

    Before this fix the same call returned 127 with "docker binary not found
    in PATH" and produced nothing. The watchdog is NOT replaced here: the
    command really executes, so a branch that merely builds a nicer argv and
    still hands it to a missing docker cannot pass.

    IT ALSO HAS TO FAIL ON A HOST THAT DOES HAVE DOCKER, or it is a test of
    this machine rather than of the branch — the first version of it passed on
    the pre-fix tree for exactly that reason, because `docker run -v
    project:/work` genuinely wrote the marker through the mount. So the
    command first reads a probe file by its HOST-ABSOLUTE path. That path is
    mounted nowhere in the fresh sibling container (`-v {project}:/work` is
    the only bind), so on the container route the `cat` fails, the `&&` stops,
    and no marker is written."""
    route(None)
    probe = tmp_path / "host_only.txt"
    probe.write_text("SEEN_BY_A_LOCAL_RUN\n")
    marker = tmp_path / "engine_ran.txt"
    rc, out, err = T._run_in_docker(
        tmp_path, f"cat {probe} && echo AT_SPEED_OK > /work/{marker.name}",
        timeout=120)
    assert rc == 0, (rc, out, err)
    assert "SEEN_BY_A_LOCAL_RUN" in out, (out, err)
    assert marker.is_file(), (rc, out, err)
    assert marker.read_text().strip() == "AT_SPEED_OK"


def test_without_a_client_the_container_route_is_the_127_it_used_to_be(
        route, monkeypatch, tmp_path):
    """THE NEGATIVE CONTROL FOR THE FIX ITSELF.

    Forcing the CONTAINER branch on a machine with no docker client
    reproduces the exact failure this fix removes — so the test file cannot
    pass by accident on a host that happens to have docker."""
    route("/usr/bin/docker")            # pretend a client exists...
    monkeypatch.setattr(T._wd, "run_host_supervised",
                        lambda cmd, **kw: WD.SupervisedResult(
                            rc=127, out="", err="", outcome="launch_error"))
    rc, out, err = T._run_in_docker(tmp_path, "yosys /work/batch.ys",
                                    timeout=60)
    assert rc == 127
    assert "docker binary not found in PATH" in err


# ── the path rewrite ──────────────────────────────────────────────────────

def test_the_mounted_paths_are_rewritten_to_this_filesystem(
        route, supervised, tmp_path):
    route(None)
    # deliberately NOT named "pdk", so a real rewrite is visible without the
    # destination spelling the mount point back again.
    pdk = tmp_path / "foundry_kit"
    pdk.mkdir()
    T._run_in_docker(tmp_path,
                     "yosys -p 'read /work/net.v' && cat /pdk/cells.lib",
                     timeout=60, pdk_dir=pdk)
    shell = supervised.argv[-1]
    assert f"{tmp_path}/net.v" in shell, shell
    assert f"{pdk}/cells.lib" in shell, shell
    assert " /work/" not in shell, shell
    assert " /pdk/" not in shell, shell


def test_the_private_liberty_mount_is_rewritten_too(
        route, supervised, tmp_path):
    """`extra_mounts` is the mount the STUCK-AT producer never had.

    A rewrite copied from it would handle /work and /pdk and silently leave
    the at-speed producer's liberty mount pointing at a directory that does
    not exist on this filesystem."""
    route(None)
    libs = tmp_path / "outside_libs"
    libs.mkdir()
    T._run_in_docker(tmp_path, "grep -c cell /libmnt/typ.lib",
                     timeout=60,
                     extra_mounts=[(str(libs), "/libmnt")])
    shell = supervised.argv[-1]
    assert f"{libs}/typ.lib" in shell, shell
    assert "/libmnt/" not in shell, shell


def test_a_token_that_merely_contains_a_mount_name_is_untouched():
    """A bare `replace` would corrupt a design called `network`."""
    out = CE.localise_mounted_paths(
        "echo network /opt/workspace /workflow /work/real",
        [("/work", "/p/proj")])
    assert "network" in out
    assert "/opt/workspace" in out
    assert "/workflow" in out
    assert "/p/proj/real" in out


def test_a_longer_mount_wins_over_a_shorter_prefix():
    out = CE.localise_mounted_paths(
        "/work/a /workdir/b",
        [("/work", "/P"), ("/workdir", "/Q")])
    assert "/P/a" in out, out
    assert "/Q/b" in out, out


def test_the_container_route_leaves_the_command_alone(route, supervised,
                                                      tmp_path):
    """The rewrite must fire on ONE route only."""
    route("/usr/bin/docker")
    libs = tmp_path / "outside_libs"
    libs.mkdir()
    T._run_in_docker(tmp_path, "grep -c cell /libmnt/typ.lib", timeout=60,
                     extra_mounts=[(str(libs), "/libmnt")])
    shell = supervised.argv[-1]
    assert "/libmnt/typ.lib" in shell, shell
    assert str(libs) not in shell, shell


# ── the announcement ──────────────────────────────────────────────────────

def test_the_route_is_announced_once(route, supervised, tmp_path, capsys):
    route(None)
    T._run_in_docker(tmp_path, "yosys -V", timeout=60)
    err = capsys.readouterr().err
    assert "EXEC ROUTE = LOCAL" in err, err
    T._run_in_docker(tmp_path, "yosys -V", timeout=60)
    assert "EXEC ROUTE = LOCAL" not in capsys.readouterr().err


def test_the_container_route_announces_nothing(route, supervised, tmp_path,
                                               capsys):
    route("/usr/bin/docker")
    T._run_in_docker(tmp_path, "yosys -V", timeout=60)
    assert "EXEC ROUTE" not in capsys.readouterr().err


def test_the_announcement_does_not_name_the_resolved_image():
    """In-image `DOCKER_IMAGE` is a registry FALLBACK; printing it would put
    an image that was never started into the transcript."""
    tree = ast.parse((PROGRAMS / "transition_fault_atpg_run.py").read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef)
              and n.name == "_announce_local_tdf_route")
    stmts = list(fn.body)
    if ast.get_docstring(fn) is not None:
        stmts = stmts[1:]          # the docstring EXPLAINS the rule; it is
                                   # not code that could print an image
    body = ast.dump(ast.Module(body=stmts, type_ignores=[]))
    assert "DOCKER_IMAGE" not in body, body


# ── one definition, not two ───────────────────────────────────────────────

def test_one_definition_of_the_route_question_covers_the_at_speed_producer():
    src = (PROGRAMS / "transition_fault_atpg_run.py").read_text()
    assert 'which("docker")' not in src, (
        "transition_fault_atpg_run defines its own route predicate; the ONE "
        "definition is _container_exec.no_container_route")
    assert "no_container_route()" in src


def test_one_definition_of_the_path_rewrite():
    """Both `docker run` producers must go through the shared rewrite."""
    shared = (PROGRAMS / "_container_exec.py").read_text()
    assert shared.count("def localise_mounted_paths") == 1
    for name in ("fault_atpg_run.py", "transition_fault_atpg_run.py"):
        src = (PROGRAMS / name).read_text()
        assert "localise_mounted_paths" in src, name
        # nobody re-implements the anchored substitution
        assert r'(?=/|\b)' not in src, (
            f"{name} carries its own copy of the mount-prefix substitution")


def test_the_stuck_at_producer_still_rewrites_the_same_way():
    """Delegating must not have changed the stuck-at producer's answer."""
    out = F._localise_mounted_paths("--lib /pdk/x.lib /work/net.v",
                                    Path("/p/proj"), Path("/k"))
    assert out == "--lib /k/x.lib /p/proj/net.v", out
    assert F._localise_mounted_paths("--lib /pdk/x.lib", Path("/p/proj"),
                                     None) == "--lib /pdk/x.lib"
