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


import _resolved_pdk_view_fixture as RV  # noqa: E402


def _resolved(library):
    """What the image's LibreLane resolver answers for this PDK/library
    (CUT_W4 step 7: the resolver, not a regex over config.tcl, is the PDK
    tier; STD_CELL_LIBRARY substitution is the tool's)."""
    return {"CLOCK_UNCERTAINTY_CONSTRAINT": "0.31",
            "CLOCK_TRANSITION_CONSTRAINT": "0.12",
            "SYNTH_DRIVING_CELL": f"{library}__inv_2/Z",
            "OUTPUT_CAP_LOAD": "91.5",
            "MAX_TRANSITION_CONSTRAINT": 2.7,
            "MAX_CAPACITANCE_CONSTRAINT": 0.18,
            "STD_CELL_LIBRARY": library}


@pytest.mark.parametrize("library", ["quartz_sc", "basalt_sc"])
def test_pinned_pdk_values_emit_six_sourced_lines(tmp_path, monkeypatch, library):
    assert env is not None
    project = tmp_path / library
    project.mkdir()
    liberty = f"/pdk/family/libs.ref/{library}/lib/slow.lib"
    seen = RV.install(monkeypatch, project, "famxD", _resolved(library))
    values, unread = p3._sdc_environment_values(project, liberty, "pinned-eda",
                                                None, None, None, "famxD")
    assert not unread
    assert set(values) == set(p3._SDC_ENV_KEYS)
    assert values["set_driving_cell"][0] == f"{library}__inv_2/Z"
    assert any(RV.RUN_IMAGE in " ".join(map(str, argv)) for argv in seen), \
        "the resolver did not run in the image this run recorded"
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
        assert text.splitlines()[pos - 1].startswith("# R8 source: LibreLane resolved")
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
    """PDK values come only from the image THIS run recorded; a run with no
    image record is NOT_READ, and no container is started for it."""
    assert env is not None
    seen = RV.install(monkeypatch, tmp_path, "famxD", _resolved("quartz_sc"),
                      record_image=False)
    values, unread = p3._sdc_environment_pdk_values(tmp_path, "famxD")
    assert values == {}
    assert unread and all(item.startswith("NOT_READ:") for item in unread)
    assert "LL_RUN_IMAGE_UNRECORDED" in unread[0]
    assert seen == []


def test_a_failing_resolver_is_not_read(tmp_path, monkeypatch):
    assert env is not None
    RV.install(monkeypatch, tmp_path, "famxD", None)
    values, unread = p3._sdc_environment_pdk_values(tmp_path, "famxD")
    assert values == {}
    assert unread and "LL_CONFIG_RESOLUTION_FAILED" in unread[0], unread


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


def test_one_ps_liberty_deck_states_ns_under_the_tools_units_line(tmp_path, monkeypatch):
    """CUT_W4 step 7: the deck is never rescaled. It opens with
    `set_cmd_units -time ns -capacitance pF` before any timing command and
    states every value in ns; OpenSTA converts (measured in
    test_cut7_sdc_units_are_the_tools on a 1ps/1fF Liberty)."""
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
    lines = text.splitlines()
    units = lines.index(env.SDC_UNITS_LINE)
    first_timing = next(i for i, l in enumerate(lines) if l.startswith(
        ('create_clock', 'set_input_delay', 'set_output_delay', 'set_clock_',
         'set_max_', 'set_load')))
    assert units < first_timing
    assert 'create_clock -name clk -period 20.0' in text
    assert 'set_clock_uncertainty 0.31 [all_clocks]' in text
    assert 'set_clock_transition 0.12 [all_clocks]' in text
    slew_lines = [line for line in lines if line.startswith('set_max_transition ')]
    assert len(slew_lines) == 1
    assert float(slew_lines[0].split()[1]) == 2.7
    assert slew_lines[0].endswith('[current_design]')


def test_a_liberty_default_slew_is_stated_in_ns(tmp_path):
    """A Liberty default is read in that Liberty's units (2.7 in a 1ps
    library) and stated once in the deck's ns: 0.0027."""
    liberty = tmp_path / 'one_ps.lib'
    liberty.write_text('library(neutral) { time_unit : "1ps"; '
                       'capacitive_load_unit (1,ff); '
                       'default_max_transition : 2.7; }\n')
    values, _unread = env._sdc_environment_values(tmp_path, str(liberty), '',
                                                  2.7, 150.0)
    assert float(values['set_max_transition'][0]) == pytest.approx(0.0027)
    assert float(values['set_max_capacitance'][0]) == pytest.approx(0.15)
    assert values['set_max_transition'][1].startswith('liberty default ')


def test_readable_but_non_declaring_pdk_names_every_absent_command(tmp_path, monkeypatch):
    seen = RV.install(monkeypatch, tmp_path, 'famxD', {'STA_CORNERS': ['nom_tt']})
    liberty = '/pdk/family/libs.ref/neutral_sc/lib/slow.lib'
    values, unread = env._sdc_environment_values(tmp_path, liberty, 'pin', None, None,
                                                 None, 'famxD')
    assert values == {} and unread == []
    assert sum('Chip(config=design' in ' '.join(map(str, a)) for a in seen) == 1
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
