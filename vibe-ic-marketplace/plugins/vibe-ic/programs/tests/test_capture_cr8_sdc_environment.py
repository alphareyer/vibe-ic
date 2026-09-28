"""R8: six SDC environment commands, each with source provenance."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import phase3_one_shot_runner as p3  # noqa: E402
try:
    import sdc_environment as env  # noqa: E402
except ImportError:  # red-on-main: the R8 producer is absent there
    env = None


_BASE = """set ::env(CLOCK_UNCERTAINTY_CONSTRAINT) 0.31
set ::env(CLOCK_TRANSITION_CONSTRAINT) 0.12
"""
_CELL = """set ::env(SYNTH_DRIVING_CELL) "$::env(STD_CELL_LIBRARY)__inv_2/Z"
set ::env(OUTPUT_CAP_LOAD) "91.5"
set ::env(MAX_TRANSITION_CONSTRAINT) 2.7
set ::env(MAX_CAPACITANCE_CONSTRAINT) 0.18
"""


@pytest.mark.parametrize("library", ["quartz_sc", "basalt_sc"])
def test_pinned_pdk_values_emit_six_sourced_lines(tmp_path, monkeypatch, library):
    assert env is not None
    project = tmp_path / library
    project.mkdir()
    liberty = f"/pdk/family/libs.ref/{library}/lib/slow.lib"

    def run(argv, **kwargs):
        config = argv[-1]
        body = _CELL if config.endswith(f"/{library}/config.tcl") else _BASE
        return subprocess.CompletedProcess(argv, 0, body, "")

    monkeypatch.setattr(p3._cex, "docker_exec_argv",
                        lambda container, *rest: ["docker", "exec", container, *rest])
    monkeypatch.setattr(p3.subprocess, "run", run)
    values, unread = p3._sdc_environment_values(project, liberty, "pinned-eda",
                                                None, None)
    assert not unread
    assert set(values) == set(p3._SDC_ENV_KEYS)
    assert values["set_driving_cell"][0] == f"{library}__inv_2/Z"
    text = p3._sdc_environment_prefix(values, unread)
    text += p3._drv_constraints_sdc_block(
        float(values["set_max_transition"][0]),
        float(values["set_max_capacitance"][0]),
        slew_source=values["set_max_transition"][1],
        cap_source=values["set_max_capacitance"][1])
    commands = [line for line in text.splitlines() if line.startswith("set_")]
    assert [line.split()[0] for line in commands] == list(p3._SDC_ENV_KEYS)
    for line in commands:
        pos = text.splitlines().index(line)
        assert text.splitlines()[pos - 1].startswith("# R8 source: pinned PDK default")
    assert f"set_driving_cell -lib_cell {library}__inv_2 -pin Z" in text
    assert "set_load 0.0915 [all_outputs]" in text


def test_design_docs_override_pdk_then_liberty(tmp_path, monkeypatch):
    assert env is not None
    docs = tmp_path / "input" / "docs"
    docs.mkdir(parents=True)
    (docs / "L9_constraints.md").write_text(
        "| `CLOCK_UNCERTAINTY_CONSTRAINT` | **0.07** |\n"
        "| `MAX_TRANSITION_CONSTRAINT` | **1.1** |\n")
    monkeypatch.setattr(env, "_sdc_environment_pdk_values",
                        lambda *_: ({"set_clock_uncertainty": ("0.31", "pinned PDK"),
                                    "set_max_transition": ("2.7", "pinned PDK"),
                                    "set_max_capacitance": ("0.18", "pinned PDK")}, []))
    values, unread = p3._sdc_environment_values(tmp_path, "lib.lib", "pin", 3.0, 0.2)
    assert not unread
    assert values["set_clock_uncertainty"][0] == "0.07"
    assert values["set_clock_uncertainty"][1].startswith("design doc ")
    assert values["set_max_transition"][0] == "1.1"
    assert values["set_max_capacitance"] == ("0.18", "pinned PDK")


def test_negated_table_value_is_not_a_design_declaration(tmp_path):
    assert env is not None
    docs = tmp_path / "input" / "docs"
    docs.mkdir(parents=True)
    (docs / "L9_constraints.md").write_text(
        "| `CLOCK_UNCERTAINTY_CONSTRAINT` | not 0.07 |\n"
        "| `SYNTH_DRIVING_CELL` | not quartz_sc__inv_2/Z |\n")
    values, unread = p3._sdc_environment_design_values(tmp_path)
    assert values == {}
    assert unread == []


def test_unreadable_pdk_is_disclosed_not_fabricated(tmp_path):
    assert env is not None
    values, unread = p3._sdc_environment_values(tmp_path, "", "", None, None)
    assert values == {}
    assert unread and all(item.startswith("NOT_READ:") for item in unread)
    text = p3._sdc_environment_prefix(values, unread)
    assert "NOT_READ:" in text
    assert not any(line.startswith("set_") for line in text.splitlines())


def test_wrong_pinned_image_is_not_read(tmp_path, monkeypatch):
    assert env is not None

    def refuse(*_args):
        raise p3._cex.ContainerImageMismatch("image identity differs")

    monkeypatch.setattr(p3._cex, "docker_exec_argv", refuse)
    liberty = "/pdk/family/libs.ref/quartz_sc/lib/slow.lib"
    values, unread = p3._sdc_environment_pdk_values(liberty, "wrong-eda")
    assert values == {}
    assert unread and all(item.startswith("NOT_READ:") for item in unread)


def test_auto_sdc_consumes_six_resolved_values(tmp_path, monkeypatch):
    values = {
        "set_clock_uncertainty": ("0.31", "pinned PDK config:clock"),
        "set_clock_transition": ("0.12", "pinned PDK config:transition"),
        "set_driving_cell": ("quartz_sc__inv_2/Z", "pinned PDK config:driver"),
        "set_load": ("91.5", "pinned PDK config:load"),
        "set_max_transition": ("2.7", "pinned PDK config:slew"),
        "set_max_capacitance": ("0.18", "pinned PDK config:cap"),
    }
    if hasattr(p3, "_sdc_environment_values"):
        monkeypatch.setattr(p3, "_sdc_environment_values",
                            lambda *_: (values, []))
    monkeypatch.setattr(p3, "_synth_max_fanout",
                        lambda *_: (None, "", []))
    text = p3._build_auto_silicon_sdc(tmp_path, pdk_name="famxD",
                                      liberty_path="/pdk/libs.ref/quartz_sc/lib/slow.lib")
    commands = [line.split()[0] for line in text.splitlines()
                if line.startswith("set_") and line.split()[0] in values]
    assert commands == list(values)
    assert text.count("# R8 source:") == 6
