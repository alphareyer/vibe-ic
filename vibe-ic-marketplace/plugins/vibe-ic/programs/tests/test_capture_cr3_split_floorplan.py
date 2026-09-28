"""CR-3: core utilization and placement density have distinct consumers."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import floorplan_knobs as fp  # noqa: E402
import phase3_one_shot_runner as p3  # noqa: E402


def _project(tmp_path: Path, name: str, body: str) -> Path:
    project = tmp_path / name
    docs = project / "input" / "docs"
    docs.mkdir(parents=True)
    (docs / "L9_floorplan.md").write_text(body)
    return project


@pytest.mark.parametrize("name,core,density", [
    ("quartz", 37, 0.51),
    ("basalt", 62, 0.28),
])
def test_knobs_are_independent_on_two_designs(tmp_path, name, core, density):
    project = _project(tmp_path, name,
                       f"| `FP_CORE_UTIL` | **{core}** |\n"
                       f"| `PL_TARGET_DENSITY` | **{density}** |\n")
    assert fp._l9_declared_die_util(project) == pytest.approx(core / 100)
    assert fp._l9_declared_place_density(project) == pytest.approx(density)
    assert p3._l9_declared_die_util is fp._l9_declared_die_util
    assert p3._l9_declared_place_density is fp._l9_declared_place_density
    only_density = _project(tmp_path, name + "_density",
                            f"| `PL_TARGET_DENSITY` | **{density}** |\n")
    assert p3._l9_declared_die_util(only_density) is None
    assert p3._l9_declared_place_density(only_density) == pytest.approx(density)


def test_pinned_image_default_is_read_at_runtime(tmp_path, monkeypatch):
    project = tmp_path / "project"
    reports = project / "reports"
    reports.mkdir(parents=True)
    (reports / "container_image.json").write_text(json.dumps({"container": "pinned-eda"}))
    seen = []

    def run(argv, **kwargs):
        seen.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "37\n", "")

    monkeypatch.setattr(p3._cex, "docker_exec_argv",
                        lambda container, *rest: ["docker", "exec", container, *rest])
    monkeypatch.setattr(p3.subprocess, "run", run)
    value, source = p3._flow_default_core_util(project)
    assert value == pytest.approx(0.37)
    assert "pinned image" in source and "FP_CORE_UTIL" in source
    assert len(seen) == 1
    assert "pinned-eda" in seen[0][0]
    assert "Floorplan.config_vars" in " ".join(seen[0][0])


def test_unreadable_image_is_not_read_not_a_number(tmp_path):
    value, source = p3._flow_default_core_util(tmp_path)
    assert value is None
    assert source.startswith("NOT_READ:")


def test_wrong_pinned_image_is_disclosed_not_read(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "container_image.json").write_text(json.dumps({"container": "wrong-eda"}))

    def refuse(*_args):
        raise p3._cex.ContainerImageMismatch("image identity differs")

    monkeypatch.setattr(p3._cex, "docker_exec_argv", refuse)
    value, source = p3._flow_default_core_util(tmp_path)
    assert value is None
    assert source.startswith("NOT_READ:")
