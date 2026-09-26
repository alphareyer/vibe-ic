module cal_chain (clk, a, y);
  input clk;
  input a;
  output reg y;
  reg q0;
  always @(posedge clk) begin
    q0 <= a;
    y <= ~q0;
  end
endmodule
