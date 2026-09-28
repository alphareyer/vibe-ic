#!/usr/bin/env python3
"""Top-module port declarations of a structural (gate-level) Verilog netlist:
parse them, check them, and remove EXACT duplicates.

WHY (N9, measured 2026-09-28 in image 0.3.83, OpenROAD 26Q3-3002):
`pdngen` (with its default add-pins) creates a temporary BTerm for each supply
net that has none and destroys it again when it gets no BPin. The database
callback adds a top-level STA port on creation, but cannot remove it on
destruction ("sta::NetworkEdit does not support port removal",
dbSta.cc inDbBTermDestroy). The next `pdngen` in the same session creates a
new BTerm of the same name, and `dbNetwork::makeTopPort` adds a SECOND port of
that name. `write_verilog` writes every STA port, so a session that runs
`pdngen` N times (the flow's PDN EM pre-sweep does, once per candidate) writes
`inout VDD;` / `inout VSS;` N times. subservient x gf180mcuD: 5 copies, which
is invalid Verilog -- iverilog "'VDD' has already been declared", yosys
"Duplicate module port" -- while OpenSTA reads it silently.

The rule here is structural and chip-agnostic: a port name may appear once in
the header and be declared once. An IDENTICAL repeat (same name, same
direction, same range) carries no information and is removed. A CONFLICTING
repeat (same name, different direction or range) is refused, never guessed.

Scope: the non-ANSI header OpenROAD writes (`module top (a, b);` followed by
`input a;` ...). An ANSI header, or a module this parser cannot find, is
reported UNPARSED, never judged.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

_HEADER_RE = re.compile(r"(?ms)^(\s*module\s+\\?(?P<top>\S+?)\s*\()(?P<ports>.*?)(\)\s*;)")
_DECL_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<dir>input|output|inout)\b(?P<rest>[^;]*);[ \t]*\r?$",
    re.M)
_RANGE_RE = re.compile(r"^\s*(?:wire\s+|reg\s+)?(?P<range>\[[^\]]*\])?\s*(?P<names>.*)$")


def _top_span(text: str, top: str) -> Optional[Tuple[re.Match, int]]:
    for m in _HEADER_RE.finditer(text):
        if m.group("top").lstrip("\\") == top:
            end = text.find("endmodule", m.end())
            return m, (len(text) if end < 0 else end)
    return None


def _header_tokens(ports: str) -> Optional[List[Tuple[str, str]]]:
    """`[(name, raw_token)]` of a non-ANSI header, or None for an ANSI one.

    `raw_token` is the text between two commas EXACTLY as written, so a
    rebuilt header keeps the whitespace that TERMINATES a Verilog escaped
    identifier (`\\a.b ,`): dropping it glues the comma into the name, and
    no Verilog reader accepts the result (review wave 7, N9 MINOR). An
    escaped identifier is recognised BEFORE the ANSI heuristic, so a `[`
    inside one (`\\d[0] `, a bit-blasted port) is part of a name, not a
    range."""
    out: List[Tuple[str, str]] = []
    for raw in ports.split(","):
        tok = re.sub(r"/\*.*?\*/|//[^\n]*", " ", raw, flags=re.S).strip()
        if not tok:
            continue
        if tok.startswith("\\"):
            out.append((tok.split()[0], raw))
            continue
        if re.search(r"\b(input|output|inout)\b|\[", tok):
            return None  # ANSI-style header: out of scope
        out.append((tok.split()[-1], raw))
    return out


def _header_names(ports: str) -> Optional[List[str]]:
    toks = _header_tokens(ports)
    return None if toks is None else [n for n, _raw in toks]


def parse(text: str, top: str) -> Dict[str, object]:
    """`{status, header, decls}`; status is PARSED or UNPARSED (with why)."""
    span = _top_span(text, top)
    if span is None:
        return {"status": "UNPARSED", "why": f"no `module {top} (...);` header"}
    m, body_end = span
    header = _header_names(m.group("ports"))
    if header is None:
        return {"status": "UNPARSED", "why": "ANSI-style header (not what this checks)"}
    decls: List[Tuple[str, str, str, int, int]] = []  # name, dir, range, start, end
    for d in _DECL_RE.finditer(text, m.end(), body_end):
        r = _RANGE_RE.match(d.group("rest"))
        rng = (r.group("range") or "").replace(" ", "") if r else ""
        for name in (r.group("names") if r else d.group("rest")).split(","):
            name = name.strip()
            if name:
                decls.append((name, d.group("dir"), rng, d.start(), d.end()))
    return {"status": "PARSED", "header": header, "decls": decls,
            "header_span": (m.start("ports"), m.end("ports"))}


def problems(text: str, top: str) -> Tuple[str, List[str]]:
    """(`PARSED`|`UNPARSED`, findings). An empty finding list means every port
    is named once in the header and declared exactly once."""
    p = parse(text, top)
    if p["status"] != "PARSED":
        return "UNPARSED", [str(p["why"])]
    out: List[str] = []
    header: List[str] = p["header"]  # type: ignore[assignment]
    seen: Dict[str, int] = {}
    for n in header:
        seen[n] = seen.get(n, 0) + 1
    out += [f"port `{n}` named {k}x in the module header"
            for n, k in seen.items() if k > 1]
    declared: Dict[str, List[Tuple[str, str]]] = {}
    for name, dr, rng, _s, _e in p["decls"]:  # type: ignore[misc]
        declared.setdefault(name, []).append((dr, rng))
    out += [f"port `{n}` declared {len(v)}x ({', '.join(d + r for d, r in v)})"
            for n, v in declared.items() if len(v) > 1]
    out += [f"header port `{n}` has no direction declaration"
            for n in seen if n not in declared]
    out += [f"`{n}` is declared but not in the module header"
            for n in declared if n not in seen]
    return "PARSED", out


def dedupe(text: str, top: str) -> Tuple[str, Dict[str, object]]:
    """Remove EXACT duplicate header names and identical duplicate
    declarations of `top`. Returns `(text, record)`; the text is unchanged
    when a repeat CONFLICTS or the module cannot be parsed."""
    p = parse(text, top)
    if p["status"] != "PARSED":
        return text, {"status": "UNPARSED", "why": p["why"], "removed": []}
    by_name: Dict[str, List[Tuple[str, str, int, int]]] = {}
    for name, dr, rng, s, e in p["decls"]:  # type: ignore[misc]
        by_name.setdefault(name, []).append((dr, rng, s, e))
    conflicts = [n for n, v in by_name.items() if len({(d, r) for d, r, _s, _e in v}) > 1]
    if conflicts:
        return text, {"status": "CONFLICT", "removed": [],
                      "why": "same port declared with different direction or "
                             "range: " + ", ".join(sorted(conflicts))}
    # every later declaration line of an already-declared name goes; a line
    # holding several names is only removed when ALL of them are repeats
    line_names: Dict[Tuple[int, int], List[str]] = {}
    for name, _d, _r, s, e in p["decls"]:  # type: ignore[misc]
        line_names.setdefault((s, e), []).append(name)
    first_seen: set = set()
    drop: List[Tuple[int, int]] = []
    removed: List[str] = []
    for (s, e), names in sorted(line_names.items()):
        repeats = [n for n in names if n in first_seen]
        first_seen.update(names)
        if repeats and len(repeats) == len(names):
            drop.append((s, e))
            removed += [f"declaration of `{n}`" for n in names]
        elif repeats:
            return text, {"status": "CONFLICT", "removed": [],
                          "why": "a repeated name shares a declaration line "
                                 "with a first declaration: " + ", ".join(repeats)}
    header: List[str] = p["header"]  # type: ignore[assignment]
    uniq = list(dict.fromkeys(header))
    removed += [f"header name `{n}` (repeat)"
                for i, n in enumerate(header) if n in header[:i]]
    if not drop and len(uniq) == len(header):
        return text, {"status": "CLEAN", "removed": []}
    new = text
    for s, e in sorted(drop, reverse=True):
        end = e + 1 if new[e:e + 1] == "\n" else e
        new = new[:s] + new[end:]
    hs, he = p["header_span"]  # type: ignore[misc]
    if len(uniq) != len(header):
        # keep each FIRST occurrence's own text (see `_header_tokens`); only
        # the repeated tokens go
        kept: List[str] = []
        seen_names: set = set()
        for name, raw in _header_tokens(new[hs:he]) or []:
            if name not in seen_names:
                seen_names.add(name)
                kept.append(raw)
        new = new[:hs] + ",".join(kept) + new[he:]
    return new, {"status": "DEDUPED", "removed": removed}
