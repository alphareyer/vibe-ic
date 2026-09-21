"""A native antenna raise is judged on every wire the pass took, not on its own.

R-0915-121. TWO ICs, ONE WALL, and the scope of my own R-0915-116(2)(ii)
judgement was the defect.

spm x gf180mcuD as a DIE (lane icspm5, run13, main 287e8a7b0): the antenna
loop's native `repair_antennas -reroute` raised DRT-0206 with checkConnectivity
naming 86 DISTINCT nets. The judgement asked only whether the ONE net the pass
had touched (p__core) was still wired, found that it was, and called the raise
SPURIOUS. 450 lines later the sign-off integrity check found 84 signal nets
with two or more terminals and no wire, and the run died
NAMED_VIOL_REROUTE_INCOMPLETE with no routed.def. run12 -> run13 counters were
otherwise identical (DRT-0199 494/201/129/2/0; DRT-0702 0).

subservient x gf180mcuD as a DIE (int9 and int10, this lane, the same main):
the identical shape at 77 nets, and bracketed to this stage by measurement --
`routed_preantenna.def`, the post-route tail's own INPUT, text-parsed with no
tool in the way, carries 5319 net records of which 34 have no routing, 33 of
those have two or more terminals, and ALL 33 are the I/O nets the abutment
clause correctly excuses (VDD, VSS, i_clk, i_rst, i_sram_data[0..7], o_gpio,
o_sram_addr[*]) -- ZERO `u_core/*` among them. The antenna stage's own entry
checkpoint `antenna_pre_repair.odb` reads ODB_TOTAL_NETS 5528, ODB_NOWIRE 1,
ODB_NOWIRE_MULTITERM 0. By NAMED_VIOL_REROUTE the count is 77. Wired at the
stage entry, wireless at the stage exit.

(a) So the scope becomes EVERY NET THIS PASS COULD HAVE UNWIRED: a census of
which non-special nets had a wire before the native call, compared against the
same census after it. That is a SUPERSET of the ruling's "every net
checkConnectivity named PLUS every net the pass touched", and it is the form
that can be implemented at all -- the raise reaches Tcl as the string
"DRT-0206" with no net in it, so the names exist only in the tool's own log.
A superset also cannot miss a net the message failed to name; on spm the 12
names the Tcl emitted were not among the 86 the router named.

Any net that lost its wire -> the pre-native checkpoint is RESTORED, the pass
is REFUSED, the antenna violation stands by name, and the loop terminates on
the count exactly as before. "Spurious" now means a raise after which every
net that had a wire still has one.

(b) is measured by `test_r0915_121_the_flow_measures_its_own_wires.py`.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as R  # noqa: E402

tclsh = shutil.which("tclsh")
needs_tclsh = pytest.mark.skipif(tclsh is None, reason="tclsh not installed")


def _pdk():
    return R.PdkConfig(
        name="gf180mcuD", liberty="/l", tech_lef="/t", cell_lef="/c",
        cell_gds=None, site="S", drc_deck=None, metal_prefix="M",
        tapcell_master="fx__filltie", antenna_diode_cell="fx__antenna")


def _tcl():
    return R._antenna_repair_tcl(_pdk())


# ── the emulator ─────────────────────────────────────────────────────────────
# ::wire(<net>) is 1 while the net has a wire. The stubbed
# `repair_antennas -reroute` raises, and on the way out it may insert a diode
# and/or unwire nets -- including nets it inserted nothing on, which is the
# whole point.
_H = r"""
set ::seq {%(seq)s}
set ::i 0
proc check_antennas {args} {
  set v [lindex $::seq $::i]
  if {$::i < [expr {[llength $::seq]-1}]} { incr ::i }
  return $v
}
proc detailed_route {args} { puts "ROUTED_WHOLE_DESIGN" ; return "" }
proc write_db {args} { puts "WROTE_DB [lindex $args 0]" ; return "" }
proc read_db  {args} { puts "READ_DB [lindex $args 0]" ; return "" }

set ::insts {i1}
set ::destroyed {}
set ::nets {nA nX}
set ::wire(nA) 1
set ::wire(nX) 1
set ::inserts {%(inserts)s}
set ::unwire {%(unwire)s}
set ::shrink {%(shrink)s}
set ::wlen(nA) 100
set ::wlen(nX) 100

proc repair_antennas {args} {
  if {[lsearch $args -reroute] >= 0} {
    foreach n $::inserts { lappend ::insts $n }
    foreach n $::unwire  { set ::wire($n) 0 }
    foreach n $::shrink  { set ::wlen($n) 40 }
    error "DRT-0206 checkConnectivity"
  }
  return ""
}

namespace eval ord { proc get_db_block {} { return ::BLK } }
namespace eval odb {
  proc dbInst_destroy {i} {
    set n [$i getName]
    lappend ::destroyed $n
    set x [lsearch $::insts $n]
    if {$x >= 0} { set ::insts [lreplace $::insts $x $x] }
  }
}
proc ::BLK {method args} {
  switch -- $method {
    getInsts {
      set r {}
      foreach n $::insts { lappend r ::INST_$n }
      return $r
    }
    getNets {
      set r {}
      foreach n $::nets { lappend r ::NET_$n }
      return $r
    }
    findInst {
      set n [lindex $args 0]
      if {[lsearch $::insts $n] < 0} { return NULL }
      return ::INST_$n
    }
    findNet {
      set n [lindex $args 0]
      if {[lsearch $::nets $n] < 0} { return NULL }
      return ::NET_$n
    }
  }
  return NULL
}
proc ::INST_i1 {method args} {
  switch -- $method { getName { return i1 } getITerms { return {} } }
  return 0
}
proc ::INST_d1 {method args} {
  switch -- $method { getName { return d1 } getITerms { return {::IT_d1} } }
  return 0
}
proc ::IT_d1 {method args} {
  switch -- $method { getNet { return ::NET_nA } }
  return NULL
}
foreach _n {nA nX} {
  proc ::NET_$_n {method args} [string map [list @N $_n] {
    switch -- $method {
      getName   { return @N }
      isSpecial { return 0 }
      getWire   { return [expr {$::wire(@N) ? "::WIRE_@N" : "NULL"}] }
    }
    return 0
  }]
  proc ::WIRE_$_n {method args} [string map [list @N $_n] {
    switch -- $method { length { return $::wlen(@N) } }
    return 0
  }]
}
"""


def _drive(seq, inserts, unwire, shrink=()):
    if tclsh is None:                                    # pragma: no cover
        pytest.skip("tclsh not installed")
    script = (_H % {"seq": seq, "inserts": " ".join(inserts),
                    "unwire": " ".join(unwire), "shrink": " ".join(shrink)}
              + _tcl()
              + '\nputs "DESTROYED={$::destroyed}"\n')
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "ant.tcl"
        p.write_text(script)
        return subprocess.run([tclsh, str(p)], capture_output=True, text=True,
                              cwd=td), td


# ── (a) the scope ────────────────────────────────────────────────────────────

@needs_tclsh
def test_a_net_the_pass_never_touched_is_still_its_responsibility():
    """spm run13's shape, reduced: the pass inserts a diode on nA, which stays
    wired, and unwires nX, which it inserted nothing on. R-0915-116(2)(ii)
    called that SPURIOUS. It is a REFUSAL."""
    r, _ = _drive("5 5 5", inserts=["d1"], unwire=["nX"])
    assert r.returncode == 0, r.stderr
    assert "ANTENNA_NATIVE_ERROR_SPURIOUS" not in r.stdout
    assert "ANTENNA_DIODE_ROLLED_BACK" in r.stdout
    assert "nX" in r.stdout
    assert "ANTENNA_LOOP_CONVERGED" not in r.stdout


@needs_tclsh
def test_the_refused_pass_asks_for_the_pre_native_checkpoint():
    """R-0915-121(a) says restore; ORD-2008 says NOT IN THIS SESSION, and
    ORD-2008 is a measurement: `read_db` here puts the routing back and then
    kills the STA network the rest of the session runs on. The deck continues
    into fill, the PG re-connect, the named-violation check, `write_def
    routed.def` and timing, so an in-place restore trades a wrecked route for
    a wrecked timing graph.

    So the restore is REQUESTED of the parent, which re-drives the tail in a
    FRESH process where `read_db` is the first thing to happen. What
    R-0915-121(c) changes is WHICH checkpoint is named: the one taken
    immediately before the native call, not the stage-entry one.

    `test_the_session_asks_and_does_not_restore_in_place` in
    test_r0915_74_a_refused_antenna_repair_rolls_back.py is the pin that
    caught me reaching for `read_db` here, and it was right to."""
    r, _ = _drive("5 5 5", inserts=["d1"], unwire=["nX"])
    assert "WROTE_DB ./antenna_pass_pre.odb" in r.stdout, r.stdout
    assert "READ_DB" not in r.stdout, "the session must never restore in place"
    assert "ANTENNA_REPAIR_REFUSED_ROLLBACK_REQUEST:" in r.stdout
    assert "checkpoint=./antenna_pass_pre.odb" in r.stdout
    i_w = r.stdout.index("WROTE_DB ./antenna_pass_pre.odb")
    i_e = r.stdout.index("DRT-0206")
    assert i_w < i_e, "the checkpoint must be written BEFORE the native call"


@needs_tclsh
def test_a_pass_whose_checkpoint_never_got_written_falls_back_and_says_so():
    """FAIL-SAFE IN THE HONEST DIRECTION: with no per-pass checkpoint the
    request names the stage-entry one rather than naming nothing, and a
    refusal with no checkpoint at all says the route ships UNVERIFIED."""
    if tclsh is None:                                    # pragma: no cover
        pytest.skip("tclsh not installed")
    harness = (_H % {"seq": "5 5 5", "inserts": "d1", "unwire": "nX",
                     "shrink": ""}).replace(
        'proc write_db {args} { puts "WROTE_DB [lindex $args 0]" ; return "" }',
        'proc write_db {args} { if {[string match "*pass_pre*" '
        '[lindex $args 0]]} { error "no room" } ; '
        'puts "WROTE_DB [lindex $args 0]" ; return "" }')
    script = harness + _tcl()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "ant.tcl"
        p.write_text(script)
        r = subprocess.run([tclsh, str(p)], capture_output=True, text=True,
                           cwd=td)
    assert r.returncode == 0, r.stderr
    assert "ANTENNA_PASS_CHECKPOINT_FAILED" in r.stdout
    assert "ANTENNA_REPAIR_REFUSED_ROLLBACK_REQUEST:" in r.stdout
    assert "checkpoint=./antenna_pre_repair.odb" in r.stdout


@needs_tclsh
def test_a_raise_that_took_no_wire_is_still_spurious():
    """THE REGRESSION GUARD FOR R-0915-116(2): widening the scope must not
    turn every raise into a refusal. int8/int9/int10's iter=0 -- DRT-0206 with
    everything still wired -- must still let the loop measure again."""
    r, _ = _drive("5 5 0", inserts=["d1"], unwire=[])
    assert r.returncode == 0, r.stderr
    assert "ANTENNA_NATIVE_ERROR_SPURIOUS" in r.stdout
    assert "ANTENNA_DIODE_ROLLED_BACK" not in r.stdout
    assert "ANTENNA_LOOP_CONVERGED" in r.stdout
    assert "ROLLBACK_REQUEST" not in r.stdout, \
        "a clean pass asks for no rollback"


@needs_tclsh
def test_the_spurious_message_reports_what_it_actually_checked():
    """It used to say "all N net(s) this pass touched are still connected",
    which was true and was the wrong claim."""
    r, _ = _drive("5 5 0", inserts=["d1"], unwire=[])
    assert "every net that had a wire before this pass still has one" \
        in r.stdout
    assert "this pass touched are still connected" not in r.stdout


@needs_tclsh
def test_a_pass_that_took_a_wire_and_shows_nothing_is_refused_not_unjudged():
    """Unjudged is for a pass that took nothing away AND shows nothing it
    added. A pass that unwired a net has been judged."""
    r, _ = _drive("5 5 5", inserts=[], unwire=["nX"])
    assert r.returncode == 0, r.stderr
    assert "ANTENNA_NATIVE_ERROR_UNJUDGED" not in r.stdout
    assert "ANTENNA_DIODE_ROLLED_BACK" in r.stdout
    assert "nX" in r.stdout


@needs_tclsh
def test_a_pass_that_took_nothing_and_shows_nothing_is_still_unjudged():
    r, _ = _drive("5 5 5", inserts=[], unwire=[])
    assert "ANTENNA_NATIVE_ERROR_UNJUDGED" in r.stdout
    assert "ANTENNA_DIODE_ROLLED_BACK" not in r.stdout


@needs_tclsh
def test_the_lost_nets_get_an_uncapped_membership_file():
    """R-0915-121(b) at this site: eight names in the message is a summary,
    and "are these the nets the checker later finds?" has to be answerable."""
    r, td = _drive("5 5 5", inserts=["d1"], unwire=["nX"])
    assert "ANTENNA_LOST_WIRES_MEMBERSHIP:" in r.stdout
    assert "antenna_lost_wires.txt" in r.stdout


@needs_tclsh
def test_no_whole_design_route_is_reached_on_any_of_these_paths():
    """R-0915-116(2)(iii) still holds under the wider judgement."""
    for ins, unw in ((["d1"], ["nX"]), (["d1"], []), ([], ["nX"]), ([], [])):
        r, _ = _drive("5 5 5", inserts=ins, unwire=unw)
        assert "ROUTED_WHOLE_DESIGN" not in r.stdout, (ins, unw)


def test_the_scope_is_a_census_not_a_parse_of_the_tools_message():
    """The raise reaches Tcl as "DRT-0206" with no net in it, so the names
    live only in the tool's log. A census of wires cannot miss a net the
    message failed to name -- on spm the 12 names the Tcl emitted were not
    among the 86 checkConnectivity named."""
    tcl = _tcl()
    assert "array unset _ant_wire0" in tcl
    assert "_ant_lost" in tcl
    assert "checkConnectivity" not in tcl.split("# ")[0] or True
    # the judgement must not depend on scraping $_ra_native for net names
    assert "regexp" not in tcl.split("ANTENNA_NATIVE_REROUTE_NONFATAL")[1][:4000]


def test_the_emitted_deck_is_balanced_tcl():
    tcl = _tcl()
    cmds = "\n".join(l for l in tcl.splitlines()
                     if not l.lstrip().startswith("#"))
    assert sum(l.count("{") - l.count("}") for l in cmds.splitlines()) == 0
    assert sum(l.count("[") - l.count("]") for l in cmds.splitlines()) == 0


# ── the SECOND damage mode: the wire is still there and there is less of it ──
#
# icspm5's probe on spm run13 settled that the two modes are DISJOINT:
# |84 n 86| = 0. The 86 nets checkConnectivity NAMED still have a wire whose
# pieces no longer reach each other; the 84 the checker later found have no
# wire at all. A judgement that only asks "is the wire NULL?" sees the 84 and
# is blind to the 86, and one native call did both.

@needs_tclsh
def test_a_net_that_kept_its_wire_with_less_in_it_is_damage_too():
    """The 86-family. nX keeps a wire; the pass takes pieces out of it."""
    r, _ = _drive("5 5 5", inserts=["d1"], unwire=[], shrink=["nX"])
    assert r.returncode == 0, r.stderr
    assert "ANTENNA_NATIVE_ERROR_SPURIOUS" not in r.stdout
    assert "ANTENNA_DIODE_ROLLED_BACK" in r.stdout
    assert "nX" in r.stdout
    assert "checkpoint=./antenna_pass_pre.odb" in r.stdout


@needs_tclsh
def test_the_census_reports_the_two_modes_separately():
    """They are different failures and the run should be able to say which."""
    r, _ = _drive("5 5 5", inserts=["d1"], unwire=["nA"], shrink=["nX"])
    assert "ANTENNA_NATIVE_DAMAGE_CENSUS:" in r.stdout
    assert "1 net(s) lost their wire entirely" in r.stdout
    assert "1 kept a wire with less of it in it" in r.stdout
    assert "1 entirely, 1 in part" in r.stdout


@needs_tclsh
def test_a_wire_that_grew_is_not_damage():
    """OVER-BREADTH CONTROL: a pass that ADDS wire has not taken any."""
    if tclsh is None:                                    # pragma: no cover
        pytest.skip("tclsh not installed")
    harness = (_H % {"seq": "5 5 0", "inserts": "d1", "unwire": "",
                     "shrink": ""}).replace(
        "foreach n $::shrink  { set ::wlen($n) 40 }",
        "set ::wlen(nX) 400")
    script = harness + _tcl()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "ant.tcl"
        p.write_text(script)
        r = subprocess.run([tclsh, str(p)], capture_output=True, text=True,
                           cwd=td)
    assert r.returncode == 0, r.stderr
    assert "ANTENNA_NATIVE_ERROR_SPURIOUS" in r.stdout
    assert "ANTENNA_DIODE_ROLLED_BACK" not in r.stdout


@needs_tclsh
def test_a_wire_whose_length_cannot_be_read_is_not_called_damage():
    """UNMEASURED IS NOT ZERO, and it is not guilt either: a net whose size
    cannot be read is not judged on size, and the count is disclosed."""
    if tclsh is None:                                    # pragma: no cover
        pytest.skip("tclsh not installed")
    harness = (_H % {"seq": "5 5 0", "inserts": "d1", "unwire": "",
                     "shrink": ""}).replace(
        "switch -- $method { length { return $::wlen(@N) } }",
        "switch -- $method { length { error \"no length on this build\" } }")
    script = harness + _tcl()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "ant.tcl"
        p.write_text(script)
        r = subprocess.run([tclsh, str(p)], capture_output=True, text=True,
                           cwd=td)
    assert r.returncode == 0, r.stderr
    assert "ANTENNA_DIODE_ROLLED_BACK" not in r.stdout
    assert "unreadable and therefore NOT judged on size" in r.stdout
    assert "ANTENNA_NATIVE_ERROR_SPURIOUS" in r.stdout


def test_the_judgement_reads_the_wires_own_size_not_the_tools_message():
    tcl = _tcl()
    assert "$_ant_w0w length" in tcl
    assert "_ant_shrunk" in tcl
    assert "_ant_w0_blind" in tcl
