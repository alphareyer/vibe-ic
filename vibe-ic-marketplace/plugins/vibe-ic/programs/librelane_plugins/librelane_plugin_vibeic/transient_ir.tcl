# vibe-ic OpenROAD.VibeicTransientIR: the vibeic/OpenROAD fork's transient PSM
# solve on IRDropReport's basis (the step's ODB, SDC, SPEF and resistance),
# every power and ground net at its voltage.
source $::env(SCRIPTS_DIR)/openroad/common/io.tcl

read_current_odb

source $::env(SCRIPTS_DIR)/openroad/common/set_power_nets.tcl
source $::env(SCRIPTS_DIR)/openroad/common/set_rc.tcl

read_spef $::env(CURRENT_SPEF_DEFAULT_CORNER)

set vsrc_files [dict create]
if { [info exists ::env(VSRC_LOC_FILES)] } {
    set vsrc_files $::env(VSRC_LOC_FILES)
}
puts "%OL_CREATE_REPORT transient_ir.rpt"
foreach {nets voltage} [list $::env(VDD_NETS) $::env(LIB_VOLTAGE) $::env(GND_NETS) 0] {
    foreach net $nets {
        set arg_list [list -net $net -transient -period $::env(_VIBEIC_PERIOD)]
        if { [info exists ::env(VIBEIC_TRANSIENT_STEPS)] } {
            lappend arg_list -steps $::env(VIBEIC_TRANSIENT_STEPS)
        }
        if { [info exists ::env(VIBEIC_DECAP_CAP)] } {
            lappend arg_list -decap_cap $::env(VIBEIC_DECAP_CAP)
        }
        if { [dict exists $vsrc_files $net] } {
            lappend arg_list -vsrc [dict get $vsrc_files $net]
        }
        puts "=== VIBEIC_TRANSIENT_NET $net"
        set_pdnsim_net_voltage -net $net -voltage $voltage
        log_cmd analyze_power_grid {*}$arg_list
    }
}
puts "%OL_END_REPORT"
