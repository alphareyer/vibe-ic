"""The digest asks WHO INVOKED the writer, not only which program wrote it.

R-0915-152, the interaction between the two rulings. `design_input_digest` subtracts the
auditor's own writes before hashing the design, and identified a compliance receipt as the
auditor's by `program: flow_compliance_check`. That name says which program produced the
document; it does not say whether the RUN ran it or the AUDIT did -- and the flow lists
`flow_compliance_check` under `programs:` for steps 2, 14, 15 and 37, so
`flow_declared_producer_run` invokes it as the step's producer and its receipt IS the step's
run evidence.

MEASURED on a real producer-invoked pass, which is the arm below: the receipt is written
`invoked_as: producer`, and the name-only rule called it the auditor's -- so the run's own
evidence was subtracted from the design the digest hashes. Both costs are invisible: an edit
to that receipt never moves the design hash, so a tally that moves with it is published as
unexplained, and the block's own `file_count` omits it without saying so.

The role decides WHEN THE DOCUMENT STATES ONE. A document written before the stamp existed
states none -- `role_of` returns None for those, never `producer` -- and keeps today's
name-based answer.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import design_input_digest as D             # noqa: E402
import _gate_authorship as GA               # noqa: E402
import _path_layout as _pl                  # noqa: E402

RECEIPT_REL = "reports/phase1/gates/stage_phase1_compliance.json"


@pytest.fixture()
def project(tmp_path):
    p = tmp_path / "proj"
    (p / "input").mkdir(parents=True)
    (p / "input" / "spec.md").write_text("# a counter\n")
    return p


def _sha(project: Path) -> str:
    blk = D.build_digest(D.scan_inputs(project), [])
    assert blk["unusable_reason"] is None, blk
    return blk["sha256"]


def _kept(project: Path) -> set:
    return set(D.kept_inputs(D.scan_inputs(project), []))


def _receipt(project: Path, role, **extra) -> Path:
    f = project / RECEIPT_REL
    f.parent.mkdir(parents=True, exist_ok=True)
    doc = {"program": "flow_compliance_check", "overall": "PASS",
           "steps": [{"id": "2", "status": "PASS"}], **extra}
    if role is not None:
        doc[GA.DOC_KEY] = role
    f.write_text(json.dumps(doc, indent=2) + "\n")
    return f


# ── the run's own evidence ───────────────────────────────────────────────────

def test_a_producer_stamped_receipt_is_the_runs_evidence(project):
    """The step's own receipt, written by the RUN. It is part of the design that is hashed."""
    f = _receipt(project, GA.ROLE_PRODUCER)
    assert D.is_auditor_output(project, f) is False, (
        "the run's own receipt was subtracted from the design the digest hashes")
    assert RECEIPT_REL in _kept(project)

    before = _sha(project)
    _receipt(project, GA.ROLE_PRODUCER, overall="FAIL")
    assert _sha(project) != before, (
        "editing the run's own evidence did not move the design hash, so a tally that "
        "moves with it is published as unexplained")


def test_an_audit_stamped_receipt_is_the_auditors(project):
    f = _receipt(project, GA.ROLE_AUDIT)
    assert D.is_auditor_output(project, f) is True
    assert RECEIPT_REL not in _kept(project)

    before = _sha(project)
    _receipt(project, GA.ROLE_AUDIT, overall="FAIL")
    assert _sha(project) == before, (
        "the auditor's own verdict changed the design hash")


def test_the_same_path_flips_with_the_role_and_nothing_else(project):
    """One file, one program name, two answers -- which is the whole point of the change."""
    seen = {}
    for role in (GA.ROLE_PRODUCER, GA.ROLE_AUDIT):
        f = _receipt(project, role)
        seen[role] = (D.is_auditor_output(project, f), _sha(project))
    assert seen[GA.ROLE_PRODUCER][0] is False
    assert seen[GA.ROLE_AUDIT][0] is True
    assert seen[GA.ROLE_PRODUCER][1] != seen[GA.ROLE_AUDIT][1], (
        "the two documents differ only in `invoked_as`, and the producer one is hashed "
        "while the audit one is not -- so the design hashes must differ")


# ── documents that state no role keep today's answer ─────────────────────────

def test_a_role_less_receipt_keeps_the_name_based_answer(project):
    """Written before the stamp existed. Silence is not a claim of being the run's."""
    f = _receipt(project, None)
    assert GA.role_of(json.loads(f.read_text())) is None
    assert D.is_auditor_output(project, f) is True, (
        "a document that predates the stamp lost the name-based rule it relied on")

    before = _sha(project)
    _receipt(project, None, overall="FAIL")
    assert _sha(project) == before


def test_an_authorship_note_has_no_role_and_is_still_the_auditors(project):
    """The notes stamp `written_by`, never `invoked_as` -- driven through the real writer."""
    import flow_compliance_check as FCC

    noted = project / "reports/audit/tapeout_checklist.json"
    noted.parent.mkdir(parents=True, exist_ok=True)
    noted.write_text(json.dumps({"checks": []}) + "\n")
    FCC._record_audit_created(project, "36", ["reports/audit/tapeout_checklist.json"])
    notes = sorted((project / "reports/audit/audit_created").glob("*.json"))
    assert notes, "the real writer left no note"
    body = json.loads(notes[0].read_text())
    assert GA.role_of(body) is None and body["written_by"] == "flow_compliance_check"
    assert D.is_auditor_output(project, notes[0]) is True


def test_a_producer_stamped_document_of_another_program_is_unaffected(project):
    """The change is about whose INVOCATION wrote it; another program's document is a design
    input either way, and must not become the auditor's by carrying the key."""
    f = project / "reports/phase2/gates/sdc_gen.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    for role in (GA.ROLE_PRODUCER, GA.ROLE_AUDIT, None):
        doc = {"program": "sdc_gen", "clocks": 1}
        if role is not None:
            doc[GA.DOC_KEY] = role
        f.write_text(json.dumps(doc) + "\n")
        expect = (role == GA.ROLE_AUDIT)
        assert D.is_auditor_output(project, f) is expect, (role, expect)


# ── the shapes that cannot carry a stamp keep their own rules ────────────────

def test_the_completion_audit_stays_the_auditors_by_path(project):
    """It carries no `invoked_as` -- measured -- so the exact-path rule still answers."""
    audit = _pl.report_path(project, "phase23_completion_audit.json")
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(json.dumps({"verdict": "PASS"}) + "\n")
    assert GA.role_of(json.loads(audit.read_text())) is None
    assert D.is_auditor_output(project, audit) is True

    # and even if something stamped it `producer`, the path rule is read first: this
    # document is the auditor's by construction and no producer writes it.
    audit.write_text(json.dumps({"verdict": "PASS",
                                 GA.DOC_KEY: GA.ROLE_PRODUCER}) + "\n")
    assert D.is_auditor_output(project, audit) is True


def test_a_superseded_copy_stays_the_auditors_whatever_it_says(project):
    """That name is minted only by the republish, so the shape answers before the content."""
    sup = project / "reports/phase1/gates/stage_phase1_compliance.superseded-1.json"
    sup.parent.mkdir(parents=True, exist_ok=True)
    sup.write_text(json.dumps({"program": "flow_compliance_check",
                               GA.DOC_KEY: GA.ROLE_PRODUCER}) + "\n")
    assert D.is_auditor_output(project, sup) is True


# ── end to end, through the real program ─────────────────────────────────────

def test_a_real_producer_invoked_pass_leaves_its_receipt_in_the_design(project):
    """The measurement this whole change came from, run rather than staged.

    `flow_compliance_check <project> --stage-id stage_phase1 --json <receipt>` with NO role
    stated is how `flow_declared_producer_run` invokes it for step 2. The receipt it writes
    must be part of the design the digest hashes.
    """
    receipt = project / RECEIPT_REL
    receipt.parent.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k != GA.ROLE_ENV}
    subprocess.run(
        [sys.executable, str(PROGRAMS / "flow_compliance_check.py"), str(project),
         "--stage-id", "stage_phase1", "--json", str(receipt)],
        capture_output=True, text=True, cwd=str(project), env=env, timeout=900)

    assert receipt.is_file(), "the producer-invoked pass wrote no receipt"
    doc = json.loads(receipt.read_text())
    assert doc[GA.DOC_KEY] == GA.ROLE_PRODUCER, doc.get(GA.DOC_KEY)
    assert D.is_auditor_output(project, receipt) is False, (
        "a receipt the RUN's own invocation wrote is still subtracted from the design")
    assert RECEIPT_REL in _kept(project)
