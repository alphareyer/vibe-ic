"""r34/R-0915-95 — the density fill closes a floor its square lattice could not.

MEASURED on subservient x gf180mcuD (r33's layout, metal fill re-run from a
stripped GDS, then the PDK's own sign-off DRC deck on the result):

    engine          metal2   metal3   sign-off DRC
    lattice only    0.2366   0.2436   M2.4, M3.4 (density below 0.30)
    + conformal     0.3135   0.3309   0 violations

The room the deck leaves for dummy metal (ceiling 0.3388 / 0.3534) was never
the limit; squares on a grid were. A first conformal draft closed density but
put pieces 0.46um apart (15 DM2.2b/DM3.2b violations) and a second repaired
spacing with skewed cuts (ACUTE/OFFGRID); the final engine was DRC-clean.

What is asserted, both directions, on layouts drawn here:
  * with a floor the lattice cannot reach, the conformal family is tried,
    wins, and clears the floor;
  * with no floor (the pre-fix config shape) nothing conformal runs and the
    layer stays below that same number;
  * a floor above drawn + all legal room is still refused as unreachable;
  * the emitted dummy geometry is legal by the deck's own terms: dummy space,
    dummy width, clearance to circuit metal, Manhattan and on the grid — on a
    seeded irregular layout whose room has necks and notches.

THREE LAYERS, so this file measures something wherever it runs. The lattice /
lane / repair-cut ARITHMETIC is pure and always executed. The GEOMETRY claims
need the engine, which is a KLayout script, so they run it through the flow's
own runner (`_klayout_launch.find_runner`); where no KLayout exists the same
tests assert the product's HONEST DEGRADATION instead — `metal_fill_emit`
DISCLOSES a named skip and never reports a fill it did not insert — and say so
in the report, because a claim nobody could measure is not a claim that passed.
chip-AGNOSTIC: every layout here is drawn by this file.
"""
from __future__ import annotations

import json
import os
import shutil
from unittest import mock
import sys
import tempfile
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import _klayout_launch as kl  # noqa: E402
import metal_fill_emit as mfe  # noqa: E402
sys.path.insert(0, str(PROGRAMS / "metal_fill"))
import metal_fill as mf  # noqa: E402

SPACE, SPACE_TO_METAL, GRID = 0.98, 2.0, 0.005

SCRIPT = r'''
import json, os, random, sys
sys.path.insert(0, os.environ["ENGINE_DIR"])
import pya, metal_fill
out = os.environ["OUT_DIR"]
ly = pya.Layout(); ly.dbu = 0.001
top = ly.create_cell("TOP"); li = ly.layer(36, 0)
u = lambda v: int(round(v / 0.001))
die = float(os.environ["DIE"])
if os.environ["SHAPE"] == "grid":
    x = 0.0
    while x < die:
        y = 0.0
        while y < die:
            top.shapes(li).insert(pya.Box(u(x), u(y), u(x + 1.0), u(y + 1.0)))
            y += 10.0
        x += 10.0
else:
    rnd = random.Random(20260917)
    for _ in range(260):
        w, h = rnd.choice((0.5, 1.0, 2.5, 6.0)), rnd.choice((0.5, 1.5, 4.0, 9.0))
        x, y = rnd.uniform(0, die - w), rnd.uniform(0, die - h)
        top.shapes(li).insert(pya.Box(u(round(x / 0.005) * 0.005), u(round(y / 0.005) * 0.005),
                                      u(round((x + w) / 0.005) * 0.005), u(round((y + h) / 0.005) * 0.005)))
top.shapes(li).insert(pya.Box(0, 0, u(1), u(1)))
top.shapes(li).insert(pya.Box(u(die - 1), u(die - 1), u(die), u(die)))
gin = os.path.join(out, "in.gds"); ly.write(gin)
cfg = {"boundary_layer": None, "window_um": None, "max_passes": 8, "mfg_grid_um": 0.005,
       "fill_datatype": None,
       "layers": [{"name": "m", "layer": [36, 0], "target": 0.35, "max": 0.95,
                   "space": 0.98, "space_to_metal": 2.0, "width": 3.37, "fill_datatype": 4}]}
if os.environ.get("FLOOR_PCT"):
    cfg["_derivation"] = {"density_floor_pct": float(os.environ["FLOOR_PCT"])}
gout = os.path.join(out, "out.gds")
rep = metal_fill.run(gin, cfg, gout, "TOP")
res = pya.Layout(); res.read(gout); t = res.top_cell()
drawn = pya.Region(t.begin_shapes_rec(res.find_layer(36, 0))).merged()
fl = res.find_layer(36, 4)
dummy = pya.Region(t.begin_shapes_rec(fl)).merged() if fl is not None else pya.Region()
off_grid = acute = 0
for poly in dummy.each():
    for pt in poly.each_point_hull():
        if pt.x % 5 or pt.y % 5:
            off_grid += 1
    for e in poly.each_edge():
        if e.dx() != 0 and e.dy() != 0:
            acute += 1
checks = {
    "dummy_space_violations": dummy.space_check(u(0.98)).count(),
    "dummy_width_violations": dummy.width_check(u(1.42)).count(),
    "dummy_to_circuit_violations": dummy.separation_check(drawn, u(2.0)).count(),
    "dummy_overlaps_circuit": (dummy & drawn).count(),
    "off_grid_vertices": off_grid, "non_manhattan_edges": acute,
    "dummy_empty": dummy.is_empty(),
}
json.dump({"report": rep, "checks": checks}, open(os.path.join(out, "result.json"), "w"))
'''


def _engine_absent_is_disclosed(tmp_path: Path) -> None:
    """With no KLayout runner the emitter DISCLOSES a named skip — it never
    reports a fill it did not insert.

    The emitter's own runner lookup is replaced for the call (never the
    process environment, which would leak into the next test), so this holds
    on a host that HAS KLayout too.
    """
    proj = tmp_path / "disclosed"
    (proj / "phase3/stage3/pnr").mkdir(parents=True)
    (proj / "phase3/stage3/pnr/x.gds").write_bytes(b"not a real stream")
    cfg = proj / "cfg.json"
    cfg.write_text(json.dumps({
        "boundary_layer": None, "window_um": None, "max_passes": 1,
        "mfg_grid_um": 0.005, "fill_datatype": None,
        "layers": [{"name": "m", "layer": [36, 0], "target": 0.35, "max": 0.95,
                    "space": 0.98, "space_to_metal": 2.0, "width": 3.37,
                    "fill_datatype": 4}]}))
    with mock.patch.object(mfe._kl, "find_runner", return_value=None):
        res = mfe.run(proj, str(proj / "phase3/stage3/pnr/x.gds"), str(cfg),
                      None, False, None, str(proj / "rep.json"))
    assert res["verdict"] == "DISCLOSED_SKIP", res
    assert "KLayout runner" in res["reason"]
    print("NOT_MEASURED: no KLayout runner here, so the fill GEOMETRY was not "
          "exercised; the disclosure contract was.")


def _run(tmp_path: Path, shape: str, die: float, floor_pct):
    """(layer record, geometry checks) — or None when no KLayout exists."""
    runner = kl.find_runner(os.environ.get("VIBEIC_EDA_CONTAINER"))
    if runner is None:
        _engine_absent_is_disclosed(tmp_path)
        return None
    work = tmp_path
    made = None
    if getattr(runner, "kind", "") == "container" and not runner.covers(str(tmp_path)):
        roots = [src for src, _ in kl._container_mounts(runner._c)
                 if os.path.isdir(src) and os.access(src, os.W_OK)]
        assert roots, "the KLayout container mounts no writable host path"
        made = Path(tempfile.mkdtemp(prefix="vibeic_fill_test_", dir=roots[0]))
        work = made
    try:
        shutil.copy2(PROGRAMS / "metal_fill" / "metal_fill.py", work / "metal_fill.py")
        script = work / "probe.py"
        script.write_text(SCRIPT)
        env = {"ENGINE_DIR": str(work), "OUT_DIR": str(work), "SHAPE": shape,
               "DIE": str(die)}
        if floor_pct is not None:
            env["FLOOR_PCT"] = str(floor_pct)
        rc, so, se = runner.run(script, env, path_keys=("ENGINE_DIR", "OUT_DIR"),
                                timeout=1800)
        result = work / "result.json"
        assert rc == 0 and result.is_file(), f"rc={rc}\n{so[-2000:]}\n{se[-2000:]}"
        data = json.loads(result.read_text())
    finally:
        if made is not None:
            shutil.rmtree(made, ignore_errors=True)
    return data["report"]["layers"][0], data["checks"]


def _legal(checks):
    assert not checks["dummy_empty"], "the fixture placed no dummy metal"
    for key in ("dummy_space_violations", "dummy_width_violations",
                "dummy_to_circuit_violations", "dummy_overlaps_circuit",
                "off_grid_vertices", "non_manhattan_edges"):
        assert checks[key] == 0, (key, checks)


def test_the_conformal_family_clears_a_floor_the_lattice_could_not(tmp_path):
    _res = _run(tmp_path, "grid", 200.0, 55.0)
    if _res is None:
        return
    layer, checks = _res
    lattice = [f["density"] for f in layer["families_tried"]
               if "conformal_tile_um" not in f]
    conformal = [f for f in layer["families_tried"] if "conformal_tile_um" in f]
    assert max(lattice) < 0.55, "fixture no longer reproduces a lattice shortfall"
    assert conformal and conformal[0]["density"] >= 0.55
    assert layer["density_after"] >= 0.55 and layer["below_floor"] is False
    _legal(checks)


def test_without_a_floor_nothing_conformal_runs(tmp_path):
    _res = _run(tmp_path, "grid", 200.0, None)
    if _res is None:
        return
    layer, checks = _res
    assert not any("conformal_tile_um" in f for f in layer["families_tried"])
    assert layer.get("conformal_residual") is None
    assert layer["density_after"] < 0.55
    _legal(checks)


def test_a_floor_above_every_legal_micron_is_still_unreachable(tmp_path):
    _res = _run(tmp_path, "grid", 200.0, 90.0)
    if _res is None:
        return
    layer, _checks = _res
    assert layer["floor_unreachable_by_any_fill"] is True
    assert layer.get("conformal_residual") is None
    assert not any("conformal_tile_um" in f for f in layer["families_tried"])


def test_irregular_room_is_filled_legally(tmp_path):
    _res = _run(tmp_path, "random", 150.0, 60.0)
    if _res is None:
        return
    layer, checks = _res
    assert any("conformal_tile_um" in f for f in layer["families_tried"])
    _legal(checks)


# --------------------- the arithmetic, wherever this runs -------------------

def test_the_tile_is_the_widest_square_the_engine_already_places():
    # 16x the configured width, on the grid, never under the winning lattice
    assert mf.conformal_tile(3370, 10825, 5) == 53920
    assert mf.conformal_tile(3370, 60000, 5) == 60000      # lattice is wider
    assert mf.conformal_tile(333, 0, 5) % 5 == 0           # snapped


def test_the_lanes_cut_the_room_at_one_space_per_tile():
    lanes = mf.conformal_lanes((0, 0, 200000, 200000), 53920, 980, 54900)
    vert = [b for b in lanes if b[3] - b[1] == 200000]
    horiz = [b for b in lanes if b[2] - b[0] == 200000]
    assert vert and horiz
    assert all(b[2] - b[0] == 980 for b in vert)           # lane == space
    assert all(b[3] - b[1] == 980 for b in horiz)
    assert vert[0][0] == 53920 // 2                        # a whole first tile
    assert vert[1][0] - vert[0][0] == 54900                # pitch = tile+space
    # a box smaller than one tile is cut by nothing
    assert mf.conformal_lanes((0, 0, 1000, 1000), 53920, 980, 54900) == []


def test_the_repair_cut_is_grown_by_space_and_snapped_outward():
    cut = mf.conformal_repair_cut((1002, 2003, 1500, 2500), 980, 5)
    assert all(v % 5 == 0 for v in cut), cut
    assert cut[0] <= 1002 - 980 and cut[1] <= 2003 - 980   # grown outward
    assert cut[2] >= 1500 + 980 and cut[3] >= 2500 + 980
    # on-grid input stays exactly one space wider on every side
    assert mf.conformal_repair_cut((1000, 2000, 1500, 2500), 980, 5) == (
        20, 1020, 2480, 3480)


def test_snap_up_never_lowers_and_lands_on_the_grid():
    assert mf.snap_up(1421, 5) == 1425 and mf.snap_up(1425, 5) == 1425
    assert mf.snap_up(7, 1) == 7
