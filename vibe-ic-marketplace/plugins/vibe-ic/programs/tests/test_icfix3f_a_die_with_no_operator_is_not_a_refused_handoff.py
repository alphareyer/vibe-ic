"""(38) A DIE whose PDK has no live shuttle is not a hand-off the operator refused.

MEASURED on the real spm runs (8HD-4, `_lane_icspm5/run13` and `run8`), both
digital DIEs, `reports/phase3/shuttle_precheck.json`:

    verdict                   NOT_APPLICABLE
    arm_state                 NOT_APPLICABLE
    arm_ran                   false
    verdict_is_the_operators  FALSE
    shuttle                   null
    reason  "validated informational catalogue; owner DIE has no operator purchase"

`foundry_handoff_package_check` decided the hand-off MODE from the file's mere
PRESENCE (`evidence_mode = _MODE_SHUTTLE if precheck_present else ...`) and then
refused anything whose verdict was not `PASS`. So on every digital DIE it emitted

    FOUNDRY_HANDOFF_SHUTTLE_PRECHECK_REFUSED:
      the shuttle operator (unnamed in the report) returned 'NOT_APPLICABLE'

— an ERROR describing a refusal by a party that does not exist, however good the
layout. The report says so in its own fields; nothing read them.

THE FLOW CONTRACT, from `tapeout_precheck`'s own docstring, which is the
authority here:

    NOT_APPLICABLE  the registry names no live shuttle for this PDK. THE ONLY
                    absence that is not a defect — the owner's "one fewer arm".
    NOT_DETERMINED  the arm should have run and could not.

So NOT_APPLICABLE is a legitimate absence and NOT_DETERMINED is not, and the two
must not be collapsed. What this branch changes is WHOSE verdict the gate thinks
it is reading — not which verdicts are acceptable. `_PRECHECK_ACCEPTS` is
untouched: a real operator that returns anything but PASS still stops the
hand-off, and all five existing tests that assert exactly that still pass.

chip-AGNOSTIC: generic operator placeholder; the PDK name never appears.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

import test_foundry_handoff_names_its_owner as H  # noqa: E402

FH = H.FH
_project, _gate = H._project, H._gate


def _precheck_raw(proj, payload):
    d = proj / "reports/phase3"
    d.mkdir(parents=True, exist_ok=True)
    (d / "shuttle_precheck.json").write_text(json.dumps(payload, indent=2))


#: The shape 37.5ic actually writes on a DIE whose PDK has no live shuttle,
#: copied field-for-field from the measured spm reports.
_NO_OPERATOR = {
    "program": "tapeout_readiness_check",
    "verdict": "NOT_APPLICABLE",
    "arm_state": "NOT_APPLICABLE",
    "arm_ran": False,
    "verdict_is_the_operators": False,
    "shuttle": None,
    "reason": "no live shuttle is registered for this process",
}


def test_a_die_with_no_operator_is_not_a_refused_handoff(tmp_path):
    """THE DEFECT, in one assertion."""
    proj = _project(tmp_path, with_chip_gds=True)
    _precheck_raw(proj, _NO_OPERATOR)
    FH.main([str(proj)])
    rc, rules = _gate(proj)
    assert "FOUNDRY_HANDOFF_SHUTTLE_PRECHECK_REFUSED" not in rules, (
        "37.5ic reported that there was no operator to ask, and the gate "
        "reported that the operator refused: %r" % (sorted(rules),))


def test_that_die_is_not_on_the_shuttle_path_and_both_programs_agree(tmp_path):
    """The generator RECORDS the mode and the checker RE-DERIVES it, so a
    disagreement is itself an ERROR (`FOUNDRY_HANDOFF_MODE_MISDECLARED`). Both
    had to change together; this is the arm that proves they did."""
    proj = _project(tmp_path, with_chip_gds=True)
    _precheck_raw(proj, _NO_OPERATOR)
    FH.main([str(proj)])
    rc, rules = _gate(proj)
    assert "FOUNDRY_HANDOFF_MODE_MISDECLARED" not in rules, sorted(rules)


def test_a_real_operator_refusal_still_stops_the_handoff(tmp_path):
    """THE OTHER DIRECTION, and the one that says this is not a weakening.
    When the verdict IS the operator's, a non-PASS still refuses."""
    proj = _project(tmp_path, with_chip_gds=True)
    _precheck_raw(proj, dict(_NO_OPERATOR, verdict="FAIL",
                             verdict_is_the_operators=True,
                             arm_ran=True, shuttle="an_open_mpw_operator"))
    FH.main([str(proj)])
    rc, rules = _gate(proj)
    assert rc == 1
    assert "FOUNDRY_HANDOFF_SHUTTLE_PRECHECK_REFUSED" in rules


def test_a_real_operator_not_determined_still_stops_the_handoff(tmp_path):
    """NOT_DETERMINED is the absence that IS a defect. Collapsing it with
    NOT_APPLICABLE is the failure this branch must not introduce."""
    proj = _project(tmp_path, with_chip_gds=True)
    _precheck_raw(proj, dict(_NO_OPERATOR, verdict="NOT_DETERMINED",
                             verdict_is_the_operators=True,
                             arm_ran=True, shuttle="an_open_mpw_operator"))
    FH.main([str(proj)])
    rc, rules = _gate(proj)
    assert rc == 1
    assert "FOUNDRY_HANDOFF_SHUTTLE_PRECHECK_REFUSED" in rules


def test_a_report_that_omits_the_field_is_read_exactly_as_before(tmp_path):
    """CONSERVATIVE BY CONSTRUCTION. Only an EXPLICIT false re-classifies, so a
    silence is never credited as "there was no operator" — the same rule
    `tapeout_precheck` applies when it refuses to write NOT_APPLICABLE for a PDK
    it could not determine."""
    proj = _project(tmp_path, with_chip_gds=True)
    payload = dict(_NO_OPERATOR, verdict="FAIL", shuttle="an_open_mpw_operator")
    payload.pop("verdict_is_the_operators")
    _precheck_raw(proj, payload)
    FH.main([str(proj)])
    rc, rules = _gate(proj)
    assert rc == 1
    assert "FOUNDRY_HANDOFF_SHUTTLE_PRECHECK_REFUSED" in rules


def test_an_unreadable_report_is_still_not_an_acceptance(tmp_path):
    """A parse failure must not become "there was no operator". It stays a
    non-pass on the shuttle path, exactly as before this branch."""
    proj = _project(tmp_path, with_chip_gds=True)
    (proj / "reports/phase3").mkdir(parents=True, exist_ok=True)
    (proj / "reports/phase3/shuttle_precheck.json").write_text("{ truncated")
    FH.main([str(proj)])
    rc, rules = _gate(proj)
    assert rc == 1
    assert "FOUNDRY_HANDOFF_SHUTTLE_PRECHECK_REFUSED" in rules
