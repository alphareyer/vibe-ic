# vibe-ic step Vibeic.ClockNetworkGlobalSizing (see __init__.py).
# The sizing is the TOOL's: OpenROAD `repair_timing -phases GLOBAL_SIZING`
# with the clock network included (`set_global_sizing_config
# -include_clock_network`), so a clock-path cell that neither CTS nor the
# default resizer phases own (#2160) is sized by the resizer itself.
source $::env(SCRIPTS_DIR)/openroad/common/io.tcl
source $::env(SCRIPTS_DIR)/openroad/common/resizer.tcl

read_current_odb

set_propagated_clock [all_clocks]
set_dont_touch_objects

source $::env(SCRIPTS_DIR)/openroad/common/set_rc.tcl
estimate_parasitics -placement

set_global_sizing_config -include_clock_network true
report_global_sizing_config
log_cmd repair_timing -verbose -setup \
    -setup_margin $::env(PL_RESIZER_SETUP_SLACK_MARGIN) -phases GLOBAL_SIZING

source $::env(SCRIPTS_DIR)/openroad/common/dpl.tcl
unset_dont_touch_objects

# The clock tree's fanout, measured here because this is the session that
# knows which instances CTS created (not in the pre-CTS snapshot): the
# largest number of loads on a net such an instance drives. It is an AUDIT
# input (`cts_quality_check` compares it with MAX_FANOUT_CONSTRAINT), not a
# repair.
set _vic_prects_insts [dict create]
set _vic_fh [open $::env(VIBEIC_CLKPATH_PRECTS_INSTANCES)]
foreach _vic_pi [split [read $_vic_fh] "\n"] {
    if {$_vic_pi ne ""} { dict set _vic_prects_insts $_vic_pi 1 }
}
close $_vic_fh
set _vic_cts_insts 0
set _vic_cts_maxfo 0
foreach _vic_inst [[ord::get_db_block] getInsts] {
    if {[dict exists $_vic_prects_insts [$_vic_inst getName]]} { continue }
    incr _vic_cts_insts
    foreach _vic_it [$_vic_inst getITerms] {
        if {[[$_vic_it getMTerm] getIoType] ne "OUTPUT"} { continue }
        set _vic_net [$_vic_it getNet]
        if {$_vic_net eq "NULL" || $_vic_net eq ""} { continue }
        set _vic_fo [expr {[llength [$_vic_net getITerms]] - 1
                           + [llength [$_vic_net getBTerms]]}]
        if {$_vic_fo > $_vic_cts_maxfo} { set _vic_cts_maxfo $_vic_fo }
    }
}
puts "CLKPATH_CTS_TREE: created_instances=$_vic_cts_insts max_fanout=$_vic_cts_maxfo"
utl::metric_integer "vibeic__cts__created_instance__count" $_vic_cts_insts
utl::metric_integer "vibeic__cts__max_fanout" $_vic_cts_maxfo

source $::env(SCRIPTS_DIR)/openroad/common/set_rc.tcl
estimate_parasitics -placement
write_views
