"""The test write guard sees a transient write before it is removed."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import _programs_tree_write_guard as guard
import pytest


def test_a_transient_programs_write_is_seen_before_unlink(tmp_path, monkeypatch):
    # Give the audit hook a scratch stand-in for the repository's programs dir;
    # the real tree must never be written even by this guard's own test.
    programs = tmp_path / "programs"
    programs.mkdir()
    monkeypatch.setattr(guard, "_PROGRAMS", str(programs))
    assert guard._CURRENT is not None
    baseline = list(guard._CURRENT)
    victim = programs / "brand_new_gate.py"
    victim.write_text("print('temporary')\n")
    victim.unlink()
    try:
        assert any("brand_new_gate.py" in hit for hit in guard._CURRENT)
    finally:
        # The protocol guard must judge only writes into the actual tree.
        guard._CURRENT[:] = baseline


def test_a_scratch_write_does_not_count_as_a_programs_write(tmp_path):
    assert guard._CURRENT is not None
    before = list(guard._CURRENT)
    (tmp_path / "scratch_gate.py").write_text("print('scratch')\n")
    assert guard._CURRENT == before


def test_mkdir_of_an_existing_fixture_does_not_report_a_write(
        tmp_path, monkeypatch):
    programs = tmp_path / "programs"
    programs.mkdir()
    monkeypatch.setattr(guard, "_PROGRAMS", str(programs))
    assert guard._CURRENT is not None
    before = list(guard._CURRENT)
    programs.mkdir(parents=True, exist_ok=True)
    assert guard._CURRENT == before


def _collect_probe(tmp_path, *, write_into_programs):
    """Run the shipped guard as a pytest plugin against a private programs/."""
    tests = tmp_path / "programs" / "tests"
    tests.mkdir(parents=True)
    shutil.copyfile(Path(guard.__file__), tests / "_programs_tree_write_guard.py")
    (tests / "conftest.py").write_text(
        "pytest_plugins = ['_programs_tree_write_guard']\n")
    victim_rel = "transient.py" if write_into_programs else "../scratch.txt"
    body = (
        "from pathlib import Path\n"
        "root = Path(__file__).resolve().parents[1]\n"
        f"victim = root / {victim_rel!r}\n"
        "victim.write_text('transient\\n')\n"
        "victim.unlink()\n"
        "def test_benign(): assert True\n"
    )
    (tests / "test_collection_probe.py").write_text(body)
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONPATH"] = str(tests)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-p", "no:cacheprovider", str(tests / "test_collection_probe.py")],
        cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30)


def test_collect_only_refuses_an_undone_programs_write(tmp_path):
    result = _collect_probe(tmp_path, write_into_programs=True)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAIL: collection wrote into" in result.stdout + result.stderr
    assert "transient.py" in result.stdout + result.stderr


def test_collect_only_accepts_a_scratch_write(tmp_path):
    result = _collect_probe(tmp_path, write_into_programs=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 test collected" in result.stdout + result.stderr


@pytest.mark.parametrize("phase", ["setup", "teardown"])
def test_fixture_transient_write_refuses_the_session(tmp_path, phase):
    tests = tmp_path / "programs" / "tests"
    tests.mkdir(parents=True)
    shutil.copyfile(Path(guard.__file__), tests / "_programs_tree_write_guard.py")
    (tests / "conftest.py").write_text(
        "pytest_plugins = ['_programs_tree_write_guard']\n")
    write = (
        "    victim = Path(__file__).resolve().parents[1] / 'transient.py'\n"
        "    victim.write_text('temporary\\n')\n"
        "    victim.unlink()\n")
    (tests / "test_phase_probe.py").write_text(
        "import pytest\n"
        "from pathlib import Path\n"
        "@pytest.fixture(autouse=True)\n"
        "def transient():\n"
        + (write + "    yield\n" if phase == "setup" else "    yield\n" + write)
        + "def test_clean(): assert True\n")
    env = os.environ.copy()
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
               PYTHONPATH=str(tests))
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         str(tests / "test_phase_probe.py")],
        cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "transient.py" in result.stdout + result.stderr
    assert phase in (result.stdout + result.stderr).lower()
