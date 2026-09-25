read_lef /foss/pdks/sky130A/libs.ref/sky130_fd_sc_hd/techlef/sky130_fd_sc_hd__nom.tlef
read_lef /foss/pdks/sky130A/libs.ref/sky130_fd_sc_hd/lef/sky130_fd_sc_hd.lef
read_liberty /foss/pdks/sky130A/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib
read_verilog /w/cal_chain.v
link_design cal_chain
initialize_floorplan -die_area {0 0 40 40} -core_area {2 2 38 38} -site unithd
make_tracks
place_pins -hor_layers met3 -ver_layers met2
global_placement
detailed_placement
set_wire_rc -signal -layer met2
set_wire_rc -clock -layer met5
global_route
detailed_route
puts "=== PNR ANTENNA ISOLATED ECO ==="
detailed_route -nets {n1}
