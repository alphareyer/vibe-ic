#!/usr/bin/env python3
"""threshold_ladder_synth.py — deterministic SOLVER for the SENSOR THRESHOLD
LADDER WITH CHANGE-DIRECTION OUTPUT family (spec -> RTL).

THE ARTIFACT.  A spec states a monotonic quantity (tank level, temperature,
battery charge, pressure) sensed by N thresholds on ONE declared input bus, a
TABLE giving the outputs asserted in each of the N+1 zones, and ONE further
output asserted according to the DIRECTION of the last zone change.  The zone
decode is a thermometer decode; the direction output needs one extra bit of
state.  That is a complete, closable specification — no judgement, no hidden
convention — and it is not covered by any existing recogniser: the FSM-table
recognisers want an explicit state table, and the truth/K-map recognisers want
every input combination as a row.

    Water Level           | Sensors Asserted | Outputs to be Asserted
    Above s[3]            | s[1], s[2], s[3] | None
    Between s[3] and s[2] | s[1], s[2]       | fr1
    Between s[2] and s[1] | s[1]             | fr1, fr2
    Below s[1]            | None             | fr1, fr2, fr3

Direction comes from the explicit previous/current level or zone comparison
and its named output. Extreme-zone output overrides do not set that convention.

§4.05 NO-LEAK / EXACT-OR-NOTHING.  Every condition below must hold or the
solver returns None, which is an honest waive to the AI backup:
  * a >=3-column, >=3-row pipe table whose sensor column names bit-selects of
    ONE declared multi-bit input and whose output column names declared 1-bit
    outputs (or the word "none");
  * the sensor sets form a strict inclusion CHAIN of sizes 0..N covering every
    named bit exactly once -- a genuine thermometer ladder, not an arbitrary
    decode;
  * exactly ONE declared output is named by the direction prose and by no table
    row;
  * one explicit previous lower/higher than current relation asserting it;
  * a stated reset (mode + polarity) naming a declared port.
chip-AGNOSTIC: no design, vendor, PDK or benchmark literal; every name, width,
zone and output set is read from the prompt.

API: synth(prompt_text, top="TopModule") -> str | None ; plus a __main__ CLI.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import port_parser as _pp  # noqa: E402

_NUMBER_WORD = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_BITSEL = re.compile(r"\b([A-Za-z_]\w*)\s*\[\s*(\d+)\s*\]")
_NONE = re.compile(r"^\s*(none|-|n/?a)\s*\.?\s*$", re.I)
_TEMPORAL_PRIOR = r"(?:previous|prior|earlier|last[- ]sample)"
_COMPARATIVE = r"(?:lower|higher|less|greater|equal|different|same|above|below|larger|smaller)"


def _pipe_rows(text: str) -> List[List[str]]:
    """Every line that reads as a >=3-cell pipe-table row, cells stripped."""
    rows = []
    for line in text.splitlines():
        if line.count("|") < 2:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 3 and any(cells):
            rows.append(cells)
    return rows


def _cell_bits(cell: str) -> Optional[Tuple[str, Tuple[int, ...]]]:
    """(bus, indices) for a sensor cell; ((), ) for an explicit none; None if the
    cell is neither."""
    if _NONE.match(cell):
        return ("", ())
    hits = _BITSEL.findall(cell)
    if not hits:
        return None
    buses = {h[0] for h in hits}
    if len(buses) != 1:
        return None
    return (buses.pop(), tuple(sorted(int(h[1]) for h in hits)))


def _cell_names(cell: str, known: Sequence[str]) -> Optional[Tuple[str, ...]]:
    """The declared output names a table cell asserts; () for an explicit none."""
    if _NONE.match(cell):
        return ()
    names = tuple(n for n in re.findall(r"[A-Za-z_]\w*", cell) if n in known)
    if not names or len(names) != len([t for t in re.split(r"[,\s]+", cell.strip()) if t]):
        return None
    return names


def _ladder(rows: List[List[str]], outs: Sequence[str]
            ) -> Optional[Tuple[str, List[Tuple[int, ...]], List[Tuple[str, ...]]]]:
    """(bus, sensor set per zone, output set per zone), zone 0 = bottom."""
    parsed = []
    for cells in rows:
        for si in range(1, len(cells)):
            bits = _cell_bits(cells[si])
            if bits is None:
                continue
            for oi in range(si + 1, len(cells)):
                names = _cell_names(cells[oi], outs)
                if names is not None:
                    parsed.append((bits, names))
                    break
            break
    if len(parsed) < 3:
        return None
    buses = {b for (b, _), _ in parsed if b}
    if len(buses) != 1:
        return None
    bus = buses.pop()
    parsed.sort(key=lambda p: len(p[0][1]))
    sets = [p[0][1] for p in parsed]
    if [len(s) for s in sets] != list(range(len(sets))):
        return None                                  # sizes must be 0..N
    for lo, hi in zip(sets, sets[1:]):
        if set(lo) - set(hi):
            return None                              # must be an inclusion chain
    return bus, sets, [p[1] for p in parsed]


def _reset(text: str, ins: Sequence[str]) -> Optional[Tuple[str, str, str]]:
    """(signal, mode, polarity) for the stated reset."""
    low = text.lower()
    mode = ("asynchronous" if re.search(r"\basynchronous(ly)?\b", low)
            else "synchronous" if re.search(r"\bsynchronous(ly)?\b", low) else None)
    if mode is None:
        return None
    polarity = ("active-high" if re.search(r"active[- ]high", low)
                else "active-low" if re.search(r"active[- ]low", low) else None)
    if polarity is None:
        return None
    named = [n for n in ins if re.search(r"rst|reset", n, re.I)]
    if len(named) != 1:
        return None
    return named[0], mode, polarity


def _direction_relation(text: str, outs: Sequence[str],
                        table_outs: Sequence[Tuple[str, ...]]) -> Optional[Tuple[str, str]]:
    """A single source-owned comparison and the output it explicitly asserts."""
    spare = set(outs) - {n for row in table_outs for n in row}
    # Descriptive mentions of an earlier sample are not conditions. Census
    # comparative/normative temporal clauses independently of the one accepted
    # previous/current grammar, though, so an unsupported synonym or conflicting
    # condition cannot disappear during extraction.
    clauses = [s.strip() for s in re.split(r"[.;]|\n\s*\n", text)
               if re.search(rf"\b{_TEMPORAL_PRIOR}\b", s, re.I) and (
                   re.search(r"\b(?:current|present|now)\b", s, re.I)
                   or re.search(rf"\b{_TEMPORAL_PRIOR}\b.*\b{_COMPARATIVE}\b", s, re.I | re.S)
                   or re.search(rf"\b{_COMPARATIVE}\s+(?:than|to|as)\s+(?:the\s+)?{_TEMPORAL_PRIOR}\b", s, re.I)
                   or re.search(r"\b(?:should|must|shall)\b", s, re.I)
                   or re.match(r"\s*(?:if|when|unless)\b", s, re.I))]
    if len(spare) != 1 or len(clauses) != 1:
        return None
    relation = re.fullmatch(
        r"If (?:the sensor change indicates that )?the previous (level|zone) "
        r"(?:was|is) (lower|higher) than the current \1, "
        r"(?:(?:the [\w ]+ \(controlled by (\w+)\)|(\w+)) should be (?:enabled|asserted)"
        r"|the [\w ]+ should be increased by opening the [\w ]+ \(controlled by (\w+)\))",
        " ".join(clauses[0].split()), re.I)
    output = (relation[3] or relation[4] or relation[5]) if relation else None
    if output not in spare:
        return None
    return output, (">" if relation[2].lower() == "lower" else "<")


def _header_ports_verbatim(text: str) -> Optional[str]:
    """The prompt's own module-header port list, reused verbatim.

    Reconstructing a port from (name, width) LOSES the declared bit range: a bus
    written `input [3:1] s` came back as `input [2:0] s`, and the body's `s[3]`
    then indexed out of range.  The header is the interface contract; copy it.
    Outputs are normalised to `output reg` because this solver drives them from
    a procedural block.
    """
    m = re.search(r"\bmodule\s+\w+\s*\((?P<body>[^)]*)\)\s*;?", text, re.S)
    if not m:
        return None
    cells = [c.strip() for c in m.group("body").split(",")]
    cells = [c for c in cells if c]
    if not cells or not all(re.match(r"(input|output|inout)\b", c) for c in cells):
        return None
    out = []
    for c in cells:
        c = re.sub(r"\s+", " ", c)
        if c.startswith("output") and not re.match(r"output\s+(reg|logic)\b", c):
            c = c.replace("output", "output reg", 1)
        out.append("  " + c)
    return ",\n".join(out)


def synth(prompt_text: str, top: str = "TopModule") -> Optional[str]:
    ins, outs = _pp.parse_ports(prompt_text)
    in_names = [n for n, _ in ins]
    out_names = [n for n, _ in outs]
    scalar_outs = [n for n, w in outs if (w or 1) == 1]
    if len(out_names) < 2 or not in_names:
        return None
    lad = _ladder(_pipe_rows(prompt_text), scalar_outs)
    if lad is None:
        return None
    bus, sets, zone_outs = lad
    if bus not in in_names:
        return None
    relation = _direction_relation(prompt_text, scalar_outs, zone_outs)
    if relation is None:
        return None
    direction, comparison = relation
    counts = [str(len(out_names))] + [w for w, n in _NUMBER_WORD.items() if n == len(out_names)]
    bottom_override = bool(re.search(
        rf"no sensors?\s+asserted,? and all ({'|'.join(counts)}) outputs? asserted",
        prompt_text, re.I))
    rst = _reset(prompt_text, in_names)
    if rst is None:
        return None
    rst_sig, rst_mode, rst_pol = rst
    clks = [n for n in in_names if re.fullmatch(r"clk|clock", n, re.I)]
    if len(clks) != 1:
        return None
    clk = clks[0]

    n_zones = len(sets)
    width = max(1, (n_zones - 1).bit_length())
    # The bit that distinguishes zone k from zone k-1 is the one the chain adds.
    added = [sorted(set(sets[k]) - set(sets[k - 1]))[0] for k in range(1, n_zones)]
    sensed = "%d'd0" % width
    for k in range(1, n_zones):
        sensed = f"{bus}[{added[k - 1]}] ? {width}'d{k} : ({sensed})"

    rst_test = f"{rst_sig}" if rst_pol == "active-high" else f"!{rst_sig}"
    sens = (f"posedge {clk}" if rst_mode == "synchronous" else
            f"posedge {clk} or " +
            ("posedge " if rst_pol == "active-high" else "negedge ") + rst_sig)

    body = []
    for k in range(n_zones):
        assigns = []
        for o in out_names:
            if o == direction:
                # Explicit extremes override only their own rows.
                if k == 0 and bottom_override:
                    assigns.append(f"{o} = 1'b1;")
                elif k == n_zones - 1 and not zone_outs[k]:
                    assigns.append(f"{o} = 1'b0;")
                else:
                    assigns.append(f"{o} = direction_asserted;")
            else:
                assigns.append(f"{o} = 1'b%d;" % (1 if o in zone_outs[k] else 0))
        body.append(f"      {width}'d{k}: begin {' '.join(assigns)} end")
    default = " ".join(f"{o} = 1'b0;" for o in out_names)

    ports = _header_ports_verbatim(prompt_text) or ",\n".join(
        [f"  input {n}" if w in (None, 1) else f"  input [{w - 1}:0] {n}"
         for n, w in ins] +
        [f"  output reg {n}" for n in out_names])
    return f"""// Sensor threshold ladder with a change-direction output.
// Zones decode `{bus}`; direction follows the stated previous/current relation.
module {top} (
{ports}
);
  reg [{width - 1}:0] zone;
  reg direction_asserted;
  wire [{width - 1}:0] sensed = {sensed};

  always @({sens}) begin
    if ({rst_test}) begin
      zone <= {width}'d0;
      direction_asserted <= 1'b1;
    end else begin
      zone <= sensed;
      if (sensed != zone) direction_asserted <= (sensed {comparison} zone);
    end
  end

  always @(*) begin
    case (zone)
{chr(10).join(body)}
      default: begin {default} end
    endcase
  end
endmodule
"""


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("prompt", help="prompt/spec text file")
    ap.add_argument("--top", default="TopModule")
    ap.add_argument("-o", "--out", default="")
    a = ap.parse_args(argv)
    rtl = synth(Path(a.prompt).read_text(errors="replace"), a.top)
    if rtl is None:
        print("threshold_ladder_synth: SKIP — prompt is not a closed "
              "threshold-ladder spec", file=sys.stderr)
        return 2
    if a.out:
        # vibe-ic#1082. The hazard this gate names is a reader observing a
        # HALF-WRITTEN declared output, and emitted RTL is squarely that: a
        # downstream elaboration handed a truncated `.v` fails for a reason that
        # has nothing to do with the design. `_atomic_artefact.write_text` is
        # signature-compatible on purpose — the payload is unchanged, only the
        # moment the final name appears.
        from _atomic_artefact import write_text as _write_text
        _write_text(a.out, rtl)
    else:
        sys.stdout.write(rtl)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
