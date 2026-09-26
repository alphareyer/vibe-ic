// Calibration structure for _ppa.power::em_power_basis: a clock that enters
// through the PDK's own IO input pad, one clock buffer, two flops toggling.
module cal_em_clock_pad (PAD, q);
  input PAD;
  output q;
  wire clk_core, clk_buf, q0, q1, d0;
  gf180mcu_fd_io__in_c u_pad (.PAD(PAD), .Y(clk_core));
  gf180mcu_fd_sc_mcu7t5v0__clkbuf_16 u_cb (.I(clk_core), .Z(clk_buf));
  gf180mcu_fd_sc_mcu7t5v0__inv_1 u_inv (.I(q0), .ZN(d0));
  gf180mcu_fd_sc_mcu7t5v0__dffq_1 u_f0 (.D(d0), .CLK(clk_buf), .Q(q0));
  gf180mcu_fd_sc_mcu7t5v0__dffq_1 u_f1 (.D(q0), .CLK(clk_buf), .Q(q1));
  assign q = q1;
endmodule
