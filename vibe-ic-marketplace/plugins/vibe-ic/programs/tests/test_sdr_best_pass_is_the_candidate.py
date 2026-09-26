#!/usr/bin/env python3
"""R-0915-68 — the child offers its BEST pass, not the pass it happened to end on.

MEASURED (opentitan_aes x sky130A, 2026-09-16). The child's bounded DRV loop
drove DRV 41,956 -> 19,013 -> 2,647, and its PASS-1 route closed at **0 router
violations** — strictly better than the `before=2` the transaction judges
against. Passes 2 and 3 then re-degraded the route to 11 and 39. The child
wrote `candidate.odb` from whatever state it ENDED in, so a pass that had
already reached clean was discarded because a later pass spoiled it, and the
parent refused a candidate that had been worth accepting two passes earlier.

The fix is NOT an in-session rollback. The same deck's antenna loop already
records the measurement that forbids one: `odb::dbChip_destroy` + `read_db`
restores the routing and then `report_worst_slack -max` dies with
`[CRITICAL ORD-2008] unknown master term type`, and the ECO journal rolls
instances back while leaving the re-routed wires, so `check_antennas` reads a
third state that never existed. The child therefore never walks back — it
writes one ODB per pass and CHOOSES WHICH FILE becomes `candidate.odb`. The
parent restores it in a fresh adopt session, which is where a restore is safe.

These tests drive the REAL emitted deck under tclsh with the shared stub, so
they pin behaviour rather than text: a fixture makes the router's DRC report
say different things on successive passes, and the assertions are on what the
deck DID.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import phase3_one_shot_runner as R  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tcl_walk                                 # noqa: E402
from _pnr_tcl_stub import STUB as _STUB          # noqa: E402
from _pnr_tcl_stub import ANNOTATED_SESSION  # noqa: E402
from test_sdr_checkpoint_and_child import _full_pnr_tcl  # noqa: E402

# NO `skipif(tclsh is None)` HERE, DELIBERATELY. A tool-absent skip turns a
# test that COULD NOT RUN into a green line on the very host doing the
# measuring, and nothing downstream can tell it from a test that ran and
# passed. `_tcl_walk.walk` resolves tclsh the way the RUNNER does -- host
# first, then the pinned EDA container through the runner's own exec seam --
# and raises `TclNotMeasured` (an AssertionError, so pytest reports a FAILURE)
# naming both routes when neither answers.

SITE = "postroute_drv_repair"

# The deck IS the deck: the walker's only job is to source it, so the text
# under test travels as `deck_text` and the container route gets it through
# the helper's heredoc rather than through a mount that does not exist.
_WALKER = "source [lindex $argv 0]\n"


class _Ran:
    """What the walk produced. No returncode: `walk` does not expose one, and
    the deck's own last marker is the stronger statement anyway -- a deck that
    died mid-way cannot print it."""

    def __init__(self, stdout, stderr, route):
        self.stdout, self.stderr, self.route = stdout, stderr, route


def _run_child(tmp_path: Path, drc_counts, *, drv_counts=None):
    """Run the real child deck with a scripted per-pass router-DRC sequence.

    `drc_counts` is what the router's DRC report says after each pass's route.
    `drv_counts` is what the DRV census says at the TOP of each pass (it drives
    the loop's own converged-exit); it defaults to a sequence that keeps the
    loop running so the route counts are what decides.
    """
    deck = _full_pnr_tcl(tmp_path)
    ckpt = tmp_path / "ckpt.def"
    ckpt.write_text("CHECKPOINT\n")
    child = R._build_pnr_sdr_child_tcl_text(
        deck, checkpoint_def_c=str(ckpt), stage=SITE)
    drv = drv_counts or [9] * (len(drc_counts) + 2)
    harness = (
        "set ::DRC_SEQ {%s}\n" % " ".join(str(c) for c in drc_counts) +
        "set ::DRC_I 0\n"
        # The deck probes `info body detailed_route` for `-output_drc` and only
        # asks for a report when the build supports it, so the stand-in must
        # carry that token in its BODY — that is the real gate, and a fixture
        # that skipped it would test a path the deck never takes.
        "proc detailed_route {args} {\n"
        "  # -output_drc\n"
        "  set path \"\"\n"
        "  foreach {k v} $args { if {$k eq \"-output_drc\"} { set path $v } }\n"
        "  if {$path eq \"\"} { return }\n"
        "  set n 0\n"
        "  if {$::DRC_I < [llength $::DRC_SEQ]} {\n"
        "    set n [lindex $::DRC_SEQ $::DRC_I]\n"
        "  } else { set n [lindex $::DRC_SEQ end] }\n"
        "  incr ::DRC_I\n"
        "  file mkdir [file dirname $path]\n"
        "  set f [open $path w]\n"
        "  for {set i 0} {$i < $n} {incr i} {\n"
        "    puts $f \"violation type: Metal Spacing\"\n"
        "    puts $f \"\\tsrcs: net:fixture_net_$i\"\n"
        "    puts $f \"\\tbbox = (0, 0) - (1, 1) on Layer met1\"\n"
        "  }\n"
        "  close $f\n"
        "}\n"
        # write_db made real so WHICH file became the candidate is observable.
        "proc write_db {path} {\n"
        "  file mkdir [file dirname $path]\n"
        "  set f [open $path w]; puts -nonewline $f \"ODB:$path\"; close $f\n"
        "  lappend ::DBS $path\n"
        "}\n"
        "set ::DBS {}\n"
        "proc write_def {path} {\n"
        "  file mkdir [file dirname $path]\n"
        "  set f [open $path w]; puts -nonewline $f DEF; close $f\n"
        "}\n"
        "proc write_verilog {path} {}\n"
        # The deck's route-guide discipline wraps BOTH routers and degrades
        # loudly if either command is missing, so the stand-in must expose
        # both or the fixture measures the degraded path instead.
        "proc global_route {args} { return }\n"
        # A real die: without it the deck's first act is SDR_DIE_NONFATAL,
        # which clears `_sdr_ok` and the repair loop never runs at all.
        "namespace eval ord { proc get_db_block {} { return ::_vic_blk } }\n"
        "proc ::_vic_blk {op args} {\n"
        "  switch -- $op {\n"
        "    getDefUnits { return 1000 }\n"
        "    getDieArea  { return ::_vic_die }\n"
        "    default     { return \"\" }\n"
        "  }\n"
        "}\n"
        "proc ::_vic_die {op args} {\n"
        "  switch -- $op { dx - dy { return 1150000 } default { return \"\" } }\n"
        "}\n"
        # The loop's converged-exit reads the STA violator report it just
        # wrote. Nothing in the stub writes one, so without this the census
        # is 0 and the deck exits at pass 1 before it ever routes.
        "set ::DRV_SEQ {%s}\n" % " ".join(str(c) for c in drv) +
        "set ::DRV_I 0\n"
        "proc report_check_types {args} {\n"
        "  set path \"\"; set take 0\n"
        "  foreach a $args {\n"
        "    if {$take} { set path $a; set take 0; continue }\n"
        "    if {$a eq \">\"} { set take 1 } elseif {[string index $a 0] eq \">\"} "
        "{ set path [string range $a 1 end] }\n"
        "  }\n"
        "  if {$path eq \"\"} { return }\n"
        "  set n 0\n"
        "  if {$::DRV_I < [llength $::DRV_SEQ]} {\n"
        "    set n [lindex $::DRV_SEQ $::DRV_I]\n"
        "  } else { set n [lindex $::DRV_SEQ end] }\n"
        "  incr ::DRV_I\n"
        "  file mkdir [file dirname $path]\n"
        "  set f [open $path w]\n"
        "  puts $f \"max capacitance\"\n"
        "  for {set i 0} {$i < $n} {incr i} "
        "{ puts $f \"  net_$i   0.1   0.2   -0.1 (VIOLATED)\" }\n"
        "  close $f\n"
        "}\n")
    out, err, route = _tcl_walk.walk(_WALKER, _STUB + ANNOTATED_SESSION + harness + child, tmp_path)
    return _Ran(out, err, route)


def _marker(out: str, token: str) -> str:
    for line in out.splitlines():
        if line.startswith(token):
            return line
    return ""


# ────────────────── the deck still parses and still stops ──────────────────

def test_the_instrumented_child_deck_is_still_valid_tcl(tmp_path):
    """NEGATIVE CONTROL for the whole change: a deck the parent `exec`s and
    cannot debug must parse and run to its own end."""
    r = _run_child(tmp_path, [3, 2, 1])
    assert "missing close-bracket" not in r.stderr
    # Ran to its OWN end, which is what "the parent cannot debug it" needs.
    assert "SDR_CHILD_DONE" in r.stdout, f"[{r.route}] {r.stdout[-2000:]}"
    assert "invalid command name" not in r.stderr, r.stderr


# ───────────────────────── the ledger is written ───────────────────────────

def test_every_pass_is_recorded_with_its_own_count(tmp_path):
    r = _run_child(tmp_path, [5, 4, 3])
    assert "SDR_PASS_RESULT:" in r.stdout, r.stdout[-2000:]
    assert _marker(r.stdout, "SDR_PASS_LEDGER:"), r.stdout[-2000:]
    assert "SDR_CANDIDATE_SOURCE:" in r.stdout


# ─────────────── a later pass that REGRESSES does not displace ─────────────

def test_a_regressing_pass_does_not_displace_the_better_one(tmp_path):
    """The measured shape: pass 1 is best, later passes are worse."""
    r = _run_child(tmp_path, [1, 11, 39])
    assert "SDR_PASS_REJECTED_SEGMENT:" in r.stdout, r.stdout[-3000:]
    src = _marker(r.stdout, "SDR_CANDIDATE_SOURCE:")
    assert "best_pass=1" in src, src
    assert "best_router_drc=1" in src, src


def test_a_regression_is_named_not_silently_dropped(tmp_path):
    r = _run_child(tmp_path, [2, 40])
    line = _marker(r.stdout, "SDR_PASS_REJECTED_SEGMENT:")
    assert line, r.stdout[-3000:]
    assert "worse than" in line
    assert "will not be offered" in line


# ───────────────── a pass that IMPROVES is kept (both directions) ───────────

def test_an_improving_pass_becomes_the_best(tmp_path):
    """NEGATIVE CONTROL: the selection must not simply always pick pass 1."""
    r = _run_child(tmp_path, [9, 4, 2])
    src = _marker(r.stdout, "SDR_CANDIDATE_SOURCE:")
    assert "best_router_drc=2" in src, src
    assert "SDR_PASS_REJECTED_SEGMENT:" not in r.stdout, (
        "a monotonically improving sequence reported a regression")


# ───────────────────────── clean stops the loop early ──────────────────────

def test_a_pass_that_reaches_zero_stops_the_loop(tmp_path):
    """A later pass cannot beat clean, so the loop must not run on and risk
    spoiling it — the measured failure this ruling came from."""
    r = _run_child(tmp_path, [4, 0, 7, 7])
    assert "SDR_PASS_ROUTE_CLEAN:" in r.stdout, r.stdout[-3000:]
    src = _marker(r.stdout, "SDR_CANDIDATE_SOURCE:")
    assert "best_router_drc=0" in src, src
    # the 7s are after the break, so they must never have been measured
    assert "router_drc=7" not in r.stdout, (
        "the loop kept going after a clean route")


# ─────────── no clean pass: the best count is still what is offered ────────

def test_with_no_clean_pass_the_lowest_count_is_offered(tmp_path):
    r = _run_child(tmp_path, [30, 12, 25])
    src = _marker(r.stdout, "SDR_CANDIDATE_SOURCE:")
    assert "best_pass=2" in src, src
    assert "best_router_drc=12" in src, src


# ───────────────────── the selection is a COPY, never a restore ────────────

def test_the_child_never_restores_over_itself():
    """The one thing this change must NOT do.

    The antenna loop in this same deck measured that `dbChip_destroy`+`read_db`
    kills the STA network (ORD-2008) and that the ECO journal leaves a third
    state. Selection is therefore a file copy. Asserted on the emitted source
    so no future edit can quietly turn it into a rollback.
    """
    import inspect
    src = inspect.getsource(R._sdr_transaction_block_tcl) if hasattr(
        R, "_sdr_transaction_block_tcl") else inspect.getsource(R)
    i = src.find("SDR_CANDIDATE_FROM_BEST_PASS")
    assert i > 0, "the best-pass selection is gone"
    window = src[max(0, i - 3000):i + 3000]
    assert "file copy -force" in window, (
        "the best pass is no longer selected by copying its ODB")
    assert "dbChip_destroy" not in window, (
        "an in-session rollback appeared in the selection path — the antenna "
        "loop already measured that it kills the STA network (ORD-2008)")
    assert "undoEco" not in window, (
        "the ECO journal appeared in the selection path — measured to leave a "
        "state that is neither the before nor the after")
