// Calibration structure for lec_run::parse_bmc_log (FX_LEC_BMC review 2): a
// 4-state FSM whose reset arrives through a 2-flop synchroniser, so no
// register is in a defined state until cycle 3; synthesis re-encodes the FSM.
module cal_bmc_rsync(input clk, input rst, input go, output ready, output busy);
  reg rst_s1, rst_s2;
  always @(posedge clk) begin rst_s1 <= rst; rst_s2 <= rst_s1; end
  localparam IDLE = 2'd0, RUN = 2'd1, DONE = 2'd2, WAIT = 2'd3;
  reg [1:0] st;
  always @(posedge clk)
    if (rst_s2) st <= IDLE;
    else case (st)
      IDLE: if (go) st <= RUN;
      RUN: st <= DONE;
      DONE: st <= WAIT;
      default: st <= IDLE;
    endcase
  assign ready = st == IDLE;
  assign busy = st == RUN;
endmodule
