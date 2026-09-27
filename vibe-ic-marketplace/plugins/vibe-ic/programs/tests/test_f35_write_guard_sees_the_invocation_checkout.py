"""F35 — a write into the directory pytest was INVOKED from must not read clean.

WHAT WAS MEASURED
=================
In the vibeic-eda image (uid 1000, account-home bind) the ATPG engine's LOCAL
route inherited the caller's cwd, and the engine writes relative to `.`: its PLY
parser leaves `parser.out` + `parsetab.py`, its simulator a `thr0x…/tb.sv`. Run
from the plugin root of the tree under test, `suite_write_guard` went RED on
all three. Run from ANOTHER checkout's plugin root against the same tests, the
three files landed in that checkout and the guard printed
`[PASS] … wrote nothing` with the session at rc 0 — because it measured only
the tree that holds its own file, and the cwd was a different tree.

Two fixes, each with its own control here:

  * the guard also measures the invocation checkout when it is another
    checkout of the SAME tree (`_cwd_sibling_root`) — and still does not
    measure an unrelated ambient repository (#1412's side of the line);
  * the local ATPG routes run the engine in a throwaway cwd, as the container
    route always did (`_container_exec.local_engine_cwd`).

The positive sample is the REAL one: the paths and the bytes the engine wrote,
from `fixtures/f35_in_image_cwd_write_sample.json` (see its `_provenance`).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
_PROGRAMS = _TESTS.parent
sys.path.insert(0, str(_PROGRAMS))
sys.path.insert(0, str(_TESTS))

import _container_exec as CE  # noqa: E402
import _progress_run as _pr  # noqa: E402
import fault_atpg_run as F  # noqa: E402
import transition_fault_atpg_run as T  # noqa: E402
from test_suite_write_guard import (  # noqa: E402
    _ambient_repo, _child_env, _detached_copy_of_the_guard)

_SAMPLE = json.loads(
    (_TESTS / "fixtures" / "f35_in_image_cwd_write_sample.json").read_text())
_FILES = _SAMPLE["files"]


def _git(*a):
    subprocess.run(["git", *a], check=True, capture_output=True)


def _checkout_pair(tmp_path: Path):
    """Checkout A (holds the guard, tracked) and B, a clone of A."""
    a = tmp_path / "a"
    _detached_copy_of_the_guard(a)
    (a / "pkg").mkdir()
    (a / "pkg" / "shipped.txt").write_text("published bytes\n")
    _git("init", "-q", str(a))
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        _git("-C", str(a), "config", k, v)
    _git("-C", str(a), "add", "-A")
    _git("-C", str(a), "commit", "-qm", "base")
    b = tmp_path / "b"
    _git("clone", "-q", str(a), str(b))
    return a, b


def _planted(tmp_path: Path, into_cwd: bool) -> Path:
    """A passing test that writes the REAL sample — into `.` like the engine,
    or into its own tmp dir like a well-behaved test."""
    tf = tmp_path / "suite" / "test_engine_like_writer.py"
    tf.parent.mkdir()
    tf.write_text(
        "import json, os, tempfile\n"
        "from pathlib import Path\n"
        f"FILES = json.loads({json.dumps(json.dumps(_FILES))})\n"
        "def test_writes_like_the_atpg_engine():\n"
        + ("    root = Path('.')\n" if into_cwd else
           "    root = Path(tempfile.mkdtemp())\n")
        + "    for rel, body in FILES.items():\n"
        "        p = root / rel\n"
        "        p.parent.mkdir(parents=True, exist_ok=True)\n"
        "        p.write_text(body)\n")
    return tf


def _session(a: Path, cwd: Path, tf: Path):
    env = _child_env()
    env["PYTHONPATH"] = str(a / "programs")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    p = _pr.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                 "-p", "suite_write_guard", str(tf)],
                capture_output=True, text=True, cwd=str(cwd), env=env)
    return p, p.stdout + p.stderr


def test_the_real_sample_written_into_another_checkout_reddens(tmp_path):
    """THE POSITIVE. Unfixed: `[PASS]`, rc 0, three files sitting in B."""
    a, b = _checkout_pair(tmp_path)
    p, out = _session(a, b, _planted(tmp_path, into_cwd=True))
    assert "1 passed" in out, out[-2000:]
    # the setup is live: the sample really landed in B, and not in A
    for rel in _FILES:
        assert (b / rel).is_file(), rel
        assert not (a / rel).exists(), rel
    assert p.returncode == 1, (
        "the engine's real writes landed in the invocation checkout and the "
        "guard let the session through:\n" + out[-2000:])
    assert "WROTE INTO THE TREE" in out, out[-2000:]
    for rel in _FILES:
        assert f"{b}/{rel}" in out, (rel, out[-2000:])


def test_the_same_writer_into_its_tmp_dir_stays_green(tmp_path):
    """PAIRED. Measuring B must not redden a session that wrote nothing there."""
    a, b = _checkout_pair(tmp_path)
    p, out = _session(a, b, _planted(tmp_path, into_cwd=False))
    assert p.returncode == 0, out[-2000:]
    assert "[PASS] suite_write_guard" in out, out[-2000:]
    assert "WRITE_GUARD_NOT_CHECKED" not in out, out[-2000:]


def test_an_unrelated_ambient_cwd_is_still_not_the_subject(tmp_path):
    """PAIRED, the #1412 side. A cwd inside a repository that is NOT another
    checkout of this tree is not measured, so its state cannot block."""
    a, _b = _checkout_pair(tmp_path)
    ambient = _ambient_repo(tmp_path)
    p, out = _session(a, ambient, _planted(tmp_path, into_cwd=True))
    assert (ambient / "parser.out").is_file()  # the write did happen there
    assert p.returncode == 0, out[-2000:]
    assert "[PASS] suite_write_guard" in out, out[-2000:]


# ── the writer: the local ATPG routes run the engine in a throwaway cwd ────

_ENGINE_LIKE = ("printf x > parser.out && printf x > parsetab.py && "
                "mkdir -p thr0x0000782ab8001030 && "
                "printf x > thr0x0000782ab8001030/tb.sv && echo ENGINE_RAN")


@pytest.fixture
def local_route(monkeypatch, tmp_path):
    real = CE.shutil.which
    monkeypatch.setattr(CE.shutil, "which",
                        lambda n, *a, **k: None if n == "docker"
                        else real(n, *a, **k))
    invoker = tmp_path / "invoker"
    invoker.mkdir()
    monkeypatch.chdir(invoker)
    project = tmp_path / "project"
    project.mkdir()
    return invoker, project


@pytest.mark.parametrize("supervised", [False, True])
def test_stuck_at_local_route_does_not_write_into_the_callers_cwd(
        local_route, supervised):
    invoker, project = local_route
    rc, out, err = F._run_docker(project, [_ENGINE_LIKE], supervised=supervised)
    assert rc == 0 and "ENGINE_RAN" in out, (rc, out, err)
    assert sorted(p.name for p in invoker.iterdir()) == [], (
        "the engine wrote into the caller's cwd")
    assert sorted(p.name for p in project.iterdir()) == []


def test_at_speed_local_route_does_not_write_into_the_callers_cwd(local_route):
    invoker, project = local_route
    rc, out, err = T._run_in_docker(project, _ENGINE_LIKE, timeout=60)
    assert rc == 0 and "ENGINE_RAN" in out, (rc, out, err)
    assert sorted(p.name for p in invoker.iterdir()) == [], (
        "the engine wrote into the caller's cwd")
    assert sorted(p.name for p in project.iterdir()) == []
