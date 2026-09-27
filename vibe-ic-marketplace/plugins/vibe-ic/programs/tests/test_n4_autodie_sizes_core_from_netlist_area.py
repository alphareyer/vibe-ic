#!/usr/bin/env python3
"""N4 — `--die-um auto` sizes the CORE from the cells the netlist HAS.

MEASURED DEFECT (lane cmpb, 2026-09-28, image 0.3.83; spm x gf180mcuD as a
HARDMACRO, the same RTL fed to LibreLane and ORFS):

    die-um=auto -> 85x85 (cells=273, avg_cell=13.17um2 [site-LEF], target_util=0.5)
    [INFO IFP-0104] Effective utilization: 2.193
    [ERROR PDN-0185] Insufficient width (64.40 um) to add straps on layer Metal4
    PDN_GRID_EMPTY -> pnr rc=1, no layout

Three faults, one per test group below:
  1. the mean cell was `site area x 6.0` — a sky130-hd width in sites, a PDK
     constant in chip-agnostic code. gf180 7t: 0.56 x 3.92 x 6 = 13.17 um^2;
     this netlist's own synthesis stat: 8857.632 um^2 / 273 = 32.4 um^2.
  2. that area sized the DIE and the 10 um inset was then cut out of it, so the
     core carried 2.19x its cells.
  3. nothing checked that the core holds one period of the PDK's power straps
     (Metal4 pitch 153.6 um), so pdngen built no grid.

The rule now: the area is READ (the synthesis stat bound to this netlist's
sha256, else every instance's own LEF SIZE); the CORE is sized from it at the
target utilisation; the die is the core plus the inset; the core is grown to
the strap floor when auto, and a pinned core below it is REFUSED with numbers.

chip-, PDK- and vendor-AGNOSTIC: every fixture below is synthetic.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROG))

import phase3_one_shot_runner as R  # noqa: E402
import _yosys_stat as YS  # noqa: E402
from _ppa import power as PP  # noqa: E402

# A 7-track-shaped library: row site 0.56 x 3.92 (2.1952 um^2). The site x 6.0
# guess gives 13.17 um^2/cell; the masters below are all wider than that.
_SITE_W, _SITE_H = 0.56, 3.92
_MASTERS = {  # name: (width, count)
    "lib__dffrnq_1": (18.48, 64),
    "lib__nand2_1": (2.24, 120),
    "lib__xor2_1": (5.60, 89),
}


def _cell_lef() -> str:
    out = ["VERSION 5.7 ;",
           "SITE coresite", "  CLASS CORE ;",
           f"  SIZE {_SITE_W} BY {_SITE_H} ;", "END coresite"]
    for name, (w, _n) in _MASTERS.items():
        out += [f"MACRO {name}", "  CLASS CORE ;", "  SITE coresite ;",
                f"  SIZE {w} BY {_SITE_H} ;", f"END {name}"]
    return "\n".join(out + ["END LIBRARY", ""])


def _true_area() -> float:
    return sum(w * _SITE_H * n for w, n in _MASTERS.values())


def _cells() -> int:
    return sum(n for _w, n in _MASTERS.values())


def _stage(tmp_path: Path, *, stats: str = "bound", masters=None):
    synth = tmp_path / "phase2" / "stage2" / "synth"
    synth.mkdir(parents=True)
    nl = synth / "top_synth.v"
    lines = ["module top (a, y);", "  input a;", "  output y;"]
    i = 0
    for name, (_w, n) in (masters or _MASTERS).items():
        for _ in range(n):
            lines.append(f"  {name} u{i} (.A(a), .Z(y));")
            i += 1
    nl.write_text("\n".join(lines + ["endmodule", ""]))
    lef = tmp_path / "cells.lef"
    lef.write_text(_cell_lef())
    if stats != "none":
        rec = {"schema": "vibe-ic/synth-stats/1",
               "netlist": "phase2/stage2/synth/top_synth.v",
               YS.NETLIST_DIGEST_FIELD: YS.netlist_digest(nl),
               "chip_area": round(_true_area(), 4), "chip_area_unit": "um^2",
               "chip_area_unit_evidence": {"established": True},
               "cell_count": _cells()}
        if stats == "unbound":
            rec[YS.NETLIST_DIGEST_FIELD] = "sha256:" + "0" * 64
        if stats == "unit_unproven":
            rec["chip_area_unit"] = "cell-library area unit"
            rec["chip_area_unit_evidence"] = {"established": False}
        (synth / "stats.json").write_text(json.dumps(rec))

    class _Pdk:
        name = "testpdk"
        site = "coresite"
        cell_lef = str(lef)
        tech_lef = str(tmp_path / "absent.tlef")
    return nl, _Pdk()


def _side(die: str) -> int:
    w, h = (int(x) for x in die.lower().split("x"))
    assert w == h
    return w


# ── 1+2: the area is READ and it sizes the CORE ─────────────────────────────

def test_the_core_is_sized_from_the_synthesis_stat_bound_to_this_netlist(tmp_path):
    """RED on main: main sizes an 8x-ish smaller die from site x 6.0."""
    nl, pdk = _stage(tmp_path)
    m: dict = {}
    die, note = R._resolve_auto_die_um("auto", nl, 0.3, pdk, None, top="",
                                       container="", metrics=m)
    core = math.ceil(math.sqrt(_true_area() / R._AUTO_DIE_TARGET_UTIL))
    assert _side(die) == core + 2 * R._AUTO_DIE_CORE_INSET_UM, (die, note)
    assert "synthesis stat" in note, note
    assert m["cell_area_um2"] == pytest.approx(_true_area(), rel=1e-6)
    assert m["core_side_um"] == core
    # the utilisation is the CORE's and it is at or under the target
    assert _true_area() / (core * core) <= R._AUTO_DIE_TARGET_UTIL + 1e-9


def test_a_stat_for_another_netlist_is_not_this_designs_area(tmp_path):
    """The stat's sha256 names a different netlist -> the instances' own LEF
    SIZEs are summed instead, and the note says so."""
    nl, pdk = _stage(tmp_path, stats="unbound")
    die, note = R._resolve_auto_die_um("auto", nl, 0.3, pdk, None, container="")
    core = math.ceil(math.sqrt(_true_area() / R._AUTO_DIE_TARGET_UTIL))
    assert _side(die) == core + 2 * R._AUTO_DIE_CORE_INSET_UM, (die, note)
    assert "LEF SIZE" in note and "synthesis stat" not in note, note


def test_an_unproven_area_unit_is_not_read_as_um2(tmp_path):
    nl, pdk = _stage(tmp_path, stats="unit_unproven")
    _die, note = R._resolve_auto_die_um("auto", nl, 0.3, pdk, None, container="")
    assert "LEF SIZE" in note, note


def test_no_per_pdk_average_cell_constant_is_left_in_the_flow():
    assert not hasattr(R, "_AUTO_DIE_AVG_SITES_PER_CELL")
    src = (PROG / "phase3_one_shot_runner.py").read_text()
    assert "site_area * " not in src.split("def _resolve_auto_die_um", 1)[1][:9000]


def test_the_measured_spm_numbers_land_where_librelane_did():
    """The measured case, as arithmetic: 8857.632 um^2 at the design's declared
    0.5 is a 134 um core and a 154 um die (LibreLane on the same RTL: 152.7 um
    at FP_CORE_UTIL 50). The old sizing gave 85 um."""
    assert R._auto_die_side_um(273, 0.5, 8857.632 / 273, min_side=1) == 134
    old = R._auto_die_side_um(273, 0.5, _SITE_W * _SITE_H * 6.0)
    assert old == 85  # the defect, reproduced from its own inputs


# ── 3: the core holds one strap period, or the run says why not ────────────

_GF_STRAPS = [{"layer": "Metal4", "width": 1.6, "pitch": 153.6, "offset": 16.32},
              {"layer": "Metal5", "width": 1.6, "pitch": 153.18, "offset": 16.65}]


def test_the_strap_floor_matches_the_measured_pdngen_boundary():
    """CALIBRATED in image 0.3.83 (OpenROAD 26Q3-3002) with these two stripes:
    row extent 94.64 refused (Metal4 needs 94.72), 94.08 refused (Metal5 needs
    94.84), 99.68 x 98.0 built. The floor must sit above every refused core
    and at or below a core that, snapped to rows, builds."""
    side, basis = PP.pdn_strap_min_core_span_um(_GF_STRAPS, nets=2,
                                                site_dims_um=(_SITE_W, _SITE_H))
    assert "94.84" in basis and "Metal5" in basis, basis
    for refused_core in (65, 80, 90, 94, 95, 96, 97):
        assert side > refused_core
    # snapped to whole rows/sites the floor core clears both needs
    assert math.floor(side / _SITE_H) * _SITE_H - _SITE_H >= 94.84
    assert math.floor(side / _SITE_W) * _SITE_W - _SITE_W >= 94.72
    assert side == 102


def test_no_strap_is_no_floor():
    assert PP.pdn_strap_min_core_span_um([], nets=2)[0] is None
    assert PP.pdn_strap_min_core_span_um([{"layer": "m", "width": 0}])[0] is None


def test_an_auto_core_below_the_strap_floor_is_grown_and_says_so(tmp_path):
    """RED on main (no strap floor exists). A small design at the target util
    would get a core pdngen cannot strap; it is grown to the floor."""
    small = {"lib__nand2_1": (2.24, 30)}
    nl, pdk = _stage(tmp_path, stats="none", masters=small)
    m: dict = {}
    die, note = R._resolve_auto_die_um(
        "auto", nl, 0.3, pdk, None, container="", metrics=m,
        strap_core_floor=(102, "Metal5 offset 16.65 + ... = 94.84 um"))
    assert _side(die) == 102 + 2 * R._AUTO_DIE_CORE_INSET_UM, (die, note)
    assert "PDN_CORE_GROWN" in note and "94.84" in note, note
    assert m["core_side_um"] == 102


def test_a_core_already_above_the_floor_is_not_touched(tmp_path):
    nl, pdk = _stage(tmp_path)
    d0, _ = R._resolve_auto_die_um("auto", nl, 0.3, pdk, None, container="")
    d1, n1 = R._resolve_auto_die_um("auto", nl, 0.3, pdk, None, container="",
                                    strap_core_floor=(102, "b"))
    assert d0 == d1 and "PDN_CORE_GROWN" not in n1


def test_the_downsize_retry_cannot_shrink_below_the_floor_die():
    """The over-sparse retry takes a floor; step_pnr hands it the strap floor
    plus both insets, so a later tightening cannot undo the growth."""
    assert R._compute_downsized_die(209, 209, 5.0, die_min_um=122) is None
    assert R._compute_downsized_die(209, 209, 20.0, die_min_um=122) is not None


def test_a_pinned_core_below_the_strap_floor_is_refused_with_numbers(
        tmp_path, monkeypatch):
    """RED on main: main runs on to pdngen, which builds no grid. DRIVES
    step_pnr itself — hermetic: no container, the strap floor supplied."""
    nl, pdk_stub = _stage(tmp_path)
    pdk = R.PdkConfig(name="testpdk", liberty=str(tmp_path / "x.lib"),
                      tech_lef=str(tmp_path / "absent.tlef"),
                      cell_lef=pdk_stub.cell_lef, cell_gds=None,
                      site="coresite", drc_deck=None)
    monkeypatch.setattr(R, "pnr_input_netlist",
                        lambda project, top: (nl, "fixture netlist", False),
                        raising=True)
    monkeypatch.setattr(R, "_strap_plan_core_floor",
                        lambda pdk, container="": (102, "Metal5 needs 94.84 um"),
                        raising=False)
    res = R.step_pnr(tmp_path, "top", pdk, "", "90x90", 0.3)
    assert res.status == "FAIL", res
    assert "PDN_CORE_TOO_SMALL" in str(res.detail), res.detail
    assert "70x70" in str(res.detail) and "102x102" in str(res.detail), res.detail
    assert "--die-um 90x90 (explicit)" in str(res.detail), res.detail
