#!/usr/bin/env python3
"""Flow109 D75 Default bare-die package/test interface component.

This is a BLOCKING component check for the inputs it actually consumes.  It
does not approve a package or a tapeout: packaged SI/PI/thermal co-design and
all Flow109 metric acceptance remain NOT_MEASURED.  The supported path reuses
the existing declaration, pad-side, pad/BTerm, bond-opening, PERC and DFT
consumers.  Missing geometry or authority is named, never read as PASS.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Mapping

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import _pad_ring
import _tapeout_declaration as declaration
import dft_atpg_coverage_check
import pad_bterm_coincidence_check
import pad_side_constraint_check
import perc_signoff_check
import provider_bond_pad_edge
import tapeout_declaration_check
import tapeout_readiness_check
from _staged_pdk_tech_lef import (TechLefResolutionError,
                                  discover_staged_tech_lefs,
                                  select_staged_tech_lef)

SCHEMA = "vibeic.flow109.package-ready.v1"
STEP = "D75"
NATIVE_STEP = "flow109.D75"
DECLARATION = Path(declaration.DECLARATION_REL)
OPERATOR_ANSWERS = Path("input/submission_template/operator_answers.json")
IO_REPORT = Path("reports/phase3/io_pad_chip_top.json")
PAD_DEF = Path(_pad_ring.PADRING_DEF_REL)
PDK_DIR = Path("input/pdk")
CONTEXT_REQUESTS = Path("reports/flow109/requests")
PROVIDER_TARGET = Path("input/submission_template/provider_target.json")


class D75Refusal(ValueError):
    def __init__(self, code: str, detail: str, *, failed: bool = False):
        self.code, self.detail, self.failed = code, detail, failed
        super().__init__(f"{code}: {detail}")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def operation_sources() -> dict[str, str]:
    """Bind the exact adapter and existing consumers loaded for D75."""
    names = (
        "flow109_package_ready.py",
        "tapeout_declaration_check.py",
        "pad_side_constraint_check.py",
        "pad_bterm_coincidence_check.py",
        "provider_bond_pad_edge.py",
        "perc_signoff_check.py",
        "dft_atpg_coverage_check.py",
    )
    return {"programs/" + name: _sha(HERE / name) for name in names}


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise D75Refusal("D75_REQUIRED_INPUT_ABSENT_OR_INVALID", str(path)) from exc
    if not isinstance(value, dict):
        raise D75Refusal("D75_REQUIRED_INPUT_ABSENT_OR_INVALID", str(path))
    return value


def _record(project: Path, path: Path) -> dict:
    resolved = path if path.is_absolute() else project / path
    if not resolved.is_file():
        raise D75Refusal("D75_REQUIRED_INPUT_ABSENT_OR_INVALID", str(path))
    try:
        rel = str(resolved.resolve().relative_to(project.resolve()))
    except ValueError:
        rel = str(resolved.resolve())
    return {"path": rel, "sha256": _sha(resolved), "bytes": resolved.stat().st_size}


def _tree_records(project: Path, relative: Path) -> list[dict]:
    root = project / relative
    return [_record(project, path) for path in sorted(root.rglob("*"))
            if path.is_file()]


def _release_records(project: Path, value) -> list[dict]:
    """Turn existing provider `_release_file` identities back into inputs."""
    records = []
    values = value if isinstance(value, list) else [value]
    for item in values:
        if isinstance(item, Mapping) and isinstance(item.get("path"), str):
            rec = _record(project, Path(item["path"]))
            if rec["sha256"] != item.get("sha256"):
                raise D75Refusal("D75_PROVIDER_SOURCE_STALE", item["path"])
            records.append(rec)
    return records


def _provider_identity(project: Path, edge: Mapping) -> dict:
    """Bind the provider consumed by the bond-edge checker to its source."""
    target_path = project / PROVIDER_TARGET
    target = _read(target_path)
    provider = target.get("provider")
    if not isinstance(provider, str) or not provider.strip():
        raise D75Refusal("D75_PROVIDER_IDENTITY_UNBOUND", str(PROVIDER_TARGET))
    source = _record(project, target_path)
    release = edge.get("profile") if isinstance(edge, Mapping) else None
    if (not isinstance(release, Mapping)
            or release.get("sha256") != source["sha256"]
            or Path(str(release.get("path", ""))).resolve() != target_path.resolve()):
        raise D75Refusal("D75_PROVIDER_IDENTITY_MISMATCH", str(PROVIDER_TARGET))
    return {"provider": provider, "source": source}


def _context(context: Mapping) -> None:
    row = context.get("resolved_row") if isinstance(context, Mapping) else None
    dispatch = (row.get("profile", {}).get("dispatch", {})
                if isinstance(row, Mapping) else {})
    if (context.get("requested_step") != STEP
            or context.get("native_step_id") != NATIVE_STEP
            or dispatch.get("adapter") != "flow109_package_ready"):
        raise D75Refusal("D75_CONTEXT_MISMATCH", "D75 adapter context required")


def _context_input(project: Path, context: Mapping) -> dict:
    """Bind the exact canonical request whose feature choices D75 consumes."""
    try:
        import flow109
        current = flow109.validate_execution_context(context)
    except (KeyError, TypeError, ValueError) as exc:
        raise D75Refusal("D75_CONTEXT_INVALID", str(exc)) from exc
    identity = current.get("context_sha256")
    if not isinstance(identity, str) or len(identity) != 64:
        raise D75Refusal("D75_CONTEXT_INVALID", "context_sha256")
    path = project / CONTEXT_REQUESTS / f"{identity}.json"
    if not path.is_file():
        raise D75Refusal("D75_CONTEXT_SOURCE_UNBOUND", str(path.relative_to(project)))
    try:
        recorded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise D75Refusal("D75_CONTEXT_SOURCE_INVALID", str(path.relative_to(project))) from exc
    if recorded != current:
        raise D75Refusal("D75_CONTEXT_SOURCE_STALE", str(path.relative_to(project)))
    return _record(project, path)


def _io_lefs(project: Path, report: dict) -> list[Path]:
    values = report.get("io_library_lefs")
    if not isinstance(values, list) or not values:
        raise D75Refusal("D75_IO_LIBRARY_UNBOUND", str(IO_REPORT))
    paths = []
    for raw in values:
        if not isinstance(raw, str) or not raw.strip():
            raise D75Refusal("D75_IO_LIBRARY_UNBOUND", repr(raw))
        path = Path(raw)
        if not path.is_absolute():
            path = project / path
            try:
                path.resolve().relative_to(project.resolve())
            except ValueError as exc:
                raise D75Refusal("D75_INPUT_ESCAPES_PROJECT", raw) from exc
        if not path.is_file():
            raise D75Refusal("D75_IO_LIBRARY_UNBOUND", raw)
        paths.append(path)
    return paths


def _tech_lef(project: Path) -> Path:
    candidates = discover_staged_tech_lefs(project / PDK_DIR)
    try:
        selected = select_staged_tech_lef(project / PDK_DIR, candidates)
    except TechLefResolutionError as exc:
        raise D75Refusal("D75_TECH_LEF_UNBOUND", str(exc)) from exc
    if selected is None:
        raise D75Refusal("D75_TECH_LEF_UNBOUND", str(PDK_DIR))
    return selected.path


def _check(name: str, status: str, detail: object, **extra) -> dict:
    return {"check": name, "status": status, "detail": detail, **extra}


def _aggregate(checks: list[dict]) -> str:
    if any(item["status"] == "FAIL" for item in checks):
        return "FAIL"
    if any(item["status"] == "NOT_MEASURED" for item in checks):
        return "NOT_MEASURED"
    return "PASS"


def validate(project: Path, receipt: Mapping) -> None:
    """Rebind every recorded source; a copied/stale D75 PASS is unusable."""
    if receipt.get("schema") != SCHEMA or receipt.get("step_id") != STEP:
        raise D75Refusal("D75_RECEIPT_INVALID", "schema/step")
    context_sha256 = receipt.get("context_sha256")
    expected_context = str(CONTEXT_REQUESTS / f"{context_sha256}.json")
    context_refusals = {
        "D75_CONTEXT_INVALID", "D75_CONTEXT_SOURCE_UNBOUND",
        "D75_CONTEXT_SOURCE_INVALID", "D75_CONTEXT_SOURCE_STALE",
    }
    refused_context = receipt.get("reason") in context_refusals
    if refused_context and receipt.get("component_status") != "NOT_MEASURED":
        raise D75Refusal("D75_RECEIPT_INVALID", "context refusal status")
    if (not refused_context
            and (not isinstance(context_sha256, str) or len(context_sha256) != 64
                 or not any(isinstance(item, Mapping)
                            and item.get("path") == expected_context
                            for item in receipt.get("inputs", [])))):
        raise D75Refusal("D75_RECEIPT_INVALID", "context identity")
    if receipt.get("consumer_load_identity") != operation_sources():
        raise D75Refusal("D75_LOAD_IDENTITY_MISMATCH", "D75 adapter/consumer sources")
    for item in receipt.get("inputs", []):
        if not isinstance(item, Mapping) or not item.get("path") or not item.get("sha256"):
            raise D75Refusal("D75_RECEIPT_INVALID", "input identity")
        path = Path(str(item["path"]))
        resolved = path if path.is_absolute() else project / path
        if not resolved.is_file() or _sha(resolved) != item["sha256"]:
            raise D75Refusal("D75_INPUT_STALE", str(path))
    if not refused_context:
        provider = receipt.get("provider_identity")
        provider_ran = any(isinstance(item, Mapping)
                           and item.get("check") == "provider_bond_pad_edge"
                           for item in receipt.get("checks", []))
        if provider is not None or provider_ran or receipt.get("component_status") == "PASS":
            target_path = project / PROVIDER_TARGET
            current_provider = _provider_identity(project, {"profile": {
                "path": str(target_path.resolve()), "sha256": _sha(target_path)}})
            if not isinstance(provider, Mapping) or provider != current_provider:
                raise D75Refusal("D75_PROVIDER_IDENTITY_MISMATCH", str(PROVIDER_TARGET))


def execute(project: Path, context: Mapping) -> dict:
    project = Path(project).resolve()
    checks: list[dict] = []
    inputs: list[dict] = []
    provider_identity = None
    try:
        _context(context)
        inputs.append(_context_input(project, context))
        decl_path = project / DECLARATION
        own_path = project / OPERATOR_ANSWERS
        inputs.extend(_tree_records(project, Path("input/submission_template")))
        inputs.extend(_tree_records(project, Path("input/docs")))
        inputs.extend((_record(project, decl_path), _record(project, own_path)))
        declaration_result = tapeout_declaration_check.evaluate(project)
        if declaration_result.get("verdict") != "PASS":
            checks.append(_check("tapeout_declaration_check", "FAIL",
                                 declaration_result))
            raise D75Refusal("D75_DECLARATION_CONSUMER_FAILED",
                             declaration_result.get("summary", "declaration refused"),
                             failed=True)
        checks.append(_check("tapeout_declaration_check", "PASS",
                             declaration_result))

        doc = _read(decl_path)
        delivery = declaration.answer(doc, "deliverable")
        if delivery != declaration.DELIVERABLE_DIE:
            raise D75Refusal("D75_DELIVERY_FORM_UNSUPPORTED", str(delivery))
        checks.append(_check("declared_delivery_form", "PASS", "bare die interface",
                             deliverable=delivery))

        features = context.get("features") or {}
        if features.get("package_model_required") is True:
            raise D75Refusal(
                "D75_PACKAGE_MODEL_PATH_UNSUPPORTED",
                "package SI/PI/thermal models and their selected commercial consumers are absent")

        io_path = project / IO_REPORT
        io = _read(io_path)
        inputs.append(_record(project, io_path))
        if io.get("program") != "io_pad_chip_top_gen" or io.get("verdict") != "WROTE":
            raise D75Refusal("D75_PAD_INTERFACE_PRODUCER_INVALID", str(IO_REPORT), failed=True)
        io_lefs = _io_lefs(project, io)
        inputs.extend(_record(project, path) for path in io_lefs)

        def_path = project / PAD_DEF
        inputs.append(_record(project, def_path))
        side = pad_side_constraint_check.check(project, def_path)
        if side.get("verdict") == "FAIL":
            checks.append(_check("pad_side_constraint_check", "FAIL", side))
        elif side.get("verdict") != "PASS":
            checks.append(_check("pad_side_constraint_check", "NOT_MEASURED", side))
        else:
            checks.append(_check("pad_side_constraint_check", "PASS", side))

        tech = _tech_lef(project)
        inputs.append(_record(project, tech))
        rc, pads = pad_bterm_coincidence_check.run(
            def_path.read_text(errors="replace"),
            tech.read_text(errors="replace"), io_lefs)
        if rc != 0 or pads.get("undecided_nets"):
            pad_status = "NOT_MEASURED"
        elif pads.get("not_connected_nets") or not pads.get("connected_nets"):
            pad_status = "FAIL"
        else:
            pad_status = "PASS"
        checks.append(_check("pad_bterm_coincidence_check", pad_status, pads))

        layouts = tapeout_readiness_check.discover_layouts(project)
        if len(layouts) != 1:
            raise D75Refusal("D75_FINAL_GEOMETRY_UNBOUND",
                             f"expected one finished layout, found {len(layouts)}")
        gds = layouts[0]
        inputs.append(_record(project, gds))
        edge = provider_bond_pad_edge.measure(
            project, gds, top=str(io.get("chip_top_module") or ""))
        provider_identity = _provider_identity(project, edge)
        inputs.extend(_release_records(project, edge.get("profile")))
        inputs.extend(_release_records(project, edge.get("opening_layer", {}).get("source")))
        inputs.extend(_release_records(project, edge.get("master_sources")))
        if edge.get("verdict") == "FAIL":
            edge_status = "FAIL"
        elif edge.get("verdict") == "PASS" or (
                edge.get("verdict") == "MANUAL_REVIEW"
                and edge.get("required_for_readiness") is False):
            edge_status = "PASS"
        else:
            edge_status = "NOT_MEASURED"
        checks.append(_check("provider_bond_pad_edge", edge_status, edge))

        l20_path = project / "phase1/generated_docs/L20_DFT_SCAN_TOPOLOGY.json"
        inputs.append(_record(project, l20_path))
        l20 = dft_atpg_coverage_check.l20_dft_applicability(project)
        requires_dft = features.get("requires_dft") is True
        if requires_dft:
            dft = dft_atpg_coverage_check.audit(project)
            status = "PASS" if dft.get("verdict") == "PASS" else (
                "FAIL" if dft.get("verdict") == "FAIL" else "NOT_MEASURED")
            checks.append(_check("dft_atpg_coverage_check", status, dft))
        elif l20.get("asserts_dft") is False:
            checks.append(_check("test_access_applicability", "NOT_APPLICABLE", l20))
        elif l20.get("asserts_dft") is True:
            checks.append(_check("test_access_applicability", "FAIL",
                                 "L20 declares DFT but Flow109 selection does not"))
        else:
            checks.append(_check("test_access_applicability", "NOT_MEASURED", l20))

        if features.get("requires_perc") is True:
            perc = perc_signoff_check.audit(project)
            pstatus = ("PASS" if perc.get("verdict") == "PASS" else
                       "FAIL" if perc.get("verdict") == "FAIL" else
                       "NOT_MEASURED")
            checks.append(_check("perc_signoff_check", pstatus, perc))
            for rel, _ in perc_signoff_check.PROJECTIONS:
                path = project / rel
                if path.is_file():
                    inputs.append(_record(project, path))
            summary = project / "reports/phase3/perc_equivalent.json"
            if summary.is_file():
                inputs.append(_record(project, summary))
        else:
            checks.append(_check("perc_signoff_check", "NOT_APPLICABLE",
                                 "requires_perc is false"))

        checks.append(_check("package_electrical_feedback", "NOT_APPLICABLE",
                             "declared Default route is bare die; package_model_required is false"))
        status = _aggregate(checks)
        reason = "D75_BARE_DIE_INTERFACE_COMPONENT_" + status
    except D75Refusal as exc:
        status = "FAIL" if exc.failed else "NOT_MEASURED"
        reason = exc.code
        checks.append(_check("adapter_refusal", status, exc.detail))

    result = {
        "schema": SCHEMA, "step_id": STEP, "program": "flow109_package_ready",
        "context_sha256": context.get("context_sha256"),
        "component_status": status, "reason": reason, "checks": checks,
        "inputs": inputs, "native_started": bool(checks),
        "provider_identity": provider_identity,
        "consumer_load_identity": operation_sources(),
        "metric_acceptance_status": "UNIMPLEMENTED",
        "flow109_acceptance": "NOT_MEASURED",
        "whole_flow_acceptance": "NOT_MEASURED",
        "delivery_accepted": False,
        "scope": "Default bare-die package/test interface component only",
    }
    validate(project, result)
    return result
