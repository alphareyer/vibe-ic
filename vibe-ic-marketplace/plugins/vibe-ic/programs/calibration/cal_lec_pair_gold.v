module cal_pair(input clk, input rst, output reg x, output reg y);
  always @(posedge clk) if (rst) begin x <= 1'b0; y <= 1'b0; end
                        else begin x <= ~x; y <= ~y; end
endmodule
