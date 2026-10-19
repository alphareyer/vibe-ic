module frame_top(input logic clk_i,rst_n,
 input logic[7:0] data_i,output logic[7:0] out_o);
 import a_words::*;
 always_ff @(posedge clk_i or negedge rst_n)
  if(!rst_n) out_o<=0; else out_o<=data_i^WordMask;
endmodule
