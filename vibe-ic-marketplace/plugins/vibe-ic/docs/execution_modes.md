# Execution modes: standalone contract and proposed runner hooks

This is the common execution seam, not a migration of the 70 producers.
`programs/execution_modes.py` remains the only scheduler and provider registry;
the five canonical front doors carry a typed request through
`programs/execution_policy.py`. Each controller run issues a frozen-work,
issued-arm, AI-comparison and program-adoption receipt chain. Domain adapters
register with the existing `Registry` and consume the existing `Controller` in
their own lane. No EDA result or IC signoff was measured in this lane.

Omitted mode is typed `PROGRAM_DEFAULT`/`default-mode` with `ultra_match=false`.
Only a live `--execution-mode ultra` issues `USER_EXPLICIT_ULTRA` and a
digest-bound request receipt. Environment labels, category metadata and child
flags cannot originate or upgrade Ultra; children preserve an issued parent
receipt through a live inherited capability endpoint or refuse. Every
production IC/IP context must supply an issued route receipt bound to the
request, project and source digests; the neutral controller fixture uses an
explicit `neutral-test` receipt and cannot silently inherit a production route.
The top-level runner keeps the live issuer object process-local and registers
the exact request digest; child interpreters have no mint capability.

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

The source run also issues `issued-plan.json` and an arm-local
`issued-completion.json` from the actual observed process completion. Its live
process keeps a separate immutable serialized transcript and signing key outside
the editable run directory. Adoption verifies both issuance and the process
transcript; replacing receipt rc/status/evidence and selecting its new digest
cannot upgrade a real nonzero execution. A signature alone cannot fabricate
source issuance. The original plan, native rc, logs and output proof remain
auditable. Gate evidence is still independently consumed: an issued rc0 grants
no design PASS, and measured FAIL retains precedence over NOT_MEASURED gates.

This issuer is scoped to one interpreter lifetime and source-owned trusted code.
Another controller in that process can consume the same issued run; restarting
the interpreter loses authority and fails closed. Persistent external supervisor
authority and cross-process key/lease management are unimplemented. These
process transcripts do not claim protection against arbitrary hostile Python
code executing inside the issuer process.

Process rc0 and file existence never establish eligibility. Actual measured
`FAIL` gates yield `FAIL`; missing/failed execution, stale/unbound evidence and
`NOT_MEASURED` gates stay `NOT_MEASURED`. Default mode executes the one arm
selected by the program's priority policy and automatically supplies a
program-issued choice witness to the same sealed `Controller.adopt` chain; a
successful default run therefore returns terminal `ADOPTED` without a choice
file, while a failed or unmeasured arm remains unadopted. Ultra mode runs all
useful distinct families and returns `AWAITING_AI_SELECTION` only when eligible
arms are available. The AI then supplies `arm_id`, the current binding, exact
receipt digest, reviewer identity and rationale to `Controller.adopt`.
Missing choice, changed inputs/outputs, incomplete gates, unmeasured or
ineligible candidates, wrong source or changed tool identity refuse. Adoption
reconsumes the actual output validator and persists `adoption.json`; it records
adoption without publishing into the project's native outputs. This is an API
blocking boundary, not a claim that a currently unmodified runner stops here.

Adoption also checks the currently relevant applicability, qualification,
availability, license-seat/resource declarations and selection policy against
the source-issued original run. These are declared controller admission checks,
not proof of an actual production license lease. The same checks, source/current
input/frozen input/output hashes and issued completion are checked again after
the source validator returns. A validator may pause; its earlier hashes cannot
authorize bytes another thread changed during validation.

The exact validated artifacts are copied into a unique `selected/<generation>`
directory, with a source-issued manifest binding run, arm, inputs and output
digests. The generation and relevant current identities are checked before the
atomic adoption record is committed. `selected_generation` identifies those
captured bytes, rather than leaving the record pointed at mutable candidate
outputs. Read-only files plus issued hashes expose later disk changes; they are
not a host security sandbox. Any future native import must consume the exact
generation and verify its manifest/digests. No such native import is added here.

Arm IDs reject dot traversal and the controller-reserved plan/result/adoption,
refusal, issued-plan and selected-generation names. Legitimate names such as
`neutral.a` keep their existing output layout. Unavailable output directories or
adoption records return named finite refusals, rather than raw filesystem errors.

## Adapter composition and explicit remaining work

The front doors now add the independent execution-policy request flags and
propagate them to children. Adapter owners resolve native identity through the
existing implementation switch and build a `Context` from the current frozen
project inputs, objective, canonical gates, output contract and
`execution_policy.controller_fields()` with an explicit issued IC/IP route.
The resulting `Context` rejects empty, mismatched or digest-invalid route
receipts. Their source-owned adapter must freeze complete transitive
tool/PDK/config inputs, bind the deployed container/image and
license probe, and consume each native report through its real gate. Component
command rendering must use the arm-local inputs and outputs; dependent producer
segments cannot run out of order or reuse a mutable shared upstream directory.

The owners can then call `plan`, `run`, obtain an actual AI decision on eligible
Ultra receipts (default mode program-adopts its sole eligible arm), call
`adopt`, and transactionally import the selected outputs through
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

The initial review's two hash-bound original proof scripts are rerun unchanged
against the original reviewed source and the repair: 14 original negative
failures and 11 adjacent positives become 25 passing controls. They cover actual
rc7 receipt rewrites, currently withdrawn admission, four event-synchronized
validation races and five colliding arm names. The original 53 author assertions
and fixture bytes remain unchanged. Appended source controls additionally
challenge fabricated issuance, current resource/policy changes and the exact
committed artifact generation. These are neutral subprocess/controller proofs,
not additional EDA adapters or production rollout evidence.

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
