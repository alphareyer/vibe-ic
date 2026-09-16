"""R-0915-87(3): the delivered hardmacro Liberty carries timing arcs from the
run's OWN post-route STA, or the step is NOT_MEASURED by name.

MEASURED on subservient r27: the kit's `subservient.lib` had 0 `timing (`
groups and `digital_hardmacro_check` returned rc 0 as PASS_TIMING_UNCHARACTERISED.
OpenSTA `write_timing_model` over the same run's post-route netlist + max SPEF +
SDC + the SETUP-corner library named in `sta_mcorner_ocv.rpt` produced 41 arcs
(9 setup_rising, 9 hold_rising, 21 rising_edge, 2 clock-tree); with the
DEF-derived pg_pins added, the gen step PASSed, the check PASSed, and step
37.5ip's release docs derived one more field than r27's (70 vs 69).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import digital_hardmacro_gen as G  # noqa: E402

RPT = ("=== SETUP corner: process=SS liberty=/pdk/ss.lib, SPEF=d.max.spef ===\n"
       "STA_BASIS: POST_ROUTE_SPEF\n"
       "=== HOLD corner: process=FF liberty=/pdk/ff.lib, SPEF=d.min.spef ===\n")
ETM = ('library (d) {\n  cell ("d") {\n    pin ("clk") { direction : input ; }\n'
       '    pin ("q") { direction : output ;\n      timing () { related_pin : "clk" ;\n'
       '        timing_type : rising_edge ; }\n    }\n  }\n}\n')


def _inputs(p: Path, rpt: str = RPT) -> None:
    pnr = p / "phase3/stage3/pnr"; pnr.mkdir(parents=True)
    (pnr / "d_pnr.v").write_text("module d; endmodule\n")
    (pnr / "constraint.sdc").write_text("create_clock -period 10 clk\n")
    sp = p / "phase3/stage3/extracted/spef_corners"; sp.mkdir(parents=True)
    (sp / "d.max.spef").write_text("*SPEF\n")
    r = p / "reports/phase3"; r.mkdir(parents=True)
    (r / "sta_mcorner_ocv.rpt").write_text(rpt)


def test_missing_post_route_inputs_are_blocked_by_upstream_and_run_nothing(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(G, "_sh", lambda *a, **k: calls.append(a) or (0, "", ""))
    text, rec = G.characterise_liberty(tmp_path, "d", "", tmp_path / "hm")
    assert text is None and rec["reason_class"] == "BLOCKED_BY_UPSTREAM"
    assert "d_pnr.v" in rec["why"] and calls == []


def test_a_report_naming_no_setup_corner_is_blocked(tmp_path, monkeypatch):
    _inputs(tmp_path, rpt="STA_BASIS: POST_ROUTE_SPEF\n")
    monkeypatch.setattr(G, "_sh", lambda *a, **k: (0, "", ""))
    text, rec = G.characterise_liberty(tmp_path, "d", "", tmp_path / "hm")
    assert text is None and rec["reason_class"] == "BLOCKED_BY_UPSTREAM"


def _fake_sta(out_text: str, rc: int = 0):
    def run(argv, *a, **k):
        tcl = Path(argv[-1].split()[-1])
        lib_out = Path(tcl.read_text().split()[-1])
        if out_text:
            lib_out.write_text(out_text)
        return rc, "", "sta said no" if rc else ""
    return run


def test_arcs_from_the_runs_own_sta_are_delivered(tmp_path, monkeypatch):
    _inputs(tmp_path)
    monkeypatch.setattr(G, "_sh", _fake_sta(ETM))
    monkeypatch.setattr(G.shutil, "which", lambda n: "/usr/bin/sta")
    text, rec = G.characterise_liberty(tmp_path, "d", "", tmp_path / "hm")
    assert rec["characterised"] is True and rec["arcs"] == 1
    assert rec["liberty"] == "/pdk/ss.lib", "must be the SETUP corner the STA used"
    assert "CHARACTERISED" in text and "timing ()" in text
    assert not list((tmp_path / "hm").glob(".d_etm*")), "scratch left behind"


def test_zero_arcs_or_tool_failure_is_an_execution_error(tmp_path, monkeypatch):
    _inputs(tmp_path)
    monkeypatch.setattr(G.shutil, "which", lambda n: "/usr/bin/sta")
    for fake in (_fake_sta(ETM.replace("timing ()", "nothing ()")), _fake_sta("", rc=1)):
        monkeypatch.setattr(G, "_sh", fake)
        text, rec = G.characterise_liberty(tmp_path, "d", "", tmp_path / "hm")
        assert text is None and rec["reason_class"] == "EXECUTION_ERROR", rec


def test_pg_pins_are_carried_from_use_not_direction():
    pins = [G.Pin(name="VDD", direction="INPUT", is_pg=True, use="POWER"),
            G.Pin(name="VSS", direction="INPUT", is_pg=True, use="GROUND"),
            G.Pin(name="clk", direction="INPUT", is_pg=False, use="SIGNAL")]
    out = G.liberty_with_pg_pins(ETM, "d", pins)
    assert "pg_pin (VDD) { pg_type : primary_power ; }" in out
    assert "pg_pin (VSS) { pg_type : primary_ground ; }" in out
    assert out.count("timing ()") == 1


def test_no_cell_group_refuses_rather_than_dropping_the_rails():
    pins = [G.Pin(name="VDD", direction="INPUT", is_pg=True, use="POWER")]
    assert G.liberty_with_pg_pins("library (d) { }\n", "d", pins) is None
