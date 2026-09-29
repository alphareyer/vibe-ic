#!/usr/bin/env python3
"""U20 (IC_BLOCKER_AUDIT §2) — an advisory NOT_MEASURED is treated like an
advisory FAIL, consistently.

MEASURED on spm v5 (IC path, deliverable DIE; 8HD-4 `spmic5/run_v5`, read-only
copies), step 7 and its advisory clause
`stage1_compliance . --json reports/phase2/gates/stage1_compliance.json`, which
both the gate module (`ENFORCEMENT: advisory`) and the canonical flow
(`advisory_program_exit_zero`) declare advisory:

  * whole-flow audit — stage1_compliance returned FAIL (rc 1): the record reads
    enforcement BLOCKING, the two-source rule honours the advisory declaration,
    and step 7 reads PASS with the refusal disclosed;
  * stage-2 scoped audit (the persisted stage2_compliance.json) — the SAME clause
    returned NOT_MEASURED: the record reads DISCLOSED_INCOMPLETE, the INCOMPLETE
    hint is emitted unconditionally, and step 7 reads NOT_MEASURED.

The less severe outcome of one advisory gate set a stricter tier than the more
severe one. Whether a two-source-advisory gate's non-green answer may move its
host step is ONE policy; it was applied to FAIL and not to NOT_MEASURED.

THE RULE: for a gate that BOTH sources declare advisory (and that declares no
non-waiverable finding), a NOT_MEASURED is recorded and disclosed exactly as a
refusal is — visible on the row, lossless in the record — and does not set the
step's tier. Every other advisory NOT_MEASURED keeps its INCOMPLETE disposition,
just as every other advisory FAIL keeps BLOCKING. Checked both ways below: for
each gate, FAIL and NOT_MEASURED land on the same side.

ROUND 2 (review wave 58). The moved clause still EXAMINED NOTHING, so it still
counts as a vacuous clause in the #901 numerator: a step whose every clause
examined nothing stays NOT_MEASURED (the round-1 test that pinned PASS for a step
whose ONLY clause returned NOT_MEASURED is replaced), and the reviewer's M1 probe is
pinned. R-0915-169's awaiting-agent reading is not pre-empted, and a NON_WAIVERABLE
declaration keeps the INCOMPLETE disposition.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parents[1]
if str(PROG) not in sys.path:
    sys.path.insert(0, str(PROG))

import flow_compliance_check as F        # noqa: E402

_T = F._T
PASS = _T.Verdict.PASS.value
FAIL = _T.Verdict.FAIL.value
NM = _T.Verdict.NOT_MEASURED.value

#: A stand-in for a nested stage audit: writes the report the real one writes
#: (`overall` + `verdict`) and exits 1, as the real one does on a non-PASS.
_NESTED = '''import json, sys
from pathlib import Path
argv = sys.argv[1:]
p = Path(argv[argv.index("--json") + 1])
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps({REPORT}))
print("Overall: " + {WORD!r} + "  (strict=True)")
sys.exit(1)
'''

_OUTCOMES = {
    "FAIL": {"overall": "FAIL", "verdict": "FAIL"},
    # the spm v5 stage-2 shape: nested NOT_MEASURED with a stated class
    "NOT_MEASURED": {"overall": "NOT_MEASURED", "verdict": "NOT_MEASURED",
                     "reason_class": "EXECUTION_ERROR"},
}


def _check(tmp_path: Path, gate: str, outcome: str):
    project = tmp_path / "proj"
    project.mkdir(parents=True, exist_ok=True)
    prog = tmp_path / "gates" / f"{gate}.py"
    prog.parent.mkdir(parents=True, exist_ok=True)
    prog.write_text(_NESTED.replace("{REPORT}", repr(_OUTCOMES[outcome]))
                    .replace("{WORD!r}", repr(outcome)))
    cmd = f"{prog} . --json reports/phase2/gates/{gate}.json"
    step = {"id": 7, "name": "constraints", "stage": "stage2",
            "gate": {"all_of": [{"advisory_program_exit_zero": cmd}]}}
    return F.check_step(project, step, {})


def test_the_gate_under_test_is_two_source_advisory():
    """The premise: stage1_compliance is advisory by BOTH declarations."""
    assert F._gate_is_two_source_advisory("stage1_compliance")
    assert not F._gate_is_two_source_advisory("probe_nested_audit")


def _pass_probe(tmp_path: Path) -> str:
    """A clause that examined something and passed."""
    probe = tmp_path / "gates" / "examines_and_passes.py"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text("print('[PASS] examined 3 files, 0 findings')\n")
    return f"{probe} ."


def _check_with_sibling(tmp_path: Path, gate: str, outcome: str):
    """The spm v5 step-7 shape: a substantive blocking sibling beside the
    advisory stage audit (there: sdc_syntax_check, pvt_matrix_check)."""
    project = tmp_path / "proj"
    project.mkdir(parents=True, exist_ok=True)
    prog = tmp_path / "gates" / f"{gate}.py"
    prog.parent.mkdir(parents=True, exist_ok=True)
    prog.write_text(_NESTED.replace("{REPORT}", repr(_OUTCOMES[outcome]))
                    .replace("{WORD!r}", repr(outcome)))
    step = {"id": 7, "name": "constraints", "stage": "stage2",
            "gate": {"all_of": [
                {"advisory_program_exit_zero":
                 f"{prog} . --json reports/phase2/gates/{gate}.json"},
                {"program_exit_zero": _pass_probe(tmp_path)}]}}
    return F.check_step(project, step, {})


def test_a_two_source_advisory_not_measured_reads_like_its_fail(tmp_path):
    """The recorded defect, on its real shape (a substantive sibling): FAIL ->
    PASS with the refusal disclosed; NOT_MEASURED -> the same tier, with the
    clause disclosed as partial vacuity."""
    on_fail = _check_with_sibling(tmp_path / "f", "stage1_compliance", "FAIL")
    on_nm = _check_with_sibling(tmp_path / "n", "stage1_compliance",
                                "NOT_MEASURED")
    assert on_fail.status == PASS, (on_fail.status, on_fail.reasons)
    assert on_nm.status == on_fail.status, (on_nm.status, on_nm.reason_class,
                                            on_nm.reasons)
    assert on_nm.partial_vacuity_disclosed, on_nm.reasons


def test_a_step_whose_only_clause_is_an_advisory_not_measured_is_not_pass(
        tmp_path):
    """REPLACES round 1's sole-clause PASS: no clause examined anything, so the
    step stays in the vacuous tier (#901)."""
    r = _check(tmp_path, "stage1_compliance", "NOT_MEASURED")
    assert r.status == NM, (r.status, r.reason_class, r.reasons)


#: The reviewer's M1 probe: a blocking clause that exits 2 CAPABILITY_ABSENT and
#: a two-source-advisory clause that returns NOT_MEASURED ZERO_DENOMINATOR.
_VACUOUS_BLOCKING = '''import json, sys
from pathlib import Path
argv = sys.argv[1:]
p = Path(argv[argv.index("--json") + 1])
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps({"verdict": "SKIP", "reason_class": "CAPABILITY_ABSENT",
                         "reason": "no simulator reaches this project"}))
print("VACUOUS_PASS: examined nothing (reason: capability absent)")
sys.exit(2)
'''


def test_the_m1_probe_two_clauses_that_examined_nothing_stay_not_measured(
        tmp_path):
    assert F._gate_is_two_source_advisory("mixed_signal_top_lvs_run")
    project = tmp_path / "proj"
    project.mkdir()
    g = tmp_path / "gates"
    g.mkdir()
    (g / "mixed_signal_merge_check.py").write_text(_VACUOUS_BLOCKING)
    (g / "mixed_signal_top_lvs_run.py").write_text(
        _NESTED.replace("{REPORT}", repr(
            {"verdict": "NOT_MEASURED", "reason_class": "ZERO_DENOMINATOR"}))
        .replace("{WORD!r}", repr("NOT_MEASURED")))
    step = {"id": "M1", "name": "mixed-signal merge", "stage": "stage_ms",
            "gate": {"all_of": [
                {"program_exit_zero": f"{g}/mixed_signal_merge_check.py . "
                                      f"--json reports/m.json"},
                {"advisory_program_exit_zero":
                 f"{g}/mixed_signal_top_lvs_run.py . --json reports/l.json"}]}}
    r = F.check_step(project, step, {})
    assert r.status == NM, (r.status, r.reasons)
    assert not r.partial_vacuity_disclosed, r.reasons


def test_the_awaiting_agent_reading_is_not_pre_empted(tmp_path):
    """R-0915-169: a two-source-advisory nested stage audit that is only waiting
    on an agent pass still reads NOT_MEASURED / awaiting_agent_pass."""
    import test_a_nested_audit_that_awaits_an_agent_is_not_an_execution_error \
        as R169
    project = tmp_path / "proj"
    project.mkdir()
    prog = tmp_path / "gates" / "stage1_compliance.py"
    prog.parent.mkdir()
    prog.write_text(R169._NESTED.format(report=R169._report(R169._RUN2),
                                        mode="write", rc=1))
    step = {"id": 7, "name": "constraints", "stage": "stage2",
            "gate": {"all_of": [{"advisory_program_exit_zero":
                                 f"{prog} . --stage 1 --strict --json "
                                 f"reports/gates/stage1_compliance.json"}]}}
    r = F.check_step(project, step, {})
    assert (r.status, r.reason_class) == (
        NM, _T.ReasonClass.AWAITING_AGENT_PASS.value), (r.status,
                                                        r.reason_class,
                                                        r.reasons)


def test_a_non_waiverable_not_measured_keeps_setting_the_tier(tmp_path):
    """The carve-out: a gate that declares its finding NON_WAIVERABLE keeps
    the INCOMPLETE disposition, even beside a substantive sibling."""
    project = tmp_path / "proj"
    project.mkdir()
    prog = tmp_path / "gates" / "stage1_compliance.py"
    prog.parent.mkdir()
    prog.write_text(
        _NESTED.replace("{REPORT}", repr(_OUTCOMES["NOT_MEASURED"]))
        .replace("{WORD!r}", repr("NOT_MEASURED"))
        .replace("sys.exit(1)",
                 "print('NON_WAIVERABLE: this finding is not deferrable')\n"
                 "sys.exit(1)"))
    step = {"id": 7, "name": "constraints", "stage": "stage2",
            "gate": {"all_of": [
                {"advisory_program_exit_zero":
                 f"{prog} . --json reports/phase2/gates/stage1_compliance.json"},
                {"program_exit_zero": _pass_probe(tmp_path)}]}}
    r = F.check_step(project, step, {})
    assert r.status == NM, (r.status, r.reasons)


def test_the_advisory_not_measured_is_still_disclosed_and_recorded(tmp_path):
    r = _check(tmp_path, "stage1_compliance", "NOT_MEASURED")
    recs = r.advisory_gate_records
    assert len(recs) == 1, recs
    assert recs[0]["verdict"] == "NOT_MEASURED", recs
    assert recs[0]["enforcement"] == "DISCLOSED_INCOMPLETE", recs
    text = "\n".join(r.reasons)
    assert "NOT_MEASURED" in text and "stage1_compliance" in text, r.reasons
    assert "advisory" in text.lower(), r.reasons


def test_a_gate_advisory_by_one_source_only_keeps_both_dispositions(tmp_path):
    """The other side of the same rule: FAIL blocks, and so NOT_MEASURED keeps
    its INCOMPLETE disposition."""
    on_fail = _check(tmp_path / "f", "probe_nested_audit", "FAIL")
    on_nm = _check(tmp_path / "n", "probe_nested_audit", "NOT_MEASURED")
    assert on_fail.status == FAIL, (on_fail.status, on_fail.reasons)
    assert on_nm.status == NM, (on_nm.status, on_nm.reasons)


def test_an_advisory_not_measured_does_not_launder_a_sibling(tmp_path):
    """The disclosure must ride the held-out advisory channel: a genuinely
    incomplete sibling clause still sets the step NOT_MEASURED."""
    project = tmp_path / "proj"
    project.mkdir()
    adv = tmp_path / "gates" / "stage1_compliance.py"
    adv.parent.mkdir(parents=True)
    adv.write_text(_NESTED.replace("{REPORT}", repr(_OUTCOMES["NOT_MEASURED"]))
                   .replace("{WORD!r}", repr("NOT_MEASURED")))
    sib = tmp_path / "gates" / "probe_nested_audit.py"
    sib.write_text(_NESTED.replace("{REPORT}", repr(_OUTCOMES["NOT_MEASURED"]))
                   .replace("{WORD!r}", repr("NOT_MEASURED")))
    step = {"id": 7, "name": "constraints", "stage": "stage2",
            "gate": {"all_of": [
                {"advisory_program_exit_zero":
                 f"{adv} . --json reports/a.json"},
                {"advisory_program_exit_zero":
                 f"{sib} . --json reports/b.json"}]}}
    r = F.check_step(project, step, {})
    assert r.status == NM, (r.status, r.reasons)
