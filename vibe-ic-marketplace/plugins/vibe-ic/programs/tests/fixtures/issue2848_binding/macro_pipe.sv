`ifdef __ICARUS__
 `define PORT_BITS 8
`else
 `define PORT_BITS 4
`endif
module macro_pipe(input clk, input [`PORT_BITS-1:0] d,
 output reg [`PORT_BITS-1:0] q);
 always @(posedge clk) q <= d ^ 8'h80;
endmodule
