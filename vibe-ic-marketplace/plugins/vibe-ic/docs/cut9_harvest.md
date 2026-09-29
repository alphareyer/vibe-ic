# CUT9 correctness repair and harvest destinations

Route: DIE. Landing order remains cut-7 -> cut-13 -> cut-20 -> cut-19 -> cut-9.
This repairs the two correctness SEND_BACK findings on the original CUT9 tip.
LibreLane remains the default synthesis tool; the direct recipe is retained for
its existing users and unresolved harvest rows. No further custom code is removed.

| Historical lesson / evidence | Destination on the tool path | Verification |
| --- | --- | --- |
| No hilomap leaves literal constants and router `zero_` nets (v068) | `synth_handoff_netlist_check.CONSTANT_NOT_TIED`; LibreLane hilomap with resolved tie cells | Existing real-Yosys tied/untied calibration pair; partial multi-bit concatenation controls |
| Undefined constants must be resolved before hilomap (HDLC v0.1.98 rule 1; issues 735/687) | `UNDEFINED_CONSTANT`; LibreLane `SYNTH_TIE_UNDEFINED=low` | Exact partial-x expression retained in `test_setundef_zero_before_hilomap_producer.py`, read by the regression test |
| Aggressive cleanup after hilomap recreates constants (v0.1.98 rule 2) | `CONSTANT_NOT_TIED` on every literal in assign RHS and cell-pin expressions | Whole-constant and partial constant controls, including nested concatenations |
| Flattening is required for the downstream handoff | `HIERARCHY_NOT_FLAT`; resolved `SYNTH_HIERARCHY_MODE` | Existing CUT9 hierarchy control |
| SystemVerilog frontend selection | LibreLane `USE_SLANG` / `read_verilog -sv`; named synthesis refusal | Existing input-selection/frontend tests |
| Empty or stale handoff (issue 1253) | `NETLIST_EMPTY`, `HANDOFF_UNBOUND`, `HANDOFF_STALE` with RTL fingerprints | Existing CUT9 empty/stale controls |
| Inline recipe recovery and substantive/vacuous distinctions (issues 649/2277/599/887) | Netlist gate over either producer; missing input is rc 2 NOT_MEASURED | Existing project-mode and flow-compliance controls |
| ABC must buffer at the declared fanout cap without synthesis sizing (DRV standard) | LibreLane `SYNTH_ABC_BUFFER_ONLY`; actual sourced script retained by `drv_stage_receipts`, consumed by `drv_capture_plan` | Native ABC script fixture, `buffer -N 4 -S 3000`, command order/separator controls, run/hash tamper controls |
| Internal tri-state buses need `tribuf -logic` | Fork [vibeic/librelane#29](https://github.com/vibeic/librelane/pull/29), merged; config `SYNTH_TRIBUF_LOGIC=True` | Installed synthesis class must declare the key before resolution; absence produces NOT_MEASURED / tool_absent |

The native ABC script fixture contains the unmodified bytes of a tool-generated
buffer-only script. The producer reads ABC's own `source` log line rather than
selecting a nearby script by filename. The stage receipt binds source, retained
script, execution log, native state/config, native/handoff netlists and run ID.
The final judge re-reads every native reference, stage receipt and run claim,
and requires the stage's run ID to match the measured bundle's run ID.
Changing any recorded evidence invalidates stage credit. Comments, strings,
attributes and parameter overrides do not constitute constant connections.
Escaped Verilog identifiers are protected before comment blanking: a legal
identifier containing `//` must not hide a partially constant connection.
Both boundaries have executable negative controls from independent review.

The prior CUT9 harvest rows with no destination remain open: post-route-elected
synthesis recipes, the `SYNTHESIS` retry, area retry, reference-flow LCU knobs,
hard-macro blackboxes and readmemh input staging. Their direct implementations
remain present. This source repair makes no full-IC PASS claim and does not
repeat old synthesis, placement, routing or signoff runs.
