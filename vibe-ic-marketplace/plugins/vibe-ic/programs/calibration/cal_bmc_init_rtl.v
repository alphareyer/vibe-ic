// Calibration structure for lec_run::parse_bmc_log (FX_LEC_BMC review 3): a
// register with a non-zero initialiser and NO reset (an LFSR), beside an FSM
// with no reset that synthesis re-encodes (so the ladder leaves points
// unproven and the search runs). The gate netlist is written `-noattr`: it
// carries no init, so the initialiser exists on the gold side only.
module cal_bmc_init(input clk, input rst, input go, output tap, output busy);
  reg [7:0] lfsr = 8'hA5;
  always @(posedge clk) lfsr <= {lfsr[6:0], lfsr[7] ^ lfsr[5] ^ lfsr[4] ^ lfsr[3]};
  assign tap = lfsr[0];
  localparam IDLE = 2'd0, RUN = 2'd1, DONE = 2'd2, WAIT = 2'd3;
  reg [1:0] st;
  always @(posedge clk)
    case (st)
      IDLE: if (go) st <= RUN;
      RUN: st <= DONE;
      DONE: st <= WAIT;
      default: st <= IDLE;
    endcase
  assign busy = st == RUN;
endmodule
