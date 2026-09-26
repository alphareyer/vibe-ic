module capture (input wire clk, input wire d, output reg q);
  always @(posedge clk) q <= d;
endmodule
module cdc_via_submodule (input wire clk_a, input wire clk_b, input wire d_in,
                          output wire q_out);
  reg flag_a;
  always @(posedge clk_a) flag_a <= d_in;
  capture u_cap (.clk(clk_b), .d(flag_a), .q(q_out));
endmodule
