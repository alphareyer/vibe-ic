module pulse_width_rearm_fixture (
  input wire clk,
  input wire rst_n,
  input wire pulse_i,
  output reg accept_o
);
  // Deliberate invalid negative arm: it never produces the required
  // level-sensitive false acceptance, so a label alone must not validate it.
  always @(posedge clk) begin
    if (!rst_n) accept_o <= 1'b0;
    else accept_o <= 1'b0;
  end
endmodule
