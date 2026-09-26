module top (input clk, output [3:0] a, output [3:0] b);
  ipwrap #(.sim(0)) w (.clk(clk), .q(a));
  ipcore u2 (.clk(clk), .q(b));
endmodule
