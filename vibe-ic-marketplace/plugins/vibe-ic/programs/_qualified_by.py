"""_qualified_by.py — the ONE reader of a data output's declared qualifier.

R-0929-X-QUALIFIED-4 (root, IC expert, 2026-09-29). Two oracles that parsed port
DESCRIPTIONS inside `reset_invariant_oracle_tb_gen` produced false PASSes — 3,
then 23 more (review_wave58/XQROLES_review.json): low-active spellings read as
high, 1-bit status outputs exempted, cross-channel pairs on shared generic
words, 高態/高電平 read as valid. Interpreting prose is spec capture, not
verification. So the qualifier is a STRUCTURED, REVIEWED fact:

    L9 port row (ports / top_ports / top_module_pins) of a DATA output:
      "qualified_by": {
        "port": "<1-bit port of the same channel>",
        "active_level": "high" | "low",
        "basis": [{"file": "<design-input path>", "line": <n>,
                   "quote": "<exact text on that line>"}, ...]
      }

and it is TRUSTED only when ALL of these hold (`trusted_qualifiers`):

  1. the field is well formed (port named, level exactly high|low, >= 1 basis);
  2. every basis quotation is present VERBATIM on the named line of a
     design-input file (input/docs, phase1/input_doc, input/design_input.txt) —
     a quotation that is not in the input is not a basis;
  3. the D1 IC-expert second pass SIGNED it: the D1 receipt
     (`ai_signed_judgement.check(project, "D1")`) is valid for the current
     bytes — which include this L9 and the expert's expectations — AND those
     signed expectations carry the D1 expectation for exactly this fact,
     `{"id": "qualified_by:<output>", "qualified_by": {"output", "port",
     "active_level"}}`;
  4. the landed strict rules: the output is a multi-bit output, the qualifier
     a 1-bit port other than the output, and neither is the clock or reset.

No signature -> the field is IGNORED, and `trusted_qualifiers` says why.
Nothing here reads a description: the field is the only input.

Phase 1 may PROPOSE the field (`write_review_request`): per declared port
table of the design input it lists the candidate (multi-bit output, 1-bit port)
pairs WITH the exact file:line quotations, and the D1 checklist (the semantic
intent of R-0929-X-QUALIFIED-2/-3). The program decides nothing: it neither
writes L9 nor judges a role; the expert does both, and signs.

chip-AGNOSTIC: no design, port or PDK literal.
"""
from __future__ import annotations

# --- sibling-import path (vibe-ic#2104) ------------------------------------
import os as _os                                                    # noqa: E402
import sys as _sys                                                  # noqa: E402

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
# ---------------------------------------------------------------------------

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

FIELD = "qualified_by"
LEVELS = {"high": "1", "low": "0"}
EXPECTATION_PREFIX = "qualified_by:"
EXPECTATION_LAYER = "L9_INTEGRATION_SPEC"
REVIEW_REQUEST = "qualified_by_review.json"
#: The design INPUT a basis may quote (§4.05: never an oracle or a harness).
DESIGN_INPUT_GLOBS = ("input/design_input.txt", "input/docs/**/*",
                      "phase1/input_doc/**/*")
_PORT_KEYS = ("ports", "top_ports", "top_module_pins")

#: The D1 review checklist for this field — R-0929-X-QUALIFIED-2/-3's semantic
#: intent, applied by the reviewer, never by a program.
CHECKLIST = (
    "the qualified port is a DATA output (write data, a responder's read data, "
    "a channel's payload) — never a flag, error, status, ready, busy, done, "
    "interrupt, enable, valid, strobe or request, even if its text mentions data",
    "the qualifier is that SAME channel's enable / write strobe / valid / "
    "acknowledge, named as a ROLE by the input — an active-level phrase "
    "(高有效, active high) is not a role",
    "both ports belong to ONE channel of ONE declared interface / port group "
    "(e.g. 寫入資料 <-> 寫入啟用 of one SRAM port); a generic shared word "
    "(from, pin, bit, write alone) is not a channel",
    "active_level is stated by the input for the qualifier; if the input does "
    "not state it, do not sign the field",
    "exactly one qualifier fits; two candidates -> no field",
    "every basis entry is an exact quotation of the design input with its "
    "file and line",
)


def _json(path: Path) -> Optional[dict]:
    try:
        value = json.loads(path.read_text(errors="replace"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _l9(project: Path) -> Optional[dict]:
    import _path_layout as _pl
    import l_doc_consumer_contract as _ldc
    doc = _json(_pl.generated_docs_dir(Path(project))
                / "L9_INTEGRATION_SPEC.json")
    return _ldc.l_doc_fields(doc) if doc is not None else None


def declared_fields(project: Path) -> Dict[str, dict]:
    """{output: raw qualified_by value} as the L9 port rows carry it."""
    l9 = _l9(project) or {}
    out: Dict[str, dict] = {}
    for key in _PORT_KEYS:
        for row in l9.get(key) or []:
            if isinstance(row, dict) and FIELD in row and row.get("name"):
                out.setdefault(str(row["name"]), row[FIELD])
    return out


def _design_input_files(project: Path) -> Dict[str, Path]:
    project = Path(project)
    files = {}
    for pattern in DESIGN_INPUT_GLOBS:
        for p in project.glob(pattern):
            if p.is_file():
                files[p.relative_to(project).as_posix()] = p
    return files


def basis_refusal(project: Path, basis) -> Optional[str]:
    """None when every entry quotes the design input verbatim at file:line."""
    if not isinstance(basis, list) or not basis:
        return "basis must be a non-empty list of {file, line, quote}"
    files = _design_input_files(project)
    for i, b in enumerate(basis):
        if not isinstance(b, dict):
            return f"basis[{i}] is not a {{file, line, quote}} record"
        rel, line, quote = b.get("file"), b.get("line"), b.get("quote")
        if not (isinstance(rel, str) and isinstance(line, int)
                and isinstance(quote, str) and quote.strip()):
            return f"basis[{i}] needs a file, an integer line and a quote"
        path = files.get(rel)
        if path is None:
            return (f"basis[{i}] names {rel!r}, which is not a design-input "
                    f"file of this project")
        lines = path.read_text(errors="replace").splitlines()
        if not 1 <= line <= len(lines) or quote.strip() not in lines[line - 1]:
            return (f"basis[{i}] quote {quote.strip()[:60]!r} is not on "
                    f"{rel}:{line}")
    return None


def _signed_expectations(project: Path) -> Tuple[Optional[list], str]:
    import ai_signed_judgement as _aj
    ok, why = _aj.check(Path(project), "D1")
    if not ok:
        return None, why
    ev = [p for p in _aj.evidence(Path(project), "D1")
          if p.name == "l_doc_expectations.json"]
    doc = _json(ev[0]) if ev else None
    exps = (doc or {}).get("expectations")
    if not isinstance(exps, list):
        return None, ("the D1 receipt is valid but its evidence carries no "
                      "expectations list")
    return exps, why


def _signed_fact(exps: list, output: str) -> Optional[dict]:
    for e in exps:
        if (isinstance(e, dict)
                and e.get("id") == f"{EXPECTATION_PREFIX}{output}"
                and e.get("layer") == EXPECTATION_LAYER
                and isinstance(e.get(FIELD), dict)):
            return e[FIELD]
    return None


def trusted_qualifiers(
        project: Path,
        outputs: List[Tuple[str, str]],
        inputs: List[Tuple[str, str]],
        excluded: Tuple[str, ...] = (),
) -> Tuple[Dict[str, List[Dict[str, str]]], Dict[str, str]]:
    """({output: [qualifier row]}, {output: why ignored}).

    A row is `{"qualifier", "active": "1"|"0", "evidence", "basis":
    "signed_field"}` — the shape the reset-invariant emitter consumes."""
    fields = declared_fields(project)
    if not fields:
        return {}, {}
    width = {n: w for n, w in (outputs + inputs)}
    out_names = {n for n, _w in outputs}
    found: Dict[str, List[Dict[str, str]]] = {}
    ignored: Dict[str, str] = {}
    exps, sig_why = _signed_expectations(project)
    for d, raw in sorted(fields.items()):
        if not isinstance(raw, dict):
            ignored[d] = "qualified_by is not a {port, active_level, basis} record"
            continue
        q, level = raw.get("port"), raw.get("active_level")
        if not isinstance(q, str) or level not in LEVELS:
            ignored[d] = ("qualified_by needs a port and an active_level of "
                          "exactly 'high' or 'low'")
            continue
        if d not in out_names or not width.get(d) or d in excluded:
            ignored[d] = (f"{d} is not a multi-bit data output of this DUT; "
                          f"a 1-bit or control output must be known after "
                          f"reset release")
            continue
        if q == d or q not in width or width[q] or q in excluded:
            ignored[d] = (f"qualifier {q!r} is not a 1-bit port of this DUT "
                          f"other than the output, the clock and the reset")
            continue
        why = basis_refusal(project, raw.get("basis"))
        if why:
            ignored[d] = why
            continue
        if exps is None:
            ignored[d] = f"not signed by the D1 IC-expert second pass: {sig_why}"
            continue
        fact = _signed_fact(exps, d)
        if fact != {"output": d, "port": q, "active_level": level}:
            ignored[d] = (f"the signed D1 expectations carry no "
                          f"'{EXPECTATION_PREFIX}{d}' fact equal to this field "
                          f"(signed: {fact!r})")
            continue
        quotes = "; ".join(f"{b['file']}:{b['line']} \"{b['quote'].strip()}\""
                           for b in raw["basis"])
        found[d] = [{"qualifier": q, "active": LEVELS[level],
                     "basis": "signed_field",
                     "evidence": (f"L9 {FIELD} of {d} (port {q}, active "
                                  f"{level}), D1-signed ({sig_why}); basis "
                                  f"{quotes}")}]
    return found, ignored


# ── Phase-1 PROPOSAL: candidates + checklist for the D1 reviewer ────────────
_TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}")
_BACKTICK_ID_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_$]*)`")
_WIDTH_ONE_RE = re.compile(r"^\s*1(?:\s*-?\s*bit)?\s*$", re.IGNORECASE)


def _tables(path: Path, rel: str) -> List[dict]:
    """Each markdown table of a design-input file: its rows, as quoted."""
    lines = path.read_text(errors="replace").splitlines()
    heading, out, i = "", [], 0
    while i < len(lines):
        if lines[i].lstrip().startswith("#"):
            heading = lines[i].lstrip("# ").strip()
        if (_TABLE_ROW_RE.match(lines[i]) and i + 1 < len(lines)
                and _TABLE_SEP_RE.match(lines[i + 1])):
            rows, j = [], i + 2
            while j < len(lines) and _TABLE_ROW_RE.match(lines[j]):
                cells = [c.strip() for c in
                         _TABLE_ROW_RE.match(lines[j]).group(1).split("|")]
                names = _BACKTICK_ID_RE.findall(cells[0]) if cells else []
                if names:
                    rows.append({"ports": names, "cells": cells,
                                 "file": rel, "line": j + 1,
                                 "quote": lines[j].strip()})
                j += 1
            if len(rows) >= 2:
                out.append({"file": rel, "heading": heading,
                            "line": i + 1, "rows": rows})
            i = j
            continue
        i += 1
    return out


def proposals(project: Path) -> List[dict]:
    """Candidate (output, 1-bit port) pairs of ONE declared table, each with
    the exact quotations — for the reviewer to accept or reject. Widths and
    directions come from the L9 port rows; nothing is inferred from text."""
    l9 = _l9(project) or {}
    port = {}
    for key in _PORT_KEYS:
        for r in l9.get(key) or []:
            if isinstance(r, dict) and r.get("name"):
                port.setdefault(str(r["name"]), r)

    def _w(r):
        w = r.get("width")
        return int(w) if isinstance(w, int) else None

    cands = []
    for rel, path in sorted(_design_input_files(project).items()):
        for t in _tables(path, rel):
            members = [(n, row) for row in t["rows"] for n in row["ports"]
                       if n in port]
            outs = [(n, row) for n, row in members
                    if str(port[n].get("direction")).startswith("output")
                    and (_w(port[n]) or 0) > 1]
            quals = [(n, row) for n, row in members if _w(port[n]) == 1]
            for d, drow in outs:
                for q, qrow in quals:
                    if q == d:
                        continue
                    cands.append({
                        "output": d, "port": q,
                        "group": {"file": rel, "heading": t["heading"],
                                  "table_line": t["line"]},
                        "basis": [{"file": rel, "line": drow["line"],
                                   "quote": drow["quote"]},
                                  {"file": rel, "line": qrow["line"],
                                   "quote": qrow["quote"]}]})
    return cands


def write_review_request(project: Path, out_dir: Path) -> Path:
    """The D1 pack's qualified_by request: field schema, checklist, the
    expectation that signs it, and the candidates. Writes nothing into L9."""
    from _atomic_artefact import write_text as _atomic_write_text
    body = {
        "schema": "vibeic.qualified-by-review.v1",
        "ruling": "R-0929-X-QUALIFIED-4",
        "field": {
            "where": "the L9 port row of the DATA output (ports/top_ports)",
            "shape": {FIELD: {"port": "<1-bit port>",
                              "active_level": "high|low",
                              "basis": [{"file": "<design-input path>",
                                         "line": "<int>",
                                         "quote": "<exact text>"}]}},
        },
        "checklist": list(CHECKLIST),
        "signing": {
            "expectation": {
                "id": f"{EXPECTATION_PREFIX}<output>",
                "layer": EXPECTATION_LAYER, "field_path": "top_ports",
                FIELD: {"output": "<output>", "port": "<port>",
                        "active_level": "high|low"},
                "expected_tokens": ["<output>", "<port>", "high|low"],
                "evidence": ["the basis quotations"]},
            "then": "sign D1 (reports/audit/ai_judgements/D1.json) over the "
                    "bytes that carry the field and this expectation",
            "unsigned": "an unsigned or unmatched field is ignored by every "
                        "consumer",
        },
        "candidates": proposals(project),
        "decides_nothing": ("candidates are every multi-bit output paired "
                            "with every 1-bit port of the same design-input "
                            "table; the program judges no role and writes no "
                            "field"),
    }
    path = Path(out_dir) / REVIEW_REQUEST
    _atomic_write_text(str(path), json.dumps(body, indent=2,
                                             ensure_ascii=False) + "\n")
    return path


def point_handoff_at_review(out_dir: Path,
                            handoff_name: str = "ic_expert_agent_handoff.json"
                            ) -> bool:
    """Name the request in the pack's handoff so the D1 reviewer is told to
    read it. True when the handoff file was updated."""
    from _atomic_artefact import write_text as _atomic_write_text
    path = Path(out_dir) / handoff_name
    doc = _json(path)
    if doc is None:
        return False
    doc[FIELD + "_review"] = {
        "file": REVIEW_REQUEST, "ruling": "R-0929-X-QUALIFIED-4",
        "ask": ("for each candidate that passes the checklist, write the "
                "qualified_by field on the L9 row of the data output AND the "
                "matching 'qualified_by:<output>' expectation; sign D1 only "
                "over those bytes. Leave any candidate you cannot verify "
                "unwritten — an absent field is the strict, safe default")}
    _atomic_write_text(str(path), json.dumps(doc, indent=2,
                                             ensure_ascii=False) + "\n")
    return True
