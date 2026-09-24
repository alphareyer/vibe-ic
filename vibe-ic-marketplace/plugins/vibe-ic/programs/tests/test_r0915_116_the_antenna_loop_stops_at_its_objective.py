"""The antenna loop stops at its objective and never routes the whole design.

R-0915-116(2). MEASURED on spm x gf180mcuD as a DIE (lane icspm5, run12 on
v1.22.60): the base route CONVERGED -- DRT-0199 494 -> 201 -> 129 -> 2 -> 0,
DRT-0702 post-route verification 0 violations -- and the PG block never routed
(zero PG_REROUTE markers, R-0915-114 holding). The antenna loop then found ONE
violating net (p__core), GRT-0015 inserted ONE diode, the native `-reroute`
raised DRT-0206, and GRT-0012 immediately afterwards reported "Found 0 antenna
violations". THE OBJECTIVE WAS ALREADY MET -- and the whole-design fallback ran
on that finished result and destroyed it.

On subservient the same fallback hit DRT-1231 (int7) and cost the run its
routed.def, its DRC, its LVS and its post-route STA.

So the fallback is deleted, the exception is judged by CONNECTIVITY of what
the pass touched, and a net whose wire is gone has its diode rolled back BY
NAME with the violation left standing and reported.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as R  # noqa: E402


def _pdk():
    return R.PdkConfig(
        name="gf180mcuD", liberty="/l", tech_lef="/t", cell_lef="/c",
        cell_gds=None, site="S", drc_deck=None, metal_prefix="M",
        tapcell_master="fx__filltie", antenna_diode_cell="fx__antenna")


def _tcl():
    return R._antenna_repair_tcl(_pdk())


# R-0915-121(3) CHANGED WHAT A REFUSED PASS DOES AFTER IT REFUSES, and these
# driven cases are re-aimed at that. They used to require `returncode == 0`
# and read a `DESTROYED={...}` probe printed AFTER the emitted block. The deck
# now STOPS on the first refusal -- measured on int11, a later antenna pass
# otherwise rewrites both checkpoints with post-damage state and the parent is
# asked to restore from a file that no longer holds anything to restore -- so
# a refusal exits non-zero and nothing after the block runs. The evidence is
# read from what the deck printed BEFORE it stopped (`DESTROYED_ONE <name>`,
# emitted by each destroy as it happens), and the exit code is now itself an
# assertion. Every property these cases measured is kept; none is relaxed.


def test_the_whole_design_fallback_is_gone():
    """(iii) -- deleted from this stage as R-0915-114(a) deleted it from the
    PG block. It is the step that destroyed a converged route on spm."""
    tcl = _tcl()
    assert "detailed_route -verbose 0 {*}$_vic_drc_opt" not in tcl
    assert "ANTENNA_REROUTE_FAILED" not in tcl


def test_the_native_incremental_path_is_still_tried_first():
    assert "-reroute" in _tcl()


def test_a_raise_is_judged_by_connectivity_not_by_the_exception():
    """(ii) -- the nets the pass touched are asked whether they still have a
    wire; the exception alone decides nothing."""
    tcl = _tcl()
    assert "_ant_touched" in tcl
    assert "getWire" in tcl
    assert "ANTENNA_NATIVE_ERROR_SPURIOUS" in tcl


def test_a_spurious_raise_lets_the_loop_measure_again():
    """The loop already owns the violation count ($_nv, re-measured each
    iteration), so a spurious raise continues rather than routing."""
    tcl = _tcl()
    i = tcl.index("ANTENNA_NATIVE_ERROR_SPURIOUS")
    assert "continue" in tcl[i:i + 500]
    assert "the loop re-measures rather than routing the whole design" in tcl


def test_a_broken_net_rolls_that_passes_diodes_back_by_name():
    tcl = _tcl()
    assert "ANTENNA_DIODE_ROLLED_BACK" in tcl
    assert "odb::dbInst_destroy" in tcl
    assert "[join [lrange $_ant_broken 0 7] {, }]" in tcl


def test_the_violation_stands_and_is_reported_never_waived():
    tcl = _tcl()
    assert "the antenna violation on those nets STANDS and is reported by name" \
        in tcl.replace("\n", " ").replace('"', "")
    assert "does not route the whole design to hide it" in \
        tcl.replace("\n", " ").replace('"', "")


def test_only_instances_this_pass_inserted_may_be_rolled_back():
    tcl = _tcl()
    assert "array unset _ant_pre_inst" in tcl
    assert "if {[info exists _ant_pre_inst($_ant_cnm)]} { continue }" in tcl


def test_the_snapshot_does_not_reuse_the_precheck_counts_name():
    """CAUGHT IN REVIEW, not in the field: the snapshot was first written into
    `_ant_pre`, which this same block already uses as the PRECHECK VIOLATION
    COUNT (`set _ant_pre [check_antennas]`). Tcl's `array unset` silently
    no-ops on a scalar and the first `set _ant_pre(x) 1` then raises
    "variable isn't array" INSIDE the catch -- the snapshot would have been
    empty on every run, every native raise would have read UNJUDGED, and no
    string-level test would have seen it. The two names must stay distinct."""
    tcl = _tcl()
    assert "set _ant_pre [check_antennas]" in tcl          # the count, unchanged
    assert "_ant_pre(" not in tcl                          # never used as an array
    assert "_ant_pre_inst(" in tcl                         # the snapshot's own name


def test_a_pass_whose_inserts_cannot_be_seen_is_not_called_clean():
    """Unjudgeable is not spurious: it stops, it does not continue."""
    tcl = _tcl()
    assert "ANTENNA_NATIVE_ERROR_UNJUDGED" in tcl
    i = tcl.index("ANTENNA_NATIVE_ERROR_UNJUDGED")
    assert "break" in tcl[i:i + 400]


def test_the_emitted_deck_is_balanced_tcl():
    tcl = _tcl()
    bal = sum(l.count("{") - l.count("}") for l in tcl.splitlines())
    assert bal == 0, f"{bal:+d} braces"


# ── the three outcomes, EXECUTED ─────────────────────────────────────────────
#
# The assertions above read the emitted text. They cannot see a variable-name
# collision, an unbalanced catch, or a branch that never reaches its `puts` --
# and one of those was live in this very block until the test above was
# written. So the same three outcomes are also DRIVEN, through the real Tcl
# interpreter, against an ODB emulated at the accessor level the block uses.

_ODB_HARNESS = """
set ::seq {%(seq)s}
set ::i 0
proc check_antennas {args} {
  set v [lindex $::seq $::i]
  if {$::i < [expr {[llength $::seq]-1}]} { incr ::i }
  return $v
}
proc detailed_route {args} { return "" }
proc write_db {args} { return "" }

set ::insts {i1}
set ::destroyed {}
set ::wired %(wired)d
set ::inserts {%(inserts)s}

proc repair_antennas {args} {
  if {[lsearch $args -reroute] >= 0} {
    foreach n $::inserts { lappend ::insts $n }
    error "DRT-0206 checkConnectivity"
  }
  return ""
}

namespace eval ord { proc get_db_block {} { return ::BLK } }
namespace eval odb {
  proc dbInst_destroy {i} {
    set n [$i getName]
    # R-0915-121(3): the deck now STOPS on a refusal, so a probe printed
    # after it never runs. Each destroy reports itself as it happens.
    puts "DESTROYED_ONE $n"
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
    findInst {
      set n [lindex $args 0]
      if {[lsearch $::insts $n] < 0} { return NULL }
      return ::INST_$n
    }
    findNet {
      set n [lindex $args 0]
      if {$n ne "nA" && $n ne "nB"} { return NULL }
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
proc ::NET_nA {method args} {
  switch -- $method {
    getName   { return nA }
    isSpecial { return 0 }
    getWire   { return [expr {$::wired ? "W" : "NULL"}] }
  }
  return 0
}

# d2 sits on nB, which ALWAYS keeps its wire: the diode on a net the repair
# did not break must survive the rollback that removes d1.
proc ::INST_d2 {method args} {
  switch -- $method { getName { return d2 } getITerms { return {::IT_d2} } }
  return 0
}
proc ::IT_d2 {method args} {
  switch -- $method { getNet { return ::NET_nB } }
  return NULL
}
proc ::NET_nB {method args} {
  switch -- $method {
    getName   { return nB }
    isSpecial { return 0 }
    getWire   { return W }
  }
  return 0
}
"""


def _drive(seq, wired, inserts, native_error="DRT-0206 checkConnectivity"):
    """Run the emitted block under tclsh against the emulated ODB."""
    import shutil
    import subprocess
    import tempfile
    tclsh = shutil.which("tclsh")
    if tclsh is None:                                   # pragma: no cover
        import pytest
        pytest.skip("tclsh not installed")
    script = ((_ODB_HARNESS % {"seq": seq, "wired": 1 if wired else 0,
                               "inserts": " ".join(inserts)})
              .replace('error "DRT-0206 checkConnectivity"',
                       f'error "{native_error}"')
              + _tcl()
              + '\nputs "DESTROYED={$::destroyed}"\n')
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "ant.tcl"
        p.write_text(script)
        return subprocess.run([tclsh, str(p)], capture_output=True, text=True,
                              cwd=td)


def test_driven_a_raise_whose_nets_kept_their_wires_lets_the_loop_converge():
    """spm run12's shape: the native path raises, the one diode it inserted is
    on a net that is still wired, and the NEXT measurement reads 0. The loop
    converges without any route being run."""
    r = _drive("5 5 0", wired=True, inserts=["d1"])
    assert r.returncode == 0, r.stderr
    assert "ANTENNA_NATIVE_ERROR_SPURIOUS" in r.stdout
    assert "ANTENNA_LOOP_CONVERGED" in r.stdout
    assert "ANTENNA_DIODE_ROLLED_BACK" not in r.stdout
    assert "DESTROYED_ONE" not in r.stdout            # nothing was rolled back


def test_driven_a_raise_that_unwired_a_net_rolls_that_pass_back_and_stops():
    r = _drive("5 5 5", wired=False, inserts=["d1"])
    # R-0915-121(3): a refusal STOPS the deck, so it exits non-zero and the
    # probe that used to print `DESTROYED={...}` after the block never runs.
    assert r.returncode != 0, "a refusal must not look like a clean session"
    assert "ANTENNA_DIODE_ROLLED_BACK" in r.stdout
    assert "nA" in r.stdout                            # the net is named
    assert "DESTROYED_ONE d1" in r.stdout              # the diode, by name
    assert "ANTENNA_LOOP_CONVERGED" not in r.stdout    # the violation stands
    assert "ANTENNA_REPAIR_REFUSED_STOP" in r.stdout + r.stderr
    # The parent persists this exact reason in antenna_repair_transaction.json.
    # A damage count alone cannot distinguish a router DRC regression from a
    # placement failure or a missing routing capability.
    request = R.antenna_rollback_request(r.stdout)
    assert request is not None
    assert "native_error=DRT-0206" in request["reason"]


def test_scoped_router_drc_delta_is_named_in_rollback_request():
    """A DRC-worsening scoped reroute must leave a usable cause in the receipt."""
    r = _drive("3 3 1", wired=False, inserts=["d1"], native_error="DRT-0712")
    assert r.returncode != 0
    request = R.antenna_rollback_request(r.stdout)
    assert request is not None
    assert "native_error=DRT-0712" in request["reason"]
    assert "ANTENNA_REPAIR_REFUSED_STOP" in r.stdout + r.stderr


def test_driven_a_pre_existing_instance_is_never_rolled_back():
    """The pre-snapshot is what separates 'this pass inserted it' from 'it was
    already there'. If the snapshot is dead the block destroys i1 as well --
    which is exactly what the `_ant_pre` name collision would have done."""
    r = _drive("5 5 5", wired=False, inserts=["d1"])
    assert "DESTROYED_ONE d1" in r.stdout
    assert "DESTROYED_ONE i1" not in r.stdout


def test_driven_a_raise_that_inserted_nothing_visible_is_unjudged_and_stops():
    r = _drive("5 5 5", wired=True, inserts=[])
    # UNJUDGED does NOT set `_ant_refused`: the pass took nothing away, so
    # there is nothing to roll back and nothing downstream to protect from.
    # The deck completes and the violation is reported by the authoritative
    # post-loop check -- that difference from a REFUSAL is the point.
    assert r.returncode == 0, r.stderr
    assert "ANTENNA_NATIVE_ERROR_UNJUDGED" in r.stdout
    assert "ANTENNA_DIODE_ROLLED_BACK" not in r.stdout
    assert "DESTROYED_ONE" not in r.stdout
    assert "ANTENNA_LOOP_CONVERGED" not in r.stdout


def test_driven_no_whole_design_route_is_ever_reached():
    """The deleted fallback, proven by execution: `detailed_route` is stubbed
    to record itself, and no path through the raise reaches it."""
    import shutil
    import subprocess
    import tempfile
    tclsh = shutil.which("tclsh")
    if tclsh is None:                                   # pragma: no cover
        import pytest
        pytest.skip("tclsh not installed")
    harness = (_ODB_HARNESS % {"seq": "5 5 5", "wired": 0, "inserts": "d1"}
               ).replace('proc detailed_route {args} { return "" }',
                         'proc detailed_route {args} '
                         '{ puts "ROUTED_WHOLE_DESIGN"; return "" }')
    script = harness + _tcl()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "ant.tcl"
        p.write_text(script)
        r = subprocess.run([tclsh, str(p)], capture_output=True, text=True,
                           cwd=td)
    # This scenario is a REFUSAL (wired=0), so R-0915-121(3) stops the deck
    # and it exits non-zero. What this test measures is unchanged and is the
    # stronger half: no path through the raise -- stop included -- reaches a
    # whole-design route.
    assert r.returncode != 0, "a refusal must not look like a clean session"
    assert "ROUTED_WHOLE_DESIGN" not in r.stdout
    assert "ANTENNA_REPAIR_REFUSED_STOP" in r.stdout + r.stderr


def test_driven_a_diode_on_a_net_that_kept_its_wire_is_not_rolled_back():
    """The ruling rolls back THE DIODES FOR THAT NET, not the transaction that
    contained them. This pass inserts two: d1 on nA, which loses its wire, and
    d2 on nB, which keeps it. Only d1 goes."""
    r = _drive("5 5 5", wired=False, inserts=["d1", "d2"])
    assert r.returncode != 0, "a refusal must not look like a clean session"
    assert "ANTENNA_DIODE_ROLLED_BACK" in r.stdout
    assert "DESTROYED_ONE d1" in r.stdout
    assert "DESTROYED_ONE d2" not in r.stdout
    assert "1 kept" in r.stdout          # d2, on a net that kept its wire
