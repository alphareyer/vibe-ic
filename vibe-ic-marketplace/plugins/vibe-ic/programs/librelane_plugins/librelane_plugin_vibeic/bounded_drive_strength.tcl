# Bounded drive-strength recovery for Vibeic.PostRouteRepair.
#
# This is deliberately narrower than repair_design.  It is called only after
# native design/timing repair and only when a real max-slew violation still
# exists, including residual slew on a newly inserted buffer input.  Targets come from OpenSTA's current violator report; candidates come
# from the linked Liberty views and must be physically present, functionally
# equivalent, allowed by the PNR exclusion policy, and measured to improve the
# violating net without increasing the whole-design slew count.  The caller's
# ordinary legalization, ECO route, RCX, STA, antenna and adoption checks judge
# the resulting candidate.

# net name -> number of max-slew violator rows on that net.  Every reported row
# must resolve back to the linked design; an incomplete census is an error, not
# an empty/clean result.
proc vic_slew_violating_nets {tag} {
    set report "$::env(STEP_DIR)/bounded_strength_${tag}.rpt"
    if {[catch {
        report_check_types -max_slew -violators -max_count 1000000 > $report
    } err]} {
        error "PRR_BOUNDED_STRENGTH_REPORT_FAILED: $err"
    }
    set fh [open $report]
    set rows [split [read $fh] "\n"]
    close $fh
    set out [dict create]
    foreach row $rows {
        if {![string match "*(VIOLATED)*" $row]} { continue }
        if {![regexp {^\s*(\S+).*\(VIOLATED\)\s*$} $row -> pin]} {
            error "PRR_BOUNDED_STRENGTH_REPORT_UNPARSEABLE: $row"
        }
        set objects [get_pins -quiet $pin]
        if {[llength $objects] == 0} { set objects [get_ports -quiet $pin] }
        if {[llength $objects] != 1} {
            error "PRR_BOUNDED_STRENGTH_PIN_UNRESOLVED: $pin"
        }
        set nets [get_nets -quiet -of_objects $objects]
        if {[llength $nets] != 1} {
            error "PRR_BOUNDED_STRENGTH_NET_UNRESOLVED: $pin"
        }
        set name [get_name [lindex $nets 0]]
        set net [$::block findNet $name]
        if {$net eq "NULL"} {
            error "PRR_BOUNDED_STRENGTH_DB_NET_UNRESOLVED: $pin -> $name"
        }
        dict incr out $name
    }
    return $out
}

proc vic_drive_family {name} {
    # Common PDK spellings include buf_1, BUF_X1 and BUFFD1.  The numeric
    # token is used only to keep candidates in the source cell's own family;
    # its value is never treated as a drive-strength claim.  If a library has
    # no such token, the measured equivalence set remains the fallback.
    if {[regexp {^(.*[^0-9])([0-9]+)([^0-9]*)$} $name -> prefix drive suffix]} {
        return [list $prefix $suffix]
    }
    return [list]
}

proc vic_master_area {master} {
    return [expr {wide([$master getWidth]) * wide([$master getHeight])}]
}

proc vic_pnr_excluded {name cell} {
    if {[info exists ::env(_PNR_EXCLUDED_CELLS)]
        && [lsearch -exact $::env(_PNR_EXCLUDED_CELLS) $name] >= 0} {
        return 1
    }
    if {![catch {get_property $cell dont_use} dont_use] && $dont_use} {
        return 1
    }
    return 0
}

proc vic_bounded_drive_strength {} {
    set initial [vic_slew_violating_nets before]
    set attempts 0
    set swapped 0
    set protected 0
    set source_views_missing 0

    foreach net_name [lsort -dictionary [dict keys $initial]] {
        # An earlier swap can also clean a sibling row/net; always retarget the
        # current measured residue instead of replaying the initial list.
        set current [vic_slew_violating_nets "net_${attempts}"]
        if {![dict exists $current $net_name]} { continue }
        set net [$::block findNet $net_name]
        if {$net eq "NULL" || [$net isSpecial]
            || [$net getSigType] in {POWER GROUND} || [$net isDoNotTouch]} {
            incr protected
            continue
        }
        set drivers [list]
        foreach it [$net getITerms] {
            if {[[$it getMTerm] getIoType] eq "OUTPUT"} { lappend drivers [$it getInst] }
        }
        if {[llength $drivers] != 1} {
            vic_say "bounded strength skips net=$net_name drivers=[llength $drivers]"
            continue
        }
        set inst [lindex $drivers 0]
        if {[$inst isDoNotTouch]} {
            incr protected
            continue
        }
        set old_master [$inst getMaster]
        if {[$old_master getType] in {CORE_SPACER CORE_WELLTAP CORE_ANTENNACELL}} {
            incr protected
            continue
        }
        set old_name [$old_master getName]
        set old_cells [get_lib_cells -quiet */$old_name]
        if {[llength $old_cells] == 0} {
            incr source_views_missing
            vic_say "bounded strength skips $old_name: absent from linked Liberty"
            continue
        }
        set seed [lindex $old_cells 0]
        sta::make_equiv_cells [$seed liberty_library]
        set equiv [sta::find_equiv_cells $seed]
        set family [vic_drive_family $old_name]
        set same_family [list]
        if {[llength $family]} {
            foreach cell $equiv {
                if {[vic_drive_family [$cell name]] eq $family} { lappend same_family $cell }
            }
        }
        if {[llength $same_family] > 0} { set equiv $same_family }

        set candidates [list]
        set seen [dict create]
        set old_area [vic_master_area $old_master]
        foreach cell $equiv {
            set name [$cell name]
            if {$name eq $old_name || [dict exists $seen $name]} { continue }
            dict set seen $name 1
            if {[vic_pnr_excluded $name $cell]} { continue }
            set master [[ord::get_db] findMaster $name]
            if {$master eq "NULL" || $master eq ""} { continue }
            set area [vic_master_area $master]
            if {$area < $old_area} { continue }
            # A candidate must exist in every linked view that supplied the
            # source master.  A single-corner alias cannot certify a
            # multi-corner replacement.
            if {[llength [get_lib_cells -quiet */$name]] < [llength $old_cells]} { continue }
            lappend candidates [list $area $name $master]
        }
        set candidates [lsort -integer -index 0 $candidates]
        set before_net [dict get $current $net_name]
        set before_all [sta::max_slew_violation_count]
        set before_setup [worst_slack -max]
        set before_hold [worst_slack -min]
        set best [list]
        set best_net $before_net
        set best_all $before_all
        foreach candidate $candidates {
            lassign $candidate area name master
            incr attempts
            if {[catch {$inst swapMaster $master} swap_error]} {
                vic_say "bounded strength rejects $old_name -> $name: $swap_error"
                catch {$inst swapMaster $old_master}
                continue
            }
            set measured [vic_slew_violating_nets "try_${attempts}"]
            set net_count [expr {[dict exists $measured $net_name]
                                 ? [dict get $measured $net_name] : 0}]
            set all_count [sta::max_slew_violation_count]
            set setup [worst_slack -max]
            set hold [worst_slack -min]
            set timing_ok [expr {(![string is double -strict $before_setup]
                                  || ![string is double -strict $setup]
                                  || $before_setup < 0 || $setup >= 0)
                                 && (![string is double -strict $before_hold]
                                     || ![string is double -strict $hold]
                                     || $before_hold < 0 || $hold >= 0)}]
            vic_say "bounded strength tries $net_name $old_name -> $name: net_slew=$before_net->$net_count all_slew=$before_all->$all_count setup=$before_setup->$setup hold=$before_hold->$hold"
            if {$timing_ok && $all_count <= $before_all
                && ($net_count < $best_net
                    || ($net_count == $best_net && $all_count < $best_all))} {
                set best $candidate
                set best_net $net_count
                set best_all $all_count
            }
            $inst swapMaster $old_master
            # The list is area-ordered.  The first candidate that closes this
            # net at no whole-design cost is the smallest legal measured fix.
            if {$best_net == 0} { break }
        }
        if {[llength $best]} {
            lassign $best area name master
            $inst swapMaster $master
            incr swapped
            vic_say "bounded strength keeps $net_name [$inst getName] $old_name -> $name area=$old_area->$area slew_rows=$before_net->$best_net"
        }
    }
    utl::metric_integer vibeic__prr__bounded_strength__attempts $attempts
    utl::metric_integer vibeic__prr__bounded_strength__swapped $swapped
    utl::metric_integer vibeic__prr__bounded_strength__protected $protected
    utl::metric_integer vibeic__prr__bounded_strength__source_views_missing $source_views_missing
    return $swapped
}
