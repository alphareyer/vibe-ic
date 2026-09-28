#!/usr/bin/env python3
"""BLOCKING post-route DRV(tran/cap/fanout) sign-off judge.

The receipt is a frozen, content-addressed measurement bundle.  Missing
instrument evidence is NOT_MEASURED; a measured rule breach is FAIL.  This
program never creates a waiver.  WAIVED is a disclosed deviation, never PASS.
Noise/SI, EM and max_length remain separately NOT_MEASURED.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _atomic_artefact import write_text  # noqa: E402

KINDS = ("max_slew", "max_capacitance", "max_fanout")
_TITLES = {"max slew": KINDS[0], "max capacitance": KINDS[1],
           "max fanout": KINDS[2]}
_VALUES = {"max_slew": ("max slew", "slew"),
           "max_capacitance": ("max capacitance", "capacitance"),
           "max_fanout": ("max fanout", "fanout")}
_NUM = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?"
_PIN = re.compile(r"^Pin\s+(\S+)(?:\s+([\^v]))?\s*$")
_METRIC = re.compile(rf"^(.+?)\s+({_NUM})(?:\s+\((?:VIOLATED|MET)\))?\s*$")
_COMMAND = "report_check_types -max_slew -max_capacitance -max_fanout -violators -verbose"
_REQUIRED_STAGES = ("synth", "placement_repair", "cts", "post_grt_repair", "signoff_sta")
_FORBIDDEN_SDC = re.compile(r"\b(set_case_analysis|set_disable_timing|set_ideal_network|set_ideal_net)\b")
_SCENE_PROFILES = Path(__file__).resolve().parent / "data/drv_signoff_scene_profiles.json"
_OWNER_SIGNERS = Path(__file__).resolve().parent / "data/drv_owner_allowed_signers"


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def _evidence(item: dict, errors: list[str], label: str) -> str:
    path = Path(str(item.get("path") or ""))
    expected = str(item.get("sha256") or "")
    if not path.is_file() or not re.fullmatch(r"[0-9a-f]{64}", expected):
        errors.append(f"{label}: file or sha256 absent")
        return ""
    actual = _sha(path)
    if actual != expected:
        errors.append(f"{label}: sha256 changed ({expected} -> {actual})")
        return ""
    return path.read_text(errors="replace")


def _sdc_values(body: str, command: str) -> list[float]:
    # Tcl is not a line-oriented format.  A conflicting later assignment must
    # never be hidden by reading only the first matching line.
    return [float(m.group(1)) for m in re.finditer(
        rf"(?m)^\s*{re.escape(command)}\s+({_NUM})(?=\s|$)", body)]


def parse_check_types(body: str, *, scene: str, mode: str,
                      violators_only: bool) -> dict[str, list[dict]]:
    """Parse OpenSTA 3.1 verbose Pin blocks; preserve rise/fall and tool slack."""
    out: dict[str, list[dict]] = {kind: [] for kind in KINDS}
    kind = None
    row: dict[str, Any] | None = None
    for raw in body.splitlines():
        line = raw.strip()
        if line in _TITLES:
            kind = _TITLES[line]
            row = None
            continue
        pin = _PIN.fullmatch(line)
        if pin and kind:
            row = {"pin": pin.group(1), "direction":
                   {"^": "rise", "v": "fall"}.get(pin.group(2), "none"),
                   "scene": scene, "mode": mode}
            out[kind].append(row)
            continue
        if row is None or kind is None:
            continue
        metric = _METRIC.fullmatch(line)
        if not metric:
            continue
        name = metric.group(1).strip().lower()
        limit_name, measured_name = _VALUES[kind]
        if name == limit_name:
            row["limit"] = float(metric.group(2))
        elif name == measured_name:
            row["measured"] = float(metric.group(2))
        elif name == "slack":
            row["slack"] = float(metric.group(2))
            row["violated"] = "(VIOLATED)" in line or row["slack"] < 0
    if violators_only:
        for rows in out.values():
            rows[:] = [r for r in rows if r.get("violated")]
    return out


def _attribute(block: str, key: str) -> float | None:
    hit = re.search(rf"\b{re.escape(key)}\s*:\s*({_NUM})\s*;", block)
    return float(hit.group(1)) if hit else None


def _balanced_block(text: str, start: int) -> str:
    opening = text.find("{", start)
    if opening < 0:
        return ""
    depth = 1
    for index in range(opening + 1, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[opening + 1:index]
    return ""


def _liberty_limits(body: str) -> dict:
    """Take pin attributes before nested timing groups and library defaults."""
    # PDK Liberty writers commonly split a unit declaration with a Tcl-style
    # backslash/newline (the linked gf180 IO library does exactly this).
    body = re.sub(r"\\\s*\n\s*", " ", body)
    time_match = re.search(r'time_unit\s*:\s*"?(' + _NUM +
                           r')\s*(fs|ps|ns|us|ms|s)"?', body, re.I)
    cap_match = re.search(r'capacitive_load_unit\s*\(\s*(' + _NUM +
                          r')\s*,\s*"?(ff|pf|nf|uf)"?\s*\)', body, re.I)
    if not time_match or not cap_match:
        raise ValueError("Liberty time/capacitance units absent")
    time_scale = float(time_match.group(1)) * {
        "fs": 1e-6, "ps": 1e-3, "ns": 1, "us": 1e3,
        "ms": 1e6, "s": 1e9}[time_match.group(2).lower()]
    cap_scale = float(cap_match.group(1)) * {
        "ff": 1e-3, "pf": 1, "nf": 1e3, "uf": 1e6}[cap_match.group(2).lower()]
    scales = {"max_slew": time_scale, "max_capacitance": cap_scale,
              "max_fanout": 1}
    def scaled(block: str, kind: str, prefix: str = "") -> float | None:
        value = _attribute(block, prefix + kind.replace("max_slew", "max_transition"))
        return value * scales[kind] if value is not None else None
    defaults = {kind: scaled(body[:body.find("cell (")], kind, "default_")
                for kind in KINDS}
    cells: dict[str, dict] = {}
    pad_cells: set[str] = set()
    for cell in re.finditer(r'\bcell\s*\(\s*"?([^"\)]+)"?\s*\)\s*\{', body):
        cell_body = _balanced_block(body, cell.start())
        if re.search(r"\bpad_cell\s*:\s*true\s*;", cell_body, re.I):
            pad_cells.add(cell.group(1).strip())
        pins = {}
        for pin in re.finditer(r'\bpin\s*\(\s*"?([^"\)]+)"?\s*\)\s*\{', cell_body):
            pin_body = _balanced_block(cell_body, pin.start())
            # Pin-level attributes occur before nested timing/power tables.
            direct = pin_body.split("timing (")[0].split("internal_power (")[0]
            pins[pin.group(1).strip()] = {
                kind: scaled(direct, kind)
                for kind in KINDS}
        cells[cell.group(1).strip()] = pins
    return {"defaults": defaults, "cells": cells, "pad_cells": pad_cells}


def _liberty_header(body: str) -> dict[str, float | None]:
    head = body[:body.find("cell (")]
    return {key: _attribute(head, key) for key in
            ("nom_process", "nom_voltage", "nom_temperature")}


def _key(kind: str, row: dict) -> tuple:
    if kind == "max_slew":
        return row["pin"], row["scene"], row["direction"]
    if kind == "max_capacitance":
        return row["pin"], row["scene"]
    return row["pin"], row["mode"]


def _positive_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _integrator_value(body: str, *keys: str) -> float | None:
    for key in keys:
        matches = re.findall(rf"(?m)^\s*set\s+::env\({re.escape(key)}\)\s+({_NUM})(?=\s|$)", body)
        if matches:
            return float(matches[-1])
    return None


def _installed_image_digest(image: str) -> str | None:
    """Read the actual local image identity; a bundle cannot assert its own pin."""
    try:
        process = subprocess.run(
            ["docker", "image", "inspect", image, "--format", "{{json .}}"],
            capture_output=True, text=True, check=False, timeout=15)
        if process.returncode:
            return None
        value = json.loads(process.stdout).get("Id")
        return value if re.fullmatch(r"sha256:[0-9a-f]{64}", str(value)) else None
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        return None


def _script_reads(body: str) -> list[tuple[str, str]]:
    return re.findall(
        r"(?m)^\s*(read_liberty|read_verilog|read_sdc|read_spef)\s+\{([^{}]+)\}\s*$",
        body)


def _owner_records(project: Path | None, identity: dict, current: dict,
                   frozen: dict) -> tuple[list[dict], str]:
    """Read only independently signed owner records, never the capture plan."""
    try:
        signers = _OWNER_SIGNERS.read_text()
    except OSError:
        return [], "OWNER_SIGNATURE_UNAVAILABLE"
    if not any(line.strip().startswith("owner ") for line in signers.splitlines()):
        return [], "OWNER_SIGNATURE_UNAVAILABLE"
    if project is None:
        return [], "OWNER_PROJECT_UNBOUND"
    expected = {
        "run_id": identity.get("run_id"),
        "tree_sha": identity.get("tree_sha"),
        "netlist_sha256": identity.get("sta_netlist"),
        "sdc_sha256": (current.get("sources") or {}).get("signoff_sdc", {}).get("sha256"),
        "l7_sha256": (frozen.get("sources") or {}).get("l7"),
        "l9_sha256": (frozen.get("sources") or {}).get("l9"),
        "pdk_config_sha256": (frozen.get("sources") or {}).get("pdk_config"),
        "spec_version": identity.get("spec_version"),
    }
    approved: list[dict] = []
    folder = project / "owner_approvals" / "drv"
    for path in sorted(folder.glob("*.json")):
        signature = path.with_suffix(path.suffix + ".sig")
        if not signature.is_file():
            continue
        try:
            payload = path.read_bytes()
            process = subprocess.run(
                ["ssh-keygen", "-Y", "verify", "-f", str(_OWNER_SIGNERS),
                 "-I", "owner", "-n", "vibeic-drv-owner", "-s", str(signature)],
                input=payload, capture_output=True, check=False)
            if process.returncode:
                continue
            record = json.loads(payload)
            if (isinstance(record, dict) and record.get("identity") == expected
                    and record.get("type") in
                    ("waiver", "design_override", "clock_limits",
                     "threshold_freeze")):
                approved.append(record)
        except (OSError, ValueError, TypeError):
            continue
    return approved, "OWNER_APPROVAL_ABSENT"


def _approved_before_run(record: dict, identity: dict) -> bool:
    try:
        approved = datetime.fromisoformat(record["owner_timestamp"])
        started = datetime.fromisoformat(identity["run_started_at"])
        return bool(record.get("owner_quote")) and approved.tzinfo is not None and (
            started.tzinfo is not None and approved < started)
    except (KeyError, TypeError, ValueError):
        return False


def _annotate_limits(row: dict, kind: str, scene: dict, libs: dict,
                     pins: dict, declared: dict, missing: list[str]) -> bool:
    """Resolve a pin against the Liberty files linked by this STA scene."""
    meta = pins.get(row["pin"])
    if not isinstance(meta, dict):
        missing.append(f"{scene['name']}: pin metadata absent for {row['pin']}")
        return False
    row.update({key: value for key, value in meta.items() if key != "liberty"})
    if row.get("net_class") not in ("clock", "data", "IO", "constant"):
        missing.append(f"{scene['name']}: pin/net class absent for {row['pin']}")
        return False
    cell, cell_pin = row.get("cell"), row.get("cell_pin")
    matched = []
    for linked in scene.get("linked_liberties") or []:
        lib = libs.get(linked.get("name")) if isinstance(linked, dict) else None
        if lib and cell in lib["limits"]["cells"]:
            pin_table = lib["limits"]["cells"][cell].get(cell_pin)
            if pin_table is not None:
                matched.append((linked["name"], lib, pin_table))
    if len(matched) != 1:
        missing.append(f"{scene['name']}: cell/pin Liberty identity unresolved for "
                       f"{row['pin']} ({len(matched)} matches)")
        return False
    lib_name, lib, pin_table = matched[0]
    actual_io = cell in lib["limits"]["pad_cells"]
    if row.get("cell_class") == "IO" and not actual_io:
        missing.append(f"{scene['name']}: IO class lacks Liberty pad_cell proof for {row['pin']}")
        return False
    row["cell_class"] = "IO" if actual_io else "std"
    if row["net_class"] == "clock" and row["pin"] not in (
            scene.get("clock_network_pins") or []):
        missing.append(f"{scene['name']}: clock net classification lacks tool proof for {row['pin']}")
        return False
    pin_limit = pin_table.get(kind)
    if pin_limit is None:
        pin_limit = lib["limits"]["defaults"].get(kind)
    explicit = declared.get({"max_slew": "slew_ns",
                             "max_capacitance": "cap_pf",
                             "max_fanout": "fanout"}[kind])
    if kind == "max_capacitance" and row.get("cell_class") == "IO":
        explicit = declared.get("io_cap_pf")
    if kind == "max_fanout" and row["net_class"] == "clock":
        explicit = declared.get("clock_fanout", explicit)
    limits = [v for v in (pin_limit, explicit) if _positive_number(v)]
    if not limits:
        missing.append(f"{scene['name']}: no Liberty or declared limit for "
                       f"{kind} {row['pin']}")
        return False
    row["kind"] = kind
    row["liberty_limit"] = pin_limit
    row["explicit_limit"] = explicit
    row["effective_limit"] = min(limits)
    row["liberty_source"] = {"name": lib_name,
                              "path": lib["identity"].get("path"),
                              "sha256": lib["identity"].get("sha256"),
                              "pin": cell_pin}
    row["limit_source"] = (
        "Liberty" if _positive_number(pin_limit) and
        pin_limit <= (explicit if _positive_number(explicit) else pin_limit)
        else "design/PDK sign-off declaration")
    row["load_pin"] = (kind == "max_slew" and
                       row.get("driver_pin", row["pin"]) != row["pin"])
    row["liberty_usage"] = (row["measured"] / pin_limit
                            if _positive_number(pin_limit) else None)
    return True


def _waiver_support(waiver: dict, row: dict, bundle: dict,
                    source_errors: list[str]) -> bool:
    """Section 6: accept numerical, content-addressed timing/EM evidence."""
    local_errors: list[str] = []
    identity = bundle["identity"]
    declared = bundle["frozen"]["values"]
    scenes = set(bundle["frozen"]["scenes"])
    timing = waiver.get("timing") or []
    if {r.get("scene") for r in timing} != scenes or len(timing) != len(scenes):
        return False
    for receipt in timing:
        if (receipt.get("netlist_sha256") != identity.get("sta_netlist") or
                receipt.get("sdc_sha256") != bundle["current"]["sources"]["signoff_sdc"]["sha256"] or
                receipt.get("period_ns") != declared.get("period_ns") or
                receipt.get("io_delay_ns") != declared.get("io_delay_ns") or
                not isinstance(receipt.get("setup_slack_ns"), (int, float)) or
                not isinstance(receipt.get("hold_slack_ns"), (int, float)) or
                receipt["setup_slack_ns"] < 0 or receipt["hold_slack_ns"] < 0):
            return False
        _evidence(receipt.get("report") or {}, local_errors,
                  "waiver timing " + str(receipt.get("scene")))
    em = waiver.get("signal_em") or {}
    if (em.get("netlist_sha256") != identity.get("sta_netlist") or
            em.get("scene") not in scenes or not em.get("worst_scene") or
            not em.get("drm_source")):
        return False
    _evidence(em.get("report") or {}, local_errors, "waiver signal EM")
    _evidence(em.get("drm_source") or {}, local_errors, "waiver DRM")
    for axis in ("average", "rms", "peak"):
        measured = (em.get("currents") or {}).get(axis)
        limit = (em.get("limits") or {}).get(axis)
        if not isinstance(measured, (int, float)) or not _positive_number(limit) or measured > limit:
            return False
    if row["net_class"] == "clock":
        clocks = waiver.get("clock_metrics") or {}
        if (not _positive_number(waiver.get("predeclared_clock_skew_limit")) or
                not _positive_number(waiver.get("predeclared_clock_latency_limit")) or
                clocks.get("skew") is None or clocks.get("latency") is None or
                clocks["skew"] > waiver["predeclared_clock_skew_limit"] or
                clocks["latency"] > waiver["predeclared_clock_latency_limit"] or
                waiver.get("clock_reason") not in
                ("intentional_cts_tradeoff", "unfixed_tool_defect")):
            return False
        if waiver["clock_reason"] == "unfixed_tool_defect" and not (
                waiver.get("upstream_issue") and waiver.get("pinned_tool_reproducer")
                and waiver.get("upstream_unfixed") is True):
            return False
    source_errors.extend(local_errors)
    return not local_errors


def judge(bundle: dict, *, project: Path | None = None) -> dict:
    """Judge measured rows independently of tool rc/checker summaries."""
    import instrument_calibration
    instrument_calibration.assert_calibrated("drv_signoff_judge::parse_check_types")
    fails: list[str] = []
    missing: list[str] = []
    findings: list[dict] = []
    io_margin_disclosures: list[dict] = []
    waived: list[dict] = []
    identity = bundle.get("identity") or {}
    frozen = bundle.get("frozen") or {}
    current = bundle.get("current") or {}
    owner_records, owner_status = _owner_records(project, identity, current, frozen)
    declared = frozen.get("values") or {}
    run_id, tree_sha = identity.get("run_id"), identity.get("tree_sha")
    if project is not None:
        if Path(str(identity.get("project") or "")).resolve() != project.resolve():
            missing.append("project identity differs from judged project directory")
    if not run_id or not tree_sha or not identity.get("spec_version"):
        missing.append("run ID, tree SHA or specification version absent")
    for name in ("sta_netlist", "lvs_netlist", "gds_netlist"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(identity.get(name) or "")):
            missing.append(f"{name} sha256 absent")
    for name in ("sta_netlist", "lvs_netlist", "gds_netlist", "odb", "def"):
        item = (identity.get("artifacts") or {}).get(name) or {}
        _evidence(item, missing, name)
        if name.endswith("netlist") and item.get("sha256") != identity.get(name):
            fails.append(f"{name}: recorded netlist sha256 disagrees with file")
    if len({identity.get(name) for name in ("sta_netlist", "lvs_netlist",
                                            "gds_netlist")}) != 1:
        fails.append("STA/LVS/GDS netlist identity mismatch")
    for name in ("openroad_commit", "opensta_commit", "pdk_commit"):
        if not identity.get(name):
            missing.append(f"{name} absent")
    image = identity.get("tool_image")
    digest = identity.get("tool_image_digest")
    if (not isinstance(image, str) or
            not re.fullmatch(r"sha256:[0-9a-f]{64}", str(digest)) or
            _installed_image_digest(image) != digest):
        missing.append("OpenSTA image digest not bound to installed image")
    source_texts = {}
    for name in ("l7", "l9", "pdk_config", "signoff_sdc"):
        item = (current.get("sources") or {}).get(name) or {}
        source_texts[name] = _evidence(item, fails, name)
        if item.get("sha256") != (frozen.get("sources") or {}).get(name):
            fails.append(f"{name}: 門檻來源已變更")
    if project is not None:
        for layer in ("l7", "l9"):
            source_path = Path(str((current.get("sources") or {}).get(
                layer, {}).get("path") or "")).resolve()
            allowed = (project / "input/docs", project / "phase1/generated_docs")
            if not any(source_path.is_relative_to(root.resolve()) and
                       source_path.name.upper().startswith(layer.upper())
                       for root in allowed):
                missing.append(f"{layer}: declaration file outside judged project")
        provenance = project / "phase3/librelane_pdk_root.provenance.json"
        try:
            root_doc = json.loads(provenance.read_text())
            pdk_root = Path(root_doc["path"]).resolve() / str(identity.get("pdk") or "")
            pdk_source = Path(str((current.get("sources") or {}).get(
                "pdk_config", {}).get("path") or "")).resolve()
            if not pdk_root.is_dir() or not pdk_source.is_relative_to(pdk_root):
                missing.append("PDK config is not inside installed PDK root")
            if ((root_doc.get("derivation") or {}).get("image_id") !=
                    identity.get("tool_image_digest")):
                missing.append("installed PDK root image identity differs from STA image")
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            missing.append("installed PDK root provenance unavailable")
    sdc = (current.get("sources") or {}).get("signoff_sdc") or {}
    sdc_text = _evidence(sdc, fails, "signoff_sdc")
    if _FORBIDDEN_SDC.search(sdc_text) and not bundle.get("owner_exclusion_declaration"):
        missing.append("DRV exclusions in sign-off SDC lack prior owner declaration")
    for field, command in (("fanout", "set_max_fanout"),
                           ("slew_ns", "set_max_transition"),
                           ("cap_pf", "set_max_capacitance")):
        values = _sdc_values(sdc_text, command)
        if not values:
            fails.append(f"sign-off SDC {command} absent")
        elif any(v != declared.get(field) for v in values):
            fails.append(f"sign-off SDC {command} != declared value")
    if (frozen.get("values") != current.get("values") or
            frozen.get("scenes") != current.get("scenes") or
            frozen.get("scope") != current.get("scope") or
            frozen.get("scene_liberties") != current.get("scene_liberties")):
        fails.append("門檻來源已變更: value/scope/scene")
    if frozen.get("scope") != "whole final netlist":
        fails.append("sign-off scope is not the whole final netlist")
    pdk_text = source_texts.get("pdk_config", "")
    integrator = {
        "fanout": _integrator_value(pdk_text, "MAX_FANOUT_CONSTRAINT", "SYNTH_MAX_FANOUT"),
        "slew_ns": _integrator_value(pdk_text, "MAX_TRANSITION_CONSTRAINT", "MAX_SLEW_CONSTRAINT"),
        "cap_pf": _integrator_value(pdk_text, "MAX_CAPACITANCE_CONSTRAINT", "MAX_CAP_CONSTRAINT"),
    }
    for field, value in integrator.items():
        if value is None:
            missing.append(f"PDK integrator {field} not extracted")
        elif field == "fanout" and declared.get("default_fanout_ceiling") != value:
            fails.append("default fanout ceiling != installed PDK config")
        elif field != "fanout" and declared.get(field) != value:
            signed_override = any(
                record.get("type") == "design_override" and
                record.get("field") == field and
                record.get("value") == declared.get(field) and
                record.get("reason") and
                _approved_before_run(record, identity)
                for record in owner_records)
            if not signed_override:
                fails.append(f"{field}: design override lacks signed prior owner "
                             f"rationale ({owner_status})")
    try:
        from declared_knob_applied_parity_check import collect_declared, _source_files
        declaration_project = project or Path(str(identity.get("project") or ""))
        if project is not None:
            l9_path = Path(str((current.get("sources") or {}).get("l9", {}).get("path") or ""))
            if l9_path.resolve() not in {p.resolve() for p in _source_files(project)}:
                missing.append("project L9 source is not a declaration file in judged project")
        if not identity.get("pdk") or not identity.get("library"):
            missing.append("L9 fanout scope identity lacks PDK or library")
        declared_l9 = collect_declared(declaration_project, pdk=str(identity.get("pdk") or ""),
                                       library=str(identity.get("library") or ""))
        l9_fanout = declared_l9.get("SYNTH_MAX_FANOUT")
        scoped_fanout = bool(re.search(
            r"(?im)^\s*\|\s*(?:pdk|library|lib|standard_cell_library)\s*\|[^\n]*"
            r"\bMAX_FANOUT_CONSTRAINT\b", source_texts.get("l9", "")))
        if l9_fanout and l9_fanout[0] != declared.get("fanout"):
            fails.append("L9 declared fanout != frozen sign-off value")
        elif not l9_fanout and scoped_fanout:
            missing.append("L9 fanout scope did not match judged PDK/library")
        elif not l9_fanout and declared.get("fanout") != integrator["fanout"]:
            fails.append("fanout has no L9 declaration or PDK-default basis")
    except (OSError, ValueError) as exc:
        missing.append(f"L9 design declaration unreadable: {exc}")
    required_scenes = frozen.get("scenes") or []
    if not required_scenes:
        missing.append("frozen corner/RC scene set absent")
    profiles = json.loads(_SCENE_PROFILES.read_text())
    profile = next((value for key, value in profiles.items()
                    if str(identity.get("pdk", "")).lower().startswith(key.lower())), None)
    if profile is None:
        missing.append("installed PDK sign-off scene profile unresolved")
    else:
        expected_scenes = {f"{pvt}_{rc}" for pvt in profile["pvt"]
                           for rc in profile["rc_corners"]}
        if set(required_scenes) != expected_scenes or len(required_scenes) != len(expected_scenes):
            missing.append("installed PDK sign-off requires the fixed 3 PVT x 3 RC scenes")
        if frozen.get("scene_profile_sha256") != _sha(_SCENE_PROFILES):
            missing.append("frozen PDK scene profile sha256 absent or changed")
        for scene_name in required_scenes:
            pvt_key = next((key for key in profile["pvt"]
                            if scene_name.startswith(key + "_")), None)
            if pvt_key and any((frozen.get("pvt") or {}).get(scene_name, {}).get(axis)
                               != value for axis, value in profile["pvt"][pvt_key].items()):
                missing.append(f"{scene_name}: frozen PVT differs from installed PDK profile")
    libs = {}
    for item in current.get("liberties") or []:
        body = _evidence(item, fails, "linked Liberty")
        if body:
            libs[item.get("name")] = {"identity": item,
                                      "limits": _liberty_limits(body),
                                      "header": _liberty_header(body)}
        if item.get("sha256") != (frozen.get("liberties") or {}).get(item.get("name")):
            fails.append("linked Liberty changed or removed")
    if set(libs) != set(frozen.get("liberties") or {}):
        fails.append("linked Liberty set changed")
    if {key for group in (frozen.get("scene_liberties") or {}).values()
        for key in group} != set(frozen.get("liberties") or {}):
        missing.append("frozen scene Liberty inventory does not cover every linked file")
    for field in ("fanout", "slew_ns", "cap_pf"):
        if not _positive_number(declared.get(field)):
            missing.append(f"declared {field} absent")
    for field in ("period_ns", "io_delay_ns"):
        if not _positive_number(declared.get(field)):
            missing.append(f"declared {field} absent for waiver qualification")

    stage_rows = {s.get("name"): s for s in bundle.get("stages") or []}
    postroute_repair_ran = bool(bundle.get("postroute_repair_ran"))
    if project is not None:
        repair_report = project / "reports/phase3/librelane_postroute_repair.json"
        if repair_report.is_file():
            try:
                recorded_repair = json.loads(repair_report.read_text())
                postroute_repair_ran = bool(recorded_repair.get("adopted"))
            except (OSError, ValueError, TypeError, AttributeError):
                missing.append("step 32 repair adoption receipt unreadable")
            if postroute_repair_ran != bool(bundle.get("postroute_repair_ran")):
                missing.append("step 32 repair adoption differs from DRV bundle")
    for name in _REQUIRED_STAGES:
        if name not in stage_rows:
            fails.append(f"{name}: required stage absent")
    if postroute_repair_ran and "postroute_repair" not in stage_rows:
        fails.append("postroute_repair: required applied constraints absent")
    broad_stage = False
    for name, stage in stage_rows.items():
        if name not in (*_REQUIRED_STAGES, "postroute_repair"):
            continue
        if not stage.get("ran") or not stage.get("behavior_report"):
            fails.append(f"{name}: did not run or lacks DRV behavior report")
        else:
            behavior = _evidence(stage["behavior_report"], fails, name + " behavior")
            if _COMMAND not in behavior or not all(
                    re.search(rf"\b{kind}\s+violators=\d+\b", behavior)
                    for kind in KINDS):
                fails.append(f"{name}: declared-value DRV behavior census absent")
        applied = stage.get("applied") or {}
        if name == "synth":
            script = _evidence(stage.get("abc_script") or {}, fails, "ABC script")
            match = re.search(r"\bbuffer\s+-N\s+(" + _NUM + r")\b", script)
            if not stage.get("synth_abc_buffering") or not match:
                fails.append("synth: fanout buffering absent")
            elif float(match.group(1)) != applied.get("fanout"):
                fails.append("synth: applied fanout differs from ABC buffer -N")
        else:
            snapshot = _evidence(stage.get("sdc_snapshot") or {}, fails,
                                 name + " pre-command SDC")
            for field, command in (("fanout", "set_max_fanout"),
                                   ("slew_ns", "set_max_transition"),
                                   ("cap_pf", "set_max_capacitance")):
                values = _sdc_values(snapshot, command)
                if not values or any(value != applied.get(field) for value in values):
                    fails.append(f"{name}: {command} snapshot != applied value")
            if not _positive_number(stage.get("fanout_check_limit")):
                fails.append(f"{name}: sta::max_fanout_check_limit absent")
            elif stage["fanout_check_limit"] != applied.get("fanout"):
                fails.append(f"{name}: top-cell fanout check limit != applied value")
            if name == "cts" and not stage.get("cts_parameters"):
                fails.append("cts: clock_tree_synthesis parameters absent")
            if name == "cts" and not isinstance(stage.get("clock_driver_fanout"), list):
                fails.append("cts: propagated clock-driver fanout list absent")
        if name == "synth" and stage.get("ideal_clock_excluded") is not True:
            fails.append("synth: post-synth DRV report did not exclude ideal clock")
        for field in ("fanout", "slew_ns", "cap_pf"):
            if name == "synth" and field != "fanout":
                continue  # ABC has no slew/cap parameter.
            value = applied.get(field)
            if not _positive_number(value):
                fails.append(f"{name}: applied {field} absent")
            elif name == "signoff_sta" and value != declared.get(field):
                fails.append(f"sign-off SDC {field} != declared value")
            elif name != "signoff_sta" and value > declared.get(field, 0):
                broad_stage = True
    pins = bundle.get("pins") or {}
    seen: dict[str, set[tuple]] = {k: set() for k in KINDS}
    rows_by_kind: dict[str, list[dict]] = {k: [] for k in KINDS}
    scene_names = set()
    spef_by_pvt: dict[str, dict[str, str]] = {}
    for scene in bundle.get("scenes") or []:
        name, mode = scene.get("name"), scene.get("mode")
        if not name or name in scene_names or name not in required_scenes:
            missing.append(f"scene identity missing, repeated or unexpected: {name}")
            continue
        scene_names.add(name)
        if not scene.get("fresh_process") or not scene.get("postroute"):
            missing.append(f"{name}: fresh post-route STA process not proven")
        if scene.get("unannotated_nets") != 0:
            missing.append(f"{name}: parasitic annotation incomplete")
        ann = _evidence(scene.get("parasitic_annotation_report") or {}, missing,
                        name + " parasitic annotation")
        counts_ann = re.findall(
            r"Found\s+(\d+)\s+(?:partially\s+)?unannotated\s+(?:drivers|nets)", ann)
        if not counts_ann or any(int(count) for count in counts_ann):
            missing.append(f"{name}: unannotated parasitic census not zero")
        if scene.get("propagated_clocks") is not True or not scene.get("clock_properties"):
            missing.append(f"{name}: propagated clock evidence absent")
        if not scene.get("excluded_pins_recorded"):
            missing.append(f"{name}: excluded pin population absent")
        excluded = scene.get("excluded_pins")
        if not isinstance(excluded, list) or any(
                not isinstance(p, dict) or not p.get("pin") or
                p.get("reason") not in ("constant", "disabled", "ideal") or
                any(not isinstance(p.get(axis), (int, float)) for axis in
                    ("fanout", "cap_pf", "slew_rise_ns", "slew_fall_ns"))
                for p in (excluded or [])):
            missing.append(f"{name}: excluded pin values incomplete")
        excluded_text = _evidence(scene.get("excluded_pins_report") or {},
                                  missing, name + " excluded pin report")
        try:
            if json.loads(excluded_text) != excluded:
                missing.append(f"{name}: excluded pins differ from raw tool report")
        except (ValueError, TypeError):
            missing.append(f"{name}: excluded pin report unreadable")
        for item in excluded or []:
            if not isinstance(item, dict) or not item.get("pin"):
                continue
            for kind, axis, direction in (
                    ("max_fanout", "fanout", "none"),
                    ("max_capacitance", "cap_pf", "none"),
                    ("max_slew", "slew_rise_ns", "rise"),
                    ("max_slew", "slew_fall_ns", "fall")):
                measured = item.get(axis)
                if not isinstance(measured, (int, float)):
                    continue
                row = {"pin": item["pin"], "scene": name, "mode": mode,
                       "direction": direction, "measured": measured,
                       "excluded_reason": item.get("reason")}
                if not _annotate_limits(row, kind, scene, libs, pins,
                                        declared, missing):
                    continue
                row["limit"] = row["effective_limit"]
                row["slack"] = row["effective_limit"] - measured
                row["violated"] = measured > row["effective_limit"]
                if row["violated"]:
                    rows_by_kind[kind].append(row)
                    if item.get("reason") != "constant":
                        fails.append(f"{name}: excluded {item.get('reason')} pin "
                                     f"{item['pin']} violates {kind}")
        for field in ("spef", "clock_properties", "all_limits_report", "positive_control_report"):
            if isinstance(scene.get(field), dict):
                _evidence(scene[field], missing, name + " " + field)
            else:
                missing.append(f"{name}: {field} absent")
        if scene.get("rc_corner") not in (frozen.get("rc_corners") or []):
            missing.append(f"{name}: RC corner not frozen")
        pvt_name, _, name_rc = name.rpartition("_")
        if not pvt_name or name_rc != scene.get("rc_corner"):
            missing.append(f"{name}: scene RC corner differs from declared RC corner")
        spef_by_pvt.setdefault(pvt_name, {})[str(scene.get("rc_corner"))] = (
            (scene.get("spef") or {}).get("sha256", ""))
        if scene.get("spef_layout_sha256") != (identity.get("artifacts") or {}).get("def", {}).get("sha256"):
            missing.append(f"{name}: SPEF not bound to routed DEF identity")
        extraction_text = _evidence(scene.get("spef_extraction_receipt") or {},
                                    missing, name + " SPEF extraction receipt")
        try:
            extraction = json.loads(extraction_text)
            if (extraction.get("routed_def_sha256") !=
                    (identity.get("artifacts") or {}).get("def", {}).get("sha256")
                    or extraction.get("spef_sha256") !=
                    (scene.get("spef") or {}).get("sha256") or
                    extraction.get("rc_corner") != scene.get("rc_corner") or
                    not re.fullmatch(r"[0-9a-f]{64}", str(
                        extraction.get("extraction_command_sha256") or ""))):
                missing.append(f"{name}: SPEF extraction input/output hashes differ")
        except (TypeError, ValueError):
            missing.append(f"{name}: SPEF extraction provenance unreadable")
        if scene.get("liberty") not in libs:
            missing.append(f"{name}: linked Liberty identity absent")
        linked = {item.get("name"): item.get("sha256")
                  for item in scene.get("linked_liberties") or []
                  if isinstance(item, dict)}
        expected_links = (frozen.get("scene_liberties") or {}).get(name)
        if (not isinstance(expected_links, list) or not expected_links or
                linked != {key: libs[key]["identity"]["sha256"]
                           for key in expected_links if key in libs} or
                set(linked) != set(expected_links)):
            missing.append(f"{name}: process did not link its frozen full Liberty set")
        expected_reads = ([
            ("read_liberty", str(Path(item["path"]).resolve()))
            for item in scene.get("linked_liberties") or []
            if isinstance(item, dict) and item.get("path")]
            + [("read_verilog", str(Path((identity.get("artifacts") or {}).get(
                "sta_netlist", {}).get("path") or "").resolve())),
               ("read_sdc", str(Path(sdc.get("path") or "").resolve())),
               ("read_spef", str(Path((scene.get("spef") or {}).get("path") or "").resolve()))])
        scripts = scene.get("tool_scripts") or []
        if not isinstance(scripts, list) or len(scripts) != 2:
            missing.append(f"{name}: fresh STA tool scripts absent")
        else:
            for index, script in enumerate(scripts):
                body = _evidence(script, missing, f"{name} STA tool script {index}")
                if (_script_reads(body) != expected_reads or
                        _COMMAND not in body or
                        re.search(r"(?m)^\s*(?:source|eval|exec)\b", body)):
                    missing.append(f"{name}: STA tool script reads differ from frozen inputs")
                if index == 1 and not re.search(
                        r"(?m)^set_max_fanout\s+1\s+\[current_design\]\s*$", body):
                    missing.append(f"{name}: positive control fanout injection absent")
        expected_pvt = (frozen.get("pvt") or {}).get(name)
        actual_pvt = (libs.get(scene.get("liberty")) or {}).get("header")
        if (not isinstance(expected_pvt, dict) or
                any((actual_pvt or {}).get(k) is None or
                    (actual_pvt or {}).get(k) != expected_pvt.get(k)
                    for k in ("nom_process", "nom_voltage", "nom_temperature"))):
            missing.append(f"{name}: Liberty PVT header mismatch")
        for linked_item in scene.get("linked_liberties") or []:
            if not isinstance(linked_item, dict):
                missing.append(f"{name}: linked Liberty record malformed")
                continue
            linked_header = (libs.get(linked_item.get("name")) or {}).get("header")
            if (not isinstance(expected_pvt, dict) or
                    any((linked_header or {}).get(k) != expected_pvt.get(k)
                        for k in ("nom_process", "nom_voltage", "nom_temperature"))):
                missing.append(f"{name}: {linked_item.get('name')} linked Liberty PVT mismatch")
        if scene.get("command") != _COMMAND:
            missing.append(f"{name}: required OpenSTA report_check_types command absent")
        report = _evidence(scene.get("report") or {}, missing, name + " violator report")
        parsed = parse_check_types(report, scene=name, mode=mode, violators_only=True)
        counter_text = _evidence(scene.get("counter_report") or {}, missing,
                                 name + " violation counters")
        tool_counters = dict((m.group(1), int(m.group(2))) for m in re.finditer(
            r"(?m)^DRV_COUNTER\s+(max_slew|max_capacitance|max_fanout)\s+(\d+)\s*$",
            counter_text))
        all_text = _evidence(scene.get("all_limits_report") or {}, missing,
                             name + " all limits")
        all_rows = parse_check_types(all_text, scene=name, mode=mode,
                                     violators_only=False)
        control_text = _evidence(scene.get("positive_control_report") or {},
                                 missing, name + " positive control")
        control_rows = parse_check_types(control_text, scene=name, mode=mode,
                                         violators_only=True)
        if not scene.get("positive_control_fresh_process"):
            missing.append(f"{name}: positive control did not use fresh process")
        controls = scene.get("positive_control_limits") or {}
        if controls.get("max_fanout") != 1 or any(
                not _positive_number(controls.get(kind)) or
                controls[kind] >= declared.get(field, 0)
                for kind, field in (("max_slew", "slew_ns"),
                                    ("max_capacitance", "cap_pf"))):
            missing.append(f"{name}: positive control thresholds not tightened")
        for kind in KINDS:
            counter = (scene.get("counters") or {}).get(kind)
            if not isinstance(counter, int) or isinstance(counter, bool) or counter < 0:
                missing.append(f"{name}: {kind} counter absent")
                continue
            if tool_counters.get(kind) != counter:
                missing.append(f"{name}: {kind} counter differs from raw tool output")
            keys = {_key(kind, row) for row in parsed[kind]}
            if len(keys) != counter or len(keys) != len(parsed[kind]):
                missing.append(f"{name}: {kind} report names != counter")
            all_bad = {_key(kind, row) for row in all_rows[kind]
                       if row.get("violated")}
            if all_bad != keys:
                missing.append(f"{name}: {kind} all-limits vs violator names differ")
            population = (scene.get("population") or {}).get(kind)
            if (not isinstance(population, int) or population < 1 or
                    len(all_rows[kind]) < population or
                    (scene.get("all_limits_max_count") or 0) < population):
                missing.append(f"{name}: {kind} all-limits population incomplete")
            if any("limit" not in row for row in all_rows[kind]):
                missing.append(f"{name}: {kind} row without limit")
            control_limit = controls.get(kind)
            expected_control = sum(
                1 for row in all_rows[kind]
                if _positive_number(control_limit) and
                isinstance(row.get("measured"), (int, float)) and
                row["measured"] > control_limit)
            actual_control = (scene.get("positive_control_counters") or {}).get(kind)
            if (expected_control < 1 or
                    len(control_rows[kind]) != expected_control or
                    actual_control != expected_control):
                missing.append(f"{name}: {kind} positive control did not detect "
                               "the measured eligible population")
            independent_bad = set()
            for row in all_rows[kind]:
                key = _key(kind, row)
                if key in seen[kind] and kind != "max_fanout":
                    missing.append(f"duplicate {kind} key {key}")
                seen[kind].add(key)
                if any(field not in row for field in ("limit", "measured", "slack")):
                    missing.append(f"{name}: incomplete {kind} row {row['pin']}")
                    continue
                if kind == "max_slew" and row["direction"] not in ("rise", "fall"):
                    missing.append(f"{name}: slew rise/fall absent for {row['pin']}")
                if not _annotate_limits(row, kind, scene, libs, pins,
                                        declared, missing):
                    continue
                effective = row["effective_limit"]
                tolerance = max(1e-6, abs(effective) * 1e-6)
                io_disclosure = (kind == "max_capacitance" and
                                 row.get("cell_class") == "IO" and
                                 row.get("explicit_limit") is None and
                                 row["measured"] <= effective and
                                 row["limit"] == declared.get("cap_pf"))
                if io_disclosure:
                    row["failed_tier"] = "IO_STD_CELL_MARGIN_DISCLOSURE"
                    io_margin_disclosures.append(row)
                elif row["limit"] > effective + tolerance:
                    fails.append(f"{name}: {kind} {row['pin']} tool limit "
                                 f"{row['limit']} exceeds frozen effective limit {effective}")
                elif row["limit"] < effective - tolerance:
                    missing.append(f"{name}: {kind} {row['pin']} tool limit "
                                   "differs from frozen effective limit")
                independently_violated = row["measured"] > effective + tolerance
                if not io_disclosure and independently_violated != bool(row.get("violated")):
                    missing.append(f"{name}: {kind} {row['pin']} tool/slack vs "
                                   "frozen limit disagree")
                if independently_violated:
                    independent_bad.add(key)
                    rows_by_kind[kind].append(row)
            if independent_bad != keys and not (
                    kind == "max_capacitance" and
                    keys - independent_bad == {
                        _key(kind, row) for row in io_margin_disclosures
                        if row["scene"] == name}):
                missing.append(f"{name}: {kind} independent violators differ "
                               "from tool report")
    if scene_names != set(required_scenes):
        missing.append("required PVT/RC scenes absent")
    for pvt_name, rc_sha in spef_by_pvt.items():
        if len(rc_sha) > 1 and len(set(rc_sha.values())) != len(rc_sha):
            missing.append(f"{pvt_name}: RC corner SPEFs have identical hashes")
    # Fanout is invariant to RC; repeated corner rows describe one driver/mode.
    dedup = {}
    for row in rows_by_kind["max_fanout"]:
        key = _key("max_fanout", row)
        if key in dedup and row["measured"] != dedup[key]["measured"]:
            missing.append(f"fanout differs across scenes for {key}")
        if key not in dedup or row.get("limit", float("inf")) < dedup[key].get("limit", float("inf")):
            dedup[key] = row
    rows_by_kind["max_fanout"] = list(dedup.values())
    for kind, rows in rows_by_kind.items():
        for row in rows:
            pin_limit = row["liberty_limit"]
            explicit = row["explicit_limit"]
            effective = row["effective_limit"]
            if (kind == "max_capacitance" and row.get("cell_class") == "IO" and
                    explicit is None and _positive_number(pin_limit) and
                    row["measured"] <= pin_limit):
                row["failed_tier"] = "IO_STD_CELL_MARGIN_DISCLOSURE"
                io_margin_disclosures.append(row)
                continue
            # OpenSTA's displayed precision can differ by one last digit;
            # the tool's negative slack and independent source tier must agree.
            if pin_limit is not None and row["measured"] > pin_limit:
                tier, waivable = ("T1_LIBERTY_FANOUT" if kind == "max_fanout"
                                  else "T1_LIBERTY"), False
            elif kind == "max_slew" and row["net_class"] == "clock":
                tier, waivable = "T2_CLOCK_SLEW", False
            elif kind == "max_fanout":
                loads = row.get("loads") or {}
                cls = row["net_class"]
                eligible = (cls == "clock" or cls == "constant" or
                            (loads.get("logical", 0) <= effective and
                             loads.get("antenna_diode", 0) > 0 and
                             loads.get("logical", 0) + loads.get("antenna_diode", 0) >= row["measured"]))
                cap = declared.get("default_fanout_ceiling")
                tier, waivable = "DECLARED_FANOUT", eligible and (
                    _positive_number(cap) and row["measured"] <= cap or
                    _positive_number(declared.get("owner_class_fanout_ceiling")) and
                    row["measured"] <= declared["owner_class_fanout_ceiling"])
                if cls == "data" and not eligible:
                    tier = "DATA_NET_RESIDUE"
            elif kind == "max_capacitance" and row.get("cell_class") == "IO" and explicit is None:
                tier, waivable = "IO_MARGIN_DISCLOSURE", False
            else:
                tier, waivable = "T3_MARGIN", True
            row["failed_tier"] = tier
            row["waivable"] = bool(waivable)
            findings.append(row)
    flow_defects = []
    if broad_stage and findings:
        fails.append("post-route violation with broader implementation constraint")
    elif broad_stage:
        flow_defects.append("implementation constraint broader than declared; open flow-defect issue")
    ledger = [r.get("waiver") for r in owner_records
              if r.get("type") == "waiver" and isinstance(r.get("waiver"), dict)]
    for row in findings:
        if not row["waivable"] or (broad_stage and findings):
            fails.append(f"{row['failed_tier']}: {row['pin']} {row['scene']}")
            continue
        key = {"netlist_sha256": identity.get("sta_netlist"),
               "tree_sha": tree_sha, "run_id": run_id,
               "driver_pin": row.get("driver_pin", row["pin"]),
               "driver_cell": row.get("driver_cell", row.get("cell")),
               "measured": row["measured"], "limit": row["effective_limit"],
               "scene_or_mode": row["mode"] if row["kind"] == "max_fanout" else row["scene"],
               "sdc_sha256": sdc.get("sha256")}
        matches = [w for w in ledger if all(w.get(k) == v for k, v in key.items())]
        valid = [w for w in matches if w.get("owner_quote") and
                 w.get("owner_timestamp") and
                 _positive_number(w.get("approved_value")) and
                 w["approved_value"] >= row["measured"] and
                 _waiver_support(w, row, bundle, missing)]
        if valid:
            waived.append({"key": key, "owner_quote": valid[0]["owner_quote"],
                           "owner_timestamp": valid[0]["owner_timestamp"],
                           "below_baseline_quality": True})
        else:
            fails.append(f"{row['failed_tier']}: no valid signed owner waiver "
                         f"for {row['pin']} ({owner_status})")
    verdict = ("FAIL" if fails else "NOT_MEASURED" if missing else
               "WAIVED" if waived else "PASS")
    return {"schema_version": 1, "name": "DRV(tran/cap/fanout)",
            "verdict": verdict, "run_id": run_id, "tree_sha": tree_sha,
            "spec_version": identity.get("spec_version"),
            "counts": {k: len(v) for k, v in rows_by_kind.items()},
            "findings": findings, "waived": waived,
            "load_pin_slew_findings": [r for r in findings
                                       if r.get("load_pin")],
            "findings_by_net_class": {name: [r for r in findings
                                              if r.get("net_class") == name]
                                      for name in ("clock", "data", "IO", "constant")},
            "thresholds": {"values": declared,
                           "sources": frozen.get("sources"),
                           "liberties": frozen.get("liberties"),
                           "scope": frozen.get("scope"),
                           "scenes": required_scenes},
            "stage_constraints": bundle.get("stages") or [],
            "io_margin_disclosures": io_margin_disclosures,
            "flow_defects": flow_defects,
            "failures": fails, "not_measured": missing,
            "out_of_scope": {k: "NOT_MEASURED" for k in
                             ("noise/SI", "signal EM", "cell EM", "max_length")}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path,
                        help="evidence JSON or project root containing reports/phase3/sta/drv_signoff_bundle.json")
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        source = (args.bundle / "reports/phase3/sta/drv_signoff_bundle.json"
                  if args.bundle.is_dir() else args.bundle)
        bundle = json.loads(source.read_text())
        result = judge(bundle, project=args.bundle if args.bundle.is_dir() else None)
        if not args.bundle.is_dir():
            result.setdefault("not_measured", []).append(
                "project context absent; threshold sources cannot be authenticated")
            result["verdict"] = ("FAIL" if result.get("failures") else
                                 "NOT_MEASURED")
        else:
            if not (args.bundle / "reports/phase3/sta/drv_capture_plan.json").is_file():
                result.setdefault("not_measured", []).append(
                    "fresh DRV capture plan absent")
            routed = args.bundle / "phase3/stage3/pnr/routed.def"
            recorded = ((bundle.get("identity") or {}).get("artifacts") or {}).get("def", {}).get("sha256")
            if not routed.is_file():
                result.setdefault("not_measured", []).append(
                    "current routed DEF absent")
            elif _sha(routed) != recorded:
                result.setdefault("not_measured", []).append(
                    "current routed DEF differs from judged layout identity")
            result["verdict"] = ("FAIL" if result.get("failures") else
                                 "NOT_MEASURED" if result.get("not_measured") else
                                 result["verdict"])
    except Exception as exc:  # noqa: BLE001 - a crashed instrument has no verdict
        result = {"name": "DRV(tran/cap/fanout)", "verdict": "NOT_MEASURED",
                  "not_measured": [f"bundle unreadable: {exc}"]}
    if result["verdict"] == "NOT_MEASURED":
        result["reason_class"] = ("input_absent" if not source.is_file()
                                  else "partial_population")
    write_text(args.json, json.dumps(result, indent=2) + "\n")
    print(result["verdict"], result["name"])
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
