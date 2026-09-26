module async_raw_sync (input wire clk, input wire rst_n, input wire btn_raw,
                       output reg led);
  reg s1, s2;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin s1 <= 1'b0; s2 <= 1'b0; led <= 1'b0; end
    else begin s1 <= btn_raw; s2 <= s1; if (s2) led <= ~led; end
  end
endmodule
