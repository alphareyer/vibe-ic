"""Identify the PDK from the flow's published asset paths.

The design spec is deliberately excluded: it describes a target, while these
paths identify the libraries actually consumed by the signed-off flow.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_PROGRAMS_DIR = str(Path(__file__).resolve().parent)
if _PROGRAMS_DIR not in sys.path:
    sys.path.insert(0, _PROGRAMS_DIR)

import _published_tree


_ROOT = re.compile(r"/foss/pdks/([A-Za-z0-9._-]+)/")
# Ciel and Volare keep each PDK at <store>/<family>/versions/<hash>/<variant>.
# The store directory is where libraries live, never which process they are.
_STORES = frozenset({"ciel", "volare"})
_CIEL = re.compile(
    r"/foss/pdks/(?:ciel|volare)/[A-Za-z0-9._-]+/versions/[A-Za-z0-9._-]+/"
    r"([A-Za-z0-9._-]+)(?![A-Za-z0-9._-])")
_FLOW_DIRS = ("/phase2/", "/phase3/")


def signoff_flow_texts(project: Path):
    """Yield published phase2/phase3 files, or disk files in a live run."""
    tracked = _published_tree.published_paths(project)
    if tracked is None:
        for directory in ("phase2", "phase3"):
            base = project / directory
            if base.is_dir():
                for path in sorted(base.rglob("*")):
                    if path.is_file():
                        yield path
        return
    for rel in sorted(tracked):
        if any(("/" + rel).find(directory) >= 0 for directory in _FLOW_DIRS):
            yield project / rel


def names_in_text(text: str) -> set[str]:
    """Resolve Ciel's versioned storage path to its actual library variant.

    The variant is the final component of the versioned PDK root. Paths to
    the root itself and to its libraries must agree. Unrecognized paths retain
    the ordinary ambiguity behavior.
    """
    normalized = _CIEL.sub(r"/foss/pdks/\1/", text)
    # A path that stops at the store (a PDK_ROOT set to the version directory
    # and expanded later) names no variant: it is no evidence, not a PDK.
    return set(_ROOT.findall(normalized)) - _STORES


def _flow_names(project: Path, stop_when_ambiguous: bool) -> set[str]:
    names: set[str] = set()
    for path in signoff_flow_texts(project):
        try:
            # Live flow trees contain multi-GB binary GDS, simulator caches,
            # and GCH files. Their first page has NUL bytes and cannot be a
            # path-bearing command/report. Avoid decoding their entire body.
            with path.open("rb") as stream:
                head = stream.read(8192)
                if b"\0" in head:
                    continue
                body = head + stream.read()
            names.update(names_in_text(body.decode(errors="replace")))
        except OSError:
            continue
        if stop_when_ambiguous and len(names) > 1:
            break
    return names


def signoff_pdk_evidence(project: Path) -> tuple[str | None, list[str]]:
    """Return (the one PDK or None, every candidate name found).

    None with no candidates is absent evidence; None with several is
    ambiguous. A gate that cannot compare states which one it was.
    """
    names = _flow_names(project, stop_when_ambiguous=False)
    return (next(iter(names)) if len(names) == 1 else None), sorted(names)


def pdk_from_signoff_flow(project: Path) -> str | None:
    """Return one PDK, or None for absent/ambiguous flow evidence."""
    names = _flow_names(project, stop_when_ambiguous=True)
    return next(iter(names)) if len(names) == 1 else None
