"""vibe-ic#2135 — the density-fill lattice is derived from the deck and the
MEASURED room, and a shortfall against the foundry floor is REFUSED BY NAME.

WHAT WAS MEASURED (the defect this pins)
----------------------------------------
On a routed die under an open PDK whose sign-off deck states a 30% per-layer
metal-coverage floor, a 0.98um dummy-to-dummy space and a 2um dummy-to-circuit
clearance, two layers came out of the fill at 0.1877 and 0.2042 and the
sign-off DRC reported the two coverage rules. The fill's own capacity report
said the lattice ceiling was 0.2288 / 0.2414 — BELOW the floor — because the
top of the fill ladder came from a constant: `_fill_width_for_target` aims at a
packing capped at 0.62 whatever the layer needs. A lattice that tops out at
23% can never satisfy a 30% floor, on any design; and the run said only
"density target NOT reached on every layer", which names no layer, no room and
no number, so a shortfall no legal fill could have closed read exactly like a
fill that under-packed.

Two things are asserted here, both directions each:
  1. the width is a FUNCTION of the deck's space, the deck's floor and the
     measured room (`lattice_width_for_floor`), and the constant it replaced
     cannot reach the floor on the numbers actually measured;
  2. every layer left under the floor is NAMED, with its numbers, and
     classified as UNREACHABLE_BY_ANY_FILL vs NOT_REACHED_BY_THIS_LATTICE.

NEGATIVE CONTROL. The pre-fix input shape is a config with no
`_derivation.density_floor_pct` — the engine then knows no floor, does no
widening and refuses nothing, which is exactly the old behaviour. Every
end-to-end assertion below is made against that same fixture in both shapes,
so a test that passes without the fix is impossible: the floor-less arm is
measured to stay BELOW the floor the floored arm clears.

chip-AGNOSTIC: every layout here is drawn by this file; no PDK, foundry, node
or design name appears. The layer number and the spacings are the fixture's
own arithmetic.
"""
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(PROGRAMS / "metal_fill"))

import metal_fill  # noqa: E402
import metal_fill_emit as E  # noqa: E402
import metal_fill_config_gen as G  # noqa: E402


# ---------------------------------------------------------------------------
# 1. The width is derived, and the constant it replaced could not get there.
# ---------------------------------------------------------------------------

# The numbers the defect was measured on: dummy-to-dummy space, drawn metal and
# legal dummy room as fractions of the die, and the deck's coverage floor.
SPACE, DRAWN, FREE, FLOOR = 0.98, 0.09477, 0.223368, 0.30


def _open_field_ceiling(width, space=SPACE, drawn=DRAWN, free=FREE):
    return drawn + free * (width / (width + space)) ** 2


def test_the_constant_width_cannot_reach_the_floor_the_derived_one_can():
    """The pre-fix width and the derived width, on the same measured room."""
    fixed = G._fill_width_for_target(SPACE, 0.35, 0.28, 0.005)
    assert _open_field_ceiling(fixed) < FLOOR, (
        "fixture no longer reproduces: the constant-aim width already clears "
        "the floor, so there is nothing for the derivation to fix")

    derived = metal_fill.lattice_width_for_floor(SPACE, DRAWN, FREE, FLOOR)
    assert derived > fixed
    assert _open_field_ceiling(derived) == pytest.approx(FLOOR, abs=1e-9)


def test_the_derivation_refuses_rather_than_returning_a_width():
    """No width exists when the floor is above drawn + ALL the room: the
    caller must refuse, not fill harder."""
    assert metal_fill.lattice_width_for_floor(
        SPACE, DRAWN, FREE, DRAWN + FREE + 0.01) is None
    # exactly at the absolute ceiling is still not reachable by a lattice
    assert metal_fill.lattice_width_for_floor(
        SPACE, DRAWN, FREE, DRAWN + FREE) is None
    # already there -> nothing to widen
    assert metal_fill.lattice_width_for_floor(SPACE, 0.5, FREE, FLOOR) == 0.0
    # unmeasured / degenerate inputs never invent a width
    assert metal_fill.lattice_width_for_floor(SPACE, DRAWN, FREE, None) is None
    assert metal_fill.lattice_width_for_floor(SPACE, DRAWN, 0.0, FLOOR) is None
    assert metal_fill.lattice_width_for_floor(0.0, DRAWN, FREE, FLOOR) is None


# ---------------------------------------------------------------------------
# 2. End to end on a drawn fixture: widen, and refuse by name.
# ---------------------------------------------------------------------------

LAYER, FILL_DT, DIE_UM, STRIPE_UM = 36, 4, 200.0, 2.0


def _pya_or_skip():
    try:
        import pya  # noqa: F401
        return pya
    except Exception:                                    # pragma: no cover
        pytest.skip("KLayout pymod (pya) not importable in this environment")


def _striped_die(pya, path, pitch_um):
    """A die crossed by vertical stripes at `pitch_um`, so the legal dummy room
    is a set of CHANNELS rather than an open field — the shape the constant
    width was measured to under-fill."""
    ly = pya.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("TOP")
    li = ly.layer(LAYER, 0)

    def u(v):
        return int(round(v / 0.001))

    x = 0.0
    while x < DIE_UM:
        top.shapes(li).insert(
            pya.Box(u(x), 0, u(min(x + STRIPE_UM, DIE_UM)), u(DIE_UM)))
        x += pitch_um
    # top and bottom edges, so the measurement bbox is the whole die
    top.shapes(li).insert(pya.Box(0, 0, u(DIE_UM), u(STRIPE_UM)))
    top.shapes(li).insert(
        pya.Box(0, u(DIE_UM - STRIPE_UM), u(DIE_UM), u(DIE_UM)))
    ly.write(str(path))


def _cfg(floor_pct):
    cfg = {
        "boundary_layer": None, "window_um": None, "max_passes": 8,
        "mfg_grid_um": 0.005, "fill_datatype": None,
        "layers": [{"name": "m", "layer": [LAYER, 0], "target": 0.60,
                    "max": 0.95, "space": SPACE, "space_to_metal": 2.0,
                    "width": 3.37, "fill_datatype": FILL_DT}],
    }
    if floor_pct is not None:
        # the ONLY difference between the two arms: whether the config carries
        # the deck's own coverage floor. Absent == the pre-fix input shape.
        cfg["_derivation"] = {"density_floor_pct": floor_pct}
    return cfg


def _run(pya, tmp_path, pitch_um, floor_pct, tag):
    gds = tmp_path / f"in_{tag}.gds"
    _striped_die(pya, gds, pitch_um)
    return metal_fill.run(str(gds), _cfg(floor_pct),
                          str(tmp_path / f"out_{tag}.gds"), "TOP")


def test_widening_closes_a_floor_the_constant_width_left_open(tmp_path):
    """Same layout, same config but for the deck floor: without it the fill
    stops BELOW the floor and says nothing; with it the lattice is widened and
    the floor is cleared, with no refusal."""
    pya = _pya_or_skip()
    without = _run(pya, tmp_path, 16.0, None, "a")["layers"][0]
    withf = _run(pya, tmp_path, 16.0, 40.0, "b")

    assert without["density_after"] < 0.40, (
        "negative control broken: the pre-fix arm already clears the floor")
    assert len(without["families_tried"]) == 1, (
        "the floor-less arm must do exactly one fill family — the old path")

    layer = withf["layers"][0]
    assert layer["density_after"] >= 0.40
    assert layer["density_after"] > without["density_after"]
    assert len(layer["families_tried"]) > 1
    assert layer["top_width_um"] > without["top_width_um"]
    assert withf["refusals"] == [], "a layer that clears the floor is not refused"


def test_a_layer_that_already_clears_the_floor_is_not_touched(tmp_path):
    """The widening search costs a layer nothing when it is not needed: the
    same fixture under a floor it already meets tries exactly one family."""
    pya = _pya_or_skip()
    res = _run(pya, tmp_path, 16.0, 20.0, "c")
    layer = res["layers"][0]
    assert layer["density_after"] >= 0.20
    assert len(layer["families_tried"]) == 1
    assert res["refusals"] == []


def test_a_shortfall_is_refused_by_name_with_its_numbers(tmp_path):
    """Reachable in principle, not reached by this lattice."""
    pya = _pya_or_skip()
    res = _run(pya, tmp_path, 16.0, 50.0, "d")
    layer = res["layers"][0]
    assert layer["density_after"] < 0.50
    assert layer["ceiling_any_fill"] > 0.50, (
        "fixture must leave room in principle, else this is the other class")
    assert len(res["refusals"]) == 1
    r = res["refusals"][0]
    assert r["layer"] == "m"
    assert r["verdict"] == "NOT_REACHED_BY_THIS_LATTICE"
    assert r["floor"] == 0.50
    assert r["achieved"] == pytest.approx(layer["density_after"], abs=1e-9)
    assert r["ceiling_any_fill"] == layer["ceiling_any_fill"]
    assert r["space_to_metal_um"] == 2.0
    # the numbers are IN the sentence, not only in the neighbouring fields
    assert "m:" in r["reason"] and "2.0um dummy-to-circuit" in r["reason"]


def test_a_floor_above_the_absolute_ceiling_is_refused_as_unreachable(tmp_path):
    """No dummy fill of any shape can reach it — only the drawn metal can."""
    pya = _pya_or_skip()
    res = _run(pya, tmp_path, 16.0, 80.0, "e")
    layer = res["layers"][0]
    assert layer["ceiling_any_fill"] < 0.80
    assert layer["floor_unreachable_by_any_fill"] is True
    assert len(layer["families_tried"]) == 1, (
        "widening a lattice that cannot help must not be attempted")
    assert [r["verdict"] for r in res["refusals"]] == ["UNREACHABLE_BY_ANY_FILL"]
    assert res["refusals"][0]["layer"] == "m"


def test_the_fill_still_honours_the_decks_own_spacings(tmp_path):
    """The widening must not buy density with a spacing violation. Measured on
    the emitted layout: dummy-to-dummy >= `space`, dummy-to-circuit >=
    `space_to_metal`. (An earlier candidate that let the fill engine place
    locally-anchored arrays reached a higher density and put fill 0.605um
    apart against a 0.98um rule — this is the check that caught it.)"""
    pya = _pya_or_skip()
    gds = tmp_path / "in_f.gds"
    _striped_die(pya, gds, 16.0)
    out = tmp_path / "out_f.gds"
    metal_fill.run(str(gds), _cfg(40.0), str(out), "TOP")

    ly = pya.Layout()
    ly.read(str(out))
    top = ly.top_cell()
    drawn = pya.Region(top.begin_shapes_rec(ly.find_layer(LAYER, 0)))
    dummy = pya.Region(top.begin_shapes_rec(ly.find_layer(LAYER, FILL_DT)))
    drawn.merge()
    dummy.merge()
    assert not dummy.is_empty(), "fixture placed no fill"
    # `space_check`/`separation_check` return the offending edge pairs; a legal
    # layout returns none at the rule distance.
    assert dummy.space_check(int(round(SPACE / ly.dbu))).is_empty()
    assert dummy.separation_check(
        drawn, int(round(2.0 / ly.dbu))).is_empty()


# ---------------------------------------------------------------------------
# 3. The operator lines.
# ---------------------------------------------------------------------------

def test_refusal_lines_name_the_layer_and_stay_silent_when_there_is_none():
    lines = E.refusal_lines({"refusals": [
        {"layer": "m", "verdict": "UNREACHABLE_BY_ANY_FILL",
         "reason": "m: fill reached 0.20 against a deck floor of 0.3"}]})
    assert len(lines) == 1
    assert "UNREACHABLE_BY_ANY_FILL" in lines[0] and "m:" in lines[0]
    # a fill that met the floor refuses nothing
    assert E.refusal_lines({"refusals": []}) == []
    # an older engine, or a `--verify-only` replay, carries no such key: absent
    # is absent, never an invented "nothing was refused"
    assert E.refusal_lines({}) == []
    assert E.refusal_lines({"refusals": "not a list"}) == []
