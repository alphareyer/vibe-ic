# Synthesis fanout bound — per-mechanism measurement (next/icmainred8-synthfo)

Image `ghcr.io/vibeic/vibeic-eda@sha256:4e9f54efe77a…` (0.3.67), container
`vibeic-eda-icmainred8`, plugin main 240c0a353. One arm at a time, `nice -n 10`.

**Command = the runner's EXACT synth command**, copied out of `synth.log` of a
fresh main synthesis (subservient: arm A copy of run2, `abc -liberty <tt lib>
-D 20000`; spm: probeSPM_A3, `-D 24000`), paths remapped to scratch, only the
`abc` line's tail varied. Step-10 basis: OpenSTA, ss_125C_4v50 liberty, the
run's own SDC, no parasitics.

| design | arm | abc tail | cells | area um2 | worst slack (ss) | internal nets over cap | netlist sha |
|---|---|---|---|---|---|---|---|
| subservient | S0  | (runner) | 2510 | 61,599.51 | -8.85 VIOLATED | 49 | ca73817730 |
| subservient | S0c | stock script spelled out (control) | 2510 | 61,599.51 | -8.85 | 49 | ca73817730 (== S0) |
| subservient | S1  | + `buffer -N 10` | 2653 | 66,270.89 | +2.55 MET | 0 | 25260a137b |
| subservient | S2  | + `buffer; upsize {D}; dnsize {D}` | 2653 | 63,482.99 | +1.51 MET | 0 | 420ad0b589 |
| spm | S0  | (runner) | 273 | 8,177.12 | +15.93 MET | 1 (register-driven) | 6efba3a468 |
| spm | S1 cap 4 / 10 | + `buffer -N 4` / `-N 10` | 273 | 8,857.63 | +15.59 MET | 1 / 0 | e572078d61 (4 == 10) |
| spm | S2 cap 4 / 10 | + full tail | 273 | 8,177.12 | +15.93 MET | 1 / 0 | 3b34fb5453 |

Cell-type deltas vs S0:
- subservient S1: +143 buffers (clkbuf_4 +90, buf_2 +35, buf_4 +14, clkbuf_3 +3) AND upsizing
  (xor2_1->xor2_4 x21, nor2_1->nor2_2 x10, nand2_1->nand2_2 x4, clkinv_1->clkinv_2 x4).
- subservient S2: buf_1 +137, clkbuf_1 +6, inv_1 +6 (dnsize takes every inserted buffer to x1).
- spm S1: 0 cells added; xor3_1->xor3_4 x16, xor2_1->xor2_4 x15 (+8.3 % area).

Readings:
1. ABC `buffer` is NOT fanout-only ("performs buffering and sizing"); there is no buffering-only
   mode. S1 is the smallest ABC lever that bounds fanout and carries no upsize/dnsize pass.
2. The earlier +4.36 ns was measured WITHOUT `-D`; with the runner's `-D 20000` the full tail gives
   +1.51 ns.
3. spm's declared cap 4 changes nothing measurable: its only over-cap net is register-driven, which
   ABC's combinational network cannot buffer (port and register fanout stay for `repair_design`).
4. `buffer` picks `clkbuf_*` cells for data nets on this PDK; the synth don't-use list the runner
   passes is empty for gf180mcuD. Disclosed, not changed here.
None of this is post-route evidence; the in-source rule requires the post-route A/B on a copy.
