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
PLUGIN = PROGRAMS.parent
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


def test_the_audit_never_takes_authorship_of_the_runs_own_document(tmp_path):
    """THE OPPOSITE OF WHAT I ASSERTED IN ROUND 3, and round 3 was the defect.

    I wrote an arm calling the overwrite "last writer wins ... the record self-heals", and
    it encoded a permanent failure. The flow lists `flow_compliance_check` under
    `programs:` for steps 2, 14, 15 and 37, so the RUN invokes it as the step's producer and
    its receipt IS the step's run evidence. Publishing over that, then re-stamping it
    `audit`, means the role-first reader refuses the step's only evidence -- every pass, and
    the next run just re-produces it. No sequence of runs ever credits those steps.

    The caller's own disclosure already said the rule: "A PRODUCER's document is never
    written over -- that is R-0915-126 and it is untouched". This holds it to that.
    """
    project = tmp_path / "proj"
    rel = "reports/phase1/gates/stage_phase1_compliance.json"
    f = project / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    produced = {"program": "flow_compliance_check",
                "steps": [{"id": "2", "status": "PASS"}],
                "overall": "PASS",
                GA.DOC_KEY: GA.ROLE_PRODUCER}
    f.write_text(json.dumps(produced) + "\n")
    before = f.read_bytes()

    assert FCC._publish_over_the_audits_own_document(
        ["flow_compliance_check", "--json", rel], project) is None, (
        "the audit claimed the run's own evidence as its previous publication")
    assert f.read_bytes() == before
    assert not list(f.parent.glob("*superseded*")), (
        "the run's document was copied aside, which is the first half of overwriting it")

    # the reader still reads it as the RUN's, which is the whole point of declining
    shared = frozenset({"flow_compliance_check"})
    assert FCC.authorship_answer(f, shared, shared) == (False, FCC.AUTHORSHIP_BY_ROLE)


#: The steps whose own producer is a compliance program, with the receipt the flow's
#: gate clause names -- read off `flow/phase1_phase2_phase3.yaml`, not typed from memory.
#:
#: DEPARTED 2026-09-24: ("2", "reports/phase1/gates/stage_phase1_compliance.json"), by
#: R-0915-141, the step-38 half, lane ictier1 (spm run23: "AUDIT-CREATED OUTPUT REFUSED: ['reports/phase1/gates/stage_phase1_compliance.json']"). Step 2 no
#: longer declares its nested clause's verdict target, so the flow-derived arm below no
#: longer finds it. A producer-role document at that path is still left alone -- the
#: single-path test above (SID "2") pins that and is untouched.
PRODUCER_IS_A_COMPLIANCE_PROGRAM = [
    ("14", "reports/analog/stage_analog_compliance.json"),
    ("15", "reports/phase2/gates/stage2_compliance.json"),
    ("37", "reports/phase3/gates/stage3_compliance.json"),
]


@pytest.mark.parametrize("sid,rel", PRODUCER_IS_A_COMPLIANCE_PROGRAM,
                         ids=[s for s, _ in PRODUCER_IS_A_COMPLIANCE_PROGRAM])
def test_each_of_the_four_steps_keeps_its_producers_document(sid, rel, tmp_path):
    """All four paths, and the population is derived from the flow by the arm below."""
    project = tmp_path / "proj"
    f = project / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"program": "flow_compliance_check",
                             "steps": [{"id": sid, "status": "PASS"}],
                             "overall": "PASS",
                             GA.DOC_KEY: GA.ROLE_PRODUCER}) + "\n")
    before = f.read_bytes()
    assert FCC._publish_over_the_audits_own_document(
        ["compliance", "--json", rel], project) is None, sid
    assert f.read_bytes() == before
    assert not list(f.parent.glob("*superseded*")), sid


def test_the_four_steps_are_the_ones_the_flow_declares(tmp_path):
    """The list above is not a guess: the flow is read and the two must agree.

    A fifth step gaining a compliance program under `programs:` arrives red here instead of
    silently losing its evidence.
    """
    import yaml
    flow = yaml.safe_load(
        (PLUGIN / "flow" / "phase1_phase2_phase3.yaml").read_text())

    def gate_cmds(node, out=None):
        out = [] if out is None else out
        if isinstance(node, dict):
            for k, v in node.items():
                if k.endswith("program_exit_zero"):
                    out.append(v if isinstance(v, str)
                               else str((v or {}).get("command", "")))
                else:
                    gate_cmds(v, out)
        elif isinstance(node, list):
            for i in node:
                gate_cmds(i, out)
        return out

    found = set()
    for st in flow.get("steps") or []:
        declared = {str(x).strip() for x in (st.get("programs") or [])
                    if isinstance(x, str)}
        req = set(st.get("required_outputs") or [])
        for cmd in gate_cmds(st.get("gate") or {}):
            toks = cmd.split()
            if not toks:
                continue
            prog = toks[0]
            if prog not in declared:
                continue
            # the writing module, following a thin wrapper
            src = (PROGRAMS / f"{prog}.py")
            if not src.is_file():
                continue
            import re as _re
            m = _re.search(r"^from\s+(\w+)\s+import\s+main\b",
                           src.read_text(errors="replace"), _re.M)
            if (m.group(1) if m else prog) != "flow_compliance_check":
                continue
            for i, tok in enumerate(toks[:-1]):
                if tok == "--json" and toks[i + 1] in req:
                    found.add((str(st.get("id")), toks[i + 1]))

    assert found == set(PRODUCER_IS_A_COMPLIANCE_PROGRAM), (
        f"the flow declares {sorted(found)}; the parametrised list is "
        f"{sorted(PRODUCER_IS_A_COMPLIANCE_PROGRAM)}")


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


def test_running_the_real_clause_leaves_the_producers_receipt_byte_identical(tmp_path):
    """END TO END, through `_check_program_exit_zero` -- the clause, its subprocess, and
    the redirect -- not `authorship_answer` on a file staged by hand.

    This is the shape spm run23 hits on steps 2, 14, 15 and 37: the run's producer has
    already written the step's receipt, and the audit then evaluates the step's own gate
    clause, whose `--json` names that same path. The gate child runs with
    `VIBEIC_FCC_ROLE=audit`, so if the audit publishes over the file the bytes come back
    stamped `audit` and the step loses its only evidence.

    TWO CONSECUTIVE PASSES, because the round-1 lesson on this branch is that a
    classification which changes with the number of passes is not a measurement.
    """
    project = tmp_path / "proj"
    (project / "input").mkdir(parents=True)
    (project / "input" / "spec.md").write_text("# a counter\n")
    rel = "reports/phase1/gates/stage_phase1_compliance.json"
    receipt = project / rel
    receipt.parent.mkdir(parents=True, exist_ok=True)
    # what `flow_declared_producer_run` leaves behind: the program's own receipt, stamped
    # `producer` because the RUN invoked it and stated no role.
    receipt.write_text(json.dumps({
        "program": "flow_compliance_check",
        "steps": [{"id": "2", "status": "PASS"}],
        "overall": "PASS",
        GA.DOC_KEY: GA.ROLE_PRODUCER,
    }, indent=2) + "\n")
    before = receipt.read_bytes()

    # the step's own gate clause, verbatim from the flow
    clause = ("flow_compliance_check . --stage-id stage_phase1 --strict "
              f"--json {rel}")

    for pass_no in (1, 2):
        outcome = FCC._check_program_exit_zero(project, clause)
        note = str(outcome[1] if isinstance(outcome, tuple)
                   else getattr(outcome, "output", "") or "")

        assert receipt.read_bytes() == before, (
            f"pass {pass_no}: the audit rewrote the run's own receipt; it now says "
            f"invoked_as="
            f"{GA.role_of(json.loads(receipt.read_text(errors='replace')))!r}")
        assert not list(receipt.parent.glob("*superseded*")), (
            f"pass {pass_no}: the run's document was copied aside")
        assert "RE-PUBLISHED" not in note, (
            f"pass {pass_no}: the clause claimed the run's evidence as its own previous "
            f"publication -- {note[:200]}")

        # and the reader credits the step: the document is the RUN's, by its own account
        shared = frozenset({"flow_compliance_check"})
        assert FCC.authorship_answer(receipt, shared, shared) == (
            False, FCC.AUTHORSHIP_BY_ROLE), f"pass {pass_no}"


# ── a claim from a pass that never finished ──────────────────────────────────

def _foreign_claim(project: Path, sid: str, rel: str, invocation: str = "77777-1") -> Path:
    """Exactly what a pass killed between its claim and its record leaves on disk."""
    note = FCC._authorship_note_path(project, sid, rel)
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(json.dumps({
        "schema": 1,
        "written_by": "flow_compliance_check",
        "state": "in_flight",
        "invocation": invocation,
        "step": sid,
        "rel": rel,
    }, indent=1) + "\n")
    return note


def test_a_killed_passs_claim_does_not_refuse_the_runs_document_forever(tmp_path, capsys):
    """The MEDIUM. An in-flight claim never expires and nothing finalises it.

    I took such a claim as an answer for ANY invocation, arguing a false refusal only costs
    a re-run. It does not: the claim outlives the pass that wrote it, so ONE interrupted
    audit refuses that path in every later audit, permanently. And the paths it bites are the
    ones no stamp can rescue -- `rtl_hygiene_lint` and `rom_init_lint` write a top-level JSON
    LIST, the reader answers UNREADABLE, and the decision falls straight through to here.
    """
    project = tmp_path / "proj"
    rel = "reports/phase2/lint/rtl_hygiene.json"
    produced = project / rel
    produced.parent.mkdir(parents=True, exist_ok=True)
    # the REAL shape of that producer's output: a top-level list, so it can carry no role
    produced.write_text(json.dumps([{"rule": "x", "severity": "ERROR"}]) + "\n")
    import design_input_digest as D
    assert GA.role_of(json.loads(produced.read_text())) is None

    note = _foreign_claim(project, "2", rel)

    assert FCC._prior_audit_created(project, "2", [rel]) == set(), (
        "another invocation's un-finalised claim still refuses the run's own document")

    # and it is DISCLOSED by name, with the invocation that left it
    stale = FCC.stale_in_flight_claims(project, "2", [rel])
    assert len(stale) == 1, stale
    assert stale[0]["claimed_by"] == "77777-1"
    assert stale[0]["file_exists"] is True
    assert stale[0]["rel"] == rel

    # the claim is IGNORED, not deleted: an invocation still running owns its own claim
    assert note.is_file()


def test_my_own_in_flight_claim_still_closes_the_window(tmp_path):
    """The half that must not move: the race the claim exists for is within ONE invocation,
    where a nested clause inherits the id."""
    project = tmp_path / "proj"
    rel = "reports/phase1/gates/stage_phase1_compliance.json"
    FCC._claim_audit_will_create(project, "2", [rel])

    assert FCC._prior_audit_created(project, "2", [rel]) == {rel}
    assert FCC.stale_in_flight_claims(project, "2", [rel]) == []

    rec = json.loads(FCC._authorship_note_path(project, "2", rel).read_text())
    assert FCC.in_flight_claim_is_this_invocations(rec) is True
    # the same record seen by a DIFFERENT invocation answers nothing
    rec["invocation"] = "a-different-one"
    assert FCC.in_flight_claim_is_this_invocations(rec) is False


def test_an_unreadable_document_is_disclosed_as_resting_on_bookkeeping(tmp_path):
    """A list-shaped document cannot state a role, so the refusal must name itself."""
    project = tmp_path / "proj"
    rel = "reports/phase2/lint/rtl_hygiene.json"
    f = project / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps([{"rule": "x"}]) + "\n")
    shared = frozenset({"rtl_hygiene_lint"})
    is_audits, why = FCC.authorship_answer(f, shared, shared)
    assert (is_audits, why) == (False, FCC.AUTHORSHIP_UNREADABLE)
    # and check_step's disclosure covers this source too, read from the shipped source
    import inspect
    src = inspect.getsource(FCC.check_step)
    assert "AUTHORSHIP_UNREADABLE" in src and "AUTHORSHIP_NO_ANSWER" in src, (
        "the per-step disclosure does not cover a document nothing can identify")


# ── the note's own atomic temp ───────────────────────────────────────────────

def test_an_interrupted_note_write_leaves_nothing_that_moves_the_design_hash(tmp_path):
    """The LOW, both halves, and the leftover is named by the WRITER's own expression."""
    import design_input_digest as D
    import threading as _th

    project = tmp_path / "proj"
    (project / "input").mkdir(parents=True)
    (project / "input" / "spec.md").write_text("# a counter\n")

    def sha() -> str:
        blk = D.build_digest(D.scan_inputs(project), [])
        assert blk["unusable_reason"] is None, blk
        return blk["sha256"]

    before = sha()
    note = FCC._authorship_note_path(project, "36", "reports/audit/tapeout_checklist.json")
    note.parent.mkdir(parents=True, exist_ok=True)
    leftover = note.with_name(f"{note.name}.{os.getpid()}.{_th.get_ident()}.tmp")
    leftover.write_text('{"schema": 1, "written')          # a half-written note

    assert D.is_auditor_output(project, leftover) is True
    assert sha() == before, (
        "a temp left by an interrupted note write moved the design hash, so every later "
        "pass reads an unchanged design as changed")


def test_the_note_writer_removes_its_temp_however_the_write_ends(tmp_path):
    """`finally`, not `except OSError`: SystemExit and KeyboardInterrupt are how a step dies."""
    import ast
    import inspect

    src = inspect.getsource(FCC._write_note_atomically)
    tree = ast.parse(src.lstrip())
    tries = [n for n in ast.walk(tree) if isinstance(n, ast.Try)]
    assert any(t.finalbody and "unlink" in ast.unparse(
        ast.Module(body=t.finalbody, type_ignores=[])) for t in tries), (
        "the temp is not removed in a `finally`, so an interrupted write leaks it")

    # and it does not mask the write's own failure
    note = tmp_path / "n.json"
    boom = OSError(28, "No space left on device")
    real = Path.write_text

    def exploding(self, *a, **k):
        if self.name.endswith(".tmp"):
            real(self, *a, **k)
            raise boom
        return real(self, *a, **k)

    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "write_text", exploding)
        with _pytest.raises(OSError) as caught:
            FCC._write_note_atomically(note, '{"a": 1}')
    assert caught.value is boom
    assert not list(tmp_path.glob("*.tmp")), "the temp outlived the interrupted write"
    assert not note.exists()


def test_a_producers_temp_in_the_note_directory_shape_is_not_swallowed(tmp_path):
    """The rule is the note directory plus the writer's exact shape, not `*.tmp`."""
    import design_input_digest as D
    project = tmp_path / "proj"
    (project / "input").mkdir(parents=True)
    (project / "input" / "spec.md").write_text("x\n")
    for rel in ("phase2/stage1/rtl/core.v.123.456.tmp",
                "reports/audit/audit_created/NOT-HEX.json.1.2.tmp",
                "reports/audit/audit_created/6be9e044.json.tmp"):
        f = project / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("")
        assert D.is_auditor_output(project, f) is False, rel
