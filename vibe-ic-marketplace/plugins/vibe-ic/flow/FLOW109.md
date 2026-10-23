# 109-flow implementation entry

The existing general front door now accepts an explicit `--flow 109` selection.
`flow109.json` owns the stable responsibility IDs, four phases, seven stages,
Boolean selection, per-row Default tool profiles, source-bound old-70 mapping,
and the 277 metric specifications. `G1` through `G9` name the nine gate layers;
`L1` through `L27` remain specification documents. No old matrix result is
credited to the new rows or gate layers.

## Resolve a plan

Pass a JSON manifest containing an explicit `features` object. Every
non-derived feature must be Boolean; absent or unknown features are refused.
Derived features are computed from the registry and contradictory declarations
are refused. The registry includes reference profiles for implementation tests;
they are not evidence that a project's features have been independently checked.

```
python3 programs/vibe_ic_one_shot_runner.py PROJECT --route ic \
  --flow 109 --flow109-manifest INPUT_MANIFEST.json --flow109-plan-only
```

The result is `reports/flow109/plan.json` with `NOT_MEASURED` execution and
acceptance states. This operation does not invoke EDA or reinterpret old PASS
receipts. Source-owned public Python APIs are `resolve_plan`, `resolve_step`,
`resolve_execution_window`, `execution_context` and `validate_execution_context`
in `programs/flow109.py`.

## Execute an existing native component

The first tranche binds selected backend responsibilities to their real
existing producer and complete canonical consumer. For example:

```
python3 programs/vibe_ic_one_shot_runner.py PROJECT --route ic \
  --flow 109 --flow109-manifest INPUT_MANIFEST.json \
  --entry-step D38 --exit-step D38 --no-dashboard
```

This dispatches the existing Default CTS window with its original execution ID
and receipts. Qualification must succeed both at admission and at the actual
producer boundary. The manifest supplies `runtime_tool_uses` and a
project-relative `qualification_registry`; source-owned use IDs are returned by
`resolve_step`. The admission module owns the exact identity/evidence schema.
An unavailable admission implementation refuses before native dispatch.

The request is preserved under `reports/flow109/requests/`. It binds the
registry, planner, selected features/profile, responsibility, runtime tool
identities and qualification path. Child execution receives the explicit
`VIBEIC_FLOW109_CONTEXT` path. The source resolver revalidates this context;
ordinary 70-flow invocation clears an inherited 109 context.

Only a single explicit mapped component is dispatchable in this tranche.
Unmapped rows, including the new ODB metal-fill and final-geometry acceptance
responsibilities, return `FLOW109_PRODUCER_UNIMPLEMENTED`. Translating an entire
new span into an old span would silently execute a different dependency order,
so multi-row execution remains `FLOW109_WINDOW_UNIMPLEMENTED` until its actual
adapters are implemented. This boundary can be extended as implementation lands.

Native component PASS remains scoped to the existing consumer. The new metric
bindings, G1-G9 checks, full route acceptance and tool/profile qualifications
have independent statuses; this registry does not establish 109/109 PASS.
