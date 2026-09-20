"""The two I/O-delay variants the owner is choosing between, prepared so the
ruling lands in ONE run.

MEASURED on r44's own netlist and max SPEF at SS (subservient x gf180mcuD as a
DIE): the design's declared I/O delay (L9 §9.1.3, 20 % of the 20 ns period =
4 ns) gives worst slack -13.48 ns; with NO external delay at all it is
-8.58 ns, and the worst path is then an INTERNAL reg->reg. So for THIS design
variant A (the input's own numbers) and variant B (the current auto-SDC) are
the SAME constraint — the input declares one — and the difference only exists
for a design that declares nothing.

  VIBEIC_IO_DELAY_SOURCE unset / declared_or_plugin  (B, today's behaviour)
      declared value when there is one, else this plugin's literal.
  VIBEIC_IO_DELAY_SOURCE=declared_only               (A)
      declared value when there is one, else NO external delay at all — and
      the SDC says the boundary paths are then NOT TIMED.

Both directions here, plus the record a gate reads instead of parsing SDC
comments.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as R  # noqa: E402

DECLARED = "### 9.1.3 I/O delay\n- set_input_delay / set_output_delay: 20% of the clock period\n"


def _project(tmp_path: Path, body: str = "") -> Path:
    docs = tmp_path / "input" / "docs"
    docs.mkdir(parents=True)
    (docs / "L9_constraints_floorplan.md").write_text(
        "# L9\n| gf180mcu_* | **20** | 50 MHz |\n" + body)
    return tmp_path


def _commands(sdc: str) -> list:
    """The SDC's actual commands — comments and `puts` are not constraints."""
    out = []
    for line in sdc.splitlines():
        s = line.strip()
        if s and not s.startswith("#") and not s.startswith("puts "):
            out.append(s)
    return out


@pytest.fixture(autouse=True)
def _clean_env():
    os.environ.pop(R.IO_DELAY_SOURCE_ENV, None)
    yield
    os.environ.pop(R.IO_DELAY_SOURCE_ENV, None)


def test_default_is_todays_behaviour(tmp_path):
    assert R.io_delay_source({}) == R.IO_DELAY_DECLARED_OR_PLUGIN
    sdc = R._build_auto_silicon_sdc(_project(tmp_path), top="d")
    assert any("set_output_delay 2" in c for c in _commands(sdc))


def test_a_typo_is_the_default_not_a_silent_change(tmp_path):
    assert R.io_delay_source({R.IO_DELAY_SOURCE_ENV: "declared-only"}) == (
        R.IO_DELAY_DECLARED_OR_PLUGIN)
    assert R.io_delay_source({R.IO_DELAY_SOURCE_ENV: "DECLARED_ONLY"}) == (
        R.IO_DELAY_DECLARED_ONLY)


def test_declared_only_omits_the_delay_when_nothing_is_declared(tmp_path):
    os.environ[R.IO_DELAY_SOURCE_ENV] = R.IO_DELAY_DECLARED_ONLY
    sdc = R._build_auto_silicon_sdc(_project(tmp_path), top="d")
    cmds = _commands(sdc)
    assert not any(c.startswith(("set_input_delay", "set_output_delay"))
                   for c in cmds), cmds
    assert "VIBEIC_IO_DELAY_OMITTED" in sdc and "NOT_MEASURED" in sdc
    assert any(c.startswith("create_clock") for c in cmds)   # clock unchanged


def test_a_declared_delay_is_emitted_under_either_variant(tmp_path):
    for src in (R.IO_DELAY_DECLARED_OR_PLUGIN, R.IO_DELAY_DECLARED_ONLY):
        os.environ[R.IO_DELAY_SOURCE_ENV] = src
        sdc = R._build_auto_silicon_sdc(_project(tmp_path / src, DECLARED),
                                        top="d")
        assert any("set_output_delay 4" in c for c in _commands(sdc)), src
        assert "VIBEIC_IO_DELAY_OMITTED" not in sdc


def test_the_contract_record_says_who_decided_and_whether_paths_are_timed():
    declared = R.io_delay_contract(4.0, 2.0, R.IO_DELAY_DECLARED_ONLY)
    assert declared["source"] == "design_declaration"
    assert declared["io_delay_ns"] == 4.0 and declared["boundary_paths_timed"]

    plugin = R.io_delay_contract(None, 2.0, R.IO_DELAY_DECLARED_OR_PLUGIN)
    assert plugin["source"] == "plugin_assumption"
    assert plugin["io_delay_ns"] == 2.0 and plugin["boundary_paths_timed"]

    omitted = R.io_delay_contract(None, 2.0, R.IO_DELAY_DECLARED_ONLY)
    assert omitted["source"] == "none_declared"
    assert omitted["io_delay_ns"] is None
    assert omitted["boundary_paths_timed"] is False
    assert "not a pass" in omitted["note"]
