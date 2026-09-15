"""R-0915-28 (as amended) — a step-10 FAIL is SUPERSEDED only by a passing
post-route sign-off, never by a label the failing report writes about itself.

THE FIRST VERSION OF THIS RULING WAS WRONG AND IS NOT IN THE TREE. It put the
tier inside `eda_report_audit`: a violation whose report declared
`STA_BASIS: PRE_LAYOUT_ESTIMATE` was reported at INFO and the gate PASSED. That
reverses a landed contract,
`test_sta_basis_scope_residuals::test_widening_step10_does_not_buy_a_green`,
whose docstring is the specification -- "the scope is the perfect instrument for
buying a green; it may not" -- and that test is byte-identical after this change,
because the gate was left exactly as it was. Step 10 still FAILS.

The question is asked ONE LEVEL UP, by the completion audit, where it can be
keyed on EVIDENCE: a step-10 FAIL is booked SUPERSEDED_BY_SIGNOFF only when a
SEPARATE, PASSING post-route multi-corner sign-off exists for the SAME corner.
That artefact is not something the failing report can author, so forging the
label buys nothing.

MEASURED on `subservient` x gf180mcuD (lane icsub2, run r13), one design, one
SDC (20 ns, from the design's own L9 table):

    pre-layout   SS 125C 4v50   worst setup -6.10 ns   TNS -371.15
    sign-off     SS multi-corner OCV        worst setup  +0.03 ns, closed
"""
import json
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import _sta_supersession as S  # noqa: E402

PRE = "phase3/stage3/sta/per_corner/sta_SS.rpt"
_RPT = """Startpoint: _3306_
Endpoint: _3377_
          -6.10   slack (VIOLATED)
tns max -371.15
wns max -6.10
STA_BASIS: PRE_LAYOUT_ESTIMATE
"""


def _gate_report(basis="PRE_LAYOUT", violation=True, evidence=PRE, passed=False):
    return {
        "program": "eda_report_audit:sta", "passed": passed,
        "findings": ([{"rule": "STA_REAL_VIOLATION_FOUND", "severity": "ERROR",
                       "message": "a real timing violation", "file": evidence}]
                     if violation else []),
        "summary": {"declared_sta_basis": basis,
                    "real_violation_found": violation},
    }


def _stance(closed=True, setup="SS", hold="FF", setup_slack=0.03,
            report="phase3/stage3/sta/sta_mcorner_ocv.rpt"):
    return {"signoff_dimension": "multi_corner_ocv_process",
            "setup_process_corner": setup, "hold_process_corner": hold,
            "multi_process_corner": True, "report": report,
            "setup_worst_slack_ns": setup_slack, "hold_worst_slack_ns": 0.51,
            "timing_closed_multi_corner": closed}


def _project(tmp_path, stance=None, pre_body=_RPT):
    p = tmp_path / "proj"
    (p / "phase3/stage3/sta/per_corner").mkdir(parents=True)
    (p / PRE).write_text(pre_body)
    if stance is not None:
        (p / "reports/phase3").mkdir(parents=True)
        (p / S.SIGNOFF_STANCE_REL).write_text(json.dumps(stance, indent=2))
    return p


# ------------------------------------------------------- the one direction that supersedes

def test_a_pre_layout_fail_with_a_passing_signoff_is_superseded(tmp_path):
    rec = S.supersession(_project(tmp_path, _stance()), _gate_report())
    assert rec is not None
    assert rec["corner"] == "SS"
    assert rec["pre_layout_setup_slack_ns"] == -6.10
    assert rec["signoff_setup_slack_ns"] == 0.03


def test_the_disclosure_names_the_corner_and_BOTH_slacks(tmp_path):
    """Nothing is hidden: a reader gets the corner, what step 10 measured, what
    sign-off measured, both files, and what SUPERSEDED does and does not mean."""
    rec = S.supersession(_project(tmp_path, _stance()), _gate_report())
    d = S.disclosure(rec)
    assert "corner SS" in d
    assert "-6.1 ns" in d and "+0.03 ns" in d
    assert PRE in d and "sta_mcorner_ocv.rpt" in d
    assert "the step-10 gate still FAILS" in d
    assert "not waived" in d
    assert "remove the passing sign-off and this step blocks again" in d


# ------------------------------------------------------- every direction that does NOT

def test_no_post_route_report_leaves_the_fail(tmp_path):
    assert S.supersession(_project(tmp_path), _gate_report()) is None


def test_a_signoff_that_did_not_close_leaves_the_fail(tmp_path):
    p = _project(tmp_path, _stance(closed=False))
    assert S.supersession(p, _gate_report()) is None


def test_a_signoff_violated_on_that_corner_leaves_the_fail(tmp_path):
    """Closed-but-negative is not closed, and the number is checked as well as
    the flag: two ways to say it, both required."""
    p = _project(tmp_path, _stance(setup_slack=-0.12))
    assert S.supersession(p, _gate_report()) is None


def test_a_signoff_for_OTHER_corners_leaves_the_fail(tmp_path):
    """Supersession is per corner. A TT/FF sign-off says nothing about SS."""
    p = _project(tmp_path, _stance(setup="TT", hold="FF"))
    assert S.supersession(p, _gate_report()) is None


def test_a_report_with_no_declared_basis_leaves_the_fail(tmp_path):
    """Silence is not a basis; the softer tier has to be EARNED."""
    p = _project(tmp_path, _stance())
    assert S.supersession(p, _gate_report(basis=None)) is None
    assert S.supersession(p, _gate_report(basis="UNDECLARED")) is None


def test_relabelling_a_post_route_report_buys_nothing(tmp_path):
    """THE ANTI-LAUNDERING CASE, at this level. Forge the label on the failing
    report and produce NO passing sign-off: the FAIL stands, because
    supersession needs the other artefact to exist and to pass."""
    p = _project(tmp_path)          # no stance file at all
    assert S.supersession(p, _gate_report(basis="PRE_LAYOUT")) is None


def test_a_report_cannot_supersede_itself(tmp_path):
    """And if the 'sign-off' names the very file that failed, it is not a
    second measurement."""
    p = _project(tmp_path, _stance(report=PRE))
    assert S.supersession(p, _gate_report()) is None


def test_a_passing_gate_is_never_superseded(tmp_path):
    """Supersession only ever applies to a FAIL; it cannot manufacture a tier
    for a step that had no finding."""
    p = _project(tmp_path, _stance())
    assert S.supersession(p, _gate_report(passed=True)) is None
    assert S.supersession(p, _gate_report(violation=False)) is None


def test_an_undeterminable_corner_leaves_the_fail(tmp_path):
    """If the evidence file does not name exactly one corner, we do not know
    which one the sign-off would have to cover."""
    p = _project(tmp_path, _stance())
    assert S.supersession(p, _gate_report(
        evidence="phase3/stage3/sta/pre_pnr_timing.rpt")) is None
    assert S.corner_of("sta_SS_and_TT.rpt") is None
    assert S.corner_of("sta_SS.rpt") == "SS"


# ------------------------------------------------------- the wiring, and the landed contract

def test_the_audit_books_the_tier_and_the_gate_still_fails():
    """The tier exists in the AUDIT, and the GATE is untouched: `eda_report_
    audit` still has exactly one rule for a real violation."""
    import flow_compliance_check as F
    import eda_report_audit as E
    assert F._SUPERSEDED_VERDICT == "SUPERSEDED_BY_SIGNOFF"
    assert F._SUPERSEDED_HINT_PREFIX.startswith("__SUPERSEDED")
    src = Path(E.__file__).read_text()
    assert "STA_REAL_VIOLATION_FOUND" in src
    assert "STA_PRE_LAYOUT_VIOLATION_DISCLOSED" not in src, (
        "the gate-level tier was reverted; it must not come back")


def test_the_landed_anti_laundering_contract_is_byte_identical():
    """The test that says a label may not buy a green is the contract. This
    change must not have edited it."""
    import subprocess
    out = subprocess.run(
        ["git", "diff", "--", "programs/tests/test_sta_basis_scope_residuals.py"],
        cwd=str(PROG.parent), capture_output=True, text=True)
    assert out.stdout.strip() == "", out.stdout
