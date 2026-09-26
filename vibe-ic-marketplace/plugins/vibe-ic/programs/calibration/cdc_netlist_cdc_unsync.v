module cdc_unsync (input wire clk_a, input wire clk_b, input wire rst,
                   input wire d_in, output wire q_out);
  reg flag_a;
  reg seen_b;
  always @(posedge clk_a) begin
    if (rst) flag_a <= 1'b0; else flag_a <= d_in;
  end
  always @(posedge clk_b) begin
    if (rst) seen_b <= 1'b0; else seen_b <= flag_a;
  end
  assign q_out = seen_b;
endmodule
