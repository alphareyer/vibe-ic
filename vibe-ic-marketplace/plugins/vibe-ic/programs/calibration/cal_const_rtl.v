module cal_const (clk, a, y, hi, lo);
  input clk;
  input a;
  output reg y;
  output hi;
  output lo;
  always @(posedge clk) y <= ~a;
  assign hi = 1'b1;
  assign lo = 1'b0;
endmodule
