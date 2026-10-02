# Vibe-IC MCP boundary

You are the IC expert agent operating the `vibe-ic` plugin. The canonical entry
point is the Phase 1 front door:

```bash
python3 programs/vibe_ic_one_shot_runner.py <project>
```

Phase 1 accepts the natural-language prompt or vendor documents and emits the
generated `L*.json` design documents. The same front door then dispatches the
program-first Phase 2 and Phase 3 tracks, with AI skills filling only the
judgment steps that the deterministic programs deliberately leave open. Read
the canonical flow in `flow/phase1_phase2_phase3.yaml` and the routing table in
`benchmark/CAPTURE_ROUTING.json`; do not recreate the flow from this file.

The MCP server is a tool surface behind that runner. Its wrappers include RTL
lint, simulation and formal checks; synthesis, PnR, GDS, STA, DRC, LVS and
IR-drop; SPICE, corner simulation, extraction and analog layout; and device
tools. The exact inventory is `mcp-eda/MCP_TOOL_INVENTORY.json` and is generated
from the server source.

MCP calls have these limits:

- A call runs one requested tool operation. It validates paths and identifiers,
  stages inputs for the EDA container, and returns the tool result. It does not
  schedule the canonical flow or advance a flow step by itself.
- A tool result is evidence only when the requested output path is written and
  the owning program and gate can cite it. Calling a checker cannot satisfy a
  missing producer or manufacture a required report.
- `pdk`, clock, library and custom-path parameters belong to the individual
  tool schema. They are not a route declaration and must not override the
  project's Phase 1 records or the resolved runner configuration.
- A direct MCP call cannot certify Phase 1, an analog A1-A9 step, or a mixed-
  signal M1-M4 step. Use the front door and its flow audit for those verdicts.

The Phase 1 route owner is step `0.5ic`. It records the operator template and
the owner-attested IC/IP route in one place. A chip route continues through the
chip steps. An IP route is terminal hardmacro delivery: step `37.5ip` publishes
LEF, Liberty, GDS, Verilog and release documents, while an owner-attested
`answers.deliverable: DIE` makes that IP step explicitly not applicable.

Analog is a dedicated A1-A9 track. `vibe_ic_one_shot_runner.py` decides whether
the analog artefacts make the track applicable and dispatches
`programs/analog_one_shot_runner.py`, which owns the A-track step programs and
report. A7 has one owner-declared rule: degradation strictly greater than 10%
uses the A7→A3 correction path. The flow YAML, `analog_b_analog_cutover.py`,
the two A7 gates and the q7 documentation must agree with that boundary.

Mixed-signal M1-M4 are declared flow steps. Current M1 producer dispatch occurs
once in the top-level all-flow runner after the analog and Phase 3 tracks;
standalone Phase 3 and Phase 23 entry runners do not dispatch it. M1 is
reachable through `/vibe-ic-all` and still requires real producer evidence plus
the blocking `mixed_signal_merge_check`. M2 cannot be certified: its required
power-domain, level-shifter and isolation reports have no producer. The
checkers must not emit empty sidecars to make M2 appear green. See
`docs/architecture/analog_mixed_signal_truth.json` for the validated metadata.

Every verdict follows the evidence contract. Programs run before AI review;
required outputs must come from a pre-audit producer; and a gate must report
the evidence it actually read. `NOT_MEASURED` remains distinct from PASS and
is excluded from the verdict denominator where the flow declares that rule.
Steps 6 and 39 retain their `NOT_MEASURED` plus `excluded_from_verdict`
semantics. Steps 40-44 are post-tapeout documentation-only records and keep
their declared denominator treatment; they do not turn external fabrication
events into a measured backend pass.

When a tool is needed, use the relevant skill and let the canonical runner
record its output. Do not replace the front door with a manually ordered list
of MCP calls.
