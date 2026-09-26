module cal_chain (clk, a, y);
  input clk;
  input a;
  output y;
  reg q0;
  always @(posedge clk) q0 <= a;
  assign y = ~q0;
endmodule
