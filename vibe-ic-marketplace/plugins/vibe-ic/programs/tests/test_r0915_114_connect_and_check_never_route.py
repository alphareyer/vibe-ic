"""The PG reconnect block connects and checks; it does not route. R-0915-114.

MEASURED on subservient x gf180mcuD as a DIE and reproduced on spm x
gf180mcuD (lanes icsub2 and icspm5, two ICs): the block answered a
PG-terminal delta with a rip-and-relay of EVERY SIGNAL WIRE -- `detailed_route`
with no net list, the guide wrapper global-routing every net first -- and that
re-route is the step that fails. subservient dies `[ERROR DRT-1231] Pin
u_core/_3245_/ZN does not have access point`; spm's first pass dies DRT-0206
with 99 checkConnectivity breaks, ZERO of them on a supply net.

It was never owed. `_vic_pg_on_no_net` counts ONLY POWER/GROUND terminals, so
the delta is PG-only by construction -- ties, fillers, decaps, diodes, spares,
whose PG pins reach the rails BY ABUTMENT -- and `global_connect` lays no
geometry: MEASURED 9498 PG shapes before it and 9498 after, on this design's
own database.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as R  # noqa: E402

SRC = (Path(__file__).resolve().parents[1]
       / "phase3_one_shot_runner.py").read_text()


def _pg_block():
    return R._build_pg_reconnect_tcl(reroute=True)


def test_a_pg_only_delta_emits_no_detailed_route():
    tcl = _pg_block()
    assert "detailed_route -verbose" not in tcl, \
        "the whole-design rip-and-relay is deleted, not conditioned"
    assert "PG_REROUTE_FAILED" not in tcl


def test_it_connects_and_says_so():
    tcl = _pg_block()
    assert "global_connect" in tcl
    assert "PG_CONNECT_OWED" in tcl and "PG_CONNECT_NOT_OWED" in tcl
    assert "it does not route" in tcl


def test_the_checks_are_emitted():
    tcl = _pg_block()
    assert "PG_ABUTMENT_OK" in tcl
    assert "detailed_route_num_drvs" in tcl
    assert "PG_DELTA_DRC_COUNT" in tcl


def test_a_terminal_on_no_net_is_refused_by_name():
    tcl = _pg_block()
    assert "PG_ABUTMENT_NOT_CONNECTED" in tcl
    assert "(on no net)" in tcl


def test_a_terminal_that_overlaps_no_rail_is_refused_by_name():
    """(c) — a cell placed off the rails owes PDN work, not routing."""
    tcl = _pg_block()
    assert "(no rail overlap)" in tcl
    assert "that is PDN work" in tcl
    assert "not signal routing" in tcl


def test_the_abutment_test_is_geometric_not_only_sigtype():
    """sigType alone is what made the sixteen look like they owed a re-route."""
    tcl = _pg_block()
    assert 'getSigType' in tcl
    assert "$_pgab_t getBBox" in tcl
    assert "getSWires" in tcl, "the rail geometry is what it is tested against"


def test_a_moved_drv_count_is_this_steps_verdict():
    tcl = _pg_block()
    assert "PG_DELTA_DRC:" in tcl
    assert "its verdict, not a note" in tcl


def test_the_drv_count_that_cannot_be_read_refuses_nothing():
    """-1 is NOT MEASURED; only a positive count is a verdict."""
    tcl = _pg_block()
    assert "set _pgdrc -1" in tcl
    assert "if {$_pgdrc > 0} {" in tcl


def test_the_emitted_block_is_balanced_tcl():
    tcl = _pg_block()
    bal = sum(l.count("{") - l.count("}") for l in tcl.splitlines())
    assert bal == 0, f"{bal:+d} braces"


# ── (b) the antenna stage answers for its own wires ───────────────────────
def _antenna_cmds():
    """The emitted block's COMMAND lines only -- its Tcl comments record the
    history of the routes that were deleted from it, names included."""
    return "\n".join(ln for ln in _antenna_block().splitlines()
                     if not ln.lstrip().startswith("#"))


def _antenna_block():
    return R._antenna_repair_tcl(R.PdkConfig(
        name="gf180mcuD", liberty="/l", tech_lef="/t", cell_lef="/c",
        cell_gds=None, site="S", drc_deck=None, metal_prefix="M",
        tapcell_master="fx__filltie", antenna_diode_cell="fx__antenna"))


def test_the_antenna_stage_no_longer_swallows_its_own_reroute_failure():
    """MEASURED (int6): the native -reroute threw DRT-0206, the fallback then
    threw DRT-1231 TWICE, both swallowed, and the diodes were left for the PG
    block's re-route -- which R-0915-114(a) deleted.

    R-0915-114(b) answered that by turning the fallback's failure into a
    RAISED verdict, ANTENNA_REROUTE_FAILED. R-0915-116(2)(iii) goes one step
    further and deletes the fallback itself -- measured destroying a converged
    route on spm (run12: GRT-0012 "Found 0 antenna violations", then the
    fallback, then DRT-0206) and costing subservient its routed.def on int7.
    So there is no whole-design route left here whose failure could be
    swallowed OR raised. THE PROPERTY IS UNCHANGED and is asserted on the
    mechanism that replaced it: the raise is judged by connectivity, and the
    branch that leaves a mutated route behind RECORDS its refusal rather than
    printing a note."""
    # no emitted line swallows a re-route failure as a note ...
    assert 'puts \\"REPAIR_ANTENNA_REROUTE_NONFATAL' not in SRC
    # ... and this stage runs no whole-design route at all any more. The
    # assertions are on the EMITTED BLOCK'S COMMANDS, not on the source file
    # and not on the deck's comments: both keep the old marker's name in the
    # note that records why it went.
    assert "ANTENNA_REROUTE_FAILED" not in _antenna_cmds()
    assert "detailed_route" not in _antenna_cmds()
    # the exit that replaced it is a recorded refusal, not a note.
    t = _antenna_block()
    i = t.index('puts "ANTENNA_DIODE_ROLLED_BACK')
    assert "set _ant_refused" in t[i:i + 600]


def test_the_antenna_stage_still_tries_its_native_reroute_first():
    assert "-reroute" in SRC, "the fork's tool-native incremental path stays"
