// Issue 2856 neutral frozen waveform.  This file is the reviewable source
// artifact for the same sequence emitted by
// pulse_width_rearm_conformance_check.py:
//
//   legal(max=4) -> overlong(active, max+2 + legal-looking suffix) -> inactive
//   -> legal(max) after a new rising edge.
//
// It deliberately names no private design.  The checker is the normal native
// consumer; it compiles the DUT with an equivalent bounded measurement module,
// records the stimulus/report hashes, and requires legal=1,bad=0,final=1.
// Keep this fixture beside the focused Python tests so an advertised executable
// path always exists in the source tree.
// PWR_SOURCE: fixtures/pulse_width_rearm/contract.json
// PWR_POSITIVE_RTL: fixtures/pulse_width_rearm/compliant.sv
// PWR_NEGATIVE_RTL: fixtures/pulse_width_rearm/level_reacquire_bad.sv
module pulse_width_rearm_regression;
  localparam integer MAX_WIDTH_CYCLES = 4;
  localparam integer OVERLONG_CYCLES = MAX_WIDTH_CYCLES + 2;
  localparam integer ACTIVE_SUFFIX_CYCLES = 2;
  initial begin
    // The actual DUT binding is intentionally owned by the generic consumer;
    // this neutral module carries the immutable waveform constants only.
    if (OVERLONG_CYCLES <= MAX_WIDTH_CYCLES || ACTIVE_SUFFIX_CYCLES < 1)
      $fatal(1, "invalid frozen pulse-width/rearm waveform");
  end
endmodule
