#!/usr/bin/env python3
"""DT2/DT3 take their timing views from LibreLane `OpenROAD.STAPostPNR`.

review70 (DT2, DT3): the path-delay grade must read its routed netlist, SDC
and SPEF from LibreLane's STAPostPNR state rather than from the runner's own
STA, and the small-delay grade must take its slack from the max_ss corner.

`librelane_contract.post_pnr_timing_inputs` reads the state and the corner's
own `sta.log`; `path_delay_fault_atpg_run` / `sdd_atpg_run` accept
`--librelane-state/--librelane-corner`; `phase3_one_shot_runner` passes them
when `phase3/librelane_switch.json` selects `librelane` for the step. The
`sta.log` lines below are copied from a real STAPostPNR run (LibreLane
3.1.0.dev1, t78 reference, corner max_ss_125C_4v50) with the paths shortened.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROGS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGS))

import librelane_contract as LL             # noqa: E402
import path_delay_fault_atpg_run as PDF     # noqa: E402
import phase3_one_shot_runner as R          # noqa: E402
import sdd_atpg_run as SDD                  # noqa: E402

CORNER = "max_ss_125C_4v50"
STA_LOG = """+ define_corners max_ss_125C_4v50
Reading timing models for corner max_ss_125C_4v50…
Reading cell library for the 'max_ss_125C_4v50' corner at '/pdk/lib/sc__ss_125C_4v50.lib'…
Reading cell library for the 'max_ss_125C_4v50' corner at '/pdk/lib/io__ss_125C_4v50.lib'…
Reading top-level netlist at '/work/51/chip_top.nl.v'…
Linking design 'chip_top' from netlist…
Reading top-level design parasitics for the 'max_ss_125C_4v50' corner at '/work/53/max.spef'…
"""


def _stapostpnr(project: Path, step: str = "OpenROAD.STAPostPNR",
                corners=(CORNER, "nom_tt_025C_5v00"), log: str = STA_LOG,
                outside: Path | None = None) -> Path:
    root = project / "phase3/librelane/03-openroad-stapostpnr"
    views = outside or (project / "phase3/librelane/views")
    views.mkdir(parents=True, exist_ok=True)
    for name in ("chip_top.nl.v", "chip_top.sdc", "nom.spef", "max.spef"):
        (views / name).write_text(name + "\n")
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.json").write_text(json.dumps({"meta": {"step": step}}))
    for corner in corners:
        (root / corner).mkdir()
        (root / corner / "sta.log").write_text(
            log.replace(CORNER, corner))
    state = root / "state_out.json"
    state.write_text(json.dumps({
        "nl": str(views / "chip_top.nl.v"), "sdc": str(views / "chip_top.sdc"),
        "spef": {"nom_*": str(views / "nom.spef"),
                 "max_*": str(views / "max.spef")}, "metrics": {}}))
    return state


# ---------------------------------------------------------------- contract
def test_the_corner_selects_its_spef_and_the_libraries_sta_read(tmp_path):
    state = _stapostpnr(tmp_path)
    got = LL.post_pnr_timing_inputs(tmp_path, state, CORNER)
    assert got["spef"] == "phase3/librelane/views/max.spef"
    assert got["sta_netlist"] == "phase3/librelane/views/chip_top.nl.v"
    assert got["sdc"] == "phase3/librelane/views/chip_top.sdc"
    assert got["liberties"] == ["/pdk/lib/sc__ss_125C_4v50.lib",
                                "/pdk/lib/io__ss_125C_4v50.lib"]
    assert got["sha256"]["spef"] == LL.digest(tmp_path / got["spef"])


@pytest.mark.parametrize("case,code", [
    ("other_step", "LL_NOT_STAPOSTPNR"),
    ("corner_not_analysed", "LL_CORNER_NOT_ANALYSED"),
    ("no_library_line", "LL_CORNER_LIBRARY_UNREAD"),
    ("outside_project", "LL_STATE_OUTSIDE_PROJECT"),
])
def test_what_cannot_be_read_is_refused(tmp_path, case, code):
    project = tmp_path / "proj"
    kwargs = {
        "other_step": {"step": "OpenROAD.STAMidPNR"},
        "corner_not_analysed": {"corners": ("nom_tt_025C_5v00",)},
        "no_library_line": {"log": "+ define_corners max_ss_125C_4v50\n"},
        "outside_project": {"outside": tmp_path / "elsewhere"},
    }[case]
    state = _stapostpnr(project, **kwargs)
    with pytest.raises(LL.Refusal) as err:
        LL.post_pnr_timing_inputs(project, state, CORNER)
    assert err.value.code == code


# ---------------------------------------------------------------- DT2
def test_sta_reads_every_corner_library(tmp_path, monkeypatch):
    seen = {}

    def fake_docker(project, cmd, timeout, pdk_dir=None, extra_mounts=None):
        tcl = (project / "phase2/stage2/dft/pdf/_pdf_sta.tcl").read_text()
        seen["tcl"] = tcl
        (project / "phase2/stage2/dft/pdf/sta_paths.rpt").write_text(
            "Startpoint: a\n")
        return 0, "", ""

    monkeypatch.setattr(PDF._tdf, "_run_in_docker", fake_docker)
    ok, _text, _msg = PDF._run_opensta_paths(
        tmp_path, "n.v", "c.sdc", "m.spef", "/pdk/typ.lib", "top", 4, None,
        timeout=10, sta_liberties=["/pdk/a.lib", "/pdk/b.lib"])
    assert ok
    lines = seen["tcl"].splitlines()
    assert lines[:2] == ["read_liberty /pdk/a.lib", "read_liberty /pdk/b.lib"]
    assert "read_liberty /pdk/typ.lib" not in lines
    assert "read_spef /work/m.spef" in lines


def test_the_report_names_its_timing_source(tmp_path):
    source = {"step": "OpenROAD.STAPostPNR", "corner": CORNER,
              "liberties": [], "sha256": {}}
    _ec, rep = PDF.run_pdf_atpg(
        tmp_path, netlist_rel="n.v", cut_rel="c.v", flat_rel="f.v",
        sta_netlist="absent.v", sdc="absent.sdc", spef="absent.spef",
        liberty="l.lib", top="t", clock="clk", dff_cells=None, k=4,
        floor=0.0, timing_fraction=0.5, pdk_dir=None, timeout=10,
        timing_source=source)
    assert rep["timing_source"]["corner"] == CORNER


def test_a_requested_state_that_cannot_be_read_grades_nothing(tmp_path):
    state = _stapostpnr(tmp_path, corners=("nom_tt_025C_5v00",))
    out = tmp_path / "dt2.json"
    rc = PDF.main([str(tmp_path), "--clock", "clk", "--librelane-state",
                   str(state.relative_to(tmp_path)), "--librelane-corner",
                   CORNER, "--json", str(out)])
    assert rc == 2
    assert not out.exists()


# ---------------------------------------------------------------- DT3
def test_dt3_reuses_a_dt2_grade_only_of_the_same_views(tmp_path):
    want = {"corner": CORNER, "sha256": {"spef": "a"}}
    dt2 = tmp_path / "dt2.json"
    args = SimpleNamespace(
        dt2_json=str(dt2), timing_source=want, netlist="n.v",
        cut_netlist="c.v", flat_core="f.v", sta_netlist="absent.v",
        sdc="absent.sdc", spef="absent.spef", liberty="l.lib", top="t",
        clock="clk", dff_cells=None, k=4, timeout=10)
    dt2.write_text(json.dumps({"verdict": "PASS", "timing_source": want}))
    _blob, note = SDD._acquire_dt2(tmp_path, args, None)
    assert note.startswith("reused")
    dt2.write_text(json.dumps({"verdict": "PASS",
                               "timing_source": {"step": "direct"}}))
    blob, note = SDD._acquire_dt2(tmp_path, args, None)
    assert note.startswith("produced")
    assert blob["timing_source"]["corner"] == CORNER


# ---------------------------------------------------------------- runner
def _switch(project: Path, **steps) -> None:
    (project / "phase3").mkdir(parents=True, exist_ok=True)
    (project / "phase3/librelane_switch.json").write_text(
        json.dumps({"steps": steps}))


def test_direct_is_the_default(tmp_path):
    _stapostpnr(tmp_path)
    assert R.atpg_librelane_timing(tmp_path, "DT2") == ([], None)


def test_librelane_passes_the_state_and_the_max_ss_corner(tmp_path):
    state = _stapostpnr(tmp_path)
    _switch(tmp_path, DT2="librelane")
    argv, refusal = R.atpg_librelane_timing(tmp_path, "DT2")
    assert refusal is None
    assert argv == ["--librelane-state", str(state.relative_to(tmp_path)),
                    "--librelane-corner", CORNER]


def test_dual_and_a_missing_state_are_refused(tmp_path):
    _switch(tmp_path, DT2="dual", DT3="librelane")
    assert R.atpg_librelane_timing(tmp_path, "DT2")[0] is None
    argv, refusal = R.atpg_librelane_timing(tmp_path, "DT3")
    assert argv is None and "0 OpenROAD.STAPostPNR" in refusal


def test_the_runner_hands_the_views_to_the_producer(tmp_path, monkeypatch):
    state = _stapostpnr(tmp_path)
    _switch(tmp_path, DT2="librelane")
    sdc = tmp_path / R._ATPG_SDC_REL
    sdc.parent.mkdir(parents=True, exist_ok=True)
    sdc.write_text("create_clock -name clk -period 10 [get_ports clk]\n")
    cut = tmp_path / R._ATPG_CUT_REL
    cut.parent.mkdir(parents=True, exist_ok=True)
    cut.write_text("module cut(); endmodule\n")
    # DT1 already graded; a direct DT2 grade exists and must be re-graded
    for step, source in (("DT1", None), ("DT2", {"step": "direct"}),
                         ("DT3", None)):
        p = tmp_path / R._ATPG_COVERAGE_REL[step]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"verdict": "PASS", "timing_source": source}))
    calls = []

    def fake_run(cmd, **_kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(R._pr, "run", fake_run)
    R.run_at_speed_atpg_producers(tmp_path, [], [])
    assert len(calls) == 1 and calls[0][1].endswith(
        "path_delay_fault_atpg_run.py")
    i = calls[0].index("--librelane-state")
    assert calls[0][i:i + 4] == ["--librelane-state",
                                 str(state.relative_to(tmp_path)),
                                 "--librelane-corner", CORNER]


def test_a_selected_but_absent_state_is_disclosed_not_graded(tmp_path,
                                                             monkeypatch):
    _switch(tmp_path, DT2="librelane")
    sdc = tmp_path / R._ATPG_SDC_REL
    sdc.parent.mkdir(parents=True, exist_ok=True)
    sdc.write_text("create_clock -name clk -period 10 [get_ports clk]\n")
    cut = tmp_path / R._ATPG_CUT_REL
    cut.parent.mkdir(parents=True, exist_ok=True)
    cut.write_text("module cut(); endmodule\n")
    for step in ("DT1", "DT3"):
        p = tmp_path / R._ATPG_COVERAGE_REL[step]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"verdict": "PASS"}))
    calls = []
    monkeypatch.setattr(R._pr, "run",
                        lambda cmd, **_kw: calls.append(cmd))
    R.run_at_speed_atpg_producers(tmp_path, [], [])
    assert calls == []
    record = json.loads((tmp_path / R._ATPG_NOT_RUN_REL["DT2"]).read_text())
    assert "LibreLane timing views" in record["reason"]
    assert record["tool_attempted"] is False


def test_a_padded_core_is_levelised_with_every_corner_library():
    script = PDF._tdf._tdf_pre_flatten_script(
        ["/pdk/sc.lib", "/pdk/io.lib"], "cut.v", "top", "flat.v")
    assert script.splitlines()[:2] == [
        "read_liberty -ignore_miss_func /pdk/sc.lib",
        "read_liberty -ignore_miss_func /pdk/io.lib"]
    # one library, as DT1 passes it, is unchanged
    assert PDF._tdf._tdf_pre_flatten_script(
        "/pdk/sc.lib", "cut.v", "top", "flat.v").splitlines()[0] == \
        "read_liberty -ignore_miss_func /pdk/sc.lib"
