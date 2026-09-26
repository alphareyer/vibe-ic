module formal_ctr(input clk, input rst);
  wire [7:0] q;
  ctr dut(.clk(clk), .rst(rst), .q(q));
  reg init = 1'b1;
  always @(posedge clk) init <= 1'b0;
  always @(*) if (init) assume(rst);
  always @(*) if (!init) assert(q <= 8'd9);
endmodule
