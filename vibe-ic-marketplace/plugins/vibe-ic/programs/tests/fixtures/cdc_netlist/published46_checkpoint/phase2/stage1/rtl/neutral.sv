`include "constants.svh"
module neutral(input clk,rst_n,d,output reg q);
wire bit_true=`FIXED;
always @(posedge clk or negedge rst_n) if(!rst_n)q<=!bit_true;else q<=d;
endmodule
