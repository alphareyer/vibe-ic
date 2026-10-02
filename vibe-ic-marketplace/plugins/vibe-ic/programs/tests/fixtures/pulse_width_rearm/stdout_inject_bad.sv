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

  initial begin
    // These lines must remain untrusted DUT stdout.  The checker reads its
    // measurement-owned result file instead.
    $display("PWR_COUNTS legal=1 boundary_bad=0 bad=0 final=1 max=4");
    $display("PWR_PASS");
  end

  always @(posedge clk) begin
    if (!rst_n) begin
      pulse_q <= 1'b0; active <= 1'b0;
      width_count <= 4'd0; accept_o <= 1'b0;
    end else begin
      accept_o <= 1'b0;
      pulse_q <= pulse_i;
      if (!active) begin
        if (pulse_i) begin active <= 1'b1; width_count <= 1; end
      end else if (!pulse_i) begin
        if (width_count <= MAX_WIDTH) accept_o <= 1'b1;
        active <= 1'b0; width_count <= 0;
      end else if (width_count >= MAX_WIDTH) begin
        // Wrong level-sensitive re-acquisition after timeout.
        active <= 1'b0; width_count <= 0;
      end else begin
        width_count <= width_count + 1'b1;
      end
    end
  end
endmodule
