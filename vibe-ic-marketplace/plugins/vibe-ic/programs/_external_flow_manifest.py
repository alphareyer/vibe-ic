#!/usr/bin/env python3
"""_external_flow_manifest.py — the import manifest for an external flow run (W0).

WHAT THIS IS
============
Under ``--librelane`` the files a step publishes under its canonical vibe-ic
path were produced by a LibreLane step, not by a vibe-ic producer. The
importer (W6; later W12 for ORFS) copies each one into place, and this
manifest is its account of that copy: ONE ROW PER IMPORTED FILE.

THE FIELD NAMES (schema 2; the importer writes exactly these)
=============================================================
Manifest: ``schema``, ``flow``, ``run_dir``, ``rows`` and, optionally,
``not_performed`` (a list; the rules whose step this run never started, for
W14). Row:

  ``step_id``           the flow YAML step id the file serves
  ``canonical_path``    project-relative, where the copy now lives
  ``tool_run_path``     run_dir-relative, where the tool wrote it
  ``canonical_sha256`` / ``tool_run_sha256``   equal: the same bytes
  ``flow``              ``librelane`` | ``orfs``
  ``tool``              the UNDERLYING tool (openroad, yosys, magic, ...)
  ``tool_step_id``      (librelane) the step INSTANCE id, e.g.
                        ``OpenROAD.DetailedRouting`` / ``OpenROAD.STAMidPNR-3``
  ``make_stage``        (orfs) the make stage, instead of tool_step_id
  ``step_dir``          run_dir-relative folder of that step run
  ``source_logs``       ``[{"path", "sha256"}, ...]`` run_dir-relative tool
                        transcripts inside ``step_dir``; a step may have
                        several (an STA corner writes two) or, for a row
                        that will be a #365 back-fill, none
  ``timestamp``, ``exit_code``
  ``measurement``       null (UNDECLARED), or exactly the record
                        `derived_measurement` reads from the artefact
  ``tool_version``      optional

``run_dir`` is PROJECT-relative (the LibreLane run lives inside the project),
the same convention as W19's witness; every run path is resolved against it,
never against the current directory.

SEGMENTS (llv1 W6: one import of several runs)
==============================================
The ``--librelane`` plan runs LibreLane twice (segment 1 ``--to
Checker.NetlistAssignStatements``, segment 2 from ``OpenROAD.CheckSDCFiles``),
and ONE import covers both. A manifest of one run is written exactly as
above (flat: ``run_dir`` + ``rows``). A manifest of two or more runs replaces
those two keys with

  ``segments``   ``[{"name", "run_dir", "rows", "flow_status"?}, ...]``

in run order: each segment's rows are relative to ITS ``run_dir``, names and
run_dirs are unique, and a ``canonical_path`` is imported once across all
segments. A one-element ``segments`` list is refused (one run is written
flat), so every one-run reader sees exactly the flat form. Readers iterate
``segments_of(manifest)``, which yields the flat form as one segment.
``flow_status`` (flat: top-level) is the importer's record that the run
finished. ``write_manifest``'s ``extra`` carries importer fields (never one
of the keys above).

ONE WITNESS SCHEMA (orchestrator ruling, llf_r3)
================================================
The provenance row for an imported file is W19's: `to_provenance_entry` calls
`_tool_log_provenance.witnessed_row`, which re-derives the witness from the
run itself (flow.log pinned by prefix, no skip, the block's own subprocess
transcripts, state_out.json completion, each output bound to its source,
exit_code from the evidence). This module never builds a witness of its own.
When the evidence does not support one, `witnessed_row` refuses and so does
`to_provenance_entry` (ManifestError): the importer then writes a #365
back-fill, never a weaker witness. A row's ``measurement`` is added to the
entry when it is not null; a missing ``tool_version`` renders ``version: None``
with ``version_capture: "NOT CAPTURED: ..."`` (#312/#365).

WHAT `validate_row` REFUSES
===========================
  * a witness that is not a tool run: run_dir that is the project (or holds
    it), a canonical file inside run_dir, the canonical file as its own
    tool-run file, a transcript that is the tool-run file, any run path that
    is absolute, contains ``..``, or resolves outside run_dir, and any
    transcript or tool-run file outside ``step_dir``;
  * a symlinked canonical file (or one reached through a symlink);
  * ``canonical_sha256 != tool_run_sha256``;
  * with ``verify_disk``: a missing run_dir, and any file that no longer
    hashes to its row (canonical, tool-run, each transcript);
  * a librelane row whose ``tool`` is not the tool its step runs, or without
    ``tool_step_id`` (an orfs row: ``make_stage``);
  * a ``measurement`` that is present but not an mcp-eda record stating
    ``measured``, names another tool or none, or (with ``verify_disk``) is
    not exactly the record `_runner_measurement.derive` reads from the
    canonical artefact (stated_by ``runner-derived``); where nothing can be
    derived it must be null. LibreLane writes no mcp-eda record, so any
    other value is a claim nobody measured.

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
import _runner_measurement  # noqa: E402
import _tool_log_provenance as _tlp  # noqa: E402

SCHEMA = "vibe-ic/external-flow-import/2"
#: Under reports/phase3/, which the stage-3 on_pass_review artefact list reads.
#: The importer writes through `write_manifest`, so it cannot pick another.
MANIFEST_REL = "reports/phase3/impl/import_manifest.json"

FLOW_LIBRELANE = "librelane"
FLOW_ORFS = "orfs"
FLOWS = (FLOW_LIBRELANE, FLOW_ORFS)
#: The field that names the flow's own step, per flow.
STEP_KEY_FOR = {FLOW_LIBRELANE: "tool_step_id", FLOW_ORFS: "make_stage"}

ROW_KEYS = ("step_id", "canonical_path", "tool_run_path", "canonical_sha256",
            "tool_run_sha256", "flow", "tool", "step_dir", "source_logs",
            "timestamp", "exit_code", "measurement")

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


def _bad_rel(value: Any) -> bool:
    if not (isinstance(value, str) and value.strip()):
        return True
    rel = PurePosixPath(value)
    return rel.is_absolute() or ".." in rel.parts


def _run_abs(project: Path, run_dir: str) -> Path:
    return Path(project) / run_dir


def _run_relative(run_abs: Path, path: Path, key: str) -> str:
    """``path`` as a posix path relative to the run; refuses outside it."""
    p = Path(path)
    if not p.is_absolute():
        p = run_abs / p
    real, root = _real(p), _real(run_abs)
    if not _under(real, root):
        raise ManifestError(f"{key} {path} is not inside run_dir {run_abs}")
    return real.relative_to(root).as_posix()


def make_row(project: Path, *, run_dir: str, step_id: str,
             canonical_path: str, tool_run_path: Path, flow: str, tool: str,
             step_dir: str, source_logs: List[Path], timestamp: str,
             exit_code: int, measurement: Optional[Dict[str, Any]],
             tool_step_id: Optional[str] = None,
             make_stage: Optional[str] = None,
             tool_version: Optional[str] = None) -> Dict[str, Any]:
    """Build one row from the files ON DISK: every digest is computed here.

    ``run_dir`` is project-relative. ``tool_run_path`` and each of
    ``source_logs`` are absolute, or relative to the run; they are STORED
    relative to the run and always resolved against it.
    """
    project = Path(project)
    if _bad_rel(run_dir):
        raise ManifestError(f"run_dir {run_dir!r} is not a project-relative "
                            "path")
    run_abs = _run_abs(project, run_dir)
    tr = _run_relative(run_abs, tool_run_path, "tool_run_path")
    logs = []
    for log in source_logs:
        rel = _run_relative(run_abs, log, "source_logs entry")
        logs.append({"path": rel, "sha256": sha256_file(run_abs / rel)})
    row: Dict[str, Any] = {
        "step_id": str(step_id),
        "canonical_path": str(PurePosixPath(canonical_path)),
        "tool_run_path": tr,
        "canonical_sha256": sha256_file(project / canonical_path),
        "tool_run_sha256": sha256_file(run_abs / tr),
        "flow": flow,
        "tool": tool,
        "step_dir": PurePosixPath(step_dir).as_posix(),
        "source_logs": logs,
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


def _shape_problems(row: Dict[str, Any]) -> List[str]:
    problems: List[str] = []
    for k in ("step_id", "canonical_path", "tool_run_path", "tool",
              "step_dir", "timestamp"):
        if not (isinstance(row[k], str) and row[k].strip()):
            problems.append(f"'{k}' is not a non-empty string")
    for k in ("canonical_sha256", "tool_run_sha256"):
        if not (isinstance(row[k], str) and _SHA_RE.match(row[k])):
            problems.append(f"'{k}' is not a sha256:<64 hex> digest")
    logs = row["source_logs"]
    if not isinstance(logs, list):
        problems.append("'source_logs' is not a list")
    else:
        for i, obj in enumerate(logs):
            if not (isinstance(obj, dict) and isinstance(obj.get("path"), str)
                    and _SHA_RE.match(str(obj.get("sha256") or ""))):
                problems.append(f"source_logs[{i}] is not {{path, sha256}}")
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
        if flow == FLOW_LIBRELANE and isinstance(row.get(want), str):
            runs = _tlp.underlying_tool(row[want])
            if runs is not None and runs != row["tool"]:
                problems.append(f"tool {row['tool']!r} is not the tool "
                                f"{row[want]!r} runs ({runs!r})")
    if row["measurement"] is not None:
        # null is the honest UNDECLARED state; a record that is present must
        # be a real one, and must be the record of THIS row's tool.
        meas = _mcp_measurement.from_provenance_entry(
            {"measurement": row["measurement"]})
        if meas.undeclared:
            problems.append("'measurement' is neither null nor an mcp-eda "
                            "measurement record stating measured true/false")
        elif meas.tool != row["tool"]:
            problems.append(f"'measurement' is {meas.tool!r}'s record, not "
                            f"{row['tool']!r}'s")
    return problems


def validate_row(row: Any, project: Path, run_dir: Any, *,
                 verify_disk: bool = True) -> List[str]:
    """Problems with one row of the run in ``run_dir``; empty when valid."""
    if not isinstance(row, dict):
        return ["row is not an object"]
    problems = [f"missing '{k}'" for k in ROW_KEYS if k not in row]
    if problems:
        return problems
    problems = _shape_problems(row)
    if _bad_rel(run_dir):
        problems.append(f"run_dir {run_dir!r} is not a project-relative path")
    if problems:
        return problems

    rels = [("canonical_path", row["canonical_path"]),
            ("tool_run_path", row["tool_run_path"]),
            ("step_dir", row["step_dir"])]
    rels += [(f"source_logs[{i}]", o["path"])
             for i, o in enumerate(row["source_logs"])]
    for key, value in rels:
        if _bad_rel(value):
            where = "the project" if key == "canonical_path" else "run_dir"
            problems.append(f"{key} {value!r} is not a relative path inside "
                            f"{where}")
    if problems:
        return problems
    project = Path(project)
    run_abs = _run_abs(project, str(run_dir))
    canon = project / row["canonical_path"]
    tool_file = run_abs / row["tool_run_path"]
    step_abs = run_abs / row["step_dir"]
    logs = [(o, run_abs / o["path"]) for o in row["source_logs"]]
    if row["canonical_sha256"] != row["tool_run_sha256"]:
        problems.append("canonical_sha256 != tool_run_sha256: the published "
                        "file is not the bytes the tool wrote")
    if any(o["path"] == row["tool_run_path"] for o, _ in logs):
        problems.append("a source log is the tool-run file itself: a file "
                        "cannot witness its own run")
    link = _through_symlink(project, row["canonical_path"])
    if link:
        problems.append(f"{link} is a symlink: an imported file is copied, "
                        "never linked")
    # THE WITNESS MUST BE A TOOL RUN, NOT THE PROJECT.
    root, proj = _real(run_abs), _real(project)
    if _under(proj, root):
        problems.append(f"run_dir {run_dir!r} is the project or contains it: "
                        "the witness must be a tool run, not the project")
    elif _under(_real(canon), root):
        problems.append(f"the canonical file lies inside run_dir {run_dir!r}: "
                        "the witness would be the published file itself")
    step_real = _real(step_abs)
    if not _under(step_real, root):
        problems.append(f"step_dir {row['step_dir']!r} resolves outside run_dir")
    for key, path in [("tool_run_path", tool_file)] + [
            (f"source_logs {o['path']!r}", p) for o, p in logs]:
        if not _under(_real(path), root):
            problems.append(f"{key} resolves outside run_dir {run_dir!r}")
        elif not _under(_real(path), step_real):
            problems.append(f"{key} is not inside step_dir "
                            f"{row['step_dir']!r}")
    if _real(tool_file) == _real(canon):
        problems.append("tool_run_path is the canonical file itself")
    if verify_disk:
        if not run_abs.is_dir():
            problems.append(f"run_dir {run_dir!r} is not a directory")
        checks = [("canonical_path", canon, row["canonical_sha256"]),
                  ("tool_run_path", tool_file, row["tool_run_sha256"])]
        checks += [(f"source_logs {o['path']!r}", p, o["sha256"])
                   for o, p in logs]
        for key, path, want in checks:
            if link and key == "canonical_path":
                continue
            if not path.is_file():
                problems.append(f"{key} {path} is not a file on disk")
            elif sha256_file(path) != want:
                problems.append(f"{key} {path} no longer hashes to its row")
        # THE MEASUREMENT IS RE-DERIVED, NEVER TAKEN FROM THE CALLER. LibreLane
        # writes no mcp-eda record, so a non-null value can only be vibe-ic's
        # own claim; the one honest source is the artefact itself
        # (`_runner_measurement.derive`, stated_by runner-derived). A value
        # that is not exactly that reading -- a self-report, a hand edit, a
        # measured:true over a DEF with no components -- is refused, and so is
        # any record where the artefact supports none (it must be null).
        # EQUAL TO THE DERIVATION, BOTH WAYS: null is honest only where the
        # artefact yields no reading -- a null over an artefact that DOES
        # read would hide a measurement (a measured:false included).
        if not link:
            derived = derived_measurement(project, row["canonical_path"],
                                          row["tool"])
            if row["measurement"] is None and derived is not None:
                problems.append("'measurement' is null but the imported "
                                "artefact yields a derived record; it must be "
                                "that record")
            elif row["measurement"] is not None and derived is None:
                problems.append("'measurement' must be null: nothing can be "
                                "derived from the imported artefact")
            elif row["measurement"] != derived:
                problems.append("'measurement' is not the record derived "
                                "from the imported artefact")
    return problems


def derived_measurement(project: Path, canonical_path: str,
                        tool: str) -> Optional[Dict[str, Any]]:
    """The only measurement a row may carry: read from the artefact itself,
    or None when nothing can be stated (then the row's value is null)."""
    return _runner_measurement.derive(Path(project), canonical_path, tool)


def to_provenance_entry(row: Dict[str, Any], project: Path,
                        run_dir: str) -> Dict[str, Any]:
    """The provenance.jsonl record that witnesses one imported file.

    Validated against the disk first, then built by W19's `witnessed_row`
    (the one witness schema). Raises ManifestError when either refuses: the
    importer then writes a #365 back-fill.
    """
    # Resolved first, as witnessed_row resolves it: a relative or symlinked
    # project path must not turn a genuine witness into a refusal.
    project = Path(project).resolve()
    # VALIDATE FIRST: witnessed_row re-derives the witness, but not the row's
    # own fields (the measurement above all). A refused row is never rendered.
    problems = validate_row(row, project, run_dir, verify_disk=True)
    if problems:
        raise ManifestError("; ".join(problems))
    if row["flow"] not in _tlp.SUPPORTED_FLOWS:
        raise ManifestError(f"flow {row['flow']!r} has no witness rule yet")
    run_abs = _run_abs(Path(project), run_dir)
    try:
        entry = _tlp.witnessed_row(
            Path(project), flow=row["flow"], step_id=row["tool_step_id"],
            run_dir=run_abs, step_dir=row["step_dir"],
            outputs={row["canonical_path"]: run_abs / row["tool_run_path"]},
            version=row.get("tool_version"), timestamp=row["timestamp"])
    except _tlp.WitnessRefused as exc:
        raise ManifestError(f"no witnessed row: {exc}") from exc
    # The manifest's transcripts must be ones the witness itself found.
    cited = {o["path"]: o["sha256"] for o in entry["witness"]["logs"]}
    for o in row["source_logs"]:
        if cited.get(o["path"]) != o["sha256"]:
            raise ManifestError(
                f"source log {o['path']} is not a transcript the flow's own "
                f"log names for {row['tool_step_id']!r}")
    if row["exit_code"] != entry["exit_code"]:
        raise ManifestError(f"exit_code {row['exit_code']} disagrees with the "
                            f"witness ({entry['exit_code']})")
    entry["step_id"] = row["step_id"]
    if not row.get("tool_version"):
        # #312/#365: an unknown build is None WITH its reason, never "".
        entry["version"] = None
        entry["version_capture"] = ("NOT CAPTURED: the import row carries no "
                                    "tool version for this step")
    if row["measurement"] is not None:
        entry["measurement"] = row["measurement"]
    return entry


def manifest_path(project: Path) -> Path:
    return Path(project) / MANIFEST_REL


#: Keys the manifest itself defines; ``write_manifest(extra=...)`` may not
#: set them.
MANIFEST_KEYS = ("schema", "flow", "run_dir", "rows", "segments",
                 "not_performed", "flow_status")
SEGMENT_KEYS = ("name", "run_dir", "rows")


def segments_of(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The manifest's runs, in order, each ``{name, run_dir, rows, ...}``.

    A flat (one-run) manifest is one segment named None; a segmented one is
    its ``segments`` list. Readers that need a row's run go through this.
    """
    if "segments" in manifest:
        return list(manifest["segments"])
    seg = {"name": None, "run_dir": manifest.get("run_dir"),
           "rows": manifest.get("rows")}
    if "flow_status" in manifest:
        seg["flow_status"] = manifest["flow_status"]
    return [seg]


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
    if not isinstance(manifest.get("not_performed", []), list):
        problems.append("not_performed, when present, is a list")
    if "segments" in manifest:
        segs = manifest["segments"]
        mixed = [k for k in ("run_dir", "rows", "flow_status") if k in manifest]
        if mixed:
            return problems + [f"a segmented manifest carries {mixed} per "
                               "segment, not at the top"]
        if not isinstance(segs, list) or len(segs) < 2:
            return problems + ["segments is a list of two or more runs (one "
                               "run is written flat: run_dir + rows)"]
        for i, seg in enumerate(segs):
            if not isinstance(seg, dict):
                return problems + [f"segment {i} is not an object"]
            missing = [k for k in SEGMENT_KEYS if k not in seg]
            if missing:
                return problems + [f"segment {i} is missing {missing}"]
            if not (isinstance(seg["name"], str) and seg["name"].strip()):
                problems.append(f"segment {i}: name is not a non-empty string")
        for key in ("name", "run_dir"):
            vals = [seg[key] for seg in segs]
            if len(set(map(str, vals))) != len(vals):
                problems.append(f"segments repeat a {key}: {vals}")
        labels = [f"segment {seg['name']!r} " for seg in segs]
    else:
        segs = segments_of(manifest)
        labels = [""]
    seen: Dict[str, str] = {}
    for label, seg in zip(labels, segs):
        run_dir = seg["run_dir"]
        if _bad_rel(run_dir):
            problems.append(f"{label}run_dir {run_dir!r} is not a "
                            "project-relative path")
            continue
        if not isinstance(seg.get("flow_status", {}), dict):
            problems.append(f"{label}flow_status, when present, is an object")
        rows = seg["rows"]
        if not isinstance(rows, list):
            problems.append(f"{label}rows is not a list")
            continue
        for i, row in enumerate(rows):
            for p in validate_row(row, project, run_dir,
                                  verify_disk=verify_disk):
                problems.append(f"{label}row {i}: {p}")
            if isinstance(row, dict):
                if flow in FLOWS and row.get("flow") != flow:
                    problems.append(f"{label}row {i}: flow "
                                    f"{row.get('flow')!r} in a {flow} manifest")
                cp = row.get("canonical_path")
                if cp in seen:
                    problems.append(f"{label}row {i}: canonical_path {cp!r} is "
                                    f"already imported by {seen[cp]}")
                elif isinstance(cp, str):
                    seen[cp] = f"{label}row {i}"
    return problems


def write_manifest(project: Path, *, flow: str, run_dir: Optional[str] = None,
                   rows: Optional[List[Dict[str, Any]]] = None,
                   not_performed: Optional[List[Any]] = None,
                   segments: Optional[List[Dict[str, Any]]] = None,
                   flow_status: Optional[Dict[str, Any]] = None,
                   extra: Optional[Dict[str, Any]] = None) -> Path:
    """Validate against the disk, then write atomically. Invalid → nothing.

    One run: ``run_dir`` + ``rows`` (+ ``flow_status``), written flat. Two or
    more: ``segments`` (each ``{name, run_dir, rows, flow_status?}``).
    """
    if (segments is None) == (run_dir is None or rows is None):
        raise ManifestError("give run_dir + rows (one run) or segments "
                            "(two or more), not both and not neither")
    if segments is not None:
        if flow_status is not None:
            raise ManifestError("a segmented manifest carries flow_status "
                                "per segment")
        manifest: Dict[str, Any] = {"schema": SCHEMA, "flow": flow,
                                    "segments": segments}
    else:
        manifest = {"schema": SCHEMA, "flow": flow,
                    "run_dir": str(run_dir), "rows": rows}
        if flow_status is not None:
            manifest["flow_status"] = flow_status
    if not_performed is not None:
        manifest["not_performed"] = not_performed
    clash = sorted(set(extra or {}) & set(MANIFEST_KEYS))
    if clash:
        raise ManifestError(f"extra may not set the manifest's own keys {clash}")
    manifest.update(extra or {})
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
