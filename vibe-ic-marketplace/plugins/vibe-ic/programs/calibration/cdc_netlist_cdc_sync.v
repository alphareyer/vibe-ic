module cdc_sync (input wire clk_a, input wire clk_b, input wire rst,
                 input wire d_in, output wire q_out);
  reg flag_a;
  reg sync1_b, sync2_b;
  always @(posedge clk_a) begin
    if (rst) flag_a <= 1'b0; else flag_a <= d_in;
  end
  always @(posedge clk_b) begin
    if (rst) begin sync1_b <= 1'b0; sync2_b <= 1'b0; end
    else begin sync1_b <= flag_a; sync2_b <= sync1_b; end
  end
  assign q_out = sync2_b;
endmodule
