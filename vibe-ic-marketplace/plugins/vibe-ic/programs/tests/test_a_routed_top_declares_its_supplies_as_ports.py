"""The IC-arm pin cross-check assumed a convention its own artefact breaks.

MEASURED on a signed-off run (lane icspm5, 2026-09-23). The generated
datasheet stated, all three derived from the same routed DEF and arithmetically
consistent,

    | Pin count (total) | 38 | phase3/stage3/pnr/routed.def |
    | Signal pins       | 36 | phase3/stage3/pnr/routed.def |
    | Supply pins       |  2 | phase3/stage3/pnr/routed.def |

and `release_docs_check --arm ic` refused the release:

    [ERROR] PIN_COUNT_DISAGREES_WITH_NETLIST (spm): PRELIMINARY_DATASHEET.md
    states 'Signal pins' = 36 ...; the netlist view
    `phase3/stage3/pnr/spm_pnr.v` declares 38 logical pin bit(s). A datasheet
    with a pin count no view supports is stale on arrival.

It did not disagree. The routed netlist is

    module chip_top (VDD, VSS, clk, p, rst, y, x);
      inout VDD; inout VSS; input clk; output p; input rst; input y;
      input [31:0] x;

— 36 signal bits plus the two supplies it declares as ports. 38 is the
document's TOTAL, and the document says the total is 38. The check was
applying the IP arm's convention ("a gate-level netlist conventionally carries
the logical interface only", written into both the checker's docstring and the
generated datasheet's own prose) to an artefact that does not follow it, and
would have been deleted as a nuisance for a reason that was not true.

The counts here are that run's, unaltered.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import release_docs_check as C  # noqa: E402

DOC = "PRELIMINARY_DATASHEET.md"

#: The routed top-level netlist as OpenROAD wrote it, supplies and all.
_ROUTED_TOP = """\
module chip_top (VDD,
 VSS,
 clk,
 p,
 rst,
 y,
 x);
 inout VDD;
 inout VSS;
 input clk;
 output p;
 input rst;
 input y;
 input [31:0] x;
endmodule
"""

#: The IP arm's delivered blackbox, which omits them. Both conventions are
#: real and the check may not assume either.
_BLACKBOX = """\
module spm (clk, p, rst, y, x);
 input clk;
 output p;
 input rst;
 input y;
 input [31:0] x;
endmodule
"""


def _rows(signal, supply, total):
    out = []
    for label, value in ((C.PIN_COUNT_LABEL, total),
                         (C.SIGNAL_PIN_LABEL, signal),
                         (C.SUPPLY_PIN_LABEL, supply)):
        if value is not None:
            out.append(C.Row(DOC, label, str(value),
                             "`phase3/stage3/pnr/routed.def`"))
    return out


def _project(tmp_path, netlist=_ROUTED_TOP):
    d = tmp_path / "phase3" / "stage3" / "pnr"
    d.mkdir(parents=True, exist_ok=True)
    (d / "spm_pnr.v").write_text(netlist)
    return tmp_path


def _run(tmp_path, signal, supply, total, netlist=_ROUTED_TOP, arm="ic"):
    findings = []
    state = C._check_pin_count(_project(tmp_path, netlist), arm, "spm",
                               _rows(signal, supply, total), findings)
    return state, [f.rule for f in findings], findings


# ------------------------------------------------------------------ POSITIVE

def test_the_run_that_was_refused_now_agrees(tmp_path):
    """AMENDED after the pre-landing review: the second reading is now
    anchored to the routed DEF's own USE SIGNAL / USE POWER|GROUND split, so
    this staging carries the DEF the datasheet cites. Without it the reading
    is refused, which is the point -- see
    `test_the_second_reading_needs_the_design_behind_it`."""
    state, codes, _ = _run_def(tmp_path, 36, 2, 38)
    assert state == "AGREES", codes
    assert codes == []


def test_the_second_reading_needs_the_design_behind_it(tmp_path):
    """And with no DEF to settle the split there IS no second reading: the
    check falls back to the netlist-equals-signal question it always asked."""
    state, codes, _ = _run(tmp_path, 36, 2, 38)
    assert state == "DISAGREES", codes


def test_a_netlist_that_omits_its_supplies_still_agrees(tmp_path):
    """The IP convention is not broken by accepting the other one: a blackbox
    declaring 36 bits still matches the signal row directly."""
    state, codes, _ = _run(tmp_path, 36, 2, 38, netlist=_BLACKBOX)
    assert state == "AGREES", codes


# ------------------------------------------------------------------ NEGATIVE

def test_an_edited_signal_count_is_still_caught(tmp_path):
    """The defect this check exists for. 30 matches neither the netlist's 36
    signal bits nor 30 + 2, so it DISAGREES -- and the parts check fires too,
    because 30 + 2 is not 38."""
    state, codes, findings = _run(tmp_path, 30, 2, 38)
    assert state == "DISAGREES", codes
    assert "PIN_COUNT_DISAGREES_WITH_NETLIST" in codes
    assert "PIN_COUNT_INTERNALLY_INCONSISTENT" in codes


def test_an_edit_made_arithmetically_consistent_is_still_caught(tmp_path):
    """THE ATTACK THE SECOND READING WOULD INVITE IF IT WERE A SECOND CHANCE.
    Edit all three rows together -- 30 / 2 / 32 -- and the parts check is
    silent. The netlist's 38 bits match neither 30 nor 32, so the
    cross-check still refuses. This is why the change costs nothing."""
    state, codes, findings = _run(tmp_path, 30, 2, 32)
    assert state == "DISAGREES", codes
    assert "PIN_COUNT_INTERNALLY_INCONSISTENT" not in codes
    msg = findings[0].message
    assert "38 logical pin bit(s)" in msg, msg
    assert "sum to 32" in msg, msg
    assert "matches neither" in msg, msg


def test_an_inflated_supply_row_cannot_close_the_gap(tmp_path):
    """And the supply row is not a free variable either: claiming 8 supplies
    to make 30 + 8 reach 38 is caught by the parts check against the total
    the same document states."""
    # AMENDED after the pre-landing review, which showed this was the HOLE,
    # not the guard: 30 + 8 == 38 == the netlist satisfied the old second
    # reading, and the parts check passed too, so a datasheet claiming eight
    # supply pins on a two-supply die was ACCEPTED. The supply row is now read
    # from the routed DEF, so it is not a free variable.
    state, codes, _ = _run_def(tmp_path, 30, 8, 38)
    assert "PIN_COUNT_DISAGREES_WITH_NETLIST" in codes, codes
    state2, codes2, _ = _run_def(tmp_path, 30, 8, 40)
    assert "PIN_COUNT_INTERNALLY_INCONSISTENT" in codes2, codes2


def test_without_a_supply_row_the_old_strictness_is_unchanged(tmp_path):
    """No supply row means no second reading to try: a document that states
    only a signal count is held to the netlist exactly as before."""
    state, codes, findings = _run(tmp_path, 30, None, None)
    assert state == "DISAGREES", codes
    msg = findings[0].message
    assert "matches neither" not in msg, (
        "with no supply row there is no second reading to report")
    # And a signal row that DOES match the netlist's bits is accepted on the
    # first reading exactly as it always was, supply row or none.
    assert _run(tmp_path, 38, None, None)[0] == "AGREES"


def test_the_finding_names_all_three_numbers(tmp_path):
    """'The pin count is wrong' is not actionable. A reader must be able to
    see which of the two readings was tried and what each was."""
    _state, _codes, findings = _run(tmp_path, 30, 2, 32)
    msg = findings[0].message
    assert "'Signal pins' = 30" in msg, msg
    assert "38 logical pin bit(s)" in msg, msg
    assert "sum to 32" in msg, msg


# ===========================================================================
# PRE-LANDING REVIEW, 2026-09-23 — two CONFIRMED highs and a medium. My
# "second reading" was a free variable: nothing anchored it to the design.
# ===========================================================================

_DEF = """\
VERSION 5.8 ;
DESIGN chip_top ;
UNITS DISTANCE MICRONS 2000 ;
PINS 38 ;
- clk + NET clk + DIRECTION INPUT + USE SIGNAL ;
- p + NET p + DIRECTION OUTPUT + USE SIGNAL ;
- rst + NET rst + DIRECTION INPUT + USE SIGNAL ;
- y + NET y + DIRECTION INPUT + USE SIGNAL ;
%s
- VDD + NET VDD + DIRECTION INOUT + USE POWER ;
- VSS + NET VSS + DIRECTION INOUT + USE GROUND ;
END PINS
COMPONENTS 100 ;
END COMPONENTS
END DESIGN
""" % "\n".join(f"- x[{i}] + NET x[{i}] + DIRECTION INPUT + USE SIGNAL ;"
                for i in range(32))


def _project_with_def(tmp_path, netlist=_ROUTED_TOP):
    d = tmp_path / "phase3" / "stage3" / "pnr"
    d.mkdir(parents=True, exist_ok=True)
    (d / "spm_pnr.v").write_text(netlist)
    (d / "routed.def").write_text(_DEF)
    return tmp_path


def _run_def(tmp_path, signal, supply, total, netlist=_ROUTED_TOP, arm="ic"):
    findings = []
    state = C._check_pin_count(_project_with_def(tmp_path, netlist), arm,
                               "spm", _rows(signal, supply, total), findings)
    return state, [f.rule for f in findings], findings


def test_the_honest_run_still_agrees_with_the_def_behind_it(tmp_path):
    state, codes, _ = _run_def(tmp_path, 36, 2, 38)
    assert state == "AGREES", codes


def test_moving_pins_into_the_supply_row_no_longer_passes(tmp_path):
    """HIGH. 30 signal + 8 supply = 38 = the netlist's bits, and the parts
    check is satisfied, so the old second reading said AGREES over a datasheet
    claiming this die has eight supply pins. The DEF marks exactly two
    USE POWER/GROUND. Nothing decided from the design's own data which ports
    are supplies; the supply row was a free variable."""
    state, codes, findings = _run_def(tmp_path, 30, 8, 38)
    assert state == "DISAGREES", codes
    assert "PIN_COUNT_DISAGREES_WITH_NETLIST" in codes, codes
    assert "USE POWER" in findings[0].message or "supply" in findings[0].message


def test_the_ip_arm_never_gets_the_second_reading(tmp_path):
    """HIGH. The IP arm's delivered blackbox omits supplies BY CONTRACT, so
    `netlist == signal + supply` there means the signal row is short by
    exactly the supply count. The old code accepted it on both arms."""
    findings = []
    d = tmp_path / "hardmacro"
    d.mkdir(parents=True, exist_ok=True)
    state = C._check_pin_count(_project_with_def(tmp_path, _BLACKBOX), "ip",
                               "spm", _rows(34, 2, 36), findings)
    assert state != "AGREES", [f.rule for f in findings]


def test_a_netlist_without_power_ports_gets_no_second_reading(tmp_path):
    """The same false accept on the IC arm: OpenROAD `write_verilog` omits
    power ports unless asked, so a 38-bit netlist of pure signal beside a
    datasheet carried forward from a build with two fewer signals (36/2/38)
    matched 36+2 and passed. The second reading is only available when the
    netlist actually DECLARES the pins the DEF marks as supplies."""
    no_pg = ("module chip_top (clk, p, rst, y, x, dbg);\n"
             " input clk;\n output p;\n input rst;\n input y;\n"
             " input [31:0] x;\n input [1:0] dbg;\n"
             "endmodule\n")          # 38 bits, none of them a supply
    state, codes, _ = _run_def(tmp_path, 36, 2, 38, netlist=no_pg)
    assert state == "DISAGREES", codes


def test_an_absent_total_row_does_not_skip_the_arithmetic(tmp_path):
    """MEDIUM (a). The comment claimed `signal + supply` was only reachable
    once the three rows were consistent. The parts check only runs when the
    total row is present, and the second reading never required it. With the
    total written NOT_MEASURED, 30/8/- matched a 38-bit netlist with no
    arithmetic check at all."""
    state, codes, _ = _run_def(tmp_path, 30, 8, None)
    assert state == "DISAGREES", codes


def test_an_inconsistent_document_is_not_also_reported_as_agreeing(tmp_path):
    """MEDIUM (b). 30/8/40 appended INTERNALLY_INCONSISTENT and then the
    second reading appended AGREES and `continue`d, suppressing the
    disagreement finding for the same document."""
    state, codes, _ = _run_def(tmp_path, 30, 8, 40)
    assert "PIN_COUNT_INTERNALLY_INCONSISTENT" in codes, codes
    assert "PIN_COUNT_DISAGREES_WITH_NETLIST" in codes, codes
    assert state == "DISAGREES", state
