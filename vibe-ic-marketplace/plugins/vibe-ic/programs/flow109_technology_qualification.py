#!/usr/bin/env python3
"""D03 Default technology qualification over a current native checkpoint.

ENFORCEMENT: blocking for this component.  It validates the selected native
tool receipt, its exact config/State inputs, selected PDK Liberty/technology
views, and then delegates cell coverage to ``pdk_consistency_check``.  It does
not claim qualification for tools or decks absent from that executed config,
and it does not by itself establish Flow109 row or delivery acceptance.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
from pathlib import Path
import sys
from typing import Mapping

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import librelane_contract as contract
import pdk_consistency_check as pdk_check

STEP = "D03"
NATIVE_STEP = "flow109.D03"
SCHEMA = "vibeic.flow109.technology-qualification.v1"
PINNED_IMAGE = ("ghcr.io/vibeic/vibeic-eda@sha256:"
                "0fc30ecd1307a11bd7757d1c5710127cd88e245059f29eab8b52e38ba4d60406")
CURRENT_RECORDS = (
    (Path("reports/phase3/ir_drop.json"), "tool_state", "tool_state_sha256"),
    (Path("reports/phase3/sta_postpnr_signoff.json"), "sta_state", "sta_state_sha256"),
)
PROJECT_PREFIXES = ("input", "phase1", "phase2", "phase3", "reports", "steps", "adopted")


class D03Refusal(ValueError):
    def __init__(self, code: str, detail: str, *, failed: bool = False):
        self.code, self.detail, self.failed = code, detail, failed
        super().__init__(f"{code}: {detail}")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise D03Refusal("D03_EVIDENCE_ABSENT_OR_INVALID", str(path)) from exc
    if not isinstance(value, dict):
        raise D03Refusal("D03_EVIDENCE_ABSENT_OR_INVALID", str(path))
    return value


def _record(project: Path, path: Path) -> dict:
    path = path.resolve(strict=True)
    try:
        shown = str(path.relative_to(project.resolve()))
    except ValueError:
        shown = str(path)
    return {"path": shown, "sha256": _sha(path)}


def _relocate(project: Path, raw: object) -> Path:
    """Resolve current project or pinned PDK paths without trusting old roots."""
    if not isinstance(raw, str) or not raw:
        raise D03Refusal("D03_INPUT_IDENTITY_INVALID", repr(raw))
    path = Path(raw)
    if str(path).startswith("/project/"):
        candidate = project / path.relative_to("/project")
    elif str(path).startswith("/foss/pdks/"):
        candidate = path
    elif not path.is_absolute():
        candidate = project / path
    else:
        parts = path.parts
        matches = [i for i, part in enumerate(parts) if part in PROJECT_PREFIXES]
        candidates = [project.joinpath(*parts[i:]) for i in matches]
        existing = [item for item in candidates if item.is_file()]
        if len(existing) != 1:
            raise D03Refusal("D03_CHECKPOINT_RELOCATION_UNBOUND", raw)
        candidate = existing[0]
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise D03Refusal("D03_INPUT_MISSING", raw) from exc
    if not (str(path).startswith("/foss/pdks/")
            or resolved.is_relative_to(project.resolve())):
        raise D03Refusal("D03_INPUT_ESCAPES_PROJECT", raw)
    return resolved


def _bound(project: Path, raw: object, expected: object, label: str) -> Path:
    path = _relocate(project, raw)
    if not isinstance(expected, str) or len(expected) != 64 or _sha(path) != expected:
        raise D03Refusal("D03_INPUT_STALE", f"{label}: {raw}")
    return path


def _current_receipt(project: Path) -> tuple[Path, dict, dict]:
    found = []
    for rel, state_key, digest_key in CURRENT_RECORDS:
        record_path = project / rel
        if not record_path.is_file():
            continue
        record = _read(record_path)
        state = _bound(project, record.get(state_key), record.get(digest_key), state_key)
        found.append((record_path, record, state.parent))
    if len(found) != 1:
        raise D03Refusal("D03_CURRENT_NATIVE_AUTHORITY_UNRESOLVED",
                         f"expected one current record, found {len(found)}")
    record_path, record, folder = found[0]
    receipt = _read(folder / "vibeic_receipt.json")
    step = (receipt.get("input") or {}).get("step")
    try:
        validated = contract.validate_step_receipt(folder, str(step or ""))
    except contract.Refusal as exc:
        raise D03Refusal("D03_NATIVE_RECEIPT_INVALID", str(exc)) from exc
    return record_path, record, validated


def _matching_paths(project: Path, config: Mapping, receipt_input: Mapping,
                    key: str, corner: str) -> list[Path]:
    raw = config.get(key)
    groups = raw if isinstance(raw, Mapping) else {}
    selected = []
    for pattern, values in groups.items():
        if not isinstance(pattern, str) or not fnmatch.fnmatch(corner, pattern):
            continue
        for value in values if isinstance(values, list) else [values]:
            expected = (receipt_input.get("config_files") or {}).get(str(value))
            selected.append(_bound(project, value, expected, f"{key}[{pattern}]"))
    if not selected:
        raise D03Refusal("D03_SELECTED_VIEW_MISSING", f"{key} at {corner}")
    return selected


def execute(project: Path, context: Mapping) -> dict:
    project = Path(project).resolve(strict=True)
    checks, inputs = [], []
    native_started = False
    try:
        row = context.get("resolved_row") or {}
        if (context.get("requested_step") != STEP or row.get("id") != STEP
                or context.get("context_sha256") in (None, "")):
            raise D03Refusal("D03_CONTEXT_INVALID", "current D03 context required")
        record_path, current, receipt = _current_receipt(project)
        fp = receipt["input"]
        if fp.get("image") != PINNED_IMAGE:
            raise D03Refusal("D03_TOOL_IMAGE_UNSUPPORTED", str(fp.get("image")))
        folder = _relocate(project, current.get("tool_state")).parent \
            if current.get("tool_state") else _relocate(project, current.get("sta_state")).parent
        config_path = folder / "config.json"
        state_path = folder / "state_out.json"
        # ``input.config`` / ``input.state`` are the provider's semantic
        # fingerprints, not byte hashes. validate_step_receipt above has
        # already rebound both files to receipt['sha256']; do not compare two
        # deliberately different identity domains.
        config = _read(config_path)
        state = _read(state_path)
        pdk = config.get("PDK")
        corner = config.get("DEFAULT_CORNER")
        if not isinstance(pdk, str) or not pdk or not isinstance(corner, str) or not corner:
            raise D03Refusal("D03_TECHNOLOGY_SELECTION_UNBOUND", "PDK/default corner")
        libs = (_matching_paths(project, config, fp, "CELL_LIBS", corner)
                + _matching_paths(project, config, fp, "PAD_LIBS", corner))
        techs = _matching_paths(project, config, fp, "TECH_LEFS", corner)
        netlist_raw = state.get("nl")
        netlist = _bound(project, netlist_raw,
                         (fp.get("state_files") or {}).get(str(netlist_raw)), "state.nl")
        native_started = True
        cells = pdk_check.extract_netlist_cells(netlist.read_text(errors="replace"), str(netlist))
        pdk_cells = set()
        for lib in libs:
            pdk_cells.update(pdk_check.extract_liberty_cells(lib.read_text(errors="replace")))
        findings = pdk_check.audit_pdk_consistency(cells, pdk_cells, pdk)
        checks.append({"check": "pdk_consistency_check",
                       "status": "FAIL" if findings else "PASS",
                       "findings": [vars(item) for item in findings],
                       "total_cell_instances": len(cells),
                       "unique_cell_types": len({item["cell_name"] for item in cells}),
                       "pdk_cell_count": len(pdk_cells)})
        inputs.extend([_record(project, record_path), _record(project, config_path),
                       _record(project, state_path), _record(project, netlist)])
        inputs.extend(_record(project, item) for item in libs + techs)
        status = "FAIL" if findings else "PASS"
        reason = "D03_PDK_LIBRARY_DECK_TOOL_COMPONENT_" + status
    except D03Refusal as exc:
        status = "FAIL" if exc.failed else "NOT_MEASURED"
        reason = exc.code
        checks.append({"check": "adapter_refusal", "status": status,
                       "detail": exc.detail})
    return {
        "schema": SCHEMA, "step_id": STEP,
        "program": "flow109_technology_qualification",
        "context_sha256": context.get("context_sha256"),
        "component_status": status, "reason": reason,
        "checks": checks, "inputs": inputs,
        "native_started": native_started,
        "metric_acceptance_status": "UNIMPLEMENTED",
        "flow109_acceptance": "NOT_MEASURED",
        "whole_flow_acceptance": "NOT_MEASURED",
        "delivery_accepted": False,
        "scope": "executed native PDK/Liberty/technology-view/tool component only",
        "unsupported_scope": "unexecuted tools, decks, corners, and formal D03 row metrics",
    }

