"""R-0929-PAD-INPUT-DRIVE: a bond-pad input is never driven by the core
library's synthesis driving cell.

MEASURED (subservient, gf180mcuD, DIE route, 2026-09-29): the auto sign-off
SDC applied the pinned PDK's LibreLane SYNTH_DRIVING_CELL (core inv_1) to
`[all_inputs]` of the pad-ring top. Each input is a bond pad, so a tiny
core inverter "drove" 3.1 pF of pad: 50.8 ns on the clock port, SS setup
-55.854 ns / hold -11.180 ns (9/9 and 6/9 scenes red). One-variable replay
without that line: all 18 checks positive (+1.622 / +0.085 ns), which is an
ideal edge and so optimistic, not sign-off.

THE RULE (root ruling, RULINGS.md):
* On a DIE (pad-ring) top the off-chip drive is the design's DECLARED driver
  or input transition, else a PDK IO-tier value with its source.
* Never the core synthesis driving cell; never an ideal edge at sign-off.
* With neither, the drive is NOT_MEASURED with its reason, and a sign-off
  STA PASS measured on the resulting ideal edges becomes NOT_MEASURED; a FAIL
  stays FAIL.
* Core / HARDMACRO runs keep the synthesis driving cell.

The pinned-PDK config read (a container file read) is replaced by its values
exactly as the CR8 tests do; the SDC builder, the pad-ring predicate and the
verdict fold are the shipped code. Neutral cell/library names only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import phase3_one_shot_runner as p3  # noqa: E402
import _tapeout_declaration as TD  # noqa: E402

_CORE_CELL = ("quartz_sc__inv_1/ZN", "pinned PDK default /pdk/x/config.tcl:SYNTH_DRIVING_CELL")
_PDK_VALUES = {
    "set_clock_uncertainty": ("0.25", "pinned PDK default cfg:CLOCK_UNCERTAINTY_CONSTRAINT"),
    "set_driving_cell": _CORE_CELL,
    "set_load": ("72.91", "pinned PDK default cfg:OUTPUT_CAP_LOAD"),
}
_REPORT = "reports/phase3/pad_input_drive.json"


def _die(tmp_path: Path, name: str = "die") -> Path:
    """A self-tape-out die: SELF_TAPEOUT.txt present, no HARDMACRO
    declaration, so the shipped `requests_pad_ring` answers True."""
    p = tmp_path / name
    marker = p / TD.SELF_TAPEOUT_REL
    marker.parent.mkdir(parents=True)
    marker.write_text(TD.SELF_TAPEOUT_MARKER + "\n")
    assert TD.requests_pad_ring(p) is True
    return p


def _core(tmp_path: Path) -> Path:
    p = tmp_path / "core"
    p.mkdir()
    assert TD.requests_pad_ring(p) is False
    return p


def _sdc(project: Path, monkeypatch, values=None) -> str:
    vals = dict(_PDK_VALUES if values is None else values)
    monkeypatch.setattr(p3, "_sdc_environment_values", lambda *_: (dict(vals), []))
    monkeypatch.setattr(p3, "_synth_max_fanout", lambda *_: (None, "", []))
    return p3._build_auto_silicon_sdc(
        project, pdk_name="famxD",
        liberty_path="/pdk/libs.ref/quartz_sc/lib/slow.lib")


def _commands(text: str, name: str):
    return [ln for ln in text.splitlines() if ln.startswith(name + " ")]


def _record(project: Path) -> dict:
    return json.loads((project / _REPORT).read_text())


# -- the SDC --------------------------------------------------------------

def test_die_top_never_carries_the_core_driving_cell(tmp_path, monkeypatch):
    text = _sdc(_die(tmp_path), monkeypatch)
    assert _commands(text, "set_driving_cell") == []
    assert "NOT_MEASURED: OFFCHIP_INPUT_DRIVE" in text
    assert "quartz_sc__inv_1/ZN" in text  # the refusal names what it refused
    # the other environment commands are untouched
    assert _commands(text, "set_load") == ["set_load 0.07291 [all_outputs]"]


def test_die_drive_is_recorded_not_measured_with_the_refused_cell(tmp_path, monkeypatch):
    project = _die(tmp_path)
    _sdc(project, monkeypatch)
    rec = _record(project)
    assert rec["verdict"] == "NOT_MEASURED"
    assert rec["refused_core_driving_cell"]["value"] == "quartz_sc__inv_1/ZN"
    assert "IO-tier" in rec["reason"]


def test_a_declared_input_transition_drives_the_die_inputs(tmp_path, monkeypatch):
    project = _die(tmp_path)
    docs = project / "input" / "docs"
    docs.mkdir(parents=True)
    (docs / "L9_constraints.md").write_text("| `set_input_transition` | **1.5** |\n")
    text = _sdc(project, monkeypatch)
    assert _commands(text, "set_input_transition") == [
        "set_input_transition 1.5 [all_inputs]"]
    assert _commands(text, "set_driving_cell") == []
    rec = _record(project)
    assert rec["verdict"] == "DECLARED" and rec["value"] == "1.5"
    assert rec["source"].startswith("design doc ")


def test_a_design_declared_driver_is_kept_on_a_die(tmp_path, monkeypatch):
    project = _die(tmp_path)
    vals = dict(_PDK_VALUES,
                set_driving_cell=("quartz_sc__buf_8/Z", "design doc L9.md:3"))
    text = _sdc(project, monkeypatch, vals)
    assert _commands(text, "set_driving_cell") == [
        "set_driving_cell -lib_cell quartz_sc__buf_8 -pin Z [all_inputs]"]
    assert _record(project)["verdict"] == "DECLARED"


def test_control_a_core_run_keeps_the_synthesis_driving_cell(tmp_path, monkeypatch):
    text = _sdc(_core(tmp_path), monkeypatch)
    assert _commands(text, "set_driving_cell") == [
        "set_driving_cell -lib_cell quartz_sc__inv_1 -pin ZN [all_inputs]"]
    assert "OFFCHIP_INPUT_DRIVE" not in text


# -- the STA verdict ------------------------------------------------------

def _rows():
    mk = lambda n, s: p3.StepResult(n, s, 0.0, f"{n} {s}", [], {})
    return [mk("sta_signoff", "PASS"), mk("sta_corner", "FAIL"),
            mk("sta_record", "PASS"), mk("em_signoff", "PASS")]


def _fold(project, rows):
    fold = getattr(p3, "_pad_drive_sta_verdict", lambda _p, r: r)
    return {r.name: r for r in fold(project, rows)}


def test_a_not_measured_drive_turns_sta_pass_into_not_measured(tmp_path, monkeypatch):
    project = _die(tmp_path)
    _sdc(project, monkeypatch)
    by = _fold(project, _rows())
    assert by["sta_signoff"].status == "NOT_MEASURED"
    assert "OFFCHIP_INPUT_DRIVE" in by["sta_signoff"].detail
    assert by["sta_record"].status == "NOT_MEASURED"
    assert by["sta_corner"].status == "FAIL"      # a violation stays a violation
    assert by["em_signoff"].status == "PASS"      # not an STA row


def test_control_a_declared_drive_leaves_the_sta_verdict_alone(tmp_path, monkeypatch):
    project = _die(tmp_path)
    docs = project / "input" / "docs"
    docs.mkdir(parents=True)
    (docs / "L9_constraints.md").write_text("| `set_input_transition` | 1.5 |\n")
    _sdc(project, monkeypatch)
    by = _fold(project, _rows())
    assert by["sta_signoff"].status == "PASS"
    assert by["sta_record"].status == "PASS"


# -- the PDK IO tier ------------------------------------------------------
#
# MEASURED (gf180mcuD b344c97, all 12 gf180mcu_fd_io corner views): every
# bond-pad signal pin (`is_pad : true`, input on the input cells, inout on the
# bidirectional ones) carries `max_transition : 1.0`, the last index of its
# PAD->Y tables. That is a documented PDK IO-tier value; the tier used to
# return None unconditionally, so both benchmark dies read NOT_MEASURED.
# The fixture below copies that grammar with neutral names.

_IO_LIB = '''library ("quartz_io__%(corner)s") {
\ttime_unit : "%(unit)s";
\tdefault_max_capacitance : 999.000000;
\tdefault_max_fanout : 1.000000;
\tcell ("quartz_io__in") {
\t\tpad_cell : true;
\t\tpin ("PU") {
\t\t\tmax_transition : 9.000000;
\t\t\tdirection : "input";
\t\t}
\t\tpin ("PAD") {
\t\t\tmax_transition : %(in_pad)s;
\t\t\tis_pad : true;
\t\t\tdirection : "input";
\t\t\tcapacitance : 3.080752;
\t\t}
\t\tpin ("Y") {
\t\t\tdirection : "output";
\t\t\tmax_capacitance : 0.500000;
\t\t\ttiming () {
\t\t\t\trelated_pin : "PAD";
\t\t\t\tcell_rise ("del_1_3_6") {
\t\t\t\t\tindex_1("0.080000, 0.500000, 1.000000");
\t\t\t\t}
\t\t\t}
\t\t}
\t}
\tcell ("quartz_io__bi") {
\t\tpin ("PAD") {
\t\t\tmax_transition : %(bi_pad)s;
\t\t\tis_pad : true;
\t\t\tdirection : "inout";
\t\t}
\t\tpin ("A") {
\t\t\tmax_transition : 7.000000;
\t\t\tdirection : "input";
\t\t}
\t}
\tcell ("quartz_io__vdd") {
\t\tpin ("DVDD") {
\t\t\tis_pad : true;
\t\t\tdirection : "inout";
\t\t}
\t}
}
'''


def _io_record(project: Path, libs) -> None:
    rec = project / "reports" / "phase3" / "io_pad_chip_top.json"
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(json.dumps({"io_library_liberty": [str(x) for x in libs]}))


def _io_lib(tmp_path: Path, corner: str, in_pad="1.000000", bi_pad="1.000000",
            unit="1ns") -> Path:
    lib = tmp_path / "pdk" / f"quartz_io__{corner}.lib"
    lib.parent.mkdir(parents=True, exist_ok=True)
    lib.write_text(_IO_LIB % dict(corner=corner, in_pad=in_pad, bi_pad=bi_pad,
                                  unit=unit))
    return lib


def test_the_io_liberty_pad_bound_drives_the_die_inputs(tmp_path, monkeypatch):
    project = _die(tmp_path)
    slow = _io_lib(tmp_path, "ss", in_pad="0.8", bi_pad="1.25")
    fast = _io_lib(tmp_path, "ff", in_pad="0.6", bi_pad="0.9")
    _io_record(project, [fast, slow])
    text = _sdc(project, monkeypatch)
    # the slowest documented pad edge of every linked view; the non-pad pins
    # (9 ns, 7 ns) and the supply pad without a bound are not candidates
    assert _commands(text, "set_input_transition") == [
        "set_input_transition 1.25 [all_inputs]"]
    assert _commands(text, "set_driving_cell") == []
    assert "NOT_MEASURED: OFFCHIP_INPUT_DRIVE" not in text
    rec = _record(project)
    assert rec["verdict"] == "PDK_IO_TIER" and rec["value"] == "1.25"
    assert rec["source"].startswith(f"PDK IO tier {slow}:quartz_io__bi/PAD")
    assert rec["refused_core_driving_cell"]["value"] == "quartz_sc__inv_1/ZN"
    views = {v["liberty"]: v for v in rec["pdk_io_tier"]["views"]}
    assert views[str(slow)]["pad_input_pins"] == 2
    assert views[str(slow)]["pad_input_pins_without_bound"] == 1
    assert len(views[str(fast)]["sha256"]) == 64
    # a PDK-tier drive is a modelled edge: the STA verdict is left alone
    by = _fold(project, _rows())
    assert by["sta_signoff"].status == "PASS"


def test_the_io_liberty_time_unit_is_converted_to_ns(tmp_path, monkeypatch):
    project = _die(tmp_path)
    _io_record(project, [_io_lib(tmp_path, "tt", in_pad="1000", bi_pad="500",
                                 unit="1ps")])
    text = _sdc(project, monkeypatch)
    assert _commands(text, "set_input_transition") == [
        "set_input_transition 1 [all_inputs]"]


def test_an_unread_io_view_is_not_measured_not_a_partial_maximum(tmp_path, monkeypatch):
    project = _die(tmp_path)
    _io_record(project, [_io_lib(tmp_path, "ff"), tmp_path / "pdk" / "gone.lib"])
    text = _sdc(project, monkeypatch)
    assert _commands(text, "set_input_transition") == []
    rec = _record(project)
    assert rec["verdict"] == "NOT_MEASURED"
    assert "NOT_READ" in rec["reason"] and "gone.lib" in rec["reason"]
    assert _fold(project, _rows())["sta_signoff"].status == "NOT_MEASURED"


def test_an_io_view_without_a_pad_bound_is_not_measured(tmp_path, monkeypatch):
    project = _die(tmp_path)
    lib = tmp_path / "pdk" / "bare.lib"
    lib.parent.mkdir(parents=True)
    lib.write_text('library ("bare") {\n cell ("c") {\n  pin ("PAD") {\n'
                   '   is_pad : true;\n   direction : "input";\n  }\n }\n}\n')
    _io_record(project, [lib])
    _sdc(project, monkeypatch)
    rec = _record(project)
    assert rec["verdict"] == "NOT_MEASURED"
    assert "documents a max_transition" in rec["reason"]


def test_a_declared_input_transition_outranks_the_io_tier(tmp_path, monkeypatch):
    project = _die(tmp_path)
    _io_record(project, [_io_lib(tmp_path, "ss")])
    docs = project / "input" / "docs"
    docs.mkdir(parents=True)
    (docs / "L9_constraints.md").write_text("| `set_input_transition` | 0.4 |\n")
    text = _sdc(project, monkeypatch)
    assert _commands(text, "set_input_transition") == [
        "set_input_transition 0.4 [all_inputs]"]
    assert _record(project)["verdict"] == "DECLARED"


def test_control_a_core_run_ignores_the_io_tier(tmp_path, monkeypatch):
    project = _core(tmp_path)
    _io_record(project, [_io_lib(tmp_path, "ss")])
    text = _sdc(project, monkeypatch)
    assert _commands(text, "set_input_transition") == []
    assert _commands(text, "set_driving_cell") == [
        "set_driving_cell -lib_cell quartz_sc__inv_1 -pin ZN [all_inputs]"]
