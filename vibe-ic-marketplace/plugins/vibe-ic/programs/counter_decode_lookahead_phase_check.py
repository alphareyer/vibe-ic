#!/usr/bin/env python3
"""Report a registered decode of the value used to advance a counter.

The deterministic fact is structural: a decode and a counter update on one
edge share a next-value expression. That is not, by itself, a functional
error. Pre-update and post-update decodes have different phases; only the
public temporal contract can choose between them. A coupled binary/Gray
pointer update can be necessary to preserve queue capacity and stop an
empty read. Do not introduce a stale pointer merely to match a reference.

Default findings remain advisory, and --strict retains its existing shape
refusal. Neither mode proves intended timing from structure alone. Validate
reset, the first transitions, wrap and admission boundaries from the visible
contract; ask for clarification when the phase is not determined. Period and
duty alone cannot establish phase. No oracle or design identity selects it.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROGRAMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROGRAMS_DIR))

from _atomic_artefact import write_json  # noqa: E402 - vibe-ic#1082

#: `// …` to end of line, and `/* … */` across newlines. Stripped before any
#: structural scan: a comment naming a pattern must never decide a verdict.
_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)

#: `always @(posedge …)` / `always_ff` header.
_EDGE_BLOCK = re.compile(r"\balways(?:_ff)?\s*@\s*\(([^)]*)\)", re.I)

#: A nonblocking assignment: `lhs <= rhs;`
_NONBLOCKING = re.compile(r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*<=\s*([^;]+);")

#: `name + something` / `name + 1'b1` — a counter lookahead.
def _increment_of(expr: str, name: str) -> bool:
    pat = re.compile(r"\b" + re.escape(name) + r"\s*\+\s*[^\s;)]+")
    return bool(pat.search(expr))


def _strip(src: str) -> str:
    return _COMMENT.sub(" ", src or "")


def _blocks(src: str):
    """(header, body) for each edge-triggered always block, brace-matched."""
    out = []
    for m in _EDGE_BLOCK.finditer(src):
        sens = m.group(1)
        if "posedge" not in sens.lower() and "negedge" not in sens.lower():
            continue
        i = src.find("begin", m.end())
        if i < 0:
            continue
        depth, j = 0, i
        while j < len(src):
            word = re.match(r"\b(begin|end)\b", src[j:])
            if word:
                depth += 1 if word.group(1) == "begin" else -1
                j += len(word.group(1))
                if depth == 0:
                    break
                continue
            j += 1
        out.append((sens, src[i:j]))
    return out


def _lookahead_wires(src: str) -> dict:
    """`wire x = cnt + 1;` → {x: cnt}. Continuous lookahead definitions."""
    found = {}
    for m in re.finditer(
        r"\b(?:wire|logic)\s*(?:\[[^\]]*\])?\s*([A-Za-z_]\w*)\s*=\s*([^;]+);", src
    ):
        name, rhs = m.group(1), m.group(2)
        for cand in re.findall(r"\b([A-Za-z_]\w*)\s*\+", rhs):
            if cand != name:
                found[name] = cand
                break
    return found


def scan(src: str) -> list:
    """Findings: a level decoded from a counter lookahead, registered on the
    same edge that advances that counter."""
    s = _strip(src)
    aliases = _lookahead_wires(s)
    findings = []
    for sens, body in _blocks(s):
        assigns = [(m.group(1), m.group(2), m.group(0))
                   for m in _NONBLOCKING.finditer(body)]
        # counters this block advances: `c <= c + …`
        advanced = {lhs for lhs, rhs, _ in assigns if _increment_of(rhs, lhs)}
        # …or advanced through a lookahead alias: `c <= c_next`
        for lhs, rhs, _ in assigns:
            for alias, base in aliases.items():
                if base == lhs and re.search(r"\b" + re.escape(alias) + r"\b", rhs):
                    advanced.add(lhs)
        if not advanced:
            continue
        for lhs, rhs, stmt in assigns:
            if lhs in advanced:
                continue                      # the counter's own update
            for counter in advanced:
                direct = _increment_of(rhs, counter)
                via_alias = any(
                    base == counter and re.search(r"\b" + re.escape(alias) + r"\b", rhs)
                    for alias, base in aliases.items()
                )
                if not (direct or via_alias):
                    continue
                # A DECODE, not an accumulation. `data_out <= acc + data_in` is
                # the accumulator's own arithmetic result, not a level decoded
                # from a phase counter: reporting it was a false positive on a
                # correct design. Require the lookahead to feed a COMPARISON or
                # an encoding call — the shapes that publish a phase.
                decodes = bool(
                    re.search(r"[<>]=?|==|!=|\?", rhs)          # comparison / mux
                    or re.search(r"\b\w*gray\w*\s*\(", rhs, re.I)  # bin2gray(...)
                )
                if decodes:
                    findings.append({
                        "signal": lhs,
                        "counter": counter,
                        "via": "alias" if (via_alias and not direct) else "inline",
                        "statement": " ".join(stmt.split())[:160],
                        "sensitivity": " ".join(sens.split()),
                    })
                    break
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Report a same-edge counter next-value decode; "
                    "phase correctness requires the public temporal contract.")
    ap.add_argument("rtl", nargs="+", help="RTL file(s) to scan")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero on a finding (default: advisory)")
    ap.add_argument("--json", help="write the structured result here")
    args = ap.parse_args()

    rows = []
    for path in args.rtl:
        p = Path(path)
        if not p.is_file():
            print(f"CANNOT CHECK: {path} is not a readable file")
            return 2
        for f in scan(p.read_text(encoding="utf-8", errors="replace")):
            f["file"] = path
            rows.append(f)

    result = {
        "schema": "vibeic.counter_decode_lookahead_phase.v1",
        "findings": rows,
        "verdict": "FINDING" if rows else "PASS",
    }
    if args.json:
        # vibe-ic#1082: the declared report appears under its final name only
        # once it is complete. `write_text` truncates first, so a death
        # mid-write leaves a short file that `required_outputs` cannot tell
        # from a finished one.
        write_json(args.json, result)

    if not rows:
        print("PASS: no level decoded from a counter lookahead on the counter's "
              "own edge.")
        return 0

    print(f"FINDING: {len(rows)} signal(s) decoded from a counter LOOKAHEAD on "
          f"the same edge that advances the counter (structural fact, "
          f"not a functional defect by itself):")
    for f in rows:
        print(f"  {f['file']}: {f['signal']} <- lookahead of {f['counter']} "
              f"({f['via']})")
        print(f"      {f['statement']}")
    print("  Preserve the declared pre-update or post-update phase. Validate "
          "reset, first transitions, wrap and accepted-transfer boundaries. "
          "Do not shift a phase or delay a pointer to match a hidden checker; "
          "an unspecified or conflicting phase requires clarification.")
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
