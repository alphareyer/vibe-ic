module top(input clk, input rst, output y);
  reg [1:0] s;
  always @(posedge clk) if (rst) s <= 2'b00; else s <= s + 2'b01;
  assign y = (s == 2'b11);
endmodule
