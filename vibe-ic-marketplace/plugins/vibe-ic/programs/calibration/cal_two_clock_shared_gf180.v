module cal_two_clock (clk, clk2, a, y, z);
  input clk;
  input clk2;
  input a;
  output y;
  output z;
  gf180mcu_fd_sc_mcu7t5v0__dffq_1 f0 (.CLK(clk), .D(a), .Q(y));
  gf180mcu_fd_sc_mcu7t5v0__dffq_1 f1 (.CLK(clk), .D(a), .Q(z));
endmodule
