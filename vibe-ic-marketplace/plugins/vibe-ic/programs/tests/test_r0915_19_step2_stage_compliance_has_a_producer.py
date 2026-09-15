"""Steps 2 and 14 declare the producer of the stage-compliance document they own.

MEASURED 2026-09-15 (lane icspm3) on the SPM verdict run at main 79506306d.
`reports/phase1/gates/stage_phase1_compliance.json` came back `audit_created`
where the immediately preceding run on the same content had it
`step_attributed`; step 2 went MISSING instead of INCOMPLETE and
`stage1_compliance` FAILED for the first time in the lane — a FIFTH failed gate
produced by nothing but timing. Host load average was 67.5 and the run took
1670 s against ~770 s nominal.

WHY IT IS A RACE, and why the first pass of this work left it. That document is
written by `flow_compliance_check --json`, whose report carries none of
`_GATE_DOCUMENT_IDENTITY_KEYS`, so `_is_gate_verdict_document` cannot answer
from content and the classifier falls back to its two TIMING facts: was the
file absent when the audit began, and did an earlier pass of the audit create
it. With no declared producer, the answer is whatever the run happened to do
first. Declaring the producer makes `flow_declared_producer_run` — the
RUNNER's, never the auditor's — write it during the run, every time, so the
race has no side to land on. Same shape as the thirteen of #2261.

chip-AGNOSTIC: everything is read from the shipped yaml.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import pytest  # noqa: E402

FLOW_YAML = PROGRAMS.parent / "flow" / "phase1_phase2_phase3.yaml"

#: (step, program, target) — the two clauses this commit declares.
ADDED = (
    ("2", "flow_compliance_check",
     "reports/phase1/gates/stage_phase1_compliance.json"),
    ("14", "flow_compliance_check",
     "reports/analog/stage_analog_compliance.json"),
)


def _P():
    import flow_declared_producer_run as P  # noqa: PLC0415
    return P


def _steps():
    yaml = pytest.importorskip("yaml")
    P = _P()
    doc = yaml.safe_load(FLOW_YAML.read_text(errors="replace"))
    return {str(s.get("id")): s for s in P._iter_steps(doc)}


@pytest.mark.parametrize("sid,program,target", ADDED,
                         ids=[f"{a}:{c.split('/')[-1]}" for a, _b, c in ADDED])
def test_all_three_declarations_agree(sid, program, target):
    P = _P()
    step = _steps()[sid]
    assert program in [str(p).strip() for p in (step.get("programs") or [])]
    assert target in [str(o) for o in (step.get("required_outputs") or [])]
    cmds = P._iter_commands(step.get("gate") or {}, [])
    assert any(c.split()[:1] == [program] and P._JSON_RE.search(c)
               and P._JSON_RE.search(c).group(1) == target for c in cmds), sid


def test_both_are_picked_up_by_the_mechanism():
    P = _P()
    got = {(c["step"], c["program"], c["target"])
           for c in P.declared_producer_clauses()}
    missing = [row for row in ADDED if row not in got]
    assert not missing, missing


def test_the_run_owes_the_document_when_the_step_was_performed(tmp_path):
    """Direction 1: absent on a step the run performed → owed, so the RUN
    writes it and the race has no side to land on."""
    P = _P()
    clause = [c for c in P.declared_producer_clauses()
              if (c["step"], c["program"], c["target"]) == ADDED[0]]
    assert clause, "step 2's clause is gone"
    sib = clause[0]["siblings"][0]
    (tmp_path / sib).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / sib).write_text("the run performed step 2\n")
    to_run, skipped = P.owed(tmp_path, clause)
    assert [r["target"] for r in to_run] == [clause[0]["target"]], skipped


def test_a_document_the_run_already_wrote_is_left_alone(tmp_path):
    """Direction 2: this adds work, it never overwrites the run's evidence."""
    P = _P()
    clause = [c for c in P.declared_producer_clauses()
              if (c["step"], c["program"], c["target"]) == ADDED[0]]
    sib = clause[0]["siblings"][0]
    (tmp_path / sib).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / sib).write_text("x\n")
    tgt = tmp_path / clause[0]["target"]
    tgt.parent.mkdir(parents=True, exist_ok=True)
    tgt.write_text('{"the run": "wrote this"}')
    to_run, _skipped = P.owed(tmp_path, clause)
    assert not to_run
    assert tgt.read_text() == '{"the run": "wrote this"}'


def test_a_step_the_run_never_performed_is_still_not_owed(tmp_path):
    """The guard is untouched: no sibling evidence means the run did not do
    the step, and its document is not owed."""
    P = _P()
    clause = [c for c in P.declared_producer_clauses()
              if (c["step"], c["program"], c["target"]) == ADDED[1]]
    assert clause
    to_run, skipped = P.owed(tmp_path, clause)
    assert not to_run
    assert skipped and "did not perform this step" in skipped[0]["why"]


def test_the_documents_identity_gap_is_still_real():
    """The premise: this IS the shape whose content cannot decide, which is
    why the timing race existed and why a producer is the fix."""
    import flow_compliance_check as F  # noqa: PLC0415
    assert "flow_compliance_check" not in "".join(
        str(k) for k in F._GATE_DOCUMENT_IDENTITY_KEYS)
