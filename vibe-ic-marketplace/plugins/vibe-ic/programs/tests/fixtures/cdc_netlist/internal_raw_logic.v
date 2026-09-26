// An INTERNAL wire whose name ends in _raw, driven by this module's own flops
// (#2063). It is not a port and has no clock domain to cross.
module internal_raw_logic (input wire clk, input wire rst, input wire [3:0] op,
                           output reg q);
  reg [3:0] sr;
  wire b_raw = sr[0] ^ sr[1];
  always @(posedge clk) begin
    if (rst) begin sr <= 4'd0; q <= 1'b0; end
    else begin sr <= op; q <= b_raw; end
  end
endmodule
