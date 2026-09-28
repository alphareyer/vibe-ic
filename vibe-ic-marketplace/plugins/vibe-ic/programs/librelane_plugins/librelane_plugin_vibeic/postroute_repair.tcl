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

# ---- route census ------------------------------------------------------------
# A signal net that needs a wire: two or more terminals, not joined by
# abutment (a pad's PAD pin on its own port). `check_antennas` SKIPS a net
# with no wire (ANT-0018), and the ECO route's report covers only the nets it
# was given, so neither can see a net some other command left unrouted.
proc vic_needs_wire {net} {
    if {[$net isSpecial] || [$net getSigType] in {POWER GROUND}} { return 0 }
    if {[llength [$net getITerms]] + [llength [$net getBTerms]] < 2} { return 0 }
    return [expr {![$net isConnectedByAbutment]}]
}
# name -> 1 for every signal net that needs a wire and has none.
proc vic_unrouted_nets {} {
    set out [dict create]
    foreach net [$::block getNets] {
        if {[vic_needs_wire $net] && [$net getWire] eq "NULL"} {
            dict set out [$net getName] 1
        }
    }
    return $out
}
# name -> 1 for every signal net that carries a wire now.
proc vic_routed_nets {} {
    set out [dict create]
    foreach net [$::block getNets] {
        if {[vic_needs_wire $net] && [$net getWire] ne "NULL"} {
            dict set out [$net getName] 1
        }
    }
    return $out
}
# name -> net for every net routed in `before` that has no wire now: what
# the commands in between took and must be handed back.
proc vic_lost_routes {before} {
    set lost [dict create]
    dict for {name _} $before {
        set net [$::block findNet $name]
        if {$net ne "NULL" && [$net getWire] eq "NULL"} { dict set lost $name $net }
    }
    return $lost
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
set ::vic_unrouted_before [vic_unrouted_nets]
set ::vic_routed_before [vic_routed_nets]
utl::metric_integer vibeic__prr__before__unrouted__count [dict size $::vic_unrouted_before]
if {[info exists ::env(VIBEIC_PRR_CENSUS_ONLY)] && $::env(VIBEIC_PRR_CENSUS_ONLY)} {
    utl::metric_integer vibeic__prr__changed 0
    vic_say "census only: nothing repaired; the input database is the result"
    exit 0
}

# ---- the fanout limit, counted on the database ----------------------------
# name -> load count for every driven signal net with more loads than the
# cap sign-off judges (VIBEIC_PRR_MAX_FANOUT: the strictest `set_max_fanout`
# of the SDC this step reads, which repair_design meets -- not LibreLane's
# MAX_FANOUT_CONSTRAINT, a PDK default when L19 declares none). A load is what STA counts: every input pin on the net and every
# top-level output port. Counted here rather than by
# sta::max_fanout_violation_count, which takes Signal 11 in a multi-corner
# session (see vic_census). Without a declared cap the dict is empty and
# the caller says the limit was not declared.
proc vic_fanout_declared {} {
    return [expr {[info exists ::env(VIBEIC_PRR_MAX_FANOUT)]
                  && [string is double -strict $::env(VIBEIC_PRR_MAX_FANOUT)]}]
}
proc vic_fanout_over {} {
    set out [dict create]
    if {![vic_fanout_declared]} { return $out }
    set cap $::env(VIBEIC_PRR_MAX_FANOUT)
    foreach net [$::block getNets] {
        if {[$net isSpecial] || [$net getSigType] in {POWER GROUND}} { continue }
        set loads 0
        set drivers 0
        foreach it [$net getITerms] {
            set io [[$it getMTerm] getIoType]
            if {$io eq "OUTPUT"} { incr drivers } elseif {$io in {INPUT INOUT}} { incr loads }
        }
        foreach bt [$net getBTerms] {
            set io [$bt getIoType]
            if {$io eq "INPUT"} { incr drivers } elseif {$io in {OUTPUT INOUT}} { incr loads }
        }
        if {$drivers && $loads > $cap} { dict set out [$net getName] $loads }
    }
    return $out
}
# Nets newly over the cap, or already over it but given still more loads.
# The latter also worsens the signoff violation and must refuse the candidate.
proc vic_fanout_added {before now} {
    set added [dict create]
    dict for {name loads} $now {
        if {![dict exists $before $name] || $loads > [dict get $before $name]} {
            dict set added $name $loads
        }
    }
    return $added
}

# ---- 4. repair ---------------------------------------------------------------
set rd_args [list -verbose]
append_if_exists_argument rd_args VIBEIC_PRR_MAX_WIRE_LENGTH -max_wire_length
append_if_exists_argument rd_args VIBEIC_PRR_SLEW_MARGIN_PCT -slew_margin
append_if_exists_argument rd_args VIBEIC_PRR_CAP_MARGIN_PCT -cap_margin
if {$::env(VIBEIC_PRR_SETUP_SEQUENCE) eq "sizeup,swap"} {
    # This candidate must stay sizing-only on the routed design. The DRV
    # controller can run repair_design in its own candidate when needed.
    vic_say "setup sequence sizeup,swap: design buffering deferred to DRV controller"
} else {
    log_cmd repair_design {*}$rd_args
}

set setup_args [list -setup -verbose]
lappend setup_args -setup_margin $::env(VIBEIC_PRR_SETUP_MARGIN)
lappend setup_args -max_buffer_percent $::env(VIBEIC_PRR_SETUP_MAX_BUFFER_PCT)
if {$::env(VIBEIC_PRR_SETUP_SEQUENCE) ne "default"} {
    lappend setup_args -sequence $::env(VIBEIC_PRR_SETUP_SEQUENCE)
}
append_if_exists_argument setup_args VIBEIC_PRR_SETUP_REPAIR_TNS_PCT -repair_tns
log_cmd repair_timing {*}$setup_args

# Hold after setup (review70 step 32: T60 moved spm SS hold -0.39 -> +0.039
# only once hold ran after setup), and never at setup's expense.
set hold_args [list -hold -verbose]
lappend hold_args -setup_margin $::env(VIBEIC_PRR_SETUP_MARGIN)
lappend hold_args -hold_margin $::env(VIBEIC_PRR_HOLD_MARGIN)
lappend hold_args -max_buffer_percent $::env(VIBEIC_PRR_HOLD_MAX_BUFFER_PCT)
log_cmd repair_timing {*}$hold_args
# What repair_design left: every later edit in this step must keep it.
set ::vic_fo_repaired [vic_fanout_over]

# The timing repair may create new fanout/slew/cap loads. Re-run DRV repair
# after the last repair_timing call; the routed re-check below remains the
# authority for whether the declared limit was actually met.
log_cmd repair_design {*}$rd_args

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

# The scoped router can return success when its post-route verifier finds
# violations that its routing loop did not see (DRT-0701/0711). Its exit code
# alone is therefore not a clean-route verdict.
proc vic_drc_count {drc} {
    if {![file exists $drc]} { return -1 }
    set fh [open $drc]
    set count 0
    foreach line [split [read $fh] "\n"] {
        if {[string match "violation type:*" [string trim $line]]} { incr count }
    }
    close $fh
    return $count
}

# The fork's scoped route (`detailed_route -nets`) refuses a result with more
# whole-design violations than it was given (DRT-0712). A refusal names the
# violations; the nets that share one with a routed net join the set and the
# set is routed again, at most VIBEIC_PRR_ECO_EXPANSIONS times. Every attempt
# is measured by the same guard against ITS entry, and each entry is taken
# after the set's wires are gone, so an attempt can only return a route no
# worse than the one it was given. Returns 1 when a route was accepted.
proc vic_eco_route {varname tag} {
    upvar #0 $varname dirty
    if {[dict size $dirty] == 0} { return 1 }
    # Resizer and global_route may remove wires outside the nominal ECO set.
    # The step's input route and wires newly present after repair are both
    # obligations for every scoped retry.
    set expected [dict merge $::vic_routed_before [vic_routed_nets]]
    set_thread_count $::env(DRT_THREADS)
    set limit [expr {[info exists ::env(VIBEIC_PRR_ECO_EXPANSIONS)]
                     ? $::env(VIBEIC_PRR_ECO_EXPANSIONS) : 2}]
    set attempt 0
    while {1} {
        dict for {name net} $dirty {
            set wire [$net getWire]
            if {$wire ne "NULL"} { odb::dbWire_destroy $wire }
            $net setWireOrdered 0
        }
        source $::env(SCRIPTS_DIR)/openroad/common/grt.tcl
        set grt_lost [vic_lost_routes $expected]
        set added 0
        dict for {name net} $grt_lost {
            if {![dict exists $dirty $name]} {
                dict set dirty $name $net
                incr added
            }
        }
        if {$added} {
            vic_say "$tag global_route took $added routed net(s) outside the ECO set; adding them to -nets"
        }
        set drc $::env(STEP_DIR)/${tag}.$attempt.drc
        set drt_args [list -droute_end_iter $::env(DRT_OPT_ITERS) -or_seed 42 -verbose 1 \
                          -output_drc $drc -nets [dict keys $dirty]]
        incr attempt
        incr ::vic_eco_attempts
        if {![catch {log_cmd detailed_route {*}$drt_args} err]} {
            set lost [vic_lost_routes $expected]
            dict for {name net} $dirty {
                if {[vic_needs_wire $net] && [$net getWire] eq "NULL"} {
                    dict set lost $name $net
                }
            }
            if {[dict size $lost]} {
                vic_say "$tag scoped route left [dict size $lost] required net(s) without wire: [dict keys $lost]"
                if {$attempt > $limit} { return 0 }
                set dirty [dict merge $dirty $lost]
                continue
            }
            set violations [vic_drc_count $drc]
            if {$violations != 0} {
                vic_say "$tag scoped route verified $violations DRC violation(s); expanding or refusing"
                set more [vic_violation_neighbours $drc $dirty]
                if {$attempt > $limit || [dict size $more] == 0} { return 0 }
                vic_say "$tag expands by [dict size $more] DRC neighbour net(s): [dict keys $more]"
                set dirty [dict merge $dirty $more]
                continue
            }
            file copy -force $drc $::env(STEP_DIR)/eco_route.drc
            return 1
        }
        vic_say "$tag attempt $attempt refused: $err"
        set more [vic_violation_neighbours $drc $dirty]
        if {$attempt > $limit || [dict size $more] == 0} { return 0 }
        vic_say "$tag expands by [dict size $more] neighbour net(s): [dict keys $more]"
        set dirty [dict merge $dirty $more]
    }
}

set ::vic_eco_attempts 0
set ::vic_eco_ok [vic_eco_route ::vic_dirty eco_route]
utl::metric_integer vibeic__prr__eco_attempt__count $::vic_eco_attempts
utl::metric_integer vibeic__prr__eco_net__final_count [dict size $::vic_dirty]
if {!$::vic_eco_ok} {
    puts stderr "VIBEIC_PRR_ECO_ROUTE_REFUSED: the scoped route added whole-design violations on every attempt ($::vic_eco_attempts); the candidate is not written"
    exit 1
}

# ---- 6b. antenna residue -> the tool's antenna repair ----------------------
# review70 step 32: route each residual to the instrument that repairs it.
# MEASURED on the spm LL15..21 chain (T102 r2): the DRV repair's ECO route
# took antenna-violating nets 0 -> 1, and the candidate was refused for it.
# A candidate that ADDED antenna violations gets OpenROAD's own
# `repair_antennas` (the PDK's DIODE_CELL, as LibreLane's drt.tcl runs it);
# only the new diodes are legalized, and only their nets are routed again,
# by the same scoped, guarded route. The judge downstream still counts.
set ::vic_ant_eco [check_antennas]
set ::vic_diodes [list]
set ::vic_ant_ripped [dict create]
if {$::vic_ant_eco > $::vic_ant_before && [info exists ::env(DIODE_CELL)]
    && $::env(VIBEIC_PRR_ANTENNA_REPAIR)} {
    set names [dict create]
    foreach inst [$::block getInsts] { dict set names [$inst getName] 1 }
    # MEASURED on spm x gf180mcuD (0.3.83, replay of 32-cand01): after
    # `repair_antennas` FOUR nets had no wire -- the two it put diodes on and
    # reported (`grt::repaired_net_names`: net48 net65), plus net47 and
    # _vibeic_aux_tie_0040, both routed a moment before. Routing only the
    # reported set shipped those two unrouted; so the set routed again is
    # every net this call took, by census, not by what the tool says it did.
    set routed [vic_routed_nets]
    set ant_args [list [lindex [split $::env(DIODE_CELL) "/"] 0]]
    append_if_exists_argument ant_args DRT_ANTENNA_REPAIR_MARGIN -ratio_margin
    if {[catch {log_cmd repair_antennas {*}$ant_args} err]} {
        vic_say "antenna repair refused: $err"
    }
    foreach inst [$::block getInsts] {
        if {![dict exists $names [$inst getName]]} { lappend ::vic_diodes $inst }
    }
    if {[llength $::vic_diodes]} {
        set mine [dict create]
        foreach inst $::vic_diodes { dict set mine [$inst getName] 1 }
        set locked [list]
        foreach inst [$::block getInsts] {
            if {[dict exists $mine [$inst getName]]} { continue }
            set status [$inst getPlacementStatus]
            if {$status ni {LOCKED FIRM COVER}} {
                lappend locked [list $inst $status]
                $inst setPlacementStatus LOCKED
            }
        }
        log_cmd detailed_placement \
            -max_displacement [subst { $::env(PL_MAX_DISPLACEMENT_X) $::env(PL_MAX_DISPLACEMENT_Y) }]
        foreach pair $locked { [lindex $pair 0] setPlacementStatus [lindex $pair 1] }
        check_placement -verbose
        global_connect
    }
    set ::vic_ant_dirty [dict create]
    foreach inst $::vic_diodes {
        lappend ::vic_created $inst
        foreach it [$inst getITerms] {
            set net [$it getNet]
            if {$net ne "NULL" && [$net getSigType] ni {POWER GROUND}} {
                dict set ::vic_ant_dirty [$net getName] $net
            }
        }
    }
    set reported [expr {[llength [info commands grt::repaired_net_names]]
                        ? [grt::repaired_net_names] : [list]}]
    foreach name $reported {
        set net [$::block findNet $name]
        if {$net ne "NULL"} { dict set ::vic_ant_dirty $name $net }
    }
    dict for {name net} [vic_lost_routes $routed] {
        if {![dict exists $::vic_ant_dirty $name]} {
            dict set ::vic_ant_ripped $name $net
            dict set ::vic_ant_dirty $name $net
        }
    }
    if {[dict size $::vic_ant_ripped]} {
        vic_say "antenna repair took the wire of [dict size $::vic_ant_ripped] net(s) it did not report: [dict keys $::vic_ant_ripped]"
    }
    if {![vic_eco_route ::vic_ant_dirty antenna_route]} {
        puts stderr "VIBEIC_PRR_ECO_ROUTE_REFUSED: the antenna repair's scoped route added whole-design violations"
        exit 1
    }
}
utl::metric_integer vibeic__prr__antenna__ripped_unreported [dict size $::vic_ant_ripped]
utl::metric_integer vibeic__prr__antenna__after_eco $::vic_ant_eco
utl::metric_integer vibeic__prr__antenna__diodes [llength $::vic_diodes]
vic_say "antenna after eco=$::vic_ant_eco diodes=[llength $::vic_diodes]"

# ---- 6c. the limits repair_design set, kept ----------------------------------
# cmp3 D14, MEASURED on spm x gf180mcuD (32-cand01): repair_design split
# u_core/wire51 to the declared 4 loads, then repair_antennas put diode
# ANTENNA_5 on that net as its 5th load -- a diode's pin is a load -- and the
# candidate shipped max_fanout 5 > 4. A net the antenna phase pushed over the
# cap gets a bounded repair_design round here (its buffers legalized alone,
# their nets routed by the same scoped, guarded ECO route); re-verify below
# refuses whatever is still over.
set ::vic_fo_rounds 0
# (the declared-cap test is spelled inline: this section is also exercised
# on its own, with none of the procs above it)
set ::vic_fo_declared [expr {[info exists ::env(VIBEIC_PRR_MAX_FANOUT)]
    && [string is double -strict $::env(VIBEIC_PRR_MAX_FANOUT)]}]
set ::vic_fo_added [expr {$::vic_fo_declared
    ? [vic_fanout_added $::vic_fo_repaired [vic_fanout_over]] : [dict create]}]
while {[dict size $::vic_fo_added] && $::vic_fo_rounds < 2} {
    incr ::vic_fo_rounds
    vic_say "round $::vic_fo_rounds: [dict size $::vic_fo_added] net(s) pushed over max_fanout $::env(VIBEIC_PRR_MAX_FANOUT) after repair_design: $::vic_fo_added"
    set names [dict create]
    foreach inst [$::block getInsts] { dict set names [$inst getName] [[$inst getMaster] getName] }
    log_cmd repair_design {*}$rd_args
    set mine [dict create]
    foreach inst [$::block getInsts] {
        set name [$inst getName]
        if {![dict exists $names $name]
            || [dict get $names $name] ne [[$inst getMaster] getName]} {
            dict set mine $name $inst
        }
    }
    if {[dict size $mine] == 0} { break }
    set locked [list]
    foreach inst [$::block getInsts] {
        if {[dict exists $mine [$inst getName]]} { continue }
        set status [$inst getPlacementStatus]
        if {$status ni {LOCKED FIRM COVER}} {
            lappend locked [list $inst $status]
            $inst setPlacementStatus LOCKED
        }
    }
    log_cmd detailed_placement \
        -max_displacement [subst { $::env(PL_MAX_DISPLACEMENT_X) $::env(PL_MAX_DISPLACEMENT_Y) }]
    foreach pair $locked { [lindex $pair 0] setPlacementStatus [lindex $pair 1] }
    check_placement -verbose
    global_connect
    set ::vic_fo_dirty [dict create]
    dict for {name inst} $mine {
        lappend ::vic_created $inst
        foreach it [$inst getITerms] {
            set net [$it getNet]
            if {$net ne "NULL" && [$net getSigType] ni {POWER GROUND}} {
                dict set ::vic_fo_dirty [$net getName] $net
            }
        }
    }
    if {![vic_eco_route ::vic_fo_dirty drv_route]} {
        puts stderr "VIBEIC_PRR_ECO_ROUTE_REFUSED: the fanout repair's scoped route added whole-design violations"
        exit 1
    }
    set ::vic_fo_added [vic_fanout_added $::vic_fo_repaired [vic_fanout_over]]
}
utl::metric_integer vibeic__prr__fanout__rounds $::vic_fo_rounds

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
set ::vic_unrouted_after [vic_unrouted_nets]
set ::vic_unrouted_new [list]
dict for {name _} $::vic_unrouted_after {
    if {![dict exists $::vic_unrouted_before $name]} { lappend ::vic_unrouted_new $name }
}
utl::metric_integer vibeic__prr__after__unrouted__count [dict size $::vic_unrouted_after]
utl::metric_integer vibeic__prr__unrouted__added [llength $::vic_unrouted_new]
vic_say "reverify route_drc=$::vic_drt antenna_nets=$::vic_ant unrouted_added=[llength $::vic_unrouted_new]"
# A candidate that hands on a net its input had routed and it did not route
# again is not a route; neither counter above can see one (ANT-0018 skips it,
# the ECO report covers only the nets it was given).
if {[llength $::vic_unrouted_new]} {
    puts stderr "VIBEIC_PRR_LOST_ROUTE_REFUSED: [llength $::vic_unrouted_new] signal net(s) the input had routed carry no wire after the repair: [lrange $::vic_unrouted_new 0 19]; the candidate is not written"
    exit 1
}

if {!$::vic_fo_declared} {
    # No declared cap: nothing to keep, and nothing is claimed kept.
    utl::metric_integer vibeic__prr__fanout__added -1
    vic_say "fanout limit: the SDC declares no set_max_fanout; not measured"
    set ::vic_fo_final [dict create]
} else {
    set ::vic_fo_final [vic_fanout_added $::vic_fo_repaired [vic_fanout_over]]
    utl::metric_integer vibeic__prr__fanout__added [dict size $::vic_fo_final]
}
if {[dict size $::vic_fo_final]} {
    puts stderr "LL_PRR_FANOUT_LIMIT_BROKEN: [dict size $::vic_fo_final] net(s) over max_fanout $::env(VIBEIC_PRR_MAX_FANOUT) that became newly over or worsened after repair_design: $::vic_fo_final; the candidate is not written"
    exit 1
}

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
# The consumer runs RCX + STAPostPNR on both changed and no-op candidates.
# Its fresh per-corner OpenROAD processes count max-fanout violations and
# refuse any residual (or disclose NOT_MEASURED).  Do not call the counter in
# this multi-corner repair session: CheckFanouts::check has signalled 11 here.
vic_census after

unset_dont_touch_objects
write_views
