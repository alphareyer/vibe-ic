"""Post-route passes that move or insert cells remove the pre-route fill first
and restore it after; a failed legalize is never "spurious"; no diode goes on a
net a diode cannot help.

MEASURED (subservient x gf180mcuD, two copies of run2, main 240c0a353):
fillers are inserted PRE-ROUTE (R-0915-105), so every post-route pass that
calls `detailed_placement` saw them as movable instances -- "Movable instances
area 1,363,895 um^2" in a 1,377,710 um^2 core, `[WARNING DPL-0037] Use
remove_fillers before detailed placement`, `[ERROR DPL-0038] Utilization
greater than 100%, impossible to legalize` on a 5.5 % design.

  * both SDR transactions were REJECTED for it (2,430 / 2,863 candidate
    placement violations) -> no post-route DRV/timing repair was ever adopted
    -> the 2x-limit slews shipped;
  * the antenna-reconverge pass inserted 12 diodes onto filler-tiled sites,
    its DPL-0038 was classed ANTENNA_NATIVE_ERROR_SPURIOUS (no net lost wire),
    and 29 check_placement violations shipped (16 overlap / 9 padding / 4
    site) -- no GDS behind them;
  * the 12 nets already carried a diode and still violated: ANT-0019 "the
    diodes already on them do not help and another one would not either".

Asserts on the REAL deck builders' text and, for the antenna block, on the
same emulated-ODB tclsh harness `test_r0915_116_*` drives.
"""
from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import phase3_one_shot_runner as R  # noqa: E402
import test_r0915_116_the_antenna_loop_stops_at_its_objective as A  # noqa: E402

SPEC = {"filler_masters": ["fx__fill_1", "fx__fill_2"],
        "slot_pinned_core": False, "design_declared_die": True,
        "sparse_active_row_fill": False}
RUNNER_SRC = (_PROGRAMS / "phase3_one_shot_runner.py").read_text()


def _first(text, needle, start=0):
    i = text.find(needle, start)
    assert i >= 0, f"{needle!r} not in deck"
    return i


# --- 1. the SDR child session -------------------------------------------------
def test_the_sdr_child_removes_fill_before_its_first_extraction():
    """RED ON MAIN (no remove_fillers in the child)."""
    deck = R._v1_8_100_signoff_drv_repair_tcl(
        "/o", None, pdk=None, filler_spec=SPEC)
    child = deck[_first(deck, "===== CHILD ROLE"):]
    rm = _first(child, "remove_fillers")
    assert rm < _first(child, "extract_parasitics"), (
        "remove_fillers must precede the pass's extraction (EST-0104)")
    assert rm < _first(child, "detailed_placement")


def test_the_sdr_child_restores_fill_and_remeasures_before_the_candidate():
    deck = R._v1_8_100_signoff_drv_repair_tcl(
        "/o", None, pdk=None, filler_spec=SPEC)
    refill = _first(deck, "SDR_REFILL_DONE")
    assert refill < _first(deck, R._SDR_CANDIDATE_DEF_NAME)
    assert "fx__fill_1" in deck[:refill], "the refill is not the flow's masters"
    assert refill < _first(deck, "SDR_REFILL_PLACEMENT_VIOLATIONS") \
        < _first(deck, R._SDR_CANDIDATE_DEF_NAME)


def test_the_sdr_parent_role_never_removes_fill():
    """The parent (shipping) session never mutates; only the child does."""
    deck = R._v1_8_100_signoff_drv_repair_tcl(
        "/o", None, pdk=None, filler_spec=SPEC)
    child_end = _first(deck, "SDR_CHILD_DONE")
    assert "remove_fillers" not in deck[child_end:]


# --- 2. the antenna repair/reconverge ------------------------------------------
def _ant(spec=SPEC):
    return R._antenna_repair_tcl(A._pdk(), "/o", filler_spec=spec)


def test_the_antenna_pass_removes_fill_immediately_before_repair():
    """RED ON MAIN."""
    t = _ant()
    ra = _first(t, "repair_antennas fx__antenna")
    rm = t.rfind("remove_fillers", 0, ra)
    assert rm > 0, "no remove_fillers before repair_antennas"
    # ...and AFTER this pass's checkpoint, so a refusal restores fill-complete
    assert _first(t, "write_db $_ant_pass_ckpt") < rm


def test_the_antenna_fill_is_restored_before_the_next_measurement():
    t = _ant()
    loop = _first(t, "for {set _i 0} {$_i < $_ant_cap}")
    top_refill = _first(t, "ANTENNA_REFILL_DONE", loop)
    assert top_refill < _first(t, "check_antennas", loop), (
        "the next iteration measures (and checkpoints) before the fill is back")
    leaving = _first(t, "THE STATE WE ARE ACTUALLY LEAVING")
    assert t.rfind("ANTENNA_REFILL_DONE", 0, leaving) > top_refill, (
        "no refill after the loop")


def test_no_spec_means_the_builders_are_unchanged():
    """A direct call without the PnR builder's fill facts emits no bracket."""
    assert "remove_fillers" not in _ant(spec=None)
    assert "remove_fillers" not in R._v1_8_100_signoff_drv_repair_tcl(
        "/o", None, pdk=None)


def test_the_pnr_builder_hands_every_post_route_pass_the_same_fill_facts():
    """ONE statement of the fill facts (`_postroute_filler_spec`) reaches
    preroute_fill, both SDR stages and both antenna slots."""
    tree = ast.parse(RUNNER_SRC)
    wanted = {"_antenna_repair_tcl", "_post_route_spef_repair_tcl",
              "_v1_8_100_signoff_drv_repair_tcl"}
    seen = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in wanted:
            kw = {k.arg: ast.unparse(k.value) for k in node.keywords}
            seen.setdefault(node.func.id, []).append(kw.get("filler_spec"))
    assert set(seen) == wanted, seen
    assert seen["_antenna_repair_tcl"] == ["_postroute_filler_spec"]
    assert seen["_post_route_spef_repair_tcl"] == ["_postroute_filler_spec"]
    assert sorted(seen["_v1_8_100_signoff_drv_repair_tcl"]) == \
        ["_postroute_filler_spec", "filler_spec"]


def test_fill_complete_candidates_and_checkpoints_are_session_products():
    """The session that writes the refilled state declares those files."""
    deck = "\n".join((R._pnr_stage_begin(stage)
                      for stage in R._SDR_TXN_DIRS))
    deck += "\nANTENNA_PRE_REPAIR_CHECKPOINT\n"
    paths = set(R._pnr_session_products(Path("/tmp/pnr"), "/o", deck))
    for dirname in R._SDR_TXN_DIRS.values():
        assert Path("/tmp/pnr") / dirname / R._SDR_CANDIDATE_DEF_NAME in paths
        assert Path("/tmp/pnr") / dirname / R._SDR_CANDIDATE_ODB_NAME in paths
    for name in R._ANTENNA_CHECKPOINT_NAMES:
        assert Path("/tmp/pnr") / name in paths


# --- 3. a failed legalize is never spurious (DRIVEN) ---------------------------
def _drive_dpl(seq="5 5 0"):
    tclsh = shutil.which("tclsh")
    if tclsh is None:                                   # pragma: no cover
        pytest.skip("tclsh not installed")
    harness = (A._ODB_HARNESS % {"seq": seq, "wired": 1, "inserts": "d1"}
               ).replace('error "DRT-0206 checkConnectivity"',
                         'error "DPL-0038 Utilization greater than 100%, '
                         'impossible to legalize"')
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "ant.tcl"
        p.write_text(harness + A._tcl() + '\nputs "DECK_END"\n')
        return subprocess.run([tclsh, str(p)], capture_output=True,
                              text=True, cwd=td)


def test_a_failed_legalize_is_refused_and_rolled_back_not_spurious():
    """RED ON MAIN: the pass whose inserted cell could not be legalized kept
    every wire, so main called it SPURIOUS and 'converged'."""
    r = _drive_dpl()
    assert "ANTENNA_NATIVE_ERROR_SPURIOUS" not in r.stdout, r.stdout
    assert "ANTENNA_LOOP_CONVERGED" not in r.stdout, r.stdout
    assert "ANTENNA_NATIVE_LEGALIZE_FAILED" in r.stdout, r.stdout
    assert "ANTENNA_REPAIR_REFUSED_ROLLBACK_REQUEST" in r.stdout, r.stdout
    assert "DESTROYED_ONE d1" in r.stdout, r.stdout
    assert r.returncode != 0 and "DECK_END" not in r.stdout, (
        "a refusal must stop the deck with its checkpoint intact")


def test_a_wire_error_that_kept_every_wire_is_still_spurious():
    """Reverse: the R-0915-116 behaviour for a non-legalize raise stands."""
    tclsh = shutil.which("tclsh")
    if tclsh is None:                                   # pragma: no cover
        pytest.skip("tclsh not installed")
    r = A._drive("5 5 0", wired=True, inserts=["d1"])
    assert "ANTENNA_NATIVE_ERROR_SPURIOUS" in r.stdout
    assert "ANTENNA_NATIVE_LEGALIZE_FAILED" not in r.stdout


# --- 4. no diode where a diode cannot help (ANT-0019) --------------------------
def test_a_net_that_already_carries_a_diode_is_diode_futile():
    """RED ON MAIN. ANT-0019's own criterion, evaluated in-deck: a violating
    net that already carries this PDK's diode master. All futile ->
    `-jumper_only`; mixed -> reported by name, and a diode landing on one is
    disclosed."""
    t = _ant()
    ra = _first(t, "repair_antennas fx__antenna")
    pre = t[:ra]
    assert 'getName] eq "fx__antenna"' in pre
    assert "ANTENNA_DIODE_FUTILE:" in pre
    assert "set _ant_mode -jumper_only" in pre
    assert "{*}$_ant_mode" in t[ra:ra + 200]
    assert "ANTENNA_DIODE_ON_FUTILE_NET" in t


# --- 5. RULING: mixed case -> futile diodes destroyed by name (DRIVEN) ---------
_MIXED_HARNESS = r"""
set ::calls 0
set ::reports {{nA nB} {nA nB} {nA} {nA}}
set ::counts {2 2 1 1}
proc check_antennas {args} {
  set i [expr {min($::calls, [llength $::counts]-1)}]
  set f [lindex $args [expr {[lsearch $args -report_file]+1}]]
  if {[lsearch $args -report_file] >= 0} {
    set fh [open $f w]
    foreach n [lindex $::reports $i] { puts $fh "Net: $n" }
    close $fh
  }
  incr ::calls
  return [lindex $::counts $i]
}
proc detailed_route {args} { return "" }
proc write_db {args} { return "" }
proc remove_fillers {args} { puts "CALL_REMOVE_FILLERS" }
set ::insts {d0}
proc repair_antennas {args} {
  puts "CALL_REPAIR_ANTENNAS $args"
  if {[lsearch $args -jumper_only] >= 0} { return "" }
  if {![info exists ::inserted]} { set ::inserted 1; lappend ::insts d1 d2 }
  return ""
}
namespace eval ord { proc get_db_block {} { return ::BLK } }
namespace eval odb {
  proc dbInst_destroy {i} {
    set n [$i getName]
    puts "DESTROYED_ONE $n"
    set x [lsearch $::insts $n]
    if {$x >= 0} { set ::insts [lreplace $::insts $x $x] }
  }
}
proc ::BLK {method args} {
  switch -- $method {
    getInsts { set r {}; foreach n $::insts { lappend r ::INST_$n }; return $r }
    findInst { set n [lindex $args 0]
               if {[lsearch $::insts $n] < 0} { return NULL }; return ::INST_$n }
    findNet  { set n [lindex $args 0]
               if {$n ne "nA" && $n ne "nB"} { return NULL }; return ::NET_$n }
    getNets  { return {} }
  }
  return NULL
}
proc ::MASTER_D {method args} { if {$method eq "getName"} { return fx__antenna }; return "" }
foreach d {d0 d1 d2} net {nA nA nB} {
  proc ::INST_$d [list method args] [string map [list @D@ $d @N@ $net] {
    switch -- $method {
      getName   { return @D@ }
      getMaster { return ::MASTER_D }
      getITerms { return {::IT_@D@} }
    }
    return 0
  }]
  proc ::IT_$d [list method args] [string map [list @D@ $d @N@ $net] {
    switch -- $method { getInst { return ::INST_@D@ } getNet { return ::NET_@N@ } }
    return NULL
  }]
}
proc ::NET_nA {method args} {
  switch -- $method {
    getName { return nA } isSpecial { return 0 } getWire { return W }
    getITerms { set r {}; foreach d {d0 d1} { if {[lsearch $::insts $d] >= 0} { lappend r ::IT_$d } }; return $r }
  }
  return 0
}
proc ::NET_nB {method args} {
  switch -- $method {
    getName { return nB } isSpecial { return 0 } getWire { return W }
    getITerms { if {[lsearch $::insts d2] >= 0} { return {::IT_d2} }; return {} }
  }
  return 0
}
"""


def test_mixed_case_destroys_the_futile_diode_by_name_and_keeps_the_other():
    """RULING: the pass runs (nB needs a diode), then the diode that landed on
    nA -- which already carried one and still violated (ANT-0019) -- is
    destroyed by name before the refill and disclosed; nA still violating at
    the next measurement is routed to jumper/reroute, never counted repaired."""
    tclsh = shutil.which("tclsh")
    if tclsh is None:                                   # pragma: no cover
        pytest.skip("tclsh not installed")
    deck = R._antenna_repair_tcl(A._pdk(), "/o", filler_spec=SPEC)
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "ant.tcl"
        p.write_text(_MIXED_HARNESS.replace("/o", td) + deck.replace("/o", td)
                     + '\nputs "INSTS={$::insts}"\n')
        r = subprocess.run([tclsh, str(p)], capture_output=True, text=True,
                           cwd=td)
    out = r.stdout
    assert "ANTENNA_DIODE_FUTILE: 1 net(s)" in out and "nA" in out, out[-3000:]
    assert "ANTENNA_DIODE_FUTILE_MIXED" in out, out[-3000:]
    assert "-jumper_only" not in out.split("CALL_REPAIR_ANTENNAS", 1)[1].splitlines()[0]
    assert "ANTENNA_DIODE_ON_FUTILE_NET_REMOVED: net=nA inst=d1 master=fx__antenna" \
        in out, out[-3000:]
    assert "DESTROYED_ONE d1" in out and "DESTROYED_ONE d2" not in out, out[-3000:]
    assert out.index("DESTROYED_ONE d1") < out.index("ANTENNA_REFILL_DONE"), (
        "the futile diode must be gone before the fill is restored")
    assert "ANTENNA_FUTILE_NET_STILL_VIOLATING" in out and \
        "NOT counted as repaired" in out, out[-3000:]
    assert "INSTS={d0 d2}" in out, out[-500:]
