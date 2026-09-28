// Calibration structure for lec_run::parse_bmc_log (FX_LEC_BMC review 3): the
// reset reaches the FSM one cycle late (a registered reset), and `abort`
// fixes part of the state before it arrives. Synthesis re-encodes the FSM
// one-hot, so an all-zero gate power-up is a state no gold state matches.
module cal_bmc_pdx(input clk, input rst, input go, input abort, output notrun, output done);
  reg rst_q;
  always @(posedge clk) rst_q <= rst;
  localparam IDLE = 2'd0, RUN = 2'd1, DONE = 2'd2, WAIT = 2'd3;
  reg [1:0] st;
  always @(posedge clk)
    if (rst_q) st <= IDLE;
    else if (abort) st <= (st == RUN) ? DONE : IDLE;
    else case (st)
      IDLE: if (go) st <= RUN;
      RUN: st <= DONE;
      DONE: st <= WAIT;
      default: st <= IDLE;
    endcase
  assign notrun = st != RUN;
  assign done = st == DONE;
endmodule
