module integer_pipe #(
 parameter integer TOTAL=65536*65536+1,
 parameter integer W=$clog2(TOTAL)+8
 )(input clk,input [W-1:0] d, output reg [W-1:0] q);
 always @(posedge clk) q<=d;
 endmodule
