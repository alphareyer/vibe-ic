# Frontend Default cut-over, 2026-10-03

Base: `59cd75606885b71ca7d11bbe34baf2541671be68` (tree
`89837e44d1d56aa13e58a4f1f21f700925df734b`). The fixed canonical runners
and gates remain the authority. No execution controller is added.

Here A means an existing tool producer is available; B means the Default
runner uses that producer with its existing output contract and gate.

| Step | A | B after this patch | Production seam / remaining requirement |
|---|---|---|---|
| 2 | READY | NOT_READY | `step_rtl_lint_tool` supports LibreLane Verilator.Lint, but requires `switch.pdk`. A declared, switch-free Phase-2 PDK source/resolver and its consumer evidence are still missing. Existing opt-in and lint gates are retained. |
| 7 | READY | READY for chip pad-ring class | `selected_mode(7)` selects LibreLane. `step_prelayout_signoff` uses its existing 8/10 invocation and publishes resolved `STA_CORNERS` as canonical PVT output. The existing SDC design-intent author and independent PVT gate remain. Core-only and explicit direct selections retain their producers. |
| 9 | READY | NOT_READY | `_step_synth_librelane` exists with native checkers and Vibe netlist/area/provenance gates. Step14's canonical YAML still invokes direct-script template/hilomap gates. Their binding to tool output, plus the Phase-2/3 canonical handoff proof, must precede Default promotion. |
| FS1 | READY | READY, retained | `step_fmeda_fault_injection` already runs the canonical simulator-backed producer before the independent `fmeda_coverage_check`. Missing RTL remains unmeasured; structural inapplicability remains explicit. |
| 12 | READY | READY, retained | `step_dft_lec_chain` already invokes fixed Yosys `opt_clean -purge`; the scan-survival gate judges the post-DFT output. No equivalent scan-preserving LibreLane producer was found. |
| 13 | READY | NOT_READY for sole LibreLane EQY | Default retains `lec_run`'s native Yosys proof and the current-netlist gate. Remote cut-13 promotes dual producers. A sole-EQY nonvacuity/X-policy contract and evidence binding to the netlist PnR consumes are missing. |
| P0 | READY | READY, retained | `flow_step_output_content_check` already invokes `p0_tool_frontend_check`: Yosys elaboration retained as `rtl_elab.json`, Verilator structural findings, and the existing P0 gate. |

Reused source: the Step7 default hunk from cut-7
`82e200e3632b53040e72cbc8ed1a95e6bbfd05b7`. P0 work from cut-frontend
`fb4cd62b799a229ed5a8fcbebdf370031a34916e` and shared-code work
`eef70912e53989c508945f64f77d6f32105d1f6b` already exist in this base.
Inspected cut-9 `95a8d78f10d74622f702344cea2acf12200a76a0` /
`f9baa1fd5f4ddc0d785a053db2a34bf6d5ec7445` and cut-13
`e3f551bec77a610fe8cb215661c0beb7e9507348` /
`ef43026aae23a0567fb327b10aaa50807b0c0378`; their overlapping gate
deletions, baseline changes and dual promotion are not adopted.

Verification exercises the production Step7 runner, mode precedence, its
SDC consumers, the tool-output adapter and independent PVT gate. The new
consumer test distinguishes staged corners from tool corners, rejects an
empty tool set through the existing gate, retains tool failures and direct
opt-out, and reproduces the original failures when only the production
default is restored. Focused existing frontend and fixed-runner coverage
checks accompany it. These are routing and gate checks; no whole-IC or
full-program-suite qualification is claimed.
