module cal_chain (A, Y);
  input A;
  output Y;
  wire n1;
  sky130_fd_sc_hd__inv_2 u1 (.A(A), .Y(n1));
  sky130_fd_sc_hd__inv_2 u2 (.A(n1), .Y(Y));
endmodule
