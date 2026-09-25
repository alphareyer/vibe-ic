"""Keep unadmitted stream files out of Phase-3 consumer locations."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import _atomic_artefact as _aa
import _path_layout as _pl


def gate_record(project: Path) -> dict:
    try:
        record = json.loads((_pl.reports_phase3_dir(project) /
                             "prestream_gate.json").read_text())
        return record if isinstance(record, dict) else {}
    except (OSError, ValueError):
        return {}


def gate_passed(project: Path, digest: str | None = None) -> bool:
    record = gate_record(project)
    return (record.get("verdict") == "PASS" and
            bool(record.get("layout_digest")) and
            (digest is None or record["layout_digest"] == digest))


def visible_gds(project: Path) -> list[Path]:
    """List every location a Phase-3 or handoff reader treats as a mask."""
    project = Path(project)
    roots = (_pl.pnr_dir(project), _pl.gds_dir(project),
             _pl.foundry_handoff_dir(project),
             _pl.foundry_handoff_dir(project) / "gds", project / "gds")
    return [p for root in roots if root.is_dir()
            for p in sorted(root.glob("*.gds"))
            if p.is_file() or p.is_symlink()]


def quarantine_visible_gds(project: Path, reason: str) -> list[str]:
    """Move all consumer-visible GDS aside, including previous run's masks."""
    project = Path(project)
    files = visible_gds(project)
    if not files:
        return []
    stamp = f"{time.time_ns()}-{os.getpid()}"
    aside = project / "phase3" / "scratch" / "gds_quarantine" / stamp
    aside.mkdir(parents=True, exist_ok=False)
    moved = []
    # Hash before the first rename: a canonical alias may point at an earlier
    # source, and moving that source first would make the alias dangling.
    hashes = {}
    for source in files:
        try:
            digest = hashlib.sha256()
            with source.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            hashes[source] = digest.hexdigest()
        except OSError:
            hashes[source] = None
    for source in files:
        rel = source.relative_to(project)
        target = aside / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        source.replace(target)
        moved.append({"source": str(rel), "quarantined": str(target.relative_to(project)),
                      "sha256": hashes[source]})
    _aa.write_text(aside / "record.json", json.dumps({
        "reason": reason, "gate": gate_record(project), "files": moved,
    }, indent=2) + "\n")
    return [row["source"] for row in moved]


def quarantine_handoff_package(project: Path, reason: str) -> list[str]:
    """Retain a previous kit for diagnosis without leaving it deliverable."""
    project = Path(project)
    handoff = _pl.foundry_handoff_dir(project)
    if not handoff.is_dir():
        return []
    members = sorted(handoff.iterdir())
    if not members:
        return []
    stamp = f"{time.time_ns()}-{os.getpid()}"
    aside = project / "phase3" / "scratch" / "handoff_quarantine" / stamp
    aside.mkdir(parents=True, exist_ok=False)
    moved = []
    for source in members:
        target = aside / source.name
        rel = source.relative_to(project)
        source.replace(target)
        moved.append({"source": str(rel), "quarantined": str(target.relative_to(project))})
    _aa.write_text(aside / "record.json", json.dumps({
        "reason": reason, "gate": gate_record(project), "files": moved,
    }, indent=2) + "\n")
    return [row["source"] for row in moved]
