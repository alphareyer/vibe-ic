"""R-0915-126 / step 26.5ic -- PRODUCER WRITES, GATE READS.

spm's DESIGN PASSED on run18L and the completion audit still refused step 26.5ic:

    AUDIT-CREATED OUTPUT REFUSED: ['reports/phase3/die_finishing.json'] --
    present, but written by this step's own gate rather than by the run, so the
    step has no run evidence

while that same step's `written.json` recorded 2 of 2 declared outputs produced,
and `reports/audit/audit_created/*.json` recorded
`written_by: flow_compliance_check`. TWO defects, both in the reader:

1. THE AUDIT DESTROYED THE EVIDENCE IT THEN REFUSED. The step's gate clause is
   `die_finishing_check . --json reports/phase3/die_finishing.json`, and that path
   is one of the step's own `required_outputs`. Evaluating the clause therefore
   rewrote the producer's document. MEASURED: re-running the audit changed that
   file's md5. The flow's own clause comment already stated the rule --
   "A document the run already produced is left byte-for-byte alone."

2. THE CLASSIFIER READ A PRODUCER'S DOCUMENT AS THE GATE'S.
   `_is_gate_verdict_document` collects identity stamps from
   `("program", "check", "gate", "emitted_by")`. run18L's die_finishing.json
   carries NO `program`; its stamps are `check: "die_finishing"` at top level and
   inside `run` -- the name of the CHECK THE PRODUCER RAN. That matches neither
   `die_finishing_gen` nor `die_finishing_check`, so the comparison fell through to
   `return True`, exactly as the 2026-09-15 comment in that function warns. The
   document says whose it is -- `run.producer: "die_finishing_gen"` -- under a key
   nothing read.
"""
import json
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import flow_compliance_check as F                          # noqa: E402

_GATES = frozenset({"die_finishing_check"})
_PRODUCERS = frozenset({"die_finishing_gen", "die_finishing_check"})


def _doc(tmp_path, payload) -> Path:
    p = tmp_path / "die_finishing.json"
    p.write_text(json.dumps(payload, indent=1) + "\n")
    return p


# -- (2) the classifier -------------------------------------------------------
def test_a_producer_stamped_document_is_the_runs(tmp_path):
    """THE RED: run18L's exact stamp shape -- a `check` name and a `run.producer`."""
    p = _doc(tmp_path, {
        "verdict": "PASS", "check": "die_finishing",
        "run": {"producer": "die_finishing_gen", "check": "die_finishing"}})
    assert F._is_gate_verdict_document(p, _GATES, _PRODUCERS) is False


def test_a_gate_written_receipt_is_still_refused(tmp_path):
    """The half of the ruling that must not move -- for a gate that is ONLY a
    gate. `_GATES` here is not among the step's producers, which is the ordinary
    shape."""
    p = _doc(tmp_path, {"program": "some_gate_check", "verdict": "PASS"})
    assert F._is_gate_verdict_document(
        p, frozenset({"some_gate_check"}), frozenset({"some_producer_gen"})
    ) is True


def test_a_producer_stamp_cannot_launder_a_gate_only_program(tmp_path):
    """A `producer` stamp naming a program that is a GATE of this step and not a
    producer of it does NOT make the document the run's -- the new read is
    scoped to producers that are not also gates."""
    p = _doc(tmp_path, {"program": "some_gate_check",
                        "run": {"producer": "some_gate_check"}})
    assert F._is_gate_verdict_document(
        p, frozenset({"some_gate_check"}), frozenset({"some_producer_gen"})
    ) is True


def test_a_program_that_is_both_producer_and_gate_is_credited(tmp_path):
    """PINNED AS FOUND, not as I first expected. 26.5ic lists
    `die_finishing_check` in BOTH `programs:` and its gate clause, and that
    shared shape is deliberately credited -- "the run produced its evidence and
    the auditor's pen landed on top of it", 13 of the 34 declared-output/own-gate
    pairs in the shipped flow. My first version of this file asserted True here
    and was wrong about the tree."""
    p = _doc(tmp_path, {"program": "die_finishing_check", "verdict": "PASS"})
    assert F._is_gate_verdict_document(p, _GATES, _PRODUCERS) is False


def test_a_document_with_no_stamp_at_all_stays_run_evidence(tmp_path):
    """Unchanged: unreadable-as-a-stamp is treated as NOT the auditor's."""
    p = _doc(tmp_path, {"seal_ring": {"verified": True}})
    assert F._is_gate_verdict_document(p, _GATES, _PRODUCERS) is False


# -- (1) the receipt redirect -------------------------------------------------
def test_a_receipt_over_an_existing_document_is_redirected(tmp_path):
    (tmp_path / "reports" / "phase3").mkdir(parents=True)
    doc = tmp_path / "reports" / "phase3" / "die_finishing.json"
    doc.write_text('{"run": {"producer": "die_finishing_gen"}}\n')
    argv = ["python3", "die_finishing_check.py", ".",
            "--json", "reports/phase3/die_finishing.json"]
    moved, note, keep = F._receipt_off_a_produced_document(argv, tmp_path)
    assert note and "RECEIPT REDIRECTED" in note
    assert moved[-1] != argv[-1]
    assert not moved[-1].startswith(str(tmp_path)), moved[-1]
    assert keep is not None, "the scratch dir must outlive the call"


def test_a_receipt_for_an_absent_document_is_left_alone(tmp_path):
    """Narrow on purpose: with nothing to protect, the pre-existing
    audit_created disclosure and its refusal are untouched."""
    argv = ["python3", "g.py", ".", "--json", "reports/phase3/absent.json"]
    moved, note, keep = F._receipt_off_a_produced_document(argv, tmp_path)
    assert (moved, note, keep) == (argv, None, None)


def test_a_clause_with_no_receipt_flag_is_left_alone(tmp_path):
    argv = ["python3", "g.py", "."]
    assert F._receipt_off_a_produced_document(argv, tmp_path) == (argv, None, None)
