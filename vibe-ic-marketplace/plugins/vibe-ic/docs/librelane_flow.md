# `--librelane`: vibe-ic's steps after RTL on LibreLane (v1)

> **Status: NOT SHIPPED.** No runner accepts `--librelane` yet. This page is the contract the
> v1 work items build to, and each section names the item that makes it true. A section
> marked *planned* describes intended behaviour, not behaviour you can run. When an item
> lands, its section is rewritten to describe what shipped, with the program and test that
> hold it. The per-step LibreLane arms that already run on the default path are documented
> in [`librelane_contract.md`](librelane_contract.md).

## What the flag is

`--librelane` runs the implementation of a design on LibreLane instead of vibe-ic's own
decks, and keeps vibe-ic's gates as the judge. Without the flag nothing changes: every
v1 item proves that `dispatch_config` and `_step_inputs` are byte-for-byte the same with
no flag.

## Scope (v1)

- **gf180mcuD, digital, no macros.** Anything else is refused by name (W24):
  `IMPL_PDK_UNSUPPORTED`, `IMPL_MACROS_UNSUPPORTED`. The analog track is out of v1
  (decision 13) and is refused by name too. *Planned.*
- `--orfs` (formerly `--openroad`) comes later and is not part of v1.
- The Caravel runner is unchanged (decision 26).

## Two LibreLane runs, not one

LibreLane cannot take "everything after RTL" in one run. Floorplan waits on the equivalence
check and the synthesis hand-off gate (step 15 `blocks_on: [13, 14, …]`), and on the chip
path the die, the pad wrapper and the SDC can only be computed from the synthesized netlist.
So each flagged run makes exactly two LibreLane invocations (W5). *Planned.*

1. **Segment 1, synthesis:** `--to Checker.NetlistAssignStatements`.
2. **Between the segments, vibe-ic:** steps 11–14, then the die, the pad wrapper and the
   SDC (W7a pulls these out of `step_pnr`).
3. **Segment 2, implementation through GDS:** `--from OpenROAD.CheckSDCFiles
   --with-initial-state`, seeded with the netlist step 13 proved.

The flow follows the deliverable (decision 22): **Chip** for a DIE, **Classic** for a
HARDMACRO. Step ids come from the image's own flow order at run time, never from a list in
source (the image-facts reader, W22).

## Where vibe-ic's check is kept

Where LibreLane's default is weaker, the vibe-ic check stays. The gates below are the ones
`flow/phase1_phase2_phase3.yaml` already runs; under the flag they judge LibreLane's output.
*Planned* (the gates exist; running them on LibreLane's output is W7b).

| | LibreLane default | kept vibe-ic check |
|---|---|---|
| (a) | the tool's base SDC | the spec SDC as `PNR_SDC_FILE` / `SIGNOFF_SDC_FILE` |
| (b) | EQY off, skips gf180 | step 13 `lec_equivalence_check` |
| (c) | setup gated at tt only; no slew, cap or fanout gate | step 23 `sta_report_check` (multi-corner, slew/cap/fanout) and `hold_corner_coverage_check` |
| (d) | static IR, report-only | step 24 `ir_drop_report_check` and `dynamic_ir_drop_check` budgets |
| (e) | no repair after detailed route | `Vibeic.PostRouteRepair` (step 32) |
| (g) | reduced DRC deck, no ERC, density only in Chip, LVS from DEF | step 31 decks: `drc_report_check`, `lvs_report_check`, `erc_density_check`, … |
| (h) | Classic does not gate antenna | step 26 `antenna_report_check`, `gds_antenna_deck_check` |
| (i) | stricter on some lint codes, looser on others | P0's blocking lint list |

(f) is ORFS-only and not in v1.

## Steps LibreLane does not do

A step the flag's flow does not perform gets its own outcome reason class (W14). It is not
red and never counts as PASS, and it names the remedy: run the default flow, or choose a
flow that performs the step. It must not read as `CAPABILITY_ABSENT`, which is a harmless
skip. *Planned.*

## Layout under the flag

The shipped GDS is LibreLane's. vibe-ic's layout-writing steps only measure (decision 7),
with two disclosed exceptions kept inside the LibreLane run: `Vibeic.PostRouteRepair` and
`Vibeic.InsertSpareCells` (decision 12). `ClockPathDriveSizing`, `NamedViolationReroute`,
the direct tail deck and the T103 EM-sized straps are dropped under the flag, and the
report says why. Metal fill is LibreLane's default (decision 19). *Planned.*

## When LibreLane fails

An honest FAIL with the tool's own report and a named next action (decision 8). There is no
fallback to the direct flow and no AI repair of the config. *Planned.*

## Inputs and records

- **Step 0.5ic:** the declaration stays "NOT_DETERMINED, never a default". Tool defaults go
  into the impl record and the config provenance, never into the declaration (W9).
  *Planned.*
- **Imported rows:** each canonical file imported from a LibreLane step is a witnessed run
  attributed to LibreLane, citing its source log path and sha256 (decision 4a; W6, W19).
  *Planned.*
- **Mode record:** the mode is resolved once at the front door; a child whose flag
  disagrees refuses `IMPL_MODE_CONFLICT` (W1). A switch file and the flag naming the same
  step refuse `IMPL_SWITCH_CONFLICT` (decision 18). An in-place mode switch is refused; a
  different mode needs a fresh project clone (decision 20). *Planned.*
- **Image:** the image resolved at dispatch is recorded (decision 24) by the image-facts
  reader (W22). The LibreLane CLI must not take the PDK, the cell library or the PDK root
  from the image's environment: the image's own login environment names another PDK (W22).
  Every LibreLane container carries the memory ceiling (W15). *Planned.*

## Publication

Flagged runs are not published in v1, and they never overwrite or stand in for the default
publication identity (decision 25; W20). *Planned.*

## Acceptance

Plan §8: A0 (the default path is unchanged; W23 is the default HARDMACRO baseline), A1
(spm and subservient under `--librelane`), A3 (mode switching), A4 (negative controls) and
A5 (publication).
