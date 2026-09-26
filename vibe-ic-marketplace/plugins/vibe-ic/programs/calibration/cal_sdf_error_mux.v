module cal_mux (a, b, s, z);
input a, b, s; output z;
gf180mcu_fd_sc_mcu7t5v0__mux2_2 u0 (.I0(a), .I1(b), .S(s), .Z(z));
endmodule
