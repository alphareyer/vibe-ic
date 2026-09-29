"""Audit OpenROAD's EM verdict; never recompute current density.

Consumed by the blocking step-25 authority gate. Missing capability, limits,
geometry or invocation evidence is NOT_MEASURED. The legacy J screen remains
available for HARVEST/A-B; OpenROAD judges the power-grid segments in dual mode.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from pathlib import Path

SCHEMA = "openroad_em/2"


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def limits(pdk_name, tech_lef, routing_limits, margin, registry=None):
    """Routing authority from LEF; per-cut authority only from the PDK overlay."""
    registry = registry or Path(__file__).with_name("pdk_registry.json")
    entries = json.loads(Path(registry).read_text()).get("pdks", [])
    overlay = next((e.get("em_limits", {}) for e in entries
                    if e.get("name") == pdk_name), {})
    lef_sha = hashlib.sha256(tech_lef.encode()).hexdigest()
    rows, provenance = [], {}
    for spec in routing_limits.values():
        value = spec.get("jmax_areal_A_per_um2")
        if spec.get("kind") != "routing" or not value:
            continue
        name = spec["orig_name"]
        applied = value * (1 - margin)
        rows.append(f"{name} {applied:.12g}")
        provenance[name] = {"basis": "AREAL", "limit": applied,
                            "source": "LEF DCCURRENTDENSITY / THICKNESS",
                            "source_sha256": lef_sha, "margin": margin}
    cut_layers = set(re.findall(r"\bLAYER\s+(\S+)\s+TYPE\s+CUT\s*;", tech_lef))
    for name, spec in overlay.get("per_cut", {}).items():
        if name not in cut_layers:
            continue
        value = spec.get("current_A")
        if (not isinstance(value, (int, float)) or not math.isfinite(value)
                or value <= 0 or not spec.get("source") or not spec.get("source_sha256")
                or not spec.get("temperature_C") or not spec.get("lifetime_basis")):
            continue
        rows.append(f"{name} per_cut {value:.12g}")
        provenance[name] = dict(spec, basis="PER_CUT", limit=value)
    return rows, provenance


def snapshot(folder, paths):
    """Content-address the actual files of this invocation, relative to its folder."""
    return {str(Path(p).relative_to(folder)): digest(p) for p in paths if Path(p).is_file()}


def _file(folder, rel, sha):
    path = (folder / rel).resolve()
    if not path.is_relative_to(folder.resolve()) or not path.is_file():
        raise ValueError(f"missing or external invocation artefact: {rel}")
    if digest(path) != sha:
        raise ValueError(f"invocation artefact changed: {rel}")
    return path


def audit(folder, record, subject_def):
    """Read log Verdict + Status/Cuts/Basis, checking coverage and provenance."""
    result = {"verdict": "NOT_MEASURED", "findings": [], "nets": {},
              "scope": "power-grid wires and vias", "signal_em": "NOT_MEASURED",
              "via_cut_status": "NOT_MEASURED"}
    try:
        invocation = record["invocation"]
        if record.get("schema") != SCHEMA:
            raise ValueError("EM_TOOL_CAPABILITY_MISSING: fresh Verdict/Status/Cuts/Basis required")
        if invocation.get("native_rc") != 0 or not invocation.get("inputs_unchanged"):
            raise ValueError("EM_TOOL_INVOCATION_FAILED_OR_INPUT_CHANGED")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", invocation.get("image_id", "")):
            raise ValueError("EM_TOOL_IMAGE_UNBOUND")
        if not invocation.get("tool_version") or not invocation.get("tool_binary_sha256"):
            raise ValueError("EM_TOOL_BINARY_UNBOUND")
        if record.get("def_sha256") != digest(subject_def):
            raise ValueError("EM_TOOL_DEF_CHANGED")
        if not record.get("source_model") or not record.get("power_basis"):
            raise ValueError("EM_TOOL_POWER_BASIS_UNBOUND")
        for rel, sha in invocation["inputs"].items():
            _file(folder, rel, sha)
        outputs = invocation["outputs"]
        log_path = _file(folder, "ir_em.log", outputs["ir_em.log"])
        log = log_path.read_text(errors="replace")
        command_sha = hashlib.sha256(invocation["command"].encode()).hexdigest()
        if "EM_TOOL_COMMAND_SHA256 " + command_sha not in log:
            raise ValueError("EM_TOOL_COMMAND_UNBOUND")
        tcl_name = invocation["tcl_file"]
        tcl_path = _file(folder, tcl_name, invocation["inputs"][tcl_name])
        if "EM_TOOL_TCL_SHA256 " + digest(tcl_path) not in log:
            raise ValueError("EM_TOOL_TCL_UNBOUND")
        power = record["power_basis"]
        if (power.get("complete") is not True or power.get("unread")
                or any(marker in log for marker in ("IR_BASIS_SDC_UNREAD",
                    "IR_BASIS_CLOCKS_UNPROPAGATED", "IR_BASIS_SPEF_UNREAD",
                    "EM_BASIS_POWER_UNREPORTED"))):
            raise ValueError("EM_TOOL_POWER_BASIS_INCOMPLETE")
        project = folder.parent.parent.resolve()
        for key in ("sdc", "spef"):
            path = Path(power[key]).resolve()
            if not path.is_relative_to(project) or digest(path) != power[key + "_sha256"]:
                raise ValueError(f"EM_TOOL_POWER_INPUT_CHANGED: {key}")
        from dynamic_ir_vectored_emit import power_basis
        current_power = power_basis(subject_def, power["sdc"], power["spef"],
                                    power["liberties"], log=log)
        if any(current_power.get(k) != power.get(k) for k in (
                "id", "layout_sha256", "sdc_sha256", "spef_sha256", "total_power_w")):
            raise ValueError("EM_TOOL_POWER_BASIS_CHANGED")
        tool_inputs = invocation["tool_inputs"]
        if set(tool_inputs) != set(invocation["required_input_paths"]):
            raise ValueError("EM_TOOL_INPUT_HASHES_MISSING")
        for path, sha in tool_inputs.items():
            observed = re.findall(r"^EM_TOOL_INPUT_SHA256 ([0-9a-f]{64})\s+"
                                  + re.escape(path) + r"\s*$", log, re.M)
            if observed != [sha, sha]:
                raise ValueError(f"EM_TOOL_INPUT_UNBOUND_OR_CHANGED: {path}")
            if Path(path).is_file() and digest(path) != sha:
                raise ValueError(f"EM_TOOL_INPUT_CHANGED: {path}")
        for marker, value in (("EM_TOOL_VERSION ", invocation["tool_version"]),
                              ("EM_TOOL_BINARY_SHA256 ", invocation["tool_binary_sha256"])):
            if marker + value not in log:
                raise ValueError("EM_TOOL_LOG_IDENTITY_MISMATCH")
        limit_path = _file(folder, "em_openroad_limits.txt",
                           invocation["inputs"]["em_openroad_limits.txt"])
        tech_path = _file(folder, "em_tool_tech.lef",
                          invocation["inputs"]["em_tool_tech.lef"])
        if tool_inputs.get(invocation["tech_lef"]) != digest(tech_path):
            raise ValueError("EM_TOOL_TECH_LEF_UNBOUND")
        import em_current_density_check as retained
        _, trusted = limits(record["pdk_name"], tech_path.read_text(),
                            retained.parse_lef_jmax(tech_path.read_text()),
                            retained._DEFAULT_MARGIN)
        cut_layers = set(re.findall(r"\bLAYER\s+(\S+)\s+TYPE\s+CUT\s*;",
                                    tech_path.read_text()))
        applied = {}
        for line in limit_path.read_text().splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            fields = line.split()
            if len(fields) == 2:
                layer, value = fields
                basis = "AREAL"
            elif len(fields) == 3 and fields[1] == "per_cut":
                layer, _, value = fields
                basis = "PER_CUT"
            else:
                raise ValueError("EM_TOOL_LIMIT_SYNTAX")
            if layer in applied:
                raise ValueError("EM_TOOL_DUPLICATE_LIMIT")
            applied[layer] = (basis, float(value))
        all_verdicts = []
        nets = record["expected_nets"]
        if not nets or len(set(nets)) != len(nets):
            raise ValueError("EM_TOOL_RAILS_UNDECLARED")
        special = re.search(r"\bSPECIALNETS\s+\d+\s*;(.*?)\bEND\s+SPECIALNETS",
                            Path(subject_def).read_text(), re.S)
        supply_nets = set()
        if special:
            for entry in special.group(1).split(";"):
                name = re.search(r"^\s*-\s+(\S+)", entry)
                if name and re.search(r"\+\s+USE\s+(POWER|GROUND)\b", entry):
                    supply_nets.add(name[1])
        if not supply_nets or supply_nets != set(nets):
            raise ValueError("EM_TOOL_RAIL_COVERAGE_INCOMPLETE")
        if not all("check_current_density -net " + net in tcl_path.read_text()
                   for net in nets):
            raise ValueError("EM_TOOL_TCL_RAIL_INVOCATION_MISSING")
        for net in nets:
            begin, end = f"=== EM_TOOL_BEGIN {net} ===", f"=== EM_TOOL_END {net} ==="
            if log.count(begin) != 1 or log.count(end) != 1:
                raise ValueError(f"EM_TOOL_NET_NOT_INVOKED: {net}")
            section = log.split(begin, 1)[1].split(end, 1)[0]
            verdicts = re.findall(r"^Verdict\s*:\s*(PASS|FAIL|NOT_MEASURED)\s*$",
                                  section, re.M)
            if len(verdicts) != 1 or f"EM_TOOL_OK {net}" not in section:
                raise ValueError(f"EM_TOOL_CAPABILITY_MISSING: {net}")
            if not re.search(r"^Net\s*:\s*" + re.escape(net) + r"\s*$", section, re.M):
                raise ValueError(f"EM_TOOL_NET_MISMATCH: {net}")
            counts = dict(rows=0, checked=0, no_area=0, no_limit=0, violated=0,
                          per_cut=0, via_records=0, via_unmeasured=0, worst_ratio=0.0)
            name = f"em_openroad_density_{net}.csv"
            path = _file(folder, name, outputs[name])
            for fresh in (path, log_path):
                # Filesystem mtimes can lag wall-clock sampling by one tick.
                # The producer deletes these outputs before starting the run.
                if fresh.stat().st_mtime_ns + 10**9 < invocation["started_ns"]:
                    raise ValueError(f"EM_TOOL_STALE_OUTPUT: {fresh.name}")
            with path.open(newline="") as f:
                reader = csv.DictReader(f)
                if not {"Layer", "Status", "Cuts", "Basis", "Ratio", "Jlimit(A/um^2)"}.issubset(reader.fieldnames or []):
                    raise ValueError(f"EM_TOOL_CAPABILITY_MISSING: {net} CSV")
                for row in reader:
                    counts["rows"] += 1
                    status, basis, layer = row["Status"], row["Basis"], row["Layer"]
                    is_via = layer in cut_layers or int(row["Cuts"]) > 0
                    counts["via_records"] += is_via
                    if status in ("NO_AREA", "NO_LIMIT"):
                        if basis != status:
                            raise ValueError("EM_TOOL_STATUS_BASIS_MISMATCH")
                        counts[status.lower()] += 1
                        counts["via_unmeasured"] += is_via
                        continue
                    if status not in ("OK", "VIOLATED") or basis not in ("AREAL", "PER_CUT"):
                        raise ValueError("EM_TOOL_UNKNOWN_STATUS_OR_BASIS")
                    prov = record["limits"].get(layer, {})
                    if (applied.get(layer, (None,))[0] != basis or prov.get("basis") != basis
                            or not prov.get("source") or not prov.get("source_sha256")
                            or prov != trusted.get(layer)):
                        raise ValueError(f"EM_TOOL_LIMIT_PROVENANCE_MISSING: {layer}")
                    value = float(row["Jlimit(A/um^2)"])
                    if (not math.isfinite(value) or value <= 0
                            or not math.isclose(value, applied[layer][1], rel_tol=0.0006)
                            or not math.isclose(applied[layer][1], prov["limit"], rel_tol=1e-10)):
                        raise ValueError(f"EM_TOOL_LIMIT_MISMATCH: {layer}")
                    if basis == "PER_CUT":
                        if int(row["Cuts"]) <= 0:
                            raise ValueError(f"EM_TOOL_CUTS_MISSING: {layer}")
                        counts["per_cut"] += 1
                    elif int(row["Cuts"]) > 0:
                        raise ValueError(f"EM_TOOL_VIA_NOT_PER_CUT: {layer}")
                    ratio = float(row["Ratio"])
                    if not math.isfinite(ratio) or ratio < 0:
                        raise ValueError("EM_TOOL_RATIO_INVALID")
                    counts["worst_ratio"] = max(counts["worst_ratio"], ratio)
                    counts["checked"] += 1
                    counts["violated"] += status == "VIOLATED"
            segment_name = f"em_segments_{net}.csv"
            segment_path = _file(folder, segment_name, outputs[segment_name])
            with segment_path.open() as f:
                solved = max(0, sum(1 for _ in f) - 1)
            if solved == 0 or counts["rows"] != solved:
                raise ValueError(f"EM_TOOL_COVERAGE_INCOMPLETE: {net} {counts['rows']}/{solved}")
            counters = {"Segments checked": counts["checked"] + counts["no_limit"],
                        "With J-limit": counts["checked"], "Vias judged per cut": counts["per_cut"],
                        "No J-limit (skipped)": counts["no_limit"],
                        "No area (skipped)": counts["no_area"], "Violations": counts["violated"]}
            for label, value in counters.items():
                matches = re.findall(r"^" + re.escape(label) + r"\s*:\s*(\d+)\s*$", section, re.M)
                if matches != [str(value)]:
                    raise ValueError(f"EM_TOOL_COUNTER_MISMATCH: {net} {label}")
            expected = ("FAIL" if counts["violated"] else "NOT_MEASURED"
                        if counts["no_area"] or counts["no_limit"] else "PASS")
            if verdicts[0] != expected:
                raise ValueError(f"EM_TOOL_VERDICT_DISAGREES_WITH_ROWS: {net}")
            counts.update(psm_segments=solved, tool_verdict=verdicts[0])
            result["nets"][net] = counts
            all_verdicts.append(verdicts[0])
        result["verdict"] = ("FAIL" if "FAIL" in all_verdicts else "NOT_MEASURED"
                              if "NOT_MEASURED" in all_verdicts else "PASS")
        if (sum(r["via_records"] for r in result["nets"].values()) > 0
                and not any(r["via_unmeasured"] for r in result["nets"].values())):
            result["via_cut_status"] = "MEASURED"
        if result["verdict"] == "FAIL":
            result["findings"].append({"severity": "ERROR", "rule": "EM_TOOL_VIOLATION",
                                        "message": "OpenROAD reports measured power-grid EM violations"})
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
        result["skip_reason"] = str(exc)
    return result


def audit_project(project):
    folder = Path(project) / "reports/phase3"
    try:
        record = json.loads((folder / "em_openroad_density.json").read_text())
        subject = Path(project) / record["subject_def"]
        if not subject.resolve().is_relative_to(Path(project).resolve()):
            raise ValueError("EM_TOOL_EXTERNAL_DEF")
        return audit(folder, record, subject)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"verdict": "NOT_MEASURED", "skip_reason": str(exc), "findings": []}
