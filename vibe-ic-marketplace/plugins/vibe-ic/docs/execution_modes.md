# Execution modes: standalone contract and proposed runner hooks

This is a partially integrated global controller, not a migration of the 70
producers. `programs/execution_modes.py` is an executable Python API tested with
finite neutral text tools. No production runner calls it, and its production
adapter registry is empty. No EDA result or IC signoff was measured in this lane.

The public portfolio is `programs/data/execution_modes_portfolio.json`. It imports
the final source/policy inventory bound to main
`f88175263d96c3a76b4cb18f72c7714c68ec6627`, inventory SHA256
`d364f39fc37b77f5ba711cea7cab879345de56c157784cf847a66108ebc518bc`:
70 canonical step IDs, 63 tools, 333 arm descriptions. Private receipt paths and
Chinese fields are omitted. Public capability links and historical source
integration do not establish present installation, a license seat or an executable
controller adapter. Every arm says `controller_runnable: false`. Licensed
candidates explicitly require both a license and a completed adapter.

`current_default` describes the pinned existing source; `policy_default` describes
the policy target and retains its deployment limitations. A9 keeps its native
L22-selected ngspice / `d_cosim` primary with Icarus libvvp. A2 retains explicit
topology choice, A3 retains the own netlist producer with ngspice validation, and
A4 retains its current ngspice sweep. PyOPUS, Xschem, CACE and SpiceBind remain
proposals pending same-input target proof. The resource changes no producer.

## Policy API

`mode(None)` returns `default-mode`. Only the exact strings `default-mode` and
`ultra-mode` are accepted; invalid strings refuse. Native `direct`, `librelane`
and `dual` are separate implementation identities supplied in `Context`; they
are never interpreted as aliases for execution policy. An ambiguous native
identity refuses. Existing switches and class defaults remain with
`librelane_contract.selected_mode`.

An owning source adapter declares applicability, availability, qualification,
the exact tool version and source/binary file digests, engine families, an
ordered component command list, required output files, canonical-output
mappings and an actual output consumer. Registration verifies the source and
executable bindings. A portfolio description alone cannot register an executor.
Adapters and validators are trusted reviewed implementation code, not commands
loaded from a public JSON file or a security boundary against hostile programs.

`Controller.plan(context, execution_mode, superiority)` selects one applicable,
available, qualified primary in default mode: LibreLane, then OpenROAD, then
another qualified tool. Own code needs an explicit scoped no-applicable-tool
reason; an external adapter that is unavailable, unresolved or failed cannot
justify own fallback. There is no automatic rerun/fallback after a process error.
Ranking can be overridden only with complete eligible measured receipts bound
to the same input digests, source SHA, exact adapter/binary versions and declared
objective/metric/direction. The output validator is reconsumed for both arms.
Canonical required gates cannot be dropped; each ready arm must map every
canonical output contract to its required files. Glob/alternative resolution is
the source adapter's responsibility and needs executable producer evidence.

Ultra mode runs declared available useful independent producer arms through a
bounded weighted scheduler. Shared engine families are disclosed and excluded
as independent candidates, including an orchestrator using the same backend as
a direct tool. Complementary/checker roles stay outside candidate selection;
their obligations remain mandatory gates. CPU affinity and RAM address-space
limits bound each component; CPU/RAM reservations and license seats bound
concurrent admission. Components within an arm run in order. Each arm receives
its own copied inputs and output directory. Frozen and current input hashes are
checked before execution, between components and again at selection.

`Controller.run` writes the full portfolio, adapter admissions, objective,
budget and frozen binding in `plan.json`. Each arm writes `receipt.json` with
actual PID/argv/rc, monotonic process intervals, stdout/stderr and digests,
source/version identity, output digests and consumed gate evidence. Timeout or
explicit cancellation kills only the controller's component process group and
reaps it; partial logs/files remain. These finite-component deadlines are not a
proposal to change existing EDA progress watchdog or native tool-stop policy.
Address-space limits are per process, not a cgroup aggregate RSS limit; production
container/process-tree memory and license leases still require the owning runner.

Process rc0 and file existence never establish eligibility. Actual measured
`FAIL` gates yield `FAIL`; missing/failed execution, stale/unbound evidence and
`NOT_MEASURED` gates stay `NOT_MEASURED`. No run automatically selects an arm:
`result.json` reports `AWAITING_AI_SELECTION`. The AI supplies `arm_id`, the
current binding, exact receipt digest, reviewer identity and rationale to
`Controller.adopt`. Missing choice, changed inputs/outputs, incomplete gates,
unmeasured candidates, wrong source or changed tool identity refuse. Adoption
reconsumes the actual output validator and persists `adoption.json`; it records
adoption without publishing into the project's native outputs. This is an API
blocking boundary, not a claim that a currently unmodified runner stops here.

## Proposed integration and explicit remaining work

The phase runner owners should add an independent execution-policy request
field/flag, resolve native identity through its existing implementation switch,
and build a `Context` from the current frozen project inputs, objective,
canonical gates and output contract. Their source-owned adapter must freeze
complete transitive tool/PDK/config inputs, bind the deployed container/image and
license probe, and consume each native report through its real gate. Component
command rendering must use the arm-local inputs and outputs; dependent producer
segments cannot run out of order or reuse a mutable shared upstream directory.

The owners can then call `plan`, `run`, obtain an actual AI decision on eligible
receipts, call `adopt`, and transactionally import the selected outputs through
the existing adoption consumer. Every consumer must propagate refusals before
advancing. This lane proposes those hooks only; native dual-mode semantics,
image/PDK provenance, real EDA adapters, production AI dispatch, transactional
native-output import and all-70 rollout remain unimplemented here.

Shared writers retain `phase1_one_shot_runner.py`, `design_one_shot_runner.py`,
`phase3_one_shot_runner.py`, `analog_one_shot_runner.py`, the canonical flow YAML,
`CAPTURE_ROUTING.json` and `librelane_contract.py`. This lane edits none of them.

## Acceptance scope

`programs/tests/test_execution_modes.py` runs real finite subprocesses with an
explicit neutral text contract, not mocked EDA engines or fake production
adapters. It exercises concurrent intervals, serial resource admission,
isolated frozen inputs, ordered dependent components, actual rc, cancellation,
partial output, current source/digest checks and eligible-only selection.
A real-repository artifact test consumes the pinned canonical YAML and public
portfolio, preserves all 70 IDs, checks final A9 policy and rejects the empty
production registry. The expected-contract base probe observes concrete values
from the old native consumer on f881; on this branch it exercises the standalone
parser. It does not prove production integration. Reverse source mutations test
the substantive evidence guards separately.

## Layer Contract Decision — execution policy and portfolio

Producing layer: n/a — execution policy, environment availability and tool
portfolio are runner properties, not design facts in L1-L27.

Consuming layer: n/a — the standalone controller consumes the mode, current
frozen inputs, adapter registry and evidence; production hooks are proposed.

Consumer program: `programs/execution_modes.py` (`mode`, `Controller.plan`,
`Controller.run`, `Controller.adopt`).

Boundary class: structural — two enumerable policies, explicit executable
adapter contracts and binding checks. AI selection remains judgement over
eligible evidence; the program cannot author the choice or design objective.

Declarative alignment: n/a — this is not a register, interface or L-layer schema.

Actionable form required: exact mode token, current input/source/tool digests,
declared objective, output mapping, gate verdicts and receipt-bound AI rationale.

Bucket A (program): deterministic admission, execution and evidence binding;
the best-result judgement remains Bucket B (skill/AI).

evidence: `programs/tests/test_execution_modes.py` executes the controller and
consumes `flow/phase1_phase2_phase3.yaml`; `programs/librelane_contract.py`
`selected_mode` preserves the separate native implementation switch.

**Verdict**: NOT_A_LAYER_FACT

Next: /vibe-ic-phase1 remains the unchanged design front door; runner owners
review the proposed hooks after independent source acceptance.
