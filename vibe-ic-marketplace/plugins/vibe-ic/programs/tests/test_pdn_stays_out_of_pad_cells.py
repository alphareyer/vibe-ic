"""The core PDN grid must not drop vias or foreign-layer straps inside a pad cell.

MEASURED on spm x gf180mcuD, DIE route (v1.22.3, image sha256:89a8fd72...):
`-connect_to_pads` + `add_pdn_connect {Metal2 Metal4}` started a Metal4 grid
stripe, a Metal3 DRCFILL and twelve via2/via3 arrays 4.7 um inside the supply
pad `gf180mcu_fd_io__dvdd`, whose LEF obstructs Metal3/Metal4 there. Sign-off
DRC reported V3.1 x48 + V3.2a x85 in that one spot and Magic's extraction 139
illegal obsm3/obsm4 overlaps, which BLOCKED LVS before any compare.

After `pdngen` the flow now removes special-net vias overlapping a placed pad
and cuts foreign-layer straps back to the pad boundary; the declared pad-layer
connection is left exactly as drawn. Run here in real OpenROAD on a tiny
design, both directions: the emitted block changes precisely the intruding
shapes, and without it the intruding shapes are all still there.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))
import phase3_one_shot_runner as R  # noqa: E402

TECH = """VERSION 5.8 ;
UNITS DATABASE MICRONS 1000 ; END UNITS
MANUFACTURINGGRID 0.005 ;
LAYER PADL TYPE ROUTING ; DIRECTION VERTICAL ; PITCH 1 ; WIDTH 0.2 ; END PADL
LAYER CUT1 TYPE CUT ; END CUT1
LAYER GRIDL TYPE ROUTING ; DIRECTION VERTICAL ; PITCH 1 ; WIDTH 0.2 ; END GRIDL
VIA V12 DEFAULT
  LAYER PADL ; RECT -0.5 -0.5 0.5 0.5 ;
  LAYER CUT1 ; RECT -0.2 -0.2 0.2 0.2 ;
  LAYER GRIDL ; RECT -0.5 -0.5 0.5 0.5 ;
END V12
MACRO PADCELL
  CLASS PAD POWER ;
  ORIGIN 0 0 ;
  SIZE 20 BY 20 ;
  PIN VDD DIRECTION INOUT ; USE POWER ;
    PORT LAYER PADL ; RECT 8 18 12 20 ; END
  END VDD
  OBS LAYER GRIDL ; RECT 0 0 20 19 ; END
END PADCELL
END LIBRARY
"""

# pad at (0,0)-(20,20); everything else in the core above y=20
DEF = """VERSION 5.8 ;
DESIGN top ;
UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( 0 0 ) ( 100000 100000 ) ;
COMPONENTS 1 ;
- u_pad PADCELL + FIXED ( 0 0 ) N ;
END COMPONENTS
SPECIALNETS 1 ;
- VDD ( u_pad VDD ) + USE POWER
  + ROUTED PADL 4000 + SHAPE STRIPE ( 10000 5000 ) ( 10000 60000 )
  NEW GRIDL 2000 + SHAPE STRIPE ( 10000 15000 ) ( 10000 90000 )
  NEW GRIDL 2000 + SHAPE STRIPE ( 2000 10000 ) ( 18000 10000 )
  NEW GRIDL 2000 + SHAPE STRIPE ( 30000 15000 ) ( 30000 90000 )
  NEW PADL 0 + SHAPE STRIPE ( 10000 16000 ) V12
  NEW PADL 0 + SHAPE STRIPE ( 10000 50000 ) V12 ;
END SPECIALNETS
END DESIGN
"""


def test_the_clip_rides_the_pad_ring_grid_only():
    tcl = R._pdn_pad_footprint_clip_tcl(["Metal2"])
    assert "if {$_vibeic_pad_ring_active}" in tcl
    assert "{Metal2}" in tcl and "odb::dbSBox_destroy" in tcl
    assert "PDN_PAD_FOOTPRINT_CLIP:" in tcl


_CELL_LEF = """MACRO cellA
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

_TECH_LEF = """LAYER lower1
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
"""


def _pdk(tmp_path, ring):
    cell, tech = tmp_path / "c.lef", tmp_path / "t.lef"
    cell.write_text(_CELL_LEF)
    tech.write_text(_TECH_LEF)
    return R.PdkConfig(
        name="unit", liberty="/x.lib", tech_lef=str(tech), cell_lef=str(cell),
        cell_gds=None, site="site", drc_deck=None, metal_prefix="lower",
        tapcell_master=None,
        pdn_straps={"stripes": [{"layer": "upperV", "width": 0.8,
                                 "pitch": 24.0, "offset": 3.0}],
                    "connects": [["lower1", "upperV"]]},
        pdn_ring=ring)


def test_the_clip_follows_pdngen_when_a_ring_is_configured(tmp_path):
    ring = {"layers": ["upperV", "upperH"], "widths": [0.8, 0.9],
            "spacings": [0.6, 0.7], "core_offset_um": 4.0,
            "connect_to_pad_layers": ["padFacing"],
            "connects": [["padFacing", "upperH"]], "min_clearance_um": 0.3}
    tcl = R._build_pdn_tcl(_pdk(tmp_path, ring))
    assert tcl.index("  pdngen\n") < tcl.index("PDN_PAD_FOOTPRINT_CLIP:")
    assert "kept_layers=padFacing" in tcl
    assert "PDN_PAD_FOOTPRINT_CLIP" not in R._build_pdn_tcl(_pdk(tmp_path, None))


def _shapes(def_text):
    body = def_text.split("SPECIALNETS", 1)[1]
    wires = sorted(re.findall(
        r"(PADL|GRIDL) (\d+) \+ SHAPE STRIPE \( (\d+) (\d+) \) \( (\d+|\*) (\d+|\*) \)",
        body))
    vias = sorted(re.findall(r"\( (\d+) (\d+) \) V12", body))
    return wires, vias


@pytest.mark.skipif(shutil.which("openroad") is None, reason="needs OpenROAD")
@pytest.mark.parametrize("with_clip", [True, False])
def test_openroad_removes_only_what_intrudes_into_the_pad(tmp_path, with_clip):
    (tmp_path / "t.lef").write_text(TECH)
    (tmp_path / "in.def").write_text(DEF)
    clip = R._pdn_pad_footprint_clip_tcl(["PADL"]) if with_clip else ""
    (tmp_path / "run.tcl").write_text(
        f"read_lef {tmp_path/'t.lef'}\nread_def {tmp_path/'in.def'}\n"
        "set _vibeic_pad_ring_active 1\n" + clip
        + f"write_def {tmp_path/'out.def'}\nexit\n")
    r = subprocess.run(["openroad", "-no_init", "-exit", str(tmp_path / "run.tcl")],
                       capture_output=True, text=True, timeout=300)
    assert (tmp_path / "out.def").is_file(), r.stdout + r.stderr
    wires, vias = _shapes((tmp_path / "out.def").read_text())
    ys = {(layer, int(x0), int(y0)) for layer, _w, x0, y0, _x1, _y1 in wires}
    if with_clip:
        assert "PDN_PAD_FOOTPRINT_CLIP: vias_removed=1 wires_clipped=1 wires_dropped=1" in r.stdout, r.stdout
        assert vias == [("10000", "50000")]                 # the core via stays
        assert ("PADL", 10000, 5000) in ys                   # pad layer untouched
        assert ("GRIDL", 10000, 20000) in ys                 # cut to the pad edge
        assert ("GRIDL", 10000, 15000) not in ys
        assert not any(layer == "GRIDL" and y0 == 10000 for layer, _x, y0 in ys)
        assert ("GRIDL", 30000, 15000) in ys                 # outside the pad
    else:
        assert ("10000", "16000") in vias
        assert ("GRIDL", 10000, 15000) in ys
