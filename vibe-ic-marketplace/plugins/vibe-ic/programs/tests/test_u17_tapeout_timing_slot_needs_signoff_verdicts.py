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
    _write(proj / sa._STEP32_LIBRELANE_REL, json.dumps(
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
