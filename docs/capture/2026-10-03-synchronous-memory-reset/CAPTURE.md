# Synchronous reset at external memory glue

2026-10-03 bounded input-derived RTL repair. The independent real1KiB write
challenge fails the authored raw-reset WEN gate (compile0/run1). An owned glue
correction samples reset on i_clk and inhibits WEN from that sample without
retiming address/data. The identical challenge passes (compile0/run0). All22
received SERV/servile files and their original official pins remain unchanged.

The necessary affected positive executes ADDI/SW/LUI/JAL, writes42@256 and GPIO,
checks each valid write against adapter address/data, then holds sampled reset
at an idle write boundary. Initial retention remains checked24..1023; the new
idle-entry reset checks an unchanged snapshot0..1023. A synchronous SRAM may
consume the preceding valid transaction at the edge sampling reset; the output
inhibit follows that sample. There is no asynchronous cancellation claim.

Fresh pinned799 native CID 9f25b163c6b5f897e69dfbdca29239681ccb17d46e1ab97905b9b50aa9c34c3c ended0; ordinary L9 pin and pad
consumers both return0. Portable packet glue-sync-reset-evidence-r1.tar and the
exact per-artifact SHA256 references in recoveries.json retain original red,
unchanged challenge, candidate bytes, diff, logs and current consumers.

General capture belongs to catalog-glue-author's existing AI authoring contract:
for a declared clock-sampled transaction interface whose complete contract has
no separately declared asynchronous safety-inhibit requirement, qualify reset
at its declared sampling edge; preserve address/data timing and declare the
SRAM reset boundary. The full input interface/protocol contract takes precedence,
including any explicit asynchronous safety inhibit. Protocol selection is
expert/author work; no chip-name rule or new transport gate.

This primary repair is post-output and follows an independent executable input
finding. Independent expert answer remains consumed77/83 with7 advisory findings,
D1 NOT_MEASURED. The core fetch correction/local-derivative admission and LC LOCAL
owner component are separate work. Whole IC/fullISA/physical acceptance stays
INCOMPLETE, named firmware absent, and RTL-to-current-synthesis LEC unmeasured.

**STATUS**: BOUNDED_REPAIR_PROVEN; Bucket B author/skill capture.

2026-10-03 follow-up: why_not_bucket_a explicitly records protocol, reset-sampling
and latency judgment. Existing enhancement_emit mapping now names catalog-glue-author
through phase2.rtl_gen.catalog_glue, a precise mapping under the existing RTL
authoring handoff. This is routing data only, no new flow step or runtime gate.
The record is Bucket B; it is not labeled as a program fix.

## Next

Proceed to independent source review and ordinary currentRTL-to-new-synthesis
LEC after composing admitted owner sources. LEC remains NOT_MEASURED.
