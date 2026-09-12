# Authentic historical routing input

`CAPTURE_ROUTING_IDENTITY.json` contains seven original Git objects authenticating
this exact path:

- Commit: `6dd97611eafa2af2d1aacc13dae88bd40c3c0e8b` (the verifier's existing CAPTURE_BASE).
- Path: `vibe-ic-marketplace/plugins/vibe-ic/benchmark/CAPTURE_ROUTING.json`.
- Blob: `444b6435a9d0e54c97db8313140c98b7aef63d73`.

The bytes were read from the existing historical Git object store on 2026-09-12.
Each object was exported through native `git cat-file`, independently checked
against its Git type/size/data identity, and linked through the original commit
and directory trees. No reconstructed commit, substituted routing table, changed
claim count, or replacement ref is used. This is a minimal path proof, not a
complete repository archive.

The existing authenticated-object reader verifies this archive when the original
commit is unavailable in the caller's clean clone. Both original routing checks
always execute. Missing or corrupted evidence leaves the historical routing
comparison failed; it cannot silently reduce the number of executed checks.
The report's expected count and historical prose remain unchanged.

`CAPTURE_ROUTING_RECEIPT_IDENTITY.json` independently authenticates the same
path at the existing immutable repair receipt
`58d5efd79cf60d75bfa156b83cecd1c63e78728f`. Its seven original Git objects
resolve to the same blob `444b6435a9d0e54c97db8313140c98b7aef63d73`.
The receipt's native parent is `324435d94a65f7ef1c8d2b8e4b66407cf778220d`,
whose native parent is the frozen base above. Both endpoints therefore prove
the quoted zero-step repair delta. Later routing additions on main are not
changes authored by that historical lane. The independent checks for current
routing coverage, target existence and known-unwired programs still use the
current routing file; this historical proof cannot waive their failures.
