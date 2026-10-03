module mixed_probe(A, VGND, VNB, VPB, VPWR, Y);
 input A,VGND,VNB,VPB,VPWR; output Y;
 sense_receiver u_receiver (.A(A),.VGND(VGND),.VNB(VNB),.VPB(VPB),.VPWR(VPWR),.Y(Y));
endmodule
