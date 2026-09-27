#!/usr/bin/env python3
"""A reused IP's size parameters come from the DESIGN DOCUMENTS, not its default.

MEASURED (subservient x gf180mcuD, FX_D13). The design documents state
``memsize = 1024`` (L3's Design Parameters table, L8 ``override: true``) and a
10-bit ``o_sram_addr``. The staged vendor top declares ``parameter memsize =
512`` and ``aw = $clog2(memsize)``, and was synthesised as staged: yosys
reported ``Parameter \\memsize = 512`` and the netlist carries a 9-bit
``o_sram_waddr``. Nothing carried the documents' value to the IP, so the
flow built a different memory than the one the design asks for, and every
later reader (pad ring, rename pairs, spec conformance) saw a width the
documents contradict.

THE RULE, program first. For every parameter the reused top declares in its
``#( ... )`` header:

  * DOCUMENT VALUES are collected with their source: an L8_RTL_CONSTANTS
    ``parameters[]`` entry (an ``override`` entry's ``value``, else its
    ``default``) and an L9 ``parameters[]`` entry's ``default``. Two different
    stated values REFUSE (``DOC_PARAMETER_CONTRADICTION``), naming both.
  * WIDTH EVIDENCE: a port of the reused top whose range uses the parameter,
    and whose width the DOCUMENT states -- an L9 top_ports entry of the same
    name that the document (not the staged-top harvest alone) declares, or the
    L9 name a SOURCE_MANIFEST ``renamed_interfaces`` pair maps onto it.
  * THE IP'S OWN MATH decides: the header is evaluated in order with the
    document values substituted (``aw = $clog2(memsize)``), and each evidenced
    port width must equal the document's width. A mismatch REFUSES
    (``DOC_IP_PARAMETER_CONTRADICTION``) with both values and their sources. A
    derived parameter (its default references another) that a document also
    states must equal what the IP computes, or it refuses the same way.
  * NO STATED VALUE, BUT WIDTH EVIDENCE: the value is chosen only among the
    values a document ALLOWS (the parameter row's legal-value list). Exactly
    one satisfying the width evidence is DERIVED; none refuses; several is
    UNRESOLVED -- the AI backup chooses with ``--choose name=value`` and this
    program verifies that choice against the same allowed set and the same
    width evidence before accepting it.
  * No document says anything about a parameter: it keeps the IP default, and
    the record says so.

The result is ``reports/phase2/reused_ip_parameters.json``: per parameter the
resolved value, every piece of evidence, and ``overrides`` -- the independent
parameters whose document value differs from the IP default. Those are what
synthesis applies to the top (``top_overrides``); a derived parameter is
never overridden, it follows.

APPLIED WHERE EVERY READER LOOKS. ``--apply`` writes each override into the
staged top's own ``#( ... )`` default (``phase2/stage1/rtl/<file>``), the same
edit ``design_one_shot_runner._apply_l8_param_overrides`` makes to an emitted
wrapper's copied block, and records the original default beside it in
``.<top>__param_overrides.json``. Synthesis (phase 2, phase 3 direct and
LibreLane), lint, the testbenches (which instantiate the DUT with its
defaults) and LEC then all elaborate the SAME memory; a ``chparam`` on one
synthesis path would have left the simulation and the netlist disagreeing.
Only a PASS record is applied.

ENFORCEMENT: blocking -- ``design_one_shot_runner.step_reused_ip_parameters``
runs it right after the reused-IP RTL is staged; a non-zero exit is that
step's FAIL (or NOT_MEASURED).

Exit: 0 PASS / NOT_APPLICABLE, 1 REFUSE / UNRESOLVED, 2 NOT_MEASURED (the
top or its header could not be read -- unread is not empty).

chip-AGNOSTIC: parameter and port names come only from the design's own RTL
header and documents.
"""
from __future__ import annotations

import os as _os
import sys as _sys

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))

import argparse  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Dict, List, Optional, Tuple  # noqa: E402

from _atomic_artefact import write_json  # noqa: E402

PROGRAM = "reused_ip_param_derive"
REPORT_REL = "reports/phase2/reused_ip_parameters.json"
L8_REL = "phase1/generated_docs/L8_RTL_CONSTANTS.json"
L9_REL = "phase1/generated_docs/L9_INTEGRATION_SPEC.json"
RTL_REL = "phase2/stage1/rtl"
MANIFEST_REL = "phase2/stage1/rtl/SOURCE_MANIFEST.json"

_INT = re.compile(r"^\s*`?\s*(\d+)\s*`?\s*$")
_IDENT = re.compile(r"[A-Za-z_]\w*")


# --------------------------------------------------------------------------- #
# the reused top's header
# --------------------------------------------------------------------------- #
def _blank(text: str, strings: bool = True) -> str:
    """``text`` with every comment byte and every string-literal byte (quotes
    kept) replaced by a space: SAME LENGTH, so an offset found in it is an
    offset in ``text``. Newlines survive, so line structure does too.

    One scanner, so `//` inside a string is not a comment and `"` inside a
    comment is not a string. ``strings=False`` blanks comments only (a
    string default keeps its bytes)."""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if text[k] != "\n":
                    out[k] = " "
            i = j
        elif c == '"':
            k = i + 1
            while k < n and text[k] != '"' and text[k] != "\n":
                if text[k] == "\\":
                    if strings:
                        out[k] = " "
                    k += 1
                if k < n and text[k] != "\n" and strings:
                    out[k] = " "
                k += 1
            i = k + 1
        else:
            i += 1
    return "".join(out)


def _strip_comments(text: str) -> str:
    return _blank(text)


def _header_block(text: str, top: str) -> Optional[Tuple[int, int]]:
    """``(start, end)`` offsets of ``top``'s ``#( ... )`` contents in
    ``text``; (0, 0)-style ``(-1, -1)`` when the module has no header; None
    when the module is absent or the header is unterminated. Found on the
    blanked copy, so a comment or a string cannot move it."""
    src = _blank(text)
    m = re.search(r"\bmodule\s+" + re.escape(top) + r"\b", src)
    if not m:
        return None
    hm = re.compile(r"\s*(?:import[^;]*;\s*)*#\s*\(").match(src, m.end())
    if not hm:
        return (-1, -1)
    depth = 1
    for i in range(hm.end(), len(src)):
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0:
                return (hm.end(), i)
    return None


def _header_chunks(blank_block: str) -> List[Tuple[str, int, int, int]]:
    """``[(name, chunk_start, value_start, value_end)]`` per declaration in a
    BLANKED header block: offsets into that block, value span trimmed. The
    one parser `header_parameters` and `apply_overrides` share."""
    out: List[Tuple[str, int, int, int]] = []
    depth, start = 0, 0
    bounds = []
    for i, ch in enumerate(blank_block):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            bounds.append((start, i))
            start = i + 1
    bounds.append((start, len(blank_block)))
    for s, e in bounds:
        chunk = blank_block[s:e]
        eq = chunk.find("=")
        if eq < 0:
            continue
        lhs = re.sub(r"^\s*(?:parameter|localparam)\b", "", chunk[:eq])
        names = _IDENT.findall(re.sub(r"\[[^\]]*\]", " ", lhs))
        if not names:
            continue
        vs, ve = s + eq + 1, e
        while vs < ve and blank_block[vs].isspace():
            vs += 1
        while ve > vs and blank_block[ve - 1].isspace():
            ve -= 1
        out.append((names[-1], s, vs, ve))
    return out


def header_parameters(text: str, top: str) -> Optional[List[Tuple[str, str]]]:
    """``[(name, default_expr)]`` of ``top``'s ``#( ... )`` block, in order.

    None when ``module top`` is absent; [] when it declares no parameter.
    The default is the ORIGINAL text of the value span (a string default
    keeps its bytes); the span itself is found on the blanked copy."""
    span = _header_block(text, top)
    if span is None:
        return None
    if span == (-1, -1):
        return []
    start, end = span
    blank_block = _blank(text)[start:end]
    block = text[start:end]
    return [(name, " ".join(_blank(block[vs:ve], strings=False).split()))
            for name, _s, vs, ve in _header_chunks(blank_block)]


def evaluate_header(params: List[Tuple[str, str]],
                    fixed: Dict[str, int]) -> Dict[str, Optional[int]]:
    """Each parameter's value, in header order, with ``fixed`` substituted.

    The IP's own math: a parameter not in ``fixed`` takes its default
    expression evaluated over the values before it. Unresolvable: None."""
    from verilog_width_resolve import eval_width_expr
    env: Dict[str, int] = {}
    out: Dict[str, Optional[int]] = {}
    for name, expr in params:
        val = fixed[name] if name in fixed else eval_width_expr(expr, env)
        out[name] = val
        if val is not None:
            env[name] = int(val)
    return out


def _range_width(width_expr: str, env: Dict[str, int]) -> Optional[int]:
    from verilog_width_resolve import eval_width_expr
    m = re.fullmatch(r"\s*\[(.+):(.+)\]\s*", width_expr or "")
    if not m:
        return 1 if not (width_expr or "").strip() else None
    hi = eval_width_expr(m.group(1), env)
    lo = eval_width_expr(m.group(2), env)
    if hi is None or lo is None:
        return None
    return abs(hi - lo) + 1


# --------------------------------------------------------------------------- #
# the documents
# --------------------------------------------------------------------------- #
def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        doc = json.loads(path.read_text(errors="replace"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _as_int(raw: Any) -> Optional[int]:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    m = _INT.match(str(raw if raw is not None else ""))
    return int(m.group(1)) if m else None


def _allowed_values(text: Any) -> List[int]:
    """The legal-value LIST a parameter row states (``256 / 512 / 1024``).

    Only a list of at least two integers separated by ``/``, ``,`` or ``|`` is
    read; a range, prose, or one number is not a list of allowed values."""
    s = str(text or "")
    m = re.match(r"\s*`?(\d+)`?(?:\s*[/,|]\s*`?(\d+)`?)+", s)
    if not m:
        return []
    return [int(v) for v in re.findall(r"\d+", m.group(0))]


def document_parameter_values(project: Path) -> Dict[str, Dict[str, Any]]:
    """``{name: {"stated": [...], "allowed": [...]}}`` from L8 and L9."""
    out: Dict[str, Dict[str, Any]] = {}
    for rel in (L8_REL, L9_REL):
        doc = _read_json(project / rel)
        for entry in (doc or {}).get("parameters") or []:
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            name = str(entry["name"]).strip("` ")
            slot = out.setdefault(name, {"stated": [], "allowed": []})
            field = "value" if entry.get("override") else "default"
            val = _as_int(entry.get(field))
            src = {"document": rel, "field": f"parameters[{name}].{field}",
                   "source": entry.get("source"),
                   "extraction_strategy": entry.get("extraction_strategy")}
            if val is not None:
                slot["stated"].append(dict(src, value=val))
            allowed = _allowed_values(entry.get("type"))
            if allowed:
                slot["allowed"].append(dict(src, field=f"parameters[{name}].type",
                                            values=allowed))
    return out


#: Phase 1's own markers for a width that is NOT a document statement.
#: `shipped_rtl_header` rows and the fill note are the phase-1 crosswalk
#: (`phase1_doc_one_shot_runner`, v1.6.555) reading the vendor header at the
#: IP's DEFAULT parameters; `implicit_1bit_default` is a grid table's default
#: for a row that stated no width (v1.6.427).
SHIPPED_RTL_STRATEGY = "shipped_rtl_header"
RTL_FILL_NOTE = "filled from shipped RTL header:"
IMPLICIT_WIDTH_TAG = "implicit_1bit_default"


def stated_width(entry: Dict[str, Any]) -> Optional[int]:
    """The width THE DOCUMENT states for an L9 top_ports entry, else None.

    None -- not evidence -- when the entry states no width, when its width is
    phase 1's implicit 1-bit default, or when the width came from the RTL:
    the staged-top harvest alone added the entry, the shipped-RTL-header
    crosswalk added it, or that crosswalk filled its blank width. Each of
    those is the IP's DEFAULT speaking. The ONE predicate for this rule and
    for 15.5ic's `io_pad_chip_top_gen._reconcile_port_widths`."""
    from _staged_top_module import EXTRACTION_STRATEGY as staged_only
    strategy = str(entry.get("extraction_strategy") or "")
    if strategy in (staged_only, SHIPPED_RTL_STRATEGY):
        return None
    if IMPLICIT_WIDTH_TAG in strategy:
        return None
    note = str(entry.get("evidence") or "")
    at = note.find(RTL_FILL_NOTE)
    if at >= 0 and "width" in note[at + len(RTL_FILL_NOTE):].split("|")[0]:
        return None
    width = _as_int(entry.get("width"))
    if width is None and _as_int(entry.get("msb")) is not None \
            and _as_int(entry.get("lsb")) is not None:
        width = abs(_as_int(entry["msb"]) - _as_int(entry["lsb"])) + 1
    return width if width and width > 0 else None


def width_pairs(manifest: Dict[str, Any]) -> List[Tuple[Dict[str, Any], str]]:
    """The rename pairs that may carry a DOCUMENT width onto an RTL port.

    Authored ``renamed_interfaces`` entries, and program-derived
    ``derived_pad_pairs`` entries whose ``rule`` is ``R1`` ONLY: R1 pairs one
    document port with the implemented ports it split into (read/write), so
    the document's width is theirs. An R2 pair joins ports that merely share
    a family atom and a pad side, which says nothing about a width."""
    out: List[Tuple[Dict[str, Any], str]] = []
    for pair in manifest.get("renamed_interfaces") or []:
        if isinstance(pair, dict):
            out.append((pair, f"{MANIFEST_REL} renamed_interfaces"))
    from _l_doc_pad_placement import DERIVED_PAD_PAIRS_KEY
    for pair in manifest.get(DERIVED_PAD_PAIRS_KEY) or []:
        if isinstance(pair, dict) and str(pair.get("rule") or "") == "R1":
            out.append((pair, f"{MANIFEST_REL} derived_pad_pairs (R1)"))
    return out


def document_port_widths(project: Path) -> Dict[str, List[Dict[str, Any]]]:
    """``{rtl port: [{width, l9, source}]}`` -- widths the DOCUMENT states.

    `stated_width` decides what is a document statement. A pair from
    `width_pairs` maps its L9 names' widths onto its RTL names."""
    l9 = _read_json(project / L9_REL) or {}
    doc_ports: Dict[str, Dict[str, Any]] = {}
    for p in l9.get("top_ports") or []:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        width = stated_width(p)
        if width is not None:
            doc_ports[str(p["name"])] = {"width": width,
                                         "evidence": p.get("evidence")}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for name, info in doc_ports.items():
        out.setdefault(name, []).append(
            {"width": info["width"], "l9": name, "source": L9_REL,
             "via": "same name"})
    manifest = _read_json(project / MANIFEST_REL) or {}
    for pair, via in width_pairs(manifest):
        for rtl in pair.get("rtl") or []:
            for l9_name in pair.get("l9") or []:
                if l9_name in doc_ports:
                    out.setdefault(str(rtl), []).append(
                        {"width": doc_ports[l9_name]["width"], "l9": l9_name,
                         "source": L9_REL, "via": via})
    return out


# --------------------------------------------------------------------------- #
# the rule
# --------------------------------------------------------------------------- #
def _top_and_text(project: Path) -> Tuple[Optional[str], Optional[str],
                                          Optional[Path]]:
    l9 = _read_json(project / L9_REL) or {}
    top = str(l9.get("top_module") or "").strip() or None
    if not top:
        return None, None, None
    rtl = project / RTL_REL
    for path in sorted(rtl.glob("*.v")) + sorted(rtl.glob("*.sv")):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if re.search(r"\bmodule\s+" + re.escape(top) + r"\b",
                     _strip_comments(text)):
            return top, text, path
    return top, None, None


def derive(project: Path, choose: Optional[Dict[str, int]] = None
           ) -> Dict[str, Any]:
    """The record (see the module docstring). Pure over the project's files."""
    from module_port_audit import parse_modules
    project = Path(project)
    explicit = dict(choose or {})
    rec: Dict[str, Any] = {"program": PROGRAM, "parameters": {},
                           "overrides": {}, "findings": []}
    top, text, path = _top_and_text(project)
    rec["top"] = top
    # A VERIFIED AI CHOICE IS REMEMBERED, AND RE-VERIFIED. `--apply` records it
    # in the sidecar; every later run (the runner never passes --choose) takes
    # it back as a choice and judges it against the documents AS THEY ARE NOW.
    recorded: Dict[str, int] = {}
    if top:
        side = _read_json(sidecar_path(project, top)) or {}
        for k, v in (side.get("ai_choice") or {}).items():
            if _as_int(v) is not None:
                recorded[str(k)] = int(_as_int(v))
    choose = dict(recorded, **explicit)
    rec["ai_choice_recorded"] = recorded
    if top is None or text is None or path is None:
        rec.update(verdict="NOT_MEASURED", rc=2,
                   reason=(f"no L9 top_module in {L9_REL}" if top is None else
                           f"no file under {RTL_REL} defines module {top}"))
        return rec
    rec["top_file"] = str(path.relative_to(project))
    header = header_parameters(text, top)
    if header is None:
        rec.update(verdict="NOT_MEASURED", rc=2,
                   reason=f"module {top} header could not be read")
        return rec
    if not header:
        rec.update(verdict="NOT_APPLICABLE", rc=0,
                   reason=f"module {top} declares no parameter")
        return rec
    names = [n for n, _e in header]
    exprs = dict(header)
    derived_params = {n for n, e in header
                      if set(_IDENT.findall(e)) & (set(names) - {n})}
    ip_default = evaluate_header(header, {})
    # comments stripped first: the ANSI header parser loses the port that
    # follows a `//` line (measured: `o_sram_waddr` after `//SRAM interface`)
    modules = [m for m in parse_modules(_strip_comments(text), str(path))
               if m.name == top]
    ports = modules[0].ports if modules else {}
    doc_vals = document_parameter_values(project)
    doc_widths = document_port_widths(project)

    # the ports whose width the IP computes from its parameters, and that a
    # document states a width for
    # IN SCOPE: a parameter that sets a port width of the top, directly or
    # through a derived parameter. Any other parameter a document states
    # (a feature switch) is reported, never applied, by this rule.
    width_idents = set()
    for port in ports.values():
        width_idents |= set(_IDENT.findall(port.width_expr or "")) & set(names)
    in_scope = set(width_idents)
    changed = True
    while changed:
        changed = False
        for n in list(in_scope):
            for dep in set(_IDENT.findall(exprs[n])) & set(names):
                if dep not in in_scope:
                    in_scope.add(dep)
                    changed = True
    rec["in_scope"] = sorted(in_scope)
    evidenced = []
    for pname, port in ports.items():
        idents = set(_IDENT.findall(port.width_expr or "")) & set(names)
        if idents and doc_widths.get(pname):
            evidenced.append((pname, port.width_expr, doc_widths[pname]))

    def width_mismatches(fixed: Dict[str, int]) -> List[Dict[str, Any]]:
        vals = evaluate_header(header, fixed)
        env = {k: v for k, v in vals.items() if v is not None}
        bad = []
        for pname, wexpr, docs in evidenced:
            got = _range_width(wexpr, env)
            for d in docs:
                if got is not None and got != d["width"]:
                    bad.append({"port": pname, "ip_range": wexpr,
                                "ip_width": got, "document_width": d["width"],
                                "document_port": d["l9"],
                                "document": d["source"], "via": d["via"]})
        return bad

    fixed: Dict[str, int] = {}
    refusals: List[Dict[str, Any]] = []
    undecided: Dict[str, List[int]] = {}
    for name in names:
        info = doc_vals.get(name) or {"stated": [], "allowed": []}
        if name not in in_scope:
            rec["parameters"][name] = {
                "ip_default_expr": exprs[name], "ip_default": ip_default[name],
                "in_scope": False, "document_values": info["stated"],
                "note": "sets no port width of the top; not decided by this "
                        "rule"}
            continue
        entry: Dict[str, Any] = {
            "ip_default_expr": exprs[name], "ip_default": ip_default[name],
            "derived_in_ip": name in derived_params,
            "document_values": info["stated"],
            "document_allowed": info["allowed"]}
        rec["parameters"][name] = entry
        stated = sorted({s["value"] for s in info["stated"]})
        if len(stated) > 1:
            refusals.append({
                "rule": "DOC_PARAMETER_CONTRADICTION", "parameter": name,
                "message": (f"the documents state {name} = "
                            + " and ".join(f"{s['value']} ({s['document']} "
                                           f"{s['field']}, from {s['source']})"
                                           for s in info["stated"]))})
            continue
        if stated and name not in derived_params:
            fixed[name] = stated[0]
            entry["value"], entry["decided_by"] = stated[0], "document"

    # a derived parameter a document states is a constraint on the IP's math:
    # checked AFTER every independent parameter is decided (below), and used
    # meanwhile to choose among the values the documents allow
    stated_derived = {}
    for name in names:
        info = doc_vals.get(name) or {"stated": []}
        stated = sorted({s["value"] for s in info["stated"]})
        if name in in_scope and name in derived_params and len(stated) == 1:
            stated_derived[name] = (stated[0], info["stated"][0])

    def derived_mismatches(fixed_now: Dict[str, int]) -> List[str]:
        vals_now = evaluate_header(header, fixed_now)
        return [n for n, (v, _s) in stated_derived.items()
                if vals_now[n] is not None and vals_now[n] != v]

    # what each parameter reaches through the IP's own derivations
    # (param -> derived param -> ... -> a port range), transitively
    reach: Dict[str, set] = {n: {n} for n in names}
    changed = True
    while changed:
        changed = False
        for n in names:
            for d in names:
                if d not in reach[n] and set(_IDENT.findall(exprs[d])) & reach[n]:
                    reach[n].add(d)
                    changed = True

    # a parameter no document states, on which evidenced widths depend
    for name in names:
        entry = rec["parameters"][name]
        if name in recorded and "value" in entry and name not in explicit:
            if entry.get("decided_by") == "document":
                # the documents now state it: they outrank a recorded choice
                entry["ai_choice_superseded"] = recorded[name]
        if ("value" in entry or name in derived_params
                or name not in in_scope):
            continue
        depends = ([e for e in evidenced
                    if set(_IDENT.findall(e[1])) & reach[name]]
                   + [d for d in stated_derived if d in reach[name]])
        allowed = sorted({v for a in entry["document_allowed"]
                          for v in a["values"]})
        if not depends or not allowed:
            continue
        fits = [v for v in allowed
                if not width_mismatches(dict(fixed, **{name: v}))
                and not derived_mismatches(dict(fixed, **{name: v}))]
        if name in choose:
            v = choose[name]
            if v not in allowed:
                refusals.append({
                    "rule": "CHOICE_NOT_ALLOWED_BY_DOCUMENTS",
                    "parameter": name,
                    "message": f"{name} = {v} is not among the values the "
                               f"documents allow {allowed}"})
            elif v not in fits:
                refusals.append({
                    "rule": "CHOICE_CONTRADICTS_DOCUMENT_WIDTHS",
                    "parameter": name,
                    "message": f"{name} = {v} gives port widths the "
                               f"documents contradict: "
                               f"{width_mismatches(dict(fixed, **{name: v}))}"})
            else:
                fixed[name] = v
                entry["value"], entry["decided_by"] = v, "ai_choice_verified"
                entry["ai_choice_source"] = ("--choose" if name in explicit
                                             else "recorded (sidecar)")
            continue
        if len(fits) == 1:
            fixed[name] = fits[0]
            entry["value"], entry["decided_by"] = fits[0], "document_widths"
        elif not fits:
            refusals.append({
                "rule": "DOC_IP_PARAMETER_CONTRADICTION", "parameter": name,
                "message": f"no value of {name} the documents allow "
                           f"{allowed} gives the port widths they state"})
        else:
            undecided[name] = fits

    # the stated derived values against the IP's math on the DECIDED set
    vals = evaluate_header(header, fixed)
    for name, (value, src) in stated_derived.items():
        entry = rec["parameters"][name]
        entry["value"], entry["decided_by"] = vals[name], "ip_math"
        if undecided or vals[name] is None or vals[name] == value:
            continue
        refusals.append({
            "rule": "DOC_IP_PARAMETER_CONTRADICTION", "parameter": name,
            "message": (f"{name}: the document states {value} "
                        f"({src['document']}), the IP computes {exprs[name]} "
                        f"= {vals[name]} under the decided values "
                        f"{fixed or '(IP defaults)'}")})

    # the decided set against every evidenced width (an undecided parameter
    # is not judged at its IP default: the choice still to come decides it)
    for m in ([] if undecided else width_mismatches(fixed)):
        refusals.append({
            "rule": "DOC_IP_PARAMETER_CONTRADICTION", "parameter": None,
            "message": (f"port {m['port']} {m['ip_range']} is "
                        f"{m['ip_width']} bit(s) under the documents' "
                        f"parameter values {fixed or '(IP defaults)'}, but "
                        f"{m['document']} states {m['document_port']} is "
                        f"{m['document_width']} ({m['via']})"),
            "evidence": m})
    rec["width_evidence"] = [{"port": p, "ip_range": w, "documents": d}
                             for p, w, d in evidenced]
    final = evaluate_header(header, fixed)
    for name in names:
        rec["parameters"][name].setdefault("value", final[name])
        rec["parameters"][name].setdefault(
            "decided_by", "ip_default" if name in in_scope else "out_of_scope")
    rec["overrides"] = {n: v for n, v in fixed.items()
                        if n not in derived_params and v != ip_default[n]}
    rec["ai_choice"] = {n: e["value"] for n, e in rec["parameters"].items()
                        if e.get("decided_by") == "ai_choice_verified"}
    rec["resolved"] = final
    if refusals:
        rec.update(verdict="REFUSE", rc=1, findings=refusals)
    elif undecided:
        rec.update(verdict="UNRESOLVED", rc=1, undecided=undecided,
                   reason=("several values the documents allow fit: "
                           + "; ".join(f"{k} in {v}" for k, v in
                                       undecided.items())
                           + ". The AI backup chooses one with --choose "
                             "name=value; this program verifies it"))
    else:
        rec.update(verdict="PASS", rc=0)
    return rec


def top_overrides(project: Path) -> Optional[Dict[str, int]]:
    """The overrides a PASS record grants, read back for synthesis.

    None when there is no PASS record: a caller must not apply a value the
    rule did not accept."""
    rec = _read_json(Path(project) / REPORT_REL)
    if not rec or rec.get("verdict") != "PASS":
        return None
    ovr = rec.get("overrides") or {}
    return {str(k): int(v) for k, v in ovr.items()
            if isinstance(v, int) and not isinstance(v, bool)}


def sidecar_path(project: Path, top: str) -> Path:
    return Path(project) / RTL_REL / f".{top}__param_overrides.json"


def apply_overrides(project: Path, rec: Dict[str, Any]) -> Dict[str, Any]:
    """Write a PASS record's overrides into the staged top's header defaults,
    and record what was applied and the verified AI choice in the sidecar.

    Returns ``{name: {"from": old_expr, "to": value}}`` for what changed.

    THE EDIT IS FOUND WHERE THE PARSER READS. The value span of each override
    comes from `_header_chunks` over the BLANKED header (comments and string
    contents are spaces of the same length), the same chunks
    `header_parameters` reads the defaults from, so a comment that mentions
    ``name = ...`` is never the target and a string default is never cut.
    The original bytes are edited only at that span. THEN THE HEADER IS READ
    BACK: every override must evaluate to its target under the IP's own
    header math, else the file is restored and this raises -- a PASS that
    left the IP at its default is not a PASS. A name with no default in the
    header raises too, never a parameter inserted."""
    if rec.get("verdict") != "PASS":
        return {}
    project = Path(project)
    path = project / rec["top_file"]
    top = rec["top"]
    changed: Dict[str, Any] = {}
    overrides = dict(rec.get("overrides") or {})
    if overrides:
        text = path.read_text(errors="replace")
        span = _header_block(text, top)
        if span is None or span == (-1, -1):
            raise ValueError(f"module {top} has no readable #( ) header in "
                             f"{path}")
        start, end = span
        chunks = {}
        for name, _s, vs, ve in _header_chunks(_blank(text)[start:end]):
            if name in chunks:
                raise ValueError(f"parameter {name} is declared twice in the "
                                 f"{top} header of {path}")
            chunks[name] = (start + vs, start + ve)
        edits = []
        for name, value in sorted(overrides.items()):
            if name not in chunks:
                raise ValueError(f"no default for parameter {name} in the "
                                 f"{top} header of {path}")
            vs, ve = chunks[name]
            changed[name] = {"from": text[vs:ve], "to": value}
            edits.append((vs, ve, str(value)))
        new = text
        for vs, ve, value in sorted(edits, reverse=True):
            new = new[:vs] + value + new[ve:]
        from _atomic_artefact import write_text
        write_text(path, new)
        after = header_parameters(new, top)
        got = evaluate_header(after or [], {})
        wrong = {n: got.get(n) for n, v in overrides.items()
                 if got.get(n) != v}
        if wrong:
            write_text(path, text)
            raise ValueError(f"the header read back after the edit does not "
                             f"carry the overrides: wanted {overrides}, got "
                             f"{wrong}; {path} restored")
    side = sidecar_path(project, top)
    prior = _read_json(side) or {}
    original = dict(prior.get("original") or {})
    for name, ch in changed.items():
        original.setdefault(name, ch["from"])
    applied = dict(prior.get("applied") or {})
    applied.update({n: str(v) for n, v in overrides.items()})
    ai_choice = dict(rec.get("ai_choice") or {})
    if changed or ai_choice or prior:
        write_json(side, {
            "program": PROGRAM, "file": rec["top_file"],
            "applied": applied, "original": original,
            "ai_choice": ai_choice, "report": REPORT_REL})
    return changed


def refresh_derived_pad_pairs(project: Path) -> Dict[str, Any]:
    """Re-derive what staging derived from the header this program changed.

    `renamed_interface_derive` (D9) derives SOURCE_MANIFEST's
    `derived_pad_pairs` when the IP is staged, from the staged header at its
    DEFAULT parameters, and D2's acceptance rule inside it rejects a pair
    whose widths differ at those defaults. MEASURED on a subservient-shaped
    fixture: at memsize 512 the `o_mem_addr -> o_mem_[rw]addr` R1 pair is
    rejected (9 vs 10 bits); at the documents' 1024 it is derived. Once
    `apply_overrides` has changed the header, that staging-time derivation
    describes an IP that no longer exists, so it is refreshed through D9's
    own `apply_to_manifest` (the one writer of those keys)."""
    import renamed_interface_derive as _rid
    mf_path = Path(project) / MANIFEST_REL
    mf = _read_json(mf_path)
    if mf is None:
        raise ValueError(f"{MANIFEST_REL} is absent or unreadable")
    mf = _rid.apply_to_manifest(Path(project), mf)
    write_json(mf_path, mf)
    derivation = mf.get("renamed_interfaces_derivation") or {}
    return {"derived_pad_pairs": len(mf.get(_rid.DERIVED_KEY) or []),
            "verdict": derivation.get("verdict"),
            "rejected": len(derivation.get("rejected") or [])}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("project")
    ap.add_argument("--choose", action="append", default=[],
                    metavar="NAME=VALUE",
                    help="the AI backup's choice for an UNRESOLVED parameter; "
                         "verified, never trusted")
    ap.add_argument("--json", default=REPORT_REL)
    ap.add_argument("--apply", action="store_true",
                    help="write a PASS record's overrides into the staged top")
    a = ap.parse_args(argv)
    choose: Dict[str, int] = {}
    for item in a.choose:
        name, _, val = item.partition("=")
        if not name or _as_int(val) is None:
            print(f"{PROGRAM}: --choose wants NAME=INTEGER, got {item!r}",
                  file=_sys.stderr)
            return 2
        choose[name.strip()] = int(_as_int(val))
    project = Path(a.project)
    rec = derive(project, choose)
    if a.apply and rec.get("verdict") == "PASS":
        try:
            rec["applied"] = apply_overrides(project, rec)
        except (OSError, ValueError) as exc:
            rec.update(verdict="NOT_MEASURED", rc=2,
                       reason=f"the overrides could not be applied: {exc}")
        if rec.get("applied"):
            try:
                rec["derived_pad_pairs_refreshed"] = \
                    refresh_derived_pad_pairs(project)
            except Exception as exc:  # noqa: BLE001 — named, never swallowed
                rec.update(verdict="NOT_MEASURED", rc=2,
                           reason=(f"the header changed but the pad pairs "
                                   f"derived from it could not be refreshed: "
                                   f"{type(exc).__name__}: {exc}"))
    prior = _read_json(sidecar_path(project, rec["top"])) if rec.get("top") \
        else None
    if prior:
        rec["applied_before"] = prior
    out = Path(a.json)
    write_json(out if out.is_absolute() else project / out, rec)
    print(f"{PROGRAM}: {rec['verdict']}"
          + (f" overrides={rec['overrides']}" if rec.get("overrides") else "")
          + (f" -- {rec.get('reason')}" if rec.get("reason") else ""))
    for f in rec.get("findings") or []:
        print(f"  {f['rule']}: {f['message']}")
    return int(rec["rc"])


if __name__ == "__main__":
    _sys.exit(main())
