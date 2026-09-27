#!/usr/bin/env python3
"""_external_flow_manifest.py — the import manifest for an external flow run (W0).

WHAT THIS IS
============
Under ``--librelane`` the files a step publishes under its canonical vibe-ic
path were produced by a LibreLane step, not by a vibe-ic producer. The
importer (W6; later W12 for ORFS) copies each one into place, and this
manifest is its account of that copy: ONE ROW PER IMPORTED FILE, saying

  * which flow step it serves   (``step_id``, the flow YAML id),
  * where it now lives          (``canonical_path``, project-relative),
  * where the tool wrote it     (``tool_run_path``),
  * that the two are the same bytes (``canonical_sha256`` == ``tool_run_sha256``),
  * which tool made it          (``tool``: the UNDERLYING tool, e.g. openroad),
  * which step of the flow ran  (``tool_step_id`` for LibreLane, e.g.
                                 ``OpenROAD.DetailedRouting``; ``make_stage``
                                 for ORFS),
  * what witnessed the run      (``source_log`` + ``source_log_sha256``),
  * and whether it measured     (``measurement``, the mcp-eda record).

WHY THE ROW BECOMES A PROVENANCE ENTRY (decision 4a)
====================================================
The owner ruled that a row imported from the tool's own step log counts as a
WITNESSED run attributed to the flow, citing its source log path and sha256.
So `to_provenance_entry` renders a row as the record `provenance_check` reads:
``tool`` is the underlying tool (so the flow YAML's existing per-step
allow-lists apply unchanged), ``exit_code`` is the tool step's own status, the
single output is the canonical path at the canonical digest, and
``measurement`` is the tool's own statement. It is NOT ``reconstructed``: rule
#365's back-fill flag is for a record the runner wrote without observing the
run, and this record is backed by the run's own log, cited by digest in
``witness``. ``attributed_to`` names the flow that drove the tool.

WHAT A VALID ROW PROVES, AND THE CHECKS THAT MAKE IT SO
=======================================================
A row belongs to ONE tool run: the manifest's ``run_dir`` (absolute, a real
directory). ``tool_run_path`` and ``source_log`` are stored RELATIVE to it and
always resolved against it, never against the current directory.
`validate_row` refuses, each with a reason:
  * a witness that is not a tool run: a run_dir that is the project (or holds
    it), a canonical file inside run_dir, a tool-run file that IS the
    canonical file, a source log that is the tool-run file, or either path
    resolving outside run_dir (``..``, absolute, or through a symlink);
  * a canonical file that is a symlink, or reached through one (a copy is the
    rule: a symlink into a tool run dir is a pointer, not a published
    artefact, and it changes under the published name when the run is redone);
  * a canonical path that is absolute or escapes the project;
  * ``canonical_sha256 != tool_run_sha256`` (not the same bytes);
  * with ``verify_disk``: a missing run_dir, and a file whose bytes no longer
    hash to its row, on either side, or a source log that no longer hashes to
    its citation;
  * a LibreLane row without ``tool_step_id`` or an ORFS row without
    ``make_stage`` (or a row carrying both);
  * a ``measurement`` that is present but is not an mcp-eda record stating
    ``measured``, or is another tool's record. ``measurement: null`` is
    allowed: it is the honest UNDECLARED state, the entry then carries no
    record, and ``provenance_check --require-measured`` reports UNMEASURED.

`to_provenance_entry` validates first, so a refused row is never rendered as
a witnessed run. A row without ``tool_version`` renders ``version: None`` with
``version_capture: "NOT CAPTURED: ..."`` (#312/#365), never ``""``.

chip-AGNOSTIC / PDK-AGNOSTIC: paths and digests only; no design is read.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

_PROGRAMS_DIR = str(Path(__file__).resolve().parent)
if _PROGRAMS_DIR not in sys.path:
    sys.path.insert(0, _PROGRAMS_DIR)

import _atomic_artefact  # noqa: E402
import _mcp_measurement  # noqa: E402

SCHEMA = "vibe-ic/external-flow-import/1"
#: Under reports/phase3/, which the stage-3 on_pass_review artefact list reads.
MANIFEST_REL = "reports/phase3/impl/import_manifest.json"

FLOW_LIBRELANE = "librelane"
FLOW_ORFS = "orfs"
FLOWS = (FLOW_LIBRELANE, FLOW_ORFS)
#: The field that names the flow's own step, per flow.
STEP_KEY_FOR = {FLOW_LIBRELANE: "tool_step_id", FLOW_ORFS: "make_stage"}

ROW_KEYS = ("step_id", "canonical_path", "tool_run_path", "canonical_sha256",
            "tool_run_sha256", "flow", "tool", "source_log",
            "source_log_sha256", "timestamp", "exit_code", "measurement")

_SHA_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class ManifestError(ValueError):
    """A manifest (or row) that cannot be written or read as valid."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _through_symlink(project: Path, rel: str) -> Optional[str]:
    """The first component of ``project/rel`` that is a symlink, or None."""
    cur = Path(project)
    for part in PurePosixPath(rel).parts:
        cur = cur / part
        if cur.is_symlink():
            return str(cur)
    return None


def _real(p: Path) -> Path:
    return Path(os.path.realpath(p))


def _under(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _run_relative(run_dir: Path, path: Path, key: str) -> str:
    """``path`` as a posix path relative to ``run_dir``; refuses outside it."""
    p = Path(path)
    if not p.is_absolute():
        p = Path(run_dir) / p
    real, root = _real(p), _real(run_dir)
    if not _under(real, root):
        raise ManifestError(f"{key} {path} is not inside run_dir {run_dir}")
    return real.relative_to(root).as_posix()


def make_row(project: Path, *, run_dir: Path, step_id: str,
             canonical_path: str, tool_run_path: Path, flow: str, tool: str,
             source_log: Path, timestamp: str, exit_code: int,
             measurement: Optional[Dict[str, Any]],
             tool_step_id: Optional[str] = None,
             make_stage: Optional[str] = None,
             tool_version: Optional[str] = None) -> Dict[str, Any]:
    """Build one row from the files ON DISK: every digest is computed here.

    ``tool_run_path`` and ``source_log`` are absolute, or relative to
    ``run_dir``; they are STORED relative to ``run_dir`` and always resolved
    against it, never against the current directory. ``measurement`` is the
    tool's own record, or None when the tool's log states nothing (the honest
    UNDECLARED state: provenance_check then reports UNMEASURED).
    """
    project, run_dir = Path(project), Path(run_dir)
    if not run_dir.is_absolute():
        raise ManifestError(f"run_dir {run_dir} is not an absolute path")
    tr = _run_relative(run_dir, tool_run_path, "tool_run_path")
    sl = _run_relative(run_dir, source_log, "source_log")
    row: Dict[str, Any] = {
        "step_id": str(step_id),
        "canonical_path": str(PurePosixPath(canonical_path)),
        "tool_run_path": tr,
        "canonical_sha256": sha256_file(project / canonical_path),
        "tool_run_sha256": sha256_file(run_dir / tr),
        "flow": flow,
        "tool": tool,
        "source_log": sl,
        "source_log_sha256": sha256_file(run_dir / sl),
        "timestamp": timestamp,
        "exit_code": exit_code,
        "measurement": measurement,
    }
    if tool_step_id is not None:
        row["tool_step_id"] = tool_step_id
    if make_stage is not None:
        row["make_stage"] = make_stage
    if tool_version is not None:
        row["tool_version"] = tool_version
    return row


def _bad_rel(value: str) -> bool:
    rel = PurePosixPath(value)
    return rel.is_absolute() or ".." in rel.parts


def validate_row(row: Any, project: Path, run_dir: Any, *,
                 verify_disk: bool = True) -> List[str]:
    """Problems with one row of the run in ``run_dir``; empty when valid."""
    if not isinstance(row, dict):
        return ["row is not an object"]
    problems = [f"missing '{k}'" for k in ROW_KEYS if k not in row]
    if problems:
        return problems
    for k in ("step_id", "canonical_path", "tool_run_path", "tool",
              "source_log", "timestamp"):
        if not (isinstance(row[k], str) and row[k].strip()):
            problems.append(f"'{k}' is not a non-empty string")
    for k in ("canonical_sha256", "tool_run_sha256", "source_log_sha256"):
        if not (isinstance(row[k], str) and _SHA_RE.match(row[k])):
            problems.append(f"'{k}' is not a sha256:<64 hex> digest")
    if not isinstance(row["exit_code"], int) or isinstance(row["exit_code"], bool):
        problems.append("'exit_code' is not an integer")
    if "tool_version" in row and not (isinstance(row["tool_version"], str)
                                      and row["tool_version"].strip()):
        problems.append("'tool_version', when present, is a non-empty string")
    flow = row["flow"]
    if flow not in FLOWS:
        problems.append(f"flow {flow!r} is not one of {FLOWS}")
    else:
        want = STEP_KEY_FOR[flow]
        other = [k for k in STEP_KEY_FOR.values() if k != want]
        if not (isinstance(row.get(want), str) and row[want].strip()):
            problems.append(f"a {flow} row names its step in '{want}'")
        for k in other:
            if k in row:
                problems.append(f"a {flow} row does not carry '{k}'")
    if row["measurement"] is not None:
        # null is the honest UNDECLARED state; a record that is present must
        # be a real one, and must be the record of THIS row's tool.
        meas = _mcp_measurement.from_provenance_entry(
            {"measurement": row["measurement"]})
        if meas.undeclared:
            problems.append("'measurement' is neither null nor an mcp-eda "
                            "measurement record stating measured true/false")
        elif meas.tool and meas.tool != row["tool"]:
            problems.append(f"'measurement' is {meas.tool!r}'s record, not "
                            f"{row['tool']!r}'s")
    if not (isinstance(run_dir, (str, Path)) and str(run_dir)
            and Path(run_dir).is_absolute()):
        problems.append(f"run_dir {run_dir!r} is not an absolute path")
    if problems:
        return problems

    for k in ("canonical_path", "tool_run_path", "source_log"):
        if _bad_rel(row[k]):
            where = "the project" if k == "canonical_path" else "run_dir"
            problems.append(f"{k} {row[k]!r} is not a relative path inside "
                            f"{where}")
    if problems:
        return problems
    project, run_dir = Path(project), Path(run_dir)
    canon = project / row["canonical_path"]
    tool_file = run_dir / row["tool_run_path"]
    log = run_dir / row["source_log"]
    if row["canonical_sha256"] != row["tool_run_sha256"]:
        problems.append("canonical_sha256 != tool_run_sha256: the published "
                        "file is not the bytes the tool wrote")
    if row["source_log"] == row["tool_run_path"]:
        problems.append("source_log is the tool-run file itself: a file "
                        "cannot witness its own run")
    link = _through_symlink(project, row["canonical_path"])
    if link:
        problems.append(f"{link} is a symlink: an imported file is copied, "
                        "never linked")
    # THE WITNESS MUST BE A TOOL RUN, NOT THE PROJECT: the run directory is
    # neither the project nor any directory holding the published file.
    root, proj = _real(run_dir), _real(project)
    if _under(proj, root):
        problems.append(f"run_dir {run_dir} is the project or contains it: "
                        "the witness must be a tool run, not the project")
    elif _under(_real(canon), root):
        problems.append(f"the canonical file lies inside run_dir {run_dir}: "
                        "the witness would be the published file itself")
    for key, path in (("tool_run_path", tool_file), ("source_log", log)):
        if not _under(_real(path), root):
            problems.append(f"{key} {row[key]!r} resolves outside run_dir "
                            f"{run_dir}")
    if _real(tool_file) == _real(canon):
        problems.append("tool_run_path is the canonical file itself")
    if verify_disk:
        if not run_dir.is_dir():
            problems.append(f"run_dir {run_dir} is not a directory")
        for key_path, key_sha, path in (
                ("canonical_path", "canonical_sha256", canon),
                ("tool_run_path", "tool_run_sha256", tool_file),
                ("source_log", "source_log_sha256", log)):
            if link and key_path == "canonical_path":
                continue
            if not path.is_file():
                problems.append(f"{key_path} {path} is not a file on disk")
            elif sha256_file(path) != row[key_sha]:
                problems.append(f"{key_path} {path} no longer hashes to "
                                f"{key_sha}")
    return problems


def to_provenance_entry(row: Dict[str, Any], project: Path,
                        run_dir: Path) -> Dict[str, Any]:
    """The provenance.jsonl record that witnesses one imported file.

    Validated against the disk first: a row `validate_row` refuses is never
    rendered as a witnessed run.
    """
    problems = validate_row(row, project, run_dir, verify_disk=True)
    if problems:
        raise ManifestError("; ".join(problems))
    step_key = STEP_KEY_FOR[row["flow"]]
    run_dir = Path(run_dir)
    entry: Dict[str, Any] = {
        "timestamp": row["timestamp"],
        "tool": row["tool"],
        "attributed_to": row["flow"],
        "argv": [],
        "inputs": {},
        "outputs": {row["canonical_path"]: row["canonical_sha256"]},
        "exit_code": row["exit_code"],
        "duration_s": None,
        "reconstructed": False,
        "step_id": row["step_id"],
        "witness": {
            "kind": "tool_step_log",
            step_key: row[step_key],
            "run_dir": str(run_dir),
            "log": str(run_dir / row["source_log"]),
            "log_sha256": row["source_log_sha256"],
        },
        "imported_from": {"path": str(run_dir / row["tool_run_path"]),
                          "sha256": row["tool_run_sha256"]},
    }
    if row.get("tool_version"):
        entry["version"] = row["tool_version"]
    else:
        # #312/#365: an unknown build is None WITH its reason, never "".
        entry["version"] = None
        entry["version_capture"] = ("NOT CAPTURED: the import row carries no "
                                    "tool version for this step")
    if row["measurement"] is not None:
        entry["measurement"] = row["measurement"]
    return entry


def manifest_path(project: Path) -> Path:
    return Path(project) / MANIFEST_REL


def validate_manifest(manifest: Any, project: Path, *,
                      verify_disk: bool = True) -> List[str]:
    if not isinstance(manifest, dict):
        return ["manifest is not an object"]
    problems: List[str] = []
    if manifest.get("schema") != SCHEMA:
        problems.append(f"schema is {manifest.get('schema')!r}, expected "
                        f"{SCHEMA!r}")
    flow = manifest.get("flow")
    if flow not in FLOWS:
        problems.append(f"flow {flow!r} is not one of {FLOWS}")
    run_dir = manifest.get("run_dir")
    if not (isinstance(run_dir, str) and run_dir and Path(run_dir).is_absolute()):
        return problems + [f"run_dir {run_dir!r} is not an absolute path"]
    rows = manifest.get("rows")
    if not isinstance(rows, list):
        return problems + ["rows is not a list"]
    seen: Dict[str, int] = {}
    for i, row in enumerate(rows):
        for p in validate_row(row, project, run_dir, verify_disk=verify_disk):
            problems.append(f"row {i}: {p}")
        if isinstance(row, dict):
            if flow in FLOWS and row.get("flow") != flow:
                problems.append(f"row {i}: flow {row.get('flow')!r} in a "
                                f"{flow} manifest")
            cp = row.get("canonical_path")
            if cp in seen:
                problems.append(f"row {i}: canonical_path {cp!r} is already "
                                f"imported by row {seen[cp]}")
            elif isinstance(cp, str):
                seen[cp] = i
    return problems


def write_manifest(project: Path, *, flow: str, run_dir: str,
                   rows: List[Dict[str, Any]]) -> Path:
    """Validate against the disk, then write atomically. Invalid → nothing."""
    manifest = {"schema": SCHEMA, "flow": flow, "run_dir": str(run_dir),
                "rows": rows}
    problems = validate_manifest(manifest, project, verify_disk=True)
    if problems:
        raise ManifestError("; ".join(problems))
    path = manifest_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_artefact.write_json(path, manifest, indent=2)
    return path


def load_manifest(project: Path, *, verify_disk: bool = True) -> Dict[str, Any]:
    path = manifest_path(project)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"{path}: {exc}") from exc
    problems = validate_manifest(manifest, project, verify_disk=verify_disk)
    if problems:
        raise ManifestError(f"{path}: " + "; ".join(problems))
    return manifest
