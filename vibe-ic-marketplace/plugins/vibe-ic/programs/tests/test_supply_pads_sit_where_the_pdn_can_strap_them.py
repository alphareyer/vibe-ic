#!/usr/bin/env python3
"""The supply pair is placed on an edge the PDN can strap it to the core from.

MEASURED (subservient x gf180mcuD, OpenROAD 26Q3-2963): the chip-top producer
put the supply pair on the shortest signal edge, which was the WEST column.
The ground cell's only core-facing rail pins are 1.0 um deep on a VERTICAL
layer, so the strap pdngen draws from them to the core ring runs against that
layer's direction. pdngen built all six straps and cut every one away. No
ground source was left for the core: `Checker.PowerGridViolations` failed with
1,697,622 `PSM-0069` violations on VSS and none on VDD. Moving only that cell
to the south or north row connected it, and so did deepening its pins to
2.0 um on the west. It failed on the east as on the west.

The synthetic library below has the same property and names nothing real:
the supply cells' core-facing rail pins are on a layer routed VERTICALLY, and
the west edge carries the fewest signals. The pair has to go to the shortest
edge whose strap runs in that direction.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
GEN = PROGRAMS / "io_pad_chip_top_gen.py"
sys.path.insert(0, str(PROGRAMS))

DOC = """# External Interface

The I/O cell library is delegated to the PDK i/o pad defaults.

## Physical Pad Placement

| Pad side | signals |
|---|---|
| South (S) | `a[1:0]` |
| East (E) | `b[1:0]` |
| North (N) | `c[2:0]` |
| West (W) | `q` |
"""

SPEC = {"top_module": "core", "top_ports": [
    {"name": "a", "direction": "input", "width": 2, "msb": 1, "lsb": 0},
    {"name": "b", "direction": "input", "width": 2, "msb": 1, "lsb": 0},
    {"name": "c", "direction": "input", "width": 3, "msb": 2, "lsb": 0},
    {"name": "q", "direction": "output", "width": 1},
]}

TECH_LEF = """VERSION 5.8 ;
LAYER lv
  TYPE ROUTING ;
  DIRECTION VERTICAL ;
  PITCH 0.5 ;
  WIDTH 0.2 ;
END lv
LAYER lh
  TYPE ROUTING ;
  DIRECTION HORIZONTAL ;
  PITCH 0.5 ;
  WIDTH 0.2 ;
END lh
END LIBRARY
"""


def _pin(name: str, use: str, rects) -> list[str]:
    lines = [f"  PIN {name}", "    DIRECTION INOUT ;", f"    USE {use} ;",
             "    PORT"]
    for layer, rect in rects:
        lines += [f"      LAYER {layer} ;",
                  "        RECT " + " ".join(f"{v:.3f}" for v in rect) + " ;"]
    return lines + ["    END", f"  END {name}"]


def _macro(name: str, cls: str, pins=(), width=10) -> str:
    lines = [f"MACRO {name}", f"  CLASS {cls} ;",
             f"  SIZE {width}.000 BY 100.000 ;"]
    for pin, use, rects in pins:
        lines += _pin(pin, use, rects)
    return "\n".join(lines + [f"END {name}", ""])


def _supply_cell(name: str, external: str, use: str, face_layer: str) -> str:
    """A supply bridge whose EXTERNAL rail alone reaches the core-facing edge
    (y = 100 in the master frame), on `face_layer`. Its other two rails run
    the width of the cell as abutment rails and reach no core-facing edge."""
    facing = [(face_layer, (1.0, 99.0, 4.0, 100.0)),
              (face_layer, (6.0, 99.0, 9.0, 100.0))]
    ring = {"IOVDD": ("POWER", (0.0, 80.0, 10.0, 84.0)),
            "IOVSS": ("GROUND", (0.0, 86.0, 10.0, 90.0))}
    core = ("VDD", "POWER") if use == "GROUND" else ("VSS", "GROUND")
    pins = [(pin, pin_use, facing if pin == external else [("lh", rect)])
            for pin, (pin_use, rect) in ring.items()]
    pins.append((core[0], core[1], [("lh", (0.0, 70.0, 10.0, 74.0))]))
    return _macro(name, "PAD POWER", pins)


def _tree(tmp_path: Path, face_layer: str = "lv",
          ground_face_layer: str | None = None) -> tuple[Path, Path]:
    project = tmp_path / "project"
    (project / "input/docs").mkdir(parents=True)
    (project / "input/docs/L3.md").write_text(DOC)
    (project / "phase1/generated_docs").mkdir(parents=True)
    (project / "phase1/generated_docs/L9_INTEGRATION_SPEC.json").write_text(
        json.dumps(SPEC))

    root = tmp_path / "pdk"
    lefdir = root / "testpdk/libs.ref/test_io/lef"
    lefdir.mkdir(parents=True)
    text = (
        "SITE test_io_site\n  CLASS PAD ;\n  SIZE 1 BY 100 ;\nEND test_io_site\n"
        "SITE test_io_corner_site\n  CLASS PAD ;\n  SIZE 40 BY 40 ;\nEND test_io_corner_site\n"
        + _macro("test_io__in", "PAD INPUT", [("PAD", "SIGNAL", [])])
        + _macro("test_io__bi", "PAD INOUT", [("PAD", "SIGNAL", [])])
        + _macro("test_io__fill", "PAD SPACER", width=1)
        + _macro("test_io__cor", "ENDCAP BOTTOMLEFT", width=40)
        + _supply_cell("test_io__pbridge", "IOVDD", "POWER", face_layer)
        + _supply_cell("test_io__gbridge", "IOVSS", "GROUND",
                       ground_face_layer or face_layer)
    )
    (lefdir / "test_io.lef").write_text(text)
    techdir = root / "testpdk/libs.ref/test_sc/techlef"
    techdir.mkdir(parents=True)
    (techdir / "test_sc.tlef").write_text(TECH_LEF)
    cfgdir = root / "testpdk/libs.tech/someflow/test_io"
    cfgdir.mkdir(parents=True)
    (cfgdir / "config.tcl").write_text(
        'set ::env(PAD_SITE_NAME) "test_io_site"\n'
        'set ::env(PAD_CORNER_SITE_NAME) "test_io_corner_site"\n'
        'set ::env(PAD_CORNER) "$::env(PAD_CELL_LIBRARY)__cor"\n'
        'set ::env(PAD_FILLERS) "$::env(PAD_CELL_LIBRARY)__fill"\n'
        'set ::env(PAD_EDGE_SPACING) "5"\n'
        'set ::env(PAD_PLACE_IO_TERMINALS) '
        '"test_io__in/PAD test_io__bi/PAD"\n')
    return project, root


def _produce(project: Path, root: Path, monkeypatch, tech_lef: str = TECH_LEF,
             plan=None):
    """Step 15.5ic's producer, driven the way the runner drives it: the
    runner reads the run's tech LEF and runs the real producer."""
    import phase3_one_shot_runner as R
    tech = root / "testpdk/libs.ref/test_sc/techlef/test_sc.tlef"
    tech.write_text(tech_lef)
    pdk = SimpleNamespace(name="testpdk", tech_lef=str(tech),
                          liberty=str(root / "unused.lib"))
    monkeypatch.setattr(R, "_padring_pdk_root_and_tree",
                        lambda _pdk, _c: (str(root), "testpdk"))
    monkeypatch.setattr(R, "_design_supply_nets",
                        lambda _pdk: ({"VDD"}, {"VSS"}))
    monkeypatch.setattr(R, "_v1_6_596_discover_tie_cells", lambda *a: {})
    if plan is not None:
        floorplan = project / "phase3/stage3/pnr/floorplan.def"
        floorplan.parent.mkdir(parents=True, exist_ok=True)
        side = int(plan["die_side_um"] * 1000)
        floorplan.write_text(
            "VERSION 5.8 ;\nDESIGN chip_top ;\n"
            "UNITS DISTANCE MICRONS 1000 ;\n"
            f"DIEAREA ( 0 0 ) ( {side} {side} ) ;\nEND DESIGN\n")
    result = R.step_io_pad_chip_top_gen(project, None, pdk, supply_plan=plan)
    assert result.status == "PASS", result.detail
    return json.loads(
        (project / "reports/phase3/io_pad_chip_top.json").read_text())


def _supply_sides(rec) -> dict:
    return {side: [p for p in pads if p.startswith("u_pad_supply_")]
            for side, pads in rec["derived_answers"]["pad_order_by_side"].items()}


def test_the_pair_leaves_an_edge_the_pdn_cannot_strap_it_from(
        tmp_path, monkeypatch):
    project, root = _tree(tmp_path, face_layer="lv")
    rec = _produce(project, root, monkeypatch)
    plan = rec["power_pad_plan"]
    # West carries one signal and is the shortest edge, but a strap from a
    # west pad runs horizontally and the pins face the core on `lv`, routed
    # vertically. South and north tie at two signals; south comes first.
    assert _supply_sides(rec)["west"] == []
    assert plan["placement_side"] == "S"
    assert _supply_sides(rec)["south"] == ["u_pad_supply_power",
                                           "u_pad_supply_ground"]
    reach = plan["edge_reach"]
    assert reach["reached_sides"] == ["S", "N"]
    assert {s: reach["sides"][s]["verdict"] for s in "SENW"} == {
        "S": "REACHED", "E": "UNREACHED", "N": "REACHED", "W": "UNREACHED"}
    west = reach["sides"]["W"]["cells"]["test_io__gbridge"]
    assert west["strap_direction"] == "HORIZONTAL"
    assert [(f["pin"], f["layer"], f["direction"])
            for f in west["core_facing_rail_pins"]] == [
                ("IOVSS", "lv", "VERTICAL"), ("IOVSS", "lv", "VERTICAL")]


def test_pins_routed_across_the_edge_keep_the_shortest_edge(
        tmp_path, monkeypatch):
    """Control: the same cells with their core-facing pins on the layer that
    routes horizontally reach the west and east columns, so the shortest edge
    stays the pair's edge."""
    project, root = _tree(tmp_path, face_layer="lh")
    rec = _produce(project, root, monkeypatch)
    plan = rec["power_pad_plan"]
    assert plan["placement_side"] == "W"
    assert plan.get("edge_reach", {}).get("reached_sides") == ["E", "W"]


def test_without_layer_directions_the_rule_is_not_claimed(
        tmp_path, monkeypatch):
    """A tech LEF that states no direction -> no reach claim: the old
    shortest-edge rule applies and the record says it was not determined."""
    project, root = _tree(tmp_path, face_layer="lv")
    rec = _produce(project, root, monkeypatch,
                   tech_lef=TECH_LEF.replace("  DIRECTION VERTICAL ;\n", "")
                   .replace("  DIRECTION HORIZONTAL ;\n", ""))
    plan = rec["power_pad_plan"]
    assert plan["placement_side"] == "W"
    assert plan.get("edge_reach", {}).get("verdict") == "NOT_DETERMINED"
    assert "routing-layer" in plan["edge_reach"]["reason"]


def test_an_edge_must_be_reached_by_every_cell_of_the_pair(
        tmp_path, monkeypatch):
    """The power cell reaches only the columns and the ground cell only the
    rows: no edge carries both, so no edge is claimed and the shortest-edge
    rule stands, with the record saying the PDN run will judge it."""
    project, root = _tree(tmp_path, face_layer="lh", ground_face_layer="lv")
    rec = _produce(project, root, monkeypatch)
    plan = rec["power_pad_plan"]
    assert plan["placement_side"] == "W"
    reach = plan.get("edge_reach", {})
    assert reach.get("verdict") == "NOT_DETERMINED"
    assert reach["reached_sides"] == []
    assert {s: reach["sides"][s]["verdict"] for s in "SENW"} == {
        "S": "UNREACHED", "E": "UNREACHED", "N": "UNREACHED", "W": "UNREACHED"}


def test_measured_pairs_are_spread_only_over_reachable_edges(
        tmp_path, monkeypatch):
    project, root = _tree(tmp_path, face_layer="lv")
    rec = _produce(project, root, monkeypatch, plan={
        "verdict": "PLANNED", "pair_count": 4,
        "subject_def_sha256": "a" * 64, "die_side_um": 500})
    by_side = rec["power_pad_plan"]["pairs_by_side"]
    assert (by_side["E"], by_side["W"]) == (0, 0)
    assert by_side["S"] + by_side["N"] == 4


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
