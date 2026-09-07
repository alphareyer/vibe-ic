"""vibe-ic#2196 — the integrator handoff says what the PARENT die must reach.

WHAT WAS MEASURED (lane cz2196, 8HD-4, the pinned image, the open PDK's own
deck run on a die that INSTANTIATES the unmodified macro):

    macro alone, die  253009 um2  -> both coverage rules FIRE
    same macro in     289444 um2  -> both still FIRE
    same macro in     358801 um2  -> both SILENT
    same macro in     488601 um2  -> both SILENT
    FOUR copies,     1012036 um2  -> both FIRE again

So the attribution shipped in #2148 is right — the parent CAN close what the
macro cannot — and the last row is why this record has to say more than the
shortfall. A die-window rule is ONE ratio over the die, i.e. the area-weighted
mean of the macro and everything around it: a bigger die changes nothing, a
DENSER surround closes it. A handoff carrying only achieved / floor / legal
ceiling states neither fact, and its `legal_ceiling` — the most the MACRO's own
fillable room could reach — reads as the die's ceiling and so reads as "nobody
can fix this", which is the opposite of what the deck does.

WHAT IS ASSERTED HERE, EACH DIRECTION
  * the attributed rule carries a `closure` block: the requirement over the
    receiver's own die area, the macro's contributed area, and a worked ladder;
  * every worked row is the formula, checked by RECONSTRUCTING the die's
    area-weighted mean from it and landing on the floor — not by restating it;
  * the requirement is published even where it exceeds the macro's own legal
    ceiling, because that ceiling does not bound the parent;
  * no die area, or no whole-die figure, yields NOT_MEASURED naming what was
    missing — never a partial requirement;
  * the closure basis is the WHOLE-DIE coverage, which is a different figure
    from the `achieved` the delivery is judged on, and both are named;
  * the three figures #2148 already published are untouched (this test must
    pass on the pre-change tree too, or the change is a substitution).

chip/PDK-AGNOSTIC: the deck below is written by this file. No foundry, node,
SKU or design name appears; the layer and rule spellings are the fixture's own.
"""
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import die_level_deck_rule_attribution as D  # noqa: E402


DECK = D.FILE_SEP + """/deck/density.rb
chip_area = extent.sized(0.0).area

# Rule COV1.a: layer_one coverage over the entire die shall be >30%
if (layer_one.area / chip_area) * 100 < 30
  extent.output('COV1.a', 'COV1.a : 30%')
end
"""

FAMILY = {"COV1.a": "/deck/density.rb"}
DIE = [0.0, 0.0, 100.0, 100.0]          # 10000 um2
DIE_AREA = 10000.0
DIE_POLY = "(0,0;0,100;100,100;100,0)"
FLOOR = 0.30
WHOLE = 0.20                            # what the macro achieved over the die
CEILING = 0.25                          # its own fillable room tops out BELOW
                                        # the floor -> the macro cannot close it


def _facts(whole=WHOLE, floor=FLOOR, ceiling=CEILING, achieved=None):
    return {"layer_one": {
        "achieved": WHOLE if achieved is None else achieved,
        "floor": floor,
        "legal_ceiling": ceiling,
        "whole_die": whole,
    }}


def _disclosure(die_area=DIE_AREA, **kw):
    return D.density_disclosure(FAMILY, DECK, _facts(**kw), die_area)[0]


# ---------------------------------------------------------------------------
# 1. The requirement is there, and it is the receiver's to evaluate.
# ---------------------------------------------------------------------------

def test_the_handoff_says_what_the_parent_die_must_reach():
    rec = _disclosure()
    closure = rec["closure"]
    assert closure != D.NOT_MEASURED
    assert closure["macro_die_area_um2"] == DIE_AREA
    assert closure["macro_covered_area_um2"] == pytest.approx(WHOLE * DIE_AREA)
    assert closure["floor"] == FLOOR
    # the formula names the receiver's own die area as the free variable
    assert "A - " in closure["requirement"]
    assert closure["worked"], "a formula with no worked row is a requirement " \
                              "the reader still has to evaluate"


def test_every_worked_row_lands_exactly_on_the_floor():
    """Reconstruct the die's area-weighted mean from each published row. If a
    row is right, the mean it describes IS the floor — checked by rebuilding
    it, never by restating the formula that produced it."""
    closure = _disclosure()["closure"]
    a = closure["macro_die_area_um2"]
    covered = closure["macro_covered_area_um2"]
    for row in closure["worked"]:
        big = row["die_area_um2"]
        rest = row["rest_of_die_coverage_required"]
        mean = (covered + (big - a) * rest) / big
        assert mean == pytest.approx(FLOOR, abs=1e-6), row


def test_a_denser_surround_closes_it_and_a_bigger_die_alone_does_not():
    """The measured 2x2 tiling in one assertion: at the macro's own coverage,
    no die size satisfies the published requirement."""
    closure = _disclosure()["closure"]
    a = closure["macro_die_area_um2"]
    covered = closure["macro_covered_area_um2"]
    for multiple in (2, 4, 100, 10000):
        big = a * multiple
        # a die made of MORE OF THE SAME carries the macro's own coverage
        assert (covered * multiple) / big == pytest.approx(WHOLE)
        row = [r for r in closure["worked"]
               if r["die_area_multiple_of_macro"] == float(multiple)]
        for r in row:
            assert r["rest_of_die_coverage_required"] > WHOLE
    assert "mean of equal values" in \
        closure["a_bigger_die_alone_does_not_close_it"]


def test_the_ladder_is_published_even_above_the_macros_own_legal_ceiling():
    """The macro's fillable room tops out at CEILING, below the floor. Rows
    demanding more than that of the parent are still published: the ceiling is
    a fact about this macro, not a bound on the die it is placed in."""
    rec = _disclosure()
    closure = rec["closure"]
    assert rec["legal_ceiling"] == CEILING
    above = [r for r in closure["worked"]
             if r["rest_of_die_coverage_required"] > CEILING]
    assert above, "the ladder was truncated at this macro's own ceiling"
    assert "not a bound on the parent die" in \
        closure["legal_ceiling_is_this_macros_not_the_dies"]


# ---------------------------------------------------------------------------
# 2. Absent inputs say so by name. Never a partial requirement.
# ---------------------------------------------------------------------------

def test_no_die_area_means_no_closure_and_says_why():
    rec = _disclosure(die_area=None)
    assert rec["closure"] == D.NOT_MEASURED
    assert "die area" in rec["closure_not_measured"]
    # the three figures #2148 publishes are unaffected by the absence
    assert rec["achieved"] == WHOLE and rec["floor"] == FLOOR


def test_no_whole_die_figure_means_no_closure_and_says_why():
    rec = _disclosure(whole=D.NOT_MEASURED)
    assert rec["closure"] == D.NOT_MEASURED
    assert "whole-die coverage" in rec["closure_not_measured"]


def test_a_zero_die_area_is_refused_rather_than_divided_by():
    closure, why = D.closure_condition(WHOLE, FLOOR, 0.0)
    assert closure is None and "not an area" in why


def test_a_layer_already_over_the_floor_asks_the_parent_for_nothing():
    closure, why = D.closure_condition(0.40, FLOOR, DIE_AREA)
    assert closure is None and "already meets the floor" in why


def test_a_flag_is_not_a_measurement():
    """True is an int in Python; a die area of 1.0 minted from a flag is a
    supplied value wearing a measurement's clothes."""
    closure, why = D.closure_condition(WHOLE, FLOOR, True)
    assert closure is None and "is not a number" in why


# ---------------------------------------------------------------------------
# 3. The basis is the whole-die ratio, not the worst window.
# ---------------------------------------------------------------------------

def test_the_closure_basis_is_the_whole_die_figure_not_the_worst_window():
    """`achieved` is the WORST of the whole-die and worst-window figures. Only
    the whole-die one shares the rule's denominator, so multiplying `achieved`
    by the die area would state a covered area the layout does not have."""
    rec = _disclosure(achieved=0.05)          # a much worse window
    assert rec["achieved"] == 0.05            # judged on the worse number
    assert rec["whole_die_coverage"] == WHOLE
    assert rec["closure"]["macro_covered_area_um2"] == \
        pytest.approx(WHOLE * DIE_AREA)


def test_the_whole_die_figure_is_read_from_the_report_not_from_achieved():
    """`fill_report_facts` takes it from the producer's own whole-die field."""
    report = {
        "keepout": {"measurement_bbox_um": list(DIE)},
        "floor": FLOOR,
        "layers": [{"name": "layer_one", "density_after": 0.22,
                    "worst_window_after": 0.11, "ceiling_any_fill": CEILING}],
    }
    _bbox, facts, why = D.fill_report_facts(report)
    assert why is None
    assert facts["layer_one"]["achieved"] == 0.11      # unchanged behaviour
    assert facts["layer_one"]["whole_die"] == 0.22


# ---------------------------------------------------------------------------
# 4. The additive guard. This one must pass on BOTH trees.
# ---------------------------------------------------------------------------

def test_the_existing_three_numbers_are_untouched():
    # THREE positional arguments on purpose: this call has to be the SAME call
    # on the pre-change tree, or the guard proves nothing about substitution.
    rec = D.density_disclosure(FAMILY, DECK, _facts())[0]
    assert rec["rule"] == "COV1.a"
    assert rec["deck_source"] == "/deck/density.rb"
    assert rec["layer"] == "layer_one"
    assert rec["achieved"] == WHOLE
    assert rec["floor"] == FLOOR
    assert rec["legal_ceiling"] == CEILING
