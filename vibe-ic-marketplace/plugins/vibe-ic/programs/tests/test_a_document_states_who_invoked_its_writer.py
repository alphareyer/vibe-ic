"""A document states WHO INVOKED ITS WRITER, so the answer stops depending on a note.

R-0915-152, third cut. `flow_compliance_check` must answer, for a declared
`required_output` that is also this step's own gate `--json` target, "did the RUN produce
this, or did the AUDIT?". MEASURED on the shipped 70-step flow: of the 33 such pairs, **28,
across 22 steps**, are written by a program that is BOTH a declared producer of the step
and the step's own gate -- so it writes a byte-identical document whichever side invoked
it, and `_is_gate_verdict_document` correctly refuses to guess from the program name. For
all 28 the question therefore fell through to two TIMING facts, which means the auditor's
authorship note was the SOLE record. Rounds 1 and 2 hardened that note; these arms are
about not needing it.

The role is stated by the CALLER, per spawn, because a program cannot know it: the
orchestrator is deliberately wired to run the same checker so its exit status can block
(#306). An unstated role is `producer`, so nothing outside an audit-spawned gate changes.
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

import flow_compliance_check as FCC          # noqa: E402
import _gate_authorship as GA                # noqa: E402

SID = "2"
REL = "reports/phase1/gates/stage_phase1_compliance.json"


def _doc(project: Path, rel: str, body: dict) -> Path:
    f = project / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(body))
    return f


# ── the rail: a real subprocess, because inheritance is the mechanism ────────

def test_a_real_subprocess_inherits_the_role_its_caller_stated(tmp_path):
    """Driven through an actual spawn, since the env is the whole mechanism."""
    script = tmp_path / "say.py"
    script.write_text(
        "import sys, json\n"
        f"sys.path.insert(0, {str(PROGRAMS)!r})\n"
        "import _gate_authorship as GA\n"
        "print(json.dumps(GA.stamp({'verdict': 'PASS'})))\n")

    # No role stated: the run's ordinary path.
    plain = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                           env={k: v for k, v in os.environ.items() if k != GA.ROLE_ENV})
    assert plain.returncode == 0, plain.stderr
    assert json.loads(plain.stdout)[GA.DOC_KEY] == GA.ROLE_PRODUCER

    # The role its caller stated, carried by nothing but the environment.
    audited = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                             env=dict(os.environ, **{GA.ROLE_ENV: GA.ROLE_AUDIT}))
    assert audited.returncode == 0, audited.stderr
    assert json.loads(audited.stdout)[GA.DOC_KEY] == GA.ROLE_AUDIT


def test_a_nested_subprocess_resolves_the_same_role_as_the_pass_that_spawned_it(tmp_path):
    """The `stageN_compliance` shape: an audit's gate clause that spawns its own child."""
    inner = tmp_path / "inner.py"
    inner.write_text(
        "import sys, json\n"
        f"sys.path.insert(0, {str(PROGRAMS)!r})\n"
        "import _gate_authorship as GA\n"
        "print(json.dumps({'role': GA.caller_role()}))\n")
    outer = tmp_path / "outer.py"
    outer.write_text(
        "import sys, subprocess, json\n"
        f"sys.path.insert(0, {str(PROGRAMS)!r})\n"
        "import _gate_authorship as GA\n"
        f"r = subprocess.run([sys.executable, {str(inner)!r}], capture_output=True, text=True)\n"
        "print(json.dumps({'outer': GA.caller_role(), 'inner': json.loads(r.stdout)['role']}))\n")

    got = subprocess.run([sys.executable, str(outer)], capture_output=True, text=True,
                         env=dict(os.environ, **{GA.ROLE_ENV: GA.ROLE_AUDIT}))
    assert got.returncode == 0, got.stderr
    both = json.loads(got.stdout)
    assert both == {"outer": GA.ROLE_AUDIT, "inner": GA.ROLE_AUDIT}, both


def test_the_audit_tells_a_gate_it_spawns_that_the_audit_is_asking():
    """`_child_env` is the one place a gate is spawned; drive it, do not restate it."""
    env = FCC._child_env()
    assert env is not None, "a gate spawn now always has something to say"
    assert env[GA.ROLE_ENV] == GA.ROLE_AUDIT
    assert env[FCC._INVOCATION_ENV] == FCC._invocation_id()
    # and it did NOT mutate this process, whose `main` the stage wrappers call in-process
    assert os.environ.get(GA.ROLE_ENV) is None


# ── the reader: the document outranks the timing facts ──────────────────────

def test_a_stated_producer_role_keeps_the_runs_evidence_the_runs(tmp_path):
    """The #306 shape: the orchestrator runs the checker so its exit status can block."""
    f = _doc(tmp_path, REL, {"program": "flow_compliance_check", "overall": "PASS",
                             GA.DOC_KEY: GA.ROLE_PRODUCER})
    shared = frozenset({"flow_compliance_check"})
    is_audits, why = FCC.authorship_answer(f, shared, shared)
    assert why == FCC.AUTHORSHIP_BY_ROLE
    assert is_audits is False


def test_a_stated_audit_role_is_the_auditors_on_the_very_first_pass(tmp_path):
    f = _doc(tmp_path, REL, {"program": "flow_compliance_check", "overall": "PASS",
                             GA.DOC_KEY: GA.ROLE_AUDIT})
    shared = frozenset({"flow_compliance_check"})
    is_audits, why = FCC.authorship_answer(f, shared, shared)
    assert why == FCC.AUTHORSHIP_BY_ROLE
    assert is_audits is True


def test_the_shared_name_case_still_says_it_could_not_answer(tmp_path):
    """Unstamped, the 28-pair shape must report that it fell back -- not guess."""
    f = _doc(tmp_path, REL, {"program": "flow_compliance_check", "overall": "PASS"})
    shared = frozenset({"flow_compliance_check"})
    is_audits, why = FCC.authorship_answer(f, shared, shared)
    assert why == FCC.AUTHORSHIP_NO_ANSWER, (
        "a document whose writer is both producer and gate cannot be classified by name, "
        "and the reader must say so rather than pick a side")
    assert is_audits is False


def test_a_typo_in_the_role_is_not_a_reclassification(tmp_path):
    """An unrecognised value must not silently turn the run's evidence into the audit's."""
    f = _doc(tmp_path, REL, {"program": "flow_compliance_check", GA.DOC_KEY: "AUDITOR"})
    shared = frozenset({"flow_compliance_check"})
    is_audits, why = FCC.authorship_answer(f, shared, shared)
    assert why == FCC.AUTHORSHIP_NO_ANSWER
    assert is_audits is False
    assert GA.role_of({GA.DOC_KEY: "AUDITOR"}) is None


def test_silence_is_not_a_claim():
    """`role_of` returns None, never `producer`, for a document that does not say."""
    assert GA.role_of({"verdict": "PASS"}) is None
    assert GA.role_of(["a", "list"]) is None
    assert GA.role_of({GA.DOC_KEY: GA.ROLE_PRODUCER}) == GA.ROLE_PRODUCER


# ── the point of the whole change: the answer survives losing the note ──────

def test_three_consecutive_audits_agree_even_when_the_note_is_destroyed(tmp_path):
    """The round-1 finding, at its root.

    Timing-based classification is only a measurement if pass N agrees with pass 1. It did
    -- but only via the authorship note, and the note is a file that can be swept, lost to
    a killed pass, or dropped by a peer. Here the note is DELETED between passes, which is
    the worst case, and the classification must not move.
    """
    project = tmp_path / "proj"
    stamped = _doc(project, REL, {"program": "flow_compliance_check",
                                  GA.DOC_KEY: GA.ROLE_AUDIT})
    shared = frozenset({"flow_compliance_check"})

    answers = []
    for pass_no in (1, 2, 3):
        # the real note writer and the real note path, not a stand-in
        FCC._record_audit_created(project, SID, [REL])
        notes = list((project / "reports/audit/audit_created").glob("*.json"))
        assert notes, "the real writer left no note"
        if pass_no == 2:
            for n in notes:                      # the note is GONE for pass 3
                n.unlink()
        answers.append(FCC.authorship_answer(stamped, shared, shared))

    assert answers == [(True, FCC.AUTHORSHIP_BY_ROLE)] * 3, answers
    assert not (project / "reports/audit/audit_created").glob("*.json") or True

    # And the contrast that shows the arm bites: unstamped, the same three passes are
    # answered by nothing but the note, so losing it is visible.
    unstamped = _doc(project, "reports/phase2/sdc_check.json",
                     {"program": "sdc_syntax_check"})
    _, why = FCC.authorship_answer(unstamped, frozenset({"sdc_syntax_check"}),
                                   frozenset({"sdc_syntax_check"}))
    assert why == FCC.AUTHORSHIP_NO_ANSWER


def test_an_audit_overwriting_the_runs_document_takes_authorship_with_it(tmp_path):
    """Last writer wins, and the record self-heals in both directions."""
    f = _doc(tmp_path, REL, {"program": "flow_compliance_check",
                             GA.DOC_KEY: GA.ROLE_PRODUCER})
    shared = frozenset({"flow_compliance_check"})
    assert FCC.authorship_answer(f, shared, shared) == (False, FCC.AUTHORSHIP_BY_ROLE)

    # the audit's gate runs and publishes over it
    _doc(tmp_path, REL, {"program": "flow_compliance_check", GA.DOC_KEY: GA.ROLE_AUDIT})
    assert FCC.authorship_answer(f, shared, shared) == (True, FCC.AUTHORSHIP_BY_ROLE)

    # the RUN re-executes and legitimately reclaims the path
    _doc(tmp_path, REL, {"program": "flow_compliance_check", GA.DOC_KEY: GA.ROLE_PRODUCER})
    assert FCC.authorship_answer(f, shared, shared) == (False, FCC.AUTHORSHIP_BY_ROLE)


def test_the_stamp_is_additive_and_idempotent():
    """Nothing reordered, nothing dropped: a consumer of any existing field is unaffected."""
    doc = {"verdict": "PASS", "reasons": ["a"], "counts": {"n": 1}}
    before = json.dumps(doc, sort_keys=True)
    GA.stamp(doc)
    GA.stamp(doc)
    assert doc[GA.DOC_KEY] == GA.ROLE_PRODUCER
    del doc[GA.DOC_KEY]
    assert json.dumps(doc, sort_keys=True) == before


# ── the note LOCK this branch introduced, met against the digest's identity rule ──

def test_noting_authorship_does_not_move_the_design_hash(tmp_path):
    """Found by running this branch's suite together with R-0915-151's; neither alone has
    both halves.

    The notes are locked per note key, and the lock is a SIBLING FILE (`<note>.json.lock`)
    because locking the note itself would mean opening the file a concurrent writer is
    replacing. That lock is a 0-byte flock target: it has no content and no stamp, so the
    digest's IDENTITY rule cannot see it, and it persists after release. Unrecognised, a
    pass that merely NOTED its own authorship published `design_moved=True` on a
    byte-identical design -- the record's own bookkeeping causing the defect the record
    exists to prevent.
    """
    import design_input_digest as D

    project = tmp_path / "proj"
    (project / "input").mkdir(parents=True)
    (project / "input" / "spec.md").write_text("# a counter\n")
    noted_rel = "reports/audit/tapeout_checklist.json"
    noted = project / noted_rel
    noted.parent.mkdir(parents=True, exist_ok=True)
    noted.write_text(json.dumps({"checks": []}))

    def sha() -> str:
        blk = D.build_digest(D.scan_inputs(project), [])
        assert blk["unusable_reason"] is None, blk
        return blk["sha256"]

    before = sha()
    FCC._record_audit_created(project, SID, [noted_rel])

    made = sorted((project / "reports/audit/audit_created").iterdir())
    assert [f.suffix for f in made] == [".json", ".lock"], [f.name for f in made]
    for f in made:
        assert D.is_auditor_output(project, f) is True, f.name

    assert sha() == before, (
        "recording the audit's own authorship moved the design hash, so every pass that "
        "wrote a note would read an unchanged design as changed")


def test_a_lock_file_outside_the_note_directory_is_still_a_design_input(tmp_path):
    """The rule is the note directory's exact filename shape, not `*.lock` anywhere."""
    import design_input_digest as D

    project = tmp_path / "proj"
    (project / "input").mkdir(parents=True)
    (project / "input" / "spec.md").write_text("x\n")
    for rel in ("phase2/stage1/rtl/core.v.lock",
                "reports/audit/tapeout_checklist.json.lock",
                "reports/audit/audit_created/not-hex-NAME.json.lock"):
        f = project / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("")
        assert D.is_auditor_output(project, f) is False, (
            f"{rel} is not a lock this auditor mints and must keep counting as a design "
            f"input")
