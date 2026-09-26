# Phase 3 LibreLane contract (T83)

`programs/librelane_contract.py` is the opt-in adapter for a migration lane. The existing Phase 3 runner remains the default for every step until a lane declares `phase3/librelane_switch.json` with `{"steps":{"15.5ic":"librelane"}}` or `"dual"`. `selected_mode(project, step_id)` returns `direct` when the file or key is absent and refuses unknown modes. Each migration lane must connect this decision at its own step's producer call site, run the existing vibe-ic gate on the selected output, and retain both arm directories. The T83 proof invokes the adapter directly; it does not switch production step 15.5ic.

## Invocation and state

The adapter invokes the installed LibreLane CLI, `python3 -m librelane.steps run --id <StepId> -c <resolved-step-config.json> -i <state_in.json> -o <project>/phase3/librelane/<nn>-<step-id>/`. This is the exact `librelane/steps/__main__.py` interface in the candidate image. Its `Step.load` reads a resolved **per-step** config, not the initial flow YAML, and its `Step.start` writes `state_out.json` into the output directory. The state carries absolute paths for ODB, DEF, `nl` netlist, SDC, optional SPEF and other views, plus the cumulative `metrics` mapping. The next step receives the previous step's `state_out.json` as `-i`; the first step receives a pinned initial state. Mount the project and every read-only source at the same absolute paths in the container. The adapter verifies path existence on the host before invocation.

Each step directory holds LibreLane output, `input_fingerprint.json`, `invocation.log`, and `vibeic_receipt.json`. The fingerprint includes image name, config hash, state JSON hash and every referenced state-file hash. A matching complete receipt resumes without re-running; a failed or changed step is archived under `phase3/librelane/attempts/` and re-run. Later steps use the newly emitted state. Receipts hash `state_out.json`, local geometry/views, JSON reports and `.rpt` reports. The `run_chain` caller supplies the existing image override candidate; image names are pinned per run.

Before any step the adapter runs the image's LibreLane CLI help and **actually executes** `yosys -Q -T -y /dev/null -p help`. A nonzero result raises `LL_IMAGE_INCAPABLE`; it never selects the direct path in response. `0.3.76` fails this probe; `0.3.71-librelane-rc11` passes. No candidate image release is implied.

## Config and provenance

`emit_config(project, pdk, output)` reads only the design's `phase1/generated_docs/L8_TIMING_WAVEFORM.json`, `L19_CONSTRAINTS_PDK.json`, `input/submission_template/tapeout_declaration.json`, and the producer's `phase3/stage3/pnr/pad_assignment.json`. It writes JSON and a sibling `.provenance.json` containing the source field for **each emitted key**. It selects one primary L8 clock scoped to the requested PDK, L19 declarations at the highest matching scope/priority, the declared die/core, and PAD side order. Conflicting equally authoritative declarations refuse with `LL_CONSTRAINT_CONFLICT`. Undeclared values remain absent.

The emitted document is a design fragment to overlay on LibreLane's resolved, PDK-filled step config. It cannot independently replace the hand-written full-flow YAML until the remaining hand-authored settings have declared input sources. In particular, missing transition/capacitance DRV limits, power-net names, custom PDN Tcl, ring settings, excluded cells and fill options are not inferred from a clean result. A migration lane may add them to an input declaration with provenance, then extend the emitter; it must not copy numbers from t78 metrics or a golden run. The T83 PadRing proof overlays the 16 emitted values relevant to its resolved step config, retaining the reference's staged source paths for a same-input replay; this isolates the step contract and is not a claim of full-flow config replacement.

## Gate and dual arm

`judge_step` consumes `state_out.json`, an optional LibreLane `metrics.json` snapshot, and named reports. It records hashes and applies declared metric bounds. Absent metrics or reports are `NOT_MEASURED`; a measured bound violation is `FAIL`. A present metric alone is not signoff. Migration lanes must map their existing vibe-ic gate's exact required metrics and limits to this reader, including geometry/report checks appropriate to the step. The proof uses padcell count and power pad count; it does not claim complete step 15.5ic signoff.

For `dual`, `execute_dual` calls the LibreLane and direct OpenROAD producers with separate `phase3/tool_arms/<step>/librelane/` and `.../openroad/` directories. Each producer writes a gate report there; the function refuses a missing or escaped report and retains both directories. `select_arms` uses `_ppa.pareto.dominates` on declared objectives and the same measured keys. Both gates must say `PASS` and carry identical nonempty scope maps. A missing measurement or failed gate yields `UNDETERMINED`; trade-off/tie yields a retained frontier and `UNDETERMINED`; only one dominating arm is selected. Both arm receipts survive. A later lane must also enforce the PPA module's full unit and feasibility contract before comparing dissimilar producer paths or signing off a selected arm.

## Refusal IDs

`LL_IMAGE_INCAPABLE`, `LL_CLOCK_AMBIGUOUS`, `LL_CONSTRAINT_CONFLICT`, `LL_INVALID_JSON_OBJECT`, `LL_INVALID_SWITCH`, `LL_STATE_MISSING`, `LL_STATE_FILE_MISSING`, `LL_STEP_CONFIG_MISMATCH`, `LL_STEP_FAILED`, and `LL_ARM_REPORT_MISSING` are explicit failures. `LL_ARM_NOT_MEASURED`, `LL_ARM_SCOPE_MISMATCH`, and `LL_PARETO_TIE` are selector reasons. No refusal silently falls back to the old backend.

## T84 physical-step replay

`OpenROAD.Floorplan` accepts a synthesized `nl` input State and creates the
initial ODB, DEF, and SDC. Later physical steps still require those views.
The verified spm replay used Floorplan, DumpRCValues,
CheckMacroAntennaProperties, SetPowerConnections, PadRing,
ManualMacroPlacement, CutRows, TapEndcapInsertion, AddPDNObstructions, and
GeneratePDN. The intermediate steps are actual dependencies in the clean t78
flow. All five target steps' metrics and ODB/DEF bytes matched t78.

`pad_ring_check.py --librelane-state <PadRing/state_out.json>` runs the
existing ring geometry audit on LibreLane's DEF, the design's pad assignment,
and the PDK IO LEF. It does not invoke `pad_ring_gen.py`. A missing declared
pad, wrong side, unresolved footprint, open filler gap, or uncovered BTerm
fails the gate. The normal direct-path gate remains available for designs
without the step switch.

The emitter now carries the pad producer's declared site, corner, filler,
edge spacing, and rotation settings as well as side order. It records each
source field and refuses invalid edge spacing. It still leaves PDK-only site
geometry, library views, PDN sizing, and excluded masters to their actual
declaration or tool config; a t78 metric is not an input source.
## Step 9 synthesis candidate

Step 9 opts in with `{"steps":{"9":"librelane"},"image":"<LibreLane-capable image>"}`. The runner selects the same staged RTL files as its direct synthesis path, including package ordering and include-hub filtering. It emits `VERILOG_FILES`, `VERILOG_DEFINES`, `USE_SLANG`, resolved PDK/Liberty inputs, sparse FSM preservation attributes and the FSM encoding-table request with input provenance. It resolves the PDK config, then runs `Yosys.JsonHeader` and `Yosys.Synthesis`. The mapped netlist and native `stat.json` remain linked by the LibreLane receipt. A binding gate compares native stat count/area to the emitted area-gate input and state metrics, and verifies the native netlist hash. The area budget, mapped-netlist, PDK consistency and provenance gates run on the selected output; any nonzero gate status fails this candidate.

This candidate requires the `vibeic/librelane` FSM hooks before LEC can use its encoding table. A local test can set `development_librelane_source` and `pdk_root_host` in the switch to mount a read-only fork source and PDK. Those are development inputs, not release evidence. Production remains `direct` when step 9 is not explicitly selected. `dual` fails closed until both isolated arms have comparable routed results and LEC proof.

`programs/_ppa/synthesis.py` runs the nine LibreLane `SYNTH_STRATEGY` arms through pre-PnR STA. Its reports are exploratory proxies. `select_postroute` admits only same-scope routed reports with proved LEC, a passing area gate and required measured power; a proxy alone never chooses an arm.

## Steps 7, 8, 10, 12, 13, 14 (T92, opt-in)

Steps 7, 8 and 10 opt in per step (`"7"`, `"8"`, `"10"` in the switch, with `image`). `step_prelayout_signoff` keeps vibe-ic's spec-derived SDC as the design deck, then `librelane_prelayout.run_prelayout` runs `OpenROAD.CheckSDCFiles` and `OpenROAD.STAPrePNR` on `<synth>/<top>_synth.v` with that deck as `PNR_SDC_FILE`/`SIGNOFF_SDC_FILE`. Step 10 (`librelane`) publishes the tool's corner reports as `per_corner/sta_<LABEL>.rpt`, stamped `STA_BASIS: PRE_LAYOUT_ESTIMATE` with `STA_BASIS_LIBERTY`, and recomposes `pre_pnr_timing.rpt`; `dual` keeps the direct reports and writes the tool's under `phase3/tool_arms/10/` as a cross-check. `judge_slack` fails a corner whose `sta.log` shows OpenSTA creating a black box and treats inf or absent worst slack as `NOT_MEASURED`. Step 8's `judge_sdc` fails on any OpenSTA diagnostic raised while reading the deck, on any `check_setup` untimed count, and on a run that timed `FALLBACK_SDC`. With step 8 switched, `sdc_syntax_check` reads that record, bound to the deck's SHA-256, instead of (`librelane`) or beside (`dual`) its regex. Step 7 writes `pvt_matrix.json` from the resolved `STA_CORNERS`; `dual` on a runner auto-SDC also times LibreLane `base.sdc`, rendered from the same declared clock and I/O-delay percentage, and compares untimed counts with `select_arms`. A tie keeps the spec-derived deck.

`run_chain` fingerprints every file a step config names, not just the config JSON. Before this, editing the SDC or an EQY script reused the stale step.

Step 13 (`"13"`: `librelane` or `dual`, plus `image` and `pdk`) keeps `lec_run` as arm A and adds `Yosys.EQY` as arm B through `librelane_eqy`. The generated `EQY_SCRIPT` drops `bitwuzla`, reads gate cells from the resolved liberty's functions (gf180mcu's Verilog models are UDPs that Yosys cannot parse), uses `-icells`, and defines initial values (`setundef -init -zero`). The reader judges each partition's strategy status files and refuses a PASS on partitions EQY tags `xbits`, because its miter treats a gold-side x as don't-care. `combine` keeps the conclusive arm, and a counterexample in either arm fails the step. When arm A's subject is not the mapped netlist PnR consumes, EQY also proves `<top>_synth.v` (`reports/lec_eqy_handoff.json`). The released 0.3.77 image ships no EQY plugins, so `development_eqy_overlay` mounts locally built v0.69 plugins. That is development evidence only.

Step 14: the step-9 tool path also runs `Checker.YosysUnmappedCells`, `Checker.YosysSynthChecks` and `Checker.NetlistAssignStatements`, then `synth_handoff_netlist_check` on the tool's netlist. That gate fails on literal constants and on undeclared tie cells. Step 12: `dft_post_optimization_scan_survival_check` also fails when ports published in `scan_chain.json` vanish. `_ppa/post_dft.py` runs `Yosys.Resynthesis` strategy arms on the scan netlist and admits an arm only when scan survival and equivalence both pass.
