#!/usr/bin/env python3
"""E5 of vibe-ic#2092 — a frame canary in front of the numbers.

MEASURED DEFECT
===============
Every number in `phase23_completion_audit.json` is produced by a different
code path, and nothing checked that they agree. Measured on ONE artefact
(lane icaes, opentitan_aes, 8HD-4, v1.17.38):

    verdict FAIL      failed_gates []       failed_gate_count 0
    registered 246    invoked 246           passed 186
    tally_delta       prior 13 steps  vs  current 69 steps

Each of those is internally consistent inside the path that produced it. None
of them was ever checked against the artefact it was written into. The
`reproduce` reader in this lane evaluates the four equations against that file
and all four are false.

WHAT IS ENFORCED
================
`audit_reconciliation` runs over the FINISHED dict, immediately before it is
written. When an equation is false the report is STILL WRITTEN — destroying it
would destroy the evidence of the break — carrying `reconciled: false` and the
broken equation by ID and by sentence, and `main` exits non-zero.

`run_status` and `overall` are untouched: the gate decisions are exactly what
they were. What is withdrawn is this artefact's certification of its own
arithmetic, and the printed line says so.

NOT_MEASURED IS NOT A BREAK, and it is not a pass either. An equation this
scope did not ask is listed under `not_measured` with the reason.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import flow_compliance_check as F  # noqa: E402

AUDIT_REL = Path("reports") / "audit" / "phase23_completion_audit.json"


def _audit(project: Path):
    return json.loads((project / AUDIT_REL).read_text(encoding="utf-8"))


# ── the pure function, over the shapes the equations are about ───────────


def _ok_audit(**over):
    a = {
        "run_status": "FAIL",
        "verdict": "FAIL",
        "failed_gates": ["g_check"],
        "failed_gate_count": 1,
        "steps": [{"id": 1, "name": "s", "status": "FAIL"}],
        "step_counts": {"FAIL": 1},
        "verdict_causes": {"names_its_cause": True, "note": ""},
        "gate_population_equation": {"holds": True, "breaks": []},
        "tally_delta": {"prior": None},
        "informational_disclosures": {"every_disclosed_step_is_a_row": True,
                                      "steps_missing_from_the_step_table": []},
    }
    a.update(over)
    return a


def test_a_clean_artefact_reconciles():
    r = F.audit_reconciliation(_ok_audit())
    assert r["reconciled"] is True, r["broken"]
    assert r["broken"] == []
    assert set(r["equations_checked"]) == set(F.AUDIT_EQUATIONS)


def test_each_equation_can_be_broken_on_its_own_and_is_named():
    for over, eid, needle in (
            ({"failed_gate_count": 9}, "E1", "failed_gate_count 9"),
            ({"step_counts": {"FAIL": 7}}, "E1", "tally 7 vs 1 row"),
            ({"verdict_causes": {"names_its_cause": False, "note": ""}},
             "E1", "names NO cause"),
            ({"gate_population_equation":
              {"holds": False, "breaks": ["invoked 246 != 186"]}},
             "E2", "invoked 246 != 186"),
            ({"tally_delta": {"prior": {"tally": {}},
                              "prior_eligibility": {
                                  "comparable": False,
                                  "reasons": ["different populations"]}}},
             "E3", "different populations"),
            ({"informational_disclosures": {
                "every_disclosed_step_is_a_row": False,
                "steps_missing_from_the_step_table": ["4", "5"]}},
             "E4", "['4', '5']")):
        r = F.audit_reconciliation(_ok_audit(**over))
        assert r["reconciled"] is False, over
        ids = [b["id"] for b in r["broken"]]
        assert eid in ids, (over, ids)
        assert any(needle in b["detail"] for b in r["broken"]), \
            (over, r["broken"])


def test_an_unasked_equation_is_not_measured_and_is_not_a_break():
    a = _ok_audit()
    for k in ("verdict_causes", "gate_population_equation",
              "tally_delta", "informational_disclosures"):
        a.pop(k)
    r = F.audit_reconciliation(a)
    assert r["reconciled"] is True
    assert {c["id"] for c in r["not_measured"]} == {"E1", "E2", "E3", "E4"}
    assert "NOT_MEASURED is not a pass" in r["note"]


def test_a_green_run_still_has_its_arithmetic_checked():
    """The canary is about the NUMBERS, not about the verdict word. A PASS
    whose counts do not add up is exactly as unquotable as a FAIL's."""
    r = F.audit_reconciliation(_ok_audit(
        run_status="PASS", verdict="PASS", failed_gates=[],
        failed_gate_count=3, steps=[], step_counts={},
        verdict_causes={"names_its_cause": None, "note": ""}))
    assert r["reconciled"] is False
    assert [b["id"] for b in r["broken"]] == ["E1"]


# ── PROVE BY RUN: the break reaches the report and the exit code ─────────


def test_a_run_whose_equations_hold_writes_reconciled_true(tmp_path):
    rc = F.main([str(tmp_path), "--phase", "all"])
    a = _audit(tmp_path)
    assert a["reconciliation"]["reconciled"] is True, \
        a["reconciliation"]["broken"]
    # An empty tree is red for its own reasons; the canary added nothing.
    assert rc == 1
    assert a["run_status"] == "FAIL"


def test_an_injected_count_drift_is_written_and_exits_non_zero(
        tmp_path, monkeypatch, capsys):
    """PROVE BY RUN. `p0_gate_census` is made to report a population the
    published counts are not the projection of — the exact drift E2 exists
    for — and the whole path is measured: the report is WRITTEN, it carries
    `reconciled: false` naming E2, `run_status` is untouched, and `main`
    exits non-zero."""
    monkeypatch.setattr(
        F, "p0_gate_census",
        lambda records: {"registered": 200, "by_verdict": {"PASS": 200},
                         "published_total": 200, "unaccounted": 0,
                         "buckets_disagreeing_with_records": [],
                         "closes": True, "invoked_is_not_coverage": "x"})
    rc = F.main([str(tmp_path), "--phase", "all"])
    a = _audit(tmp_path)
    r = a["reconciliation"]
    assert r["reconciled"] is False
    assert [b["id"] for b in r["broken"]] == ["E2"]
    # The injected census claims 200 records; the run's own counts project
    # none of them. Keyed on the INJECTED number, which is the drift, not on
    # the registry size — the registry is disclosed, never asserted.
    assert "200" in r["broken"][0]["detail"]
    assert "do not partition the records they project" in \
        r["broken"][0]["detail"]
    assert rc == 1
    # THE GATE DECISIONS ARE UNTOUCHED — only the certification is withdrawn.
    assert a["run_status"] == "FAIL"
    assert a["step_counts"] == _audit(tmp_path)["step_counts"]
    out = capsys.readouterr().out
    assert "THIS REPORT DOES NOT RECONCILE" in out
    assert "do not quote its counts" in out
    assert "[E2]" in out


def test_the_report_is_written_rather_than_withheld_on_a_break(
        tmp_path, monkeypatch):
    """Refusing to WRITE would destroy the evidence of the break. The refusal
    is a refusal to CERTIFY, and it is recorded in the file itself."""
    monkeypatch.setattr(
        F, "p0_gate_census",
        lambda records: {"registered": 1, "by_verdict": {"PASS": 1},
                         "published_total": 1, "unaccounted": 0,
                         "buckets_disagreeing_with_records": [],
                         "closes": True, "invoked_is_not_coverage": "x"})
    F.main([str(tmp_path), "--phase", "all"])
    assert (tmp_path / AUDIT_REL).is_file()
    a = _audit(tmp_path)
    assert a["reconciliation"]["reconciled"] is False
    assert a["reconciliation"]["broken"][0]["equation"]
