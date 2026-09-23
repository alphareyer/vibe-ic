# L2 `protocol_overview.half_duplex` is a document-level keyword hit (investigation, no fix)

Lane ictier1, for the dispatcher's ruling. Measured on 98 real L2_FRS.json
(87 in benchmark-data, 2 frozen replays, 9 plugin fixtures). spm has no protocol_overview in any published cell and is untouched
by anything below.

## Mechanism (phase1_doc_one_shot_runner.py ~23871-23905)

`half_duplex = True` when ANY extracted document (a) passes
`_has_single_wire_protocol_evidence` and (b) matches `half[\s-]?duplex`
ANYWHERE in the same document. (b) is document-level, not sentence-level, and
has no negation / contrast handling. The emitted `evidence` string is then
"half-duplex/single-wire/1-wire keyword match".

The same L2 usually also carries a `duplex` text written by the per-protocol
synthesizers (`*_protocol_synth.py`), which states the design's duplex mode
in words. The two are never reconciled.

## The two flipped-true designs, at their source sentences

- pcie_gen5 (`phase1/input_doc/pcie_gen5_spec.txt`): "…chosen over the
  traditional parallel bus because of the inherent limitations of the latter,
  including half-duplex operation…" — half-duplex is attributed to the bus
  PCIe replaced. L2 `duplex`: "dual-simplex (independent TX + RX differential
  pairs per Lane…)".
- ufs (`phase1/input_doc/ufs_spec.txt`): "UFS implements a full-duplex serial
  LVDS interface … than the 8-lane parallel and halfduplex interface of
  eMMCs" and "full-duplex serial differential vs eMMC's parallel half-duplex
  bus". L2 `duplex`: "full-duplex (independent M-PHY TX and RX…)".
- The plugin fixture stage_phase1_on_pass_review/reject_pcie_gen5 is a copy
  of the pcie_gen5 L2 (same contradiction).

## Census: flag vs. the same L2's own `duplex` text (98 L2s)

| half_duplex | duplex text says | count |
|---|---|---|
| true | half (incl. sent's "half-duplex / unidirectional") | 10 |
| true | full / dual-simplex / unidirectional | **3** (ufs, pcie_gen5, fixture reject_pcie_gen5) |
| true | half AND full, as alternatives (hdlc, ethernet, rs485) | 3 |
| true | no duplex field (dali) | 1 |
| false | half | **17** (onewire, can, canfd, lin, flexray, smbus_pmbus, mipi_spmi_rffe, ble, lora, mipi, emmc, onfi, hyperbus, ddr, ddr4, lpddr5, fixture accept_lpddr5) |
| false | full | 13 |
| false | unidirectional | 4 |
| false | no duplex field | 10 |
| false | mixed / none / non-string duplex field | 4 |
| absent | half | 12 |
| absent | other / none | 18 |
| no protocol_overview | – | 3 (spm ×3) |

So the flag contradicts its own L2's text in 20 designs: 3 say true where the
text says full/dual-simplex, 17 say false where the text says half-duplex.

## The 17 false-with-half-text rows are a DIFFERENT question

The flag, as the extractor builds it, means "SINGLE-WIRE half-duplex" (it
requires `_has_single_wire_protocol_evidence`), which is the protocol family
`internal_vs_external_timing_check` actually checks (H0/H1/BR/IBT symbol
pulses on one shared wire). Most of the 17 are half-duplex in the generic
sense (a shared bidirectional bus turned around: DDR DQ, eMMC, ONFI, SMBus,
CAN). Setting half_duplex=true for them from the `duplex` text would make the
step-2 gate demand H0/H1/BR/IBT groups from DDR4 — ~17 new FAILs that answer
the wrong question. A few (onewire, LIN, possibly flexray/SPMI) are genuinely
single-wire half-duplex and read false because the document never says
"half-duplex" next to its single-wire evidence — a miss in the other
direction, noted but not addressed by the rule below.

## Proposed smallest principled rule (not implemented — awaiting the ruling)

1. **The `duplex` field can veto, not grant.** When the same L2 carries a
   `duplex` text that states full-duplex, dual-simplex or unidirectional and
   does NOT state half-duplex, `half_duplex` may not be `true`; it is `false`
   with `evidence` naming the `duplex` field. Measured effect: exactly 3 rows
   move true -> false (ufs, pcie_gen5, fixture reject_pcie_gen5); on step 2
   they move FAIL -> NOT_APPLICABLE (DESIGN_DECLARED_NA). No other corpus row
   moves; spm unaffected (no protocol_overview).
2. **A half-duplex mention counts only in a sentence that attributes it to the
   design**, in the SAME sentence as the single-wire evidence (today the scan
   is document-wide): a contrastive / negated sentence ("than X's half-duplex
   bus", "limitations of the latter, including half-duplex") never sets it.
   This is the root-cause fix for how pcie/ufs got true at all, but I could
   not measure it reliably from outside the extractor (my document globs and
   contrast list are cruder than its own sentence splitter); it should be
   measured by re-running the extractor's own function over the corpus inputs
   before it is ruled on.
3. "Either" texts ("half-duplex on 2-wire, full-duplex on 4-wire") keep the
   flag as the keyword rule sets it; with R-0915-156 in place a silent flag is
   already INCOMPLETE, not FAIL, at step 2.
