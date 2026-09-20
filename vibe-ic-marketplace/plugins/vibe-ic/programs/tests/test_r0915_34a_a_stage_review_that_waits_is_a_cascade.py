"""A review declining because its stage is not green is a CASCADE, not a fault.

MEASURED 2026-09-15 (lane icspm3, R-0915-34(a)) on `spm` x gf180mcuD.
`stage_on_pass_review` is the gate of steps 2, 7, 14, 15, 37 and 39, all in
`advisory_program_exit_zero` slots. When the stage under review is not green it
returns rc 2 — correctly: it reviews a PASS. But it stated no class, so the
reader fell back to its fail-closed default and booked `EXECUTION_ERROR`, and
every one of those six steps inherited INCOMPLETE from a sentence about
somebody ELSE's step.

It looked. It read the register. It is WAITING. That is `BLOCKED_BY_UPSTREAM`,
and the class is now STATED in the field `report_reason_class` reads before any
prose recogniser (#2275 / #2276), together with the ROWS it waits on so a
reader can act on it.

THE NEGATIVE CONTROL IS THE POINT, and it is the neighbouring branch: a review
that truly COULD NOT RUN — no register, an unreadable one, or one carrying no
row for this stage — has established nothing, states nothing, and keeps the
fail-closed `EXECUTION_ERROR`. `_p0_declared_absent`'s rule in spirit: the
register must EXIST and say the rows are not green.

chip-AGNOSTIC: synthetic compliance registers in tmp_path.
"""
from __future__ import annotations

import json
import subprocess  # nosec B404 — a declared flow program
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import pytest  # noqa: E402

import stage_on_pass_review as S  # noqa: E402
import _flow_reason_taxonomy as R  # noqa: E402


def _register(tmp_path, rows, stage="stage1", name="c.json"):
    p = tmp_path / name
    p.write_text(json.dumps({"steps": [
        {"id": i, "stage": stage, "status": st} for i, st in rows]}))
    return p


# ── the rows are named, not just the words ────────────────────────────────

def test_stage_passed_names_the_rows_it_waits_on(tmp_path):
    got = S.stage_passed(
        _register(tmp_path, [("1", "PASS"), ("2", "NOT_MEASURED"),
                             ("P0", "FAIL")]), "stage1", None)
    assert got["passed"] is False
    assert got["non_green_rows"] == [
        {"id": "2", "status": "NOT_MEASURED"},
        {"id": "P0", "status": "FAIL"}], got


def test_a_green_stage_names_no_rows(tmp_path):
    got = S.stage_passed(
        _register(tmp_path, [("1", "PASS"), ("6", "PASS_WITH_WAIVERS")]), "stage1", None)
    assert got["passed"] is True
    assert got["non_green_rows"] == []


# ── direction 1: waiting on a non-green row is BLOCKED_BY_UPSTREAM ────────

def _run(tmp_path, register, stage="stage1"):
    out = tmp_path / "report.json"
    argv = [sys.executable, str(PROGRAMS / "stage_on_pass_review.py"),
            str(tmp_path), "--stage", stage, "--json", str(out)]
    if register is not None:
        argv += ["--compliance", str(register)]
    r = subprocess.run(argv, capture_output=True, text=True,  # nosec B603
                       check=False)
    doc = json.loads(out.read_text()) if out.is_file() else None
    return r, doc


def test_the_review_no_longer_waits_and_publishes_what_it_went_past(tmp_path):
    """R-0915-34a's WAIT is exactly what R-0915-85 deleted, and this file is
    where the two rulings meet.

    R-0915-34a was right that a review which waits must SAY it is waiting and
    on what — the alternative was a silent NOT_CHECKED that the completion
    audit read as "this step's whole population is unexamined". What R-0915-85
    changes is the waiting, not the saying: the review reads the stage's
    ARTEFACTS, which are on disk either way, and r26 measured what waiting
    costs — steps 2, 7, 15 and 37 NOT_MEASURED behind one review that never
    looked, and a run published FAIL in which every gate that looked passed.

    So the disclosure is STRICTLY LARGER than the one 34a asked for: the record
    names the rows the review proceeded past, rather than naming the rows it
    refused over.
    """
    reg = _register(tmp_path, [("1", "PASS"), ("2", "NOT_MEASURED")])
    r, doc = _run(tmp_path, reg)
    assert doc is not None, "the review wrote no report"
    assert doc.get("reviewed_over_non_green_rows") == [
        {"id": "2", "status": "NOT_MEASURED"}], doc
    assert "REVIEWING ANYWAY" in r.stdout, r.stdout
    # and it did not simply refuse: the record it wrote is the REVIEW's own,
    # carrying the rules it applied rather than a single decline word.
    assert doc.get("rules") or doc.get("unproven_rejections") is not None, doc


def test_the_rows_it_went_past_reach_the_human_line(tmp_path):
    reg = _register(tmp_path, [("1", "PASS"), ("2", "NOT_MEASURED"),
                               ("P0", "FAIL")])
    r, _doc = _run(tmp_path, reg)
    assert "Non-green rows disclosed:" in r.stdout, r.stdout
    assert "2=NOT_MEASURED" in r.stdout and "P0=FAIL" in r.stdout, r.stdout


# ── direction 2: a review that truly cannot run is still a fault ──────────

@pytest.mark.parametrize("what", ["no-register", "unreadable", "no-row"])
def test_a_review_that_could_not_run_states_nothing(tmp_path, what):
    """It has established nothing about anyone, so it may not name a cascade
    it did not observe — the fail-closed default stands."""
    reg = None
    if what == "unreadable":
        reg = tmp_path / "c.json"
        reg.write_text("{ not json")
    elif what == "no-row":
        reg = _register(tmp_path, [("1", "PASS")], stage="stageZ")
    _r, doc = _run(tmp_path, reg)
    if doc is not None:
        assert doc.get("reason_class") is None, (what, doc)
        assert R.report_reason_class(doc) is None, (what, doc)
        assert "blocked_by" not in doc, (what, doc)


def test_an_unestablished_verdict_is_not_a_cascade(tmp_path):
    """`stage_passed` returns None there, and None is not False."""
    assert S.stage_passed(None, "stage1", None)["passed"] is None
    assert S.stage_passed(
        _register(tmp_path, [("1", "PASS")]), "stageZ", None)["passed"] is None


def test_BLOCKED_BY_UPSTREAM_is_not_skip_eligible():
    """Accuracy only, as ruled: naming the cascade must not green anything."""
    assert R.BLOCKED_BY_UPSTREAM not in R.SKIP_ELIGIBLE
    assert R.record_verdict(R.BLOCKED_BY_UPSTREAM) == "BLOCKED"
