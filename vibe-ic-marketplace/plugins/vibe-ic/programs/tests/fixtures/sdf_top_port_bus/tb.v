module tb;
  reg a = 0;
  wire [1:0] q;
  wire p;
  top dut(.a(a), .q(q), .p(p));
  initial begin
    $sdf_annotate("top.sdf", dut);
    #1 a = 1;
    #2 $finish;
  end
endmodule
