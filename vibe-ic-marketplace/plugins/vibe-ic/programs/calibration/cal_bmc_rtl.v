// Calibration structure for lec_run::parse_bmc_log (FX_LEC_BMC_CEX): a
// counter with a synchronous active-high reset and a registered compare.
module cal_bmc(input clk, input rst, input en, output reg [3:0] q, output reg hit);
  always @(posedge clk)
    if (rst) begin q <= 4'd0; hit <= 1'b0; end
    else if (en) begin q <= q + 4'd1; hit <= (q == 4'd4); end
endmodule
