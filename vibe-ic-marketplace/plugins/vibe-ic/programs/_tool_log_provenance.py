#!/usr/bin/env python3
"""Provenance rows for artefacts imported from an external flow's own step logs.

WHY THIS EXISTS (plan W19, owner decision 4a, 2026-09-28)
--------------------------------------------------------
Under ``--librelane`` the layout is produced by LibreLane, not by a vibe-ic
session, and vibe-ic IMPORTS the step outputs into its canonical tree. The
provenance ledger then needs a row per imported artefact, and there are only
two honest shapes for it:

* **A back-fill.** The runner hashes a file it did not watch being written.
  Doctrine #365 (``phase3_one_shot_runner._restamp_provenance_output``) says
  that row is ``reconstructed: true`` with ``duration_ms: None``. Nothing here
  changes that: a back-fill is still a back-fill.
* **A witnessed run.** The owner ruled (decision 4a) that LibreLane's OWN step
  log is a witness: the step ran, the tool wrote a transcript, and the flow's
  own ``flow.log`` names the step and the folder it ran in. Such a row is
  ``reconstructed: false``, is attributed to LibreLane, and CITES the evidence
  by path and sha256 so any later reader can re-check it.

The difference between the two is exactly the citation, so the citation is
what this module builds and what ``verify_witness`` re-reads. A witness that
cannot be re-read — its log is gone, rewritten, a symlink, outside the
project, or its step is not in the flow's own log — is not a witness, and
``provenance_check`` refuses a row that claims one.

ATTRIBUTION
-----------
``tool`` stays the UNDERLYING tool (``openroad``, ``yosys``, ``magic``,
``klayout``, ``netgen``, ``verilator``), derived from the LibreLane step id and
never taken from the caller, so the flow's existing ``provenance_check
--tool`` allow-lists judge the row unchanged (decision 4a, not 4b: no
allow-list or flow-YAML edit). ``attributed_to`` names the flow that ran the
tool. A ``Checker.*`` / ``Misc.*`` step runs no subprocess and writes no tool
log, so it can never be witnessed; neither can a step whose folder holds no
log. Those imports stay back-fills.

WHAT THIS DOES NOT DO
---------------------
It does not write a ``measurement`` record. ``mcp-eda``'s
``measurementRecord()`` is the only writer of that schema; a row without one
reads UNMEASURED under ``provenance_check --require-measured`` (the
INCOMPLETE tier, rc 0, never a PASS). Stating ``measured: true`` for an
imported step is the importer's call (W6), made from the step's own state,
not assumed here.

chip-AGNOSTIC: no design, PDK or corner literal. The only literals are
LibreLane's own flow-log grammar and its step-id prefixes.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

#: The external flows whose step logs count as a witness. ORFS joins with its
#: own importer (plan W12 / the ORFS half of W19); until then an ``orfs`` row
#: claiming a witness is refused, not guessed at.
SUPPORTED_FLOWS = ("librelane",)

WITNESS_KIND = "tool_step_log"

#: LibreLane step-id prefix -> the tool that actually ran. ``Odb.*`` steps run
#: inside OpenROAD's Python. Prefixes absent here (``Checker``, ``Misc``) run
#: no tool and cannot be witnessed.
_STEP_PREFIX_TOOL = {
    "OpenROAD": "openroad",
    "Odb": "openroad",
    "Yosys": "yosys",
    "Magic": "magic",
    "KLayout": "klayout",
    "Netgen": "netgen",
    "Verilator": "verilator",
}

#: LibreLane's flow.log line for a step it started. Fixed grammar, printed by
#: ``librelane.steps.step.Step.start``: ``Running '<id>' at '<dir>'…``.
_RUNNING_RE = re.compile(r"^Running '([^']+)' at '([^']+)'…$", re.M)

class WitnessRefused(ValueError):
    """The caller asked for a witnessed row the evidence does not support."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def underlying_tool(step_id: str) -> Optional[str]:
    """The tool a LibreLane step runs, or None when it runs no tool."""
    return _STEP_PREFIX_TOOL.get(str(step_id).split(".", 1)[0])


def flow_log_names_step(flow_log: str, step_id: str, step_dir: str) -> bool:
    """Does the flow's own log say it started ``step_id`` in ``step_dir``?

    ``step_dir`` is the step folder relative to the run directory, e.g.
    ``44-openroad-detailedrouting`` or a nested sub-step
    ``42-openroad-repairantennas/2-openroad-checkantennas``. LibreLane prints
    the folder relative to its working directory (``runs/<tag>/<dir>``), so a
    match is a path whose trailing components equal ``step_dir``.
    """
    import instrument_calibration
    instrument_calibration.assert_calibrated(
        "_tool_log_provenance::flow_log_names_step")
    want = tuple(p for p in Path(step_dir).parts if p not in ("", "."))
    if not want:
        return False
    for m in _RUNNING_RE.finditer(flow_log or ""):
        if m.group(1) != step_id:
            continue
        parts = Path(m.group(2)).parts
        if len(parts) >= len(want) and tuple(parts[-len(want):]) == want:
            return True
    return False


def _inside(project: Path, path: Path, what: str) -> str:
    """``path`` relative to ``project``; refuses symlinks and escapes."""
    raw = path if path.is_absolute() else project / path
    # Every component under the project must be a real directory/file:
    # a symlinked log or folder is a pointer to evidence, not evidence.
    try:
        rel = raw.relative_to(project)
    except ValueError:
        raise WitnessRefused(f"{what} {raw} is outside the project {project}")
    cur = project
    for part in rel.parts:
        cur = cur / part
        if cur.is_symlink():
            raise WitnessRefused(f"{what} {raw} passes through a symlink ({cur})")
    if ".." in rel.parts:
        raise WitnessRefused(f"{what} {raw} escapes the project")
    return rel.as_posix()


def witnessed_row(project: Path, *, flow: str, step_id: str, run_dir: Path,
                  step_dir: str, logs: Iterable[Path],
                  outputs: Dict[str, Path], exit_code: int,
                  duration_ms: Optional[int] = None,
                  version: Optional[str] = None,
                  timestamp: Optional[str] = None) -> Dict[str, Any]:
    """Build the provenance row for outputs imported from one external step.

    Every hash is computed here from the bytes on disk, never accepted from
    the caller. Raises ``WitnessRefused`` when the evidence does not support a
    witnessed row; the caller then writes a ``reconstructed: true`` back-fill
    (#365) instead — never a weaker witness.
    """
    project = Path(project).resolve()
    if flow not in SUPPORTED_FLOWS:
        raise WitnessRefused(f"flow {flow!r} has no witness rule "
                             f"(supported: {', '.join(SUPPORTED_FLOWS)})")
    tool = underlying_tool(step_id)
    if tool is None:
        raise WitnessRefused(f"step {step_id!r} runs no tool, so it writes no "
                             "tool log to witness it")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise WitnessRefused(f"exit_code must be an int, got {exit_code!r}")
    run_rel = _inside(project, Path(run_dir), "run directory")
    run_abs = project / run_rel
    step_rel = Path(step_dir).as_posix()
    step_abs = run_abs / step_rel
    _inside(project, step_abs, "step directory")
    if not step_abs.is_dir():
        raise WitnessRefused(f"step directory {step_abs} does not exist")
    flow_log = run_abs / "flow.log"
    flow_log_rel = _inside(project, flow_log, "flow log")
    if not flow_log.is_file():
        raise WitnessRefused(f"{flow_log} is missing: nothing names the step")
    if not flow_log_names_step(flow_log.read_text(errors="replace"),
                               step_id, step_rel):
        raise WitnessRefused(f"{flow_log_rel} never started {step_id!r} in "
                             f"{step_rel!r}")
    cited: List[Dict[str, str]] = []
    for log in logs:
        rel = _inside(project, Path(log), "step log")
        abs_log = project / rel
        if not abs_log.is_file():
            raise WitnessRefused(f"step log {rel} does not exist")
        try:
            abs_log.relative_to(step_abs)
        except ValueError:
            raise WitnessRefused(f"step log {rel} is not inside the step "
                                 f"directory {step_rel}")
        cited.append({"path": rel, "sha256": _sha256(abs_log)})
    if not cited:
        raise WitnessRefused(f"no tool log cited for {step_id!r}")
    outs: Dict[str, str] = {}
    for rel_name, path in outputs.items():
        rel = _inside(project, Path(path), "output")
        if rel != Path(rel_name).as_posix():
            raise WitnessRefused(f"output key {rel_name!r} is not the path "
                                 f"of the file it declares ({rel})")
        if not (project / rel).is_file():
            raise WitnessRefused(f"output {rel} does not exist")
        outs[rel] = _sha256(project / rel)
    if not outs:
        raise WitnessRefused("a row must declare at least one output")
    row: Dict[str, Any] = {
        "timestamp": timestamp or _dt.datetime.now(_dt.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tool": tool,
        "attributed_to": flow,
        "step": step_id,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "outputs": outs,
        "reconstructed": False,
        "witness": {
            "kind": WITNESS_KIND,
            "flow": flow,
            "step_id": step_id,
            "run_dir": run_rel,
            "step_dir": step_rel,
            "flow_log": {"path": flow_log_rel, "sha256": _sha256(flow_log)},
            "logs": cited,
        },
    }
    if version:
        row["version"] = version
    return row


def claims_witness(entry: Dict[str, Any]) -> bool:
    """Does this row claim anything beyond a plain logged run?"""
    return isinstance(entry, dict) and (
        "witness" in entry or "attributed_to" in entry)


def verify_witness(entry: Dict[str, Any],
                   project: Path) -> Tuple[Optional[bool], str]:
    """Re-read a row's witness. ``(None, "")`` when the row claims none.

    ``(False, reason)`` for every way a claimed witness fails to hold up;
    ``(True, "")`` only when each cited file is still the file it cites and
    the flow's own log still names the step in the cited folder.
    """
    if not claims_witness(entry):
        return None, ""
    project = Path(project).resolve()
    w = entry.get("witness")
    if not isinstance(w, dict):
        return False, "attributed_to is set but the row cites no witness"
    if entry.get("reconstructed") is True:
        return False, ("the row is a runner back-fill (reconstructed: true, "
                       "#365) and also claims a witness")
    if w.get("kind") != WITNESS_KIND:
        return False, f"unknown witness kind {w.get('kind')!r}"
    flow = w.get("flow")
    if flow not in SUPPORTED_FLOWS or entry.get("attributed_to") != flow:
        return False, (f"witness flow {flow!r} / attributed_to "
                       f"{entry.get('attributed_to')!r} is not a supported "
                       "matching pair")
    step_id = str(w.get("step_id") or "")
    if entry.get("tool") != underlying_tool(step_id):
        return False, (f"tool {entry.get('tool')!r} is not the tool step "
                       f"{step_id!r} runs ({underlying_tool(step_id)!r})")

    def _cited(obj: Any, what: str) -> Tuple[Optional[Path], str]:
        if not isinstance(obj, dict) or not obj.get("path") \
                or not obj.get("sha256"):
            return None, f"{what} citation is missing its path or sha256"
        try:
            rel = _inside(project, Path(str(obj["path"])), what)
        except WitnessRefused as exc:
            return None, str(exc)
        p = project / rel
        if not p.is_file():
            return None, f"cited {what} {rel} does not exist"
        if _sha256(p) != str(obj["sha256"]):
            return None, (f"cited {what} {rel} no longer has the sha256 the "
                          "row cites")
        return p, ""

    flow_log, why = _cited(w.get("flow_log"), "flow log")
    if flow_log is None:
        return False, why
    try:
        run_rel = _inside(project, Path(str(w.get("run_dir") or "")),
                          "run directory")
    except WitnessRefused as exc:
        return False, str(exc)
    if flow_log != project / run_rel / "flow.log":
        return False, "the cited flow log is not the run directory's flow.log"
    step_dir = str(w.get("step_dir") or "")
    step_abs = project / run_rel / step_dir
    import instrument_calibration
    try:
        named = flow_log_names_step(flow_log.read_text(errors="replace"),
                                    step_id, step_dir)
    except instrument_calibration.Uncalibrated as exc:
        return False, f"the flow-log reader may not judge: {exc}"
    if not named:
        return False, (f"the flow's own log never started {step_id!r} in "
                       f"{step_dir!r}")
    logs = w.get("logs")
    if not isinstance(logs, list) or not logs:
        return False, "the witness cites no tool log"
    for obj in logs:
        p, why = _cited(obj, "step log")
        if p is None:
            return False, why
        try:
            p.relative_to(step_abs)
        except ValueError:
            return False, (f"cited step log {obj.get('path')} is not inside "
                           f"the step directory {step_dir}")
    return True, ""


def is_witnessed(entry: Dict[str, Any], project: Path) -> bool:
    """True only for a row whose witness re-verifies. A back-fill never is."""
    ok, _ = verify_witness(entry, project)
    return ok is True
