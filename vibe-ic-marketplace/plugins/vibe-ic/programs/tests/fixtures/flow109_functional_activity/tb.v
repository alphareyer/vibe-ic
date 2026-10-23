`timescale 1ns/1ps
module workload;
  reg a = 0, b = 0, c = 0;
  wire y, z;
  activity_beta dut(.a(a), .b(b), .c(c), .y(y), .z(z));
  integer i;
  initial begin
    for (i = 0; i < 8; i = i + 1) begin
      #5; {a, b, c} = i;
      #0.001;
      if (y !== ((a ^ b) & c) || z !== ~a) $fatal(1, "functional mismatch");
    end
    #5; $finish;
  end
endmodule
