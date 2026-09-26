module cal_inv (a, z);
input a; output z; wire n;
gf180mcu_fd_sc_mcu7t5v0__inv_1 u0 (.I(a), .ZN(n));
gf180mcu_fd_sc_mcu7t5v0__inv_1 u1 (.I(n), .ZN(z));
endmodule
