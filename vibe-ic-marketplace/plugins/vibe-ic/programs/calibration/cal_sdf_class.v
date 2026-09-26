module cal_sdf_class (a, b, s, oe, ie, p);
input a, b, s, oe, ie;
output p;
wire m, y;
gf180mcu_fd_sc_mcu7t5v0__mux2_2 u0 (.I0(a), .I1(b), .S(s), .Z(m));
gf180mcu_fd_io__bi_24t u_pad (.A(m), .CS(ie), .SL(ie), .IE(ie), .OE(oe), .PU(ie), .PD(ie), .PAD(p), .Y(y));
endmodule
