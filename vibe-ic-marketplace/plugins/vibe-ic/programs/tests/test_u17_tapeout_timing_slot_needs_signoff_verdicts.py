"""U17 — Step 36's timing slot credited a timing REPORT, not a timing VERDICT.

`signoff_audit --mode tapeout` credited the timing pillar as soon as a
post-route-ranked `*timing*.rpt` existed. A report that says setup slack is
-55 ns is a timing report; it is not tape-out timing. subservient v4 stopped
at Step 32 with SS setup at -55.9 ns (IC_BLOCKER_AUDIT U17), and the older
spm tail read READY_FOR_TAPEOUT on presence alone.

Rule now: the slot is credited only when Step 23's declared sign-off verdict
(`reports/phase3/sta/post_route_summary.json`, written by `sta_report_check`)
and Step 32's own gate evaluation (`postroute_timing_repair_audit`, plus the
tool arm's report verdict when it ran) are both PASS. A FAIL or an absent
verdict names the step and its state; neither is ever PASS.

The -55 ns case runs the REAL step-23 producer on the report, so the verdict
the tape-out gate reads is the one the flow writes. chip-AGNOSTIC fixtures.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import signoff_audit as sa  # noqa: E402
import _gdsii  # noqa: E402
import _si_signoff_fixture  # noqa: E402
import _tapeout_timing_fixture as TT  # noqa: E402
import _progress_run as _pr  # noqa: E402

_LVS_MATCH = """\
Subcircuit summary:
Circuit 1: top                          |Circuit 2: top
Netlists match uniquely.
Final result: Circuits match uniquely.
"""
_STA_REL = "phase3/stage3/sta/post_route_timing.rpt"


def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def _path_group(slack: float) -> str:
    state = "MET" if slack >= 0 else "VIOLATED"
    return ("Startpoint: u_in (rising edge-triggered flip-flop clocked by clk)\n"
            "Endpoint: u_out (rising edge-triggered flip-flop clocked by clk)\n"
            "Path Type: max\n"
            f"  data arrival time  {24.0 - slack:.2f}\n"
            "  data required time 24.00\n"
            f"  slack ({state})  {slack:.2f}\n" + "-" * 40 + "\n")


def _project(tmp_path: Path, slack: float) -> Path:
    """Every other tape-out pillar clean, the SI verdict PROVED, Step 32 a
    consistent no-repair outcome — so the timing verdict alone decides."""
    proj = tmp_path / "p"
    _gdsii.write_gdsii(proj / "phase3/stage4/gds/top.gds")
    _write(proj / "phase3/stage3/pnr/top_pnr.v", "module top(); endmodule\n")
    _write(proj / "drc_signoff.rpt", "Total violations: 0\n")
    _write(proj / "reports/phase3/lvs.rpt", _LVS_MATCH)
    _si_signoff_fixture.write_proved_si_report(proj)
    TT.write_step32_no_repair(proj)
    TT.write_step23_other_clauses(proj)
    _write(proj / _STA_REL, _path_group(slack) * 20)
    return proj


def _run_step23(proj: Path) -> dict:
    """Step 23's declared producer, as the flow invokes it."""
    r = _pr.run([sys.executable, str(_PROGRAMS / "sta_report_check.py"),
                 str(proj), "--mode", "sta", "--under", _STA_REL,
                 "--json", TT.STEP23_SUMMARY],
                capture_output=True, text=True, cwd=str(proj))
    path = proj / TT.STEP23_SUMMARY
    assert path.is_file(), (r.stdout + r.stderr)[-800:]
    return json.loads(path.read_text())


def _rules(result) -> set:
    return {f.rule for f in result.findings}


def test_a_minus_55ns_report_does_not_credit_the_timing_slot(tmp_path):
    proj = _project(tmp_path, -55.85)
    step23 = _run_step23(proj)
    assert step23["passed"] is False            # the flow's own verdict: FAIL

    r = sa._check_tapeout(proj)
    assert r.summary["evidence"]["timing"] is False, _rules(r)
    assert "TAPEOUT_TIMING_STEP23_FAIL" in _rules(r)
    assert "TAPEOUT_TIMING_EXISTS" not in _rules(r)
    assert r.passed is False
    assert r.summary["timing_signoff_verdicts"]["23"]["state"] == "FAIL"
    assert r.summary["timing_signoff_verdicts"]["32"]["state"] == "PASS"


def test_control_the_same_project_with_met_timing_passes(tmp_path):
    """Without this the case above could be FAILing for a fixture reason."""
    proj = _project(tmp_path, 0.85)
    assert _run_step23(proj)["passed"] is True
    r = sa._check_tapeout(proj)
    assert r.summary["evidence"]["timing"] is True, [
        (f.rule, f.message) for f in r.findings if f.severity == "ERROR"]
    assert r.passed is True
    assert "TAPEOUT_TIMING_EXISTS" in _rules(r)


def test_an_absent_step23_verdict_is_not_measured_never_credited(tmp_path):
    proj = _project(tmp_path, 0.85)             # a MET report, but no verdict
    r = sa._check_tapeout(proj)
    assert r.summary["evidence"]["timing"] is False
    assert "TAPEOUT_TIMING_STEP23_NOT_MEASURED" in _rules(r)
    assert r.passed is False


def test_a_step32_contradiction_blocks_the_slot(tmp_path):
    """Step 23 PASS, but Step 32's own record says a repair was required
    while the run certified none: Step 32 FAILs and the slot is held."""
    proj = _project(tmp_path, 0.85)
    _run_step23(proj)
    (proj / TT.STEP32_DIR / "postroute_timing_repair_decision.json").write_text(
        json.dumps({"repair_needed": True, "action": "repair_timing",
                    "reason": "setup below floor at the slow corner"}))
    r = sa._check_tapeout(proj)
    assert r.summary["evidence"]["timing"] is False
    assert "TAPEOUT_TIMING_STEP32_FAIL" in _rules(r)
    assert r.passed is False


def test_a_tool_arm_step32_fail_blocks_the_slot(tmp_path):
    proj = _project(tmp_path, 0.85)
    _run_step23(proj)
    _write(proj / "reports/phase3/librelane_postroute_repair.json", json.dumps(
        {"step": "32", "verdict": "FAIL", "code": "LL_PRR_DRV_VIOLATION",
         "reason": "declared postroute DRV has 3 residual pin/check violations"}))
    r = sa._check_tapeout(proj)
    assert r.summary["evidence"]["timing"] is False
    assert "TAPEOUT_TIMING_STEP32_FAIL" in _rules(r)


def test_absent_step32_outcome_is_not_measured(tmp_path):
    proj = _project(tmp_path, 0.85)
    _run_step23(proj)
    for name in ("no_repair_needed.flag", "postroute_timing_repair_decision.json"):
        (proj / TT.STEP32_DIR / name).unlink()
    r = sa._check_tapeout(proj)
    assert r.summary["evidence"]["timing"] is False
    assert "TAPEOUT_TIMING_STEP32_NOT_MEASURED" in _rules(r)


# ── Round 2 (review wave 58) ───────────────────────────────────────────────
_MULTICORNER = """\
# Multi-corner SPEF STA
# SETUP corner: max-RC   HOLD corner: min-RC
# corners_available: max,min
=== SETUP (max-RC corner, SPEF=max, liberty=corner.lib) ===
STA_BASIS: POST_ROUTE_SPEF
STA_BASIS_CORNER: max
worst slack max -55.85
=== HOLD (min-RC corner, SPEF=min, liberty=corner.lib) ===
STA_BASIS: POST_ROUTE_SPEF
STA_BASIS_CORNER: min
worst slack min 0.30
"""


def test_a_slow_corner_violation_behind_a_met_nominal_is_not_tapeout_timing(
        tmp_path):
    """The nominal (tt) alias is MET and its summary passes; Step 23's
    slow-corner clause, run for real, FAILs at -55.85 ns. Step 23 is FAIL."""
    proj = _project(tmp_path, 0.85)
    assert _run_step23(proj)["passed"] is True
    _write(proj / "phase3/stage3/sta/sta_spef_multicorner.rpt", _MULTICORNER)
    r = _pr.run([sys.executable, str(_PROGRAMS / "post_route_signoff_corner_check.py"),
                 str(proj), "--json",
                 str(proj / "reports/phase3/sta/post_route_signoff_corner.json")],
                capture_output=True, text=True)
    assert r.returncode == 1, r.stdout[-400:]
    rep = sa._check_tapeout(proj)
    assert rep.summary["evidence"]["timing"] is False, _rules(rep)
    assert "TAPEOUT_TIMING_STEP23_FAIL" in _rules(rep)
    assert rep.passed is False


def test_every_step23_clause_record_is_required(tmp_path):
    """A clause record the flow declares for Step 23 and the run did not
    publish holds the slot (the nominal summary alone is not Step 23)."""
    proj = _project(tmp_path, 0.85)
    _run_step23(proj)
    (proj / "reports/phase3/sta/sta_corner_record_completeness.json").unlink()
    rep = sa._check_tapeout(proj)
    assert rep.summary["evidence"]["timing"] is False
    assert "TAPEOUT_TIMING_STEP23_NOT_MEASURED" in _rules(rep)


def test_a_stale_step23_summary_is_not_measured(tmp_path):
    """The summary passed a +0.85 ns report; the report on disk now says
    -55.85 ns. The recorded subject digest no longer matches."""
    proj = _project(tmp_path, 0.85)
    assert _run_step23(proj)["passed"] is True
    _write(proj / _STA_REL, _path_group(-55.85) * 20)
    rep = sa._check_tapeout(proj)
    assert rep.summary["evidence"]["timing"] is False, _rules(rep)
    assert "TAPEOUT_TIMING_STEP23_NOT_MEASURED" in _rules(rep)
    assert "stale" in rep.summary["timing_signoff_verdicts"]["23"]["reason"]


def test_a_bare_passed_true_is_not_a_step23_verdict(tmp_path):
    proj = _project(tmp_path, -55.85)
    _write(proj / TT.STEP23_SUMMARY, json.dumps({"passed": True}))
    rep = sa._check_tapeout(proj)
    assert rep.summary["evidence"]["timing"] is False
    assert "TAPEOUT_TIMING_STEP23_NOT_MEASURED" in _rules(rep)


def test_a_foreign_producer_summary_is_not_a_step23_verdict(tmp_path):
    proj = _project(tmp_path, 0.85)
    doc = _run_step23(proj)
    doc["program"] = "hand_written"
    _write(proj / TT.STEP23_SUMMARY, json.dumps(doc))
    rep = sa._check_tapeout(proj)
    assert "TAPEOUT_TIMING_STEP23_NOT_MEASURED" in _rules(rep)


def test_an_unreadable_step32_record_is_not_measured_not_a_crash(tmp_path):
    proj = _project(tmp_path, 0.85)
    _run_step23(proj)
    d = proj / TT.STEP32_DIR
    (d / "no_repair_needed.flag").unlink()
    (d / "postroute_timing_repair_decision.json").write_text(json.dumps(
        {"repair_needed": True, "action": "repair_timing"}))
    (d / "repair_log.json").write_text("[]")
    rep = sa._check_tapeout(proj)            # must not raise
    assert rep.summary["evidence"]["timing"] is False
    assert "TAPEOUT_TIMING_STEP32_NOT_MEASURED" in _rules(rep)
    assert rep.summary["evidence"]["gds"] is True   # the other slots still judged


def test_an_absent_step32_decision_record_is_not_measured(tmp_path):
    proj = _project(tmp_path, 0.85)
    _run_step23(proj)
    (proj / TT.STEP32_DIR / "postroute_timing_repair_decision.json").unlink()
    rep = sa._check_tapeout(proj)
    assert rep.summary["evidence"]["timing"] is False
    assert "TAPEOUT_TIMING_STEP32_NOT_MEASURED" in _rules(rep)


def test_step32_labels_fail_outranks_unmeasured_and_waived_is_named(tmp_path):
    proj = _project(tmp_path, 0.85)
    _run_step23(proj)
    (proj / TT.STEP32_DIR / "postroute_timing_repair_decision.json").write_text(
        json.dumps({"repair_needed": True, "action": "timing_not_measured",
                    "timing_basis_status": "NOT_MEASURED"}))
    rep = sa._check_tapeout(proj)
    assert "TAPEOUT_TIMING_STEP32_FAIL" in _rules(rep), _rules(rep)

    proj2 = _project(tmp_path / "w", 0.85)
    _run_step23(proj2)
    _write(proj2 / "reports/phase3/librelane_postroute_repair.json",
           json.dumps({"step": "32", "verdict": "WAIVED"}))
    rep2 = sa._check_tapeout(proj2)
    assert "TAPEOUT_TIMING_STEP32_WAIVED" in _rules(rep2), _rules(rep2)
