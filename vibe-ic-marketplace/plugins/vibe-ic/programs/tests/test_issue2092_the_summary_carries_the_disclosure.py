#!/usr/bin/env python3
"""vibe-ic#2092 — the summary a reader reads carries what the run disclosed.

MEASURED DEFECT
===============
On the icaes opentitan_aes run (8HD-4),
`reports/audit/flow_compliance_check.log:215` printed

    Step-level gates (informational, not gating --strict-structural):
    3 step(s) FAIL/MISSING

beside `Overall: PASS_WITH_WAIVERS`, and `reports/final_summary.md` for the
SAME run contains the word "informational" ZERO times. A disclosure a run
makes and its own summary does not repeat is a disclosure to nobody.

E4 asks for two landing places — a row in the step table (owned by
`test_issue2092_a_disclosure_reaches_the_step_table.py`) and a line in the
orchestrator summary, which is this file. E5's `reconciled: false` lands here
too: when the producer's own equations over its own numbers are false, every
count rendered under **Verdict** is a count it has withdrawn, and a reader
must not have to open the JSON to learn that.
"""
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import final_report_generate as F  # noqa: E402

_STEPS = [
    {"id": "1", "name": "Spec-to-RTL", "stage": "stage1", "status": "PASS"},
    {"id": "2", "name": "RTL validation", "stage": "stage1",
     "status": "MISSING"},
    {"id": "4", "name": "Simulation", "stage": "stage1", "status": "FAIL"},
]


def _audit(**over):
    a = {
        "schema_version": 1,
        "verdict": "PASS_WITH_WAIVERS",
        "run_status": "PASS_WITH_WAIVERS",
        "step_counts": {"PASS": 1, "MISSING": 1, "FAIL": 1},
        "steps": [dict(s) for s in _STEPS],
        "informational_disclosures": {
            "disclosures": [{
                "kind": "step_level_gates_not_gating_strict_structural",
                "headline": ("Step-level gates (informational, not gating "
                             "--strict-structural): 3 step(s) FAIL/MISSING"),
                "count": 3,
                "step_ids": ["2", "4", "5"],
                "lines": [],
                "gating": False,
                "steps_missing_from_the_step_table": [],
            }],
            "every_disclosed_step_is_a_row": True,
            "steps_missing_from_the_step_table": [],
        },
        "reconciliation": {"reconciled": True, "broken": [],
                           "equations_checked": ["E1", "E2", "E3", "E4"]},
    }
    a.update(over)
    return a


def _render(monkeypatch, tmp_path: Path, audit: dict) -> str:
    project = tmp_path / "project"
    audit_path = (project / "reports" / "audit" /
                  "phase23_completion_audit.json")
    flow = tmp_path / "flow.yaml"
    flow.write_text(yaml.safe_dump({
        "version": 2,
        "flow_name": "issue2092_synthetic",
        "total_steps": len(audit["steps"]),
        "analog_steps": 0,
        "stages": [{"id": "stage1", "name": "synthetic stage"}],
        "steps": [{"id": r["id"], "name": r["name"], "stage": "stage1"}
                  for r in audit["steps"]],
    }, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(F, "FLOW_YAML", flow)

    def _fake_run_audit(*_a, **_k):
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text(json.dumps(audit, indent=2), encoding="utf-8")
        return ("=== synthetic ===\nProject: /p\nFlow def: /f\n"
                "Steps: 3 total\n  PASS=1  FAIL=1  MISSING=1\n"
                "Overall: PASS_WITH_WAIVERS\n"), audit["run_status"]

    monkeypatch.setattr(F, "_run_audit", _fake_run_audit)
    return F._render(project, run_audit=True)


def test_the_disclosure_reaches_the_summary(monkeypatch, tmp_path):
    md = _render(monkeypatch, tmp_path, _audit())
    assert "Reported, NOT gating" in md
    assert "3 step(s) FAIL/MISSING" in md
    for sid in ("`2`", "`4`", "`5`"):
        assert sid in md, sid
    assert "did NOT count" in md


def test_a_run_with_nothing_disclosed_gains_no_line(monkeypatch, tmp_path):
    """No disclosure, no sentence. A banner that is always there is a banner
    nobody reads."""
    md = _render(monkeypatch, tmp_path, _audit(
        informational_disclosures={"disclosures": [],
                                   "every_disclosed_step_is_a_row": None,
                                   "steps_missing_from_the_step_table": []}))
    assert "Reported, NOT gating" not in md


def test_a_report_that_does_not_reconcile_says_so_under_verdict(
        monkeypatch, tmp_path):
    md = _render(monkeypatch, tmp_path, _audit(
        reconciliation={
            "reconciled": False,
            "equations_checked": ["E1", "E2", "E3", "E4"],
            "broken": [{"id": "E2",
                        "equation": "invoked == every named bucket",
                        "detail": "invoked_gate_count 246 != PASS 186 = 186"}],
        }))
    assert "The audit does not reconcile" in md
    assert "`[E2]`" in md
    assert "invoked_gate_count 246 != PASS 186 = 186" in md
    assert "Do not quote the counts below" in md
    # It lands under Verdict, ahead of the counts it is about.
    assert md.index("The audit does not reconcile") < md.index("- PASS=")


def test_a_reconciling_report_gains_no_banner(monkeypatch, tmp_path):
    md = _render(monkeypatch, tmp_path, _audit())
    assert "The audit does not reconcile" not in md


def test_a_disclosed_step_that_is_in_no_row_is_flagged_in_the_summary(
        monkeypatch, tmp_path):
    a = _audit()
    a["informational_disclosures"]["disclosures"][0][
        "steps_missing_from_the_step_table"] = ["5"]
    a["informational_disclosures"]["steps_missing_from_the_step_table"] = ["5"]
    a["informational_disclosures"]["every_disclosed_step_is_a_row"] = False
    md = _render(monkeypatch, tmp_path, a)
    assert "in NO row of the" in md
    assert "['5']" in md


def test_an_audit_written_before_these_fields_renders_unchanged(
        monkeypatch, tmp_path):
    """Every audit artefact tracked today lacks both blocks. The renderer must
    not gain a banner, or a traceback, over their absence."""
    a = _audit()
    a.pop("informational_disclosures")
    a.pop("reconciliation")
    md = _render(monkeypatch, tmp_path, a)
    assert "Reported, NOT gating" not in md
    assert "The audit does not reconcile" not in md
    assert "- PASS=" in md
