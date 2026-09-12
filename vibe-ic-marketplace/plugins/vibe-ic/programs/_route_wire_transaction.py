"""Same-session route trials without destroying the linked STA database.

The original wire objects remain alive while native copies are routed. Restore
is credited only after native DEF bytes match the trial-start checkpoint.
This is not an ECO journal or a read_db rollback: neither preserves both route
geometry and the live STA network on the measured tool.
"""


def wire_transaction_tcl() -> str:
    return r'''
proc _vic_wire_begin {directory report} {
  if {[file exists $directory]} { error "WIRE_TRIAL_DIRECTORY_EXISTS $directory" }
  file mkdir $directory
  write_def $directory/before.def
  file copy $report $directory/before.drc.rpt
  set saved {}
  foreach net [[ord::get_db_block] getNets] {
    set wire [$net getWire]
    lappend saved [list $net $wire]
    if {$wire eq "NULL"} { continue }
    $wire detach
    set trial [odb::dbWire_create $net]
    $trial append $wire
  }
  return $saved
}
proc _vic_wire_finish {directory report saved accept} {
  if {$accept} {
    foreach item $saved {
      lassign $item net wire
      if {$wire ne "NULL"} { odb::dbWire_destroy $wire }
    }
    return
  }
  # Preserve the rejected evidence before restoring the selected report.
  if {[file exists $report]} { file copy $report $directory/rejected.drc.rpt }
  write_def $directory/rejected.def
  foreach item $saved {
    lassign $item net wire
    if {$wire ne "NULL"} {
      $wire attach $net
      if {[[$net getWire] getId] != [$wire getId]} { error WIRE_RESTORE_ID_MISMATCH }
    } else {
      set trial [$net getWire]
      if {$trial ne "NULL"} { odb::dbWire_destroy $trial }
    }
  }
  write_def $directory/restored.def
  set a [open $directory/before.def rb]
  set b [open $directory/restored.def rb]
  set same 1
  while {![eof $a] || ![eof $b]} {
    if {[read $a 65536] ne [read $b 65536]} { set same 0; break }
  }
  close $a; close $b
  if {!$same} { error WIRE_RESTORE_DEF_MISMATCH }
  # If any non-wire object changed, the byte check above refuses the restore.
  # Never publish a selected report for a partially restored database.
  file copy -force $directory/before.drc.rpt $report
}
'''
