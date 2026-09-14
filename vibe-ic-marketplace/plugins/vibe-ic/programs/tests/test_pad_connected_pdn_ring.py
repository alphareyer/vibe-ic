"""Pad-connected PDN rings are config-owned, geometry-fitted, and fail closed."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parent.parent.parent
PROGRAMS = PLUGIN / "programs"
sys.path.insert(0, str(PROGRAMS))
R = importlib.import_module("phase3_one_shot_runner")


CELL_LEF = """\
MACRO cellA
  CLASS CORE ;
  SIZE 2 BY 10 ;
  PIN PWR
    USE POWER ;
    PORT
      LAYER lower1 ;
        RECT 0 9.7 2 10.3 ;
    END
  END PWR
  PIN GND
    USE GROUND ;
    PORT
      LAYER lower1 ;
        RECT 0 -0.3 2 0.3 ;
    END
  END GND
END cellA
"""


TECH_LEF = """\
LAYER lower1
  TYPE ROUTING ;
  DIRECTION HORIZONTAL ;
  PITCH 0.5 ;
  WIDTH 0.2 ;
END lower1
LAYER upperV
  TYPE ROUTING ;
  DIRECTION VERTICAL ;
  PITCH 1.0 ;
  WIDTH 0.4 ;
END upperV
LAYER upperH
  TYPE ROUTING ;
  DIRECTION HORIZONTAL ;
  PITCH 1.0 ;
  WIDTH 0.4 ;
END upperH
"""


RING = {
    "layers": ["upperV", "upperH"],
    "widths": [0.8, 0.9],
    "spacings": [0.6, 0.7],
    "core_offset_um": 4.0,
    "connect_to_pad_layers": ["padFacing"],
    "connects": [["padFacing", "upperH"]],
    "min_clearance_um": 0.3,
}


def _pdk(tmp_path: Path, ring=RING):
    cell = tmp_path / "cells.lef"
    tech = tmp_path / "tech.lef"
    cell.write_text(CELL_LEF)
    tech.write_text(TECH_LEF)
    return R.PdkConfig(
        name="unit", liberty="/not/read.lib", tech_lef=str(tech),
        cell_lef=str(cell), cell_gds=None, site="site", drc_deck=None,
        metal_prefix="lower", tapcell_master=None,
        pdn_straps={
            "stripes": [
                {"layer": "upperV", "width": 0.8,
                 "pitch": 24.0, "offset": 3.0},
                {"layer": "upperH", "width": 0.9,
                 "pitch": 25.0, "offset": 3.2},
            ],
            "connects": [["lower1", "upperV"],
                         ["upperV", "upperH"]],
        },
        pdn_ring=ring,
    )


def test_ring_emission_measures_geometry_and_extends_only_primary_grid(tmp_path):
    tcl = R._build_pdn_tcl(_pdk(tmp_path))
    assert "getCoreArea" in tcl
    assert "getDbUnitsPerMicron" in tcl
    assert "string match \"PAD*\"" in tcl
    assert "string match \"PAD_POWER*\"" in tcl
    assert "PDN_PAD_RING_REFUSED" in tcl
    assert "PDN_PAD_RING_INERT" in tcl
    assert "PDN_PAD_RING_PLAN" in tcl
    assert ("define_pdn_grid -name grid -voltage_domains CORE "
            "-connect_to_pads -connect_to_pad_layers {padFacing}") in tcl
    assert ("add_pdn_ring -grid grid -layers {upperV upperH} "
            "-widths {0.8 0.9} -spacings {0.6 0.7}") in tcl
    assert ("add_pdn_connect -grid grid -layers {padFacing upperH}") in tcl
    primary = [line for line in tcl.splitlines()
               if "add_pdn_stripe" in line and "$_sec_pwr" not in line]
    secondary = [line for line in tcl.splitlines()
                 if "add_pdn_stripe" in line and "$_sec_pwr" in line]
    assert primary and all("${_vibeic_ring_extend}" in line
                           for line in primary)
    assert secondary and all("${_vibeic_ring_extend}" not in line
                             for line in secondary)


def test_ring_absence_preserves_ordinary_grid_and_no_extension(tmp_path):
    pdk = _pdk(tmp_path, ring=None)
    tcl = R._build_pdn_tcl(pdk)
    assert "  define_pdn_grid -name grid -voltage_domains CORE\n" in tcl
    assert "PDN_PAD_RING_" not in tcl
    assert "_vibeic_ring_extend" not in tcl
    assert "-connect_to_pads" not in tcl


@pytest.mark.parametrize(
    "bad",
    [
        {**RING, "core_offset_um": 0},
        {**RING, "connect_to_pad_layers": []},
        {**RING, "layers": ["upperV", "bad;command"]},
        {**RING, "widths": [0.8]},
    ],
)
def test_invalid_ring_config_refuses_during_generation(tmp_path, bad):
    with pytest.raises(ValueError, match="invalid pdn_ring"):
        R._build_pdn_tcl(_pdk(tmp_path, ring=bad))


def test_registry_ring_is_bound_to_the_real_pdk_entry():
    registry = json.loads((PROGRAMS / "pdk_registry.json").read_text())
    entries = [entry for entry in registry["pdks"]
               if entry.get("name") == "gf180mcuD"]
    assert len(entries) == 1
    ring = entries[0]["pdn_ring"]
    assert ring == {
        "layers": ["Metal4", "Metal5"],
        "widths": [1.6, 1.6],
        "spacings": [1.7, 1.7],
        "core_offset_um": 6.0,
        "connect_to_pad_layers": ["Metal2"],
        "connects": [["Metal2", "Metal5"], ["Metal2", "Metal4"]],
        "min_clearance_um": 0.46,
    }


def _self_tapeout(project: Path) -> None:
    """The delivery decides whether a ring pins the die (98109186b): a record
    an earlier run left on disk must not re-pin a floorplan to a ring THIS
    delivery does not build. These projects are self-tape-outs, which build
    their ring, so the record they write is the ring's own measurement."""
    st = project / "input" / "submission_template"
    st.mkdir(parents=True, exist_ok=True)
    (st / "SELF_TAPEOUT.txt").write_text("self tape-out\n", encoding="utf-8")

@pytest.mark.parametrize("with_ring", [False, True])
def test_pnr_reserves_pdn_space_before_floorplan(monkeypatch, tmp_path, with_ring):
    """Run the real step up to floorplan sizing, observing the inset it uses.

    A pad-depth-only inset left a 5.12 um gap in rbsub7. The PDK recipe
    requires 6 um core offset + 4.9 um rails + 0.46 um pad clearance.
    The control retains the historical pad inset when no PDN ring is declared.
    """
    pdk = _pdk(tmp_path, ring=None)
    registry = json.loads((PROGRAMS / "pdk_registry.json").read_text())
    cfg = next(e for e in registry["pdks"] if e["name"] == "gf180mcuD")["pdn_ring"]
    pdk.pdn_ring = cfg if with_ring else None
    netlist = tmp_path / "dut.v"
    netlist.write_text("module dut(input clk, output q); assign q=clk; endmodule\n")
    rec = tmp_path / "reports/phase3/io_pad_chip_top.json"
    rec.parent.mkdir(parents=True)
    rec.write_text(json.dumps({"verdict": "WROTE", "die_required_um": {
        "ring_depth_um": 381.0, "die_side_um": 1962.0}}))
    _self_tapeout(tmp_path)
    monkeypatch.setattr(R, "pnr_input_netlist", lambda *a: (netlist, "test DUT", False))
    monkeypatch.setattr(R, "_v1_6_599_check_wrapper_pin_order_cfg", lambda *a: None)
    monkeypatch.setattr(R, "_stage_via_legalized_tech_lef", lambda *a: {"status": "NOT_NEEDED"})
    monkeypatch.setattr(R, "set_invocation_provenance_sink", lambda *a: None)
    monkeypatch.setattr(R, "_macro_supply_preroute_decision", lambda *a, **kw: None)
    monkeypatch.setattr(R, "step_io_pad_chip_top_gen", lambda *a: R.StepResult("io", "PASS", 0, "test record"))
    monkeypatch.setattr(R, "_resolve_staged_silicon_sdc", lambda *a: None)
    monkeypatch.setattr(R, "_liberty_drv_limits", lambda *a: {})
    monkeypatch.setattr(R, "_build_auto_silicon_sdc", lambda *a, **kw: "")
    monkeypatch.setattr(R, "_seal_ring_core_margin_um", lambda *a: (None, {}))
    monkeypatch.setattr(R, "_slot_geometry", lambda *a: None)

    class AtFloorplan(Exception):
        pass

    observed = {}

    def capture(die_w, die_h, core_pad, *args):
        observed.update(die=(die_w, die_h), core_pad=core_pad)
        raise AtFloorplan

    monkeypatch.setattr(R, "seal_ring_die_and_core", capture)
    with pytest.raises(AtFloorplan):
        R.step_pnr(tmp_path, "dut", pdk, "unused", "1962x1962", 0.4)
    assert observed["die"] == (1962, 1962)
    assert observed["core_pad"] == (393 if with_ring else 381)


def test_pdn_space_tracks_both_orientations_without_chip_names(tmp_path):
    rec = tmp_path / "reports/phase3/io_pad_chip_top.json"
    rec.parent.mkdir(parents=True)
    rec.write_text(json.dumps({"verdict": "WROTE", "die_required_um": {
        "ring_depth_um": 80.0}}))
    _self_tapeout(tmp_path)
    # Deliberately unrelated layer names, widths and pad dimensions.
    pdk = _pdk(tmp_path)
    inset, why = R._padring_core_inset_um(tmp_path, pdk)
    assert why == ""
    assert inset == pytest.approx(86.8)  # 80 + 4 + (2*0.9+0.7) + 0.3
    pdk.pdn_ring = {**RING, "widths": [2.0, 0.9]}
    wider, why = R._padring_core_inset_um(tmp_path, pdk)
    assert why == "" and wider == pytest.approx(88.9)


def test_invalid_ring_cannot_supply_a_floorplan_inset(tmp_path):
    rec = tmp_path / "reports/phase3/io_pad_chip_top.json"
    rec.parent.mkdir(parents=True)
    rec.write_text(json.dumps({"verdict": "WROTE", "die_required_um": {
        "ring_depth_um": 80.0}}))
    _self_tapeout(tmp_path)
    pdk = _pdk(tmp_path, ring={**RING, "widths": [0.8]})
    inset, why = R._padring_core_inset_um(tmp_path, pdk)
    assert inset is None and "invalid pdn_ring" in why
