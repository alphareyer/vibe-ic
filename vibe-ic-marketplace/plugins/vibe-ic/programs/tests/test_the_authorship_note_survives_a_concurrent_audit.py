"""Two audits of one step must not race each other's authorship note.

R-0915-152. The note's key is `sha1(f"{sid}|{rel}")`, so every evaluation of one step's
one declared output arrives at the SAME file — and two of them can be in flight at once:

  * step 7's gate clause runs a NESTED `flow_compliance_check`, which re-evaluates
    step 2 while the outer pass is evaluating it too;
  * and on the default threaded path (`_compliance_workers`) two threads of a single
    pass reach the same step's note.

Nothing serialised them, and the write was `write_text` — truncate, then write. So a
reader could observe zero bytes or half a JSON object. An unreadable note is treated as
ABSENT, and absent means "no earlier pass claimed this document", which CREDITS the
auditor's own output as the run's evidence. That is "MISSING twice, PASS forever" reached
by concurrency rather than by staleness, and the same race can drop a note the other
evaluation had just decided to rely on.

Two fixes, both needed:

  * `_write_note_atomically` writes a sibling temp file and `os.replace`s it, which is
    atomic within a directory on POSIX — so a reader sees the old note or the new one,
    never a partial one;
  * `_note_lock` holds an exclusive `flock` on a per-key lock FILE across the write, the
    drop and the read. Not on the note itself: `os.replace` swaps the inode out from
    under any holder, so a lock on the note would protect nothing.

Both are exercised here against the real functions, with real concurrency.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[2]
PROGRAMS = PLUGIN / "programs"
sys.path.insert(0, str(PROGRAMS))

import flow_compliance_check as FCC                          # noqa: E402

REL = "reports/phase1/gates/stage_phase1_compliance.json"
SID = "2"


def _artefact(project: Path, body: str = '{"program": "x"}') -> Path:
    f = project / REL
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(body + "\n")
    return f


# ── the note is never observed half-written ────────────────────────────────

def test_a_reader_never_sees_a_partial_note(tmp_path):
    """THE RACE, run for real: one thread rewrites the note in a loop while another
    reads it through the shipped reader. Every read must be whole — absent or valid,
    never torn.

    Under `write_text` this is the window where a reader gets zero bytes and concludes
    "no earlier pass claimed this", which credits the auditor's own document.
    """
    _artefact(tmp_path)
    note = FCC._authorship_note_path(tmp_path, SID, REL)
    note.parent.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    torn: list = []

    def writer():
        n = 0
        while not stop.is_set():
            n += 1
            # a payload whose LENGTH changes every time, so a torn read is visible
            FCC._record_audit_created(tmp_path, SID, [REL])
            if n % 7 == 0:
                _artefact(tmp_path, '{"program": "x", "pad": "' + "y" * (n % 50) + '"}')

    def reader():
        for _ in range(400):
            if not note.is_file():
                continue
            try:
                raw = note.read_text()
            except OSError:
                continue
            if not raw.strip():
                torn.append("empty")
                continue
            try:
                doc = json.loads(raw)
            except ValueError:
                torn.append(raw[:40])
                continue
            if doc.get("rel") != REL:
                torn.append(f"bad payload {doc!r}"[:60])

    w = threading.Thread(target=writer, daemon=True)
    r = threading.Thread(target=reader)
    w.start()
    r.start()
    r.join(timeout=60)
    stop.set()
    w.join(timeout=10)
    assert not torn, f"{len(torn)} partial/invalid read(s): {torn[:3]}"


def test_the_write_is_a_replace_not_a_truncate():
    """SOURCE PIN on the property the arm above depends on: a truncate-then-write is
    the window, and `os.replace` is what closes it."""
    import ast
    src = (PROGRAMS / "flow_compliance_check.py").read_text()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_write_note_atomically")
    body = ast.unparse(fn)
    assert "os.replace" in body, "the note is no longer replaced atomically"
    # and the recorder must go through it rather than writing the note directly
    rec = next(n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_record_audit_created")
    rec_src = ast.unparse(rec)
    assert "_write_note_atomically" in rec_src, rec_src[:200]
    assert "note.write_text" not in rec_src, (
        "the recorder writes the note directly again, so a reader can see a "
        "truncated file")


# ── the write, the drop and the read are serialised per note ───────────────

def test_a_concurrent_write_and_drop_leave_a_consistent_note(tmp_path):
    """The other half of the race: one evaluation RECORDS while another DROPS. The
    outcome may be either — the note present and whole, or absent — but never a note
    that exists and cannot be read, which is the state that credits the auditor."""
    _artefact(tmp_path)
    note = FCC._authorship_note_path(tmp_path, SID, REL)
    errors: list = []

    def recorder():
        for _ in range(150):
            try:
                FCC._record_audit_created(tmp_path, SID, [REL])
            except Exception as exc:                        # pragma: no cover
                errors.append(f"record: {exc!r}")

    def dropper():
        for _ in range(150):
            try:
                FCC._drop_audit_created_note(tmp_path, SID, [REL])
            except Exception as exc:                        # pragma: no cover
                errors.append(f"drop: {exc!r}")

    ts = [threading.Thread(target=recorder), threading.Thread(target=dropper)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=60)
    assert not errors, errors[:3]
    if note.is_file():
        doc = json.loads(note.read_text())      # must not raise
        assert doc["rel"] == REL and str(doc["step"]) == SID


def test_the_lock_is_per_note_and_not_on_the_note_itself(tmp_path):
    """A lock on the note would protect nothing — `os.replace` swaps the inode out
    from under any holder — and a single global lock would serialise unrelated steps.
    So the lock is a sibling file, one per note key."""
    note = FCC._authorship_note_path(tmp_path, SID, REL)
    note.parent.mkdir(parents=True, exist_ok=True)
    # R-0915-168 — the NAME comes from the declaration, not from this arm. The lock used to be
    # spelled `<note>.lock` here and in `_note_lock`; both now ask
    # `_path_layout.auditor_lock_name`, so the shape the digest recognises and the shape taken
    # here cannot drift. The property is unchanged: a sibling, one per note key, never the note.
    import _path_layout as _PL
    with FCC._note_lock(note):
        lock = _PL.auditor_lock_name(note)
        assert lock.is_file(), "no lock file was taken"
        assert lock != note, "the lock is the note itself"
        assert lock.parent == note.parent, "the lock is not a sibling"
    other = FCC._authorship_note_path(tmp_path, "9", "reports/other.json")
    assert _PL.auditor_lock_name(other) != _PL.auditor_lock_name(note), (
        "two different notes share one lock, so unrelated steps serialise")


def test_two_processes_do_not_interleave_one_note(tmp_path):
    """ACROSS PROCESSES, which is the shape that actually ships: step 7's clause runs a
    NESTED flow_compliance_check, so the two writers are two processes, and a
    thread-only lock would not have helped."""
    import subprocess
    _artefact(tmp_path)
    script = tmp_path / "hammer.py"
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(PROGRAMS)!r})\n"
        "from pathlib import Path\n"
        "import flow_compliance_check as F\n"
        f"p = Path({str(tmp_path)!r})\n"
        f"for _ in range(120):\n"
        f"    F._record_audit_created(p, {SID!r}, [{REL!r}])\n")
    procs = [subprocess.Popen([sys.executable, str(script)]) for _ in range(3)]
    for pr in procs:
        pr.wait(timeout=120)
    assert all(pr.returncode == 0 for pr in procs), [pr.returncode for pr in procs]
    note = FCC._authorship_note_path(tmp_path, SID, REL)
    assert note.is_file()
    doc = json.loads(note.read_text())          # whole, after 360 concurrent writes
    assert doc["rel"] == REL
    # no temp files left behind by any of them
    leftovers = sorted(x.name for x in note.parent.glob("*.tmp"))
    assert not leftovers, leftovers


def test_the_recorder_and_the_dropper_both_take_the_lock():
    """SOURCE PIN: both mutators must hold it. One of them outside the lock is the
    whole race back."""
    import ast
    tree = ast.parse((PROGRAMS / "flow_compliance_check.py").read_text())
    for name in ("_record_audit_created", "_drop_audit_created_note"):
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == name)
        assert "_note_lock(" in ast.unparse(fn), (
            f"{name} mutates the note without holding its lock")


# ── the race the commit NAMES: read -> gate -> record ───────────────────────
#
# The lock above protects single syscalls, and a review was right that those were
# already atomic. The race that matters spans three steps with the GATE in the middle:
#
#   T2 (outer) evaluates step 2: the declared path is ABSENT, so its first clause
#       writes it. T2 goes on running its remaining clauses.
#   T7's first clause is a NESTED `stage1_compliance` SUBPROCESS which evaluates step 2
#       too. It sees the file PRESENT with NO note, credits it as run evidence, and
#       drops a note that is not there.
#   T2 records — after a reader has already counted the auditor's own document as the
#       run's.
#
# A lock cannot be held across the gate: the gate is a subprocess that can take
# minutes, and holding one would serialise the whole audit. So the CLAIM is published
# BEFORE the gate, keyed to this invocation — which a nested subprocess resolves to the
# same string — and any evaluation inside that invocation refuses to credit it.


def _claim_state(project, sid=SID, rel=REL):
    note = FCC._authorship_note_path(project, sid, rel)
    return json.loads(note.read_text()) if note.is_file() else None


def test_a_file_this_invocation_is_creating_is_never_credited(tmp_path):
    """THE OUTCOME, not the lock: while this invocation's gate is mid-write, a
    concurrent evaluation of the same step must NOT credit the file."""
    FCC._claim_audit_will_create(tmp_path, SID, [REL])
    claim = _claim_state(tmp_path)
    assert claim and claim["state"] == "in_flight", claim
    assert claim["invocation"] == FCC._invocation_id()

    # the gate has now written the file — the exact window T7 used to win
    _artefact(tmp_path)
    prior = FCC._prior_audit_created(tmp_path, SID, [REL])
    assert REL in prior, (
        "a concurrent evaluation credited a file THIS invocation's own audit was in "
        "the middle of creating; that is the auditor signing off its own document")


def test_a_nested_subprocess_sees_its_parents_claim(tmp_path):
    """ACROSS PROCESSES, which is the shape that ships: T7's clause is a nested
    `flow_compliance_check`, so the reader is a different PROCESS. It must resolve the
    same invocation id and reach the same refusal."""
    import subprocess
    FCC._claim_audit_will_create(tmp_path, SID, [REL])
    _artefact(tmp_path)
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import sys, json\n"
        f"sys.path.insert(0, {str(PROGRAMS)!r})\n"
        "from pathlib import Path\n"
        "import flow_compliance_check as F\n"
        f"prior = F._prior_audit_created(Path({str(tmp_path)!r}), {SID!r}, [{REL!r}])\n"
        "print(json.dumps({'prior': sorted(prior), 'inv': F._invocation_id()}))\n")
    out = subprocess.run([sys.executable, str(probe)], capture_output=True,
                         text=True, timeout=300, env=dict(os.environ))
    assert out.returncode == 0, out.stderr[-300:]
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got["inv"] == FCC._invocation_id(), (
        "the nested process minted a DIFFERENT invocation id, so it could not see its "
        "parent's claim")
    assert REL in got["prior"], (
        "the nested evaluation credited a file its own invocation was creating")


def test_a_run_produced_file_is_still_credited(tmp_path):
    """THE OTHER DIRECTION, and the one a conservative claim could break: a file the
    RUN produced, with no claim and no note, must still be credited."""
    _artefact(tmp_path)
    assert FCC._prior_audit_created(tmp_path, SID, [REL]) == set()


def test_the_claim_is_published_before_the_gate_runs():
    """SOURCE PIN on the ORDER, because that is the whole fix: the claim must precede
    the gate evaluation, not follow it."""
    src = (PROGRAMS / "flow_compliance_check.py").read_text()
    claim_at = src.index("_claim_audit_will_create(project, sid, _absent_before_gate)")
    gate_at = src.index("passed, reasons = _evaluate_gate(project, gate", claim_at - 4000)
    assert claim_at < gate_at, (
        "the claim is published AFTER the gate, which leaves the window exactly where "
        "it was")


def test_two_threads_racing_one_step_never_credit_the_auditors_file(tmp_path):
    """REAL THREADS, asserting the outcome. One thread plays the outer pass (claim,
    then write, then record); the other plays the nested evaluation reading in between.
    No interleaving may yield 'credited'."""
    # A CHEAP BARRIER TIMEOUT ON PURPOSE. On a tree WITHOUT the claim, the outer
    # thread raises before reaching the barrier, so the nested thread waits it out --
    # and my first cut paired 40 attempts with a 30 s timeout, which turned the
    # base-arm measurement into a twenty-minute hang. It must fail FAST on the base,
    # not slowly: the assertion is what discriminates, never the wall clock.
    credited: list = []
    for attempt in range(20):
        proj = tmp_path / f"r{attempt}"
        (proj / "reports/phase1/gates").mkdir(parents=True, exist_ok=True)
        barrier = threading.Barrier(2, timeout=2)

        def outer():
            # A TREE WITHOUT THE CLAIM MUST STILL RUN THE RACE, or this arm measures
            # nothing on the base: my first cut let the missing function stop the
            # writer, so the nested thread never saw the file and the arm passed on
            # main. The claim is attempted; the gate's write happens either way.
            try:
                FCC._claim_audit_will_create(proj, SID, [REL])
            except AttributeError:
                pass
            finally:
                try:
                    barrier.wait()
                except Exception:
                    pass
            _artefact(proj)                     # the gate writes it
            FCC._record_audit_created(proj, SID, [REL])

        def nested():
            try:
                barrier.wait()
            except Exception:
                pass
            for _ in range(30):
                if (proj / REL).is_file():
                    if not FCC._prior_audit_created(proj, SID, [REL]):
                        credited.append(attempt)
                    return

        ts = [threading.Thread(target=outer), threading.Thread(target=nested)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=60)
    assert not credited, (
        f"{len(credited)} of 20 interleavings credited the auditor's own document "
        f"(attempts {credited[:5]})")
