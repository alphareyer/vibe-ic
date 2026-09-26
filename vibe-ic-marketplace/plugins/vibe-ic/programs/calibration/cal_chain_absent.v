module cal_chain (clk, a, y);
  input clk;
  input a;
  output y;
  wire q0;
  wire n0;
  gf180mcu_fd_sc_mcu7t5v0__dffq_1 f0 (.CLK(clk), .D(a), .Q(q0));
  cal_absent_master i0 (.I(q0), .ZN(n0));
  gf180mcu_fd_sc_mcu7t5v0__dffq_1 f1 (.CLK(clk), .D(n0), .Q(y));
endmodule
