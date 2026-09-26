create_clock -name clk -period 10 [get_ports clk]
set_input_delay 2 -clock clk [get_ports a]
set_output_delay 2 -clock clk [get_ports y]
