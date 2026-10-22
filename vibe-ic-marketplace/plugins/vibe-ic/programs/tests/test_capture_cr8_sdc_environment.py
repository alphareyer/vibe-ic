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


@pytest.mark.parametrize("container", ["", "legacy-name"])
def test_local_pdk_values_use_the_current_image_files(tmp_path, monkeypatch, container):
    library = "quartz_sc"
    root = tmp_path / "pdk/family"
    configs = root / "libs.tech/librelane"
    (configs / library).mkdir(parents=True)
    (configs / "config.tcl").write_text(_BASE)
    (configs / library / "config.tcl").write_text(_CELL)
    liberty = str(root / "libs.ref" / library / "lib/slow.lib")
    monkeypatch.setattr(env._cex, "no_container_route", lambda: True)

    def forbidden(*args, **kwargs):
        raise AssertionError("LOCAL PDK reads must not launch Docker")

    monkeypatch.setattr(env._cex, "docker_exec_argv", forbidden)
    values, unread = env._sdc_environment_values(
        tmp_path, liberty, container, None, None)
    assert not unread
    assert set(values) == set(env._SDC_ENV_KEYS)
    assert values["set_max_transition"][0] == "2.7"
    assert values["set_max_capacitance"][0] == "0.18"
    assert values["set_driving_cell"][0] == "quartz_sc__inv_2/Z"
    assert all(source.startswith("pinned PDK default ")
               for _, source in values.values())


def test_readable_host_cache_wins_over_retained_container_identity(tmp_path, monkeypatch):
    """A continuation keeps the container id after resolving its pinned PDK
    into a same-host cache.  The host-only path must not be sent to docker."""
    library = "quartz_sc"
    root = tmp_path / "cache/by-image-digest/family"
    configs = root / "libs.tech/librelane"
    (configs / library).mkdir(parents=True)
    (configs / "config.tcl").write_text(_BASE)
    (configs / library / "config.tcl").write_text(_CELL)
    liberty = str(root / "libs.ref" / library / "lib/slow.lib")
    monkeypatch.setattr(env._cex, "no_container_route", lambda: False)

    def forbidden(*args, **kwargs):
        raise AssertionError("a readable host-cache PDK must not use docker")

    monkeypatch.setattr(env._cex, "docker_exec_argv", forbidden)
    values, unread = env._sdc_environment_pdk_values(
        liberty, "retained-pinned-eda",
        lambda path, _container: "/container-only" + path)
    assert not unread
    assert values["set_max_transition"][0] == "2.7"
    assert values["set_max_capacitance"][0] == "0.18"
    assert values["set_driving_cell"][0] == "quartz_sc__inv_2/Z"


def test_missing_local_pdk_files_are_named_and_not_fabricated(tmp_path, monkeypatch):
    root = tmp_path / "pdk/family"
    liberty = str(root / "libs.ref/quartz_sc/lib/slow.lib")
    monkeypatch.setattr(env._cex, "no_container_route", lambda: True)
    values, unread = env._sdc_environment_pdk_values(liberty, "")
    assert values == {}
    assert unread == [
        f"NOT_READ: pinned PDK config {root}/libs.tech/librelane/config.tcl",
        f"NOT_READ: pinned PDK config {root}/libs.tech/librelane/quartz_sc/config.tcl"]


@pytest.mark.parametrize("declared,expected", [(None, 10), (4, 4)])
def test_local_synthesis_and_sdc_use_the_library_config_fanout(
        tmp_path, monkeypatch, declared, expected):
    root = tmp_path / "pdk/family"
    configs = root / "libs.tech/librelane"
    (configs / "quartz_sc").mkdir(parents=True)
    (configs / "config.tcl").write_text(_BASE)
    (configs / "quartz_sc/config.tcl").write_text(
        _CELL + "set ::env(MAX_FANOUT_CONSTRAINT) 10\n")
    if declared is not None:
        docs = tmp_path / "input/docs"
        docs.mkdir(parents=True)
        (docs / "L9_constraints.md").write_text(
            f"| SYNTH_MAX_FANOUT | {declared} |\n")
    monkeypatch.setattr(p3._cex, "no_container_route", lambda: True)
    liberty = str(root / "libs.ref/quartz_sc/lib/slow.lib")
    cap, source, unread = p3._synth_max_fanout(tmp_path, "family", liberty, "")
    assert cap == expected and unread == []
    assert ("MAX_FANOUT_CONSTRAINT" in source if declared is None
            else "L9 declares" in source)


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


def test_one_ps_liberty_scales_every_time_valued_environment_command(tmp_path, monkeypatch):
    liberty = tmp_path / 'one_ps.lib'
    liberty.write_text('library(neutral) { time_unit : "1ps"; }\n')
    values = {
        'set_clock_uncertainty': ('0.31', 'design ns'),
        'set_clock_transition': ('0.12', 'pinned PDK ns'),
        'set_max_transition': ('2.7', 'pinned PDK ns'),
    }
    monkeypatch.setattr(p3, '_sdc_environment_values', lambda *_: (values, []))
    monkeypatch.setattr(p3, '_synth_max_fanout', lambda *_: (None, '', []))
    text = p3._build_auto_silicon_sdc(tmp_path, liberty_path=str(liberty))
    assert 'create_clock -name clk -period 20000' in text
    assert 'set_clock_uncertainty 310 [all_clocks]' in text
    assert 'set_clock_transition 120 [all_clocks]' in text
    slew_lines = [line for line in text.splitlines()
                  if line.startswith('set_max_transition ')]
    assert len(slew_lines) == 1
    assert float(slew_lines[0].split()[1]) == 2700.0
    assert slew_lines[0].endswith('[current_design]')


def test_liberty_default_slew_is_already_in_its_own_one_ps_unit(tmp_path, monkeypatch):
    liberty = tmp_path / 'one_ps.lib'
    liberty.write_text('library(neutral) { time_unit : "1ps"; '
                       'default_max_transition : 2.7; }\n')
    values = {'set_max_transition': ('2.7',
              f'liberty default {liberty}:default_max_transition')}
    monkeypatch.setattr(p3, '_sdc_environment_values', lambda *_: (values, []))
    monkeypatch.setattr(p3, '_synth_max_fanout', lambda *_: (None, '', []))
    text = p3._build_auto_silicon_sdc(tmp_path, liberty_path=str(liberty))
    slew_lines = [line for line in text.splitlines()
                  if line.startswith('set_max_transition ')]
    assert len(slew_lines) == 1
    assert float(slew_lines[0].split()[1]) == 2.7


def test_readable_but_non_declaring_pdk_names_every_absent_command(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(env._cex, 'docker_exec_argv',
                        lambda container, *args: ['docker', 'exec', container, *args])

    def readable(argv, **_kwargs):
        calls.append(argv[-1])
        return subprocess.CompletedProcess(argv, 0, '# no R8 declaration\n', '')

    monkeypatch.setattr(env.subprocess, 'run', readable)
    liberty = '/pdk/family/libs.ref/neutral_sc/lib/slow.lib'
    values, unread = env._sdc_environment_values(tmp_path, liberty, 'pin', None, None)
    assert values == {} and unread == []
    assert len(calls) == 2
    text = env._sdc_environment_prefix(values, unread)
    records = {line.split(': ', 1)[1].split(';', 1)[0]
               for line in text.splitlines() if line.startswith('# UNDECLARED: ')}
    assert records == set(env._SDC_ENV_KEYS.values())
    assert not any(line.startswith('set_') for line in text.splitlines())


def test_unreadable_pdk_names_each_unresolved_command(tmp_path):
    values, unread = env._sdc_environment_values(tmp_path, '', '', None, None)
    text = env._sdc_environment_prefix(values, unread)
    assert 'NOT_READ:' in text
    for key in env._SDC_ENV_KEYS.values():
        assert f'NOT_MEASURED: {key}' in text
        assert f'UNDECLARED: {key}' not in text
