# Default M3/M4 producer boundaries

The fixed runner dispatches M3 after passing M2, then M4 after passing M3.
The canonical gates use `--require-current-production`. The old report-reader
CLI is retained for existing callers/tests; it does not qualify Default M3/M4.
Strict gates do not read waivers, accept stubs or skip on missing evidence.

`mixed_signal_m3_run PROJECT --top TOP --json AUDIT` writes both declared M3
outputs and `reports/analog/mixed_signal/m3_run.json`. It binds the absolute
project/top, current L22, merged GDS, routed Verilog/DEF/constraints, available
post-route SPEF and analog extraction/hardmacro views, and exact output hashes.
Missing files remain null hashes. New/changed inputs invalidate the receipt.

This revision implements **unmeasured production only**. There is no qualifying
positive M3 schema. A9's existing real ngspice/d_cosim producer composes A3
decks and an observer, not current extracted inputs coupled to the actual
digital wrapper. The OpenSTA/SPEF timing-aware SI program is an advisory
capacitive screen without qualified hardmacro receiver/noise coverage. Neither
result may be promoted to a measured PASS. No simulator is launched here;
execution/version lists and measured scenario/interface lists are empty.
Both outputs say NOT_MEASURED, production returns 2, and strict audits return 1
with NOT_VERIFIED. Even re-hashed asserted PASS data cannot extend this schema.
Required native integration and missing input details are explicit blockers.

`mixed_signal_signoff_run PROJECT --top TOP --json AUDIT` derives signoff from
current M1 evidence, re-derived M2 structural evidence, both strict M3 audits
and existing LibreLane material-bound DRC/LVS/antenna/density evidence readers.
Native PV must apply to the exact merged GDS, not an earlier digital-only GDS.
Unselected native producers have NOT_MEASURED coverage with no legacy fallback.
`ready_for_tapeout` is calculated; previous signoff is never an input.
Blocked production writes NOT_READY/false and returns 2.

`--check-only` reads existing production bytes and never creates signoff or
the M3 required outputs. Audit destinations cannot overwrite evidence, even
via symlinks. The M4 audit compares the existing signoff with a fresh pure
derivation, including all upstream hashes and native PV details. Missing,
malformed, foreign, stale, failed and unmeasured evidence prevents READY.
M2 is unchanged. A passing bounded M2 test establishes structural wiring only;
M3/M4 require independent real-design execution and review before acceptance.
