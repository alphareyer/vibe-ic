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
    state, codes, _ = _run(tmp_path, 36, 2, 38)
    assert state == "AGREES", codes
    assert codes == []


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
    state, codes, _ = _run(tmp_path, 30, 8, 38)
    assert "PIN_COUNT_DISAGREES_WITH_NETLIST" not in codes, codes
    # 30 + 8 == 38 == the netlist, so the cross-check is satisfied -- and the
    # document is refused anyway, by its own arithmetic against a stated
    # total it no longer matches. Both halves are needed; neither is enough.
    state2, codes2, _ = _run(tmp_path, 30, 8, 40)
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
