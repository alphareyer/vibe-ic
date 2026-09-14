"""The PDK reference compares the arc the DECK actually stimulated (icspm2).

MEASURED FAILURE THIS PINS
==========================
A completed gf180mcuD run of `spm` published

    SPICE_STA_CRITICAL_MISMATCH: SPICE=3.287294ns vs Liberty+SPEF=5.628571429ns
    (-65.216303%; derived tolerance 9.850635%)

The -65 % is not a SPICE-vs-STA number. It is SPICE against a DERIVED
reference: `design_reference_ns = liberty_cone x (1 + pdk_characterisation.
gap_pct)` = 5.628571 x 1.679054 = 9.450675, and 67.9 % of that gap came from ONE
of the four reference cells:

    buf_2    cell_rise/I    ratio 0.918   gap   -8.19 %
    nand2_1  cell_fall/A1   ratio 0.805   gap  -19.51 %
    nor2_1   cell_rise/A2   ratio 0.810   gap  -18.97 %
    xor3_1   cell_fall/A3   ratio 4.345   gap +334.45 %      <-- 20x out

WHY. The deck instantiates, from the PDK's own `.SUBCKT …__xor3_1 A1 A2 A3 Z
VDD VNW VPW VSS`:

    x0f  0  0  si0f  so0f  vdd vdd 0 0  gf180mcu_fd_sc_mcu7t5v0__xor3_1

i.e. A1 = 0, A2 = 0 (`tie_value_for_cell`: XOR ties low), A3 toggling. The
liberty declares SIX `timing()` groups for `related_pin: A3`, in two
oppositely-unate families, and at the very grid point the gate used
(index_1[5] = 1.769 ns, index_2[1] = 0.001853 pF):

    when '!A1&A2'   negative_unate  cell_fall 0.1888   <-- the FIRST group, read
    when 'A1&!A2'   negative_unate  cell_fall 0.1888
    when  None      negative_unate  cell_fall 0.1888
    when '!A1&!A2'  positive_unate  cell_fall 1.159    <-- what the deck SET
    when 'A1&A2'    positive_unate  cell_fall 1.159
    when  None      positive_unate  cell_fall 1.159

`stage_nldm_table` took the first group matching `related_pin` and ignored its
`when`, so a SPICE measurement of one logical condition was divided into a
Liberty number for a different, oppositely-unate one. ngspice measured
0.820247 ns: against 0.1888 that is +334 %; against 1.159 it is -29.2 %, the
same direction and order of magnitude as its three siblings.

A two-input NAND/NOR has one family, so the defect is invisible on them. It
appears the first time a path uses a cell whose arcs are `when`-split.
"""
import inspect
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import spice_correlation_check as scc  # noqa: E402


def _arc(*, when, sense, fall, rise, related="A3"):
    return f"""\
    timing() {{
      related_pin : "{related}" ;
      timing_sense : {sense} ;
      timing_type : combinational ;
{'      when : "' + when + '" ;' if when else ''}
      cell_fall(t) {{
        index_1 ("0.1, 1.769");
        index_2 ("0.001853, 0.1905");
        values ("0.05, 0.5", \\
                "{fall}, 2.0");
      }}
      cell_rise(t) {{
        index_1 ("0.1, 1.769");
        index_2 ("0.001853, 0.1905");
        values ("0.05, 0.5", \\
                "{rise}, 2.0");
      }}
    }}
"""


# The real xor3_1 arc set, reduced to the two grid points that matter.
XOR3 = """\
  cell (gf180mcu_fd_sc_mcu7t5v0__xor3_1) {
    pin(A1) { direction : input ; }
    pin(A2) { direction : input ; }
    pin(A3) { direction : input ; }
    pin(Z)  { direction : output ; function : "(A1^A2^A3)" ; }
""" + _arc(when="!A1&A2", sense="negative_unate", fall="0.1888", rise="0.6735") \
    + _arc(when="A1&!A2", sense="negative_unate", fall="0.1888", rise="0.6735") \
    + _arc(when=None,     sense="negative_unate", fall="0.1888", rise="0.6735") \
    + _arc(when="!A1&!A2", sense="positive_unate", fall="1.159", rise="0.7156") \
    + _arc(when="A1&A2",   sense="positive_unate", fall="1.159", rise="0.7172") \
    + _arc(when=None,      sense="positive_unate", fall="1.159", rise="0.7172") \
    + "  }\n"

# A single-arc cell: the shape the old code was right about.
NAND2 = """\
  cell (gf180mcu_fd_sc_mcu7t5v0__nand2_1) {
    pin(A1) { direction : input ; }
    pin(A2) { direction : input ; }
    pin(Z)  { direction : output ; function : "(!(A1 A2))" ; }
""" + _arc(when=None, sense="negative_unate", fall="0.3763", rise="0.5",
           related="A1") + "  }\n"

LIB = "library (x) {\n" + XOR3 + NAND2 + "}\n"


def _stage(cell, pin, transition):
    return {"cell": cell, "toggle_pin": pin, "out_pin": "Z",
            "transition": transition, "input_slew_ns": 1.769,
            "sta_load_pf": 0.001853, "sta_delay_ns": 1.0}


def _grid(stage, lib=LIB, i=1, j=0):
    got = scc.stage_nldm_table(lib, stage)
    assert got is not None, f"no table selected for {stage}"
    name, table = got
    return name, table["values"][i][j]


# ---------------------------------------------------------------------------
# DIRECTION 1 — the mis-bound arc is gone
# ---------------------------------------------------------------------------
def test_the_xor3_reference_is_the_arc_the_deck_set(tmp_path):
    """A1 = A2 = 0 is `!A1&!A2`, the positive_unate family: 1.159, not 0.1888."""
    name, v = _grid(_stage("gf180mcu_fd_sc_mcu7t5v0__xor3_1", "A3", "fall"))
    assert name == "cell_fall"
    assert v == 1.159, (
        f"selected {v}; 0.1888 is the negative_unate arc the deck did NOT set")


def test_the_deck_tie_is_read_from_the_deck_builder_not_re_derived(tmp_path):
    """`deck_side_input_state` must agree with `_installed_pin_node`, the one
    function that decides the tie — two spellings would drift."""
    fn = getattr(scc, "deck_side_input_state", None)
    if fn is None:
        pytest.skip("pre-fix tree has no deck_side_input_state")
    st = _stage("gf180mcu_fd_sc_mcu7t5v0__xor3_1", "A3", "fall")
    state = fn(st, scc.extract_cell_block(LIB, st["cell"]))
    assert state == {"A1": 0, "A2": 0}
    for pin, want in state.items():
        node = scc._installed_pin_node(pin, st, "IN", "OUT")
        assert node == ("vdd" if want else "0"), (pin, node, want)


def test_a_nand2_whose_side_input_is_tied_HIGH_is_still_selected(tmp_path):
    """The tie is per-family (`tie_value_for_cell`): AND/NAND tie high. The
    selection must not assume zeros."""
    fn = getattr(scc, "deck_side_input_state", None)
    if fn is None:
        pytest.skip("pre-fix tree has no deck_side_input_state")
    st = _stage("gf180mcu_fd_sc_mcu7t5v0__nand2_1", "A1", "fall")
    assert fn(st, scc.extract_cell_block(LIB, st["cell"])) == {"A2": 1}


# ---------------------------------------------------------------------------
# DIRECTION 2 — nothing else moves, and an undecidable `when` selects nothing
# ---------------------------------------------------------------------------
def test_a_single_arc_cell_is_unchanged(tmp_path):
    name, v = _grid(_stage("gf180mcu_fd_sc_mcu7t5v0__nand2_1", "A1", "fall"))
    assert (name, v) == ("cell_fall", 0.3763)


def test_an_unreadable_when_does_not_select_and_does_not_crash(tmp_path):
    lib = LIB.replace('when : "!A1&!A2" ;', 'when : "A1 @@ A2" ;')
    name, v = _grid(_stage("gf180mcu_fd_sc_mcu7t5v0__xor3_1", "A3", "fall"), lib)
    # falls back to first-match — the pre-fix behaviour, never a guess
    assert (name, v) == ("cell_fall", 0.1888)


def test_a_when_naming_a_pin_the_deck_does_not_fix_selects_nothing(tmp_path):
    lib = LIB.replace('when : "!A1&!A2" ;', 'when : "!A1&!A9" ;')
    _name, v = _grid(_stage("gf180mcu_fd_sc_mcu7t5v0__xor3_1", "A3", "fall"), lib)
    assert v == 0.1888


def test_a_cell_the_liberty_does_not_carry_is_None(tmp_path):
    assert scc.stage_nldm_table(LIB, _stage("nope", "A1", "fall")) is None


def test_the_rise_transition_picks_the_rise_table_of_the_same_arc(tmp_path):
    name, v = _grid(_stage("gf180mcu_fd_sc_mcu7t5v0__xor3_1", "A3", "rise"))
    assert name == "cell_rise"
    assert v == 0.7156, "the '!A1&!A2' arc's rise value, not another family's"


# ---------------------------------------------------------------------------
# The `when` grammar, stated as a table
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("expr,expected", [
    ("!A1&!A2", True), ("!A1&A2", False), ("A1&!A2", False), ("A1&A2", False),
    ("!A1 & !A2", True), ("A1|A2", False), ("!A1|A2", True),
    ("A1^A2", False), ("!A1^A2", True), ("(!A1)&(!A2)", True),
    ("A1'&A2'", True), ("A1*A2", False), ("A1+A2", False),
    ("!A1 !A2", True),                      # juxtaposition is AND
    ("1", True), ("0", False),
])
def test_when_grammar(expr, expected):
    fn = getattr(scc, "evaluate_when", None)
    if fn is None:
        pytest.skip("pre-fix tree has no evaluate_when")
    assert fn(expr, {"A1": 0, "A2": 0}) is expected


@pytest.mark.parametrize("expr", ["", "   ", "A9", "A1 @ A2", "(A1", "A1)",
                                  "&A1", None, 7])
def test_an_undecidable_when_is_None_never_a_guess(expr):
    fn = getattr(scc, "evaluate_when", None)
    if fn is None:
        pytest.skip("pre-fix tree has no evaluate_when")
    assert fn(expr, {"A1": 0, "A2": 0}) is None


# ---------------------------------------------------------------------------
# The property, over cells the OLD code also runs — so the control arm is not
# carried by one cell and one symbol.
# ---------------------------------------------------------------------------
# An XOR2: same two families, one side input, tied LOW by `tie_value_for_cell`.
XOR2 = """\
  cell (lib__xor2_1) {
    pin(A1) { direction : input ; }
    pin(A2) { direction : input ; }
    pin(Z)  { direction : output ; function : "(A1^A2)" ; }
""" + _arc(when="A1", sense="negative_unate", fall="0.200", rise="0.300",
           related="A2") \
    + _arc(when="!A1", sense="positive_unate", fall="1.400", rise="1.500",
           related="A2") + "  }\n"

# An AND-family cell whose side input is tied HIGH: the satisfied `when` is the
# SECOND group, so the tie DIRECTION has to be read, not assumed to be zero.
AND2 = """\
  cell (lib__and2_1) {
    pin(A1) { direction : input ; }
    pin(A2) { direction : input ; }
    pin(Z)  { direction : output ; function : "(A1 A2)" ; }
""" + _arc(when="!A2", sense="positive_unate", fall="9.900", rise="9.900",
           related="A1") \
    + _arc(when="A2", sense="positive_unate", fall="0.4400", rise="0.5500",
           related="A1") + "  }\n"

LIB2 = "library (y) {\n" + XOR2 + AND2 + "}\n"


def test_an_xor2_picks_the_family_its_tied_low_side_input_selects():
    """Not xor3-specific: any `when`-split cell tied low takes the `!A1` arc."""
    name, v = _grid(_stage("lib__xor2_1", "A2", "fall"), LIB2)
    assert (name, v) == ("cell_fall", 1.400), (
        "0.200 is the 'A1' arc, which the deck's A1 = 0 tie does NOT satisfy")


def test_an_and2_tied_HIGH_takes_the_second_group_not_the_first():
    """The tie DIRECTION is read. `tie_value_for_cell` ties AND/NAND high, so
    the satisfied `when` is `A2` — the SECOND group — and first-match is wrong
    in the opposite direction from the XOR case."""
    name, v = _grid(_stage("lib__and2_1", "A1", "fall"), LIB2)
    assert (name, v) == ("cell_fall", 0.4400), (
        "9.900 is the '!A2' arc, which the deck's A2 = 1 tie does NOT satisfy")


def test_the_and2_rise_arc_follows_the_same_group():
    name, v = _grid(_stage("lib__and2_1", "A1", "rise"), LIB2)
    assert (name, v) == ("cell_rise", 0.5500)


def test_the_measured_xor3_ratio_lands_in_family_with_its_siblings():
    """The whole point, in one number. ngspice measured 0.820247 ns on the
    reference deck; the published run divided it by 0.1888 and reported
    +334 %, while its three sibling cells were -8.2 %, -19.5 % and -19.0 %."""
    _name, liberty_ns = _grid(
        _stage("gf180mcu_fd_sc_mcu7t5v0__xor3_1", "A3", "fall"))
    gap_pct = (0.820247 / liberty_ns - 1.0) * 100.0
    assert -40.0 < gap_pct < 0.0, (
        f"gap {gap_pct:.2f}% against liberty {liberty_ns}; the siblings are "
        f"-8.19 / -19.51 / -18.97 %")
