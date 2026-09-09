#!/usr/bin/env python3
"""data_converter_rtl_gen — the deterministic digital datapath for the
``data_converter`` class, or a REFUSAL that names the field it lacked.

WHAT WAS MISSING (vibe-ic#2197)
===============================
`data_converter` carried ``rtl_gen: null``, so the only reachable outcome for a
converter WITH a digital datapath was ``rtl_gen WAIVED -> fallback_skill``, an
LLM authoring hand-off. MEASURED on live main ``9c653d47f1`` in the pinned
image, a converter whose Phase-1 input declares digital content::

    status      : WAIVED      fallback : spec-to-rtl
    rtl dir     : False       (phase2/stage1/rtl never created)
    registry rtl_gen : None
    refusal-by-name sources : NONE

So the flow could neither author the datapath NOR say what it lacked. Both
halves are this file.

WHY A CIC, AND WHY NOTHING ELSE (§4.05)
=======================================
A decimator's ORDER, BAND EDGES and COEFFICIENT SET are design decisions a
specification does not state, and inventing them yields something plausible,
unverifiable, and indistinguishable from a correct filter until silicon. This
generator therefore emits the ONE decimation structure that has no free
coefficients at all: a cascaded integrator-comb (Hogenauer). Every dimension of
it is ARITHMETIC on declared parameters --

    stages          N   declared
    decimation      R   declared
    differential M  M   declared
    input width     Win declared
    output width    Wout = ceil(N * log2(R*M)) + Win     <- Hogenauer, exact

-- and its coefficients are unity by construction, so there is nothing here for
a generator to choose. A droop-compensating FIR, a half-band chain or any
filter with a stated passband ripple is NOT derivable from these parameters and
is deliberately OUT OF CONTRACT: this program refuses rather than inventing one.

THE DECLARED-FIELD CONTRACT
===========================
Read from the flow's own Phase-1 artefact, ``L5_ADI_SPEC.json``, in the
``analog_blocks[].spec.specs[]`` row shape that `analog_resolution_stimulus`
already reads (one convention, one reader, so the two can never disagree):

    osr                             -> R, decimation factor
    decimator_stages                -> N
    decimator_differential_delay    -> M
    bitstream_width                 -> Win

A row is DECLARED when it carries a finite numeric ``target`` (or ``min``).
Absent, non-numeric or out-of-domain ⇒ this program prints

    REFUSE: I cannot build this because the specification does not state <field>

on stderr and exits 2, emitting nothing. `design_one_shot_runner.step_rtl_gen`
turns a non-zero generator into ``rtl_gen FAIL`` and routes to the class's
declared ``fallback_skill`` -- so the refusal is REACHABLE and NAMED, which is
what #2197 asks for, and is never a silent pass.

Exit codes:  0 emitted   2 refused by name (declared field absent)
Chip-AGNOSTIC: no design name, PDK, vendor or class keyword is read.

Usage:  python3 data_converter_rtl_gen.py <project_dir> [--top NAME]
"""
from __future__ import annotations

import math
import os as _os
import sys as _sys

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))

import argparse                                                  # noqa: E402
import json                                                      # noqa: E402
from pathlib import Path                                         # noqa: E402
from typing import Any, Dict, Optional, Tuple                     # noqa: E402

import _path_layout as _pl                                       # noqa: E402

#: The Phase-1 artefact this generator reads. Same file, same row shape, as
#: `analog_resolution_stimulus.resolution_axis`.
L5_REL = "L5_ADI_SPEC.json"

#: field -> (accepted row names, human sentence for the refusal). ORDER IS THE
#: REFUSAL ORDER, so a design missing several is told about them one at a time
#: in a stable sequence rather than in dict order.
DECLARED_FIELDS: Tuple[Tuple[str, Tuple[str, ...], str], ...] = (
    ("decimation_factor", ("osr", "oversampling_ratio", "decimation_factor"),
     "the decimation factor (OSR)"),
    ("stages", ("decimator_stages", "cic_stages"),
     "the number of decimator stages"),
    ("differential_delay", ("decimator_differential_delay",
                            "cic_differential_delay"),
     "the decimator's differential delay"),
    ("input_width", ("bitstream_width", "modulator_output_width"),
     "the modulator output (bitstream) width in bits"),
)


class Refusal(Exception):
    """Cannot build, and the missing DECLARED field is named."""

    def __init__(self, field: str, sentence: str, detail: str = ""):
        self.field = field
        super().__init__(
            f"I cannot build this because the specification does not state "
            f"{sentence} (declared-field `{field}`)" + (f" — {detail}" if detail else ""))


def _rows(project: Path) -> Dict[str, float]:
    """``{row name: numeric value}`` over every analog block's declared specs.

    A row counts only when it carries a FINITE numeric `target` (else `min`) —
    the same admission rule `analog_resolution_stimulus` applies, so a field
    this generator calls declared is one that reader calls declared too.
    """
    path = _pl.generated_docs_dir(project) / L5_REL
    if not path.is_file():
        raise Refusal("L5_ADI_SPEC", "an analog/mixed-signal specification",
                      f"{path} is absent")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refusal("L5_ADI_SPEC", "a readable analog/mixed-signal "
                      "specification", f"{path}: {exc}") from exc
    out: Dict[str, float] = {}
    blocks = data.get("analog_blocks") if isinstance(data, dict) else None
    for blk in (blocks if isinstance(blocks, list) else []):
        if not isinstance(blk, dict):
            continue
        spec = blk.get("spec")
        rows = spec.get("specs") if isinstance(spec, dict) else None
        for row in (rows if isinstance(rows, list) else []):
            if not isinstance(row, dict):
                continue
            name = str(row.get("name", "")).strip().lower()
            if not name:
                continue
            value: Any = row.get("target")
            if not (isinstance(value, (int, float))
                    and not isinstance(value, bool) and math.isfinite(value)):
                value = row.get("min")
            if (isinstance(value, (int, float)) and not isinstance(value, bool)
                    and math.isfinite(value)):
                out.setdefault(name, float(value))
    return out


def declared_parameters(project: Path) -> Dict[str, int]:
    """The four declared integers, or a `Refusal` naming the first one absent.

    Every value is required to be a POSITIVE INTEGER: a CIC with a fractional
    stage count or a zero decimation factor is not a structure, and silently
    rounding one would be exactly the invention this generator exists to avoid.
    """
    rows = _rows(project)
    got: Dict[str, int] = {}
    for field, names, sentence in DECLARED_FIELDS:
        value: Optional[float] = None
        for n in names:
            if n in rows:
                value = rows[n]
                break
        if value is None:
            raise Refusal(field, sentence,
                          f"none of {list(names)} is a declared spec row")
        if value != int(value) or int(value) < 1:
            raise Refusal(
                field, sentence,
                f"declared as {value!r}, which is not a positive integer; "
                f"this generator will not round a declared value")
        got[field] = int(value)
    return got


def output_width(p: Dict[str, int]) -> int:
    """Hogenauer's exact register growth. ARITHMETIC, not a design choice."""
    n, r, m, win = (p["stages"], p["decimation_factor"],
                    p["differential_delay"], p["input_width"])
    return int(math.ceil(n * math.log2(r * m))) + win


_RTL = """// GENERATED by data_converter_rtl_gen.py (vibe-ic#2197) — DO NOT EDIT.
//
// Cascaded integrator-comb (Hogenauer) decimator. Every dimension below is
// arithmetic on a DECLARED parameter; this file contains no filter
// coefficient, because a CIC has none. See the generator's docstring for the
// declared-field contract and for what it refuses to invent.
//
//   decimation factor R   = {r}      (declared: {r_src})
//   stages            N   = {n}      (declared: {n_src})
//   differential delay M  = {m}      (declared: {m_src})
//   input width       Win = {win}      (declared: {win_src})
//   output width      Wout= {wout}      = ceil(N*log2(R*M)) + Win  [Hogenauer]
module {top} #(
    parameter int DECIM_R = {r},
    parameter int STAGES  = {n},
    parameter int DIFF_M  = {m},
    parameter int WIN     = {win},
    parameter int WOUT    = {wout}
) (
    input  logic                    clk,
    input  logic                    rst_n,
    input  logic signed [WIN-1:0]   din,
    input  logic                    din_valid,
    output logic signed [WOUT-1:0]  dout,
    output logic                    dout_valid
);

    // ---- integrator section, at the input rate ---------------------------
    logic signed [WOUT-1:0] integ [STAGES];
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            for (int i = 0; i < STAGES; i++) integ[i] <= '0;
        end else if (din_valid) begin
            integ[0] <= integ[0] + $signed({{{{(WOUT-WIN){{din[WIN-1]}}}}, din}});
            for (int i = 1; i < STAGES; i++) integ[i] <= integ[i] + integ[i-1];
        end
    end

    // ---- rate change -----------------------------------------------------
    localparam int CNTW = (DECIM_R <= 1) ? 1 : $clog2(DECIM_R);
    logic [CNTW-1:0] cnt;
    logic            tick;
    assign tick = din_valid && (cnt == CNTW'(DECIM_R - 1));
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)            cnt <= '0;
        else if (din_valid)    cnt <= tick ? '0 : (cnt + CNTW'(1));
    end

    // ---- comb section, at the decimated rate -----------------------------
    // The chain is walked INSIDE the process with a blocking local, not as an
    // array of continuous assignments: an unpacked `cnode[]` fed by
    // `cnode[i+1] = cnode[i] - ...` is a strict feed-forward chain per element
    // but an ARRAY-WIDE dependency to a lint's flattening analysis, which
    // -Wall rejects as UNOPTFLAT (circular combinational logic). NOTE a
    // comment line may not BEGIN with the linter's own name — that is lexed
    // as a pragma and is itself an error, measured here.
    // MEASURED on this generator's own output before the change. Restructured
    // rather than waived — the emitted filter is identical.
    logic signed [WOUT-1:0] cdly [STAGES][DIFF_M];

    always_ff @(posedge clk or negedge rst_n) begin : comb_proc
        logic signed [WOUT-1:0] chain;
        if (!rst_n) begin
            for (int i = 0; i < STAGES; i++)
                for (int j = 0; j < DIFF_M; j++) cdly[i][j] <= '0;
            dout       <= '0;
            dout_valid <= 1'b0;
        end else begin
            dout_valid <= 1'b0;
            if (tick) begin
                chain = integ[STAGES-1];
                for (int i = 0; i < STAGES; i++) begin
                    for (int j = DIFF_M-1; j > 0; j--) cdly[i][j] <= cdly[i][j-1];
                    cdly[i][0] <= chain;
                    chain       = chain - cdly[i][DIFF_M-1];
                end
                dout       <= chain;
                dout_valid <= 1'b1;
            end
        end
    end

endmodule
"""


def emit(project: Path, top: str = "cic_decimator") -> Path:
    p = declared_parameters(project)
    wout = output_width(p)
    src = {f: next(n for n in names if n in _rows(project))
           for f, names, _ in DECLARED_FIELDS}
    text = _RTL.format(
        top=top, r=p["decimation_factor"], n=p["stages"],
        m=p["differential_delay"], win=p["input_width"], wout=wout,
        r_src=src["decimation_factor"], n_src=src["stages"],
        m_src=src["differential_delay"], win_src=src["input_width"])
    out_dir = _pl.rtl_dir(project)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{top}.sv"
    out.write_text(text, encoding="utf-8")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project_dir")
    ap.add_argument("--top", default="cic_decimator")
    a = ap.parse_args(argv)
    try:
        out = emit(Path(a.project_dir), a.top)
    except Refusal as r:
        print(f"REFUSE: {r}", file=_sys.stderr)
        return 2
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
