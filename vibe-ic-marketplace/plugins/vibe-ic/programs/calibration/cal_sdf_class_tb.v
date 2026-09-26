`timescale 1ns/1ps
module tb;
reg a, b, s, oe, ie; wire p;
cal_sdf_class dut (.a(a), .b(b), .s(s), .oe(oe), .ie(ie), .p(p));
initial begin $sdf_annotate("cal_sdf_class.sdf", dut); a=0; b=1; s=0; oe=1; ie=0; #5 s=1; #5 $finish; end
endmodule
