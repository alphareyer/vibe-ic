module ipcore #(parameter [0:0] sim = 1'b0, parameter W = 4) (input clk, output reg [W-1:0] q);
  always @(posedge clk) q <= q + 1'b1;
endmodule
module ipwrap #(parameter sim = 0) (input clk, output [3:0] q);
  ipcore #(.sim(sim)) u (.clk(clk), .q(q));
endmodule
