"""R-0915-25 — the extension-net census, and the half of it that may relay.

MEASURED (sha256 x sky130A, run9's own checkpoint, decoded opcode by opcode):
the two nets `detailed_route` refused with DRT-1010 each carry exactly one
POINT_EXT op, and the coordinate drt calls the non-orthogonal END is that op's
point — paired with the BEGIN of a different path. Each path is orthogonal; the
database holds no diagonal, and neither does any DEF on either side of the
restore. The defect is in the router's wire reader, so until it is fixed the
flow must not hand a restored session a wire the reader mis-pairs.

R-0915-33 — THE RELAY IS RETIRED; THE CENSUS STAYS.

Five cases in this file used to pin the relay's behaviour. They are replaced,
not deleted, by the cases that pin its retirement, and the IDs that went are
named in the commit. What retired it, MEASURED on `subservient` x gf180mcuD
(lane icsub2, r14, on the tree that shipped the relay):

  * DRT-1010 IS COSMETIC. `DRT-0702 Post-route verification: 0 violation(s)` in
    every session that ran one (5 of 5); the gates' own `routed_router.drc.rpt`
    is 0 BYTES; and NO GATE READS DRT-1010 — the only file that mentions it is
    the raw transcript copied to `reports/phase3/drc_router.rpt`, on which
    `drc_report_check` PASSES. sha256 run7 is the same shape.
  * RELAYING DOES NOT CURE IT. r14's failing net `o_sram_addr[7]` was IN the
    first child's 31-net census, WAS relaid, and came back still tripping
    DRT-1010.
  * AND IT COSTS REAL TIMING. Same RTL, same SDC, same input, `SHIP_WNS_BEFORE`
    -3.0296530586256614 (no relay) -> -3.636286041864957 (relay live), the
    convergence plateauing with pass2 worse than pass1, and `sta_corner` SS
    +0.03 (closed) -> -0.50 (VIOLATED).

So every restore deck censuses and names the nets, and NO deck drops a wire.

The negative controls are unchanged: a net with no extension op is never
touched, a census of 0 says so, PG is refused by the shared helper, and the
census asks the decoder for nothing that could segfault.
"""
import shutil
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import phase3_one_shot_runner as R  # noqa: E402

tclsh = shutil.which("tclsh")
needs_tclsh = pytest.mark.skipif(tclsh is None, reason="tclsh not installed")

SPARE_PLAN = {"instances": [{"name": "spare_inverter_0", "cell": "inv_2"},
                            {"name": "spare_dff_0", "cell": "dfxtp_2"}]}


def _reroutes() -> str:
    return R._after_restore_tcl("read_def /x/pre_repair.def\n", SPARE_PLAN,
                                reroutes_immediately=True)


def _no_reroute() -> str:
    return R._after_restore_tcl("read_def /x/pre_repair.def\n", SPARE_PLAN,
                                reroutes_immediately=False)


# --------------------------------------------------------------- the census

def test_the_census_runs_on_every_restore_not_only_the_rerouting_one():
    for tcl in (_reroutes(), _no_reroute()):
        assert "EXT_WIRE_CENSUS:" in tcl
        assert "set _ext_nets {}" in tcl


def test_the_census_names_every_net_it_counts():
    assert "EXT_WIRE_CENSUS_NET: $_ext_nm" in R._ext_wire_census_tcl()


def test_a_census_of_zero_says_so_instead_of_being_silent():
    cen = R._ext_wire_census_tcl()
    assert "EXT_WIRE_CENSUS_EMPTY" in cen
    assert "if {[llength $_ext_nets] == 0} {" in cen


def test_the_census_asks_the_decoder_for_nothing_that_can_segfault():
    """`getTechVia` on a non-via op returns a dangling pointer and SEGFAULTS,
    which `catch` cannot trap. The census needs no coordinates, so it must call
    `next` and nothing else."""
    cen = R._ext_wire_census_tcl()
    assert "odb::dbWireDecoder_next" in cen
    for unsafe in ("getTechVia", "getPoint", "getRect", "getLayer", "getVia",
                   "getITerm", "getBTerm", "getRule"):
        assert f"dbWireDecoder_{unsafe}" not in cen, unsafe


def test_the_decode_loop_is_bounded():
    """F060: a SWIG decode loop that waits for a string sentinel never
    returns."""
    cen = R._ext_wire_census_tcl()
    assert "for {set _ext_i 0} {$_ext_i < 100000} {incr _ext_i} {" in cen


def test_an_unexpected_opcode_abstains_rather_than_relaying_the_wrong_nets():
    """The opcode numbers are pinned empirically, not from a symbolic enum. If
    that alphabet does not hold in some build the census must produce NOTHING,
    not a wrong list."""
    cen = R._ext_wire_census_tcl()
    assert "if {$_ext_op < 0 || $_ext_op > 12} { set _ext_abstain 1; break }" in cen
    assert "EXT_WIRE_CENSUS_ABSTAINED" in cen
    # and abstaining must EMPTY the list, so the relay below relays nothing
    abstain = cen.split("if {$_ext_abstain} {\n  puts \"EXT_WIRE_CENSUS_ABSTAINED")[1]
    assert "set _ext_nets {}" in abstain


# ---------------------------------------------------------------- the relay

def test_no_deck_drops_a_wire_any_more():
    """REPLACES `test_a_deck_that_reroutes_relays_the_censused_nets` and
    `test_a_deck_that_no_reroute_censuses_but_refuses_to_drop_by_name`.

    Those two pinned the asymmetry between a deck that re-routes and one that
    may not. R-0915-33 retires both halves: neither drops anything, so the two
    emit the SAME text and there is no asymmetry left to pin."""
    for build in (_reroutes, _no_reroute):
        text = build()
        assert "EXT_WIRE_RELAID" not in text, build.__name__
        assert "EXT_WIRE_RELAY_DEFERRED" not in text, build.__name__
        assert "EXT_WIRE_RELAY_RETIRED" in text, build.__name__
    assert R._ext_wire_relay_tcl() == R._ext_wire_relay_deferred_tcl()


def test_the_retired_marker_carries_the_census_count_and_the_reason():
    """REPLACES nothing; the disclosure is the whole of what is left. Silence
    would read as 'there were none', which is the failure mode the census was
    built to avoid in the first place."""
    text = R._ext_wire_relay_tcl()
    assert "[llength $_ext_nets]" in text
    assert "NONE was dropped" in text
    assert "cosmetic" in text and "DRT-0702" in text
    assert "router-reader fix" in text


def test_the_emitters_can_no_longer_destroy_a_wire():
    """REPLACES `test_the_relay_iterates_the_census_list_and_never_the_whole_
    block`, `test_the_relay_goes_through_the_one_permitted_clear_helper` and
    `test_the_relay_leaves_the_instances_alone`.

    Those three constrained HOW the relay dropped wiring: only the censused
    nets, only through the one permitted helper, never touching an instance.
    The stronger statement now available is that this path cannot drop a wire
    at all -- it names no destroy, calls no clear helper, and iterates
    nothing."""
    for build in (_reroutes, _no_reroute):
        emitted = build()
        # the CENSUS still walks nets; the relay text must not.
        relay = R._ext_wire_relay_tcl()
        # STRONGER THAN A TOKEN BLACKLIST: every statement this emitter
        # produces is a `puts`. It cannot destroy, clear, iterate or touch
        # anything, because printing is the only thing it does. (A blacklist
        # would also have caught the word "destroyed" in its own comment, which
        # is prose, not a call.)
        stmts = [ln for ln in relay.splitlines()
                 if ln.strip() and not ln.lstrip().startswith("#")]
        assert stmts, relay
        assert all(ln.lstrip().startswith("puts ") for ln in stmts), stmts
        assert "dbWire_destroy" not in relay
        assert "_vibeic_spare_safe_clear_net" not in relay
        assert emitted.count("EXT_WIRE_RELAY_RETIRED") == 1, build.__name__


def test_the_spare_relay_is_NOT_retired_with_it():
    """THE NEGATIVE CONTROL FOR THE SCOPE OF THIS RULING. #2255's spare-wiring
    relay answers a different measured defect and is untouched: a deck that
    re-routes still drops the spare tie-off wiring for the router to lay
    again."""
    text = _reroutes()
    assert "SPARE_WIRING_RELAID" in text
    assert "_vibeic_spare_safe_clear_net" in text


def test_the_shared_helper_still_refuses_pg():
    proc = R._spare_safe_clear_net_proc_tcl()
    assert 'if {$_st eq "POWER" || $_st eq "GROUND"} { return PG }' in proc


# ------------------------------------------------------- shape of the decks

def test_both_compositions_define_ext_nets_before_anything_consumes_it():
    for tcl in (_reroutes(), _no_reroute()):
        assert tcl.index("set _ext_nets {}") < tcl.index("$_ext_nets")


def test_the_spare_relay_still_runs_in_a_rerouting_deck():
    """The extension relay does not replace it: `dont_touch` makes the shared
    clear return PROTECTED, so the spare nets are the only ones a child would
    otherwise never re-route."""
    assert "SPARE_WIRING_RELAID:" in _reroutes()


def test_every_restore_site_goes_through_the_one_composer():
    src = (PROG / "phase3_one_shot_runner.py").read_text()
    assert src.count("_after_restore_tcl(") >= 4  # def + three call sites


@needs_tclsh
@pytest.mark.parametrize("build", [_reroutes, _no_reroute])
def test_the_emitted_tcl_is_balanced(build, tmp_path):
    import subprocess
    deck = tmp_path / "frag.tcl"
    deck.write_text(build())
    probe = tmp_path / "probe.tcl"
    probe.write_text(
        f'set fh [open {deck}]; set t [read $fh]; close $fh\n'
        'puts [expr {[info complete $t] ? "OK" : "UNBALANCED"}]\n')
    out = subprocess.run([tclsh, str(probe)], capture_output=True, text=True)
    assert out.stdout.strip() == "OK", out.stdout + out.stderr
