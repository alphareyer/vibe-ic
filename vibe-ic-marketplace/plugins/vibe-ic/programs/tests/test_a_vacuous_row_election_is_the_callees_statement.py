"""R-0915-135, reworked (lane ictier1) — the vacuous row's class is what the CALLEE
STATED, carried where no token reader looks, and voted by EVERY vacuous clause.

THE RULING. The NOT_MEASURED row of a vacuous clause carries the callee's own
classified first line and the class IT STATED, mapped to the row vocabulary; an
unmapped class, a disagreement, or a silent clause DECLINES (the row keeps its
previous reason_class). The first payload line stays the command identity.

THE THREE DEFECTS THE PRE-LANDING REVIEW CONFIRMED on next/icslot61 (54e542634),
each pinned here through the real `check_step` over real callee processes, RED on
that tip:

  1. The diagnostic rode inside `out`, where the line-start token readers
     (`INCOMPLETE:`, `STRUCTURE_ONLY:`, `SUBSTANTIVE_PASS`) scan every line: callee
     TEXT alone moved a step PASS/partial_vacuity -> NOT_MEASURED/
     partial_population.
  2. The election read only legacy `__VACUOUS_HINT__` payloads; a clause vacuous
     only through its JSON report (`{"verdict": "NOT_APPLICABLE", "reason_class":
     "EXTERNAL"}`) counted toward unanimity but did not vote, so its dissent never
     declined the election.
  3. The "stated" class was the audit's own prose inference: a callee saying
     `NOT_MEASURED [EXTERNAL]: ... needs no tool here` was elected
     CAPABILITY_ABSENT -> tool_absent because "no tool" matched first.
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
_NM = _T.Verdict.NOT_MEASURED.value
_NO_POP = _T.ReasonClass.NO_POPULATION.value
_PARTIAL = _T.ReasonClass.PARTIAL_POPULATION.value
_TOOL_ABSENT = _T.ReasonClass.TOOL_ABSENT.value

_GATE = '''import json, sys
from pathlib import Path
LINES = {lines!r}
RC = {rc!r}
REPORT = {report!r}
argv = sys.argv[1:]
if REPORT is not None and "--json" in argv:
    p = Path(argv[argv.index("--json") + 1])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(REPORT))
for line in LINES:
    print(line)
sys.exit(RC)
'''


def _gate(tmp_path: Path, name: str, lines, rc: int, report=None) -> str:
    """A callee on disk, named by ABSOLUTE path (which `_resolve_program_cmd`
    honours), so nothing is written into the shipped programs tree."""
    p = tmp_path / "gates" / f"{name}.py"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_GATE.format(lines=list(lines), rc=rc, report=report))
    return str(p)


def _step(*clauses):
    return {"id": 1, "name": "the step under audit", "stage": "stage1",
            "gate": {"all_of": [{"program_exit_zero": c} for c in clauses]}}


def _check(tmp_path: Path, *clauses):
    project = tmp_path / "proj"
    project.mkdir(exist_ok=True)
    return FCC.check_step(project, _step(*clauses), {})


def _substantive(tmp_path):
    return _gate(tmp_path, "substantive_check", ["[PASS] examined 12 nets"], 0)


# ── finding 1: callee text never reaches the token readers ─────────────────

_VACUOUS_THEN_INCOMPLETE = ["VACUOUS_PASS: no simulator available",
                            "INCOMPLETE: waveform compare skipped"]


def test_an_incomplete_line_in_a_vacuous_callee_does_not_move_a_pass(tmp_path):
    """Review scenario: clause A substantive, clause B rc 2 printing a
    VACUOUS_PASS line then an INCOMPLETE line. Base: PASS with partial vacuity.
    icslot61: NOT_MEASURED(partial_population), on text alone."""
    b = _gate(tmp_path, "foo_check", _VACUOUS_THEN_INCOMPLETE, 2)
    r = _check(tmp_path, f"{_substantive(tmp_path)} .", f"{b} .")
    assert r.status == _PASS, (r.status, r.reason_class, r.reasons)
    assert r.partial_vacuity_disclosed, r.reasons


def test_an_incomplete_line_in_a_lone_vacuous_callee_keeps_the_vacuous_row(
        tmp_path):
    """Same callee alone: base NOT_MEASURED(no_population); icslot61 made it
    partial_population ("its input was applicable and was NOT examined")."""
    b = _gate(tmp_path, "foo_check", _VACUOUS_THEN_INCOMPLETE, 2)
    r = _check(tmp_path, f"{b} .")
    assert (r.status, r.reason_class) == (_NM, _NO_POP), (r.status,
                                                         r.reason_class)


def test_a_structure_only_line_in_a_vacuous_callee_discloses_nothing(tmp_path):
    b = _gate(tmp_path, "bar_check", ["VACUOUS_PASS: no simulator available",
                                      "STRUCTURE_ONLY: nothing bound"], 2)
    r = _check(tmp_path, f"{_substantive(tmp_path)} .", f"{b} .")
    assert not r.structure_only_disclosed, r.reasons
    assert not any("STRUCTURE-ONLY" in x for x in r.reasons), r.reasons


def test_the_callees_line_still_reaches_the_row(tmp_path):
    """The ruling's positive half survives the move out of `out`."""
    b = _gate(tmp_path, "xor_check", [
        "NOT_MEASURED [CAPABILITY_ABSENT]: no KLayout runner reaches this "
        "project, so the comparison was not performed"], 2)
    r = _check(tmp_path, f"{b} .")
    assert (r.status, r.reason_class) == (_NM, _TOOL_ABSENT), (r.status,
                                                               r.reason_class)
    assert any("no KLayout runner reaches this project" in x
               for x in r.reasons), r.reasons


# ── finding 2: every member of all_vacuous_cmds votes ──────────────────────

def test_a_json_only_vacuous_clause_that_states_another_class_declines(
        tmp_path):
    """Review scenario: A rc 2 stating CAPABILITY_ABSENT; B exits 0, prints
    nothing vacuous, and its report says NOT_APPLICABLE / EXTERNAL. Both are
    vacuous (unanimous), B dissents -> decline to no_population. icslot61
    elected tool_absent off A alone."""
    a = _gate(tmp_path, "xor_check", [
        "NOT_MEASURED [CAPABILITY_ABSENT]: no KLayout runner reaches this "
        "project"], 2)
    b = _gate(tmp_path, "bar_check", ["[OK] bar_check"], 0,
              report={"verdict": "NOT_APPLICABLE", "reason_class": "EXTERNAL"})
    r = _check(tmp_path, f"{a} .", f"{b} . --json reports/r.json")
    assert r.status == _NM, (r.status, r.reasons)
    assert r.reason_class == _NO_POP, r.reason_class


def test_a_json_only_vacuous_clause_that_agrees_is_a_vote(tmp_path):
    """Control: the same JSON-only clause stating the SAME class is a vote for
    it, so the election still carries."""
    a = _gate(tmp_path, "xor_check", [
        "NOT_MEASURED [CAPABILITY_ABSENT]: no KLayout runner reaches this "
        "project"], 2)
    b = _gate(tmp_path, "bar_check", ["[OK] bar_check"], 0,
              report={"verdict": "NOT_APPLICABLE",
                      "reason_class": "CAPABILITY_ABSENT"})
    r = _check(tmp_path, f"{a} .", f"{b} . --json reports/r.json")
    assert (r.status, r.reason_class) == (_NM, _TOOL_ABSENT), (r.status,
                                                               r.reason_class)


# ── finding 3: only a STATED class is elected; an inferred one never ───────

def test_a_callee_that_states_external_is_not_elected_tool_absent(tmp_path):
    """Review scenario: `no tool` matches the prose capability recogniser
    before EXTERNAL is tried. The callee STATED EXTERNAL, which has no row
    word -> decline."""
    b = _gate(tmp_path, "board_check", [
        "NOT_MEASURED [EXTERNAL]: board-level bring-up needs no tool here"], 2)
    r = _check(tmp_path, f"{b} .")
    assert r.status == _NM, r.status
    assert r.reason_class == _NO_POP, r.reason_class


def test_a_callee_that_states_no_class_is_silent_and_declines(tmp_path):
    """Review scenario: no class stated at all; `no simulator` is the audit's
    inference, and an inferred class is never elected."""
    b = _gate(tmp_path, "deck_check", [
        "VACUOUS_PASS: design declares no analog block, so no simulator deck "
        "applies"], 2)
    r = _check(tmp_path, f"{b} .")
    assert r.status == _NM, r.status
    assert r.reason_class == _NO_POP, r.reason_class


def test_an_inferred_class_on_the_row_is_tagged_as_inference(tmp_path):
    """The row may still show what the audit inferred, but never as a class
    the callee stated."""
    b = _gate(tmp_path, "deck_check", [
        "VACUOUS_PASS: design declares no analog block, so no simulator deck "
        "applies"], 2)
    r = _check(tmp_path, f"{b} .")
    joined = "\n".join(r.reasons)
    assert "stated_class=" not in joined, joined
    assert "reason_class=CAPABILITY_ABSENT" not in joined, joined
