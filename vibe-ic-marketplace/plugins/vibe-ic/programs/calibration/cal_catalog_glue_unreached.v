module top (input clk, output reg [3:0] a);
  always @(posedge clk) a <= a + 1'b1;
endmodule
