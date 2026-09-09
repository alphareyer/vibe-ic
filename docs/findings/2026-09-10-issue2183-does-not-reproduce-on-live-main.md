# #2183 does not reproduce on live main — the root cause is real and already repaired

**Verdict: NOT REPRODUCIBLE at `9c653d47f` (v1.20.18). 1 root cause. It was real at the
issue's own base, it is fixed, and the fix is pinned by tests that provably go RED when the
defect is re-planted.**

The issue's HEADLINE MECHANISM is separately **REFUTED**: Step 5 did not read the ephemeral
staging root, not even at the base the issue was filed against. The filer flagged this as
inference themselves — "the identified candidate for what it read instead" — and the
inference is what does not hold. The producer leak they measured is real; the consequence
they attributed to it is not its consequence.

Host 8hd-3 for the four arms; re-verified on 8HD-4 (see the last section). Pinned image
`ghcr.io/vibeic/vibeic-eda@sha256:89a8fd72…` (read from `programs/_eda_pin.py
IMAGE_DIGEST`, verified as the running container's image). Design
`ic/spm`, PDK `gf180mcuD` — the same design and the same secondary target the issue used.
Scratch paths below are written `<scratch>` on purpose. RE-MEASURED at
`b6a73c0aa3`: `.md` IS in `shipped_path_portability_check._SCAN_EXTS`, but the land gate's
ratchet runs that checker over the PLUGIN root, and `docs/findings/` is outside it — so a
home path here would not in fact have been caught. The caution stands on its own merits and
the correction is recorded rather than the claim quietly kept.

## What was run, and what each arm answered

Four arms. Every arm drove the REAL runner in the pinned image; none tested a function in
isolation, because the issue's whole point is that the run disagreed with the function.

| # | tree | driver / project | producers leak stage root | Step 5 `missing_declarations` | obligations |
|---|---|---|---|---|---|
| 1 | live main | `design_one_shot_runner`, phase1 copied — **INVALID, see below** | n/a | `[]` | 0 |
| 2 | live main | `design_one_shot_runner`, complete project, `--force-rtl-regen` | **0** | (crashed before Step 5) | — |
| 3 | live main | as 2, `--exit-step 5` | **0** | `[]` | 0 |
| 4 | live main | **`vibe_ic_one_shot_runner`, full phase1, `--pdk gf180mcuD`** — the issue's own shape | **0** | **`[]`** | **4** |
| B | **`2692e510fae5` (v1.19.40), the issue's base** | as 3 | **2 files, `/tmp/vibeic-rtl-step-6a178lsa/spm_base`** | `[]` | 0 |

**Arm 1 was invalid and is reported rather than dropped.** I staged only `phase1/` + `input/`,
so `reports/phase1/extraction_coverage_report.{md,json}` and
`reports/audit/phase1/expert_parse_track.json` were absent, `rtl_gen` answered
`REFUSED TO RUN: 3 declared input(s) ABSENT`, and the staging transaction — the thing under
suspicion — never executed. Its clean result proved nothing. Both producer artefacts were
ABSENT, which is how I caught it. A partial fixture is the trap this arm fell into.

## Arm 4 is the closure: the issue's own "should be" figure, reproduced exactly

The issue states: *"The declarations exist, are readable, and yield 4 real obligations."*
Arm 4 — fresh project holding only `input/`, full phase1, the top-level orchestrator, the
issue's PDK — wrote:

```
verdict                  INCOMPLETE      applicability APPLICABLE
property_denominator     5               authored_property_count 1
missing_declarations     []
declaration_root         <scratch>/spm_full/phase1/generated_docs
declaration_root_present True            declaration_root_error None
unresolved: 4
  L8.clock_and_reset_waveform.clocks.0.edge            UNAUTHORED
  L8.clock_and_reset_waveform.resets.0.name            UNAUTHORED
  L8.clock_and_reset_waveform.resets.0.polarity        UNAUTHORED
  L8.clock_and_reset_waveform.resets.0.port_description UNAUTHORED
```

Four obligations, status `UNAUTHORED` — declared, read, and owed to an author. The issue's
broken artefact was `property_denominator 3, authored_property_count 0,
missing_declarations ["L3","L6","L8"]`, every row `DECLARATION_MISSING`. Neither the count,
the verdict, nor the status matches any more.

Both gates the issue names now PASS on that tree:

```
project_outputs_in_tree_check  PASS  337 file(s) scanned, 0 absolute path reference(s)
                                     examined — no such reference found
formal_proof_evidence_check    PASS  PROOF_CHAIN_OK: all_proved substantiated by an
                                     elaboratable .sby + SymbiYosys PASS transcript;
                                     property denominator 1/1 closed
```

`project_outputs_in_tree_check`'s "0 references examined" is a zero over a **non-empty**
scan (337 files), and it is corroborated independently: `grep -rl vibeic-rtl-step` over the
whole project returns 0 on every live-main arm.

## The one root cause, and where it was repaired

**The staged transaction published files that had recorded its own ephemeral root.**
`step_rtl_gen` snapshots the project under `<TMPDIR>/vibeic-rtl-step-XXXX/<name>` and runs
every generator against that root; a generator that records its own project root inside an
artefact wrote the scratch path, and the commit copied that string into the canonical tree,
where it outlived the directory it named.

Measured across the fix, same design, same command, same container:

* **arm B (v1.19.40)** — `declaration_contract.json` and `lessons_scoring_record.json` both
  carry `project = "/tmp/vibeic-rtl-step-6a178lsa/spm_base"`. 2 files.
* **live main** — both carry the live project root. 0 files.

Attribution, by symbol presence at the two shas:

| symbol | v1.19.40 | live main |
|---|---|---|
| `_phase1_remap_stage_tree` (runner) | absent | present |
| `declaration_root` (formal_harness_gen) | absent | 18 refs |
| `declaration_root_present` | absent | 7 refs |

The publish-seam rewrite landed in `7245fee1f`; the Step 5 disclosure half — naming the root
it read, and separating "root absent" / "root unlistable" / "file unparseable" from "the
design declared nothing" — landed in `9de8a3647`, `2c0a2dc49` and `eabb3b207` (v1.20.2).

## Why the headline mechanism is refuted, not merely unreproduced

Arm B is the decisive one. At the issue's **own base**, with the producer leak demonstrably
live in that same run, Step 5 still reported `missing_declarations []` and read the live
project's `generated_docs`. The leak and the denominator are independent: the two producers
record `str(project)` into their own artefacts, while `_read_l_docs` resolves
`generated_docs_dir(project)` from the root Step 5 is actually handed. Nothing observed —
at either sha — shows Step 5 being handed the stage root.

So the issue's `property_denominator 3 / authored_property_count 0` shape has a different
cause, and the honest statement is that it is **NOT_MEASURED** here: that shape is the
`if not rtl_files:` branch of `generate()` reached with three missing declarations, i.e. a
run in which Step 5 saw *neither* RTL *nor* `generated_docs`. No arm reproduced it. It is
not projected to zero and it is not attributed to the staging root.

## The fix is pinned, and the pinning is not vacuous

Three regression tests already ship:

```
programs/tests/test_issue2183_staged_transaction_publishes_the_canonical_root.py
programs/tests/test_issue2183_step5_declaration_denominator_names_its_root.py
programs/tests/test_issue2183_step5_root_it_cannot_list_is_not_an_absent_declaration.py
```

* clean live main — **27 passed**
* live main + the defect RE-PLANTED (the publish seam stops rewriting: replace the
  `_phase1_remap_stage_tree(...)` call with `[]`, the smallest edit that reintroduces it) —
  **3 failed, 24 passed**:
  `test_the_published_artefact_names_a_root_that_exists`,
  `test_no_published_file_carries_the_stage_root_anywhere`,
  `test_the_rewrite_is_disclosed_by_name`

A guard that cannot fail is not a guard; these fail on the exact defect, and the pairing was
re-measured at `b6a73c0aa3` before this document landed. **This branch adds no fourth test
for #2183 itself** — it would be duplicate coverage of an already-pinned fix. The one test
it does add belongs to the SECONDARY finding below, which is a different defect.

## The secondary finding, ACTED ON in this branch

**`step_dft_lec_chain` raised `UnboundLocalError` on `pdk`, and no caller catches it.**

REPRODUCED at `b6a73c0aa3` by driving the real step — not a function in isolation, and with
nothing stubbed before the crash point:

```
File ".../design_one_shot_runner.py", line 19582, in step_dft_lec_chain
    if pdk and pdk == _cpdk.COMMERCIAL_PDK_ID:
UnboundLocalError: cannot access local variable 'pdk'
```

`pdk` is bound at ONE place — inside the Step-11 branch, the `else` of
`if not full_chip: … elif not clk: …`. Step DT1, 500 lines below, reads it at FUNCTION
scope, and DT1's own preconditions are different and weaker: a derivable clock and
`phase2/stage2/dft/cut_netlist.v` on disk. Both survive an earlier full run, so a
`--skip-phase3` re-run over such a tree reaches the read with the binding never executed.
`main()` calls this through `_spf.gate` and then `plan.extend(...)`; the exception leaves
the runner and the phase-2 run dies at steps 11-13, after step 9 has already written its
artefacts. That is what killed arm 2.

**The repair is a measurement, not a default.** `pdk` is initialised to `None` — NOT
SNIFFED — and DT1 sniffs it with the same call on the same netlist Step 11 uses. `""` was
rejected deliberately: it is `_dft_atpg_sniff_pdk`'s own answer for a generic/unmapped
netlist, so defaulting to it would have silenced the crash while publishing "there are no
library-mapped cells" about a run that never looked.

Pinned by `programs/tests/test_issue2183_dt1_reads_a_pdk_step11_never_sniffed.py`
(6 cases: the crash path, the untouched precondition branch, the sniff's identity with
Step 11's, both `--pdk-dir` arms, and an AST guard that the binding is at function scope).
Arms: **live main + the fix — 6 passed; live main sources + the test alone — 5 failed,
1 passed**, the one pass being the deliberately-unchanged precondition control.

## One finding still NOT acted on (deliberately out of scope, no issue opened)

**`_read_l_docs` still has one silent-zero state.** `if project is None or _pl is None:`
returns no declarations with `root=None`, `root_present=False` and `root_error=None` — the
fourth member of exactly the family #2183 separated, and the only one still
indistinguishable from "the design declares nothing". It cannot produce the issue's
artefact (`_write_property_contract` returns early on `_pl is None`, so no contract would
exist at all — measured, which is how this was ruled out as the cause), and `_path_layout`
is always present in-tree, so it is defensive rather than live. The honest repair is
`NOT_MEASURED`, not zero. Left alone under "do not widen scope".

## Not measured

* The `pdk` crash's blast radius across other designs and PDKs. The repaired path is
  pinned; how many published runs died on it is not counted here and is not projected.
* The `property_denominator 3 / authored_property_count 0` shape itself — stated as
  NOT_MEASURED above, and still is. No arm reproduced it at either sha.

## Re-verification at a later main

Everything above was first measured at `9c653d47f` on 8hd-3. Re-run at `b6a73c0aa3` on
8HD-4, in the same pinned image, before this document was landed:

* the three shipped #2183 regression tests — **27 passed** on clean live main;
* the same three with the publish-seam rewrite re-planted (`_stage_rewrites = []`) —
  **3 failed, 24 passed**, and the three are the same three the document names;
* `landing_hygiene_ratchet_check --base origin/main --head HEAD` — PASS on all three
  halves (atomic writes, shipped-path portability, watchdog compliance).

`fleet_scripts/falsref.sh` DOES exist on 8HD-4 but is the 2026-09-04 copy: it has no
hygiene arm and no mutation arm. The hygiene arm was therefore run directly with the land
gate's own instrument rather than reported by the falsifier.
