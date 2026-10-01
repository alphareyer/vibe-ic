# F2 interface under the root typed API direction

Dependency: F1 `execution_step_protocol.py`, commit
`f1997a38ee6922a301bb7e7d1160df23505c3cd3`, SHA256
`8c9195780a30930e36ad99e78513783400ce9af5f39cf4f60dc4ae832e656799`.
It and the four supporting F1 modules (`execution_native_worker`,
`execution_policy`, `execution_production`, `execution_resource_lease`) are
externally installed read-only dependencies, outside F2's functional diff.
F1 owns registration and the runner hooks. Earlier ad185 receipts remain
historical evidence of their exact source versions.

Factory: `execution_adapters_frontend.prepare(request)`; helper:
`execution_frontend_worker.prepare_frontend(request, PreparedStep)`; consumer:
`execution_frontend_worker.consume_frontend(project, context, controller, run,
adopted)`. Every returned execute result declares `adoption_paths`, the precise
directories/files whose canonical contents the consumer may import. Consumer
scratch, Docker receipts and generation directories stay outside the project.

F1 must build the common `source_files` manifest first. F2 uses that exact map
unchanged in every Adapter, Context and worker request. Context's read-only
`source_files` property exposes the exact immutable `sources` tuple as a map;
it never stores an independently mutable manifest. Required files are
published by `execution_frontend_worker.source_files()`: non-test program
sources/data, canonical flow, consumed agent sources, host Python and Docker.
The F1 factory and delegated worker paths must appear. Missing hashes refuse;
F2 never adds files to the supplied manifest.

Common parameters: `route` (DIE/HARDMACRO), nonempty `objective`, actual `top`
(except D1/0.5ic), `native.image_id` (immutable local sha256 image ID), and
`native.tool_files` (absolute in-image binary paths to exact SHA256).
`external_input_roots` optionally maps named complete external input trees to
absolute paths. Native mounts use the frozen copies, never a live PDK tree.
`input_roots` is required separately for each step: a mapping of input labels
to their exact absolute lexical Paths, for example
`{'phase2/stage1/rtl': project/'phase2/stage1/rtl',
'phase1/generated_docs': project/'phase1/generated_docs'}`. A sequence of
relative names is also accepted. Labels may differ from relative project
paths. Internal inputs retain their actual project locations; external
declaration/native-facts controls retain their absolute lexical paths,
direct aliases and complete populations, and are mounted from frozen copies
at those absolute paths. There is no global population fallback.
Missing names, empty directories and alias identities remain bound. F1 must
include every actual consumed design/model/declaration/report root. Canonical
required-input alternatives must have a current file in that bound population;
missing models or an explicitly consumed mutable output refuse. Gate-output
qualification patterns are excluded only from execute inputs. Dispositions
bind their current declaration within the complete supplied input roots.

`native_facts` is the issued parsed dict; `native_facts_file` is its canonical
Path. F2 checks their equality and binds the current file digest, then freezes
those bytes as `native_facts.json`. It does not interpret the dict as a path.
Typed Path facts are serialized explicitly for the immutable worker request.
Project-local producer paths are remapped to the private frozen project.

Controls `record` and `lease` are outside the current input project. `lease` is
a directory with `lease.json`. F2 consumes F1's actual `cpus`, `host_ram_mb`,
`container_ram_mb`, integer `parent_pid`, and canonical positive decimal-string
`parent_start_ticks`; F1's actual `/proc` start-time helper detects PID reuse.
F2 neither issues a lease nor demands the superseded fictitious `owner_pid`,
`native_ram_mb` or `native_admission` fields. The actual frontdoor `timeout_s`
bounds its native process and host supervisor. Docker memory and memory-swap
are equal, so there is no additional native swap allowance. F1 alone must bind
this lease and exact argv to real capacity admission; matching a live PID or
parsing facts does not establish that admission. The final F1 issuer/publication
and separately admitted native evidence remain dependencies. No domain creates
placeholder admission/deadline facts or derives admission from source budget.

Row-specific parameters come from the actual phase front door:

| Rows | Parameters and existing producer |
|---|---|
| D1 | `design_one_shot_runner.step_phase1(project)` |
| 0.5ic | Owner answers input; staged `template` or owner `no_template_reason`; ingest then declaration generator |
| 1 | `ic_class`; `step_rtl_gen(..., force_regen=True)`; AI handoff remains incomplete |
| 2 | `baseline_rtl_dir`, `clock`, `timeout_s`; actual native frontends and crosslayer equivalence |
| 3 | Existing CDC crossing, async-input, reg-crossing and reset producers |
| 4 | `ic_class`, bounded `build_jobs`; professional/reference/unit TB, Verilator measure-tb |
| 5 | `timeout_s`, `mem_limit_kb`, `prove_engines`; deterministic harness, actual formal proof and full-stack simulation |
| 6 | Actual Quartus compile then map audit; absent software admission is a refusal |
| 7/10 | Complete existing `PdkConfig` in `pdk`; existing ASIC SDC emitter / pre-layout signoff callable |
| 8 | Existing SDC syntax and declaration validators |
| 11 | `netlist`, `clock`, `pdk_name`, `liberty`, `cell_model`, `timeout_s`, optional declared reset/polarity |
| FS1 | Owner-declared `asil`; actual injection producer and coverage consumer |
| DT1 | `clock`, `netlist`, `liberty`, `timeout_s`, `sat_timeout_s`, `max_faults` |
| 12 | Exact existing `read_verilog; opt_clean -purge; write_verilog -noattr` producer, scan survival consumer |
| 13 | `lec_max_completed_rungs`; current router-selected LEC subject, never a substitute |
| DT2/DT3 | `clock`, `netlist`, `sdc`, `spef`, `liberty`, `timeout_s`; current SAT/timing grades |
| P0 | `structural_claims` from input; existing native frontend output feeds existing `formal_structural_check.check_claim` |

For inapplicability/physical handoff, F1 must explicitly provide `declaration`
as the exact current canonical Path (or explicit os.fspath-compatible path).
Other types refuse. F2 returns its digest plus nonempty observed
facts. DFT absence uses the canonical L20/input evaluator. HARDMACRO route uses
the canonical tapeout declaration check. Step 6 may disclose physical handoff
only when a current owner-authored declaration states `board_present: false`;
this stays NOT_MEASURED and is never a native software fallback or PASS.

`execution_frontend_worker.adoption_paths(step_id)` publishes concrete relative
directories/files, including the actual gate report destinations. Patterns are
bounded by their concrete directory prefix; wildcard text is never a journal
destination. The consumer verifies every copied hash belongs to the live
Controller-issued generation, then runs the existing canonical semantic
consumer on its isolated bytes before held-directory CAS publication. The
existing transaction helpers retain old subtrees for rollback. Prepublication
refusal does not restore or overwrite concurrent work. Historical provenance
bytes are immutable upstream input; the current journal checkpoint advances
only with this selected transaction and rolls back with it.
F2 preserves timestamps for old entries not written by the selected generation
when the existing helper swaps a whole directory; an intervening owner metadata
change refuses publication before that swap.

The returned result is `IMPORTED`, with `step_id`, `source_sha`, issued
`selected_generation` (the full issued object), current `binding`, nonempty
`copied`, actual `primary_gates`, unchanged `design_verdict`, and fresh
`consumer_detail`, using F1's actual `imported_result()` helper. Its
`json_parameters()` helper performs typed snapshot encoding. A nested `CONSUMED`
detail remains descriptive. A blocking consumer FAIL refuses publication and
retains that FAIL in the refusal detail and original producer/evidence receipts.
The refusal code for a measured blocking consumer failure is `GATE_FAIL`,
which retains FAIL through F1's dispatcher and rollback receipt. Missing,
unbound, stale or unexecuted evidence stays NOT_MEASURED. Partial unmeasured
metadata cannot erase a measured FAIL.

Per-program PASS now requires a current actual canonical execution-ledger
record with substantive PASS and raw exit zero. Aggregate canonical PASS
cannot certify a conditional program that did not execute or erase an advisory
failure. The frozen portfolio's treatment of skipped/advisory programs still
requires F1 coordination; these states remain NOT_MEASURED in the domain.

The owned `tests/fixtures/execution_frontend_step7_software.py` fixture
exercises the real Step7 emitter, current-record and SDC consumers, live
Controller-issued selection, F1 factory/import and F2 CAS transaction under
an explicitly limited software SDC contract. Its scoped portfolio does not
modify or satisfy the canonical full-Step7 portfolio. Its neutral Liberty
input has no physical views; PVT, native EDA and full Step7 remain OPEN.

Both new flat modules use the existing issue2104 programs-directory bootstrap
before sibling imports. The existing root-approved selector is reserved for the
final narrow; no early sweep or extra review is requested.

No new shared API test, review or proof wave is requested. The native capacity
request is `evidence/native-capacity.request.json` in this task's delivery root.
