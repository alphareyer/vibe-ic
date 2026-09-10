# Issue #2234: the buffer chain on the SS setup path is stage COUNT, not buffer STRENGTH

Lane `cyrebuf`, 2026-09-10. HOST 8HD-6 (192.168.1.108), load 2.7/32 cores.
Base `main` = `c07ba9ef75c7c459d76848584203c0c37b6759f0`, tree `2a5c7abc7155071fcee7c05043316812fc59de35`.
Image `$VIBEIC_EDA_IMAGE_REPO@sha256:89a8fd7295208ee6…` (label `0.3.49`), OpenROAD `26Q3-2075-g18e98f9e44`,
source read at `vibeic/OpenROAD` commit `18e98f9e4433898bb514744fbfa726a63cce5b9c`.

Issue #2234 asked which of three hypotheses explains the `rebuffer*` chain on the `sha256` SS setup
path, and required that the answer be established by measurement with the file:line that makes the
choice, before anything is changed. This is that answer. **All three hypotheses as worded are false**,
and the remedy the issue implies — stronger buffers — is measured here to be worth **+0.104 ns against
a 1.86 ns miss**.

## Hypothesis 3 (23 separate repaired nets, so the finding is path depth) — FALSE

Measured from the netlist the sign-off STA actually read, parsing instance connectivity:

    __uuf__._14057_ (a21oi_4)  net __uuf__._07315_  fanout=2 -> __uuf__._14058_/A2, rebuffer67/A
    rebuffer67 -> rebuffer66 -> ... -> rebuffer5600   every stage fanout=1
    rebuffer5600  net net5600  fanout=1 -> __uuf__._14360_/A

It is ONE chain, on ONE net, every stage fanout 1, terminating at ONE sink. Not 23 separate nets.
Driver and sink are ~33.3 um apart (Manhattan, from the routed DEF); the chain's own placement
wanders ~120 um and backtracks, so the buffers are not repeaters segmenting a long wire.

## Hypothesis 1 (the sizing-limits preamble does not reach rebuffering, so it selects from a narrowed family) — FALSE

The first clause is true and the conclusion is false, so the hypothesis is false.

*The preamble cannot reach rebuffering.* `sizing_area_limit_` / `sizing_leakage_limit_` — what
`set_opt_config -limit_sizing_area/-limit_sizing_leakage` sets — are referenced in exactly three
places: `RepairDesign.cc:412-414`, the setter at `Resizer.cc:463-497`, and a temporary relaxation at
`Resizer.cc:1782-1783`. There is **no reference in `Rebuffer.cc` at all**. So the plugin's
`_sizing_limits_preamble_tcl` governs `repair_design`'s cell selection and nothing about which cell
rebuffering inserts.

*But rebuffering is not held to a narrowed family either.* Its pool is `buffer_fast_sizes_`, built by
`Resizer::findFastBuffers()` (`Resizer.cc:895-947`) as a Pareto front over `buffer_cells_`, and read
by `Rebuffer::init()` at `Rebuffer.cc:1470`. Measured in the pinned image on this design's own library
setup (same LEF/liberty/`set_dont_use` as the run, `rsz::report_fast_buffer_sizes`):

    There are 7 fast buffers
    buf_2   area 5.0   Cin 1.8e-15  intrinsic 1.7e-10  Rdrv 7466.6
    buf_1   area 3.8   Cin 2.1e-15  intrinsic 9.9e-11  Rdrv 18239.1
    buf_4   area 7.5   Cin 2.5e-15  intrinsic 1.8e-10  Rdrv 4218.3
    buf_6   area 11.3  Cin 4.8e-15  intrinsic 1.5e-10  Rdrv 2981.1
    buf_8   area 15.0  Cin 7.2e-15  intrinsic 1.6e-10  Rdrv 2372.3
    buf_12  area 20.0  Cin 9.4e-15  intrinsic 1.7e-10  Rdrv 1671.8
    buf_16  area 27.5  Cin 1.4e-14  intrinsic 1.7e-10  Rdrv 1459.9

The whole drive ladder is available. `buf_16`, 12.5X stronger than `buf_1`, was in the pool the entire
time. The family was never narrowed.

## Hypothesis 2 (the pool reaches it and the cost function prefers many small buffers) — first clause TRUE, second clause FALSE

The choice is made at `Rebuffer.cc:1317` (`for (BufferSize buffer_size : buffer_sizes_)`) with the
acceptance test at `Rebuffer.cc:1334-1341`; the chain is materialised by `Rebuffer::exportBufferTree`
with the `"rebuffer"` prefix at `Rebuffer.cc:2499-2500`, reached only from `Rebuffer::rebufferPin`,
whose one in-flow caller is the BufferMove of `repair_timing` at `move/BufferCandidate.cc:30`.
(`fullyRebuffer` uses the prefix `"place"` at `Rebuffer.cc:2330`, so it is not the source of these
instances.) `BufferGenerator::isApplicable` (`move/BufferGenerator.cc:29-33`) requires
`target.fanout > 1`; this driver's fanout is 2, so it qualifies.

So the pool does reach it and the cost function did choose `buf_1`. But the second clause — that this
is an area/leakage-optimal choice which is **delay-pessimal** — is false. `buf_1` has the **lowest
intrinsic delay in the family** (9.9e-11 s against 1.5e-10 to 1.8e-10 for every other buffer), and the
chain's loads are 0.00-0.02 pF, where intrinsic delay dominates the RC term. At these loads `buf_1` is
close to the best per-stage choice available.

Measured, on the retained implementation with its own sign-off parasitics, by swapping all 17 chain
instances to a stronger drive and changing nothing else (same placement, same SPEF, same routing; a
control arm that swaps nothing reproduces the baseline bit-for-bit):

    chain cell      setup WNS (ns)    delta vs buf_1    stdcell area (um2)   delta
    buf_1 as built    -1.8553              --                172081.3         --
    buf_2             -1.7511           +0.1042              172102.6       +21.3
    buf_4             -1.7591           +0.0962              172145.1       +63.8
    buf_8             -1.8089           +0.0464              172272.7      +191.4

Upsizing the entire chain recovers **0.104 ns of the 6.25 ns the chain costs — 1.7 %** — and the
response is **non-monotonic**: `buf_2` beats `buf_4` beats `buf_8`. Making the buffers stronger past
`buf_2` makes the path *worse*, because a stronger buffer's higher intrinsic delay and higher input
capacitance cost more than its lower drive resistance saves at 0.01 pF. There is no sizing choice in
this library that closes a 1.86 ns miss on this path.

## What the finding actually is

The retained worst path carries 19 `rebuffer*` stages costing 6.25 ns of a 33.07 ns arrival (18.9 %).
Per stage, at the SS corner:

    loads 0.00-0.02 pF, stage delay min 0.20 / mean 0.329 / max 0.50 ns

Even if every stage achieved the *minimum* delay observed anywhere in the chain, 19 stages have a
floor of **3.80 ns** — twice the 1.86 ns miss. The cost is bought by the stage COUNT. The correct
question is why a 19-deep fanout-1 chain exists on a net whose driver and sink are 33 um apart, not
why its cells are small.

`BufferCandidate::apply()` (`move/BufferCandidate.cc:28-54`) returns `.accepted = true` whenever
`rebufferPin` inserted at least one buffer; its only rejection is "couldn't insert any buffers". The
slack check that guards the move is at the policy level (`policy/SetupLegacyPolicy.cc:405-408`,
reverting through `MoveCommitter::restoreJournal`), i.e. per endpoint, per pass, against the
parasitics basis in force at that moment. The chain is the accumulated residue of many such
individually-improving passes: the run made 12 `RSZ-0040` insertions totalling 169 buffers across
three parasitics bases (placement-estimated, global-route-estimated, and post-route extracted), and
the chain's instance names fall in two disjoint index ranges plus four instances absent from
`routed.def` but present in the post-route repaired netlist — three generations, no pass ever
re-evaluating the chain as one object.

## A correction to the numbers the issue quotes

The issue's contributor table (23 stages / 7.77 ns / 23.3 % of a 33.32 ns arrival, slack -2.25) is
computed from `sta_mcorner_ocv_postrepair.rpt`. That is the arm the flow **reverted**: the run's own
`postroute_timing_repair_decision.json` records `"action": "timing_repair_reverted_regression"` with
`repair_setup_delta_ns: -0.39`, and the retained artefacts are the pre-repair ones. The number that
governs the FAIL is therefore **-1.86 ns**, on `sta_mcorner_ocv.rpt` over netlist `sha256_pnr.v`, whose
worst path carries **19** `rebuffer*` stages costing **6.25 ns of 33.07 ns (18.9 %)**. The finding
survives the correction; its headline figures change.

Separately worth recording: that reverted repair pass believed it had closed. Its own report reads
`SHIP_WNS_BEFORE: -3.425` -> `SHIP_WNS_AFTER_REPAIR: +0.0046`, and an independent re-measurement of the
same layout returned -2.25. Reproduced here in 2 s: one `repair_timing -setup` on `routed.def` with the
sign-off script's own preamble takes WNS from -1.855 to **+0.018** and TNS to 0.0 while inserting 12
more buffers. A repair pass that reports closure against its own extraction and a sign-off gate that
reports a 2 ns miss on the same layout is a separate defect from this one, and is not filed here.

## What was NOT measured

* No end-to-end `sha256` re-run was performed, because no fix was landed: the fix direction the issue
  proposes is measured above at +0.104 ns against a 1.86 ns miss, and a 22-hour arm for a change that
  cannot close the gate is not worth spending before the issue is re-scoped.
* The baseline arm from lane `rbsha7` on 8HD-8 could not be read from this host (no ssh credential).
  The baseline used here is this lane's own re-measurement, `-1.8553` ns, which agrees with the run's
  retained sign-off report of `-1.86` ns.
* The swap arms are first-order: no re-legalisation and no re-route after the swap. They are a
  timing what-if on a fixed topology, which is exactly the question "would stronger cells have helped"
  asks; they are not a placed-and-routed result.
