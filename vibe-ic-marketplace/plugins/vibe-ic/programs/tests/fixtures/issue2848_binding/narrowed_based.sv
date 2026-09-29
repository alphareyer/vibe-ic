module narrowed_pipe #(parameter logic [3:0] W=8'd24)
 (input clk,input [W-1:0] d,output reg [W-1:0] q);
 always @(posedge clk) q<=d;
 endmodule
