#!/usr/bin/env python3
"""_inplace_chain.py — the ONE definition of "these bytes are still the bytes
this run produced, through every in-place rewrite that touched them".

WHY THIS MODULE EXISTS
======================
R-0915-162 let a consumed staging file stop being a missing-output finding when
`sha256(destination) == staged_sha256`. That is one hop, and a deliverable is
not written once. MEASURED on spm run23, to the second:

    reports/phase3/die_finishing.json   19:08:28   seal ring promoted
    reports/phase3/cmp_fill_emit.json   19:09:02
    reports/phase3/die_density_fill.json 19:11:52
    phase3/stage3/pnr/spm.gds           19:11:52.299   <- rewritten IN PLACE

`die_density_fill_gen` rewrote the promoted GDS 3 m 24 s after the promotion,
so `sha256(spm.gds)` at audit time can never equal the digest taken before the
rename. The one-hop check would have failed on every real run. (It appeared to
pass only because the validating fixture computed `staged_sha256` FROM the
already-filled file, which is circular: a digest taken from the answer.)

R-0915-166 — VERIFY THE CHAIN, NOT ONE HOP. Every in-place writer of a
deliverable records what it found and what it left:

    {"path": <project-relative>, "sha_before": <hex>, "sha_after": <hex>}

and a consumer walks `staged_sha256 -> (before -> after)* -> sha256(dest now)`.
A CONTINUOUS chain is evidence. A GAP is not, and a gap is exactly what an
unrecorded writer -- or a hand-edited deliverable -- produces.

chip-AGNOSTIC: no PDK, vendor, IC, project or directory-name literal.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

#: The key an in-place writer publishes its links under, in its OWN report.
#: One spelling, read by the checker and written by every writer.
LINKS_KEY = "in_place_rewrites"


def sha256_file(path: Path) -> Optional[str]:
    """sha256 of `path`, or None when it cannot be read.

    None is a THIRD STATE. "I could not digest this" is not "the digest did not
    match", and a caller that collapses them turns an unreadable file into a
    tampered one.
    """
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def project_rel(path: Path, project: Path) -> str:
    """`path` spelled relative to the project root when it is inside it.

    A record a checker must resolve owes it a project-relative spelling.
    Falls back to the absolute spelling rather than inventing a relative one
    for a path genuinely outside the tree.
    """
    try:
        return str(Path(path).resolve().relative_to(Path(project).resolve()))
    except (ValueError, OSError):
        return str(path)


def link(dest: Path, project: Path, sha_before: Optional[str],
         sha_after: Optional[str]) -> Dict[str, Any]:
    """One link, in the shape every writer publishes.

    `sha_before` is None when the destination did not exist yet -- a CREATE,
    not a rewrite, which is a legitimate start of a chain and is recorded as
    such rather than as an unreadable file.
    """
    return {"path": project_rel(dest, project),
            "sha_before": sha_before, "sha_after": sha_after}


def _same_path(a: str, b: str) -> bool:
    return Path(str(a)).as_posix().strip("/") == Path(str(b)).as_posix().strip("/")


def links_for(path_rel: str, docs: List[Any]) -> List[Dict[str, Any]]:
    """Every recorded link naming `path_rel`, from any depth of any document."""
    out: List[Dict[str, Any]] = []
    for doc in docs:
        for rec in _dicts_in(doc):
            entries = rec.get(LINKS_KEY)
            if not isinstance(entries, list):
                continue
            for e in entries:
                if (isinstance(e, dict) and isinstance(e.get("path"), str)
                        and _same_path(e["path"], path_rel)):
                    out.append(e)
    return out


def _dicts_in(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _dicts_in(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _dicts_in(v)


def chain_reaches(start_sha: str, final_sha: str,
                  links: List[Dict[str, Any]]) -> Tuple[bool, str]:
    """Is there a continuous `start_sha -> ... -> final_sha` walk over `links`?

    ORDER IS NOT TAKEN FROM THE RECORD. Reports are written by different
    programs at different times and nothing orders them reliably, so the walk
    is driven by the DIGESTS: from the current digest, take the link whose
    `sha_before` matches, move to its `sha_after`, repeat. That is the only
    ordering that cannot be forged by writing the links in a convenient
    sequence.

    Returns (ok, reason). `reason` names the gap when there is one, because
    "the chain is broken" and "the chain is broken HERE" are different facts to
    a reader.
    """
    if not isinstance(start_sha, str) or not start_sha:
        return False, "no starting digest was recorded"
    if not isinstance(final_sha, str) or not final_sha:
        return False, "the destination could not be digested now"
    if start_sha == final_sha:
        return True, "the destination still carries the staged bytes unchanged"
    cur = start_sha
    used: List[int] = []
    hops = 0
    while cur != final_sha:
        nxt = None
        for i, e in enumerate(links):
            if i in used:
                continue
            if e.get("sha_before") == cur and isinstance(e.get("sha_after"),
                                                         str):
                nxt = i
                break
        if nxt is None:
            return False, (
                f"the chain stops at {cur[:12]}…: no recorded in-place rewrite "
                f"of this path says it found those bytes, so something changed "
                f"the deliverable without recording it")
        used.append(nxt)
        cur = links[nxt]["sha_after"]
        hops += 1
        if hops > len(links):                                # pragma: no cover
            return False, "the recorded rewrites form a cycle"
    return True, (f"{hops} recorded in-place rewrite(s) carry the staged bytes "
                  f"to the destination's current content")
