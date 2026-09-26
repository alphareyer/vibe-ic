# vibe-ic step Vibeic.PostRouteRepair (flow step 32, T102). See postroute_repair.py.
#
# One candidate of the post-detailed-route repair, on the routed ODB:
#   parasitics from the router's own wires (OpenRCX, every declared ruleset,
#   read back per STA corner) marked as the detailed-routing source (the
#   vibeic/OpenROAD fork's `estimate_parasitics -detailed_routing`) ->
#   repair_design -> repair_timing -setup -> repair_timing -hold ->
#   legalize the changed cells -> connect every created cell to its supply ->
#   ECO route of the nets the repair touched -> re-verify.
# It JUDGES NOTHING: every number it prints is a census the caller's judge
# (OpenROAD.RCX + OpenROAD.STAPostPNR, pg_supply_pin_ownership_check) reads
# next to the tool's own reports.
source $::env(SCRIPTS_DIR)/openroad/common/io.tcl
source $::env(SCRIPTS_DIR)/openroad/common/resizer.tcl

read_current_odb
source $::env(SCRIPTS_DIR)/openroad/common/set_rc.tcl
set_propagated_clock [all_clocks]

proc vic_say {line} { puts "PRR: $line" }
proc vic_metric {name value} {
    if {[string is double -strict $value]} { utl::metric_float $name $value }
}

# ---- 0. the fork capability ----------------------------------------------
# `estimate_parasitics -detailed_routing` is the vibeic/OpenROAD fork's flag.
# The caller probes it before this step runs (librelane_postroute_repair.
# fork_capability: the real flag against a bogus control flag, never a
# help-text scrape); on an OpenROAD without it the call below fails the step.

# ---- 1. the supply rules the direct deck registers (F24) ----------------
# write_views re-applies LibreLane's own rules; the direct deck's rules are
# registered too, so a cell this step creates is on the declared supply
# whichever flow produced the database.
if {[info exists ::env(VIBEIC_PRR_PG_RULES_TCL)] && $::env(VIBEIC_PRR_PG_RULES_TCL) ne ""} {
    source $::env(VIBEIC_PRR_PG_RULES_TCL)
    vic_say "pg rules registered from $::env(VIBEIC_PRR_PG_RULES_TCL)"
}

set_dont_touch_objects

# ---- 2. the input census --------------------------------------------------
set ::vic_before [dict create]
set ::vic_at [dict create]
set ::vic_fillers 0
foreach inst [$::block getInsts] {
    if {[[$inst getMaster] getType] in {CORE_SPACER}} {
        incr ::vic_fillers
        continue
    }
    dict set ::vic_before [$inst getName] [[$inst getMaster] getName]
    dict set ::vic_at [$inst getName] [list {*}[$inst getLocation] [$inst getOrient]]
}
vic_say "input instances=[dict size $::vic_before] fillers=$::vic_fillers"
vic_metric vibeic__prr__input_fillers $::vic_fillers

# Fillers leave: a fully tiled row has no legal site for a resized cell, and
# removing instances after parasitics are annotated invalidates them (EST-0104).
remove_fillers

# ---- 3. parasitics from the router's wires, per STA corner ---------------
proc vic_annotate {tag} {
    set spefs [dict create]
    foreach {pattern ruleset} $::env(RCX_RULESETS) {
        set sane [string map {* x} $pattern]
        set out "$::env(STEP_DIR)/${tag}_${sane}.spef"
        define_process_corner -ext_model_index 0 X
        if {[catch {extract_parasitics -ext_model_file $ruleset -lef_res} err]} {
            puts stderr "VIBEIC_PRR_EXTRACTION_FAILED: $pattern: $err"
            exit 1
        }
        write_spef $out
        dict set spefs $pattern $out
    }
    set read 0
    set names [lln::get_corner_names]
    foreach name $names {
        dict for {pattern spef} $spefs {
            if {[string match $pattern $name]} {
                read_spef -corner $name $spef
                incr read
                break
            }
        }
    }
    if {$read != [llength $names]} {
        puts stderr "VIBEIC_PRR_CORNER_UNANNOTATED: $read of [llength $names] corners have a ruleset"
        exit 1
    }
    estimate_parasitics -detailed_routing
    vic_say "$tag parasitics: [dict size $spefs] ruleset(s) over $read corner(s)"
}

proc vic_census {tag} {
    # The fanout count is not taken here: sta::max_fanout_violation_count
    # takes Signal 11 in CheckFanouts::check in a multi-corner session on
    # OpenROAD 26Q3 (measured); STAPostPNR counts it per corner.
    set setup [worst_slack -max]
    set hold [worst_slack -min]
    set tns [total_negative_slack -max]
    set slew [sta::max_slew_violation_count]
    set cap [sta::max_capacitance_violation_count]
    vic_say "$tag setup_ws=$setup setup_tns=$tns hold_ws=$hold slew=$slew cap=$cap"
    vic_metric vibeic__prr__${tag}__setup__ws $setup
    vic_metric vibeic__prr__${tag}__setup__tns $tns
    vic_metric vibeic__prr__${tag}__hold__ws $hold
    utl::metric_integer vibeic__prr__${tag}__max_slew__count $slew
    utl::metric_integer vibeic__prr__${tag}__max_cap__count $cap
}

vic_annotate before
vic_census before
# The antenna census of the input route, by the same command the re-verify
# uses, so a candidate is compared with its input on one instrument.
set ::vic_ant_before [check_antennas]
utl::metric_integer vibeic__prr__before__antenna__violating_nets $::vic_ant_before
if {[info exists ::env(VIBEIC_PRR_CENSUS_ONLY)] && $::env(VIBEIC_PRR_CENSUS_ONLY)} {
    utl::metric_integer vibeic__prr__changed 0
    vic_say "census only: nothing repaired; the input database is the result"
    exit 0
}

# ---- 4. repair ---------------------------------------------------------------
set rd_args [list -verbose]
append_if_exists_argument rd_args VIBEIC_PRR_MAX_WIRE_LENGTH -max_wire_length
append_if_exists_argument rd_args VIBEIC_PRR_SLEW_MARGIN_PCT -slew_margin
append_if_exists_argument rd_args VIBEIC_PRR_CAP_MARGIN_PCT -cap_margin
log_cmd repair_design {*}$rd_args

set setup_args [list -setup -verbose]
lappend setup_args -setup_margin $::env(VIBEIC_PRR_SETUP_MARGIN)
lappend setup_args -max_buffer_percent $::env(VIBEIC_PRR_SETUP_MAX_BUFFER_PCT)
append_if_exists_argument setup_args VIBEIC_PRR_SETUP_REPAIR_TNS_PCT -repair_tns
log_cmd repair_timing {*}$setup_args

# Hold after setup (review70 step 32: T60 moved spm SS hold -0.39 -> +0.039
# only once hold ran after setup), and never at setup's expense.
set hold_args [list -hold -verbose]
lappend hold_args -setup_margin $::env(VIBEIC_PRR_SETUP_MARGIN)
lappend hold_args -hold_margin $::env(VIBEIC_PRR_HOLD_MARGIN)
lappend hold_args -max_buffer_percent $::env(VIBEIC_PRR_HOLD_MAX_BUFFER_PCT)
log_cmd repair_timing {*}$hold_args

# ---- 5. what changed -------------------------------------------------------
set ::vic_created [list]
set ::vic_resized [list]
set ::vic_kept 0
foreach inst [$::block getInsts] {
    set name [$inst getName]
    if {![dict exists $::vic_before $name]} {
        lappend ::vic_created $inst
    } else {
        incr ::vic_kept
        if {[dict get $::vic_before $name] ne [[$inst getMaster] getName]} {
            lappend ::vic_resized $inst
        }
    }
}
set ::vic_removed [expr {[dict size $::vic_before] - $::vic_kept}]
vic_say "changed created=[llength $::vic_created] resized=[llength $::vic_resized] removed=$::vic_removed"
utl::metric_integer vibeic__prr__created__count [llength $::vic_created]
utl::metric_integer vibeic__prr__resized__count [llength $::vic_resized]
utl::metric_integer vibeic__prr__removed__count $::vic_removed

set ::vic_changed [expr {[llength $::vic_created] + [llength $::vic_resized] + $::vic_removed}]
utl::metric_integer vibeic__prr__changed $::vic_changed
if {$::vic_changed == 0} {
    # A repair that changed no instance hands back the INPUT database
    # (PostRouteRepair.run): re-routing an identical netlist is a fresh
    # route with its own quality lottery (v1.8.43: 13 new min-area islands).
    vic_say "no-op: the repair changed no instance; the input database is the candidate"
    exit 0
}

# ---- 6. legalize, supply, reroute ------------------------------------------
# Only the cells the repair created or resized are legalized; every other
# instance is LOCKED for the call and restored (the Odb.InsertECOBuffers
# pattern), so the router's placement is not re-legalized around them.
set ::vic_mine [dict create]
foreach inst [concat $::vic_created $::vic_resized] { dict set ::vic_mine [$inst getName] 1 }
set ::vic_locked [list]
foreach inst [$::block getInsts] {
    if {[dict exists $::vic_mine [$inst getName]]} { continue }
    set status [$inst getPlacementStatus]
    if {$status ni {LOCKED FIRM COVER}} {
        lappend ::vic_locked [list $inst $status]
        $inst setPlacementStatus LOCKED
    }
}
source $::env(SCRIPTS_DIR)/openroad/common/dpl_cell_pad.tcl
log_cmd detailed_placement \
    -max_displacement [subst { $::env(PL_MAX_DISPLACEMENT_X) $::env(PL_MAX_DISPLACEMENT_Y) }]
foreach pair $::vic_locked { [lindex $pair 0] setPlacementStatus [lindex $pair 1] }
check_placement -verbose
# A cell the legalizer moved anyway: its wires move too.
set ::vic_moved [list]
foreach inst [$::block getInsts] {
    set name [$inst getName]
    if {[dict exists $::vic_at $name] && ![dict exists $::vic_mine $name]
        && [dict get $::vic_at $name] ne [list {*}[$inst getLocation] [$inst getOrient]]} {
        lappend ::vic_moved $inst
    }
}
utl::metric_integer vibeic__prr__moved__count [llength $::vic_moved]

# Supply: every created cell on its declared supply BEFORE anything is
# written. A database bridged from a DEF carries no global-connect rules (a
# DEF does not hold them: measured, 13 hold buffers with all four supply pins
# on no net), so LibreLane's own rules (SCL_POWER_PINS/SCL_GROUND_PINS onto
# VDD_NETS/GND_NETS, the ones write_views applies) and the direct deck's
# (VIBEIC_PRR_PG_RULES_TCL, registered above) are applied here, and the
# census below is taken after the same connection write_views makes.
source $::env(SCRIPTS_DIR)/openroad/common/set_power_nets.tcl
set_global_connections
global_connect
set ::vic_pg_open 0
foreach inst $::vic_created {
    foreach it [$inst getITerms] {
        if {[$it getSigType] in {POWER GROUND} && [$it getNet] eq "NULL"} {
            incr ::vic_pg_open
            vic_say "supply pin unconnected: [$inst getName]/[[$it getMTerm] getName]"
        }
    }
}
utl::metric_integer vibeic__prr__created__pg_unconnected $::vic_pg_open

# ECO: the nets the repair touched lose their wires and are routed again;
# every other net keeps the router's geometry.
set ::vic_dirty [dict create]
foreach inst [concat $::vic_created $::vic_resized $::vic_moved] {
    foreach it [$inst getITerms] {
        set net [$it getNet]
        if {$net ne "NULL" && [$net getSigType] ni {POWER GROUND}} {
            dict set ::vic_dirty [$net getName] $net
        }
    }
}
vic_say "eco nets=[dict size $::vic_dirty]"
utl::metric_integer vibeic__prr__eco_net__count [dict size $::vic_dirty]

# The nets that share a violation with a routed net, from the router's own
# report (`srcs: net:<a> net:<b>`): the neighbours an ECO route may take up.
proc vic_violation_neighbours {drc named} {
    set found [dict create]
    if {![file exists $drc]} { return $found }
    set fh [open $drc]
    foreach line [split [read $fh] "\n"] {
        set line [string trim $line]
        if {![string match "srcs:*" $line]} { continue }
        set nets [list]
        foreach word [lrange [split $line] 1 end] {
            if {[string match "net:*" $word]} { lappend nets [string range $word 4 end] }
        }
        set touches 0
        foreach n $nets { if {[dict exists $named $n]} { set touches 1 } }
        if {!$touches} { continue }
        foreach n $nets {
            set net [$::block findNet $n]
            if {![dict exists $named $n] && $net ne "NULL"
                && [$net getSigType] ni {POWER GROUND} && ![$net isSpecial]} {
                dict set found $n $net
            }
        }
    }
    close $fh
    return $found
}

# The fork's scoped route (`detailed_route -nets`) refuses a result with more
# whole-design violations than it was given (DRT-0712). A refusal names the
# violations; the nets that share one with a routed net join the set and the
# set is routed again, at most VIBEIC_PRR_ECO_EXPANSIONS times. Every attempt
# is measured by the same guard against ITS entry, and each entry is taken
# after the set's wires are gone, so an attempt can only return a route no
# worse than the one it was given.
set ::vic_eco_attempts 0
set ::vic_eco_ok 1
if {[dict size $::vic_dirty] > 0} {
    set_thread_count $::env(DRT_THREADS)
    set ::vic_eco_ok 0
    set limit [expr {[info exists ::env(VIBEIC_PRR_ECO_EXPANSIONS)]
                     ? $::env(VIBEIC_PRR_ECO_EXPANSIONS) : 2}]
    while {1} {
        dict for {name net} $::vic_dirty {
            set wire [$net getWire]
            if {$wire ne "NULL"} { odb::dbWire_destroy $wire }
            $net setWireOrdered 0
        }
        source $::env(SCRIPTS_DIR)/openroad/common/grt.tcl
        set drc $::env(STEP_DIR)/eco_route.$::vic_eco_attempts.drc
        set drt_args [list -droute_end_iter $::env(DRT_OPT_ITERS) -or_seed 42 -verbose 1 \
                          -output_drc $drc -nets [dict keys $::vic_dirty]]
        incr ::vic_eco_attempts
        if {![catch {log_cmd detailed_route {*}$drt_args} err]} {
            set ::vic_eco_ok 1
            file copy -force $drc $::env(STEP_DIR)/eco_route.drc
            break
        }
        vic_say "eco attempt $::vic_eco_attempts refused: $err"
        set more [vic_violation_neighbours $drc $::vic_dirty]
        if {$::vic_eco_attempts > $limit || [dict size $more] == 0} { break }
        vic_say "eco expands by [dict size $more] neighbour net(s): [dict keys $more]"
        set ::vic_dirty [dict merge $::vic_dirty $more]
    }
}
utl::metric_integer vibeic__prr__eco_attempt__count $::vic_eco_attempts
utl::metric_integer vibeic__prr__eco_net__final_count [dict size $::vic_dirty]
if {!$::vic_eco_ok} {
    puts stderr "VIBEIC_PRR_ECO_ROUTE_REFUSED: the scoped route added whole-design violations on every attempt ($::vic_eco_attempts); the candidate is not written"
    exit 1
}

# ---- 7. re-verify ------------------------------------------------------------
# Router DRC: the fork's scoped route refuses a result with more whole-design
# violations than it was given (DRT-0711/0712), which fails this step; the
# count below is the ECO route's own report, recorded for the reader.
set ::vic_drt 0
if {[file exists $::env(STEP_DIR)/eco_route.drc]} {
    set fh [open $::env(STEP_DIR)/eco_route.drc]
    foreach line [split [read $fh] "\n"] {
        if {[string match "violation type:*" [string trim $line]]} { incr ::vic_drt }
    }
    close $fh
}
utl::metric_integer vibeic__prr__route__drc_errors $::vic_drt
set ::vic_ant [check_antennas]
utl::metric_integer vibeic__prr__after__antenna__violating_nets $::vic_ant
vic_say "reverify route_drc=$::vic_drt antenna_nets=$::vic_ant"

if {$::vic_fillers > 0} {
    if {[info exists ::env(VIBEIC_PRR_REFILL_TCL)] && $::env(VIBEIC_PRR_REFILL_TCL) ne ""} {
        source $::env(VIBEIC_PRR_REFILL_TCL)
    } else {
        set fill_list [list]
        foreach pattern [concat $::env(DECAP_CELLS) $::env(FILL_CELLS)] {
            lappend fill_list [string map {' {}} $pattern]
        }
        filler_placement $fill_list
    }
    global_connect
}

vic_annotate after
vic_census after

unset_dont_touch_objects
write_views
