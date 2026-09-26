// Two clocks, one flag crossing through a two-flop synchroniser.
module multi_clock_sync (input wire clk_a, input wire clk_b, input wire d_in,
                         output reg q_out);
  reg flag_a, s1, s2;
  always @(posedge clk_a) flag_a <= d_in;
  always @(posedge clk_b) begin s1 <= flag_a; s2 <= s1; q_out <= s2; end
endmodule
