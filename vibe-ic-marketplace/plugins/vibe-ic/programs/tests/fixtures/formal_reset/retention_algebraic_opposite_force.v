module m(input clk, rst, en, clear, input [2:0] d,
         output reg [1:0] q, output reg poison);
  always @(posedge clk) begin
    if (rst) q[0] <= 1'b0; else q[0] <= d[0];
    if (!rst) q[1] <= ((d[1] + en) - en) - d[1];
  end
  always @(posedge clk) poison <= en ? 1'bx : d[2];
endmodule
