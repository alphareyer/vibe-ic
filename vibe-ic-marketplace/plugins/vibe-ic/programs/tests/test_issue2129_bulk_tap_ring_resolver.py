"""A5's bulk-tap search RUNS, and says what it examined (vibe-ic#2129).

THE DEFECT, measured on the real producer before this module existed.
`ring_layer_of` asked ONE geometric question of every section of a gencell
child — does this layer cover >= 90% of the CELL bounding box in both axes? —
and on the PDK's own gencell children the answer was NO for the guard ring,
because the WELL rectangle the ring sits inside is wider than the ring. So it
returned None on 34 of 34 children across u_hawaii_adc's two analog blocks
(8 in `ldo`, 26 in `delta_sigma`; ihp-sg13g2, image
sha256:1463dac58116ca6650ec84e9e4b11a73a70f4094c95a9a19e620482251471c57,
lambda 100/um, 2026-09-07). With no ring layer, `build_plan`'s bulk-tap block
never entered on ANY device: `choose_tap`, the tap-clearance floor and
`bulk_tap_row_separation_lambda` were unreachable code on real silicon.

And nothing said so. On the same two blocks the base producer wrote 390 and 8
deviation rows, ZERO of them a `bulk_tap_*` row, out of 226 and 10 devices
that carry a guard ring — byte-identical to what a block whose every tap
clears the floor produces. Blindness and cleanliness were one artefact.

AFTER, same inputs, same image:

    block         devices  examined  not tappable  bulk_tap shortfalls
    ldo                11        10             1 (the MiM capacitor)   5
    delta_sigma       238       226            12 (the MiM capacitors) 177

    shortfall_devices is a SUBSET of `searched` on both blocks.

The two halves this module pins:

  1. THE RESOLVER asks the PDK first. `body_contact_layers` is the technology
     file's own types/contact table answering which drawn types could be a
     body connection at all; only then does geometry pick the enclosing frame
     out of that short list. No label is read (the refused attempt at
     8353c69a2 anchored on the bulk LABEL and produced 240 shortfalls the
     sign-off deck contradicted), and no PDK, layer or device is named.
  2. THE SEARCH STATES ITS DENOMINATOR, in `_gate_denominator.Denominator`
     shape, and A5 REFUSES a vacuous one when a device the PDK says has a
     body connection went unsearched. A device with no body connection is a
     stated zero, not a refusal — that difference is a test below.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import _plugin_tree  # noqa: F401 — puts programs/ on sys.path

import analog_a5_layout_emit as A5E
import analog_a5_pdk_device_limits as A5L

PROGRAMS = Path(__file__).resolve().parents[1]


# A technology file in the shape a real open PDK writes: a well plane, an
# active plane carrying diffusion and poly, two metals, and the contacts that
# join them. Diffusion-to-metal contacts are the body/signal connections; the
# via is metal-to-metal and must never be a candidate.
TECH = """
planes
  well,w
  active,act
  metal1,m1
  metal2,m2
end

types
  well pwell,pw
  well nwell,nw
  active ndiff,nd
  active psubdiff,psd
  active poly,p
  active ndiffc,ndc
  active psubdiffcont,psdc
  active polycont,pc
  metal1 metal1,m1,met1
  metal1 via1,v1
  metal2 metal2,m2,met2
end

contact
  ndiffc        ndiff    metal1
  psubdiffcont  psubdiff metal1
  polycont      poly     metal1
  via1          metal1   metal2
  stackable
end
"""


def _table():
    return A5L.layer_identity(TECH, "/pdk/x.tech")


# The PDK's own MOS gencell child, trimmed to the sections this question
# needs: a well wider than everything, a four-bar substrate ring drawn on the
# body contact, and the drain / gate contacts it encloses.
MOS_CHILD = """magic
tech pdktech
timestamp 1
<< pwell >>
rect -300 -300 300 300
<< psubdiff >>
rect -220 200 220 240
rect -220 -200 -180 200
rect 180 -200 220 200
rect -220 -240 220 -200
<< psubdiffcont >>
rect -180 180 180 200
rect -180 -180 -160 180
rect 160 -180 180 180
rect -180 -200 180 -180
<< ndiffc >>
rect -60 -40 -20 40
rect 20 -40 60 40
<< polycont >>
rect -10 -100 10 -60
rect -10 60 10 100
<< metal1 >>
rect -180 -200 180 200
<< labels >>
rlabel psubdiffcont 0 -190 0 -190 0 B
port 1 nsew
rlabel ndiffc -40 0 -40 0 0 D
port 2 nsew
rlabel polycont 0 80 0 80 0 G
port 3 nsew
rlabel ndiffc 40 0 40 0 0 S
port 4 nsew
<< properties >>
string FIXED_BBOX -300 -300 300 300
<< end >>
"""

# The capacitor child: ONE body contact, ONE solid rectangle. It has no guard
# ring, and that is a property of the device, not a miss.
CAP_CHILD = """magic
tech pdktech
timestamp 1
<< metal2 >>
rect -560 -560 560 560
<< metal1 >>
rect -480 -480 480 480
<< via1 >>
rect -480 -480 480 480
<< labels >>
rlabel via1 0 0 0 0 0 C1
port 1 nsew
rlabel metal2 510 0 510 0 0 C2
port 2 nsew
<< properties >>
string FIXED_BBOX -560 -560 560 560
<< end >>
"""


# ── 1. the resolver ───────────────────────────────────────────────────
def test_the_ring_is_resolved_on_the_pdks_own_mos_child():
    """RED before this branch: the old rule answered None here, because the
    ring covers 0.60 x 0.633 of the cell against a 0.9 floor — the well it
    sits in is wider than it is."""
    cell = A5E.parse_cell(MOS_CHILD, _table())
    assert A5E.ring_layer_of(cell) == "psubdiffcont"


def test_the_old_geometric_premise_is_the_one_that_failed():
    """The measurement the fix rests on, stated as an assertion rather than
    as prose: the ring does NOT cover the cell, and it DOES enclose every
    other body contact. The second property is the one that identifies it."""
    cell = A5E.parse_cell(MOS_CHILD, _table())
    bx1, by1, bx2, by2 = cell["bbox"]
    ring = A5E._extent(cell["sections"]["psubdiffcont"])
    cover_x = (ring[2] - ring[0]) / (bx2 - bx1)
    cover_y = (ring[3] - ring[1]) / (by2 - by1)
    assert cover_x < 0.9 and cover_y < 0.9, (
        "if the ring did cover the cell the old rule would have worked and "
        "this whole issue would not exist")
    for other in ("ndiffc", "polycont"):
        o = A5E._extent(cell["sections"][other])
        assert (ring[0] <= o[0] and ring[1] <= o[1]
                and ring[2] >= o[2] and ring[3] >= o[3])


def test_the_pdk_answers_which_layers_could_be_a_ring():
    """The candidate set is the technology file's, not a name this program
    recognises: every diffusion contact is a candidate and the via — whose
    two residues are both routing planes — is not."""
    cell = A5E.parse_cell(MOS_CHILD, _table())
    assert A5E.body_contact_layers(cell) == [
        "ndiffc", "polycont", "psubdiffcont"]
    assert "via1" not in A5E.body_contact_layers(cell)
    assert "metal1" not in A5E.body_contact_layers(cell)


def test_a_device_with_no_body_connection_has_no_ring():
    """THE CONTROL. One body contact, one solid rectangle: None, and the
    resolver says so through the candidate list rather than by silence."""
    cell = A5E.parse_cell(CAP_CHILD, _table())
    assert A5E.body_contact_layers(cell) == []
    assert A5E.ring_layer_of(cell) is None


def test_without_the_technology_table_the_old_rule_still_answers():
    """The resolver never reads a layer NAME. With no table there are no
    candidates at all, and the ONLY honest answer left is the original
    geometry-only rule — which is exactly the rule that answers None here,
    because the well is wider than the ring. That path is unreachable from
    the producer (`read_pdk` refuses ENV_UNAVAILABLE without the table) and
    it is what `test_issue2056_bulk_tap_check_states_its_denominator.py`'s
    table-less fixtures exercise, so this arm is why they are unchanged."""
    cell = A5E.parse_cell(MOS_CHILD)
    assert A5E.body_contact_layers(cell) == []
    assert A5E.ring_layer_of(cell) is A5E._ring_by_cell_coverage(cell)
    assert A5E.ring_layer_of(cell) is None, (
        "the well spans the cell and the ring does not — the whole defect")
    # and with the table the SAME bytes resolve, which is the fix
    assert A5E.ring_layer_of(A5E.parse_cell(MOS_CHILD, _table())) == (
        "psubdiffcont")


def test_a_solid_body_contact_plate_is_not_a_frame():
    """Geometry still has to agree. Replace the four ring bars with one solid
    rectangle of the same extent and the layer stops being a ring, even
    though the PDK still calls it a body contact."""
    solid = MOS_CHILD.replace(
        """<< psubdiffcont >>
rect -180 180 180 200
rect -180 -180 -160 180
rect 160 -180 180 180
rect -180 -200 180 -180
""",
        """<< psubdiffcont >>
rect -180 -200 180 200
""")
    cell = A5E.parse_cell(solid, _table())
    assert "psubdiffcont" in A5E.body_contact_layers(cell)
    assert A5E.ring_layer_of(cell) is None


def test_a_frame_that_encloses_nothing_is_not_the_ring():
    """The other half of the geometric test. Shrink the ring so the drain
    contacts fall outside it and it is no longer enclosing them, so it is no
    longer the structure a bulk tap belongs on."""
    small = MOS_CHILD.replace(
        """<< psubdiffcont >>
rect -180 180 180 200
rect -180 -180 -160 180
rect 160 -180 180 180
rect -180 -200 180 -180
""",
        """<< psubdiffcont >>
rect -30 20 30 30
rect -30 -20 -20 20
rect 20 -20 30 20
rect -30 -30 30 -20
""")
    cell = A5E.parse_cell(small, _table())
    assert A5E.ring_layer_of(cell) is None


# ── 2. the denominator's MEMBERSHIP half, and the refusal ────────────
#
# `bulk_tap_denominator(plan) -> dict` and the three counters it reads landed
# on main at 682f7a304 as a DISCLOSURE (#2056 item 3's silence), and
# `test_issue2056_bulk_tap_check_states_its_denominator.py` holds that shape
# — including that it moves no verdict on its own. What #2129 adds is the
# membership the acceptance is stated in, and ONE verdict, in one direction.
class _FakePlan:
    """Only the fields the disclosure reads, as main's own fixture does."""

    def __init__(self, considered, searched=(), unexamined=(),
                 deviations=()):
        self.bulk_tap_considered = considered
        self.bulk_tap_examined = len(searched)
        self.bulk_tap_searched = list(searched)
        self.bulk_tap_ring_layer = {d: "psubdiffcont" for d in searched}
        self.bulk_tap_unexamined = list(unexamined)
        self.deviations = list(deviations)


def test_a_search_that_ran_names_what_it_examined():
    plan = _FakePlan(
        4, searched=["m1", "m2", "m3"],
        unexamined=[{"device": "c1", "model": "c", "tappable": False,
                     "body_contact_layers": [], "reason": "no ring"}],
        deviations=[{"device": "m2",
                     "quantity": "bulk_tap_clearance_lambda"}])
    d = A5E.bulk_tap_denominator(plan)
    assert d["examined"] == 3 and d["considered"] == 4
    assert d["not_applicable_reason"] == ""
    assert A5E.bulk_tap_refusal(d) == ""
    de = d["details"]
    assert de["searched"] == ["m1", "m2", "m3"]
    assert de["ring_layer"]["m2"] == "psubdiffcont"
    assert de["shortfall_devices"] == ["m2"]
    assert de["not_tappable"] == ["c1"]
    assert de["tappable_not_searched"] == []


def test_the_shortfall_list_is_a_subset_of_what_was_searched():
    """MEMBERSHIP, not counts — the acceptance #2129 is stated in. A
    shortfall row naming a device the search never entered is a shortfall of
    nothing, which is what the label-anchored attempt at 8353c69a2 produced
    240 of."""
    plan = _FakePlan(
        2, searched=["m1", "m2"],
        deviations=[{"device": "m1",
                     "quantity": "bulk_tap_clearance_lambda"},
                    {"device": "m2",
                     "quantity": "bulk_tap_row_separation_lambda"},
                    {"device": "m1",
                     "quantity": "metal2_space_to_device_lambda"}])
    de = A5E.bulk_tap_denominator(plan)["details"]
    assert set(de["shortfall_devices"]) <= set(de["searched"])
    assert de["shortfall_devices"] == ["m1", "m2"]


def test_a_vacuous_search_over_tappable_devices_is_REFUSED():
    """THE MUTATION ARM. This is what `ring_layer_of` answering None again
    produces, and it must never read as a clean bulk-tap result."""
    plan = _FakePlan(
        1, unexamined=[{"device": "m1", "model": "m", "tappable": True,
                        "body_contact_layers": ["ndiffc", "psubdiffcont"],
                        "reason": "ring_layer_of identifies no guard-ring "
                                  "layer in this gencell's output"}])
    d = A5E.bulk_tap_denominator(plan)
    assert d["examined"] == 0
    why = A5E.bulk_tap_refusal(d)
    assert why, "a search that entered nothing must be refused, not passed"
    assert "NOT EVALUATED" in why
    assert d["details"]["tappable_not_searched"] == ["m1"]


def test_a_block_with_nothing_to_tap_is_STATED_not_refused():
    """THE CONTROL for the refusal, and the arm that makes it trustworthy. A
    block whose devices carry no body connection examines nothing for a
    reason that is a property of the devices; it still SAYS so, and it is not
    a defect. Without this the refusal would fire on every capacitor bank."""
    plan = _FakePlan(
        1, unexamined=[{"device": "c1", "model": "c", "tappable": False,
                        "body_contact_layers": [],
                        "reason": "ring_layer_of identifies no guard-ring "
                                  "layer in this gencell's output"}])
    d = A5E.bulk_tap_denominator(plan)
    assert d["examined"] == 0
    assert d["not_applicable_reason"], "a zero still has to say why"
    assert A5E.bulk_tap_refusal(d) == ""
    assert d["details"]["not_tappable"] == ["c1"]
    assert d["details"]["tappable_not_searched"] == []


def test_the_refusal_is_the_only_verdict_the_disclosure_moves():
    """A non-zero denominator NEVER refuses, whatever it found. The exit code
    for a shortfall belongs to the sign-off deck, not to this producer."""
    plan = _FakePlan(
        2, searched=["m1", "m2"],
        deviations=[{"device": "m1",
                     "quantity": "bulk_tap_clearance_lambda"}])
    assert A5E.bulk_tap_refusal(A5E.bulk_tap_denominator(plan)) == ""


def test_the_emitter_refuses_on_it_by_name():
    """The producer's own wiring, read from its source so that deleting the
    branch is a red rather than a silent behaviour change."""
    src = (PROGRAMS / "analog_a5_layout_emit.py").read_text()
    assert 'why_refused = bulk_tap_refusal(report["bulk_tap"])' in src
    assert "report[\"result\"] = BULK_TAP_VACUOUS" in src
    assert A5E.BULK_TAP_VACUOUS == "BULK_TAP_SEARCH_EXAMINED_NOTHING"


def test_a_zero_denominator_can_never_be_silent():
    """The shared type refuses it — the reason this rides on `Denominator`
    rather than on a dict of its own."""
    import _gate_denominator as _gd
    with pytest.raises(ValueError):
        _gd.Denominator(unit="routed devices", examined=0, considered=9)
