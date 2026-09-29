# Root-only engineering recipe; execute from exact OpenROAD src/rsz/test.
# Based on pinned repair_hold11.tcl; no private design or physical acceptance.
if {![info exists ::env(CUT20_LIMIT)]} { error "CUT20_LIMIT required (legacy, 0, 5)" }
if {[llength [info commands rsz::hold_buffer_count_limit_abi]] != 1
    || [rsz::hold_buffer_count_limit_abi] != 1} {
    error "LL_HOLD_ABSOLUTE_CAP_UNSUPPORTED: vibeic-hold-count-limit-v1 required"
}
source helpers.tcl
define_corners fast slow
read_liberty -corner slow Nangate45/Nangate45_slow.lib
read_liberty -corner fast Nangate45/Nangate45_fast.lib
read_lef Nangate45/Nangate45.lef
read_def repair_hold1.def
create_clock -period 2 clk
set_input_delay -clock clk 0 {in1 in2}
set_output_delay -clock clk -0.1 out
set_propagated_clock clk
source Nangate45/Nangate45.rc
set_wire_rc -layer metal1
estimate_parasitics -placement
report_design_area_metrics
report_cell_usage
report_worst_slack -min
report_worst_slack -max
# Actual setup execution and its actual resulting State/count, not a replay.
repair_timing -setup -max_buffer_percent 50
report_design_area_metrics
report_cell_usage
set args [list -hold -hold_margin 0.3 -max_buffer_percent 100]
if {$::env(CUT20_LIMIT) ne "legacy"} {
    lappend args -max_buffer_count $::env(CUT20_LIMIT)
}
repair_timing {*}$args
report_design_area_metrics
report_cell_usage
report_worst_slack -min
report_worst_slack -max
write_db /work/after.odb
write_def /work/after.def
