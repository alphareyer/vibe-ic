# Declared design subject must exist before wrapper emission

## Summary

Two bounded Bucket A recoveries augment existing producers: declared-top
identity and primary prose clock binding. Neither adds a flow step or gives
whole-IC acceptance credit. The generic capture routing subtypes name the
actual program owners and preserve the existing wrapper-generator mapping.

Program-first enhancement, Bucket A: augment the existing wrapper producer in
`programs/design_one_shot_runner.py::_autoemit_chip_top_wrapper`. An explicit
L9 top different from the runner's wrapper sentinel is a binding subject. If
no staged module defines it, refuse with `CHIP_TOP_SUBJECT_MISSING` and retain
the normal RTL-authoring handoff. When the subject appears later in a
multi-module file, prefer it over the filename heuristic.

The canonical IC/DIE Default Subservient/gf180mcuD attempt at base commit
`dc2dc7e1fa6d65baa3ed4cf89715b672c4e2404c` imported generic SERV but had no
declared `subservient` SoC top. The producer emitted `chip_top` around the
unrelated internal `serv_compdec`, losing the SoC interface. The first attempt
ended rc=1. The exact raw project and results remain frozen; this capture does
not change that verdict or claim whole-IC convergence.

Executable evidence used the frozen current-run L9 and catalog RTL: the base
producer wrote the wrong wrapper; the changed producer wrote none and named
the missing subject. One bounded pinned-image control run per arm measured
base 2 failed / 38 passed and candidate 40 passed. Four new controls cover
missing-subject refusal, a present subject in a multi-module file, the wrapper
sentinel, and an already authored wrapper. The unchanged surrounding wrapper
controls cover ordinary port and declaration behavior. No repeated run or
full-suite claim is included.

Portable artifact packet: `declared-top-identity-evidence-r2.tar`.
See `wrapper-guard.evidence.json`, `wrapper-guard.delta.patch` (SHA256
`ef49f02e8eac2f29722e5870d7a33eab2998fc78bb8ddceaccc0c54f404240ce`),
`source-base-controls.log`, `source-controls.log`, and `raw-attempt-001/`.

The existing catalog-glue-author handoff subsequently produced one new
input-derived SoC integration file, separate from 22 reused SERV/servile files
at upstream revision `7d9cde4e6ca4f4c84d7512a752a4c187537dd373` and two
disclosed upstream corrections. The benchmark's own shared-SRAM catalog
self-match was refused. Pinned native Icarus elaboration and delivered
1024-byte SRAM boot/store/GPIO/reset-retention checks passed; receipts and RTL
hashes are in `native-integration-001/`. This is bounded integration evidence.
Named blinky/hello inputs are absent; ISA and physical acceptance are unmeasured.

Remaining owners at capture freeze: Phase-1 producer for `clk` versus `i_clk`
binding if a producer test proves the defect; the independent input-only expert
and normal judgement interface for D1; the authorized in-image canonical LOCAL route for native continuation; reused RTL/author layer for the synchronous reset-glitch
contract. No generated L-doc edits, fabricated signatures, global transport
rewrite, version bump, or main push belong to this change.

2026-10-03 follow-up: transport scope is RESOLVED by the existing canonical
LOCAL route inside a fresh immutable pinned Docker run, with `--skip` first,
unchanged PATH, and no Docker CLI/socket mount. The external-container
`--require-image` option is omitted under root's explicit ruling; host
CID/ImageId/RepoDigest receipts bind the real image, and internal image
provenance SKIP remains SKIP. Transport is no longer waiting for root.

Portable measured evidence references:

- `wrapper-guard.evidence.json` — SHA256 `76a298852246cf1a8d066a516bf8e4c5ba277cacb4fc945aed0b34a049214870`.
- `wrapper-guard.delta.patch` — SHA256 `ef49f02e8eac2f29722e5870d7a33eab2998fc78bb8ddceaccc0c54f404240ce`.
- `source-base-controls.log` — SHA256 `430514735e8d1de577efc80d7b1b16e843258c879a7103b83c9ade4a2d23a6db`.
- `source-controls.log` — SHA256 `b87ffef9711cd8d4857f5776ac049f67423787859b9736f7f02ddf08aac5f9c7`.
- `raw-attempt-001/artifacts.json` — SHA256 `1ee27218d9e5dcb48ecdadbd3d109d074fd7faede7388492e74028cfd15f39c9`.
- `native-integration-001/result.json` — SHA256 `eb723268e626a7d0b03bade47a10dfe9ef65dbe4a68baa43fb4e4387a5055b9f`.
- `source-base-clock-artifact.json` — SHA256 `bd76dd67610d1c6d3ec30db4a281ef1730a6d7bfa4125c84574c21b0b73f0384`.
- `source-clock-artifact.json` — SHA256 `7d4ab87dbe970d56eec22bf93433ec1f8105d21935d78c6d9a7d1033a38b934d`.
- `source-base-clock-controls.log` — SHA256 `31ab27c2d8b0c560a14b60374c50a9a7525b64b6df3ab3b12b0c8e7acd7ed1c3`.
- `source-clock-controls.log` — SHA256 `c7a1fbfe10f722e8dbeb7e2026a947e6dfb11d7c81ae31997a426d5139736c60`.
- `source-base-clock-exit.json` — SHA256 `1f985f39a2559205f21e4c9c1baec84d016e32bff05b68454a2a0d76c46cc670`.
- `source-clock-exit.json` — SHA256 `29ce9e969c5856a7df2087c79eecd6d8c912a8f808d16bb6b995e5e1313fdda0`.

## Existing program reuse

The GF180 20 ns selection in the original run is `ALREADY_PROGRAM`: the
previously landed PDK-scoped rule propagated the original declared target
into L8, L9, L19 and stage-1 SDC. It is separate from the new clock-port
identity repair and is excluded from the two new Bucket A records.

## Next action

Continue the existing same-host canonical run with the two producer repairs,
consume the independent expert answer through the normal interface, and
report actual remaining findings. Retain incomplete D1, missing firmware,
unmeasured ISA/physical work and the original failed attempt as separate
evidence. Source landing does not require whole-IC acceptance.
