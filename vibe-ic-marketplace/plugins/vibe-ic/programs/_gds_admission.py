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


def admission_path(project: Path) -> Path:
    return _pl.reports_phase3_dir(project) / "gds_admission.json"


def admitted_gds(project: Path, gds: Path, digest: str | None = None) -> bool:
    """A stream is reusable only with a matching gate, basis and byte receipt."""
    try:
        record = json.loads(admission_path(project).read_text())
        if not isinstance(record, dict):
            return False
        bound = record.get("layout_digest")
        inputs = record.get("basis_inputs")
        if not bound or not gate_passed(project, bound) or not isinstance(inputs, dict):
            return False
        if digest is not None and bound != digest:
            return False
        if not inputs or any(_sha256(project / rel) != sha
                             for rel, sha in inputs.items()):
            return False
        return (bound == gate_record(project).get("layout_digest")
                and record.get("gds_relpath") == str(gds.relative_to(project))
                and record.get("gds_sha256") == _sha256(gds)
                and (record.get("gds_members") or {}).get(gds.name)
                == record.get("gds_sha256"))
    except (OSError, ValueError, TypeError):
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def admit_gds(project: Path, gds: Path, digest: str, basis_paths: list[Path]) -> None:
    if not digest or not gate_passed(project, digest) or not gds.is_file():
        raise ValueError("GDS admission requires this layout's PASS pre-stream gate")
    _aa.write_text(admission_path(project), json.dumps({
        "layout_digest": digest,
        "gds_relpath": str(gds.relative_to(project)),
        "gds_sha256": _sha256(gds),
        "gds_members": {path.name: _sha256(path)
                        for path in sorted(_pl.pnr_dir(project).glob("*.gds"))
                        if path.is_file()},
        "basis_inputs": {str(path.relative_to(project)): _sha256(path)
                         for path in basis_paths},
    }, indent=2) + "\n")


def admitted_package_source(project: Path) -> bool:
    try:
        record = json.loads(admission_path(project).read_text())
        rel = record["gds_relpath"]
        source = project / rel
        return source.is_file() and admitted_gds(project, source)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def admitted_package_sources(project: Path, sources: dict[str, Path]) -> bool:
    """Every packaged mask must have the admitted stream's recorded bytes."""
    if not admitted_package_source(project):
        return False
    try:
        record = json.loads(admission_path(project).read_text())
        name = Path(record["gds_relpath"]).name
        members = record.get("gds_members")
        return (isinstance(members, dict) and name in sources
                and all(members.get(member) == _sha256(source)
                        for member, source in sources.items()))
    except (OSError, ValueError, KeyError, TypeError):
        return False


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
