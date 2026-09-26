* u_hawaii_adc — ANALOG BLOCK INTERFACE DECLARATION
*
* WHY THIS FILE EXISTS. L5_ANALOG_SPEC.md declares each analog block's
* PERFORMANCE (targets, ranges, units) and nothing about its PINS, so the two
* producers that need the interface derived it independently and disagreed:
* the Phase-2 RTL blackbox reads the doc prose, the A2 topology emitter reads
* its topology library, and `analog_hardmacro_pinname_consistency_check`'s own
* "golden source" — `spec.json:interface.pins[]` — was never emitted by
* anything, so the gate self-skipped and the disagreement reached OpenROAD as
* STA-0201 "port not found" warnings at PnR.
*
* This is the design's declaration of that interface, in the SPICE form the
* flow's own Phase-1 metadata reader already ingests. EVERY pin cites the
* document line it comes from. Two pins are marked DERIVED rather than STATED
* and say why; nothing here is invented silently.
*
* ---------------------------------------------------------------------------
* Block A — delta_sigma (x6). Cited from input/docs/L5_ANALOG_SPEC.md unless
* noted.
*   vin     STATED  L5 Block A: "| Vin (diff) | 1.0 | 0-1.2 | V | input range
*                   vs VHI/VLO |"; L1: "Analog inputs `IN1..IN6` (PAD),
*                   differential referenced to `VHI`/`VLO`."
*   vrefp   STATED  L5 Block A: "| Vref | 1.0 | 0.8-1.2 | V | reference
*   vrefn           (VHI-VLO) |" — the reference is the VHI/VLO PAIR, so it
*                   enters the block as two pins. L1: "Reference pins
*                   `VHI`/`VLO`".
*   vdd     STATED  L5 Block A: "| Vdd (core) | 1.2 | 1.1-1.3 | V | core
*                   supply (one copy from the LDO) |"
*   vss     DERIVED L1: "| Supplies | IO/analog **1.8 V** (IOVDD) - core
*                   **1.2 V** ... |". The documents name every SUPPLY and no
*                   RETURN: `gnd`/`vss`/`ground` appear nowhere in L1, L5 or
*                   L9. A declared potential is a difference against a return
*                   node, so the return is entailed by the supply declaration
*                   itself; it is recorded here as DERIVED, and the absence of
*                   an explicit ground pin in the input documents is a real
*                   gap in them, not a modelling choice made here.
*   clk     STATED  L5 Block A: "| fclk | 1.0 | 0.1-10 | MHz | modulator clock
*                   (CK4/5/6) (est) |"
*   bit_out STATED  L5 Block A: "| output | 1-bit serial (OUTn / dout) | - |
*                   - | digital bitstream per channel |"
*
* NOT DECLARED HERE, and deliberately: `rst` and `vcm`. Both exist on the
* current A5 layout because a switched-capacitor incremental topology needs
* them ("resets/accumulates per conversion window", L5 Block A
* converter_type), but neither is a chip-level pin in any input document. They
* are INTERNAL to the block's chosen topology. A topology that exposes them at
* the block boundary is exposing an implementation detail, and the gate that
* compares this declaration against the layout is the right place for that to
* surface.
* ---------------------------------------------------------------------------
.subckt delta_sigma vdd vss vin vrefp vrefn clk bit_out
.ends delta_sigma
*
* ---------------------------------------------------------------------------
* Block B — ldo (x1).
*   vin     STATED  L5 Block B: "| Vin | 1.8 | 1.6-2.0 | V | IOVDD (confirmed
*                   top pin) |"
*   vout    STATED  L5 Block B: "| Vout | 1.2 | 1.1-1.3 | V | regulated CORE
*                   for the LDO-fed modulator copy |"; L1: supplies "`VLDO`".
*   vref    STATED  L1: "supplies `IOVDD` (1.8 V), `CORE` (1.2 V),
*                   `VLDO`/`VREF` for the LDO channel."
*   vss     DERIVED same entailment as Block A above.
* ---------------------------------------------------------------------------
.subckt ldo vin vss vref vout
.ends ldo
