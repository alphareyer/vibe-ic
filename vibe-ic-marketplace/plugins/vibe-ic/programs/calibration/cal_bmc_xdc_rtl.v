// Calibration structure for lec_run::parse_bmc_log (FX_LEC_BMC review): an
// RTL don't-care. sel == 2'b11 assigns 1'bx, so synthesis may choose any
// value there; a gate that chose 1 is equivalent under Verilog x semantics.
module cal_bmc_xdc(input clk, input rst, input [1:0] sel, input a, input b,
                   input c, output reg y);
  reg yn;
  always @* case (sel)
    2'b00: yn = a;
    2'b01: yn = b;
    2'b10: yn = c;
    default: yn = 1'bx;
  endcase
  always @(posedge clk)
    if (rst) y <= 1'b0; else y <= yn;
endmodule
