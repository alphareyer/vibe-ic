module ctr(input clk, input rst, output reg [7:0] q);
  always @(posedge clk) if (rst || q == 8'd11) q <= 8'd0; else q <= q + 8'd1;
endmodule
