# OpenSTA calibration for the Step-32 timing basis reader. CAL_PERIOD_NS is
# supplied by the invoker; the same synthetic chain and Liberty are used in
# both arms. The reports are the tool's unedited stdout after the CLI preface.
set lib [lindex [glob /foss/pdks/ciel/gf180mcu/versions/*/gf180mcuD/libs.ref/gf180mcu_fd_sc_mcu7t5v0/lib/gf180mcu_fd_sc_mcu7t5v0__tt_025C_5v00.lib] 0]
read_liberty $lib
read_verilog /work/cal_chain_gf180.v
link_design cal_chain
create_clock -name clk -period $::env(CAL_PERIOD_NS) [get_ports clk]
set_input_delay 0 -clock clk [get_ports a]
set_output_delay 0 -clock clk [get_ports y]
report_checks -path_delay max -group_path_count 1
report_tns
report_wns
report_worst_slack
