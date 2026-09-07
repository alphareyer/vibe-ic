#!/usr/bin/env python3
"""A vector drawing is an ASSET; the .md beside it is the document.

`input_doc_texts` is the shared reader every Phase-1 layer's prose ingest
goes through, and its callers treat what it returns as the design's own
requirement text. An `.svg` is the one textual member of the input corpus
whose text is not prose — coordinates, transforms, path data, font metadata
— so reading it is how a token match becomes a requirement nobody wrote.

BOTH DIRECTIONS, because a skip rule with only one is a rule that can delete
the corpus and still pass: the drawing is skipped, AND the document beside
it is still read. Remove `.svg` from `_SKIP_SUFFIXES` and the first test
goes red; widen the rule to skip its neighbours and the second does.
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROGRAMS = _HERE.parent
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import l_doc_consumer_contract as C        # noqa: E402

_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
    '<path d="M0 0 L10 10"/>'
    '<text x="1" y="2">CORE_UTIL_TARGET = 45</text></svg>\n'
)
_MD = "# Floorplan and Placement\n\nCORE_UTIL_TARGET = 45\n"


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    docs = root / "input" / "docs"
    docs.mkdir(parents=True)
    (docs / "block_diagram.svg").write_text(_SVG, encoding="utf-8")
    (docs / "floorplan.md").write_text(_MD, encoding="utf-8")
    return root


def test_the_drawing_is_not_read_as_requirement_text(tmp_path: Path) -> None:
    read = [p.name for p, _ in C.input_doc_texts(_project(tmp_path))]
    assert "block_diagram.svg" not in read, read


def test_the_document_beside_it_is_still_read(tmp_path: Path) -> None:
    got = {p.name: t for p, t in C.input_doc_texts(_project(tmp_path))}
    assert "floorplan.md" in got, sorted(got)
    assert got["floorplan.md"] == _MD


def test_the_skip_set_still_holds_every_binary_it_held_before() -> None:
    """MEMBERSHIP, so a widening cannot hide as an addition."""
    assert {".gds", ".lef", ".lib", ".db", ".png", ".pdf", ".gz", ".zip",
            ".vcd", ".fst", ".bin", ".hex", ".svg"} == C._SKIP_SUFFIXES
