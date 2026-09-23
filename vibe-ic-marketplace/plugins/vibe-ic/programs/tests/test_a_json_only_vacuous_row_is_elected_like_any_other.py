"""R-0915-135 follow-up (lane ictier1d) — the JSON channel gets the same row.

Review of next/ictier1b (LOW, confirmed): a step whose vacuous clauses are ALL
JSON-channel (the callee exits 0 and its own --json report says NOT_APPLICABLE
with a stated `reason_class`) took a branch that never ran the class election
and never carried the callee's line: `{B}` alone read no_population while
`{A(rc 2), B}` stating the same class read tool_absent -- the election was not
monotonic, and the same statement got a different class by exit code. In the
mixed case B voted but was named on no row line.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import flow_compliance_check as FCC                          # noqa: E402

_T = FCC._T
_NM = _T.Verdict.NOT_MEASURED.value
_NO_POP = _T.ReasonClass.NO_POPULATION.value
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


def _gate(tmp_path, name, lines, rc, report=None):
    p = tmp_path / "gates" / f"{name}.py"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_GATE.format(lines=list(lines), rc=rc, report=report))
    return str(p)


def _check(tmp_path, *clauses):
    proj = tmp_path / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    step = {"id": 1, "name": "probe", "stage": "stage1",
            "gate": {"all_of": [{"program_exit_zero": c} for c in clauses]}}
    return FCC.check_step(proj, step, {})


_NO_SIM = {"verdict": "NOT_APPLICABLE", "reason_class": "CAPABILITY_ABSENT",
           "reason": "no simulator on PATH"}


def test_a_json_only_clause_that_states_a_mapped_class_is_elected(tmp_path):
    b = _gate(tmp_path, "b_check", ["[OK] b"], 0, report=_NO_SIM)
    r = _check(tmp_path, f"{b} . --json r.json")
    assert (r.status, r.reason_class) == (_NM, _TOOL_ABSENT), (
        r.status, r.reason_class, r.reasons)
    assert any("no simulator on PATH" in x for x in r.reasons), r.reasons


def test_the_same_statement_gets_the_same_class_on_either_exit_code(tmp_path):
    rc0 = _gate(tmp_path, "rc0_check", ["[OK]"], 0, report=_NO_SIM)
    rc2 = _gate(tmp_path, "rc2_check",
                ["NOT_MEASURED [CAPABILITY_ABSENT]: no simulator on PATH"], 2)
    a = _check(tmp_path / "a", f"{rc0} . --json r.json")
    b = _check(tmp_path / "b", f"{rc2} .")
    assert a.reason_class == b.reason_class == _TOOL_ABSENT, (
        a.reason_class, b.reason_class)


def test_a_json_only_clause_stating_an_unmapped_class_declines(tmp_path):
    b = _gate(tmp_path, "ext_check", ["[OK]"], 0,
              report={"verdict": "NOT_APPLICABLE", "reason_class": "EXTERNAL",
                      "reason": "board bring-up is off-chip"})
    r = _check(tmp_path, f"{b} . --json r.json")
    assert (r.status, r.reason_class) == (_NM, _NO_POP), r.reason_class
    assert any("board bring-up is off-chip" in x for x in r.reasons), r.reasons


def test_every_voter_is_named_on_the_row(tmp_path):
    """Mixed: A (rc 2) and B (JSON-only) both state CAPABILITY_ABSENT. B's vote
    counts, so B's clause and its line must be on the row too."""
    a = _gate(tmp_path, "xor_check",
              ["NOT_MEASURED [CAPABILITY_ABSENT]: no KLayout runner here"], 2)
    b = _gate(tmp_path, "b_check", ["[OK] b"], 0, report=_NO_SIM)
    r = _check(tmp_path, f"{a} .", f"{b} . --json r.json")
    assert (r.status, r.reason_class) == (_NM, _TOOL_ABSENT), r.reason_class
    joined = "\n".join(r.reasons)
    assert "b_check.py . --json r.json" in joined, joined
    assert "no simulator on PATH" in joined, joined
    assert "no KLayout runner here" in joined, joined
