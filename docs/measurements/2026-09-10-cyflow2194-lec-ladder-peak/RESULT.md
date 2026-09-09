# #2194 acceptance 1, measured: the ladder's peak is the MAX of its rungs

Lane cyflow2194, 8HD-6 (192.168.1.108), 2026-09-10.
Pinned image `192.168.1.112:5000/vibeic-eda@sha256:89a8fd72…` (the digest
`tools/ci/hermetic_candidate_runner.py:IMAGE_DIGEST` pins), container
`cyflow2194-eda`, real Yosys 0.68+.

## Why this measurement did not exist

`c071f6253` split the ladder into one process per rung and landed **`Refs #2194,
not Closes`** — deliberately, and it said why: *"the saving the issue reports is
not reproduced on the design that was available to measure"*. Its own per-rung
table reads `leg 1 = 38092 KiB; legs 2-4 NOT_MEASURED — each ran in under 1 s,
shorter than the supervisor's sampling interval.`

So acceptance 1 — *"the ladder's peak RSS becomes the max of its rungs, not their
sum — PROVEN by sampling at least every 30 s across a real multi-rung run, with
the per-rung table published"* — stayed open for want of a design whose rungs are
long enough to sample, and of an instrument that does not need them to be.

## The subject

A design chosen to make every rung measurable: large state so the miter is big,
no multiplier so induction stays cheap. 64-bit LFSR + 16x64 shift pipeline,
1153 compared points, sky130A `tt_025C_1v80`, `synth`/`dfflibmap`/`abc`.
Both arms: **verdict PASS, equivalent true, compared 1153, unproven 0.**

## The two arms

Same design, same container, same code. The only difference is which documented
path runs: checkpointing ON gives one process per rung, and with it OFF *"the
ladder stays in ONE process and the emitted recipe is byte-identical to the
pre-#2194 one — that path remains a control rather than a second
implementation."* The control was selected without editing code, by making
`reports/lec_checkpoints` uncreatable, and the run says so itself:
`WARN: proof checkpointing OFF — could not create …: File exists`.

| | per-rung (main today) | single process (control) |
|---|---|---|
| `lec_ladder.processes` | **4** | **1** |
| Yosys per-leg peak (MB) | 154.53 / 166.80 / 45.55 / 46.00 | 211.43 |
| **peak = MAX of rungs** | **166.80 MB** | — |
| SUM of rung peaks | 412.88 MB | — |
| supervisor `peak_rss_kib` | 165100 KiB | 222940 KiB |

**ACCEPTANCE 1 IS MET.** The ladder's peak is the max of its rungs (166.80 MB),
not their sum (412.88 MB), and it is **21 % below** the single-process peak on
the same design (211.43 MB). The accumulation #2194 describes is real and is
removed. It is smaller here than opentitan_aes's 48 GiB vs 16.3 GB because this
miter is far smaller — the ratio is a property of the design, and the quantity
reported is the gap, not an assumed ratio.

## What the measurement exposed — a peak that was a poll

Cross-reading this repo's instrument against Yosys's own closing line
(`End of script. … MEM: <X> MB peak`, one per process, an exact `ru_maxrss`):

| rung | supervisor poll | Yosys's own |
|---|---|---|
| equiv_simple_full | 165100 KiB | 154.53 MB |
| **equiv_induct_seq4** | **64832 KiB** | **166.80 MB** |
| equiv_induct_seq16 | 53016 KiB | 45.55 MB |
| equiv_induct_seq64 | 53412 KiB | 46.00 MB |

`leg_peak_rss_kib` reads the stall supervisor's samples and the supervisor polls
every `DEFAULT_POLL_S` = 30 s. **A poll is not a peak**: it reports nothing for a
rung shorter than its interval (which is why c071f6253 measured three
NOT_MEASURED rows) and, for a longer one, whatever the process happened to hold
when the clock struck. The seq4 row is a sample published in a field named
`peak`, understating 2.6x — and it made the ladder's headline `peak_rss_kib`
(165100) not the maximum either.

Yosys states each process's exact peak in evidence every run already keeps, and
nothing read it. Unit check of MB→KiB on the control arm, where the poll DID
catch the maximum: 211.43 MB → 216504 KiB against the supervisor's 222940 KiB
for the whole process tree — agreeing to 3 %.

## After the repair, same design, fresh run (no cache, no checkpoint reuse)

| rung | published peak | sampled | self-reported | source |
|---|---|---|---|---|
| equiv_simple_full | 164788 | 164788 | 157962 | supervisor+yosys |
| **equiv_induct_seq4** | **170332** | **53496** | **170332** | supervisor+yosys |
| equiv_induct_seq16 | 53080 | 53080 | 46643 | supervisor+yosys |
| equiv_induct_seq64 | 53192 | 53192 | 46899 | supervisor+yosys |

`lec_ladder.peak_rss_kib` 170332, `sum_of_rung_peaks_kib` 441392. The poll alone
would have published 53496 for seq4 — 3.2x low — and named a smaller rung the
ladder's peak. Both readings are kept; the larger is published because both are
lower bounds and neither may become a 0. Where neither instrument has anything,
the field stays NOT_MEASURED.

## Not measured here

The 48 GiB opentitan_aes case itself. This host was not asked to reproduce a
48 GiB run; the gap is measured on a design that exhibits the accumulation at a
size this host can hold, and it is reported as the gap, not as a ratio carried
over from another design.
