module pulse_width_rearm_fixture (
  input  wire clk,
  input  wire rst_n,
  input  wire pulse_i,
  output reg  accept_o
);
  localparam integer MAX_WIDTH = 4;
  reg pulse_q;
  reg active;
  reg timed_out;
  reg [3:0] width_count;

  always @(posedge clk) begin
    if (!rst_n) begin
      pulse_q <= 1'b0; active <= 1'b0; timed_out <= 1'b0;
      width_count <= 4'd0; accept_o <= 1'b0;
    end else begin
      accept_o <= 1'b0;
      pulse_q <= pulse_i;
      if (timed_out) begin
        if (!pulse_i) timed_out <= 1'b0;
      end else if (!active) begin
        if (pulse_i && !pulse_q) begin active <= 1'b1; width_count <= 1; end
      end else if (!pulse_i) begin
        // Deliberate off-by-one: accepts max+1 instead of max.
        if (width_count <= MAX_WIDTH + 1) accept_o <= 1'b1;
        active <= 1'b0; width_count <= 0;
      end else if (width_count >= MAX_WIDTH + 1) begin
        active <= 1'b0; width_count <= 0; timed_out <= 1'b1;
      end else begin
        width_count <= width_count + 1'b1;
      end
    end
  end
endmodule
