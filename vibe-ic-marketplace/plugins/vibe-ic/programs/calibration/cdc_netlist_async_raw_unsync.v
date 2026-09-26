module async_raw_unsync (input wire clk, input wire rst_n, input wire btn_raw,
                         output reg led);
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) led <= 1'b0; else if (btn_raw) led <= ~led;
  end
endmodule
