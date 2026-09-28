"""Bind a Phase-3 record's file citations to the bytes it actually read.

The runner publishes the map; the completion auditor checks it. A later
recheck gets a tree-named receipt when the run already cites its audit.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

AUDIT_REL = "reports/audit/phase23_completion_audit.json"
REPORT_REL = "reports/orchestrator/phase3_one_shot.json"
_SHA = re.compile(r"^[0-9a-f]{64}$")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _in_tree_file(project: Path, value: str) -> tuple[str, Path] | None:
    token = value.split("#", 1)[0].strip()
    if not token or "\n" in token or len(token) > 4096:
        return None
    try:
        path = Path(token)
        if not path.is_absolute():
            path = project / path
        path = path.resolve()
        root = project.resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return None
    except (OSError, ValueError):
        return None
    return str(path.relative_to(root)), path


def bind(project: Path, record: Any, *, extra_paths: tuple[str, ...] = ()) -> dict[str, str]:
    """Hash existing in-tree files named in the runner's step record."""
    found: dict[str, str] = {}

    def add(value: str) -> None:
        hit = _in_tree_file(project, value)
        if hit:
            rel, path = hit
            if rel != REPORT_REL:
                found[rel] = digest(path)

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)
        elif isinstance(value, str):
            add(value)
            for match in re.finditer(r"(?<![A-Za-z0-9:/])/(?!/)[^\s,;\"'()\[\]{}]+", value):
                add(match.group().rstrip("."))

    walk(record)
    for name in extra_paths:
        add(name)
    return dict(sorted(found.items()))


def check(project: Path, record: dict) -> tuple[str, list[dict]]:
    """Return PASS or NOT_MEASURED and a named check for every citation."""
    citations = record.get("cited_artefacts")
    if not isinstance(citations, dict):
        return "NOT_APPLICABLE", []
    rows = []
    root = project.resolve()
    for rel, expected in sorted(citations.items()):
        path = (root / rel).resolve()
        valid = (isinstance(rel, str) and not Path(rel).is_absolute()
                 and path.is_relative_to(root) and isinstance(expected, str)
                 and bool(_SHA.fullmatch(expected)))
        actual = digest(path) if valid and path.is_file() else None
        rows.append({"path": rel, "expected_sha256": expected,
                     "actual_sha256": actual,
                     "status": ("PASS" if valid and actual == expected
                                else "NOT_MEASURED"),
                     "reason": (None if valid and actual == expected
                                else "STALE_CITATION")})
    return ("PASS" if rows and all(row["status"] == "PASS" for row in rows)
            else "NOT_MEASURED"), rows


def recheck_path(project: Path, record: dict, scan_hashes: dict[str, str]) -> Path | None:
    """A cited canonical audit is immutable; address the recheck by tree SHA."""
    citations = record.get("cited_artefacts")
    if not isinstance(citations, dict) or AUDIT_REL not in citations:
        return None
    h = hashlib.sha256()
    for rel, sha in sorted(scan_hashes.items()):
        h.update(rel.encode("utf-8", "surrogateescape") + b"\0"
                 + sha.encode("ascii") + b"\n")
    for rel in sorted(citations):
        hit = _in_tree_file(project, rel)
        actual = digest(hit[1]) if hit else "MISSING"
        h.update(rel.encode("utf-8", "surrogateescape") + b"\0"
                 + actual.encode("ascii") + b"\n")
    return project / "reports/audit" / f"phase23_completion_audit.{h.hexdigest()}.json"
