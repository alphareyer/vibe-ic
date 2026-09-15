"""R-0915-25 — the extension-net census, and the half of it that may relay.

MEASURED (sha256 x sky130A, run9's own checkpoint, decoded opcode by opcode):
the two nets `detailed_route` refused with DRT-1010 each carry exactly one
POINT_EXT op, and the coordinate drt calls the non-orthogonal END is that op's
point — paired with the BEGIN of a different path. Each path is orthogonal; the
database holds no diagonal, and neither does any DEF on either side of the
restore. The defect is in the router's wire reader, so until it is fixed the
flow must not hand a restored session a wire the reader mis-pairs.

Both directions are pinned here:

  * a deck that re-routes in the same pass CENSUSES and RELAYS;
  * a deck that may not route at all CENSUSES and REFUSES BY NAME — run7's
    `pnr_sdr_adopt_2` restored a checkpoint and ran ZERO `detailed_route`, so a
    dropped conductor there would never be laid again.

and the negative controls: a net with no extension op is never touched, a
census of 0 says so, PG is refused by the shared helper, and the census asks the
decoder for nothing that could segfault.
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

def test_a_deck_that_reroutes_relays_the_censused_nets():
    tcl = _reroutes()
    assert "EXT_WIRE_RELAID:" in tcl
    assert "EXT_WIRE_RELAY_DEFERRED" not in tcl


def test_a_deck_that_may_not_route_censuses_but_refuses_to_drop_by_name():
    tcl = _no_reroute()
    assert "EXT_WIRE_RELAY_DEFERRED" in tcl
    assert "EXT_WIRE_RELAID:" not in tcl
    assert "does not re-route in the same pass" in tcl


def test_the_relay_iterates_the_census_list_and_never_the_whole_block():
    """The negative control for "a net without an extension op is untouched":
    the relay loop must walk the CENSUS, not the database."""
    rel = R._ext_wire_relay_tcl()
    assert "foreach _ext_nm $_ext_nets {" in rel
    assert "getNets" not in rel


def test_the_relay_goes_through_the_one_permitted_clear_helper():
    """There is exactly ONE `odb::dbWire_destroy` site in the program, and it
    refuses POWER and GROUND by the net's own SigType. A second one here would
    route around that refusal."""
    rel = R._ext_wire_relay_tcl()
    assert "_vibeic_spare_safe_clear_net $_ext_rn 1" in rel
    body = rel.split("proc _vibeic_spare_safe_clear_net")[-1]
    after_proc = body.split("}\n", 1)[-1]
    assert "odb::dbWire_destroy" not in after_proc.split("set _ext_relaid")[-1]


def test_the_relay_leaves_the_instances_alone():
    rel = R._ext_wire_relay_tcl()
    for forbidden in ("unset_dont_touch", "set_dont_touch", "delete_inst",
                      "swap_master", "place_inst"):
        assert forbidden not in rel, forbidden
    assert "the instances are untouched" in rel


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
