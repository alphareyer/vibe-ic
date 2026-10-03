# Supported native M3 contract

The ordinary fixed DAG invokes `mixed_signal_m3_run.py` before the two existing
strict M3 gates. Without `input/mixed_signal/native.json`, its R2 unavailable
path remains unchanged. This fixture declares only a sampled model component;
`full_design_verified` remains false even when the cosim gate passes.

`native.json` uses `vibeic.mixed_signal.native_inputs.v1`, an explicit `top` and
`scope: declared_model_component`. All paths must resolve inside the project.
The digital section supplies a flattened current Verilog netlist, optional
`extra_sources`, and five distinct scalar port names: `clock`, `reset_n`,
`drive`, `sense`, `response`. Reset is active low. The analog section supplies
an explicit two-port SPICE `subckt` with ports `drive,sense`, optional flattened
model `dependencies`, DAC rails/transition in V/ns and ADC thresholds in V.
Hidden Verilog/SPICE includes and control sections are unsupported.

Current L22 `verification_plan.cosim_scenarios` supplies unique scenario IDs,
`native.clock_period_ns`, `cycles`, `measure_from_ns`, `tran_step_ns`, and
nonempty finite criteria with `native_metric` and `min`/`max` limits. Metrics:
settled_voltage_min_v, settled_voltage_max_v, response_mismatches,
unknown_samples, drive_transitions. Measurements sample the native waveform
at actual wrapper observation times after the declared start. Drive transitions
cover the entire actual drive trace. At least three cycles, at most 4096 and
one million transient intervals are supported.

Icarus compiles and VVP runs the supplied wrapper to observe its actual drive.
That trace produces the ngspice DAC source. A second actual wrapper execution
consumes ADC samples derived from the native analog waveform. Drive trajectory
must be independent of measured feedback: a changed drive requires a coupled
solver and returns NOT_MEASURED. This bounded feed-forward path does not model
arbitrary asynchronous feedback, extracted receiver noise or gate timing SDF.
It never substitutes a generated wrapper or expected output for the supplied
DUT. GNU time records peak RSS; native children run sequentially under 8 GiB.

Optional `si` declares current `netlist`, `sdc`, `spef`, `liberties`, `vdd_v`,
`noise_margin_mv`, optional `propagated_clock`, and `interfaces` with a `pin`
and bounded timing `criteria` (`metric`, `min`/`max`). OpenSTA timing fields:
arr_rise_min/max, arr_fall_min/max, slew_rise_max, slew_fall_max, slack_max
(all time values use the existing OpenSTA emitter's ns convention).
A complete matching SPEF with extracted connections for declared pins is
required. Existing OpenSTA/SI code produces arrival/slew observations and an
advisory noise/coupling screen. Missing pin metrics remain NOT_MEASURED.
A measured timing/over-slack coupling failure is FAIL. The capacitive noise
screen remains ADVISORY; no qualified hardmacro receiver/noise coverage is
available in this contract, so full SI/M3/tapeout readiness remains blocked.
No SPEF, Liberty or post-route result is invented by this fixture.

Canonical cosim/SI outputs have native schema `vibeic.mixed_signal.m3.native.v1`.
`m3_run.json` binds exact input/output hashes, native commands, current command
input hashes, tool versions, timestamps, scenario IDs, scope and peak RSS.
The gates rederive metrics from the bound native waveform and wrapper logs;
syntax-only, changed input, replay, wrong paths/design, empty scenarios and
failed measurements cannot pass. Auditing is read-only for canonical and native
artifacts. M4 derives readiness from current M1-M3 and native physical checks;
any required FAIL persists, and unavailable checks prevent readiness.
