# D55 functional activity component

The general `--flow 109 --entry-step D55 --exit-step D55` entry dispatches
`flow109_functional_activity.execute`. It uses the existing
`sim_activity_dump.dump` producer on the **adopted gate netlist**, never RTL,
and the existing `_ppa.power` activity and power-report consumers.

The pinned LibreLane `OpenROAD.STAPostPNR` has no VCD/SAIF configuration.
Its present final-power adoption is vectorless. D55 therefore uses native
OpenSTA `read_vcd`/`report_power` as a complementary component. It does not
replace or publish `reports/phase3/power.rpt`, and cannot claim the actual
post-route/final power operation adopted this activity.

## Input and qualification

`input/flow109/D55.json` has schema
`vibeic.flow109.functional-activity.v1`, `basis: adopted_netlist_gls`, the exact
`top`, SHA-bound `adopted_netlist`, `liberty`, nonempty `simulation_models`,
`sdc`, `requirements`, `loads`, and a `modes` object with one SHA-bound
`testbench` for every required mode. Required files use `{path, sha256}`.
The SDC is a source-bound, qualified executable input, not an adapter-generated
timing target. Its transitive dependencies must be represented by the selected
qualification reference; the adapter does not certify arbitrary SDC programs.

The bound requirements file records owner approval and source reference,
`modes.<name>.required_ports`, and a full-window `{start_tick: 0, end_tick,
timescale}`. `external_loads` records one rail's voltage and every output's
`{rail, capacitance_library_units}`. The separately bound load file must equal
that approved content plus `requirements_sha256`. The selected Liberty's
nominal voltage must match. The adapter does not choose product thresholds.

Each compiler, simulator and OpenSTA use needs an independent current D03
qualification, with exact executable, library/model scope, operation source
closure and configuration. Native launch rechecks qualification, source and
input identities. Generated VCD, scope manifest, Tcl and power report are
also hash-bound before consumption/publication. Tests' developer control
records are not production PDK/tool qualification.

## Evidence and limits

Each unique attempt retains actual argv/return code/log, GLS VCD and scope,
per-port observations/transitions, window, native annotation origin for every
required top port, output-load census and actual vector-power report. A global
nonzero annotation count alone is insufficient: every required data port must
have origin `vcd`; all native scalar top ports must be in the requirement set.
A simulator failure remains FAIL even when it writes a partial VCD.

A successful component publishes
`adopted/functional_activity/gls_activity_freeze.json`; failed attempts cannot
leave an old current freeze available. Existing attempt receipts are retained.

This tranche supports only full-window, single-rail, scalar top-port GLS.
Partial windows, bus/inout handling, mapped RTL VCD/SAIF, analog current traces,
mission adequacy, qualified upper bounds and actual downstream post-route
power adoption remain unimplemented/unmeasured. All three complete D55
metric bindings and `flow109_acceptance` remain **NOT_MEASURED**. A component
PASS proves this declared native producer/consumer operation, not D55 signoff.
