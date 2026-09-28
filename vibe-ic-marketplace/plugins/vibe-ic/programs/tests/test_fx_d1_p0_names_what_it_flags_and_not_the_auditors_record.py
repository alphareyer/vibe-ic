"""P0's external-storage gate: name what it flags, and do not judge the
auditor's own record (lane fxlvs, task D1).

MEASURED on the spm core integration run (lane fxspm1, 8HD-4, image 0.3.83).
The run's only FAIL was stage 1's P0 sub-gate `project_outputs_in_tree_check`:

    [FAIL] project_outputs_in_tree_check: 1 blocking external-storage
    reference(s) (1 live, 0 dangling, 0 outside-root) — this is what the gate
    exits 1 on: reports/audit/phase23_completion_audit.json:

in all 14 stage-1 passes, while the FINAL audit carried no volatile path and
the whole-run P0 was PASS.

TWO DEFECTS.
  1. The path is not named anywhere. The deciding line is capped at 200
     characters (#2084); a long volatile path does not fit, and the P0 record
     keeps only that line. The gate now also writes its flagged references IN
     FULL to a `--json` report, and the umbrella publishes them as the record's
     `evidence.flagged`.
  2. The root cause, found by capturing every version of the canonical audit
     during a whole-flow audit of a copy of that run: a scoped pass the auditor
     runs as a STEP GATE (`stageN_compliance`) writes the canonical audit, and
     its `command_argv` names the auditor's private receipt scratch
     (`/tmp/gate_receipt_<rand>/stageN_compliance.json`), live until the
     auditor exits. Stage 1's P0 read that intermediate. The flow YAML gives the
     audit document to the AUDIT and to no step, and every version a P0 can see
     is superseded by the audit written after P0 finishes -- so P0 tells the
     gate which record is the auditor's, and the gate discloses that it did not
     judge it. Run standalone, the document is judged like any other.

Drives the real P0 umbrella (`_run_structural_rtl_gates`, scoped to this one
gate) and the real gate program. chip-AGNOSTIC: synthetic tree.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))
import flow_compliance_check as F  # noqa: E402

GATE = "project_outputs_in_tree_check"
CHECK = PROGRAMS / f"{GATE}.py"
AUDIT_REL = "reports/audit/phase23_completion_audit.json"


@pytest.fixture()
def live_receipt():
    """The auditor's private receipt scratch, live as it is during the audit."""
    d = Path(tempfile.mkdtemp(prefix="gate_receipt_"))
    f = d / "stage2_compliance.json"
    f.write_text("{}")
    yield f
    shutil.rmtree(d, ignore_errors=True)


def _project(tmp_path: Path, *, audit_argv_json: Path | None = None,
             cited: Path | None = None) -> Path:
    p = tmp_path / "proj"
    (p / "phase2/stage1/rtl").mkdir(parents=True)
    (p / "phase2/stage1/rtl/core.v").write_text("module core(); endmodule\n")
    (p / "reports").mkdir(parents=True, exist_ok=True)
    (p / "reports/a_declared_output.json").write_text(json.dumps(
        {"verdict": "PASS", **({"artefact": str(cited)} if cited else {})}))
    if audit_argv_json is not None:
        (p / "reports/audit").mkdir(parents=True, exist_ok=True)
        # What a nested `stage2_compliance` pass writes over the canonical
        # audit while the outer audit runs it as a step gate.
        (p / AUDIT_REL).write_text(json.dumps({
            "verdict": "NOT_MEASURED",
            "scope": {"stage": "stage2", "whole_flow": False},
            "command_argv": ["flow_compliance_check.py",
                             str(p), "--stage", "stage2",
                             "--json", str(audit_argv_json)]}))
    return p


def _p0(project: Path, monkeypatch) -> dict:
    monkeypatch.setattr(F, "_STRUCTURAL_RTL_GATES", (GATE,))
    records: list = []
    F._run_structural_rtl_gates(project, records_out=records)
    assert len(records) == 1, records
    return records[0]


# ── the root cause: P0 judged the auditor's own intermediate record ──────
def test_p0_does_not_fail_the_run_on_the_auditors_intermediate_record(
        tmp_path, monkeypatch, live_receipt):
    project = _project(tmp_path, audit_argv_json=live_receipt)
    record = _p0(project, monkeypatch)
    assert record["verdict"] == "PASS", record


def test_standalone_the_audit_document_is_still_judged(tmp_path, live_receipt):
    """Not run by its auditor, the document is a declaration file like any
    other: the same tree still FAILs, naming the audit."""
    project = _project(tmp_path, audit_argv_json=live_receipt)
    r = subprocess.run([sys.executable, str(CHECK), str(project)],
                       capture_output=True, text=True)
    assert r.returncode == 1, r.stdout
    assert AUDIT_REL in r.stdout


def test_the_record_not_judged_is_disclosed(tmp_path, live_receipt):
    project = _project(tmp_path, audit_argv_json=live_receipt)
    r = subprocess.run([sys.executable, str(CHECK), str(project),
                        "--auditor-record", AUDIT_REL],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
    info = [ln for ln in r.stdout.splitlines() if "not judged" in ln]
    assert info and AUDIT_REL in info[0], r.stdout


# ── the disclosure: the record names the full path ────────────────────────
def test_the_p0_record_names_the_full_path_it_failed_on(tmp_path, monkeypatch):
    long_dir = Path(tempfile.mkdtemp(prefix="gate_receipt_"))
    live = long_dir / ("a_volatile_artefact_whose_name_does_not_fit_the_"
                       "deciding_line_of_the_gate.json")
    live.write_text("{}")
    try:
        project = _project(tmp_path, cited=live)
        record = _p0(project, monkeypatch)
        assert record["verdict"] == "FAIL", record
        assert str(live) not in record["message"], "fits after all: no test"
        assert record["evidence"].get("flagged") == [
            f"reports/a_declared_output.json → {live} (live)"], record
    finally:
        shutil.rmtree(long_dir, ignore_errors=True)


def test_the_report_carries_every_flagged_reference(tmp_path, live_receipt):
    gone = Path("/tmp") / f"fx_d1_swept_{tmp_path.name}" / "x.json"
    project = _project(tmp_path, cited=live_receipt)
    (project / "reports/b.json").write_text(json.dumps({"out": str(gone)}))
    out = tmp_path / "report.json"
    r = subprocess.run([sys.executable, str(CHECK), str(project),
                        "--json", str(out)], capture_output=True, text=True)
    assert r.returncode == 1, r.stdout
    doc = json.loads(out.read_text())
    assert doc["verdict"] == "FAIL" and doc["exit_code"] == 1
    assert {(x["path"], x["class"]) for x in doc["flagged"]} == {
        (str(live_receipt), "live"), (str(gone), "dangling")}, doc
