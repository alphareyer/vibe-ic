// TF24 calibration structure: one PDK buffer between two ports.
module cal_pg (input a, output y);
  gf180mcu_fd_sc_mcu7t5v0__buf_1 u0 (.I(a), .Z(y));
endmodule
