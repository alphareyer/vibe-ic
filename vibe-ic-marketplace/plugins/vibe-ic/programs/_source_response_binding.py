"""Source-verified request/ack constant response bindings for reused RTL.

The design prompt may request a response tie-off. A staged wrapper, rather than
the prompt wording, supplies the actual connection and data bits. Both the L9
producer and the chip wrapper use this verifier; neither may invent an ack or
interpret an all-zero package default as an acknowledged response.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, List

_PROG_DIR = Path(__file__).resolve().parent
if str(_PROG_DIR) not in sys.path:
    sys.path.insert(0, str(_PROG_DIR))

import _prose_polarity as _polarity
import _staged_top_module as _staged


_IDENT = r"[A-Za-z_]\w*"
_LITERAL = r"\d+'[hHbBdD][0-9a-fA-F_]+"
_RESPONSE = re.compile(
    rf"\.\s*(?P<input>{_IDENT})\s*\(\s*\{{\s*(?P<wire>{_IDENT})\s*,"
    rf"\s*(?P<ack>1'[bB][01])\s*,\s*(?P<data>{_LITERAL})\s*\}}\s*\)")
_TIE_OFF = re.compile(r"\btie[ -]?off\b", re.I)


def _mask_comments(source: str) -> str:
    return re.sub(r"/\*.*?\*/|//[^\n]*", lambda m: " " * len(m.group()),
                  source, flags=re.S)


def _match_paren(text: str, start: int) -> int:
    if start < 0 or start >= len(text) or text[start] != "(":
        return -1
    depth = 0
    for pos in range(start, len(text)):
        if text[pos] == "(":
            depth += 1
        elif text[pos] == ")":
            depth -= 1
            if depth == 0:
                return pos
    return -1


def _instances(source: str, dut: str) -> List[str]:
    blocks = []
    for match in re.finditer(r"\b" + re.escape(dut) + r"\b\s*", source):
        pos = match.end()
        if pos < len(source) and source[pos] == "#":
            pos += 1
            while pos < len(source) and source[pos].isspace():
                pos += 1
            if pos >= len(source) or source[pos] != "(":
                continue
            pos = _match_paren(source, pos) + 1
        tail = re.match(r"\s*[A-Za-z_]\w*\s*\(", source[pos:])
        if not tail:
            continue
        opening = source.find("(", pos + tail.start())
        close = _match_paren(source, opening)
        if close >= 0 and source[close + 1:].lstrip().startswith(";"):
            blocks.append(source[opening + 1:close])
    return blocks


def _port_expression(block: str, name: str) -> str:
    found = list(re.finditer(r"\.\s*" + re.escape(name) + r"\s*\(", block))
    if len(found) != 1:
        raise ValueError(f"source wrapper must bind {name} exactly once")
    opening = block.find("(", found[0].start())
    close = _match_paren(block, opening)
    if close < 0:
        raise ValueError(f"unclosed source binding {name}")
    return block[opening + 1:close].strip()


def verify_source(project: Path, dut: str, spec: Dict[str, Any],
                  modules: Dict[str, Any] | None = None) -> Dict[str, str]:
    """Return the exact bound expression, or refuse an unproved connection."""
    inp, req, rel = (spec.get(k) for k in
                     ("input_port", "request_port", "source"))
    if not all(isinstance(v, str) and re.fullmatch(_IDENT, v)
               for v in (inp, req)):
        raise ValueError("response binding needs named input/request ports")
    if not isinstance(rel, str) or not rel.startswith("input/"):
        raise ValueError("response binding source must be staged input RTL")
    root = (Path(project) / "input").resolve()
    src = (Path(project) / rel).resolve()
    if not src.is_relative_to(root) or src.suffix not in (".v", ".sv"):
        raise ValueError("response binding source escapes staged input RTL")
    try:
        scan = _mask_comments(src.read_text())
    except OSError as exc:
        raise ValueError(f"response binding source unreadable: {rel}") from exc
    blocks = _instances(scan, dut)
    if len(blocks) != 1:
        raise ValueError(f"{rel} must contain one instance of {dut}")
    block = blocks[0]
    req_wire = _port_expression(block, req)
    resp = _port_expression(block, inp)
    if not re.fullmatch(_IDENT, req_wire):
        raise ValueError("source request binding is not a named wire")
    pattern = (r"\{\s*" + re.escape(req_wire) +
               rf"\s*,\s*(1'[bB]1)\s*,\s*({_LITERAL})\s*\}}")
    match = re.fullmatch(pattern, resp)
    if not match:
        raise ValueError("source response is not request-ack plus constant data")
    if modules is not None:
        ports = getattr(modules.get(dut), "ports", None)
        if not isinstance(ports, dict):
            raise ValueError("source DUT declaration is absent")
        if (getattr(ports.get(inp), "direction", None) != "input" or
                getattr(ports.get(req), "direction", None) != "output"):
            raise ValueError("source response/request port directions disagree")
    return {
        "dut_module": dut,
        "input_port": inp,
        "request_port": req,
        "source": rel,
        "source_expression": resp,
        "ack_literal": match.group(1),
        "data_literal": match.group(2),
        "emitted_expression": "{" + req + ", " + match.group(1) +
                              ", " + match.group(2) + "}",
        "entropy": "constant_non_random_test_only",
    }


def _requested_stems(extracted: Dict[str, str]) -> set[str]:
    stems: set[str] = set()
    for body in extracted.values():
        if not isinstance(body, str):
            continue
        for match in _TIE_OFF.finditer(body):
            lo, hi = _polarity.sentence_scope(
                body, match.start(), match.end(), extra_breaks=("；", ";", "\n"))
            clause = body[lo:hi]
            if _polarity.is_denied(clause):
                continue
            stems.update(t.lower() for t in re.findall(_IDENT, clause))
    return stems


def discover(project: Path, extracted: Dict[str, str]) -> List[Dict[str, str]]:
    """Find a unique source-proven responder requested by the staged prose.

    Ambiguous or absent evidence emits no binding; the D1 expectation remains
    unresolved. A downstream consumer re-verifies the selected source.
    """
    stems = _requested_stems(extracted)
    if not stems:
        return []
    vendor = Path(project) / "input/vendor_rtl"
    modules = _staged.scan_staged_modules(vendor)
    if not modules:
        return []
    found: Dict[tuple, Dict[str, str]] = {}
    for src in sorted(vendor.rglob("*")):
        if not src.is_file() or src.suffix not in (".v", ".sv"):
            continue
        try:
            scan = _mask_comments(src.read_text())
        except OSError:
            continue
        if not _RESPONSE.search(scan):
            continue
        rel = src.relative_to(project).as_posix()
        for dut in sorted(modules):
            for block in _instances(scan, dut):
                for response in _RESPONSE.finditer(block):
                    inp = response.group("input")
                    stem = re.sub(r"_i$", "", inp, flags=re.I).lower()
                    if stem not in stems:
                        continue
                    wire = response.group("wire")
                    request_ports = []
                    for port in getattr(modules[dut], "ports", {}).values():
                        if getattr(port, "direction", None) != "output":
                            continue
                        name = str(getattr(port, "name", ""))
                        try:
                            if _port_expression(block, name) == wire:
                                request_ports.append(name)
                        except ValueError:
                            continue
                    if len(request_ports) != 1:
                        continue
                    spec = {"input_port": inp,
                            "request_port": request_ports[0],
                            "source": rel}
                    try:
                        row = verify_source(project, dut, spec, modules)
                    except ValueError:
                        continue
                    row["directive"] = "tie-off"
                    found[(dut, inp, request_ports[0], rel)] = row
    return list(found.values()) if len(found) == 1 else []
