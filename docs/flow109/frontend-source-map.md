# Frontend Default producer ownership

This map was read from the source-owned 109 registry, the canonical YAML and
actual runner call sites. Legacy coordinates below are internal cross-references,
not new display IDs. No provider listing is treated as execution evidence.

| Responsibility | Existing owner | Existing consumer / constraint |
|---|---|---|
| D09 | `design_one_shot_runner.step_rtl_gen` | Canonical authored RTL gates; an AI handoff is not a completed producer |
| D10 | `p0_tool_frontend_check` and structural claims | Shared legacy P0/RTL validation spans require separation before exact dispatch |
| D11 | `step_rtl_lint_tool`, rewrite and determinism owners | Existing canonical validation shares a site with D12; never translate the shared span as D11 alone |
| D12 | Existing CDC/RDC/census producers | Must retain crossing/exception consumers; not covered by a generic lint result |
| D13 | Existing professional/reference/unit testbench producers and Verilator coverage | Requires the existing full functional evidence consumer |
| D14 | Existing harness/property/full-stack formal producers | Explicit bounds and unknown proof outcomes stay independent |
| D15-D17 | No complete existing single Default owner for the new responsibilities | Remain unimplemented; do not borrow adjacent simulation PASS |
| D18 | Existing FPGA early producer | Hardware absence remains unmeasured |
| D19 | `step_asic_sdc` | PVT/SDC current producer and canonical consumer; no invented corner completeness |
| D20 | `step_sdc_validation` | Native post-synthesis validation can be deferred; early syntax PASS is insufficient |
| D21 | New soft-IP synthesis responsibility | No mapped ASIC result substituted for portable soft-IP evidence |
| D22 | `step_yosys_synth` -> `phase3.step_synth` -> native LibreLane synthesis | This tranche uses the existing Default owner via `retained_call` and unchanged complete canonical consumer |
| D23 | Existing DFT insertion/ATPG/BSDL | Safety fault injection belongs to D27, not an implicit DFT success |
| D24 | Existing post-DFT optimization | Provider listing alone is insufficient: preserve actual mapped handoff and consumer |
| D25 | Existing RTL/post-DFT LEC | Actual equivalence proof and all declared consumers required |
| D26 | Existing pre-layout STA | Required matrix and constraint consumer remain responsible |
| D27 | New safety fault-injection contract | Deferred until a real product/tool-use qualification exists |
| D28 | Existing transition ATPG | Pattern proof and adopted-netlist identities must remain bound |
| D29 | Existing final FPGA flow | Optional hardware branch, not ASIC timing signoff |
| D30 | `step_synthesis_handoff` | Consumes the current mapped producer chain, not a filename-only netlist |

`execution_frontend_worker` and `execution_frontend_providers` are Controller
arms. In ordinary Default mode `dispatch_fixed_step` returns without running
those arms. They must not be relabeled as the Default producer. Some listed
worker functions are placeholders or incomplete standalone CLI wrappers.
