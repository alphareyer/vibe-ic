module spare(input x, output y); assign y=x; endmodule
module packed_pipe #(parameter integer BASE=3, parameter integer W=BASE<<1)(
 output reg signed [1:0][0:W-1] q,
 input wire clock, reset_n, reset,
 input wire signed [1:0][0:W-1] d);
 always @(posedge clock or negedge reset_n or posedge reset)
  if (!reset_n || reset) q <= '0;
  else q <= d;
endmodule
