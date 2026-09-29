"""Step-25 tool evidence and the existing blocking authority consumer."""
import csv
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _em_tool_report as E
import em_peak_current_authority_check as A


def evidence(tmp_path, status="OK", basis="PER_CUT", cuts=2):
    folder = tmp_path / "reports/phase3"
    folder.mkdir(parents=True)
    subject = tmp_path / "route.def"
    subject.write_text("SPECIALNETS 2 ;\n- supply + USE POWER ;\n- return + USE GROUND ;\nEND SPECIALNETS\n")
    registry = json.loads(Path(E.__file__).with_name("pdk_registry.json").read_text())
    entry = next(e for e in registry["pdks"] if e.get("em_limits"))
    layer, rule = next(iter(entry["em_limits"]["per_cut"].items()))
    tech = folder / "em_tool_tech.lef"
    tech.write_text(f"LAYER {layer}\n TYPE CUT ;\nEND {layer}\n")
    rows, prov = E.limits(entry["name"], tech.read_text(), {}, 0.1)
    (folder / "session.tcl").write_text("check_current_density -net supply\ncheck_current_density -net return\n")
    (folder / "em_openroad_limits.txt").write_text("\n".join(rows) + "\n")
    sdc, spef = tmp_path / "clock.sdc", tmp_path / "route.spef"
    sdc.write_text("create_clock -period 10 clk\n")
    spef.write_text("*SPEF \"IEEE 1481-1998\"\n")
    command = "openroad -no_init -exit " + str(folder / "session.tcl")
    from dynamic_ir_vectored_emit import power_basis
    power_log = "Total power      : 1.00e-03 W\n"
    power = power_basis(subject, sdc, spef, [], log=power_log)
    inputs = {str(p): E.digest(p) for p in (subject, tech, sdc, spef)}
    record = {"schema": E.SCHEMA, "expected_nets": ["supply", "return"],
              "subject_def": "route.def", "def_sha256": E.digest(subject),
              "source_model": "PSM default sources", "power_basis": power,
              "pdk_name": entry["name"], "limits": prov,
              "invocation": {"image_id": "sha256:" + "b" * 64,
                             "native_rc": 0, "inputs_unchanged": True,
                             "tool_version": "test-version", "tool_binary_sha256": "c" * 64,
                             "started_ns": time.time_ns(), "command": command,
                             "tcl_file": "session.tcl", "tech_lef": str(tech),
                             "tool_inputs": inputs, "required_input_paths": list(inputs),
                             "inputs": E.snapshot(folder, [folder / "session.tcl", folder / "em_openroad_limits.txt", tech])}}
    skipped = status in ("NO_AREA", "NO_LIMIT")
    verdict = "FAIL" if status == "VIOLATED" else "NOT_MEASURED" if skipped else "PASS"
    import hashlib
    log = ("EM_TOOL_VERSION test-version\nEM_TOOL_BINARY_SHA256 " + "c" * 64 + "\n"
           + "EM_TOOL_COMMAND_SHA256 " + hashlib.sha256(command.encode()).hexdigest() + "\n"
           + "EM_TOOL_TCL_SHA256 " + E.digest(folder / "session.tcl") + "\n")
    hashes = "".join(f"EM_TOOL_INPUT_SHA256 {sha}  {path}\n" for path, sha in inputs.items())
    log += hashes + power_log
    for net in record["expected_nets"]:
        report = folder / f"em_openroad_density_{net}.csv"
        with report.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Layer", "Status", "Cuts", "Basis", "Ratio", "Jlimit(A/um^2)"])
            w.writerow([layer, status, cuts, basis, 1.2 if status == "VIOLATED" else 0.5, rule["current_A"]])
        (folder / f"em_segments_{net}.csv").write_text(f"Layer,Current\n{layer},0.0001\n")
        log += (f"=== EM_TOOL_BEGIN {net} ===\nNet : {net}\n"
                f"Segments checked : {int(status != 'NO_AREA')}\n"
                f"With J-limit : {int(not skipped)}\nVias judged per cut: {int(basis == 'PER_CUT')}\n"
                f"No J-limit (skipped): {int(status == 'NO_LIMIT')}\n"
                f"No area (skipped) : {int(status == 'NO_AREA')}\n"
                f"Violations : {int(status == 'VIOLATED')}\nVerdict : {verdict}\n"
                f"EM_TOOL_OK {net}\n=== EM_TOOL_END {net} ===\n")
    log += hashes
    (folder / "ir_em.log").write_text(log)
    record["invocation"]["outputs"] = E.snapshot(folder, list(folder.glob("*.csv")) + [folder / "ir_em.log"])
    (folder / "em_openroad_density.json").write_text(json.dumps(record))
    return folder, record, subject


@pytest.mark.parametrize("status,basis,want", [("OK", "PER_CUT", "PASS"),
    ("VIOLATED", "PER_CUT", "FAIL"), ("NO_AREA", "NO_AREA", "NOT_MEASURED"),
    ("NO_LIMIT", "NO_LIMIT", "NOT_MEASURED")])
def test_tool_verdict_and_skips(tmp_path, status, basis, want):
    folder, record, subject = evidence(tmp_path, status, basis)
    result = E.audit(folder, record, subject)
    assert result["verdict"] == want, result
    assert result["nets"]["supply"]["rows"] == 1


@pytest.mark.parametrize("mutation", ["image", "native_rc", "input_changed", "stale",
    "missing_return", "missing_columns", "coverage", "cuts", "limit", "log_verdict",
    "report_tampered", "def_tampered", "log_identity", "provenance", "zero_rows"])
def test_incomplete_or_changed_evidence_never_passes(tmp_path, mutation):
    folder, record, subject = evidence(tmp_path)
    inv = record["invocation"]
    report = folder / "em_openroad_density_supply.csv"
    log = folder / "ir_em.log"
    if mutation == "image": inv["image_id"] = "unknown"
    elif mutation == "native_rc": inv["native_rc"] = 1
    elif mutation == "input_changed": inv["inputs_unchanged"] = False
    elif mutation == "stale": inv["started_ns"] = time.time_ns() + 2 * 10**9
    elif mutation == "missing_return": record["expected_nets"] = ["supply"]
    elif mutation == "missing_columns": report.write_text(report.read_text().replace("Cuts", "OldCuts"))
    elif mutation == "coverage": (folder / "em_segments_supply.csv").write_text("header\nrow\nrow\n")
    elif mutation == "cuts": report.write_text(report.read_text().replace("OK,2,", "OK,0,"))
    elif mutation == "limit": report.write_text(report.read_text().replace("0.00018", "0.18"))
    elif mutation == "log_verdict": log.write_text(log.read_text().replace("Verdict : PASS", "Verdict : FAIL"))
    elif mutation == "report_tampered": report.write_text("destroyed\n")
    elif mutation == "def_tampered": subject.write_text(subject.read_text() + "changed")
    elif mutation == "log_identity": inv["tool_version"] = "different-version"
    elif mutation == "provenance": record["limits"][next(iter(record["limits"]))].pop("source_sha256")
    elif mutation == "zero_rows": report.write_text(report.read_text().splitlines()[0] + "\n")
    if mutation not in ("report_tampered", "def_tampered"):
        inv["outputs"] = E.snapshot(folder, list(folder.glob("*.csv")) + [log])
    assert E.audit(folder, record, subject)["verdict"] == "NOT_MEASURED"


def test_missing_official_capability_never_passes(tmp_path):
    folder, record, subject = evidence(tmp_path)
    record["schema"] = "legacy"
    assert E.audit(folder, record, subject)["verdict"] == "NOT_MEASURED"


@pytest.mark.parametrize("status,basis,want", [("OK", "PER_CUT", "PASS"),
    ("VIOLATED", "PER_CUT", "FAIL"), ("NO_AREA", "NO_AREA", "INCOMPLETE")])
def test_authority_consumes_tool_in_dual_mode(tmp_path, monkeypatch, status, basis, want):
    evidence(tmp_path, status, basis)
    switch = tmp_path / "phase3/librelane_switch.json"
    switch.parent.mkdir()
    switch.write_text(json.dumps({"schema": "librelane_switch/1", "steps": {"25": "dual"}}))
    monkeypatch.setattr(A, "jmax_tier", lambda *args: {"verdict": "PASS"})
    monkeypatch.setattr(A, "read_peaks", lambda *args: [])
    monkeypatch.setattr(A, "read_supply_authority", lambda *args: [])
    verdict, _ = A.evaluate(tmp_path, None, None, 0.1)
    assert verdict == want


def test_overlay_authority_is_consumed_from_real_registry():
    from _hostpaths import repo_path
    registry = repo_path("vibe-ic-marketplace/plugins/vibe-ic/programs/pdk_registry.json")
    entry = next(e for e in json.loads(registry.read_text())["pdks"] if e.get("em_limits"))
    name, rule = next(iter(entry["em_limits"]["per_cut"].items()))
    rows, prov = E.limits(entry["name"], f"LAYER {name}\n TYPE CUT ;\nEND {name}\n", {}, 0.1, registry)
    assert rows == [f"{name} per_cut {rule['current_A']:.12g}"]
    assert prov[name]["source_sha256"] == rule["source_sha256"]


def test_missing_via_overlay_gets_no_invented_limit(tmp_path):
    registry = tmp_path / "registry.json"
    registry.write_text('{"pdks": [{"name": "neutral"}]}')
    rows, prov = E.limits("neutral", "LAYER cut TYPE CUT ; END cut", {}, 0.1, registry)
    assert rows == [] and prov == {}


@pytest.mark.parametrize("mutation", ["complete", "unread", "sdc", "spef", "command", "trusted_limit"])
def test_independent_review_challenges_are_refused(tmp_path, mutation):
    folder, record, subject = evidence(tmp_path)
    if mutation == "complete":
        record["power_basis"]["complete"] = False
    elif mutation == "unread":
        log = folder / "ir_em.log"
        log.write_text(log.read_text() + "IR_BASIS_SDC_UNREAD: failure\n")
        record["invocation"]["outputs"]["ir_em.log"] = E.digest(log)
    elif mutation in ("sdc", "spef"):
        Path(record["power_basis"][mutation]).write_text("changed after measurement\n")
    elif mutation == "command":
        record["invocation"]["command"] = "true"
    else:
        layer = next(iter(record["limits"]))
        record["limits"][layer]["limit"] *= 1000
        path = folder / "em_openroad_limits.txt"
        path.write_text(path.read_text().replace("0.00018", "0.18"))
        record["invocation"]["inputs"][path.name] = E.digest(path)
        for path in folder.glob("em_openroad_density_*.csv"):
            path.write_text(path.read_text().replace("0.00018", "0.18"))
            record["invocation"]["outputs"][path.name] = E.digest(path)
    result = E.audit(folder, record, subject)
    assert result["verdict"] == "NOT_MEASURED", result


def test_ab_reads_fresh_tool_rows_instead_of_cached_summary(tmp_path):
    folder, record, _ = evidence(tmp_path)
    record.update(E.audit_project(tmp_path))
    for counts in record["nets"].values():
        counts["worst_ratio"] = 0.9
    (folder / "em_openroad_density.json").write_text(json.dumps(record))
    (folder / "em.json").write_text(json.dumps({"subject_def_sha256": record["def_sha256"]}))
    (folder / "em_current_authority.json").write_text(json.dumps({"retained_jmax_screen": {
        "verdict": "PASS", "offender_count": 0, "summary": {"worst_utilization": 0.9}}}))
    import em_current_density_check as retained
    retained.emit_openroad_ab(tmp_path, [])
    result = json.loads((folder / "em_openroad_ab.json").read_text())
    assert result["tool_worst_utilization"] == 0.5
    assert result["utilization_agrees"] is False
