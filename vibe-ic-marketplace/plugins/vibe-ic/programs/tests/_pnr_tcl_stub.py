"""The tclsh stand-in every pnr.tcl evaluability test loads.

Five test files evaluate the COMPLETE `pnr.tcl` under `tclsh` to prove the
deck reaches its end (ORGANIC #581: a Tcl syntax error emitted after detailed
route killed a whole run AFTER routing converged, so no GDS was ever written).
Each of them carried its own copy of one line:

    proc unknown {args} { return "" }

That line is a no-op for every OpenROAD command, which is exactly right for a
deck whose control flow never reads a tool's RETURN VALUE. v1.19.95 emitted
the first block that does: `_cts_legal_buffer_selection_tcl` captures
`report_dont_use` through `utl::redirectString*` and looks masters up through
`[ord::get_db] findMaster`. Under the one-line stub the capture comes back
empty, the block's own "the capture did not work" guard fires, and the deck
aborts at 18% of its length -- so the other 82%, including every post-route
block #581 was written to protect, stopped being parsed at all.

The stub is not a relaxation of that guard: the guard is CORRECT, and it is
measured correct against the pinned image (OpenROAD 26Q3-2075-g18e98f9e44,
`report_dont_use` present, `utl::redirectString*` round-trips the sentinels,
verified with and without `-metrics`). What was missing is a stand-in able to
represent it. So the four primitives the deck reads a value from are modelled
with real semantics, and EVERYTHING else still falls through to the same
no-op `unknown` as before -- the stub grows only where the deck grew.

`MASTERS_ABSENT` is the same interpreter with `findMaster` answering NULL, so
a test can pin what the deck does when a requested master is not in the
physical database without hand-writing a second stub.

THE FIFTH PRIMITIVE IS OUTPUT REDIRECTION, and it arrived the same way the
first four did -- the deck grew a dependency and the stand-in had to grow with
it. v1.22.13 (#2376) emitted `_pnr_sta_corner_binding_tcl`, which RE-READS the
`sta.rpt` the line above it wrote and stamps each native path with the Liberty
its Corner selects. In OpenROAD `report_checks ... > file` is not shell
redirection: the command wrappers rewrite a trailing `> file` / `>> file` into
`utl::redirect_file_begin`, so the file EXISTS the moment that line runs --
which is why the deck may open it unconditionally, and why doing so is not a
#581 hazard. Under the one-line `unknown` stub nothing created it, and the
deck died at `open ... r` with `couldn't open ".../sta.rpt"` -- MEASURED, 16
cases across SIX of this directory's deck evaluators, all one cause.

So `unknown` now honours a trailing `>`/`>>` the way OpenROAD does: the target
is created (truncated, or appended to) and the command's own output goes into
it. A stubbed command produces no output, so the file is created EMPTY, which
is the honest stand-in for `report_checks` on a design the stub never linked
-- and the deck's reader handles an empty report by writing it straight back,
the same branch a real no-paths report takes. It is NOT a relaxation: nothing
in a deck is exempted and no assertion moves; a deck that opens a file NOTHING
redirected into still fails exactly as before.
"""
from __future__ import annotations

# OpenROAD's command wrappers turn a trailing `> file` / `>> file` into a
# real redirect of that command's output; every other argument is passed
# through and the command's return value is unchanged. Modelled here in the
# ONE place every unstubbed command lands, so the deck cannot grow a second
# redirect site the stand-in does not see.
_UNKNOWN = r"""
proc unknown {args} {
  set n [llength $args]
  if {$n >= 2} {
    set redirect [lindex $args [expr {$n - 2}]]
    if {$redirect eq ">" || $redirect eq ">>"} {
      set target [lindex $args end]
      set f [open $target [expr {$redirect eq ">" ? "w" : "a"}]]
      close $f
    }
  }
  return ""
}
"""


# `utl::report` under an active `utl::redirectStringBegin` accumulates instead
# of printing, and `utl::redirectStringEnd` hands the accumulation back --
# the contract the deck's sentinel check is written against.
_UTL = '''
namespace eval utl {
  variable capture ""
  variable capturing 0
  proc redirectStringBegin {} {
    variable capture ""
    variable capturing 1
  }
  proc report {args} {
    variable capturing
    variable capture
    if {$capturing} {
      append capture "[join $args { }]\\n"
    } else {
      puts [join $args " "]
    }
  }
  proc redirectStringEnd {} {
    variable capturing
    variable capture
    set capturing 0
    set out $capture
    set capture ""
    return $out
  }
}
'''

# The shape OpenROAD's own `report_dont_use` prints on a linked design with
# nothing excluded (measured in the pinned image).
_REPORT_DONT_USE = '''
proc report_dont_use {} {
  utl::report "Don't Use Cells:"
  utl::report "  none"
}
'''

# `[ord::get_db] findMaster <name>` -> a handle that answers `getName` and
# `getWidth`. Width 0 keeps every master inside any measured width bound, so
# the stub never invents an exclusion the real database would not make.
_ORD_DB_PRESENT = '''
namespace eval ord { proc get_db {} { return ::_stub_db } }
proc ::_stub_db {op args} {
  if {$op ne "findMaster"} { return "" }
  set _n [lindex $args 0]
  interp alias {} ::_stub_master_$_n {} ::_stub_master $_n
  return ::_stub_master_$_n
}
proc ::_stub_master {name op args} {
  switch -- $op {
    getName  { return $name }
    getWidth { return 0 }
    default  { return "" }
  }
}
'''

_ORD_DB_ABSENT = '''
namespace eval ord { proc get_db {} { return ::_stub_db } }
proc ::_stub_db {op args} {
  if {$op ne "findMaster"} { return "" }
  return NULL
}
'''

#: Prepend to any deck evaluated under `tclsh`.
STUB = _UNKNOWN + _UTL + _REPORT_DONT_USE + _ORD_DB_PRESENT

#: The same interpreter, with no master present in the physical database.
STUB_MASTERS_ABSENT = _UNKNOWN + _UTL + _REPORT_DONT_USE + _ORD_DB_ABSENT


# OPT-IN, never part of STUB: an OpenSTA session in which `read_spef` DID
# annotate the design. R-0915-83's census sets `parasitics_in_sta` only from
# `report_parasitic_annotation -report_unannotated`, read back, plus one leaf
# driver pin of the linked design that is not listed in it. Under the plain
# STUB nothing is annotated -- the report is created empty and `get_pins`
# answers nothing -- so the census refuses, which is the honest answer for a
# stand-in that models no parasitics. A harness that models a WORKING
# `read_spef` must model this too, beside it.
#
# The report is the tool's own bytes for an annotated session (MEASURED,
# vibeic-eda 0.3.79, `calibration/drv_with_parasitics_negative.ann`). The pin
# queries answer ONLY for the census's own `get_pins -hierarchical *` and the
# one stand-in pin, so every other `get_pins`/`get_property` in the deck gets
# the same "" the fallthrough gave it before.
ANNOTATED_SESSION = r"""
proc report_parasitic_annotation {args} {
  set n [llength $args]
  if {$n >= 2 && [lindex $args end-1] in {> >>}} {
    set f [open [lindex $args end] [expr {[lindex $args end-1] eq ">" ? "w" : "a"}]]
    puts $f "Found 0 unannotated drivers."
    puts $f "Found 0 partially unannotated drivers."
    close $f
  }
}
proc get_pins {args} {
  if {$args eq {-hierarchical *}} { return {_stub_drv/Y} }
  return ""
}
proc get_full_name {obj} { return $obj }
proc get_property {obj prop} {
  if {$obj ne "_stub_drv/Y"} { return "" }
  switch -- $prop { direction { return output } is_hierarchical { return 0 } }
  return ""
}
"""
