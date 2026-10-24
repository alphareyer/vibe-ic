"""D42 Default routed-candidate checks on the adopted D41 database.

This adapter runs only LibreLane's existing post-route checkers.  It does not
route, repair, extract, or infer connectivity from DEF geometry.  A D42 PASS
therefore means the two mandatory catalog obligations were measured on the
exact adopted D41 ODB.  The selected wire reporter/checker also executes, but
an absent PDK threshold is the native checker's explicit NOT_APPLICABLE skip,
never a measured wire-limit PASS.  D42 is not Flow109 or whole-IC acceptance.
``Checker.TrDRC`` exposes a total routed-DRC count, not
marker subtypes, so D42 conservatively requires the total to be zero for its
short/overlap obligation. ``Checker.DisconnectedPins`` measures LibreLane's
critical-pin classification, not arbitrary unused outputs.  Wire length uses
the PDK-resolved threshold and never invents a universal limit.
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
from typing import Iterable

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from _atomic_artefact import write_json
from flow109_admission import AdmissionRefusal, require_launch_admission
import librelane_contract as contract
import _pad_ring as pad_ring

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
    "disconnected_required_pins": (
        "LibreLane critical disconnected pins; an output disabled by the "
        "current netlist's Liberty-proven constant control may be classified "
        "inactive only when the native table, LEF, Liberty and pad-producer "
        "record all agree"),
    "wire_length": "PDK threshold checker; not foundry signoff DRC",
}
_HEX = re.compile(r"[0-9a-f]{64}")
_VERILOG_KEYWORDS = {"module", "endmodule", "input", "output", "inout",
                     "wire", "reg", "assign", "supply0", "supply1", "tri"}
_INSTANCE = re.compile(
    r"^[ \t]*(?P<cell>[A-Za-z_][\w$]*)[ \t]+"
    r"(?P<inst>\\\S+|[A-Za-z_][\w$\[\]]*)\s*"
    r"\(\s*(?P<body>(?:\.[A-Za-z_][\w$]*\s*\([^()]*\)\s*,?\s*)+)\)\s*;",
    re.M)
_CONNECTION = re.compile(r"\.([A-Za-z_][\w$]*)\s*\(\s*([^()]*?)\s*\)")


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
    lexical = project / raw.relative_to("/project") if raw.is_absolute() \
        and raw.is_relative_to("/project") else raw
    lexical = project / lexical if not lexical.is_absolute() else lexical
    # Resolve only after rejecting every lexical symlink component.  Checking
    # is_symlink() on the resolved target accepts a same-project alias and
    # loses the producer's file identity.
    current = lexical
    while True:
        if current.is_symlink():
            raise AdmissionRefusal("D42_SUBJECT_SYMLINK", f"{label}: {lexical}")
        if current == project or current.parent == current:
            break
        current = current.parent
    path = lexical.resolve()
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
             HERE / "librelane_contract.py", HERE / "_pad_ring.py"]
    result = {str(path.relative_to(HERE.parent)): _sha(path) for path in paths}
    # Bind the installed implementations that interpret the metrics and write
    # the disconnected/wire reports, not merely the Python executable.
    from librelane.steps.checker import TrDRC
    from librelane.steps.odb import ReportDisconnectedPins
    for key, cls in (("checker", TrDRC), ("odb", ReportDisconnectedPins)):
        native = Path(inspect.getsourcefile(cls))
        result[f"native/librelane/steps/{key}.py"] = _sha(native)
    return result


def _identifier(value: str) -> str:
    value = (value or "").strip()
    return value[1:].strip() if value.startswith("\\") else value


def _netlist_instances(text: str) -> dict[str, tuple[str, dict[str, str]]]:
    """Named structural instances only; ambiguity is refused by the caller."""
    result = {}
    for match in _INSTANCE.finditer(text or ""):
        master = match.group("cell")
        if master in _VERILOG_KEYWORDS:
            continue
        instance = _identifier(match.group("inst"))
        pins = {_identifier(pin): _identifier(net)
                for pin, net in _CONNECTION.findall(match.group("body"))}
        if instance in result:
            raise AdmissionRefusal("D42_DISCONNECTED_QUALIFICATION_UNBOUND",
                                   f"duplicate netlist instance {instance}")
        result[instance] = (master, pins)
    return result


def _config_paths(config: dict, key: str) -> list[str]:
    raw = config.get(key)
    values: Iterable[object]
    if isinstance(raw, dict):
        values = [item for group in raw.values()
                  for item in (group if isinstance(group, list) else [group])]
    elif isinstance(raw, list):
        values = raw
    else:
        values = []
    return sorted(set(str(item) for item in values if isinstance(item, str) and item))


def _pdk_file(pdk_root: Path, raw: str, pdk: str) -> Path:
    """Map a resolved config path back into the admitted PDK root."""
    parts = Path(raw).parts
    try:
        suffix = parts[parts.index(pdk):]
    except ValueError as exc:
        raise AdmissionRefusal("D42_DISCONNECTED_QUALIFICATION_UNBOUND",
                               f"PDK path does not name {pdk}: {raw}") from exc
    lexical = pdk_root.joinpath(*suffix)
    current = lexical
    while True:
        if current.is_symlink():
            raise AdmissionRefusal("D42_DISCONNECTED_QUALIFICATION_UNBOUND",
                                   f"symlinked PDK authority: {lexical}")
        if current == pdk_root or current.parent == current:
            break
        current = current.parent
    path = lexical.resolve()
    if not path.is_relative_to(pdk_root) or not path.is_file():
        raise AdmissionRefusal("D42_DISCONNECTED_QUALIFICATION_UNBOUND",
                               f"missing PDK authority: {path}")
    return path


def _merge_pin_views(paths: list[Path], parser, label: str) -> tuple[dict, dict[str, str]]:
    """Every corner/view must agree; a first-file-wins merge is not authority."""
    merged = {}
    hashes = {}
    for path in paths:
        hashes[str(path)] = _sha(path)
        for master, pins in parser(path.read_text(errors="replace")).items():
            if master in merged and merged[master] != pins:
                raise AdmissionRefusal("D42_DISCONNECTED_QUALIFICATION_UNBOUND",
                                       f"{label} views disagree for {master}")
            merged[master] = pins
    return merged, hashes


def _disconnected_endpoints(table: Path) -> list[tuple[str, str]]:
    endpoints = []
    for line in table.read_text(errors="replace").splitlines():
        if not line.startswith("│"):
            continue
        columns = [part.strip() for part in line.split("│")[1:-1]]
        if len(columns) != 5 or not columns[4]:
            continue
        endpoint = columns[4]
        if "/" not in endpoint:
            raise AdmissionRefusal("D42_DISCONNECTED_REPORT_INVALID", endpoint)
        instance, pin = endpoint.rsplit("/", 1)
        endpoints.append((instance, pin))
    if len(endpoints) != len(set(endpoints)):
        raise AdmissionRefusal("D42_DISCONNECTED_REPORT_INVALID",
                               "duplicate disconnected endpoint")
    return endpoints


def _constant_driver(net: str, instances: dict, liberty: dict) -> dict | None:
    drivers = []
    for instance, (master, pins) in instances.items():
        model = liberty.get(master, {})
        for pin, connected in pins.items():
            rec = model.get(pin, {})
            function = str(rec.get("function") or "").strip()
            if connected == net and rec.get("direction") == "output" \
                    and function in ("0", "1"):
                drivers.append({"instance": instance, "master": master,
                                "pin": pin, "level": int(function)})
    return drivers[0] if len(drivers) == 1 else None


def _producer_snapshot(project: Path, record_path: Path) -> dict:
    """Bind the live producer report to its newest immutable step capture."""
    captures = sorted((project / "steps/phase3/stage3").glob(
        "155ic_pad_ring_chip_ic_path_only_*/outputs.json"), reverse=True)
    if not captures:
        raise AdmissionRefusal("D42_DISCONNECTED_QUALIFICATION_UNBOUND",
                               "IO-pad producer capture is absent")
    manifest_path = _inside(project, str(captures[0]), "IO-pad producer capture")
    manifest = _read(manifest_path)
    rows = [row for row in manifest.get("outputs", []) if isinstance(row, dict)
            and row.get("rel") == "reports/phase3/io_pad_chip_top.json"]
    if len(rows) != 1 or not _HEX.fullmatch(str(rows[0].get("sha256") or "")):
        raise AdmissionRefusal("D42_DISCONNECTED_QUALIFICATION_UNBOUND",
                               "newest IO-pad capture does not bind the producer report")
    snapshot = _inside(project, str(manifest_path.parent / str(rows[0].get("snapshot") or "")),
                       "IO-pad producer snapshot")
    digest = _sha(record_path)
    if rows[0]["sha256"] != digest or _sha(snapshot) != digest:
        raise AdmissionRefusal("D42_DISCONNECTED_QUALIFICATION_UNBOUND",
                               "live IO-pad report differs from newest captured producer output")
    return {"manifest": _ref(project, manifest_path),
            "snapshot": _ref(project, snapshot)}


def _qualify_inactive_outputs(project: Path, table: Path, prepared: dict,
                              native_disconnected: int,
                              native_critical: int) -> dict:
    """Prove that every native disconnect is a disabled receiver output.

    This does not alter the native metric.  It produces a second, explicitly
    qualified requirement count, and only when all current authorities agree.
    Mixed or incomplete populations retain the full native critical count.
    """
    result = {"status": "UNQUALIFIED", "native_disconnected": native_disconnected,
              "native_critical": native_critical, "qualified": [],
              "required_disconnected_pins": native_critical}
    if native_critical == 0:
        result.update(status="NOT_NEEDED", required_disconnected_pins=0)
        return result
    try:
        endpoints = _disconnected_endpoints(table)
        if native_disconnected != native_critical or len(endpoints) != native_disconnected:
            raise AdmissionRefusal(
                "D42_DISCONNECTED_QUALIFICATION_UNBOUND",
                "qualification requires the native full and critical populations to match")
        record_path = _inside(project, "reports/phase3/io_pad_chip_top.json",
                              "current IO-pad producer record")
        record = _read(record_path)
        producer_capture = _producer_snapshot(project, record_path)
        generated = _inside(project, record.get("chip_top_verilog"),
                            "current IO-pad producer output")
        if record.get("program") != "io_pad_chip_top_gen" or record.get("verdict") != "WROTE":
            raise AdmissionRefusal("D42_DISCONNECTED_QUALIFICATION_UNBOUND",
                                   "IO-pad producer record is not WROTE")
        pad_instances = record.get("pad_instances")
        aux = record.get("aux_pin_signal_connections")
        if not isinstance(pad_instances, dict) or not isinstance(aux, list):
            raise AdmissionRefusal("D42_DISCONNECTED_QUALIFICATION_UNBOUND",
                                   "IO-pad producer authority is incomplete")
        aux_by_endpoint = {(item.get("instance"), item.get("pin")): item
                           for item in aux if isinstance(item, dict)}
        instances = _netlist_instances(prepared["views"]["nl"].read_text(errors="replace"))
        pdk = str(prepared["config"].get("PDK") or "")
        pdk_root = prepared["pdk_root"]
        pad_lib_paths = [_pdk_file(pdk_root, raw, pdk)
                         for raw in _config_paths(prepared["config"], "PAD_LIBS")]
        cell_lib_paths = [_pdk_file(pdk_root, raw, pdk)
                          for raw in _config_paths(prepared["config"], "CELL_LIBS")]
        lef_paths = [_pdk_file(pdk_root, raw, pdk)
                     for raw in _config_paths(prepared["config"], "PAD_LEFS")]
        if not pad_lib_paths or not cell_lib_paths or not lef_paths:
            raise AdmissionRefusal("D42_DISCONNECTED_QUALIFICATION_UNBOUND",
                                   "PAD_LIBS, CELL_LIBS and PAD_LEFS are required")
        pad_lib, pad_hashes = _merge_pin_views(
            pad_lib_paths, pad_ring.parse_liberty_pad_cells, "pad Liberty")
        cell_lib, cell_hashes = _merge_pin_views(
            cell_lib_paths, pad_ring.parse_liberty_pad_cells, "cell Liberty")
        lef, lef_hashes = _merge_pin_views(
            lef_paths, pad_ring.parse_lef_pin_roles, "pad LEF")
        qualified = []
        for instance, pin in endpoints:
            if instance not in instances or instance not in pad_instances:
                raise AdmissionRefusal("D42_DISCONNECTED_QUALIFICATION_UNBOUND",
                                       f"unowned disconnected endpoint {instance}/{pin}")
            master, pins = instances[instance]
            producer = pad_instances[instance]
            if (producer.get("master") != master or producer.get("direction") != "output"
                    or pin in pins):
                raise AdmissionRefusal("D42_DISCONNECTED_QUALIFICATION_UNBOUND",
                                       f"current netlist/producer mismatch at {instance}/{pin}")
            lib_pin = pad_lib.get(master, {}).get(pin, {})
            if lib_pin.get("direction") != "output" or not lib_pin.get("function"):
                raise AdmissionRefusal("D42_DISCONNECTED_QUALIFICATION_UNBOUND",
                                       f"Liberty does not define output {master}/{pin}")
            if lef.get(master, {}).get(pin) != ("OUTPUT", "SIGNAL"):
                raise AdmissionRefusal("D42_DISCONNECTED_QUALIFICATION_UNBOUND",
                                       f"LEF does not define OUTPUT SIGNAL {master}/{pin}")
            literals = pad_ring._product_literals(lib_pin.get("function"))
            if not literals:
                raise AdmissionRefusal("D42_DISCONNECTED_QUALIFICATION_UNBOUND",
                                       f"unreadable output function for {master}/{pin}")
            disabling = []
            for negated, control in literals:
                net = pins.get(control)
                if not net:
                    continue
                driver = _constant_driver(net, instances, cell_lib)
                if driver is None:
                    continue
                literal_value = ((not bool(driver["level"])) if negated
                                 else bool(driver["level"]))
                authority = aux_by_endpoint.get((instance, control), {})
                if (literal_value is False and authority.get("net") == net
                        and authority.get("level") == driver["level"]
                        and authority.get("tie_instance") == driver["instance"]
                        and authority.get("tie_master") == driver["master"]
                        and authority.get("tie_pin") == driver["pin"]):
                    disabling.append({"control_pin": control, "net": net,
                                      "negated": negated, "driver": driver})
            if not disabling:
                raise AdmissionRefusal("D42_DISCONNECTED_QUALIFICATION_UNBOUND",
                                       f"no current constant disables {instance}/{pin}")
            qualified.append({"endpoint": f"{instance}/{pin}", "master": master,
                              "function": lib_pin["function"],
                              "disabling_controls": disabling})
        result.update(
            status="QUALIFIED_INACTIVE_OUTPUTS", qualified=qualified,
            required_disconnected_pins=0,
            authority={
                "producer_record": _ref(project, record_path),
                "producer_capture": producer_capture,
                "producer_output": _ref(project, generated),
                "adopted_netlist": _ref(project, prepared["views"]["nl"]),
                "adopted_odb": _ref(project, prepared["views"]["odb"]),
                "pad_liberty_sha256": pad_hashes,
                "cell_liberty_sha256": cell_hashes,
                "pad_lef_sha256": lef_hashes,
                "native_table": _ref(project, table)})
    except AdmissionRefusal as exc:
        result.update(reason=exc.code, detail=str(exc))
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
    reported_disconnected = _count(states["Odb.ReportDisconnectedPins"].get(
        "metrics", {}).get("design__disconnected_pin__count"),
        "design__disconnected_pin__count")
    reported_critical = _count(states["Odb.ReportDisconnectedPins"].get(
        "metrics", {}).get(METRICS["disconnected_required_pins"]),
        "reporter design__critical_disconnected_pin__count")
    if reported_critical != disconnected:
        raise AdmissionRefusal("D42_NATIVE_METRIC_DISAGREEMENT",
                               f"reporter={reported_critical}, checker={disconnected}")
    qualification = _qualify_inactive_outputs(
        project, table, prepared, reported_disconnected, disconnected)
    required_disconnected = _count(
        qualification["required_disconnected_pins"], "qualified disconnected count")
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
                    "native_disconnected_pins": reported_disconnected,
                    "native_critical_disconnected_pins": disconnected,
                    "qualified_inactive_output_pins": len(qualification["qualified"]),
                    "disconnected_required_pins": required_disconnected,
                    "wire_length_max_um": str(wire_max),
                    "wire_length_threshold_um": str(threshold) if threshold is not None else None}
    failed = []
    if route:
        failed.append("route_check.route_short_overlap_errors")
    if required_disconnected:
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
                "disconnected_pin_qualification": qualification,
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
        if (prior.get("schema") != SCHEMA
                or prior.get("reason") not in ("D42_WIRE_THRESHOLD_UNSTATED",
                                               "D42_NATIVE_CHECK_FAILED")
                or prior.get("native_started") is not True or prior.get("inputs") != prepared["inputs"]
                or prior.get("image") != prepared["image"]
                or prior.get("adopted_candidate") != prepared["candidate"]):
            continue
        recorded = prior.get("outputs")
        if isinstance(recorded, dict) and all(
                isinstance(recorded.get(step), dict)
                and recorded[step].get("folder") for step in STEPS):
            folders = [project / recorded[step]["folder"] for step in STEPS]
        else:
            lane = project / "phase3/librelane" / (
                "D42-route-check-" + receipt_path.parent.name[:12])
            folders = [lane / f"{index:02d}-{step.lower().replace('.', '-')}"
                       for index, step in enumerate(STEPS, 1)]
        for step, folder in zip(STEPS, folders):
            receipt = contract.validate_step_receipt(folder, step)
            recorded_step = recorded.get(step, {}) if isinstance(recorded, dict) else {}
            if recorded_step and (recorded_step.get("receipt_sha256") != _sha(
                    folder / "vibeic_receipt.json")
                    or recorded_step.get("state_sha256") != _sha(folder / "state_out.json")):
                raise AdmissionRefusal("D42_RETAINED_NATIVE_STALE", str(folder))
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
