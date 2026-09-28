#!/usr/bin/env python3
"""Tests for postroute_timing_repair_status_gen.py (v1.6.36 — Step 30 repair status emitter)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parent.parent / "postroute_timing_repair_status_gen.py"


def _run(project: Path):
    return subprocess.run(
        [sys.executable, str(PROG), str(project)],
        capture_output=True, text=True,
    )


def _write_sta(project: Path, content: str):
    sta_dir = project / "phase3/stage3/sta"
    sta_dir.mkdir(parents=True, exist_ok=True)
    (sta_dir / "post_route_timing.rpt").write_text(content)


def test_emits_no_no_repair_flag_when_tns_zero(tmp_path):
    """All-MET STA → no_repair_needed.flag emitted, verdict PASS."""
    _write_sta(tmp_path, "Endpoint reset_n\nslack (MET)\nslack (MET)\n")
    r = _run(tmp_path)
    assert r.returncode == 0
    assert (tmp_path / "phase3/stage3/postroute_timing_repair/no_repair_needed.flag").is_file()
    out = json.loads(r.stdout)
    assert out["verdict"] == "PASS"
    assert out["tns_zero"] is True


def test_emits_no_no_repair_flag_when_tns_explicit_zero(tmp_path):
    """Explicit `tns 0.00` → no_repair_needed.flag emitted."""
    _write_sta(tmp_path, "report_tns\ntns 0.00\nwns 0.05\n")
    r = _run(tmp_path)
    assert r.returncode == 0
    assert (tmp_path / "phase3/stage3/postroute_timing_repair/no_repair_needed.flag").is_file()


def test_emits_repair_log_when_tns_negative(tmp_path):
    """STA with VIOLATED + no MET → repair_log.json emitted."""
    _write_sta(tmp_path, "Endpoint clk\nslack VIOLATED\n-2.0 violation\n")
    r = _run(tmp_path)
    assert r.returncode == 0
    assert (tmp_path / "phase3/stage3/postroute_timing_repair/repair_log.json").is_file()
    log = json.loads((tmp_path / "phase3/stage3/postroute_timing_repair/repair_log.json").read_text())
    assert log["verdict"] == "REPAIR_REQUIRED"


def test_vacuous_pass_when_no_sta(tmp_path):
    """No STA report → exit 2 (VACUOUS_PASS)."""
    r = _run(tmp_path)
    assert r.returncode == 2


def test_falls_back_to_pnr_sta_rpt(tmp_path):
    """No sta_dir, but pnr/sta.rpt → still parses + emits flag."""
    pnr_dir = tmp_path / "phase3/stage3/pnr"
    pnr_dir.mkdir(parents=True, exist_ok=True)
    (pnr_dir / "sta.rpt").write_text("Endpoint x\nslack (MET)\n")
    r = _run(tmp_path)
    assert r.returncode == 0
    assert (tmp_path / "phase3/stage3/postroute_timing_repair/no_repair_needed.flag").is_file()


def test_readable_sta_without_a_measurement_is_not_a_repair_diagnosis(tmp_path):
    sta = tmp_path / "phase3/stage3/sta/sta_spef_based.rpt"
    sta.parent.mkdir(parents=True)
    sta.write_text("Post-route STA report header only\n")
    repair_dir = tmp_path / "phase3/stage3/postroute_timing_repair"
    repair_dir.mkdir(parents=True)
    flag = repair_dir / "no_repair_needed.flag"
    flag.write_text("stale clean certificate\n")

    result = _run(tmp_path)
    assert result.returncode != 0
    summary = json.loads(result.stdout)
    status = json.loads((repair_dir / "measurement_not_available.json").read_text())
    assert summary["verdict"] == status["verdict"] == "NOT_MEASURED"
    assert status["timing_basis_status"] == "NOT_MEASURED"
    assert status["timing_repair_needed"] is False
    assert status["nontiming_failures"] == []
    assert "STA" in status["remediation"] and "Re-run" in status["remediation"]
    assert "non-timing sign-off domain FAILED" not in status["remediation"]
    assert not flag.exists()
    assert not (repair_dir / "repair_log.json").exists()


def test_missing_sta_revokes_old_clean_certificate_and_names_missing_sources(tmp_path):
    repair_dir = tmp_path / "phase3/stage3/postroute_timing_repair"
    repair_dir.mkdir(parents=True)
    for name in ("no_repair_needed.flag", "no_repair_summary.json", "repair_log.json"):
        (repair_dir / name).write_text("stale result\n")

    result = _run(tmp_path)
    assert result.returncode == 2
    status = json.loads((repair_dir / "measurement_not_available.json").read_text())
    assert status["verdict"] == "NOT_MEASURED"
    assert status["reason_class"] == "missing_sta_report"
    assert status["sta_source"] is None
    assert "phase3/stage3/sta/sta_spef_based.rpt" in status["sta_candidates"]
    assert "Re-run post-route STA" in status["remediation"]
    assert all(not (repair_dir / name).exists() for name in
               ("no_repair_needed.flag", "no_repair_summary.json", "repair_log.json"))

    audit = subprocess.run(
        [sys.executable, str(PROG.with_name("postroute_timing_repair_audit.py")),
         str(tmp_path), "--json", str(tmp_path / "audit.json")],
        capture_output=True, text=True)
    assert audit.returncode == 1
    report = json.loads((tmp_path / "audit.json").read_text())
    assert report["summary"]["pass"] is False
    assert "STA_REPORT_MISSING" in {f["category"] for f in report["findings"]}

    _write_sta(tmp_path, "report_tns\ntns 0.00\nwns 0.05\n")
    measured = _run(tmp_path)
    assert measured.returncode == 0
    assert json.loads(measured.stdout)["verdict"] == "PASS"
    assert not (repair_dir / "measurement_not_available.json").exists()
    assert (repair_dir / "no_repair_needed.flag").is_file()
    assert subprocess.run(
        [sys.executable, str(PROG.with_name("postroute_timing_repair_audit.py")),
         str(tmp_path)], capture_output=True, text=True).returncode == 0
