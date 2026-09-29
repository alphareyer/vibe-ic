# c930sub finite IC engineering handoff

**STATUS**: finite source correction and native proof complete; upstream acceptance NOT_READY; whole IC NOT_COMPLETE_HOLD.

## Findings

The preserved chip-top functional FAIL is an unconditional idle-write-data X/Z check. The INPUT declares write enable and requires synchronous active-high reset; it does not require known idle write data. No reset re-arm obligation follows from a pulse without an active clock edge. The original failing receipt remains FAIL and was not replaced or edited. No RTL repair is justified by this measurement.

An independent self-checking Icarus probe uses the untouched delivered RTL and pad wrapper plus official shipped PDK behavioral models. It observes 750 between-edge pulses, 2250 qualified-transaction comparisons, 55 idle-write-data X samples, zero active-write X samples, 12 instruction-bus acknowledgements, two data-bus acknowledgements, a directed GPIO write and a directed SRAM store. Clock-edge reset reboots at RESET_PC and preserves all 1024 SRAM bytes. The new five-word directed stimulus is not the missing blinky/hello firmware or ISA-suite evidence.

Native controls: `qualified_green` rc0; `unsupported_idle_obligation` rc1 at `we=0,data=xx`; `async_source_reverse` rc1 because an asynchronous-reset source mutation changes a qualified control between edges. These are an independent valid obligation, an unsupported-obligation contrast, and a substantive source corruption control; they are not an original FAIL-to-PASS repair claim.

## Source correction and D1

The existing input-only carry-through producer discarded contract subsections whose heading contained only a chosen module name. It now copies descendants of an explicit L8 submodule-contract heading into `L9.integration.submodule_contracts`, bounded by heading depth and excluding denied subtrees. It preserves input provenance, existing consumer values, and idempotence. No RTL, physical artifact, port mapping, clock, memory, PDK or check threshold changed.

Producer proof on actual supplied INPUT: pre-fix FAIL rc1 observed an empty contract; post-fix PASS rc0 carried six sections; exact producer-source revert FAIL rc1 observed the empty contract again. Seven finite callable controls passed. This is a chip-agnostic additive producer change, not a new BLOCKING gate. Prove-by-run blocking is not claimed. The failure name set in the real producer arms is `{INPUT module-bus contract omitted from L9.integration.submodule_contracts}` versus the empty set in the candidate.

The two misplaced D1 expectations were re-scoped without deleting obligations: reset-vector carriage addresses the architecture plus parameter fields, and the GF180 period/clock expectation addresses its scoped timing evidence plus the clock-name field. The instruction/data-bus expectation addresses the submodule contract, not the chip's external ports. All 20 requested fields were reviewed against actual INPUT; the new program report agrees with 20 and has zero findings. The new digest-bound D1 signature is accepted by the existing program contract. It approves the reviewed extraction expectations only, not aggregate Phase-1 or whole-IC acceptance.

Original root: `4a3a5c348335347e809e173d49d0309008324a2b7cce1f9961dccdb10cf88081`; original current request: `9cbacfc976782a9eff80b7ebe389d6c9c8d6ebe311251ec08a0ebab130ad4cde`, still awaiting judgement. New root: `bd92f9f4917798afcd3714fd7ae640fbde14acc0345e52c3ad39ddf495e5ceaf`; new signed request: `bb8c0b5debf7277b61482e77bf3bb694ee02096398cdce6c259f4c86b31bd75f`.

## Bindings and limits

HTTPS base `1e9c9b72ea37685da2a5d390f41d2addc277881b` (v1.26.80). Original run source `344f09e0bec11c888cead926c6cff3f9242140d2` / tree `e6a1091ee3a74e7703356080f654550f28feffce` remains clean. All 10 INPUT copies and all 25 source/manifest/pad files were verified.

Native localization uses actual preserved 0.3.85 OCI `sha256:70ebc4fba7b456855f8711b70ffe0b94dff7152e5c354545201dc7a486ab469c`, not published 0.3.87. Icarus 14.0 devel `1d2f5f610`; Verilator 5.053 `d52e26d2d` inventoried but not used for the proof. PDK real root ends in `b344c97eacc2aaf8e14ae7e43e2e9dc0871de2c0/gf180mcuD`. Exact model/binary/source hashes and commands are in the evidence packet.

Each native container uses `--skip` as its first entrypoint parameter, cpuset 14,15, two CPUs, 4GiB RAM with no extra swap, 256 PIDs and network none. Original sources are read-only. Terminal CIDs, admitted host PIDs, final PID zero, tool return codes and resource configurations are retained. No full suite, narrow landing gate, physical run, RCX, evaluation or full IC restart was executed.

Corpus sweep NOT_MEASURED: the finite brief forbids cohort/corpus reads. Broad source acceptance and independent two-source review remain NOT_READY, not a zero-FP claim. The existing full-IC flow did not run on this candidate. The Phase-1 verification aggregate was not run, so no aggregate verification PASS is claimed.

Remaining IC blockers: original pad functional FAIL; incomplete signatures/RTL and delivered ISA/firmware evidence; off-chip input drive NOT_MEASURED; native ten bond-pad cap violations unwaived; no current GDS/final LVS before Step-32 HOLD; remaining physical/tapeout tail not run. Native setup +0.374612ns and hold +0.085524ns remain preserved-run observations, not new signoff. SRAM FITS/42 pads/40 signals/zero unbonded is likewise preserved-run evidence.

## Layer Contract Decision

Producing layer: L8 design-input submodule contract sections.
Consuming layer: L9 `integration.submodule_contracts`.
Consumer program: `programs/phase1_expert_parse_track.py` resolves the named field for the independent input-derived expectation.
Boundary class: structural carriage of explicit prose; no behavioral synthesis or wiring is inferred.
Declarative alignment: n/a for prose preservation; no new IP-XACT or SystemRDL wiring semantics are asserted.
Bucket A: the existing deterministic producer owns the loss of heading descendants.
evidence: `producer_red.log`, `producer_green.log`, `producer_reverse.log`, `d1-final-review.json` in the evidence packet.
**Verdict**: CONTRACT_OK for this bounded named carriage; broader acceptance remains NOT_READY.

## Capture

The benchmark-enhancement-capture program emitted one Bucket-A candidate under the canonical Phase-1 ingestion route. The actual implemented correction is in the carry-through producer, called from the Phase-1 document runner. The emitted sketch was not applied or treated as a second implementation. Capture record and terminal rc0 are retained; no oracle/golden/harness or cohort/corpus was read.

Next: /vibe-ic-phase1 after root's independent review and dependency landing, followed by root's one authorized fresh full-IC run. This handoff authorizes no HOLD release.
