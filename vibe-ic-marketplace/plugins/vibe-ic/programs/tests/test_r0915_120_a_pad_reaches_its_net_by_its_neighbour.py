"""A PG terminal reaches its net by a rail OR by a same-net neighbour.

R-0915-120, refining R-0915-114(c). MEASURED on int8 (subservient x gf180mcuD
as a DIE, 2026-09-21) on the run's own `antenna_pre_repair.odb`:

    PG terminals                                      187948
    on no net                                              0
    overlapping no rail of their net                    1416   (0.75%)
       908 gf180mcu_fd_io__fill10   204 gf180mcu_fd_io__fill1
       160 gf180mcu_fd_io__fillnc    84 gf180mcu_fd_io__bi_24t
        40 gf180mcu_fd_io__in_c      16 gf180mcu_fd_io__cor
         2 gf180mcu_fd_io__dvdd       2 gf180mcu_fd_io__dvss
       -- every one a pad-ring cell, ZERO standard cells, all in the 393 um
       pad band the core PDN's stripes correctly do not reach.
    of those, abutting a same-net PG terminal on ANOTHER instance    1416
    touching neither a rail nor a same-net neighbour                    0

R-0915-114(c) asked only about rails. That is the right question for a core
cell over the PDN grid and the wrong one for a pad ring, which carries
VDD/VSS/DVDD/DVSS by PAD-TO-PAD ABUTMENT -- the same abutment mechanism
R-0915-114 named for ties, fillers, decaps and spares, with the adjacent pad
as the partner instead of a stripe. The refusal was a 100% false positive and
it cost int8 its routed.def, its DRC, its LVS and its post-route STA.

The gate grows no hole: a terminal that touches NEITHER still fails by name,
a neighbour on a DIFFERENT net does not count, and a cell's own second PG pin
does not count. Each of those is driven below through the real Tcl
interpreter against an ODB emulated at the accessor level the block uses --
the same lesson as the `dbSBox.getBox` false zero: a traversal that cannot see
the geometry reports a connection that exists as absent.

MEASURED BOTH DIRECTIONS at the branch point: against the pre-R-0915-120
block these ten read 4 failed / 6 passed, and the six are the over-breadth
controls marked as such in their own bodies -- they are green on both sides
BY DESIGN, and exist so the new clause cannot widen into "touches any PG
terminal".
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


def _block():
    return R._build_pg_reconnect_tcl(reroute=True)


def _cmds():
    return "\n".join(ln for ln in _block().splitlines()
                     if not ln.lstrip().startswith("#"))


# ── the emulator ─────────────────────────────────────────────────────────────
# a terminal is {inst pin net x0 y0 x1 y1 sigtype rail}; rail=1 means the net
# owns a special wire exactly covering this terminal.
_HARNESS = r"""
set ::terms {%(terms)s}

proc global_connect {args} { return "" }
proc pin_access {args} { return "" }
proc detailed_route_num_drvs {args} { return 0 }
namespace eval ord { proc get_db_block {} { return ::BLK } }

proc _mk {i} {
  foreach {nm lo} {IT 0 MT 0 NT 0 SW 0 WR 0 BB 0} { }
  proc ::IT_$i {method args} [string map [list @I $i] {
    switch -- $method {
      getMTerm { return ::MT_@I }
      getNet   { return ::NT_@I }
      getBBox  { return ::BB_@I }
    }
    return 0
  }]
  proc ::MT_$i {method args} [string map [list @I $i] {
    set t [lindex $::terms @I]
    switch -- $method {
      getSigType { return [lindex $t 7] }
      getName    { return [lindex $t 1] }
    }
    return 0
  }]
  proc ::NT_$i {method args} [string map [list @I $i] {
    set t [lindex $::terms @I]
    switch -- $method {
      getName   { return [lindex $t 2] }
      getSWires { if {[lindex $t 8]} { return [list ::SW_@I] } ; return {} }
    }
    return 0
  }]
  proc ::SW_$i {method args} [string map [list @I $i] {
    switch -- $method { getWires { return [list ::WR_@I] } }
    return {}
  }]
  foreach p {WR BB} {
    proc ::${p}_$i {method args} [string map [list @I $i] {
      set t [lindex $::terms @I]
      switch -- $method {
        xMin { return [lindex $t 3] } yMin { return [lindex $t 4] }
        xMax { return [lindex $t 5] } yMax { return [lindex $t 6] }
      }
      return 0
    }]
  }
}

set ::instorder {}
array set ::instterms {}
for {set i 0} {$i < [llength $::terms]} {incr i} {
  _mk $i
  set nm [lindex [lindex $::terms $i] 0]
  if {[lsearch -exact $::instorder $nm] < 0} {
    lappend ::instorder $nm
    set ::instterms($nm) {}
  }
  lappend ::instterms($nm) ::IT_$i
}
set ::instcmds {}
set k 0
foreach nm $::instorder {
  proc ::IN_$k {method args} [string map [list @N $nm] {
    switch -- $method {
      getName      { return {@N} }
      getITerms    { return $::instterms(@N) }
      isDoNotTouch { return 0 }
      getMaster    { return ::MASTER }
    }
    return 0
  }]
  lappend ::instcmds ::IN_$k
  incr k
}
proc ::MASTER {method args} { return "emulated_master" }
proc ::DIE {method args} {
  switch -- $method {
    xMin { return 0 } yMin { return 0 }
    xMax { return 2000000 } yMax { return 2000000 }
  }
  return 0
}
proc ::BLK {method args} {
  switch -- $method {
    getInsts   { return $::instcmds }
    getDieArea { return ::DIE }
  }
  return NULL
}
"""


def _drive(terms):
    if tclsh is None:                                    # pragma: no cover
        pytest.skip("tclsh not installed")
    rows = " ".join("{%s}" % " ".join(str(f) for f in t) for t in terms)
    script = (_HARNESS % {"terms": rows}) + _block()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "pg.tcl"
        p.write_text(script)
        return subprocess.run([tclsh, str(p)], capture_output=True, text=True,
                              cwd=td)


# a pad and its neighbour, touching, on the same net, neither over a rail
_PAD_A = ["padA", "VDD", "VDD", 0, 0, 100, 100, "POWER", 0]
_PAD_B = ["padB", "VDD", "VDD", 100, 0, 200, 100, "POWER", 0]


@needs_tclsh
def test_a_terminal_over_a_rail_is_connected_as_it_always_was():
    """The half R-0915-114(c) already had, unchanged.

    OVER-BREADTH CONTROL: green on both sides of the change by design."""
    r = _drive([["u1", "VPWR", "VDD", 0, 0, 10, 10, "POWER", 1]])
    assert "PG_ABUTMENT_NOT_CONNECTED" not in r.stdout + r.stderr
    assert "PG_ABUTMENT_OK" in r.stdout


@needs_tclsh
def test_a_pad_with_no_rail_reaches_its_net_by_its_same_net_neighbour():
    """int8's 1416: no rail anywhere, and connected all the same."""
    r = _drive([_PAD_A, _PAD_B])
    assert "PG_ABUTMENT_NOT_CONNECTED" not in r.stdout + r.stderr, r.stdout
    assert "PG_ABUTMENT_OK" in r.stdout
    assert "2 of 2 terminal(s) that overlap no rail reach the net by abutting" \
        in r.stdout


@needs_tclsh
def test_a_terminal_with_no_rail_and_no_neighbour_is_still_refused_by_name():
    """The tooth the gate keeps. A floating terminal has no same-net
    neighbour either, so the second question does not rescue it."""
    r = _drive([["lonely", "VPWR", "VDD", 0, 0, 10, 10, "POWER", 0]])
    out = r.stdout + r.stderr
    assert "PG_ABUTMENT_NOT_CONNECTED" in out
    assert "lonely/VPWR" in out
    assert "no rail overlap and no same-net neighbour" in out


@needs_tclsh
def test_a_neighbour_of_a_different_net_does_not_count():
    """OVER-BREADTH CONTROL: passes against the pre-R-0915-120 block too,
    which refused everything without a rail. It exists to stop the new
    clause widening into "touches any PG terminal"."""
    r = _drive([["padA", "VDD", "VDD", 0, 0, 100, 100, "POWER", 0],
                ["padB", "VSS", "VSS", 100, 0, 200, 100, "GROUND", 0]])
    out = r.stdout + r.stderr
    assert "PG_ABUTMENT_NOT_CONNECTED" in out
    assert "padA/VDD" in out
    assert "padB/VSS" in out


@needs_tclsh
def test_a_cells_own_second_pg_pin_does_not_count_as_its_neighbour():
    """Two pins of ONE instance touching each other connect it to nothing.

    OVER-BREADTH CONTROL: green on both sides of the change by design."""
    r = _drive([["solo", "VDD1", "VDD", 0, 0, 100, 100, "POWER", 0],
                ["solo", "VDD2", "VDD", 100, 0, 200, 100, "POWER", 0]])
    out = r.stdout + r.stderr
    assert "PG_ABUTMENT_NOT_CONNECTED" in out
    assert "solo/VDD1" in out


@needs_tclsh
def test_a_signal_terminal_is_not_this_gates_business():
    """OVER-BREADTH CONTROL: green on both sides of the change by design."""
    r = _drive([["u1", "A", "n1", 0, 0, 10, 10, "SIGNAL", 0]])
    assert "PG_ABUTMENT_NOT_CONNECTED" not in r.stdout + r.stderr


@needs_tclsh
def test_a_neighbour_that_only_comes_near_does_not_count():
    """Abutment is touching, not proximity: a gap is not a connection.

    OVER-BREADTH CONTROL: green on both sides of the change by design."""
    r = _drive([["padA", "VDD", "VDD", 0, 0, 100, 100, "POWER", 0],
                ["padB", "VDD", "VDD", 101, 0, 200, 100, "POWER", 0]])
    out = r.stdout + r.stderr
    assert "PG_ABUTMENT_NOT_CONNECTED" in out
    assert "padA/VDD" in out


def test_the_second_question_is_asked_only_of_the_no_rail_set():
    """A design whose terminals all sit over rails pays nothing for
    R-0915-120: the index is built inside the `llength $_pgab_nr > 0` guard."""
    cmds = _cmds()
    i_guard = cmds.index("if {[llength $_pgab_nr] > 0} {")
    i_index = cmds.index("array unset _pgab_ix")
    assert i_guard < i_index


def test_the_partner_must_be_a_different_instance():
    assert "if {[lindex $_pgab_c 0] eq $_pgab_on} { continue }" in _cmds()


def test_the_block_still_never_routes():
    """R-0915-114(a) holds: this block connects and checks."""
    cmds = _cmds()
    assert "detailed_route -verbose" not in cmds
    assert "global_route" not in cmds
