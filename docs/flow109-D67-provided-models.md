# Default D67 provided-model input boundary

D67 runs the already selected ngspice tool on an owner-provided passive lumped
RLC subcircuit, then reopens its actual deck, model, native log, source and
executable identity through the normal D67 consumer. It does not extract a
package, qualify a package supplier or grant whole-flow acceptance.

`input/flow109/D67.json` binds `requirements` and `port_reference` by path/SHA256.
Their canonical paths are:

- `requirements/package_electrical/package_rc_thermal_validity.json`
- `adopted/package_electrical/package_model_ports_identity.json`

Both need an APPROVED requirements_owner approval with a bound source_reference.
The requirements bind the provided top/netlist and a nonempty closed list of
model/corner/mission cases. Each case declares a model path/hash and subcircuit,
three explicit drive/observe/return port mappings, the ideal-voltage ramp/hold
waveform with explicit initial/final voltages, temperature/rise/step/
stop/sample mission parameters, and numeric limits with units for **every**
provided R/L/C constant plus each requested supported native transient measure.
The adopted reference binds that exact top/netlist/model/corner/mission/port
identity. The executable case in `test_flow109_package_electrical.py` and the
provided `tests/fixtures/flow109_d67/package.sp` show this bounded contract.

Only self-contained constant passive RLC models (at most 64 components and
10,000 declared transient steps) are supported. Unprovided models, absent
approval, mission, port mapping, units or bounds do not become zero/defaults.
Raw IBIS, behavioral/transistor models, includes, arbitrary SPICE commands,
thermal/full-wave requirements and other unimplemented signal checks refuse.
Only an approved scope explicitly declaring thermal/full-wave not required and
no additional signal obligations can accept the bounded electrical result.
The thermal metric's limited status is disclosed; no thermal value is invented.

D06 itself is still UNIMPLEMENTED at the pinned base. D55's existing
`flow109_functional_activity._derive_scenario_authority` reads the provided SDC
`set_load`, cell/pad Liberty capacitance units and voltage rails, and actual IO
connectivity. Its producer can retain that authority/external_loads in
`adopted/functional_activity/gls_activity_freeze.json` (or its disclosed partial
freeze). This is a **bare-die C-only interface envelope**, not an actual package
R/L or thermal model. It cannot satisfy the physical package-model requirement
by itself, and this change does not promote it into one or rerun STA/GLS.

The actual SPM package_model_required flag stays true. No actual-case model,
port map, approved package bounds or mission/corner authority was supplied in
this work package. The actual-case input gap remains the canonical D67 manifest
and its source-owned model/reference/requirements above; D75's unsupported
package path and all other outstanding requirements are preserved. No SPM/ADC
physical files are moved, copied, edited or rerun by this source task.
