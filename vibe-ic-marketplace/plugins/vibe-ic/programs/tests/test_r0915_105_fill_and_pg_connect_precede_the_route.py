"""R-0915-105: fillers, taps and the PG connect run BEFORE the first route.

MEASURED on a die whose core is its ring's whole interior (run9, 2026-09-21).
The flow inserted 53,993 fillers, repaired well-tie coverage and re-applied
`global_connect` AFTER the design was routed, and then owed itself a
whole-design `detailed_route` so the spacing engine could see rails that had
just become net-owned shapes. That re-route failed its own connectivity check —
`[ERROR DRT-0206] checkConnectivity error.` — and the failure was SWALLOWED as
`PG_REROUTE_NONFATAL`, so:

  * 105 signal nets were left with no wire and no abutment (probed at the stage
    boundaries: intact at `BEGIN_postroute_fill`, gone at
    `BEGIN_postroute_named_violation_reroute`),
  * nothing said so for another 3877 log lines, where a different checker
    reported `NAMED_VIOL_REROUTE_INCOMPLETE: 105`,
  * no `routed.def` was written and `drc` / `lvs` were never dispatched.

And the re-connect that owed all this had moved **12 terminals of 23,766** onto
a net (measured with the audit's own predicate, before and after).

A rollback is not the repair and this repo already measured why: in the adopt
script's own words, `dbChip_destroy` + `read_db` restores the wires and then
kills STA for the rest of the session (`[CRITICAL ORD-2008]`), and ODB's ECO
journal restores neither state. "The loop cannot restore a best it walked past
— it must not walk past it."

So the order changes instead: place -> fillers/taps -> PG connect -> route. The
residual post-route connect stays for instances a later step creates (antenna
diodes, a repair buffer), it re-routes ONLY when the delta says terminals
actually changed, and a re-route that fails is a VERDICT of that step, by name.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import phase3_one_shot_runner as R  # noqa: E402

SRC = (PROGRAMS / "phase3_one_shot_runner.py").read_text()


def _template() -> str:
    """The PnR script template, as the builder's own source carries it."""
    import inspect
    return inspect.getsource(R._build_pnr_tcl_text)


# ------------------------------------------------- (1) the order, in the script

def test_fillers_and_the_connect_run_before_the_first_detailed_route():
    t = _template()
    fill = t.index("{filler_block}{pg_preconnect_block}")
    route = t.index('puts "{_PNR_STAGE_MARKER} detailed_route"')
    assert fill < route, "fillers/taps/PG connect must precede detailed_route"
    # and the post-route stage no longer carries them
    post = t.index('puts "{_PNR_STAGE_MARKER} postroute_fill"')
    tail = t[post:]
    assert "{filler_block}" not in tail
    assert "{pg_reconnect_block}" in tail          # the residual connect stays


def test_the_new_stage_is_in_the_flows_own_stage_order():
    order = R._PNR_STAGE_ORDER
    assert "preroute_fill" in order
    assert order.index("preroute_fill") < order.index("detailed_route")
    assert order.index("global_route") < order.index("preroute_fill")
    # the post-route stage is not deleted: the residual connect + audit live there
    assert "postroute_fill" in order


def test_the_pre_route_connect_has_no_reroute_because_there_is_no_route_yet():
    pre = R._build_pg_reconnect_tcl(reroute=False)
    # INVOCATION, not substring: R-0915-123 reads the router's DRV COUNTER
    # (`detailed_route_num_drvs`) to measure this block's delta, and that name
    # contains the command's name without ever calling it. The property here
    # is that the pre-route connect runs no ROUTE.
    assert not any(ln.strip().split()[:1] == ["detailed_route"]
                   for ln in pre.replace("[", " \n").replace("]", " \n")
                                .replace("{", " \n").replace("}", " \n")
                                .replace(";", "\n").splitlines() if ln.strip())
    assert "global_connect" in pre and "PG_NET_OWNERSHIP_AUDIT" in pre


# ------------------------------------- (3) delta 0 -> no re-route, and it says so

def test_the_reroute_is_owed_only_when_the_reconnect_changed_terminals():
    """SUPERSEDED IN ITS REMEDY BY R-0915-114, NOT IN ITS PRINCIPLE.

    R-0915-105 established that the DELTA decides. R-0915-114 measured WHICH
    delta this can ever be: `_vic_pg_on_no_net` counts ONLY POWER/GROUND
    terminals, so the delta is PG-only by construction, and `global_connect`
    lays no geometry (MEASURED: 9498 PG shapes before it and 9498 after). The
    remedy for such a delta is a CONNECT and two CHECKS, never a whole-design
    re-route -- which on two ICs and two PDKs is the step that fails.

    The delta machinery this test was written to protect is unchanged; what it
    now gates is the connect-and-check path.
    """
    rr = R._build_pg_reconnect_tcl(reroute=True)
    # the delta is measured with the audit's own predicate, not a second one
    assert 'proc _vic_pg_on_no_net' in rr
    assert 'set _pg_bad_before [_vic_pg_on_no_net]' in rr
    assert 'set _pg_bad_after [_vic_pg_on_no_net]' in rr
    assert 'set _pg_delta [expr {$_pg_bad_before - $_pg_bad_after}]' in rr
    # zero -> the route is kept, and the run SAYS nothing was owed
    assert 'if {$_pg_delta <= 0} {' in rr
    assert 'PG_CONNECT_NOT_OWED' in rr
    body = rr[rr.index('if {$_pg_delta <= 0} {'):]
    not_owed, owed = body.index("PG_CONNECT_NOT_OWED"), body.index("} else {")
    assert not_owed < owed
    # and NEITHER branch routes any more
    assert "detailed_route -verbose" not in rr, (
        "R-0915-114: this block connects and checks; it does not route")


def test_a_failed_reroute_is_a_named_verdict_and_is_never_swallowed():
    """The NO-SWALLOW principle survives; the thing that may fail changed.

    R-0915-105 refused to let a failed re-route pass as a note. R-0915-114
    deletes the re-route from this block entirely, so what this block can now
    fail on are its two CHECKS -- and neither is swallowed: a PG terminal that
    reaches no rail raises PG_ABUTMENT_NOT_CONNECTED, and a moved DRV count
    raises PG_DELTA_DRC. The stage that still routes (antenna repair) answers
    for its own wires with ANTENNA_REROUTE_FAILED, which R-0915-114(b) turned
    from a swallowed note into a verdict.
    """
    rr = R._build_pg_reconnect_tcl(reroute=True)
    assert 'puts "PG_REROUTE_NONFATAL' not in rr
    assert 'error "PG_ABUTMENT_NOT_CONNECTED:' in rr
    assert 'error "PG_DELTA_DRC:' in rr
    # each error text names the consequence rather than sending the reader away
    assert "not signal routing" in rr
    assert "its verdict, not a note" in rr


def test_the_failure_path_does_not_try_to_roll_back():
    """The repo measured that a mid-session rollback destroys STA for the rest
    of the session; this repair must not reintroduce one."""
    rr = R._build_pg_reconnect_tcl(reroute=True)
    # the COMMENT may cite the measurement that rules a rollback out; the CODE
    # must not attempt one, so the comment lines are stripped before asking.
    code = "\n".join(l for l in rr.splitlines()
                     if not l.lstrip().startswith("#"))
    for forbidden in ("dbChip_destroy", "read_db", "undoEco", "beginEco"):
        assert forbidden not in code, f"{forbidden} is not a repair here"
    assert "dbChip_destroy" in rr, "the reason it is not used stays on record"


# ------------------------------------------------ (2) the other direction holds

def test_the_audit_is_unchanged_and_still_unconditional():
    """The PG question itself is not weakened: the ownership audit runs in both
    forms, before and after the route, whatever the delta says."""
    for rr in (True, False):
        t = R._build_pg_reconnect_tcl(reroute=rr)
        assert "PG_NET_OWNERSHIP_AUDIT" in t
        assert "getNet] eq \"NULL\"" in t


def test_the_emitted_script_carries_no_pdk_or_design_literal():
    """Chip-AGNOSTIC, including the measurement in the new comments."""
    t = R._build_pg_reconnect_tcl(reroute=True)
    for token in ("gf180", "sky130", "ihp", "spm", "subservient", "caravel"):
        assert token not in t.lower(), token


def test_both_pg_blocks_come_from_one_builder():
    """One implementation, two call sites — not a second copy that can drift."""
    assert SRC.count("def _build_pg_reconnect_tcl(") == 1
    assert "pg_reconnect_block = _build_pg_reconnect_tcl()" in SRC
    assert "pg_preconnect_block = _build_pg_reconnect_tcl(reroute=False)" in SRC
    assert re.search(r"pg_preconnect_block=pg_preconnect_block,", SRC)

def test_every_stage_breadcrumb_this_change_touches_is_actually_emitted():
    """`_pnr_stage_begin`/`_pnr_stage_end` emit a Tcl COMMENT, so anything
    sharing their line is commented out. MEASURED on the first cut of this
    change: `{_pnr_stage_begin("preroute_fill")}puts "..."` put both the new
    breadcrumb AND the pre-existing `detailed_route` one behind a `#`, and the
    run's own log carried neither — `_pnr_stage_from_log` would have lost the
    stage it reports when PnR dies."""
    t = _template()
    for label in ("preroute_fill", "detailed_route"):
        hits = [l for l in t.splitlines()
                if f'puts "{{_PNR_STAGE_MARKER}} {label}"' in l]
        assert hits, f"no breadcrumb emitted for {label}"
        for line in hits:
            stripped = line.lstrip()
            assert not stripped.startswith("#"), (label, line)
            assert "<<<PNR_STAGE" not in line.split("puts")[0], (
                f"{label}'s puts shares a line with a stage comment marker, "
                f"which comments it out: {line!r}")
