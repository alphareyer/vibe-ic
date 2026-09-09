#!/usr/bin/env python3
"""analog_adc_enob_fit.py — A4 producer: converter waveform -> SNDR -> ENOB.

THE GAP THIS CLOSES, measured on this tree:

  * `analog_adc_enob_corner_check.py` (the R12 per-corner ENOB gate) reads
    `phase3/analog/<block>/corner_results.json` and takes each corner's `enob`
    field, or an `sndr_db`/`sndr`/`snr_db` field via ENOB=(SNDR-1.76)/6.02.
  * Exactly ONE program in this tree WRITES `corner_results.json`:
    `analog_real_corner_sweep.py`. It names `enob` once, in a comment at line
    728 saying the ADC-level target is not handled there, and never writes an
    `enob` or `sndr` field. `grep -c sndr` over that file is 0.
  * No program in `programs/` computes SNDR or ENOB from a waveform at all.

So the consumer exists, the producer does not, and the ENOB gate can only ever
report SKIP ("no per-corner SNDR/ENOB data present") no matter how many corners
a converter run completes. That is the failure this file removes.

§4.05. Only the TOOL OUTPUT (the transient dump) and the design INPUT
(`spec.json`: the ENOB target and the stimulus the design declares) are read.
No oracle, golden, harness or reference value is opened.

SNDR is in-band signal power against every other in-band bin — noise AND
distortion, DC excluded — over fs/(2*OSR), which is the band an oversampled
converter is graded on. ENOB = (SNDR_dB - 1.76) / 6.02, the same relation the
gate uses, so producer and consumer cannot drift apart.

REFUSAL, not a number, whenever the record cannot support the fit: fewer than
`--min-bins` in-band bins, a tone outside the band, a degenerate spectrum, or a
record that ends before the measurement window opens. A converter whose record
is too short is UNMEASURED, never 0 bits and never a pass.
"""
from __future__ import annotations

import argparse
import json
import math
import os as _os
import sys
from pathlib import Path
from typing import List, Optional, Tuple

# `programs/` is a flat directory whose modules import each other by BARE name.
# Python only puts a file's own directory on `sys.path` when it runs as
# `__main__`; loaded by path (how the gates and the wiring audit load a
# program) it does not, so the bare sibling import below would raise.
if _os.path.dirname(_os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))

# EVERY DECLARED REPORT GOES THROUGH THIS (vibe-ic#1082). `atomic_artifact_write_check`
# is a RATCHET: it carries a residual baseline of programs that still write
# their declared destination with a bare `.write_text`, and a NEW program that
# does so raises the count — measured here as
# "1 program(s) newly write a declared report destination without
# _atomic_artefact: analog_adc_enob_fit.py:239". `corner_results.json` is
# converted too, and that one matters more than the flagged line: it is a
# SHARED artefact several gates read, so a torn write there is read by somebody.
from _atomic_artefact import write_json                              # noqa: E402

PROGRAM = "analog_adc_enob_fit"
_SNDR_SLOPE = 6.02
_SNDR_INTERCEPT = 1.76


def read_dump(path: Path, col: int = 1, tcol: int = 0
              ) -> Tuple[List[float], List[float]]:
    """`wrdata`-style whitespace columns: time first, then value columns.

    The deck's own dump contract is "first declared vector, first value
    column", which is `col=1` here. Header and comment rows are skipped by
    trying to parse them, not by matching a banner.
    """
    t: List[float] = []
    y: List[float] = []
    with path.open() as fh:
        for line in fh:
            s = line.split()
            if len(s) <= max(col, tcol):
                continue
            try:
                tv = float(s[tcol])
                yv = float(s[col])
            except ValueError:
                continue
            t.append(tv)
            y.append(yv)
    return t, y


def resample_zoh(t: List[float], y: List[float], fs: float,
                 t0: float, t1: float) -> List[float]:
    """Zero-order hold onto a uniform grid.

    A converter bitstream is piecewise constant between clock edges, so ZOH is
    the correct reconstruction from a variable-step transient; linear
    interpolation would invent transitions the design never made and flatter
    the spectrum.
    """
    n = int(round((t1 - t0) * fs))
    out = [0.0] * n
    j = 0
    for k in range(n):
        tk = t0 + k / fs
        while j + 1 < len(t) and t[j + 1] <= tk:
            j += 1
        out[k] = y[j]
    return out


def sndr_db(x: List[float], fs: float, f_tone: float, osr: int,
            min_bins: int = 8) -> Tuple[Optional[float], Optional[int],
                                        Optional[str]]:
    """In-band SNDR, or (None, None, why) when the record cannot answer."""
    n = len(x)
    if n < 2:
        return None, None, "resampled record has fewer than 2 samples"
    band = int(n / (2 * osr))
    if band < min_bins:
        return None, band, (f"in-band bins {band} < {min_bins}: the record is "
                            f"too short to fit a resolution at OSR {osr}")
    k = int(round(f_tone * n / fs))
    if k <= 0 or k >= band:
        return None, band, (f"tone bin {k} is outside the in-band range "
                            f"[1,{band}) — the tone is not in the graded band")
    try:
        import numpy as np  # noqa: WPS433
    except ImportError:
        return None, band, "numpy unavailable: cannot fit a spectrum"
    # A CONSTANT RECORD CARRIES NO SIGNAL, AND THE `<= 0` GUARD BELOW CANNOT
    # SEE THAT. Mean-removal of a flat record leaves floating-point DUST, not
    # zeros: MEASURED on an all-1.0 record, `sig` and `noi` both come out at
    # ~1e-32 — strictly positive — so the degenerate-spectrum guard never fires
    # and the program reported `SNDR=-322.837 dB  ENOB=-53.92 bit` with rc 0.
    # That is a number invented from numerical residue, which is precisely what
    # this program exists not to do.
    #
    # The refusal is stated on the INPUT, where the physics is: a record whose
    # variation is below 2**-40 of its own scale is constant to any measurement
    # this program could make — 40 bit is finer than any ENOB the flow can
    # express, so no such record can carry one. This REFUSES MORE than before;
    # it does not widen anything.
    _peak = max(abs(v) for v in x) or 1.0
    if (max(x) - min(x)) <= _peak * (2.0 ** -40):
        return None, band, ("constant record: its variation is below 2**-40 of "
                            "its own scale, so it carries no signal to measure")
    m = sum(x) / n
    w = [0.5 - 0.5 * math.cos(2 * math.pi * i / n) for i in range(n)]
    xw = np.asarray([(x[i] - m) * w[i] for i in range(n)])
    p = np.abs(np.fft.rfft(xw)) ** 2
    sig = float(p[max(1, k - 2):k + 3].sum())
    tot = float(p[1:band].sum())
    noi = tot - sig
    if sig <= 0 or noi <= 0:
        return None, band, "degenerate spectrum: signal or noise power <= 0"
    return 10 * math.log10(sig / noi), band, None


def _stim(spec: Optional[dict], name: str) -> Optional[float]:
    """A stimulus parameter the DESIGN declares, from spec.json."""
    for s in (spec or {}).get("specs") or []:
        if isinstance(s, dict) and str(s.get("name", "")).strip().lower() == name:
            for key in ("target", "value", "min"):
                v = s.get(key)
                if isinstance(v, (int, float)) and math.isfinite(v):
                    return float(v)
    return None


def merge_corner(results: Path, row: dict) -> dict:
    try:
        doc = json.loads(results.read_text())
    except Exception:
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    corners = [c for c in (doc.get("corners") or [])
               if isinstance(c, dict) and c.get("name") != row["name"]]
    corners.append(row)
    corners.sort(key=lambda c: str(c.get("name")))
    doc["corners"] = corners
    doc.setdefault("_provenance", f"{PROGRAM}: SNDR fitted from the transient "
                                  f"dump; ENOB=(SNDR-1.76)/6.02")
    results.parent.mkdir(parents=True, exist_ok=True)
    write_json(results, doc)
    return doc


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dump", type=Path, help="wrdata-style transient dump")
    ap.add_argument("--corner", required=True, help="corner name for the row")
    ap.add_argument("--block-dir", type=Path, default=None,
                    help="phase3/analog/<block>; merges the row into its "
                         "corner_results.json and reads its spec.json")
    ap.add_argument("--fclk", type=float, default=None)
    ap.add_argument("--tone", type=float, default=None)
    ap.add_argument("--osr", type=int, default=None)
    ap.add_argument("--from-s", type=float, default=0.0)
    ap.add_argument("--to-s", type=float, default=None)
    ap.add_argument("--col", type=int, default=1)
    ap.add_argument("--min-bins", type=int, default=8)
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args(argv)

    spec = None
    if a.block_dir is not None:
        try:
            spec = json.loads((a.block_dir / "spec.json").read_text())
        except Exception:
            spec = None
    fclk = a.fclk if a.fclk is not None else _stim(spec, "fclk")
    tone = a.tone if a.tone is not None else _stim(spec, "tone")
    osr = a.osr if a.osr is not None else _stim(spec, "osr")
    missing = [n for n, v in (("fclk", fclk), ("tone", tone), ("osr", osr))
               if v is None]
    if missing:
        print(f"[{PROGRAM}] rc2 — the design INPUT does not declare "
              f"{', '.join(missing)}; pass it explicitly. A fit against an "
              f"assumed stimulus would not be a measurement.", file=sys.stderr)
        return 2
    osr = int(osr)

    if not a.dump.is_file():
        print(f"[{PROGRAM}] rc2 — dump not present: {a.dump}", file=sys.stderr)
        return 2
    t, y = read_dump(a.dump, col=a.col)
    rec = {"program": PROGRAM, "corner": a.corner, "dump": str(a.dump),
           "fclk_hz": fclk, "tone_hz": tone, "osr": osr,
           "samples_raw": len(t)}
    if len(t) < 2:
        rec.update(status="UNMEASURED", reason="dump has fewer than 2 rows",
                   sndr_db=None, enob=None)
    else:
        t1 = a.to_s if a.to_s is not None else t[-1]
        rec["record_end_s"] = t[-1]
        rec["window_s"] = [a.from_s, t1]
        if t1 <= a.from_s:
            rec.update(status="UNMEASURED", sndr_db=None, enob=None,
                       reason=(f"record ends at {t[-1]:.6e} s, before the "
                               f"measurement window opens at {a.from_s:.6e} s"))
        else:
            x = resample_zoh(t, y, fclk, a.from_s, t1)
            s, band, why = sndr_db(x, fclk, tone, osr, a.min_bins)
            rec["samples_resampled"] = len(x)
            rec["in_band_bins"] = band
            if s is None:
                rec.update(status="UNMEASURED", sndr_db=None, enob=None,
                           reason=why)
            else:
                rec.update(status="MEASURED", sndr_db=round(s, 3),
                           enob=round((s - _SNDR_INTERCEPT) / _SNDR_SLOPE, 3),
                           reason=None)
    if a.json is not None:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        write_json(a.json, rec)
    if a.block_dir is not None:
        row = {"name": a.corner, "status": rec["status"]}
        if rec["status"] == "MEASURED":
            row.update(sndr_db=rec["sndr_db"], enob=rec["enob"])
        else:
            row["reason"] = rec["reason"]
        merge_corner(a.block_dir / "corner_results.json", row)
    if rec["status"] == "MEASURED":
        print(f"[{PROGRAM}] MEASURED {a.corner}: SNDR={rec['sndr_db']} dB  "
              f"ENOB={rec['enob']} bit ({rec['in_band_bins']} in-band bins)")
        return 0
    print(f"[{PROGRAM}] UNMEASURED {a.corner}: {rec['reason']}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
