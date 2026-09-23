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
    with FCC._note_lock(note):
        lock = note.with_name(note.name + ".lock")
        assert lock.is_file(), "no lock file was taken"
        assert lock != note, "the lock is the note itself"
    other = FCC._authorship_note_path(tmp_path, "9", "reports/other.json")
    assert other.with_name(other.name + ".lock") != note.with_name(note.name + ".lock")


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
