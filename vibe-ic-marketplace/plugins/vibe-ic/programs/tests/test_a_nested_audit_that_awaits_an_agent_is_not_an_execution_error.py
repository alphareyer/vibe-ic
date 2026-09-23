"""R-0915-169 — a NESTED audit that is only waiting on an agent pass is not an
execution error one level up.

MEASURED on subservient run2 (lane ictier1, main f757abef7, a read-only copy):
step 2's advisory clause `flow_compliance_check . --stage-id stage_phase1
--strict ...` exits 1 and its own report says, in typed fields,

    overall            NOT_MEASURED
    steps[D1]          status NOT_MEASURED, reason_class awaiting_agent_pass
    steps[0.5ic]       status PASS
    blockers[D1]       basis awaiting-agent-pass
    gate ledger        phase1_expert_parse_track . --check-report  exit_code 4

and nothing in it errored. `_advisory_execution_record` found no report-level
`reason_class`, inferred one from prose, matched no recogniser, fail-closed to
EXECUTION_ERROR, and step 2 was published NOT_MEASURED / execution_error.

THE RULING. In the ADVISORY clause branch a nested flow-compliance report is read
by its own typed rows: `overall` NOT_MEASURED and EVERY row that is neither PASS
nor a declared N/A is NOT_MEASURED with reason_class awaiting_agent_pass (or its
blocker's basis is awaiting-agent-pass) -> the clause carries the AWAITING hint
and the step reads NOT_MEASURED / awaiting_agent_pass, naming the nested step(s)
and the gate that emitted the hand-off. EVERY OTHER MIX STAYS AS TODAY: a FAIL
row blocks; any other NOT_MEASURED class, an unreadable report or a report some
other program wrote reads EXECUTION_ERROR. The verdict never changes; only the
reason class does.

The nested audit is played by a stand-in program on disk, named by ABSOLUTE path
(which `_resolve_program_cmd` honours), that writes the report the real one
writes and exits 1 -- so the real `check_step` classifies it end to end.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
PROGRAMS = PLUGIN / "programs"
sys.path.insert(0, str(PROGRAMS))

import flow_compliance_check as FCC                          # noqa: E402

_T = FCC._T
_NM = _T.Verdict.NOT_MEASURED.value
_FAIL = _T.Verdict.FAIL.value
_AWAIT = _T.ReasonClass.AWAITING_AGENT_PASS.value
_EXEC = _T.ReasonClass.EXECUTION_ERROR.value

_NESTED = '''import json, sys
from pathlib import Path
REPORT = {report!r}
argv = sys.argv[1:]
if REPORT is not None and "--json" in argv:
    p = Path(argv[argv.index("--json") + 1])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(REPORT))
print("=== Vibe-IC phase1_phase2_phase3 stage compliance ===")
print("Overall: " + (REPORT or {{}}).get("overall", "NOT_MEASURED")
      + "  (strict=True)")
sys.exit(1)
'''

_EPT = "phase1_expert_parse_track . --check-report"


def _row(sid, status, reason_class=None):
    return {"id": sid, "name": f"step {sid}", "stage": "stage_phase1",
            "status": status, "reason_class": reason_class}


def _blocker(sid, status, basis):
    return {"step_id": sid, "status": status, "basis": basis}


def _report(rows, blockers=None, overall=_NM, program="flow_compliance_check",
            ledger=None):
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    counts.setdefault("PASS", 0)
    counts.setdefault("FAIL", 0)
    return {"program": program, "overall": overall, "counts": counts,
            "steps": rows,
            "blockers": blockers if blockers is not None else [
                _blocker(r["id"], r["status"],
                         "awaiting-agent-pass"
                         if r.get("reason_class") == _AWAIT else "other")
                for r in rows if r["status"] not in ("PASS", "NOT_APPLICABLE")],
            "gate_execution_ledger": ledger if ledger is not None else [
                {"gate": "phase1_expert_parse_track", "cmd": _EPT, "rc": 0,
                 "verdict": "PASS", "reason_class": None, "exit_code": 4},
                {"gate": "phase1_all_l_docs_present_check",
                 "cmd": "phase1_all_l_docs_present_check .", "rc": 0,
                 "verdict": "PASS", "reason_class": None, "exit_code": 0}]}


#: The recorded run2 shape.
_RUN2 = [_row("D1", _NM, _AWAIT), _row("0.5ic", "PASS")]


def _nested(tmp_path: Path, report) -> str:
    p = tmp_path / "gates" / "flow_compliance_check.py"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_NESTED.format(report=report))
    return str(p)


def _check(tmp_path: Path, report, stage_id="stage_phase1", step_id=2):
    project = tmp_path / "proj"
    project.mkdir(exist_ok=True)
    prog = _nested(tmp_path, report)
    cmd = (f"{prog} . --stage-id {stage_id} --strict "
           f"--json reports/gates/{stage_id}_compliance.json")
    step = {"id": step_id, "name": "the step under audit", "stage": "stage1",
            "gate": {"all_of": [{"advisory_program_exit_zero": cmd}]}}
    return FCC.check_step(project, step, {})


def _records(r):
    """The advisory record as the step publishes it: one `GATE EVIDENCE:` line
    of `key=value` fields per clause."""
    out = []
    for x in r.reasons:
        if x.startswith("GATE EVIDENCE: "):
            fields = x[len("GATE EVIDENCE: "):].split()
            out.append(dict(f.split("=", 1) for f in fields[1:] if "=" in f))
    return out


def _text(r):
    return "\n".join(r.reasons)


# ── the recorded shape: RED on main ────────────────────────────────────────

def test_the_run2_shape_reads_awaiting_not_execution_error(tmp_path):
    r = _check(tmp_path, _report(_RUN2))
    assert (r.status, r.reason_class) == (_NM, _AWAIT), (
        r.status, r.reason_class, r.reasons)
    assert "EXECUTION_ERROR" not in _text(r), r.reasons


def test_the_row_names_the_nested_step_and_the_gate_that_handed_off(tmp_path):
    r = _check(tmp_path, _report(_RUN2))
    awaiting = [x for x in r.reasons if x.startswith("AWAITING an agent pass")]
    assert awaiting, r.reasons
    assert any("D1" in x for x in awaiting), awaiting
    assert any(_EPT in x for x in awaiting), awaiting
    assert not any("0.5ic" in x for x in awaiting), awaiting


def test_the_gate_record_no_longer_says_execution_error(tmp_path):
    """The GATE EVIDENCE line is built from this record; it must not keep the
    false class the step row dropped."""
    r = _check(tmp_path, _report(_RUN2))
    recs = _records(r)
    assert recs and all(x["reason_class"] != "EXECUTION_ERROR"
                        for x in recs), recs
    assert all(x["enforcement"] == "DISCLOSED_INCOMPLETE" for x in recs), recs


def test_the_blocker_basis_alone_is_enough(tmp_path):
    """The ruling's other typed field: a row whose own reason_class is absent
    but whose blocker states awaiting-agent-pass."""
    rows = [_row("D1", _NM, None), _row("0.5ic", "PASS")]
    r = _check(tmp_path, _report(
        rows, blockers=[_blocker("D1", _NM, "awaiting-agent-pass")]))
    assert (r.status, r.reason_class) == (_NM, _AWAIT), (
        r.status, r.reason_class, r.reasons)


def test_a_declared_na_sibling_does_not_stop_the_reading(tmp_path):
    rows = _RUN2 + [_row("0.6", "NOT_APPLICABLE", "design_declared_na")]
    r = _check(tmp_path, _report(rows))
    assert (r.status, r.reason_class) == (_NM, _AWAIT), (
        r.status, r.reason_class, r.reasons)


def test_step_14s_stage_analog_clause_reads_the_same(tmp_path):
    rows = [_row("A1", _NM, _AWAIT), _row("A2", "PASS")]
    r = _check(tmp_path, _report(rows), stage_id="stage_analog", step_id=14)
    assert (r.status, r.reason_class) == (_NM, _AWAIT), (
        r.status, r.reason_class, r.reasons)
    assert any("A1" in x for x in r.reasons
               if x.startswith("AWAITING an agent pass")), r.reasons


# ── every other mix stays exactly as today ─────────────────────────────────

def test_awaiting_beside_another_not_measured_class_stays_fail_closed(
        tmp_path):
    rows = _RUN2 + [_row("0.7", _NM, "partial_population")]
    r = _check(tmp_path, _report(rows))
    assert (r.status, r.reason_class) == (_NM, _EXEC), (
        r.status, r.reason_class, r.reasons)


def test_awaiting_beside_a_fail_row_blocks(tmp_path):
    rows = _RUN2 + [_row("0.5ic-b", _FAIL, "gate_reached_verdict")]
    r = _check(tmp_path, _report(rows, overall=_FAIL))
    assert r.status == _FAIL, (r.status, r.reason_class, r.reasons)
    assert any(x["enforcement"] == "BLOCKING" for x in _records(r)), r.reasons
    assert not any(x.startswith("AWAITING an agent pass")
                   for x in r.reasons), r.reasons


def test_an_unreadable_nested_report_stays_execution_error(tmp_path):
    r = _check(tmp_path, None)
    assert r.status != "PASS", (r.status, r.reasons)
    assert r.reason_class != _AWAIT, (r.status, r.reason_class, r.reasons)


def test_a_report_another_program_wrote_stays_execution_error(tmp_path):
    r = _check(tmp_path, _report(_RUN2, program="some_other_check"))
    assert (r.status, r.reason_class) == (_NM, _EXEC), (
        r.status, r.reason_class, r.reasons)


def test_a_waived_row_is_not_a_declared_na(tmp_path):
    """The ruling exempts PASS and declared N/A only."""
    rows = _RUN2 + [_row("0.8", "WAIVED-DEFERRED")]
    r = _check(tmp_path, _report(rows))
    assert r.reason_class != _AWAIT, (r.status, r.reason_class, r.reasons)


def test_an_overall_that_is_not_not_measured_is_not_read(tmp_path):
    r = _check(tmp_path, _report(_RUN2, overall="PASS_WITH_WAIVERS"))
    assert r.reason_class != _AWAIT, (r.status, r.reason_class, r.reasons)


def test_a_nested_audit_with_no_awaiting_row_is_not_awaiting(tmp_path):
    rows = [_row("D1", _NM, "partial_population"), _row("0.5ic", "PASS")]
    r = _check(tmp_path, _report(rows))
    assert (r.status, r.reason_class) == (_NM, _EXEC), (
        r.status, r.reason_class, r.reasons)
