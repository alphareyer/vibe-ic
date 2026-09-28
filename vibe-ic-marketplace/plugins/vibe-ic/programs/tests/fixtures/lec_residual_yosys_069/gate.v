module top(input clk, input rst, output y);
  reg [3:0] h;
  always @(posedge clk) if (rst) h <= 4'b0001;
  else h <= {h[2:0], h[3]};
  assign y = h[3];
endmodule
