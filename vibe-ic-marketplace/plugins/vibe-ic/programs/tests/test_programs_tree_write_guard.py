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


def _real_link_probe(tmp_path, *, cwd_inside, destination_inside, mode):
    """Exercise the shipped hook in a fresh process, using real directory FDs."""
    import json
    probe = r'''
import importlib.util
import json
import os
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("neutral_guard", sys.argv[1])
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
root = Path(sys.argv[2])
source, programs, outside = [root / name for name in ("source", "programs", "outside")]
for path in (source, programs, outside):
    path.mkdir()
seed = source / "seed.bin"
seed.write_bytes(b"neutral hard-link payload\n")
cwd = programs if sys.argv[3] == "1" else outside
destination = programs if sys.argv[4] == "1" else outside
mode = sys.argv[5]
os.chdir(cwd)
src_fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY)
fd_directory = (outside if destination == programs else programs) if mode == "absolute" else destination
dst_fd = os.open(fd_directory, os.O_RDONLY | os.O_DIRECTORY)
descriptor_directories = [os.readlink(f"/proc/self/fd/{fd}") for fd in (src_fd, dst_fd)]
guard._PROGRAMS = str(programs)
guard._CURRENT = []
events = []
def capture(event, args):
    if event == "os.link":
        events.append(list(args))
sys.addaudithook(capture)
guard._install()
basename = "linked.bin"
target = str(destination / basename) if mode == "absolute" else basename
kwargs = {"src_dir_fd": src_fd}
if mode != "cwd":
    kwargs["dst_dir_fd"] = dst_fd
os.link("seed.bin", target, **kwargs)
actual = (cwd if mode == "cwd" else destination) / basename
stat_source, stat_destination = seed.stat(), actual.stat()
values = {
    "scope": "Real kernel os.link and shipped Python audit hook; neutral shadow tree only",
    "mode": mode, "cwd_inside": cwd == programs,
    "destination_inside": actual.parent == programs,
    "audit_events": events, "descriptor_directories": descriptor_directories,
    "payload": actual.read_text(),
    "source_destination_same_device_inode":
        (stat_source.st_dev, stat_source.st_ino) == (stat_destination.st_dev, stat_destination.st_ino),
    "actual_destination_exists": actual.exists(),
    "cwd_guess_exists": (cwd / basename).exists(), "hits": guard._CURRENT,
}
os.close(dst_fd)
os.close(src_fd)
print(json.dumps(values))
'''
    result = subprocess.run(
        [sys.executable, "-B", "-c", probe, guard.__file__, str(tmp_path),
         str(int(cwd_inside)), str(int(destination_inside)), mode],
        text=True, capture_output=True, timeout=30,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert result.returncode == 0, result.stdout + result.stderr
    values = json.loads(result.stdout)
    (tmp_path / "link_guard_observed.json").write_text(json.dumps(values, indent=2) + "\n")
    assert values["source_destination_same_device_inode"], values
    assert values["actual_destination_exists"] and values["payload"] == "neutral hard-link payload\n", values
    assert len(values["audit_events"]) == 1, values
    return values


@pytest.mark.parametrize("cwd_inside,destination_inside", [
    pytest.param(True, False, id="outside-fd-from-inside-cwd"),
    pytest.param(False, True, id="inside-fd-from-outside-cwd"),
    pytest.param(True, True, id="inside-fd-normal"),
    pytest.param(False, False, id="outside-fd-normal"),
])
def test_real_link_destination_uses_its_directory_fd(tmp_path, cwd_inside, destination_inside):
    values = _real_link_probe(tmp_path, cwd_inside=cwd_inside,
                              destination_inside=destination_inside, mode="fd")
    event = values["audit_events"][0]
    assert event[:2] == ["seed.bin", "linked.bin"] and event[2] >= 0 and event[3] >= 0, values
    assert values["destination_inside"] == destination_inside, values
    if cwd_inside != destination_inside:
        assert values["cwd_guess_exists"] is False, values
    assert values["hits"] == (["os.link linked.bin"] if destination_inside else []), values


@pytest.mark.parametrize("inside", [True, False], ids=["inside-cwd", "outside-cwd"])
def test_real_link_without_destination_fd_keeps_cwd_contract(tmp_path, inside):
    values = _real_link_probe(tmp_path, cwd_inside=inside,
                              destination_inside=not inside, mode="cwd")
    assert values["audit_events"][0][3] == -1, values
    assert values["hits"] == (["os.link linked.bin"] if inside else []), values


@pytest.mark.parametrize("inside", [True, False], ids=["inside-absolute", "outside-absolute"])
def test_absolute_link_destination_ignores_directory_fd(tmp_path, inside):
    values = _real_link_probe(tmp_path, cwd_inside=not inside,
                              destination_inside=inside, mode="absolute")
    assert os.path.isabs(values["audit_events"][0][1]), values
    assert values["hits"] == (["os.link linked.bin"] if inside else []), values
