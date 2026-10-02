module pulse_width_rearm_fixture (
  input  wire clk,
  input  wire rst_n,
  input  wire pulse_i,
  output reg  accept_o
);
  reg seen;
  always @(posedge clk) begin
    if (!rst_n) begin
      seen <= 1'b0;
      accept_o <= 1'b0;
    end else begin
      if (pulse_i) seen <= 1'b1;
      // Deliberate stuck/extra pulse: once observed, acceptance never clears.
      accept_o <= seen;
    end
  end
endmodule
