module bridge_top(input clk, input reset_n, input sense,
                  output reg enable, output reg accepted);
  always @(posedge clk) begin
    if (!reset_n) begin enable <= 0; accepted <= 0; end
    else begin enable <= 1; accepted <= sense; end
  end
endmodule
