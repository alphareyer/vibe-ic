# Declared receiver/noise contract

The R3 contract remains supported unchanged. An optional
`native.json.si.receiver_noise` adds exactly these fields (see native.json for
a complete example): `schema`, `id`, `top`, `scope`, `analysis`, `model`,
`model_sha256`, `subckt`, `ports`, `dependencies`, `temperature_c`,
`dc_drive_v`, `frequency_start_hz`, `frequency_stop_hz`,
`points_per_decade`, `criteria`.

Schema is `vibeic.mixed_signal.receiver_noise_inputs.v1`; analysis is
`ngspice_noise`; scope is `declared_model_component`. Top must match the native
design. Model/subcircuit names and paths are literal and project-local. The
model defines a two-port subcircuit (`drive`, `sense`), and may use a flat map
of model dependencies to exact SHA256 hashes. The main model also has its
mandatory exact current SHA256; a dependency cannot shadow that binding.
Missing/stale/foreign models, unknown fields/analyses, hidden includes/control
and incomplete/unbounded criteria are refused before any native command.
Absence of the entire receiver_noise declaration stays NOT_MEASURED.

The declared band is finite and positive, stop greater than start and at most
1 THz, with 20..200 points per decade and 2..20000 exact logarithmic intervals.
Temperature is above absolute zero and at most 200 C. DC bias must lie within
the native contract's declared drive rails. Criteria use
`metric: integrated_output_noise_v_rms`, `units: V_RMS`, a finite nonnegative
`max`, and optional nonnegative `min` no greater than max.

The producer creates a current model-bound deck and runs ngspice using its
SPARSE matrix solver, explicitly clearing `sqrnoise`. It saves the output
voltage noise density (`V/sqrt(Hz)`) and integrated output voltage noise
(`V_RMS`) for the entire declared band. Commands/versions, current input/model
hashes, run/scenario identity, exact artifact bytes, timestamps and peak RSS
use the existing M3 receipt. The consumer validates those bindings, regenerates
the deck, checks the complete native frequency grid, compares scalar output
with the native log, checks spectral integration within the fine-grid numerical
cross-check tolerance, and regrades current criteria. Grading uses ngspice's
integrated scalar, not a predicted value or text preview.

Inside-limit observation gives noise coverage MEASURED and component verdict
PASS. Measured limit violation retains MEASURED coverage with verdict FAIL.
A failed native model/tool run is FAIL without a measured scalar. Unsupported
or absent coverage is NOT_MEASURED. The strict SI audit exposes only rederived
noise diagnostics. Timing and crosstalk stay independent; full readiness
requires all existing dimensions actually measured. This path grants no PDK,
PVT, extracted receiver, whole-IC or physical signoff coverage.

Noise vector/units reference: [official ngspice manual, noise analysis](https://ngspice.sourceforge.io/docs/ngspice-44-manual.pdf).
