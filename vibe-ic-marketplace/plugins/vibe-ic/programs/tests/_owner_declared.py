#!/usr/bin/env python3
"""_owner_declared — the owner attestation a fixture needs, in ONE place.

R-0915-95 (2026-09-17) made `deliverable` an OWNER-ONLY question: an answer
nobody entitled to give it gave is not a declaration, and
`_tapeout_declaration.answer` reports it `NOT_DETERMINED`. So a fixture that
means "this design's delivery route WAS declared" has to say WHO declared it,
exactly as a real design's `input/step_0_5ic_answers.json` now does.

Written here instead of inlined at ~30 construction sites so the attestation is
one concept a reviewer reads once, and so two fixtures cannot drift into
claiming different provenances for the same modelled fact.

`attest` IS CONDITIONAL ON THE QUESTION BEING ANSWERED, and that is the whole
care in this file. A fixture that leaves `deliverable` unanswered is modelling
an UNDECLARED design -- several of them are the negative controls that keep the
guards honest -- and attaching an attestation to those would quietly convert
every one of them into a declared design. That is the exact mistake this helper
exists not to make, so it never adds a provenance for a question nobody
answered, and it never overwrites one a fixture wrote on purpose.

chip-AGNOSTIC: no vendor, foundry, process node, SKU or design name.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import _tapeout_declaration as _TD                             # noqa: E402

#: What a fixture is asserting when it uses this: that the party taking
#: delivery answered, and said so in a place a reviewer could check.
CITATION = ("test fixture: the owner declared this delivery's route "
            "(models an owner ruling cited in the design's own answers file)")


def provenance(key: str = "deliverable") -> Dict[str, Any]:
    """The `answer_provenance` map for one owner-answered question."""
    return {key: {"answered_by": _TD.ANSWERED_BY_OWNER_VALUE,
                  "citation": CITATION}}


def attest(doc: Dict[str, Any], key: str = "deliverable") -> Dict[str, Any]:
    """Attest `key` in `doc` to the owner IF the fixture answered it.

    Returns `doc` so it can wrap a construction expression. Mutates in place,
    which is what every call site here wants.
    """
    if not isinstance(doc, dict):
        return doc
    answers = doc.get("answers")
    value = (answers or {}).get(key) if isinstance(answers, dict) \
        else doc.get(key)
    if not _TD.is_answered(value):
        return doc
    existing = doc.get(_TD.PROVENANCE_KEY)
    merged = dict(existing) if isinstance(existing, dict) else {}
    merged.setdefault(key, provenance(key)[key])
    doc[_TD.PROVENANCE_KEY] = merged
    return doc
