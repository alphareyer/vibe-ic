# Declared physical scan access

A published post-DFT netlist may have manufacturing ports absent from the functional
L9 interface. `io_pad_chip_top_gen.py` accepts an explicit integration choice in
`config/dft_test_access.json`; it then includes those ports in its normal
LEF/Liberty-derived IO-cell selection, wrapper connections and pad-ring answers.
It preserves L9 and the functional pad-side partition. Without a plan, the existing
`DFT_CONTROL_UNCONNECTED` refusal remains blocking.

The supported mapping is one dedicated pad per scalar scan port. Port names,
directions and functional levels must match the selected netlist and published
scan metadata. Both sources are bound by SHA256; any netlist or metadata change
requires review and renewal of the declaration. Unknown ports, duplicate mappings,
direction conflicts, stale hashes and nonmatching functional levels refuse.

Example (identifiers and hash values must come from the actual design):

```json
{
  "schema": "vibeic.dft-test-access.v1",
  "mapping": "dedicated_pads",
  "authority": "Integration decision and original-input review reference",
  "netlist_sha256": "<selected post-DFT netlist SHA256>",
  "scan_metadata_sha256": "<published scan_chain.json SHA256>",
  "functional_mode": {"serial_in": 0, "scan_enable": 0},
  "ports": [
    {"name": "serial_in", "direction": "input", "side": "W"},
    {"name": "scan_enable", "direction": "input", "side": "W"},
    {"name": "serial_out", "direction": "output", "side": "W"}
  ]
}
```

`functional_mode` states externally driven levels, not permanent internal ties.
The plan does not synthesize a pin mux, change a clock, add timing exceptions,
or certify a scan clock rate. The integrator must check original-input pin/die
constraints, mode entry/exit, reset, scan transport and the downstream physical
consumers. The output record includes the declaration hash and mapping; existing
per-pad records identify the chosen masters, core pins, bond terminals and auxiliary
control levels. A generated wrapper is not a physical signoff result.

To reverse the integration, remove the plan and rerun the producer in a fresh
output directory. A published scan core will then refuse until another complete
integration plan is supplied. A functional-only run without published scan
metadata retains its original wrapper bytes.
