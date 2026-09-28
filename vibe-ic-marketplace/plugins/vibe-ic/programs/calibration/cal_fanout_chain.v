module cal_fanout_chain (input A, output Y);
  wire n1;
  gf180mcu_fd_sc_mcu7t5v0__inv_1 u1 (.I(A), .ZN(n1));
  gf180mcu_fd_sc_mcu7t5v0__inv_1 u2 (.I(n1), .ZN(Y));
endmodule
