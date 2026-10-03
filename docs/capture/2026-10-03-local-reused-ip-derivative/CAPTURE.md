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

## Next

Root should proceed to final review and shipping of this capture correction.
Current design qualification, including normal current synthesis/LEC consumption,
remains pending admission of the composed owner sources. Root owns version and
push; this follow-up changes capture documentation and routing only.
