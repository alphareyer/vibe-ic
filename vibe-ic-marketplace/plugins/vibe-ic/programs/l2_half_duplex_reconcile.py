#!/usr/bin/env python3
"""The L2's own `duplex` text may VETO a keyword-derived half_duplex=true
(R-0915-164 b).

Phase 1 sets `protocol_overview.half_duplex=True` from a document-level keyword
hit (single-wire evidence plus "half-duplex" anywhere in the same document, no
negation or contrast handling). The protocol synthesizers later write a
`duplex` sentence into the same `protocol_overview`. The two were never
reconciled, and on pcie_gen5 and ufs the flag said half-duplex while the text
said "dual-simplex" / "full-duplex" -- the keyword had hit a sentence about the
interface each design REPLACED.

VETO, NEVER GRANT. When the duplex text states full-duplex, dual-simplex,
simplex or unidirectional and does NOT state half-duplex, half_duplex may not
be True: it becomes False, with `evidence` naming the field and the previous
keyword evidence kept beside it. Ambiguous texts ("half-duplex … or
full-duplex …") and texts that say half-duplex never change anything, and the
text never sets a False/absent flag to True -- the flag means SINGLE-WIRE
half-duplex, which a generic "half-duplex on DQ" does not establish.

chip-AGNOSTIC: reads only the two protocol_overview fields.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import l_doc_generator_stamp as _stamp          # noqa: E402

_HALF_RE = re.compile(r"half[\s\-]?duplex|半[\s]*[雙双][\s]*工", re.IGNORECASE)
_NOT_HALF_RE = re.compile(
    r"full[\s\-]?duplex|dual[\s\-]?simplex|\bsimplex\b|\bunidirectional\b|"
    r"全[\s]*[雙双][\s]*工|單[\s]*向", re.IGNORECASE)


def duplex_text_vetoes(text: Any) -> bool:
    """True iff `text` states a not-half-duplex mode and no half-duplex one."""
    if not isinstance(text, str) or not text.strip():
        return False
    return bool(_NOT_HALF_RE.search(text)) and not _HALF_RE.search(text)


def reconcile(l2: Any) -> Optional[Dict[str, Any]]:
    """Apply the veto to an L2 document in place. Returns the change record,
    or None when nothing changed."""
    po = l2.get("protocol_overview") if isinstance(l2, dict) else None
    if not isinstance(po, dict) or po.get("half_duplex") is not True:
        return None
    duplex = po.get("duplex")
    if not duplex_text_vetoes(duplex):
        return None
    previous = po.get("evidence")
    po["half_duplex"] = False
    po["half_duplex_keyword_evidence"] = previous
    po["evidence"] = (
        f"protocol_overview.duplex states {duplex[:120]!r} (not half-duplex); "
        f"it vetoes the keyword-derived half_duplex=true (R-0915-164 b)")
    return {"from": True, "to": False, "duplex": duplex,
            "keyword_evidence": previous}


def reconcile_file(path: Path) -> bool:
    """Reconcile one L2_FRS.json on disk; rewrite it only when it changed."""
    path = Path(path)
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return False
    if reconcile(doc) is None:
        return False
    _stamp.dump(path, doc)
    return True


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        print(arg, "changed" if reconcile_file(Path(arg)) else "unchanged")
