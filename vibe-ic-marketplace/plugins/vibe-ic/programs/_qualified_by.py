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

Proposing the field in Phase 1 and the D1 reviewer's checklist (the semantic
intent of R-0929-X-QUALIFIED-2/-3) are wired into the D1 expert track
separately, on top of that track's pending rework (D1FIX). This module is the
schema and the one trusted reader; with no signed field, every data output
keeps the strict rule (X after reset release is a FAIL).

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
from pathlib import Path
from typing import Dict, List, Optional, Tuple

FIELD = "qualified_by"
LEVELS = {"high": "1", "low": "0"}
EXPECTATION_PREFIX = "qualified_by:"
EXPECTATION_LAYER = "L9_INTEGRATION_SPEC"
#: The design INPUT a basis may quote (§4.05: never an oracle or a harness).
DESIGN_INPUT_GLOBS = ("input/design_input.txt", "input/docs/**/*",
                      "phase1/input_doc/**/*")
_PORT_KEYS = ("ports", "top_ports", "top_module_pins")

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
