"""D42 Default routed-candidate checks on the adopted D41 database.

This adapter runs only LibreLane's existing post-route checkers.  It does not
route, repair, extract, or infer connectivity from DEF geometry.  A D42 PASS
therefore means the two catalog obligations and the selected wire-length
checker were measured on the exact adopted D41 ODB; it is not Flow109 or
whole-IC acceptance.  ``Checker.TrDRC`` exposes a total routed-DRC count, not
marker subtypes, so D42 conservatively requires the total to be zero for its
short/overlap obligation. ``Checker.DisconnectedPins`` measures LibreLane's
critical-pin classification, not arbitrary unused outputs.  Wire length uses
the PDK-resolved threshold and is NOT_MEASURED when that limit is absent.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import sys
import uuid

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from _atomic_artefact import write_json
from flow109_admission import AdmissionRefusal, require_launch_admission
import librelane_contract as contract

SCHEMA = "vibeic.flow109.route-check.v1"
EDA_IMAGE = ("ghcr.io/vibeic/vibeic-eda@sha256:"
             "0fc30ecd1307a11bd7757d1c5710127cd88e245059f29eab8b52e38ba4d60406")
STEPS = ["Checker.TrDRC", "Odb.ReportDisconnectedPins",
         "Checker.DisconnectedPins", "Odb.ReportWireLength",
         "Checker.WireLength"]
METRICS = {"route_short_overlap_errors": "route__drc_errors",
           "disconnected_required_pins": "design__critical_disconnected_pin__count"}
CAPABILITY_LIMITS = {
    "route_short_overlap_errors": "LibreLane exposes total routed DRC; zero is conservative",
    "disconnected_required_pins": "LibreLane critical disconnected pins only",
    "wire_length": "PDK threshold checker; not foundry signoff DRC",
}
_HEX = re.compile(r"[0-9a-f]{64}")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise AdmissionRefusal("D42_EVIDENCE_ABSENT", str(path)) from exc
    if not isinstance(value, dict):
        raise AdmissionRefusal("D42_EVIDENCE_INVALID", str(path))
    return value


def _project_path(project: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise AdmissionRefusal("D42_SUBJECT_UNBOUND", label)
    raw = Path(value)
    # Native receipts use the canonical /project mount.  Translate only that
    # exact namespace; arbitrary absolute paths never become project inputs.
    path = project / raw.relative_to("/project") if raw.is_absolute() \
        and raw.is_relative_to("/project") else raw
    path = (project / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_relative_to(project):
        raise AdmissionRefusal("D42_SUBJECT_MISSING", f"{label}: {path}")
    return path


def _inside(project: Path, value: object, label: str) -> Path:
    path = _project_path(project, value, label)
    if path.is_symlink() or not path.is_file():
        raise AdmissionRefusal("D42_SUBJECT_MISSING", f"{label}: {path}")
    return path


def _ref(project: Path, path: Path) -> dict[str, str]:
    return {"path": str(path.resolve().relative_to(project)), "sha256": _sha(path)}


def operation_sources() -> dict[str, str]:
    paths = [Path(__file__), HERE / "flow109.py", HERE / "flow109_admission.py",
             HERE / "librelane_contract.py"]
    result = {str(path.relative_to(HERE.parent)): _sha(path) for path in paths}
    # Bind the installed implementations that interpret the metrics and write
    # the disconnected/wire reports, not merely the Python executable.
    from librelane.steps.checker import TrDRC
    from librelane.steps.odb import ReportDisconnectedPins
    for key, cls in (("checker", TrDRC), ("odb", ReportDisconnectedPins)):
        native = Path(inspect.getsourcefile(cls))
        result[f"native/librelane/steps/{key}.py"] = _sha(native)
    return result


def _adopted_subject(project: Path) -> dict:
    backend_path = project / "reports/phase3/default_steps/32/backend_result.json"
    backend = _read(backend_path)
    if (backend.get("producer_verdict") != "PASS"
            or backend.get("gates") != {"backend_canonical": "PASS",
                                        "backend_native_substance": "PASS"}):
        raise AdmissionRefusal("D42_D41_NOT_ACCEPTED", str(backend_path))

    report_path = project / "reports/phase3/librelane_postroute_repair.json"
    handoff_path = project / "reports/phase3/librelane_postroute_repair_handoff.json"
    report, handoff = _read(report_path), _read(handoff_path)
    candidate = report.get("adopted")
    if not isinstance(candidate, str) or not re.fullmatch(r"32-cand[0-9]+", candidate):
        raise AdmissionRefusal("D42_D41_NOT_ACCEPTED", "adopted candidate absent")
    state_path = _inside(project, handoff.get("state"), "D41 handoff state")
    if (handoff.get("state_sha256") != _sha(state_path)
            or candidate not in state_path.parts):
        raise AdmissionRefusal("D42_D41_HANDOFF_STALE", str(state_path))
    receipt = contract.validate_step_receipt(state_path.parent, "Vibeic.PostRouteRepair")
    state = _read(state_path)
    views = {}
    for name in ("def", "odb", "nl", "pnl", "sdc"):
        path = _inside(project, state.get(name), f"D41 {name}")
        try:
            output_name = str(path.relative_to(state_path.parent))
        except ValueError:
            output_name = ""
        expected = receipt.get("sha256", {}).get(output_name)
        if expected is None:
            state_files = receipt.get("input", {}).get("state_files", {})
            matches = [sha for raw, sha in state_files.items()
                       if _project_path(project, raw, f"D41 recorded {name}") == path]
            expected = matches[0] if len(matches) == 1 else None
        if not _HEX.fullmatch(str(expected or "")) or _sha(path) != expected:
            raise AdmissionRefusal("D42_D41_VIEW_STALE", f"{name}: {path}")
        views[name] = path
    for name in ("def", "nl"):
        bound = handoff.get("views", {}).get(name, {})
        source = _inside(project, bound.get("source"), f"D41 {name} source")
        dest = _inside(project, bound.get("dest"), f"D41 {name} adopted destination")
        digest = _sha(source)
        if (source != views[name] or digest != _sha(dest)
                or bound.get("source_sha256") != digest
                or bound.get("dest_sha256") != digest):
            raise AdmissionRefusal("D42_D41_HANDOFF_STALE", name)
    config_path = state_path.parent / "config.json"
    config = _read(config_path)
    if config.get("PDK") != "gf180mcuD" or not config.get("STD_CELL_LIBRARY"):
        raise AdmissionRefusal("D42_PDK_UNSUPPORTED", str(config.get("PDK")))
    inputs = {"d41_backend": _ref(project, backend_path),
              "d41_report": _ref(project, report_path),
              "d41_handoff": _ref(project, handoff_path),
              "d41_state": _ref(project, state_path),
              "d41_receipt": _ref(project, state_path.parent / "vibeic_receipt.json"),
              "d41_config": _ref(project, config_path)}
    inputs.update({f"view_{name}": _ref(project, path) for name, path in views.items()})
    return {"candidate": candidate, "state": state, "state_path": state_path,
            "views": views, "config": config, "inputs": inputs}


def prepare(project: Path, context: dict) -> dict:
    project = Path(project).resolve()
    if context.get("requested_step") != "D42" or context.get("native_step_id") != "flow109.D42":
        raise AdmissionRefusal("D42_CONTEXT_MISMATCH", "D42 Default component only")
    image = os.environ.get("VIBEIC_EDA_IMAGE", EDA_IMAGE)
    if image != EDA_IMAGE:
        raise AdmissionRefusal("D42_IMAGE_MISMATCH", image)
    pdk_root_value = os.environ.get("VIBEIC_LIBRELANE_PDK_ROOT")
    if not pdk_root_value:
        raise AdmissionRefusal("D42_PDK_ROOT_UNSTATED", "VIBEIC_LIBRELANE_PDK_ROOT")
    pdk_root = Path(pdk_root_value).resolve()
    if not (pdk_root / "gf180mcuD").is_dir():
        raise AdmissionRefusal("D42_PDK_ROOT_MISSING", str(pdk_root))
    sources = operation_sources()
    uses = context.get("runtime_tool_uses")
    if uses is not None:
        if (not isinstance(uses, list) or any(
                use.get("semantics", {}).get("operation_sources") != sources for use in uses)):
            raise AdmissionRefusal("D42_OPERATION_SOURCE_CHANGED", "runtime inventory")
    subject = _adopted_subject(project)
    return {**subject, "image": image, "pdk_root": pdk_root,
            "sources": sources, "context_sha256": context.get("context_sha256")}


def _before(project: Path, context: dict, prepared: dict) -> None:
    require_launch_admission(project, "flow109.D42", {"flow109_context": context})
    if operation_sources() != prepared["sources"]:
        raise AdmissionRefusal("D42_OPERATION_SOURCE_CHANGED", "before native launch")
    current = _adopted_subject(project)
    if current["inputs"] != prepared["inputs"]:
        raise AdmissionRefusal("D42_INPUT_CHANGED", "adopted D41 subject changed")


def _count(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AdmissionRefusal("D42_METRIC_INVALID", label)
    return value


def _decimal(value: object, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise AdmissionRefusal("D42_METRIC_INVALID", label) from exc
    if not result.is_finite() or result < 0:
        raise AdmissionRefusal("D42_METRIC_INVALID", label)
    return result


def _consume(project: Path, folders: list[Path], configs: dict[str, Path],
             prepared: dict) -> dict:
    if len(folders) != len(STEPS):
        raise AdmissionRefusal("D42_NATIVE_OUTPUT_INCOMPLETE", str(len(folders)))
    receipts = {}
    states = {}
    for step, folder in zip(STEPS, folders):
        receipts[step] = contract.validate_step_receipt(folder, step)
        states[step] = _read(folder / "state_out.json")
    table = folders[1] / "full_disconnected_pins_table.txt"
    wires = folders[3] / "wire_lengths.csv"
    if not table.is_file() or not table.read_text(errors="replace").strip():
        raise AdmissionRefusal("D42_DISCONNECTED_REPORT_EMPTY", str(table))
    if not wires.is_file() or not wires.read_text(errors="replace").strip():
        raise AdmissionRefusal("D42_WIRE_REPORT_EMPTY", str(wires))
    route = _count(states["Checker.TrDRC"].get("metrics", {}).get(METRICS[
        "route_short_overlap_errors"]), "route__drc_errors")
    disconnected = _count(states["Checker.DisconnectedPins"].get("metrics", {}).get(METRICS[
        "disconnected_required_pins"]), "design__critical_disconnected_pin__count")
    wire_max = _decimal(states["Checker.WireLength"].get("metrics", {}).get(
        "route__wirelength__max"), "route__wirelength__max")
    wire_config = _read(configs["Checker.WireLength"])
    threshold_raw = wire_config.get("WIRE_LENGTH_THRESHOLD")
    if threshold_raw is None:
        invocation = folders[4] / "invocation.log"
        invocation_text = " ".join(invocation.read_text(errors="replace").split())
        if ("Threshold for Threshold-surpassing long wires" not in invocation_text
                or "is not set. The checker will be skipped." not in invocation_text):
            raise AdmissionRefusal("D42_WIRE_CHECK_STATUS_UNBOUND", str(invocation))
        threshold = None
        wire_check = {"status": "NOT_APPLICABLE",
                      "reason": "PDK-resolved WIRE_LENGTH_THRESHOLD is unset; native checker skipped"}
    else:
        threshold = _decimal(threshold_raw, "WIRE_LENGTH_THRESHOLD")
        wire_check = {"status": "FAIL" if wire_max > threshold else "PASS",
                      "threshold_um": str(threshold)}
    measurements = {"route_short_overlap_errors": route,
                    "disconnected_required_pins": disconnected,
                    "wire_length_max_um": str(wire_max),
                    "wire_length_threshold_um": str(threshold) if threshold is not None else None}
    failed = []
    if route:
        failed.append("route_check.route_short_overlap_errors")
    if disconnected:
        failed.append("route_check.disconnected_required_pins")
    if threshold is not None and wire_max > threshold:
        failed.append("route_check.wire_length_threshold")
    outputs = {}
    for step, folder in zip(STEPS, folders):
        outputs[step] = {"folder": str(folder.relative_to(project)),
                         "receipt_sha256": _sha(folder / "vibeic_receipt.json"),
                         "state_sha256": _sha(folder / "state_out.json")}
    outputs["Odb.ReportDisconnectedPins"]["report_sha256"] = _sha(table)
    outputs["Odb.ReportWireLength"]["report_sha256"] = _sha(wires)
    identity = {"producer": "LibreLane native route-check chain",
                "checker": {"steps": STEPS, "metric_keys": METRICS},
                "tool_execution": outputs,
                "consumed_inputs": prepared["inputs"],
                "requirements": {"route_check.disconnected_required_pins": 0,
                                 "route_check.route_short_overlap_errors": 0},
                "configuration": {step: _sha(path) for step, path in configs.items()},
                "source": prepared["sources"]}
    return {"status": "FAIL" if failed else "PASS",
            "reason": "D42_NATIVE_CHECK_FAILED" if failed else "D42_NATIVE_CHECKS_PASS",
            "failed_obligations": failed, "measurements": measurements,
            "wire_length_check": wire_check, "identity": identity, "outputs": outputs}


def _retained_native(project: Path, prepared: dict) -> tuple[list[Path], dict[str, Path], dict] | None:
    """Reuse one completed checker chain after a consumer-only source repair."""
    root = project / "reports/flow109/D42"
    for receipt_path in sorted(root.glob("*/receipt.json"), reverse=True):
        prior = _read(receipt_path)
        if (prior.get("schema") != SCHEMA or prior.get("reason") != "D42_WIRE_THRESHOLD_UNSTATED"
                or prior.get("native_started") is not True or prior.get("inputs") != prepared["inputs"]
                or prior.get("image") != prepared["image"]
                or prior.get("adopted_candidate") != prepared["candidate"]):
            continue
        lane = project / "phase3/librelane" / ("D42-route-check-" + receipt_path.parent.name[:12])
        folders = [lane / f"{index:02d}-{step.lower().replace('.', '-')}"
                   for index, step in enumerate(STEPS, 1)]
        for step, folder in zip(STEPS, folders):
            contract.validate_step_receipt(folder, step)
        fingerprint = _read(folders[0] / "input_fingerprint.json")
        actual = {_project_path(project, raw, "retained D42 input"): sha
                  for raw, sha in fingerprint.get("state_files", {}).items()}
        expected = {path: prepared["inputs"]["view_" + name]["sha256"]
                    for name, path in prepared["views"].items()}
        if fingerprint.get("image") != prepared["image"] or actual != expected:
            raise AdmissionRefusal("D42_RETAINED_NATIVE_STALE", str(lane))
        configs = {step: folder / "config.json" for step, folder in zip(STEPS, folders)}
        return folders, configs, _ref(project, receipt_path)
    return None


def execute(project, context):
    project = Path(project).resolve()
    attempt = project / "reports/flow109/D42" / uuid.uuid4().hex
    attempt.mkdir(parents=True, exist_ok=False)
    result = {"schema": SCHEMA, "step_id": "D42", "native_step_id": "flow109.D42",
              "component_status": "NOT_MEASURED", "metric_acceptance_status": "NOT_MEASURED",
              "flow109_acceptance": "NOT_MEASURED", "whole_flow_acceptance": "NOT_MEASURED",
              "native_started": False, "context_sha256": context.get("context_sha256"),
              "capability_limits": CAPABILITY_LIMITS}
    try:
        prepared = prepare(project, context)
        result.update(inputs=prepared["inputs"], operation_sources=prepared["sources"],
                      adopted_candidate=prepared["candidate"], image=prepared["image"])
        _before(project, context, prepared)
        retained = _retained_native(project, prepared)
        if retained is not None:
            folders, configs, retained_receipt = retained
            result.update(native_reused=True, retained_native_receipt=retained_receipt)
        else:
            overlay = {"ERROR_ON_TR_DRC": (False, "D42 consumer records native metric"),
                       "ERROR_ON_DISCONNECTED_PINS": (False, "D42 consumer records native metric"),
                       "ERROR_ON_LONG_WIRE": (False, "D42 consumer records native metric")}
            configs = contract.resolve_step_configs(
                project, prepared["image"], "gf180mcuD", STEPS,
                pdk_root=prepared["pdk_root"], folder="D42-config-" + attempt.name[:12],
                overlay=overlay)
            bridge = attempt / "adopted_state.json"
            state = dict(prepared["state"])
            state.update({name: str(path) for name, path in prepared["views"].items()})
            write_json(bridge, state)
            _before(project, context, prepared)
            result["native_started"] = True
            lane = "D42-route-check-" + attempt.name[:12]
            folders = contract.run_chain(
                project, prepared["image"], [(step, configs[step], bridge) for step in STEPS],
                mounts=[(prepared["pdk_root"], "/pdk")], lane=lane, pdk_root="/pdk")
        consumed = _consume(project, folders, configs, prepared)
        result.update(component_status=consumed["status"],
                      metric_acceptance_status=consumed["status"],
                      reason=consumed["reason"],
                      failed_obligations=consumed["failed_obligations"],
                      measurements=consumed["measurements"],
                      wire_length_check=consumed["wire_length_check"],
                      identity=consumed["identity"], outputs=consumed["outputs"])
    except AdmissionRefusal as exc:
        result.update(component_status="FAIL" if exc.status == "FAIL" else "NOT_MEASURED",
                      reason=exc.code, detail=str(exc))
    except contract.Refusal as exc:
        result.update(reason=exc.code, detail=str(exc))
    except (OSError, ValueError, TypeError, KeyError, ImportError) as exc:
        result.update(reason="D42_EXECUTION_INCOMPLETE", detail=f"{type(exc).__name__}: {exc}")
    write_json(attempt / "receipt.json", result)
    result["receipt"] = _ref(project, attempt / "receipt.json")
    return result
