"""MAIN_FALSEPASS_OSS (root, 2026-09-29) — adding a failure turned the run green.

MEASURED on live main (found by a final check; probe on g3 and LECNP, not
introduced by them): the OS-constraints promotion (an OSS-blocked sign-off FAIL
-> PASS_WITH_WAIVERS with a must-close deferral row) guarded itself only with
`failing` / `missing` / `oss_blocked_skipped` / `not_owed` and a refusal of
WAIVED. A plain NOT_MEASURED row sits in none of them. So

    step 13 NOT_MEASURED(inconclusive)                 -> NOT_MEASURED  rc 1
    step 13 NOT_MEASURED(inconclusive) + step 28 FAIL  -> PASS_WITH_WAIVERS rc 0

The promotion replaced the FAIL rung with PASS_WITH_WAIVERS and skipped the
NOT_MEASURED rung of the run ladder (FAIL > NOT_MEASURED > ... ). Fixed at the
source: the promoted rows leave the FAIL rung, and the rest of the ladder is
still read over every in-scope row that is not `excluded_from_verdict`.

Controls, unchanged: only OSS-blocked FAILs + everything else PASS ->
PASS_WITH_WAIVERS rc 0; a NOT_MEASURED row the F10 rule excluded from the
verdict does not block the promotion.
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
_PASS = _T.Verdict.PASS.value
_FAIL = _T.Verdict.FAIL.value
_NM = _T.Verdict.NOT_MEASURED.value
_PWW = _T.Verdict.PASS_WITH_WAIVERS.value
_INCONCLUSIVE = _T.ReasonClass.INCONCLUSIVE.value


def _drive_main(tmp_path, monkeypatch, verdicts, excluded=()):
    """The real `main()` over the canonical flow, every step's verdict stubbed
    (PASS unless `verdicts` says otherwise); ids in `excluded` carry an F10
    `excluded_from_verdict` sentence."""
    proj = tmp_path / "proj"
    rtl_dir = proj / "phase2" / "stage1" / "rtl"
    rtl_dir.mkdir(parents=True)
    (rtl_dir / "top.v").write_text(
        "module top(input a, output b); assign b = a; endmodule\n")
    tmpl = proj / "input" / "submission_template"
    tmpl.mkdir(parents=True)
    (tmpl / "NO_TEMPLATE.txt").write_text("IP/hardmacro delivery\n")

    def _check(_project, step, _waivers, **_kw):
        sid = step.get("id")
        status, rc = verdicts.get(str(sid), (_PASS, ""))
        r = FCC.StepResult(id=sid, name=step.get("name", ""),
                           stage=step.get("stage", ""), status=status,
                           reason_class=rc,
                           reasons=([] if status == _PASS
                                    else [f"stub {status} {rc}"]))
        if str(sid) in excluded:
            r.excluded_from_verdict = ("NOT_MEASURED, excluded from the run "
                                       "verdict: probe")
        return r

    def _umbrella(_project, **kw):
        out = kw.get("records_out")
        if out is not None:
            out.extend(FCC._p0_gate_record(g, "PASS", "", {})
                       for g in list(FCC._STRUCTURAL_RTL_GATES)[:2])
        return (True, [], [], [])

    monkeypatch.setattr(FCC, "check_step", _check)
    monkeypatch.setattr(FCC, "_run_structural_rtl_gates", _umbrella)
    report = tmp_path / "report.json"
    rc = FCC.main([str(proj), "--json", str(report), "--strict"])
    audit = json.loads((proj / "reports" / "audit" /
                        "phase23_completion_audit.json").read_text())
    return rc, json.loads(report.read_text()), audit


def test_the_probe_an_oss_fail_does_not_green_an_unmeasured_run(
        tmp_path, monkeypatch, capsys):
    """RED on main 0d986900d: PASS_WITH_WAIVERS rc 0."""
    rc, report, audit = _drive_main(tmp_path, monkeypatch, {
        "13": (_NM, _INCONCLUSIVE), "28": (_FAIL, "")})
    capsys.readouterr()
    assert report["overall"] == _NM, report["overall"]
    assert audit["verdict"] == _NM, audit["verdict"]
    assert rc != 0


def test_control_the_unmeasured_row_alone(tmp_path, monkeypatch, capsys):
    rc, report, _a = _drive_main(tmp_path, monkeypatch, {
        "13": (_NM, _INCONCLUSIVE)})
    capsys.readouterr()
    assert report["overall"] == _NM and rc == 1


def test_control_only_oss_blocked_fails_still_promote(tmp_path, monkeypatch,
                                                      capsys):
    rc, report, audit = _drive_main(tmp_path, monkeypatch, {
        "28": (_FAIL, "")})
    capsys.readouterr()
    assert report["overall"] == _PWW, report["overall"]
    assert rc == 0
    deferred = {str(d.get("step_id"))
                for d in audit["open_source_constraints_deferrals"]}
    assert "28" in deferred, deferred


def test_control_an_excluded_unmeasured_row_does_not_block(
        tmp_path, monkeypatch, capsys):
    rc, report, _a = _drive_main(tmp_path, monkeypatch, {
        "13": (_NM, _INCONCLUSIVE), "28": (_FAIL, "")}, excluded=("13",))
    capsys.readouterr()
    assert report["overall"] == _PWW, report["overall"]
    assert rc == 0


def test_the_deferrals_stay_listed_when_the_run_is_not_measured(
        tmp_path, monkeypatch, capsys):
    """The OSS-blocked row is still a must-close row; it just no longer hides
    the unmeasured one."""
    _rc, _report, audit = _drive_main(tmp_path, monkeypatch, {
        "13": (_NM, _INCONCLUSIVE), "28": (_FAIL, "")})
    capsys.readouterr()
    deferred = {str(d.get("step_id"))
                for d in audit["open_source_constraints_deferrals"]}
    assert "28" in deferred, deferred
