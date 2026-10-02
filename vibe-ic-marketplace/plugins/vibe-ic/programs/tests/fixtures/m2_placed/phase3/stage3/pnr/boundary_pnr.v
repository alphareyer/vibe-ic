module boundary(input enable, output result);
wire raw, shifted, isolated;
plain u_src(.A(1'b0), .Y(raw));
shift_cell u_shift(.A(raw), .Y(shifted));
clamp_cell u_iso(.A(shifted), .EN(enable), .Y(isolated));
plain u_sink(.A(isolated), .Y(result));
endmodule
