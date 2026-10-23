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

## A01 structured analog specification component

`--entry-step A01 --exit-step A01` calls the existing
`analog_a1_spec_emit.py` producer and `analog_a1_spec_extract_check.py` consumer.
It does not start topology generation, SPICE, PVT or layout. Use the declared
IC/IP endpoint and a nonempty structured analog block list with attributed L5
specifications. Existing unbound/human-owned specs are preserved, and do not
become fresh production merely because the old gate accepts their shape.

The Python tool use also requires `semantics.operation_sources`: the exact
portable path-to-SHA256 map returned by `flow109_analog.operation_sources()`.
That map binds the adapter, producer, consumer and existing transitive source
closure. Source changes invalidate this qualification scope before launch.
The adapter checks D03 admission again at each actual subprocess call and
binds consumed L1/L5/L9/block-list bytes, current outputs and actual gate JSONs.

Each attempt is preserved under `reports/flow109/attempts/A01-*/`; the latest
component view is `reports/flow109/components/A01.json`. A completed native
component returns CLI success with `component_status: PASS`, while
`flow109_acceptance` remains `NOT_MEASURED`: the two A01 metric obligations and
G1-G9 audit have not been implemented by this dispatch adapter. Qualification
contract fixtures in developer tests are not production qualification records.

## D44 final adopted-netlist equivalence component

`--entry-step D44 --exit-step D44` uses the installed LibreLane `Yosys.EQY`
step and the existing `librelane_eqy` recipe/partition consumer. A model/port
elaboration check rejects functionless/unknown instantiated cells. No second
full LEC or gate-level simulation substitutes for the actual EQY proof.

`input/flow109/D44.json` binds reference RTL, the final adopted netlist,
functional Liberty, explicit LibreLane PDK/library/root, and STA/LVS-reference/
release netlist sources. This tranche supports identical logical bytes and no
power/physical-cell/port transformations. An unqualified representation change
is `NOT_MEASURED`, never a reason to drop cells or ports. The native generic
PDK preprocessing branch uses Icarus; LibreLane's other formal-PDK helper
branch requires its own qualified tool use and is not silently substituted.

All per-use identities bind `flow109_final_lec.operation_sources()` in
`semantics.operation_sources`. The adapter checks admission again at actual
model/proof launch and retains exact native configurations, inputs, source
hashes and partition evidence. Every declared EQY partition must have a result;
unresolved/unknown or gold-X partitions cannot produce component PASS.

A proven component publishes
`adopted/final_lec/sta_lvs_release_netlist_mapping.json` with the common logical
netlist source and proof identity. This prepares the downstream source chain;
it does not claim that STA, LVS, or release consumers have run/adopted it.
`flow109_acceptance` and `baseline_adoption` remain `NOT_MEASURED` until their
individual metric/consumer bindings are implemented.
