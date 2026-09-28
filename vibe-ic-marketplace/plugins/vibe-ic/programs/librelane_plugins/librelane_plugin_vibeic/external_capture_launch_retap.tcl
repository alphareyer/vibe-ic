# The owning ClockPathDriveSizing step has already read _vic_prects_insts from
# the actual pre-CTS ODB. It is the authority for which buffers CTS created.
# When a constrained register-to-output path misses setup, an earlier CTS net
# can remove launch-only clock insertion delay without changing the data path.
# Retap only through linked, pure non-inverting buffer cells that CTS created.
# Each trial measures its own path and the design-wide setup/hold before it is
# kept; a rejected trial restores the original net.
set _vic_ext_was_propagated 0
foreach _vic_ext_clk [all_clocks] {
    if {[get_property $_vic_ext_clk is_propagated]} {
        set _vic_ext_was_propagated 1
    }
}
if {!$_vic_ext_was_propagated} { set_propagated_clock [all_clocks] }

proc vic_upstream_cts_root {block inst pin_name prects} {
    set it [$inst findITerm $pin_name]
    if {$it eq "NULL" || $it eq ""} { return "" }
    set net [$it getNet]
    set root ""
    for {set depth 0} {$depth < 64} {incr depth} {
        if {$net eq "NULL" || $net eq ""} { break }
        set drivers {}
        foreach term [$net getITerms] {
            if {[[$term getMTerm] getIoType] eq "OUTPUT"} { lappend drivers $term }
        }
        if {[llength $drivers] != 1} { break }
        set driver [lindex $drivers 0]
        set cell [$driver getInst]
        if {[dict exists $prects [$cell getName]]} { break }
        set sta_cell [get_cells -quiet [$cell getName]]
        if {$sta_cell eq ""} { break }
        set lc [get_lib_cells -quiet -of_objects $sta_cell]
        if {$lc eq "" || ![get_property $lc is_buffer]} { break }
        set inputs {}
        foreach term [$cell getITerms] {
            if {[[$term getMTerm] getIoType] eq "INPUT"} { lappend inputs $term }
        }
        if {[llength $inputs] != 1} { break }
        set root $net
        set net [[lindex $inputs 0] getNet]
    }
    if {$depth == 64} { return "" }
    return $root
}

set paths [find_timing_paths -path_delay max -to [all_outputs] -group_path_count 5000 -endpoint_path_count 100]
set worst_out ""
if {[llength $paths]} { set worst_out [get_property [lindex $paths 0] slack] }
set _vic_ext_period ""
foreach _vic_ext_clk [all_clocks] {
    set p [get_property $_vic_ext_clk period]
    if {[string is double -strict $p] && $p > 0 && ($_vic_ext_period eq "" || $p < $_vic_ext_period)} {
        set _vic_ext_period $p
    }
}
# Placement RC can make the external-capture path appear met at CTS even
# though routed RCX fails it. Examine paths within 5% of the shortest active
# clock period; the per-retap setup/hold checks still decide every change.
set margin ""
if {$_vic_ext_period ne ""} { set margin [expr {0.05 * $_vic_ext_period}] }
if {![string is double -strict $worst_out] || $margin eq "" || $worst_out > $margin} {
    puts "VIC_RETAP none: output setup is outside the near-critical window ($worst_out, limit=$margin)"
} else {
    set regs [dict create]
    foreach r [all_registers] { dict set regs [get_full_name $r] 1 }
    set candidates [dict create]
    foreach p $paths {
        set slack [get_property $p slack]
        if {$slack > $margin} { continue }
        set spin [get_property $p startpoint]
        set c [get_cells -quiet -of_objects $spin]
        if {$c eq "" || ![dict exists $regs [get_full_name $c]]} { continue }
        set clocks {}
        foreach cp [get_pins -of_objects $c] {
            if {[get_property $cp is_register_clock]} { lappend clocks [get_property $cp lib_pin_name] }
        }
        if {[llength $clocks] != 1} { continue }
        set name [get_full_name $c]
        if {![dict exists $candidates $name]} {
            dict set candidates $name [list [lindex $clocks 0] [get_full_name $spin] $slack]
        }
    }
    puts "VIC_RETAP margin=$margin candidates=[dict size $candidates]"
    set block [ord::get_db_block]
    dict for {name row} $candidates {
        lassign $row pin_name spin_name old_path_slack
        set inst [$block findInst $name]
        if {$inst eq "NULL"} { continue }
        set it [$inst findITerm $pin_name]
        set oldnet [$it getNet]
        set target [vic_upstream_cts_root $block $inst $pin_name $_vic_prects_insts]
        if {$target eq "" || $target eq "NULL" || $target eq $oldnet} {
            puts "VIC_RETAP skip $name: no pure CTS buffer chain"
            continue
        }
        set w0 [sta::worst_slack -max]
        set t0 [sta::total_negative_slack -max]
        set h0 [sta::worst_slack -min]
        set q0 [find_timing_paths -path_delay max -from [get_pins $spin_name] -to [all_outputs] -group_path_count 1 -endpoint_path_count 1]
        set s0 [get_property [lindex $q0 0] slack]
        if {[catch {
            $it disconnect
            $it connect $target
            estimate_parasitics -placement
            set w1 [sta::worst_slack -max]
            set t1 [sta::total_negative_slack -max]
            set h1 [sta::worst_slack -min]
            set q1 [find_timing_paths -path_delay max -from [get_pins $spin_name] -to [all_outputs] -group_path_count 1 -endpoint_path_count 1]
            set s1 [get_property [lindex $q1 0] slack]
        } err]} {
            catch {$it disconnect}
            catch {$it connect $oldnet}
            catch {estimate_parasitics -placement}
            error "VIC_RETAP_MEASUREMENT_FAILED $name: $err"
        }
        if {$s1 > $s0 + 0.0001 && $w1 >= $w0 - 0.0001 && $t1 >= $t0 - 0.01 * abs($t0) && ($h1 >= 0 || ($h0 < 0 && $h1 >= $h0))} {
            puts "VIC_RETAP keep $name $pin_name [$oldnet getName] -> [$target getName] local=$s0->$s1 setup=$w0->$w1 tns=$t0->$t1 hold=$h0->$h1"
        } else {
            $it disconnect
            $it connect $oldnet
            estimate_parasitics -placement
            puts "VIC_RETAP reject $name local=$s0->$s1 setup=$w0->$w1 tns=$t0->$t1 hold=$h0->$h1"
        }
    }
}
if {!$_vic_ext_was_propagated} { unset_propagated_clock [all_clocks] }
