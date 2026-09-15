"""R-0915-24 — `global_connect` skips a do-not-touch instance, so the spare
pool's power pins stayed floating in every restored session.

The tool says it itself, once per spare, and it had been saying it all along.
MEASURED on r13 (`subservient` x gf180mcuD, lane icsub2, finished logs):

    148 x  [WARNING ODB-0383] spare_<x> is marked do not touch and will be
           skipped in global connections
           -- 37 spares in each of 4 sessions, and in EVERY session the 37
           messages land on the lines immediately after
           `SPARE_DONTTOUCH_REASSERTED: 37 of 37`

and in the candidate ODB the child handed back to the parent, read in a fresh
OpenROAD:

    SPARE_INSTANCES 74        SPARE_DONT_TOUCH 37
    SPARE_PG_ITERMS 296       SPARE_PG_ITERMS_WITH_NO_NET 148     (37 x 4)
    NONSPARE_DONT_TOUCH_INSTANCES 0

The ordering is the whole defect: `_after_restore_tcl` re-asserts `dont_touch`
on every spare FIRST and then calls the R-0915-12 global-connect re-assert, so
that apply runs inside the window where the spares are untouchable.  icsha2
measured the same on sha256 and took it 236 -> 0 by releasing the attribute for
the global-connect call alone.

So the window is opened immediately before `global_connect` and closed
immediately after it -- scoped to THIS run's own spare plan, never to
`dont_touch` in general, and never around a repair or a route.
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

DECK = ('  add_global_connection -net VDD -pin_pattern "^VDD$" -power\n'
        '  add_global_connection -net VSS -pin_pattern "^VSS$" -ground\n')
PLAN = {"instances": [{"name": "spare_inverter_0", "cell": "inv_1"},
                      {"name": "spare_dff_1", "cell": "dffq_2"}]}

LIFT = "SPARE_DONTTOUCH_LIFTED_FOR_GLOBAL_CONNECT"
BACK = "SPARE_DONTTOUCH_REASSERTED_AFTER_GLOBAL_CONNECT"


def _emit(deck, plan=None):
    """Call the helper the way a tree WITHOUT this fix can also be called.

    The three cases below assert a contract that must hold on both sides of
    this change -- no spares, no PDN, valid Tcl -- so they have to RUN on a
    base tree too; a case that dies on the signature has answered nothing
    about the contract it was written for.  The cases that must DISCRIMINATE
    call the two-argument form directly and are meant to fail on base.
    """
    try:
        return R._pg_global_connect_reassert_tcl(deck, plan)
    except TypeError:
        return R._pg_global_connect_reassert_tcl(deck)


def test_the_window_opens_before_global_connect_and_closes_after_it():
    t = R._pg_global_connect_reassert_tcl(DECK, PLAN)
    assert LIFT in t and BACK in t
    assert t.index(LIFT) < t.index("catch {global_connect}") < t.index(BACK)


def test_only_this_runs_own_spares_are_released():
    """The NEGATIVE CONTROL. The postroute_fill block lifts every dont_touch in
    the design and is right to; this one must not. A do-not-touch instance the
    spare plan does not name is never named here."""
    t = R._pg_global_connect_reassert_tcl(DECK, PLAN)
    assert "spare_inverter_0" in t and "spare_dff_1" in t
    # the loop iterates a literal list of THIS plan's names, not a query over
    # the block's do-not-touch instances
    assert "foreach _pg_sn {spare_inverter_0 spare_dff_1}" in t
    assert "isDoNotTouch" in t          # it still only lifts what IS protected
    assert "getInsts" not in t          # ... and never sweeps the whole design
    # a protected instance that is not a spare is not reachable from this text
    for name in ("chip_top_dont_touch_macro", "u_pll", "clk_gate_0"):
        assert name not in t


def test_the_window_holds_nothing_but_the_connect():
    """Never for repair, never for route: between opening and closing there is
    one command."""
    t = R._pg_global_connect_reassert_tcl(DECK, PLAN)
    inside = t[t.index(LIFT):t.index(BACK)]
    assert "global_connect" in inside
    for forbidden in ("detailed_route", "global_route", "repair_design",
                      "repair_timing", "resize", "place_", "route_"):
        assert forbidden not in inside


def test_the_reassert_is_not_conditional_on_the_connect_succeeding():
    """`global_connect` is in its own `catch`, so control reaches the restore
    whether it returned or threw. A spare left unprotected by a failed connect
    is exactly what #2255 exists to prevent."""
    t = R._pg_global_connect_reassert_tcl(DECK, PLAN)
    apply_stmt = t[t.index("if {[catch {global_connect}"):]
    apply_line = apply_stmt[:apply_stmt.index("\n")]
    assert apply_line.rstrip().endswith("}")      # the catch closes on its line
    after = t[t.index(apply_line) + len(apply_line):]
    # the restore is the next statement, not nested in an else of the apply
    assert after.lstrip().startswith("if {[catch {")
    assert "$_pg_si setDoNotTouch true" in after


def test_a_design_with_no_spares_emits_the_same_deck_as_before():
    """No plan, no window: a design that plans no physical spares must not gain
    a byte."""
    t = _emit(DECK)
    assert LIFT not in t and BACK not in t
    assert "setDoNotTouch" not in t
    assert _emit(DECK, {"instances": []}) == t
    assert _emit(DECK, None) == t


def test_a_deck_with_no_pdn_still_emits_nothing():
    """The empty-string contract is unchanged: no rules, no block, even with a
    spare plan in hand."""
    assert _emit("puts hi\n", PLAN) == ""


def test_every_restored_session_gets_the_window():
    """All three restore seams go through `_after_restore_tcl`, so the plan has
    to reach the helper from there or two of them would silently keep the bug."""
    for reroutes in (True, False):
        t = R._after_restore_tcl(DECK, PLAN, reroutes_immediately=reroutes)
        assert LIFT in t, reroutes
        assert BACK in t, reroutes
        # and the re-assert of #2255 still comes first, which is what put the
        # connect inside the window in the first place
        assert t.index("SPARE_DONTTOUCH_REASSERTED:") < t.index(LIFT)


@needs_tclsh
def test_the_emitted_window_is_valid_tcl(tmp_path):
    import subprocess
    t = _emit(DECK, PLAN)
    src = tmp_path / "win.tcl"
    src.write_text(t)
    probe = tmp_path / "parse.tcl"
    probe.write_text(
        'if {[catch {info complete [read [open {%s} r]]} e]} {puts "ERR $e"; '
        'exit 1}\nexit 0\n' % src)
    cp = subprocess.run([tclsh, str(probe)], capture_output=True, text=True)
    assert cp.returncode == 0, cp.stdout + cp.stderr
