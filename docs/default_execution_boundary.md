# Default execution boundary

Source audit: main `59cd75606885b71ca7d11bbe34baf2541671be68` (2026-10-03).
No administrative packet-seal, handoff-custody, or 1179-receipt admission
requirement was found on the normal execution path. No runtime code was removed.

The authorized policy keeps the canonical 70-row DAG and its applicable IC/IP
conditions: Default uses one existing integrated primary producer per step;
Ultra alone fans out competing alternatives within a step. AI routes at Phase 1
and assists/reviews the selected producers; it does not invent or reorder steps.
The [canonical flow](../vibe-ic-marketplace/plugins/vibe-ic/flow/phase1_phase2_phase3.yaml)
and [IC Expert operating map](../vibe-ic-marketplace/plugins/vibe-ic/agents/ic-expert-agent.md#L72)
remain the routing authorities.

## Existing execution path

Paths below are relative to `vibe-ic-marketplace/plugins/vibe-ic/`.

| Boundary | Current source callsites |
| --- | --- |
| User entry | [commands/vibe-ic-phase1.md:18](../vibe-ic-marketplace/plugins/vibe-ic/commands/vibe-ic-phase1.md#L18) invokes the normal Phase-1 runner. |
| Benchmark routing | [programs/benchmark_dispatch.py:6059](../vibe-ic-marketplace/plugins/vibe-ic/programs/benchmark_dispatch.py#L6059) validates all issued AI task-nature responses before launching design runners; `:5778` builds the ordinary solver arguments. This existing routing contract remains required. |
| Phase dispatch | [programs/vibe_ic_one_shot_runner.py:324](../vibe-ic-marketplace/plugins/vibe-ic/programs/vibe_ic_one_shot_runner.py#L324) resolves phase runner filenames; `:641` launches them, with Phase 1 at `:1976`, Phase 2 at `:2299`, and Phase 3 at `:2404`. |
| Phase implementations | [programs/phase1_one_shot_runner.py:92](../vibe-ic-marketplace/plugins/vibe-ic/programs/phase1_one_shot_runner.py#L92) imports the document producer and `:763` calls its `main`; [programs/phase2_one_shot_runner.py:46](../vibe-ic-marketplace/plugins/vibe-ic/programs/phase2_one_shot_runner.py#L46) imports `design_one_shot_runner.main`. |
| Native tool selection | [programs/librelane_contract.py:1893](../vibe-ic-marketplace/plugins/vibe-ic/programs/librelane_contract.py#L1893) resolves implementation records, per-step switches, production/class defaults, then `direct`. Actual callers include [programs/design_one_shot_runner.py:13448](../vibe-ic-marketplace/plugins/vibe-ic/programs/design_one_shot_runner.py#L13448) and [programs/phase3_one_shot_runner.py:17291](../vibe-ic-marketplace/plugins/vibe-ic/programs/phase3_one_shot_runner.py#L17291). |

The native `direct`, `librelane`, and `dual` switches retain their existing
semantics; they are not aliases for Default/Ultra. The
[existing execution-mode contract](../vibe-ic-marketplace/plugins/vibe-ic/docs/execution_modes.md)
describes `execution_modes.py` as a standalone API with proposed runner hooks.
Its [portfolio metadata](../vibe-ic-marketplace/plugins/vibe-ic/programs/data/execution_modes_portfolio.json#L13)
records `NO_PRODUCTION_RUNNER_HOOKS`, zero registered production executors, and
`policy_globally_implemented: false`. This audit does not claim a completed
unified-controller rollout or measured EDA/signoff acceptance.

## Evidence and landing

Enhancement receipt population (including 1179), development packet seals, and
administrative handoff custody are progress metadata, not prerequisites to
Default execution or landing. A preparation-only assembler's refusal and a
unified-controller ledger's `0/70` or `0/1179` do not reset existing Default
progress. Proposed R0 route/activation receipts remain a planned integration
contract, not a requirement imposed by the inspected normal runners. This does
not waive the existing benchmark task-nature review shown above.

Existing typed, present, current, source-bound tool-output evidence and all nine
levels of artifact gates remain required for their applicable acceptance claims.
Existing source/input provenance, project locks, resource admission, and duplicate
run protection also remain in force: [programs/canonical_run_admission.py:123](../vibe-ic-marketplace/plugins/vibe-ic/programs/canonical_run_admission.py#L123)
binds real run identity and `:217` admits it. These are distinct from enhancement
receipt quotas. Root owns landing under the current authorization for exact
affected product/caller/ratchet checks and an ordinary fast-forward push; this
document adds no gate or authority format.

The IC path's [physical seal-ring step 26.5ic](../vibe-ic-marketplace/plugins/vibe-ic/programs/phase3_one_shot_runner.py#L44139)
is a layout obligation and remains unchanged. It is distinct from an
administrative packet seal.
