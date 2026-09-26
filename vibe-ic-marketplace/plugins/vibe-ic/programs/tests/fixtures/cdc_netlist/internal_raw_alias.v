module internal_raw_alias (input wire clk, input wire rst, input wire [3:0] op,
                     output reg q);
  reg [3:0] sr;
  wire b_raw = sr[0];
  always @(posedge clk) begin
    if (rst) begin sr <= 4'd0; q <= 1'b0; end
    else begin sr <= op; q <= b_raw; end
  end
endmodule
