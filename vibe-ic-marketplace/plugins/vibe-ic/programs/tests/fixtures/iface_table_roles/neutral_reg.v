module neutral_reg(input wire clk,input wire reset_n,input wire cs,input wire we,input wire [7:0] address,input wire [31:0] write_data,output wire [31:0] read_data,output wire error);
assign read_data=32'd0;
assign error=1'b0;
endmodule
