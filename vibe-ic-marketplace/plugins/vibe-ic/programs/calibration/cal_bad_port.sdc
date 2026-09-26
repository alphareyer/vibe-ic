create_clock -name clk -period 10 [get_ports clk]
set_input_delay 2 -clock clk [get_ports cal_no_such_port]
set_output_delay 2 -clock clk [get_ports y]
