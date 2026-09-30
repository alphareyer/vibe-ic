module scope_pipe #(parameter W=8)(input clk,input [W-1:0] z,x,
 input [1:0] auxiliary,output reg [W-1:0] q,output status);
 always @(posedge clk) q<=z; assign status=0;
 endmodule
