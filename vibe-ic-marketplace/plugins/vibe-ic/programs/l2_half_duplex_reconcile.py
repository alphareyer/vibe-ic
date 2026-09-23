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

VETO, NEVER GRANT, AND NEVER ON A TEMPLATE ALONE (R-0915-167). When the
duplex text states full-duplex, dual-simplex, simplex or unidirectional and
does NOT state half-duplex, AND a sentence of the design's own input
documents states such a mode, half_duplex may not be True: it becomes False,
with `evidence` naming the document sentence and the field, the previous
keyword evidence kept beside it. The duplex text on its own is synth
boilerplate (the UART synth writes "full duplex" even over a single-wire
half-duplex K-line document), so it never decides by itself. Ambiguous texts ("half-duplex … or
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
from typing import Any, Dict, Mapping, Optional, Tuple

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


def document_statement(documents: Any) -> Optional[Tuple[str, str]]:
    """``(document, sentence)`` of the first sentence in the design's OWN input
    documents that states a not-half-duplex mode, or None.

    R-0915-167: the synth's `duplex` text is a template (115 of the 116 sites
    that write it are fixed literals, chosen by which synth fired), so it is
    never sufficient on its own. The sentence unit is the extractor's own --
    a run of text between `.`, `!` or `?` (gen_l2_frs's `[^.!?]`).
    """
    if not isinstance(documents, Mapping):
        return None
    for name, text in documents.items():
        if not isinstance(text, str):
            continue
        for sentence in re.split(r"[.!?]", text):
            if _NOT_HALF_RE.search(sentence):
                return str(name), " ".join(sentence.split())[:200]
    return None


def reconcile(l2: Any, documents: Any = None) -> Optional[Dict[str, Any]]:
    """Apply the veto to an L2 document in place. Returns the change record,
    or None when nothing changed.

    Vetoes ONLY when BOTH say the design is not half-duplex: the synth's
    `duplex` text AND a sentence of the design's own input documents
    (`documents`: name -> text, what gen_l2_frs reads). A design whose
    documents make no such statement -- or that ships none -- keeps
    half_duplex=true, which step 2 reads as INCOMPLETE, never FAIL.
    """
    po = l2.get("protocol_overview") if isinstance(l2, dict) else None
    if not isinstance(po, dict) or po.get("half_duplex") is not True:
        return None
    duplex = po.get("duplex")
    if not duplex_text_vetoes(duplex):
        return None
    stated = document_statement(documents)
    if stated is None:
        return None
    doc_name, sentence = stated
    previous = po.get("evidence")
    po["half_duplex"] = False
    po["half_duplex_keyword_evidence"] = previous
    po["evidence"] = (
        f"the design's own document {doc_name} states {sentence!r}, and "
        f"protocol_overview.duplex states {duplex[:120]!r} (not half-duplex); "
        f"together they veto the keyword-derived half_duplex=true "
        f"(R-0915-167)")
    return {"from": True, "to": False, "duplex": duplex,
            "document": doc_name, "sentence": sentence,
            "keyword_evidence": previous}


def reconcile_file(path: Path, documents: Any = None) -> bool:
    """Reconcile one L2_FRS.json on disk; rewrite it only when it changed."""
    path = Path(path)
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return False
    if reconcile(doc, documents) is None:
        return False
    _stamp.dump(path, doc)
    return True


if __name__ == "__main__":
    # usage: l2_half_duplex_reconcile.py L2_FRS.json [input documents ...]
    l2_path, docs = Path(sys.argv[1]), sys.argv[2:]
    texts = {d: Path(d).read_text(errors="replace") for d in docs}
    print(l2_path, "changed" if reconcile_file(l2_path, texts) else "unchanged")
