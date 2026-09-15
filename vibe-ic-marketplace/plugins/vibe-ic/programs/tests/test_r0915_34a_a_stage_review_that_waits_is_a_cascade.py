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
        _register(tmp_path, [("1", "PASS"), ("2", "INCOMPLETE"),
                             ("P0", "MISSING")]), "stage1", None)
    assert got["passed"] is False
    assert got["non_green_rows"] == [
        {"id": "2", "status": "INCOMPLETE"},
        {"id": "P0", "status": "MISSING"}], got


def test_a_green_stage_names_no_rows(tmp_path):
    got = S.stage_passed(
        _register(tmp_path, [("1", "PASS"), ("6", "WAIVED")]), "stage1", None)
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


def test_a_review_that_waits_states_BLOCKED_BY_UPSTREAM(tmp_path):
    reg = _register(tmp_path, [("1", "PASS"), ("2", "INCOMPLETE")])
    r, doc = _run(tmp_path, reg)
    assert r.returncode == 2, r.stdout + r.stderr
    assert doc is not None, "the review wrote no report"
    assert doc["reason_class"] == R.BLOCKED_BY_UPSTREAM, doc
    assert doc["blocked_by"] == [{"id": "2", "status": "INCOMPLETE"}], doc
    assert R.report_reason_class(doc) == R.BLOCKED_BY_UPSTREAM


def test_the_reader_takes_that_class_over_its_own_default(tmp_path):
    """The whole point: without a stated class the sentence falls through to
    the fail-closed EXECUTION_ERROR."""
    reg = _register(tmp_path, [("1", "PASS"), ("2", "INCOMPLETE")])
    _r, doc = _run(tmp_path, reg)
    assert R.infer_nonverdict_reason(
        verdict="NOT_CHECKED", message=doc["why"],
        evidence={"reason_class": R.report_reason_class(doc)}
    ) == R.BLOCKED_BY_UPSTREAM
    # and the same sentence with NO stated class keeps the old reading
    assert R.infer_nonverdict_reason(
        verdict="NOT_CHECKED", message=doc["why"],
        evidence={"exit_code": 2}) != R.BLOCKED_BY_UPSTREAM


def test_the_waiting_rows_reach_the_human_line(tmp_path):
    reg = _register(tmp_path, [("1", "PASS"), ("2", "INCOMPLETE"),
                               ("P0", "MISSING")])
    r, _doc = _run(tmp_path, reg)
    assert "BLOCKED_BY_UPSTREAM, waiting on:" in r.stdout, r.stdout
    assert "2=INCOMPLETE" in r.stdout and "P0=MISSING" in r.stdout, r.stdout


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
