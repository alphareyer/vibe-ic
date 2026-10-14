// SPDX-License-Identifier: Apache-2.0
// Independent neutral metadata interface fixture, not a benchmark DUT.
module neutral_endpoint(input wire i_clk, input wire i_reset,
 output wire [7:0] o_store_read_addr, output wire [7:0] o_store_write_addr,
 output wire o_flag);
assign o_store_read_addr=8'h13; assign o_store_write_addr=8'h27;
assign o_flag=i_clk & ~i_reset;
endmodule
