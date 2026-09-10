#!/usr/bin/env python3
"""A repair arm the flow REVERTED could still be read as the sign-off report.

THE DEFECT, REPRODUCED. `sta_signoff_rigor_check._find_report` resolves "the
sign-off STA report" by filename glob and lexicographic PATH order alone. Its
first pattern is `sta_mcorner_ocv*.rpt`, which matches BOTH

    phase3/stage3/sta/sta_mcorner_ocv.rpt              <- retained, governing
    phase3/stage3/sta/sta_mcorner_ocv_postrepair.rpt   <- the arm the flow REVERTED

and nothing in the selector has ever read `postroute_timing_repair_decision.json`,
the only artefact that says which of the two the flow adopted. `phase3_one_shot_
runner` is explicit that both stay on disk: "The post-route repair outputs stay
on disk under their own names for debug; they are NOT adopted as the shipped
artefacts." Not adopting is asserted there and enforced nowhere.

Measured on the unmodified selector, three shapes:

    both present, same directory     -> sta_mcorner_ocv.rpt          (correct)
    only the reverted arm survives   -> sta_mcorner_ocv_postrepair.rpt  (WRONG)
    both, reverted arm one dir over  -> aa_repair/..._postrepair.rpt    (WRONG)

The first is correct BY ACCIDENT and not by design: `sta_mcorner_ocv.rpt` beats
`sta_mcorner_ocv_postrepair.rpt` only because `.` (0x2E) sorts under `_` (0x5F).
`rglob` sorts whole PATHS, so the moment the discarded arm sits in a directory
that sorts earlier the accident reverses and the discarded arm wins outright.

WHY THIS IS WORTH A GATE. It is not hypothetical: issue #2234's own contributor
table (23 stages / 7.77 ns / 23.3 %, slack -2.25) was computed from
`sta_mcorner_ocv_postrepair.rpt` — the reverted arm — while the number that
governs the FAIL is -1.86 ns on `sta_mcorner_ocv.rpt`. A human read the
discarded arm as sign-off because nothing on disk distinguishes them; the
selector can make exactly the same mistake, and would report a verdict rather
than a mistake. See `docs/findings/2026-09-10-issue2234-the-buffer-chain-is-
stage-count-not-buffer-strength.md`.

THE REMEDY IS THE ONE ALREADY IN THE TREE. `lvs_tapeout_signoff_check._find_
report` faced the same shape with an in-tree snapshot copy that sorted first,
and answers it by preferring the canonical report and returning None when only
the non-authoritative copy exists — never silently consuming it. This applies
that doctrine to the reverted repair arm, keyed on the flow's OWN record
(`repair_after.report`) rather than on a `*_postrepair*` filename pattern, so a
rename in the runner cannot quietly empty the exclusion set.

A REFUSAL, NOT A PASS. When the discarded arm is the only candidate the gate
returns IO_ERROR (rc 2) — NOT_MEASURED. That is the honest outcome: a rigor
verdict computed on an arm the flow threw away is not a sign-off verdict, and
reporting one would be worse than reporting nothing.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import sta_signoff_rigor_check as R  # noqa: E402

_RETAINED = "sta_mcorner_ocv.rpt"
_REVERTED = "sta_mcorner_ocv_postrepair.rpt"


def _run(root: Path, *, reports, action, recorded=None, sta_dir="sta",
         write_decision=True) -> Path:
    """A run tree: `reports` under phase3/stage3/<sta_dir>, plus the decision."""
    sta = root / "phase3" / "stage3" / sta_dir
    sta.mkdir(parents=True, exist_ok=True)
    for name in reports:
        (sta / name).write_text("Startpoint: a\nEndpoint: b\nslack (MET) 1.0\n")
    if write_decision:
        dec_dir = root / "phase3" / "stage3" / "postroute_timing_repair"
        dec_dir.mkdir(parents=True, exist_ok=True)
        rec = {"action": action}
        if recorded is not None:
            rec["repair_after"] = {"report": recorded}
        (dec_dir / "postroute_timing_repair_decision.json").write_text(
            json.dumps(rec, indent=2) + "\n")
    return root


def _rel(sta_dir: str, name: str) -> str:
    return f"phase3/stage3/{sta_dir}/{name}"


# ---------------------------------------------------------------------------
# The instrument can see. Without the decision record the discarded arm IS
# selected -- so the tests below are measuring the exclusion and not a tree in
# which the file was never a candidate in the first place.
# ---------------------------------------------------------------------------
def test_positive_control_with_no_decision_record_the_arm_is_still_chosen(tmp_path):
    root = _run(tmp_path, reports=[_REVERTED], action=None,
                write_decision=False)
    found = R._find_report(root)
    assert found is not None and found.name == _REVERTED, (
        "with no decision record on disk the postrepair report must still be "
        "selected -- otherwise this file's other cases prove nothing, because "
        "the candidate was never reachable")


def test_the_reverted_arm_alone_is_refused_rather_than_judged(tmp_path):
    root = _run(tmp_path, reports=[_REVERTED],
                action="timing_repair_reverted_regression",
                recorded=_rel("sta", _REVERTED))
    assert R._find_report(root) is None, (
        "the only candidate belongs to a repair arm the flow REVERTED; it must "
        "not be resolved as the sign-off report")
    res = R.check(root)
    assert res["verdict"] == "IO_ERROR", (
        f"expected a NOT_MEASURED refusal, got a verdict: {res}")
    assert _REVERTED in " ".join(res.get("non_adopted_reports", [])), (
        f"the refusal must NAME the arm it declined to read: {res}")


def test_a_reverted_arm_one_directory_over_does_not_win_on_path_order(tmp_path):
    """`.` < `_` is an accident; a directory component reverses it."""
    root = _run(tmp_path, reports=[_REVERTED], action=None,
                sta_dir="aa_repair", write_decision=False)
    _run(root, reports=[_RETAINED],
         action="timing_repair_reverted_regression",
         recorded=_rel("aa_repair", _REVERTED))
    found = R._find_report(root)
    assert found is not None and found.name == _RETAINED, (
        f"the governing report must win over a discarded arm that merely sorts "
        f"earlier by directory; got {found}")


def test_both_present_the_governing_report_is_the_one_read(tmp_path):
    """Correct on the unmodified selector too, by the `.` < `_` accident.

    Kept as a regression pin, not as a falsifier: it is GREEN in both arms.
    """
    root = _run(tmp_path, reports=[_RETAINED, _REVERTED],
                action="timing_repair_reverted_regression",
                recorded=_rel("sta", _REVERTED))
    found = R._find_report(root)
    assert found is not None and found.name == _RETAINED


# ---------------------------------------------------------------------------
# The exclusion must not over-reach.
# ---------------------------------------------------------------------------
def test_an_ADOPTED_postrepair_report_is_still_read(tmp_path):
    """The guard is bound to the DECISION, never to the filename."""
    root = _run(tmp_path, reports=[_REVERTED], action="timing_repair_ran",
                recorded=_rel("sta", _REVERTED))
    found = R._find_report(root)
    assert found is not None and found.name == _REVERTED, (
        "a repair the flow ADOPTED leaves its report as legitimate sign-off "
        "evidence; excluding it on the strength of its name would be wrong")


@pytest.mark.parametrize("body", ["{not json", "[]", '{"action": null}',
                                  '{"action": "timing_repair_reverted_regression"}'])
def test_an_unusable_decision_record_excludes_nothing(tmp_path, body):
    """Fail-open is the safe direction: this set only ever REMOVES candidates."""
    root = _run(tmp_path, reports=[_REVERTED], action=None,
                write_decision=False)
    dec = root / "phase3" / "stage3" / "postroute_timing_repair"
    dec.mkdir(parents=True, exist_ok=True)
    (dec / "postroute_timing_repair_decision.json").write_text(body)
    found = R._find_report(root)
    assert found is not None and found.name == _REVERTED, (
        "an unreadable record, or one naming no report, must not turn a run "
        "that never repaired into a refusal")


def test_the_exclusion_reads_the_RECORD_not_the_filename(tmp_path):
    """A `*_postrepair*` pattern would be a re-implementation that drifts.

    The runner is free to rename what `_measure_postrepair_mcorner_ocv` writes.
    The decision carries the path it actually wrote, so the exclusion follows
    the rename; a name-shaped guard would silently stop excluding anything.
    """
    odd = "sta_mcorner_ocv_second_attempt.rpt"
    root = _run(tmp_path, reports=[odd],
                action="timing_repair_reverted_regression",
                recorded=_rel("sta", odd))
    assert R._find_report(root) is None, (
        "the arm named by the record must be excluded whatever it is called")
