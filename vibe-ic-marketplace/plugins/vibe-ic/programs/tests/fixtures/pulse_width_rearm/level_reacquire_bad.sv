module pulse_width_rearm_fixture (
  input  wire clk,
  input  wire rst_n,
  input  wire pulse_i,
  output reg  accept_o
);
  localparam integer MAX_WIDTH = 4;
  reg pulse_q;
  reg active;
  reg [3:0] width_count;

  always @(posedge clk) begin
    if (!rst_n) begin
      pulse_q <= 1'b0;
      active <= 1'b0;
      width_count <= 4'd0;
      accept_o <= 1'b0;
    end else begin
      accept_o <= 1'b0;
      pulse_q <= pulse_i;
      if (!active && pulse_i) begin
        // Deliberately level-sensitive: after timeout this re-acquires while
        // the same active pulse remains high.
        active <= 1'b1;
        width_count <= 4'd1;
      end else if (active && !pulse_i) begin
        if (width_count <= MAX_WIDTH)
          accept_o <= 1'b1;
        active <= 1'b0;
        width_count <= 4'd0;
      end else if (active && pulse_i && width_count >= MAX_WIDTH) begin
        // Wrong repair: discard only the current count, then level-reacquire.
        active <= 1'b0;
        width_count <= 4'd0;
      end else if (active && pulse_i) begin
        width_count <= width_count + 1'b1;
      end
    end
  end
endmodule
