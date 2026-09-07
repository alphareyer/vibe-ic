"""The bulk-tap check says what it EXAMINED, so a zero cannot read as clean.

WHY THIS FILE EXISTS (vibe-ic#2056 item 3).  `choose_tap`, the
`bulk_tap_clearance_lambda` floor and `bulk_tap_row_separation_lambda` run
only for a device whose guard ring `ring_layer_of` identifies.  MEASURED on
u_hawaii_adc (ihp-sg13g2, image 0.3.46, lanes czadc28/czadc29): it identifies
NONE of that PDK's gencell children, because the ring sits inside a WELL
rectangle wider than itself and so fails the "encloses the cell" test it is
judged by.  The whole subsystem therefore never ran on a real device — and the
A5 record said nothing at all about that.  An empty `deviations` list reads as
"the clearance floor was met"; what it meant was "the floor was never applied".

CORRECTING THE ENCLOSURE TEST IS NOT WHAT THIS CLOSES.  That was measured and
deliberately not landed (v1.17.93, 8353c69a2): a label-anchored ring does run
the search, changes nothing the verdict reads, and adds 240 bulk-tap
shortfalls the sign-off deck then contradicts — no verdict bought.  What was
left over was the SILENCE, and silence is the one part that costs nothing to
end.  The count is stated in this repo's own zero-denominator shape
(`_gate_denominator.Denominator`), whose constructor refuses a zero with no
reason, so it cannot regress into silence by omission.

IT IS A DISCLOSURE, NOT A VERDICT: no exit code, no gate and no deviation
depends on it, and `test_the_disclosure_changes_no_verdict` is what holds that.

MUTATIONS THESE TESTS MUST KILL:
  * Dropping `report["bulk_tap"]` fails `test_the_record_carries_the_count`.
  * Counting every device as examined fails
    `test_a_device_whose_ring_is_not_identified_is_not_examined`.
  * A zero with an empty reason fails `test_a_zero_must_say_why`.
  * Counting a device whose ring IS found as unexamined fails
    `test_a_device_whose_ring_is_identified_is_examined`.
  * Changing `deviations` or the exit-code inputs fails
    `test_the_disclosure_changes_no_verdict`.
"""

import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import _gate_denominator as DEN        # noqa: E402
import analog_a5_layout_emit as A5E    # noqa: E402
import analog_a5_pdk_device_limits as A5L  # noqa: E402


# A minimal magic DRC deck, in the shape a real open PDK writes one. Every
# number the router needs comes from here rather than from a default, which
# is the emitter's own contract.
_TECH = """
 width  m1  180  "Metal1 width < 0.18um (M1.a)"
 spacing m1 m1 180 touching_ok "Metal1 spacing < 0.18um (M1.b)"
 spacing allm2 allm2 210 touching_ok "Metal2 spacing < 0.21um (M2.b)"
 spacing allm3 allm3 210 touching_ok "Metal3 spacing < 0.21um (M3.b)"
 area allm2 144000 200 "Metal2 minimum area < 0.144um^2 (M2.d)"
 width v1/m1 200 "Via1 width < 0.2um (V1.a)"
 spacing v1 v1 210 touching_ok "Via1 spacing < 0.21um (V1.b)"
 width v2/m2 200 "Via2 width < 0.2um (V2.a)"
 width v3/m3 200 "Via3 width < 0.2um (V3.a)"
 surround v1/m1 *m1,rm1 5 absence_illegal \\
\t"Metal1 overlap of Via1 < 0.005um (V1.c)"
 surround v1/m2 *m2,rm2 45 directional \\
\t"Metal2 overlap of Via1 < 0.045um in one direction (M2.c1)"
"""


# ── the two cell shapes, written as the gencell writes them ──────────────
#
# A RING, as `ring_layer_of` recognises one: several bars on one layer that
# together span (almost) the whole cell in both axes while filling only a
# fraction of its area.
def _cell_with_a_ring():
    return {
        "cell": "res_gen_ring", "delta": (0, 0),
        "bbox": (0, 0, 100, 100),
        "sections": {
            "guard": [(0, 0, 100, 8), (0, 92, 100, 100),
                      (0, 0, 8, 100), (92, 0, 100, 100)],
            "metal1": [(20, 20, 40, 40)],
        },
        "labels": [
            {"name": "R1", "layer": "metal1", "x": 25, "y": 25, "level": 1},
            {"name": "R2", "layer": "metal1", "x": 35, "y": 35, "level": 1},
            {"name": "B", "layer": "guard", "x": 4, "y": 50, "level": 1},
        ],
    }


# THE REAL SHAPE THIS ISSUE IS ABOUT: the ring is drawn, but it sits inside a
# WELL rectangle wider than the ring, so the enclosing-frame test picks
# nothing and `ring_layer_of` answers None.
def _cell_whose_ring_is_hidden_by_a_wider_well():
    return {
        "cell": "res_gen_well", "delta": (0, 0),
        "bbox": (-20, -20, 120, 120),
        "sections": {
            "well": [(-20, -20, 120, 120)],
            "guard": [(0, 0, 100, 8), (0, 92, 100, 100),
                      (0, 0, 8, 100), (92, 0, 100, 100)],
            "metal1": [(20, 20, 40, 40)],
        },
        "labels": [
            {"name": "R1", "layer": "metal1", "x": 25, "y": 25, "level": 1},
            {"name": "R2", "layer": "metal1", "x": 35, "y": 35, "level": 1},
            {"name": "B", "layer": "guard", "x": 4, "y": 50, "level": 1},
        ],
    }


def _dev(name, nets):
    return {"name": name, "model": "res_gen", "class": "resistor",
            "nets": list(nets), "pars": {"w": 1.0, "l": 2.0, "m": 1}}


class _FakePlan:
    """Only the three fields the disclosure reads.

    Deliberately not a real `Plan`: the function under test must answer from
    the counts it is handed, so a test that could only be written by running
    the whole emitter would be testing the emitter."""

    def __init__(self, considered, examined, unexamined=()):
        self.bulk_tap_considered = considered
        self.bulk_tap_examined = examined
        self.bulk_tap_unexamined = list(unexamined)


# ── the predicate the whole subsystem hangs on ───────────────────────────

def test_the_measured_shape_is_reproduced_here():
    """The fixture must actually exhibit the defect, or the rest proves
    nothing: a ring hidden inside a wider well is not identified."""
    assert A5E.ring_layer_of(_cell_with_a_ring()) == "guard"
    assert A5E.ring_layer_of(_cell_whose_ring_is_hidden_by_a_wider_well()) is None


# ── the disclosure itself ────────────────────────────────────────────────

def test_a_zero_must_say_why():
    d = A5E.bulk_tap_denominator(
        _FakePlan(7, 0, [{"device": "xr1", "reason": "no guard-ring layer"}]))
    assert d["examined"] == 0
    assert d["considered"] == 7
    reason = d["not_applicable_reason"]
    assert reason, "a zero with no reason is the silence this closes"
    assert "0 of 7" in reason, reason
    assert "NOT EVALUATED" in reason, reason
    assert "no guard-ring layer" in reason, reason


def test_a_block_that_routed_nothing_says_that_instead():
    d = A5E.bulk_tap_denominator(_FakePlan(0, 0))
    assert d["examined"] == 0 and d["considered"] == 0
    assert "routed no device at all" in d["not_applicable_reason"]


def test_a_non_zero_count_carries_no_excuse():
    d = A5E.bulk_tap_denominator(_FakePlan(3, 3))
    assert d["examined"] == 3 and d["considered"] == 3
    assert d["not_applicable_reason"] == ""


def test_the_unexamined_devices_are_named_not_just_counted():
    rows = [{"device": "xr1", "reason": "no guard-ring layer"},
            {"device": "xr2", "reason": "no guard-ring layer"}]
    d = A5E.bulk_tap_denominator(_FakePlan(2, 0, rows))
    assert [r["device"] for r in d["details"]["unexamined"]] == ["xr1", "xr2"]


def test_the_shared_type_refuses_a_silent_zero():
    """The guarantee is the TYPE's, not this module's wording."""
    with pytest.raises(ValueError):
        DEN.Denominator(unit="routed devices", examined=0, considered=5)


# ── what the emitter actually counts ─────────────────────────────────────

def _plan_for(cells_and_devs):
    """Run the real `build_plan` over hand-built gencell output."""
    devs = [d for d, _c in cells_and_devs]
    cells = {A5E.config_key(d): c for d, c in cells_and_devs}
    geo = A5E.Geo(A5L.deck_rules(_TECH), 0.18, 100, 0.30, 0.15)
    facts = A5E.PdkFacts()
    # the gencell declaration the emitter reads from the PDK's own
    # `<model>_defaults` proc; nothing here is PDK-specific beyond its shape
    facts.gencells["res_gen"] = {"namespace": "testpdk",
                                 "params": ["w", "l", "m"]}
    return A5E.build_plan(devs, ["vout", "vss"], cells, facts, geo,
                          tap_clear=10)


def test_a_device_whose_ring_is_identified_is_examined():
    plan = _plan_for([(_dev("xr1", ["vout", "vss", "vss"]),
                       _cell_with_a_ring())])
    assert plan.bulk_tap_considered == 1
    assert plan.bulk_tap_examined == 1
    assert plan.bulk_tap_unexamined == []


def test_a_device_whose_ring_is_not_identified_is_not_examined():
    plan = _plan_for([(_dev("xr1", ["vout", "vss", "vss"]),
                       _cell_whose_ring_is_hidden_by_a_wider_well())])
    assert plan.bulk_tap_considered == 1
    assert plan.bulk_tap_examined == 0
    row = plan.bulk_tap_unexamined[0]
    assert row["device"] == "xr1"
    assert row["ring_layer"] is None
    assert "no guard-ring layer" in row["reason"]


def test_the_record_carries_the_count():
    """The disclosure is a dict of the documented shape, not prose."""
    plan = _plan_for([(_dev("xr1", ["vout", "vss", "vss"]),
                       _cell_whose_ring_is_hidden_by_a_wider_well())])
    d = A5E.bulk_tap_denominator(plan)
    assert set(d) >= {"unit", "examined", "considered",
                      "not_applicable_reason"}
    assert d["examined"] == 0 and d["considered"] == 1
    assert "0 of 1" in d["not_applicable_reason"]
    # and the emitter attaches it beside the shortfall list, never inside it
    src = (PROGRAMS / "analog_a5_layout_emit.py").read_text()
    assert 'report["bulk_tap"] = bulk_tap_denominator(plan)' in src


def test_the_disclosure_changes_no_verdict():
    """A device the search cannot enter must still route exactly as before.

    The disclosure is bookkeeping. If it moved a deviation, a shape or the
    exit-code inputs, every analog block's verdict would move with it."""
    hidden = _plan_for([(_dev("xr1", ["vout", "vss", "vss"]),
                         _cell_whose_ring_is_hidden_by_a_wider_well())])
    assert A5E.blocking_shorts(hidden.deviations) == []
    assert [d["quantity"] for d in hidden.deviations
            if str(d["quantity"]).startswith("bulk_tap")] == []
    # the counters are the ONLY new state; nothing painted depends on them
    assert hidden.shapes, "the device was still routed"
