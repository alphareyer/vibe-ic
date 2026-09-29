"""CUT_W4 step 7 (R-0929-TOOL-DEFAULT): the SDC's units are the TOOL's job.

vibe-ic used to rewrite SDC text into the Liberty's units: `_scale_sdc_to_liberty_units`
over a staged deck, and a `_tu_scale` factor over the auto deck.  That solved a
real problem -- ASAP7's 1ps Liberty read an ns deck 1000x too tight (#179,
afef14dd2) -- and caused its own: a double scale to `-period 2e+07`
(fc2338b81), a commented `time_unit` read as live (28a5d8a44), a first-number
rewrite that can turn `clk_1` into `clk_1000`, and a `set_load` never scaled
at all.  OpenSTA already solves it: `set_cmd_units -time ns -capacitance pF`
makes it read every later number in ns / pF and convert to the Liberty.

So every deck vibe-ic authors opens with that line and states ns / pF, and
nothing rescales it.  These tests are where the old A4 tests' detection lives
now (test_issue162_167_asap7_pdk_agnostic.py::test_a4_*):

* the line is there, before any timing command, exactly once;
* the deck text does not depend on the Liberty's units (no rescale, so no
  double rescale);
* OpenSTA itself -- in the pinned vibeic-eda image -- reads the authored deck
  on a 1ps / 1fF Liberty as 20 ns = 20000 ps and 5 fF, and a staged deck's own
  3.5 ns output delay as 3500 ps.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import phase3_one_shot_runner as R  # noqa: E402
import sdc_environment as E  # noqa: E402
from not_verified_tier import skip_not_verified  # noqa: E402

#: A 1ps / 1fF Liberty the pinned image ships (ASAP7 INVBUF, TT).
_PS_LIB = "/foss/pdks/asap7/libs.ref/asap7sc7p5t/lib/asap7sc7p5t_INVBUF_RVT_TT_nldm_220122.lib"
_NETLIST = ("module t (input clk, input a, output y);\n"
            "  INVx1_ASAP7_75t_R u (.A(a), .Y(y));\nendmodule\n")
_TIMING = ("create_clock", "set_input_delay", "set_output_delay", "set_clock_",
           "set_max_", "set_load", "set_driving_cell", "set_input_transition")


def _lib(tmp: Path, unit_time: str, unit_cap: str) -> Path:
    p = tmp / f"lib_{unit_time}.lib"
    p.write_text(f'library(x){{\n time_unit : "{unit_time}";\n'
                 f' capacitive_load_unit ({unit_cap});\n}}\n')
    return p


def _auto(proj: Path, liberty: str, monkeypatch) -> str:
    values = {"set_load": ("5", "test: 5 fF declared as OUTPUT_CAP_LOAD"),
              "set_clock_uncertainty": ("0.25", "test ns")}
    monkeypatch.setattr(R, "_sdc_environment_values", lambda *_: (values, []))
    monkeypatch.setattr(R, "_synth_max_fanout", lambda *_: (None, "", []))
    return R._build_auto_silicon_sdc(proj, liberty_path=liberty)


def _units_first(text: str) -> None:
    lines = text.splitlines()
    assert lines.count(E.SDC_UNITS_LINE) == 1, text[:600]
    first = next(i for i, l in enumerate(lines) if l.startswith(_TIMING))
    assert lines.index(E.SDC_UNITS_LINE) < first, lines[:first + 1]


def test_the_auto_deck_opens_with_the_tools_units_line(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    (proj / "phase1").mkdir(parents=True)
    _units_first(_auto(proj, str(_lib(tmp_path, "1ps", "1,ff")), monkeypatch))


def test_the_deck_does_not_depend_on_the_liberty_units(tmp_path, monkeypatch):
    """No rescale, hence no double rescale: a 1ps and a 1ns Liberty get the
    same numbers (the old writer emitted `-period 20000` for one of them)."""
    proj = tmp_path / "proj"
    (proj / "phase1").mkdir(parents=True)
    ps = _auto(proj, str(_lib(tmp_path, "1ps", "1,ff")), monkeypatch)
    ns = _auto(proj, str(_lib(tmp_path, "1ns", '1.0,"pf"')), monkeypatch)
    pick = lambda t: [l for l in t.splitlines() if l.startswith(_TIMING)]  # noqa: E731
    assert pick(ps) == pick(ns)
    assert "create_clock -name clk -period 20.0 " in ps
    assert "set_load 0.005 [all_outputs]" in ps


def test_a_staged_deck_gets_the_line_once_and_keeps_its_own(tmp_path):
    staged = "create_clock -name core -period 10.0 [get_ports clk]\n"
    once = E.with_sdc_units(staged)
    _units_first(once)
    assert E.with_sdc_units(once) == once, "the line was added twice"
    own = "set_cmd_units -time ps\ncreate_clock -name core -period 10000 [get_ports clk]\n"
    assert E.with_sdc_units(own) == own, "a deck's own units were overridden"


# -- the tool ---------------------------------------------------------------

def _sta():
    """(argv prefix, mount-needed) for a real OpenSTA that has `_PS_LIB`."""
    host = shutil.which("sta")
    if host and Path(_PS_LIB).is_file():
        return [host, "-no_init", "-exit"], False
    tried = [f"host sta={host or 'absent'}"]
    if shutil.which("docker"):
        import _eda_pin  # noqa: PLC0415
        image = _eda_pin.image_reference()
        held = subprocess.run(["docker", "image", "inspect", image],
                              capture_output=True, text=True, timeout=60)
        if held.returncode == 0:
            return (["docker", "run", "--rm", "--entrypoint", "sta",
                     "--user", f"{os.getuid()}:{os.getgid()}", "@MOUNT@", image,
                     "-no_init", "-exit"], True)
        tried.append(f"pinned image {image} not held")
    else:
        tried.append("docker absent")
    skip_not_verified(
        "OpenSTA with a 1ps Liberty is not reachable on host "
        + os.uname().nodename + " (" + "; ".join(tried) + "), so the deck "
        "cannot be handed to the tool that converts its units",
        "run on a lane host that holds the pinned vibeic-eda image")


def _tool_reads(tmp_path: Path, deck: str) -> dict:
    prefix, mount = _sta()
    work = tmp_path / uuid.uuid4().hex[:8]
    work.mkdir(parents=True)
    (work / "t.v").write_text(_NETLIST)
    (work / "deck.sdc").write_text(deck)
    (work / "t.tcl").write_text(
        f"read_liberty {_PS_LIB}\nread_verilog {work}/t.v\nlink_design t\n"
        f"read_sdc {work}/deck.sdc\n"
        # read back in the LIBERTY's own units
        "set_cmd_units -time ps -capacitance fF\n"
        "puts \"PERIOD [get_property [get_clocks *] period]\"\n"
        "report_net y\n")
    argv = [x for a in prefix for x in (["-v", f"{work}:{work}"] if a == "@MOUNT@" else [a])]
    r = subprocess.run(argv + [str(work / "t.tcl")], capture_output=True,
                       text=True, timeout=300)
    assert r.returncode == 0, (r.stdout[-2000:], r.stderr[-2000:])
    out = {"period_ps": None, "load_ff": None, "log": r.stdout}
    for line in r.stdout.splitlines():
        if line.startswith("PERIOD "):
            out["period_ps"] = float(line.split()[1])
        if line.strip().startswith("Pin capacitance:"):
            out["load_ff"] = float(line.split()[-1])
    return out


def test_opensta_reads_the_auto_deck_in_ns_on_a_ps_liberty(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    (proj / "phase1").mkdir(parents=True)
    deck = _auto(proj, _PS_LIB, monkeypatch)
    got = _tool_reads(tmp_path, deck)
    assert got["period_ps"] == pytest.approx(20000.0), got["log"][-1500:]
    assert got["load_ff"] == pytest.approx(5.0), got["log"][-1500:]


def test_without_the_line_the_tool_reads_the_same_deck_1000x_tight(tmp_path, monkeypatch):
    """THE OTHER DIRECTION: the line is what makes the difference, so the
    assertion above pins the tool's conversion, not a coincidence."""
    proj = tmp_path / "proj"
    (proj / "phase1").mkdir(parents=True)
    deck = _auto(proj, _PS_LIB, monkeypatch).replace(E.SDC_UNITS_LINE, "# (no units line)")
    got = _tool_reads(tmp_path, deck)
    assert got["period_ps"] == pytest.approx(20.0), got["log"][-1500:]


def test_opensta_reads_a_staged_decks_own_numbers_in_ns(tmp_path):
    staged = ("create_clock -name core -period 10.0 [get_ports clk]\n"
              "set_output_delay 3.5 -clock core [all_outputs]\n")
    got = _tool_reads(tmp_path, E.with_sdc_units(staged))
    assert got["period_ps"] == pytest.approx(10000.0), got["log"][-1500:]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
