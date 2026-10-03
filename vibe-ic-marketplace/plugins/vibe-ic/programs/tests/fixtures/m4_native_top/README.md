# Bounded open mixed-top PV input

`mixed_probe` is a logical interface wrapper around one full-device open-PDK
inverter renamed `sense_receiver`. Its GDS/SPICE/CDL geometry and devices come
from `sky130_fd_sc_hd__inv_1` in the pinned image's Sky130A library; origin and
source GDS hash are in `input/mixed_signal/open_layout_origin.json`. The digital
input stream supplies wrapper port labels. The routed DEF declares placement;
M1 actually assembles the receiver into `phase3/mixed_signal/top_merged.gds`.
There are no captured PV reports, metrics, signoff assertions or PASS fixtures.

The test runs fresh native M1, hashes its outputs and the current input files,
then declares `input/mixed_signal/top_pv.json`. Ordinary M4 executes existing
LibreLane Magic/KLayout DRC, full Magic extraction and Netgen LVS against that
exact stream and its explicit powered logical/CDL/model pair. Independent
KLayout LVS and density remain NOT_MEASURED when their PDK runsets are absent.
Antenna requires both native populations and affirmative route evidence; this
fixture does not supply a complete routed chip or route-completeness assertions.

The paired input layout is a small verification subject, not a qualified analog
design or a tapeout. M1's initial hierarchy comparison uses hardmacro stubs;
M4 Netgen compares actual extracted devices to the full declared macro SPICE.
M2/M3 evidence is absent. No M1-M4/ready-for-tapeout credit follows from the
component proof alone. A changed logical connection must produce native FAIL.

Use `programs/tests/test_m4_native_top_pv.py` with native Docker available and
the declared pinned image. The request accepts precisely schema, top, scope,
PDK/image, layout, logical, powered_netlist, CDL, routed_DEF, SDC, SPICE/CDL model
paths and input_sha256. All paths are project-local. The scope is
`merged_top_native_pv`; schema is `vibeic.mixed_signal.top_pv_inputs.v1`.
Imports/includes in supplied logical/model files need a separately supported
material closure and currently refuse; use standalone declared models.
