# Explicit local derivative of reused IP

## Historical R1 source snapshot (2026-10-03)

**STATUS**: BOUNDED_SOURCE_ADMISSION_RECOVERED; independent review pending.

The original official verifier, its complete pin/event comparisons, and its
checksum rule are unchanged. A local semantic correction is not an upstream
erratum or newly authored core. The new ordinary admission dispatch verifies
a separate official parent using that original verifier, replays a finite
exact declared patch, and checks all current staged files (headers included).
A SHA-bound declaration binds original input quotes, exact unchanged challenge,
parent-red/candidate-green receipts and full measured RTL inventory. The existing
pull producer records local provenance and the normal Step1 consumer explicitly
labels LOCAL_DERIVATIVE. Missing/drifted/extra/undeclared data refuse.

Existing pull invocation:

    python3 programs/ip_catalog_pull.py PROJECT --local-derivative local/record.json

The record schema is vibeic.reused_ip_local_derivative.v1. Required references
are parent_project, parent_manifest, parent_provenance, patch, input_obligations,
proof.challenge/parent_red/candidate_green, current_inventory and local_provenance
actor/reason/owner_ruling. All references are project-relative path+SHA256; the
patch format exact_utf8_replacements.v1 specifies reused file before/after SHA256
and finite unique before/after text edits. SOURCE_MANIFEST retains original
official source_pins and adds local_derivative:{path,sha256}. Producer provenance
separately counts unchanged official, locally adapted reused and authored files.

The meaningful pre-fix ordinary-consumer control fails on an observed checksum
refusal; it is not a missing API test. Final new16controls pass. Existing8official
controls passed in the preceding bounded run. Negative cases include current
drift, extra header, undeclared modification, missing parent/patch/proof/input,
changed producer receipt, forged fully rehashed parent, ambiguous patch, symlink,
record drift and unavailable upstream hiding known current drift. The unchanged
earlier symlink preflight refuses as NOT_MEASURED; source verification is MISMATCH.

Actual current-run original inputs and prior challenge bytes were staged in a
new copied project. Normal producer returns0, ordinary Step1 PASS_WITH_WAIVERS
labels LOCAL_DERIVATIVE, and the strict official checker still refuses the adapted
file. The full22reused inventory is21official unchanged+1local adaptation; glue
stays separately authored. The WEN candidate is not silently combined.

Original NOT_ACCEPTED and failed test/launcher boundaries are retained. Packet
local-derivative-admission-evidence-r1.tar and recoveries.json bind exact evidence.
Source acceptance is not native design qualification, D1, LEC or wholeIC PASS.
The input-grounded semantic challenge remains its existing immutable0/1 vs0/0
proof. CurrentRTL-to-new-synthesis LEC remains NOT_MEASURED, not equivalence with
the intentionally corrected upstream bug. No LC owner or global transport edit.

## R2 source-only review (2026-10-04 Asia/Taipei)

**STATUS**: ACCEPT_COMPONENT for the R1 refusal-precedence repair only.

Independent review accepted source e7506df9b47c2c6eec6bd67f7ef034c84ee7b81c,
tree 382c6ac812e449cce494953c461168735e2d1c8d, based on
fb0bfb9e9f1210cceb585303b06a03fc7ecdb83a. The unchanged missing-producer-event
counterexample failed on R1 and passed on final R2. Retained final evidence has
4 passed: that counterexample, ordinary producer/consumer admission, complete
consumer with unavailable parent returning NOT_MEASURED, and new-producer staging.
The intermediate 3 failed/1 passed draft remains preserved. Existing consumers
now reject a known missing or drifted local event before unavailable parent
replay; the strict official checksum verifier and staging exemption are unchanged.

Capture subtype phase2.rtl_gen.reused_ip_local_derivative routes Bucket A to
programs/ip_catalog_pull.py, with programs/design_one_shot_runner.py as the normal
Step 1 consumer. This is ALREADY-PROGRAM metadata for the accepted implementation;
an emitted rule sketch is not an additional runtime implementation. Generic
Pattern, docstring and fix_action are recorded in recoveries.json.

Portable evidence: local-derivative-provenance-r2.tar, SHA256
7b5aeb3461911fddbfd3053dec590675d51a353a815ff0712bb07072f172f7e0;
local-derivative-precedence-r2-green2/focused.xml, SHA256
f482022059418314cbd87cf65995fb70dabd26ace42b2237b2a32d35db68343a;
subservient-local-derivative-independent-r2/HANDOFF.md, SHA256
01c59e52be2c39e3ec3c9506208074bfc6e4de9884f6cd70aa10467ff42b5b4e.
The historical R1 snapshot and its immutable evidence hashes above are retained.
No runtime controls or native work were replayed for this capture correction.
LEC and D1 remain NOT_MEASURED, the derivative NOT_ADOPTED, and whole IC INCOMPLETE.

## v1.27.10 landed provenance and offline-reference follow-up

The dated supplied landing receipt binds `eef4b319b3b4fadd890ced8abbf8d969a6804357`
and tree `f0e9764984bd86bc1576e260d4d856ea88a39dd5`. Accepted components
`3094a0593b2cc21f5c03c3893838035680e76d1c` and
`694c833485fb0a87611c049d5c83868be7638743` are now absorbed in that release.
The producer writes the declared replacement before publishing written-output
digests; the strict official verifier remains unchanged. Offline admission is
limited to the explicitly approved read-only origin/ref/commit/inventory.

Existing evidence is retained accurately: the wrong-cwd attempt is not credited;
correct-cwd c1 reports six passes and one unavailable-parent fixture failure
because its cache was actually available. b20 changes only that fixture, and
its exact prior-red selector passes with the refusal assertions preserved.
Offline driver rc0 comprises one base UNAVAILABLE and six candidate expected
states (one VERIFIED and five MISMATCH), not seven candidate PASS cases.

The separate legacy recovery `daceff44d48cde2b449e33f7aa6f9128c8d507e4`, tree
`ba1fca5bc041bc08f43210d2942f517f71c8c4dd`, has independent ACCEPT_COMPONENT
and six retained focused passes but is an unlanded candidate. It re-derives all
bindings before recognizing exactly the legacy event lacking both exit_code and
outputs, writes the same verified bytes, retains the old event prefix and appends
a current event. Actual003 producer VERIFIED/rc0 does not promote the ordinary
bounded entry/exit rc1/NOT_MEASURED awaiting signed judgement. The previous
RUNNER_LOCK_REENTRANT cause attribution was incorrect; delegated re-entry is legal.
Actual002 refusal, actual001
setup failure, and the historical prose-versus-launch identity discrepancy remain.
No LEC, 247, D1, producer or consumer is replayed for this docs update.

Portable packet/file/SHA references and final ten shipping source blobs are in
`vibe-ic-marketplace/plugins/vibe-ic/docs/enhancements/public-digital-flow-recoveries/v12710-evidence-index.json`.
The original source snapshots above remain unchanged; `recoveries.json` appends
the landed follow-up and accepted legacy candidate without a duplicate rule.

## Next action

Root should proceed to final review and shipping of this capture correction.
Current design qualification, including normal current synthesis/LEC consumption,
remains pending admission of the composed owner sources. Root owns version and
push; this follow-up changes capture documentation and routing only.

## Dated current-facts correction (v1.27.11)

The paragraph immediately above retains the earlier v1.27.10 snapshot. Root's
LANDED receipt now binds main `dd001c6c62912a59be9a270fb15412b6ecf72544`, tree
`97879719cc181e5d35f695f1fd4780ec885e7442`, absorbing `daceff` and the final LC
ordinary LOCAL workdir repair. Five existing controls plus one actual Python
child positive and independent component ACCEPT are retained, not rerun.

Actual003 awaited signed Step1 judgement, not a lock repair. The later independent
current Step1 PASS has normal API check true, receipt SHA
`46a9ea0094088a48acad1f364459ae79eca284d47eb7c01d90f361ae607059a4`, evidence
`576b5b7881c5517996c7f8ef7a25e0ce1d0d06756f7351af25b64cec9331f1c4`; D1 is
unchanged. Legal delegated parent-child re-entry is not a refusal cause.

Existing actual006 then measured normal synthesis and current RTL-to-synthesis
LEC PASS, but overall Phase 2 FAIL/rc1 in 212 seconds. Final audit reports five
dangling references and historical outputless-event completeness; Phase 3 is
SKIPPED. Zifencei stimulus and rv32i_40 coverage remain NOT_MEASURED. Original ten
inputs, 23 RTL files and D1 are preserved. This is not whole-flow acceptance.

Seven portable packet/file/SHA references and the current landed identities are
appended to the existing public-digital `capture.json` and evidence index. No
source tests, native flow, D1 or prior proof were replayed for this correction.

## Next action

Root should proceed to review the dated correction. Existing source owners retain
the actual006 audit work and formal tool-image publication. This capture adds no
implementation, new gate, version change, repin or whole-IC PASS.
