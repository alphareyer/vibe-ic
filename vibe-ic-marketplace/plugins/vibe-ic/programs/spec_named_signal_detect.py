#!/usr/bin/env python3
r"""spec_named_signal_detect.py — PROGRAM-FIRST named-signal-preservation advisory.

GENERAL CORE (benchmark-AGNOSTIC). Backticks mark identifiers, not their roles.
Signal-preservation advice needs signal-role evidence from the input sentence,
section or table. Keep explicitly named ports AND internal nets/registers;
record libraries, parameters, metadata and commands in their stated roles.
Unclassified identifiers remain for expert review. This is ADVISORY, never a
gate or a complete semantic analysis. Reads ONLY the prompt (§4.05).

Usage:
    from spec_named_signal_detect import detect_named_signals
    r = detect_named_signals(prompt)     # -> dict

    python3 spec_named_signal_detect.py --prompt @file.md   # CLI, JSON out
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

# Formatting locates candidates; the source context supplies the role.
_TICK_ID_RE = re.compile(r"`([a-zA-Z][A-Za-z0-9_]*)`")
# Existing keyword/clock/reset exclusions are retained.
_NOT_A_SIGNAL = frozenset({
    "module", "endmodule", "input", "output", "inout", "wire", "reg", "logic",
    "parameter", "localparam", "always", "assign", "posedge", "negedge",
    "verilog", "systemverilog", "clk", "clock", "rst", "reset",
})

# Vocabulary of source roles, not a blacklist of design identifiers.
_ROLE_RE = re.compile(
    r"\b(?:(?P<library>librar(?:y|ies))|"
    r"(?P<parameter>parameters?|localparam)|"
    r"(?P<metadata>metadata|schema|fields?|keys?|(?:cell\s+)?area\s+gate)|"
    r"(?P<command>commands?|SDC|Tcl)|"
    r"(?P<setting>settings?|floorplan|constraints?)|"
    r"(?P<module>modules?|instances?)|"
    r"(?P<signal>signals?|nets?|wires?|reg|logic|registers?|ports?|"
    r"inputs?|outputs?|inout|flags?|counters?|strobes?|pulses?))\b", re.I)
_SIGNAL_ACTION_RE = re.compile(
    r"\b(?:drives?|compute[sd]?|holds?|gates?|clears?|tracks?)\b", re.I)
# An absent register interface supplies no signal authority for its subject.
_DENIED_ROLE_RE = re.compile(
    r"\b(?:not|never|no)\s+(?:an?\s+)?(?:(?:RTL|SW[- ]visible)\s+)?$", re.I)


def _context_role(text: str, start: Optional[int] = None,
                  end: Optional[int] = None) -> Optional[str]:
    """Read a local role cue; equally near conflicting cues stay ambiguous."""
    cues = [(m, "non_signal" if m.lastgroup == "signal" and
             _DENIED_ROLE_RE.search(text[:m.start()]) else m.lastgroup)
            for m in _ROLE_RE.finditer(text)]
    if not cues:
        return None
    if start is None:
        roles = {role for _, role in cues}
        return next(iter(roles)) if len(roles) == 1 else None
    distances = []
    for m, role in cues:
        distances.append((max(start - m.end(), m.start() - end, 0), role))
    nearest = min(d for d, _ in distances)
    roles = {role for d, role in distances if d == nearest and d <= 96}
    return next(iter(roles)) if len(roles) == 1 else "ambiguous" if roles else None


def detect_named_signals(prompt: str) -> Dict[str, Any]:
    """Return source-supported signal names and advisory evidence. The existing
    three-name advice threshold stays; uncertain roles never mandate nets."""
    seen: List[str] = []
    evidence = []
    non_signals = []
    ambiguous = []
    section = ""
    section_role = None
    table_header = ""
    table_role = None
    for line_no, line in enumerate((prompt or "").splitlines(), 1):
        stripped = line.strip()
        matches = list(_TICK_ID_RE.finditer(line))
        # Hide identifier spellings from the role vocabulary. HDL keywords
        # remain visible as declaration cues (e.g. `input` `data_valid`).
        masked = _TICK_ID_RE.sub(
            lambda m: (" " + m.group(1) + " " if
                       m.group(1).lower() in _NOT_A_SIGNAL else
                       " " * len(m.group(0))), line)
        if stripped.startswith("#"):
            section = stripped
            section_role = _context_role(masked)
            table_header, table_role = "", None
        if not stripped.startswith("|"):
            table_header, table_role = "", None
        elif not table_header:
            table_header = stripped
            table_role = _context_role(masked)
        for m in matches:
            nm = m.group(1)
            if nm.lower() in _NOT_A_SIGNAL:
                continue
            # A sentence is a smaller authority than the whole mixed document.
            left = max((i + 1 for i, ch in enumerate(masked[:m.start()])
                        if ch in ".;!?"), default=0)
            right = next((i for i in range(m.end(), len(masked))
                          if masked[i] in ".;!?"), len(masked))
            clause = masked[left:right]
            local = _context_role(clause, m.start() - left, m.end() - left)
            role = table_role or local or section_role
            if role is None and _SIGNAL_ACTION_RE.search(clause):
                role = "signal"
            row = {"name": nm, "line": line_no, "context": stripped,
                   "section": section, "table_header": table_header}
            if role == "signal":
                evidence.append(row)
                if nm not in seen:
                    seen.append(nm)
            elif role and role != "ambiguous":
                non_signals.append({**row, "role": role})
            else:
                ambiguous.append(row)

    has = len(seen) >= 3
    requirement = None
    if has:
        sample = ", ".join(seen[:12]) + ("…" if len(seen) > 12 else "")
        requirement = (
            "PRESERVE SPEC-NAMED SIGNALS VERBATIM: the input gives signal-role "
            "evidence for (" + sample + "). Declare EACH one as a real named net/reg "
            "with EXACTLY that identifier — do NOT rename (no _w/_reg/_sig suffix), "
            "fold it into an expression, or optimize it away. This applies to "
            "INTERNAL/intermediate signals (index, counter, state, flag), not only "
            "ports: a white-box testbench may force/peek any of them by the exact "
            "spec name through the hierarchy, and a missing/renamed one crashes "
            "a test with a 'no child object named <name>' error before any "
            "functional check. This is advisory: review ambiguous identifiers "
            "against the input and keep non-signals in their stated roles.")

    return {
        "has_named_signals": has,
        "named_signals": seen,
        "requirement": requirement,
        "signal_evidence": evidence,
        "non_signal_identifiers": non_signals,
        "ambiguous_identifiers": ambiguous,
    }


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--prompt", required=True, help="prompt text or @file")
    a = ap.parse_args(argv)
    prompt = a.prompt
    if prompt.startswith("@"):
        prompt = Path(prompt[1:]).read_text()
    print(json.dumps(detect_named_signals(prompt), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
