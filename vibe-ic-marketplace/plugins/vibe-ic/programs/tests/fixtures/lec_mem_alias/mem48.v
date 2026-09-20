// 4-word x 8-bit memory: gold `memory_map` splits it into m[0..3]; a synthesis
// netlist that keeps it as one 32-bit vector is what R-0915-97 is about.
module mem48(input clk, input we, input [1:0] a, input [7:0] d, output [7:0] q);
  reg [7:0] m [0:3];
  always @(posedge clk) if (we) m[a] <= d;
  assign q = m[a];
endmodule
