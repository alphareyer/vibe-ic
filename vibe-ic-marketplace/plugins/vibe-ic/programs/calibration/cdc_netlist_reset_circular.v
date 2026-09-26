module done_gen (input wire clk, input wire rst, output reg done);
  always @(posedge clk or posedge rst) begin
    if (rst) done <= 1'b0; else done <= 1'b1;
  end
endmodule
module reset_circular (input wire clk, input wire ext_rst, output wire ok);
  wire rst;
  wire d_done;
  assign rst = ext_rst | d_done;
  done_gen u_gen (.clk(clk), .rst(rst), .done(d_done));
  assign ok = d_done;
endmodule
