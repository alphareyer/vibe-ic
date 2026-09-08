"""Bounded route-only recovery for native DRC nets attached to fixed IO pads.

Consumed by the normal named-marker repair stage. The ordinary spare-safe
clear policy remains unchanged. A pad's dont_touch
protects its instance; this transaction never edits an instance or terminal.
Reserved instances and non-pad dont_touch endpoints still forbid rip-up.
Every instance attribute and net terminal is compared before accepting output.
Final native DRC, routing integrity and antenna checks remain mandatory.
"""
from __future__ import annotations

import re
from pathlib import Path

from phase3_one_shot_runner import (
    _routing_integrity_check_tcl,
    _spare_safe_clear_net_proc_tcl,
)


def _word(value: str) -> str:
    if any(c in value for c in "{}\n\r"):
        raise ValueError("Unsupported Tcl identifier/path")
    return "{" + value + "}"


def strict_integrity_tcl(marker: str) -> str:
    """Use the shared geometric connectivity check, refusing unknown or holes."""
    t = _routing_integrity_check_tcl(marker)
    handler = f'puts "{marker}_UNROUTED_CHECK_NONFATAL: $e"'
    if t.count(handler) != 1:
        raise ValueError("Shared integrity failure handler changed")
    t = t.replace(handler,
                  f'error "{marker}_UNROUTED_CHECK_FAILED: $e"')
    return t + f'if {{$_unr != 0}} {{error "{marker}_INCOMPLETE: $_unr $_unrn"}}\n'


def normal_recovery_tcl(rpt_path: str,
                        reserved_instances: list[str] | None) -> str:
    """Live native Short markers select the transaction in the normal producer.

    None means the caller did not supply an authoritative reserve plan: retain
    the original protected-wire policy. An empty list is a declared empty plan.
    Existing routing layers remain in force. Only the selected nets are added
    to GRT; normal guide regeneration is required before detailed routing.
    """
    if reserved_instances is None:
        return 'puts "PAD_MARKER_RECOVERY_SKIP: reserve plan unavailable"\n'
    t = _guard_tcl(reserved_instances)
    t += _spare_safe_clear_net_proc_tcl()
    t += r'''
proc _normal_pad_marker_recovery {rpt} {
  global _b _identity_before
  if {![file exists $rpt]} {puts "PAD_MARKER_RECOVERY_SKIP: native report unavailable"; return}
  set fp [open $rpt r]; set report [read $fp]; close $fp
  set names {}; set short 0
  foreach line [split $report \n] {
    if {[regexp {^violation type: (.+)$} $line -> type]} {set short [expr {$type eq "Short"}]}
    if {!$short} {continue}
    foreach token [split [string trim $line]] {
      if {[string range $token 0 3] eq "net:"} {lappend names [string range $token 4 end]}
    }
  }
  set names [lsort -unique $names]; set pads 0; set refused {}
  foreach name $names {
    set n [$_b findNet $name]
    if {$n eq "NULL"} {lappend refused [list $name MISSING]; continue}
    set disposition [_route_only_class $n]
    puts "PAD_MARKER_ADMISSION $name $disposition"
    # Discover the pad independently of permission: a protected ordinary
    # sink can make the same pad net PROTECTED_NONPAD and must still refuse.
    foreach it [$n getITerms] {
      set i [$it getInst]
      if {[[$i getMaster] isPad] && [$i isDoNotTouch]
          && [$i getPlacementStatus] in {FIRM LOCKED FIXED COVER}} {
        incr pads
        break
      }
    }
    if {$disposition ni {ORDINARY FIXED_PAD_SIGNAL}} {lappend refused [list $name $disposition]}
  }
  if {$pads == 0} {puts "PAD_MARKER_RECOVERY_SKIP: no fixed-pad Short marker"; return}
  if {[llength $refused]} {error "PAD_MARKER_RECOVERY_REFUSED $refused"}
  puts "PAD_MARKER_RECOVERY_START [llength $names] $names"
  foreach name $names {
    set n [$_b findNet $name]
    if {[_route_only_class $n] eq "FIXED_PAD_SIGNAL"} {
      set w [$n getWire]
      if {$w ne "NULL"} {odb::dbWire_destroy $w}
    } else {
      set disposition [_vibeic_spare_safe_clear_net $n]
      if {$disposition ni {CLEARED UNROUTED}} {error "PAD_MARKER_CLEAR_REFUSED $name $disposition"}
    }
    grt::add_net_to_route $n
  }
  global_route
  detailed_route -droute_end_iter 30 -output_drc $rpt
  drt::check_drc -output_file $rpt -marker_name PAD_MARKER_FINAL
  if {[_route_identity] ne $_identity_before} {error PAD_MARKER_IDENTITY_CHANGED}
  puts "PAD_MARKER_IDENTITY_UNCHANGED [llength $_identity_before]"
'''
    t += strict_integrity_tcl('PAD_MARKER_RECOVERY')
    t += r'''
  set fp [open $rpt r]; set final [read $fp]; close $fp
  set count [regexp -all -line {^violation type:} $final]
  puts "PAD_MARKER_FINAL_DRC $count"
  if {$count != 0} {error "PAD_MARKER_RESIDUAL_DRC $count"}
  set antenna [check_antennas -verbose]
  puts "PAD_MARKER_FINAL_ANTENNA $antenna"
  if {![string is integer -strict $antenna] || $antenna != 0} {
    error "PAD_MARKER_ANTENNA_NOT_CLEAN $antenna"
  }
  puts PAD_MARKER_RECOVERY_COMPLETE
}
'''
    return t + f'_normal_pad_marker_recovery {_word(rpt_path)}\n'


def _guard_tcl(reserved_instances: list[str]) -> str:
    t = "set _reserved [dict create]\n"
    for name in sorted(set(reserved_instances)):
        t += f"dict set _reserved {_word(name)} 1\n"
    t += r'''
proc _route_identity {} {
  set records {}
  foreach i [[ord::get_db_block] getInsts] {
    lappend records [list INST [$i getName] [[$i getMaster] getName] [$i getOrigin] [$i getOrient] [$i getPlacementStatus] [$i isDoNotTouch]]
  }
  foreach n [[ord::get_db_block] getNets] {
    set pins {}
    foreach it [$n getITerms] {lappend pins [list I [[$it getInst] getName] [[$it getMTerm] getName]]}
    foreach bt [$n getBTerms] {lappend pins [list B [$bt getName]]}
    lappend records [list NET [$n getName] [$n getSigType] [lsort $pins]]
  }
  return [lsort $records]
}
proc _route_only_class {_n} {
  global _reserved
  if {[$_n getSigType] in {POWER GROUND}} {return PG}
  set pad 0
  set ordinary 0
  foreach it [$_n getITerms] {
    set i [$it getInst]
    if {[dict exists $_reserved [$i getName]]} {return RESERVED}
    if {[$i isDoNotTouch]} {
      if {![[$i getMaster] isPad]} {return PROTECTED_NONPAD}
      if {[$i getPlacementStatus] ni {FIRM LOCKED FIXED COVER}} {return UNFIXED_PAD}
      set pad 1
    } elseif {![[$i getMaster] isPad]} {set ordinary 1}
  }
  if {$pad && $ordinary} {return FIXED_PAD_SIGNAL}
  return ORDINARY
}
set _identity_before [_route_identity]
set _b [ord::get_db_block]
set _reserved_nets [dict create]
foreach name [dict keys $_reserved] {
  set i [$_b findInst $name]
  if {$i eq "NULL"} {error "RESERVED_INSTANCE_MISSING $name"}
  foreach it [$i getITerms] {
    set n [$it getNet]
    if {$n ne "NULL"} {dict set _reserved_nets [$n getName] $n}
  }
}
foreach name [dict keys $_reserved_nets] {
  set disposition [_route_only_class [dict get $_reserved_nets $name]]
  if {$disposition ni {PG RESERVED}} {error "RESERVED_CONTROL_FAILED $name $disposition"}
}
puts "RESERVED_ROUTE_CONTROLS [dict size $_reserved_nets]"
'''
    return t


def emit_repair(source_odb: Path, marker_report: Path, out: Path,
                reserved_instances: list[str], threads: int = 8,
                signal_layers: str = "", clock_layers: str = "") -> str:
    text = marker_report.read_text()
    if not re.search(r"^violation type: Short$", text, re.M):
        raise ValueError("Native Short marker required")
    nets = sorted(set(re.findall(r"\bnet:(\S+)", text)))
    if not nets or not reserved_instances:
        raise ValueError("Named native markers and reserved-instance plan required")
    if threads < 1 or threads > 16:
        raise ValueError("Thread bound exceeded")
    t = f"set_thread_count {threads}\nread_db {_word(str(source_odb))}\n"
    t += _guard_tcl(reserved_instances)
    t += _spare_safe_clear_net_proc_tcl()
    t += "set _targets [list " + " ".join(_word(n) for n in nets) + "]\n"
    t += r'''
set _pad_targets 0
foreach name $_targets {
  set n [$_b findNet $name]
  if {$n eq "NULL"} {error "MARKER_NET_MISSING $name"}
  set disposition [_route_only_class $n]
  puts "ROUTE_ONLY_ADMISSION $name $disposition"
  if {$disposition ni {ORDINARY FIXED_PAD_SIGNAL}} {error "ROUTE_ONLY_REFUSED $name $disposition"}
  if {$disposition eq "FIXED_PAD_SIGNAL"} {incr _pad_targets}
}
if {$_pad_targets == 0} {error "NO_FIXED_PAD_SIGNAL_TARGET"}
# Tool-owned rip-up and re-route. No cell/flag/pin/net-connection editing.
foreach name $_targets {
  set n [$_b findNet $name]
  if {[_route_only_class $n] eq "FIXED_PAD_SIGNAL"} {
    set w [$n getWire]
    if {$w ne "NULL"} {odb::dbWire_destroy $w}
    puts "ROUTE_ONLY_FIXED_PAD_WIRE_INVALIDATED $name"
  } else {
    puts "ROUTE_ONLY_ORDINARY $name [_vibeic_spare_safe_clear_net $n]"
  }
}
'''
    if not signal_layers or not clock_layers:
        raise ValueError("Existing routing-layer contract required")
    t += f"set_routing_layers -signal {_word(signal_layers)} -clock {_word(clock_layers)}\n"
    t += "foreach name $_targets {grt::add_net_to_route [$_b findNet $name]}\n"
    t += "global_route\nputs ROUTE_ONLY_TARGET_GUIDES_REBUILT\n"
    t += f"detailed_route -droute_end_iter 30 -output_drc {_word(str(out/'route.drc'))}\n"
    t += _routing_integrity_check_tcl("PAD_ROUTE_ONLY")
    t += r'''
set _identity_after [_route_identity]
if {$_identity_before ne $_identity_after} {error "ROUTE_ONLY_IDENTITY_CHANGED"}
puts "ROUTE_ONLY_IDENTITY_UNCHANGED [llength $_identity_after]"
'''
    t += f"drt::check_drc -output_file {_word(str(out/'final.drc'))} -marker_name PAD_ROUTE_ONLY_FINAL\n"
    for command, name in [('write_db', 'routed.odb'), ('write_def', 'routed.def'),
                          ('write_verilog', 'routed.v')]:
        t += f"{command} {_word(str(out/name))}\n"
    t += "check_placement\ncheck_antennas -verbose\nputs PAD_ROUTE_ONLY_COMPLETE\nexit\n"
    return t
