# Evaluate measurements from one retap trial.  A retap that creates a hold
# violation is refused even when its local setup path improves.
proc vic_retap_decide {local_before local_after setup_before setup_after tns_before tns_after hold_before hold_after} {
    foreach value [list $local_before $local_after $setup_before $setup_after $tns_before $tns_after $hold_before $hold_after] {
        if {![string is double -strict $value]} { return [list REJECT MEASUREMENT_UNAVAILABLE] }
    }
    if {![expr {$local_after > $local_before + 0.0001}]} {
        return [list REJECT LOCAL_SETUP_NOT_IMPROVED]
    }
    if {![expr {$setup_after >= $setup_before - 0.0001 && $tns_after >= $tns_before - 0.01 * abs($tns_before)}]} {
        return [list REJECT DESIGN_SETUP_REGRESSED]
    }
    if {![expr {$hold_after >= 0 || ($hold_before < 0 && $hold_after >= $hold_before)}]} {
        return [list REJECT HOLD_REGRESSED]
    }
    return [list KEEP MEASURED_IMPROVEMENT]
}
