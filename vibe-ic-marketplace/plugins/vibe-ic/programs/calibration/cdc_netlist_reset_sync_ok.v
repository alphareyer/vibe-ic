module reset_sync_ok (input wire clk, input wire ext_rst_n, input wire d,
                      output reg q);
  reg r1, r2;
  always @(posedge clk or negedge ext_rst_n) begin
    if (!ext_rst_n) begin r1 <= 1'b0; r2 <= 1'b0; end
    else begin r1 <= 1'b1; r2 <= r1; end
  end
  wire rst_n = r2;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) q <= 1'b0; else q <= d;
  end
endmodule
