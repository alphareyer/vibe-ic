"""A session the supervisor STOPPED has a NOT_MEASURED protocol, not an
incomplete one (vibe-ic#2219).

WHAT #2219 ACTUALLY LEFT BEHIND. Its headline — a nested-relay lease derived
from the wrong quantity — was repaired in v1.20.12 (`relay_window`), and the
named test is green at `--cpus=1` on live main: 3 reps, loads 27 / 24 / 18, all
`1 passed`. What survived that fix is the SECOND line of the failure it was
filed on:

    WATCHDOG_STALLED: configured forward-progress signals did not advance for
        > 2.5s — killed as hung, not slow.
    PROGRESS_PROTOCOL_INCOMPLETE: m.66.1.jsonl: terminal event missing
        (stage=running)

The first line is the supervisor saying "I stopped it". The second is the
supervisor grading the subject's protocol on an exam it just cancelled. The
stream is one event short BECAUSE THE SESSION WAS KILLED — the subject never
reached its terminal event and was never going to be allowed to. Reported that
way, anything counting protocol failures books a defect against a subject that
was behaving perfectly, which is exactly how the original report reads.

WHAT THIS CHANGES, AND WHAT IT DOES NOT. `protocol_complete` still goes False.
`incomplete` already contains `result.outcome != "natural"`, so the run's
VERDICT does not move by one bit — a killed session was incomplete before and is
incomplete now. Only the sentence changes, from a measured-sounding claim about
the subject to an honest statement that the thing was not measured. The observed
stream state is still printed after the reason, so nothing is withheld.

THE PAIRED ARM IS THE POINT. A session that exits NATURALLY with a short stream
really did fail its protocol, and must still say INCOMPLETE. Both directions are
asserted below; a change that simply renamed the token would fail the second.
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest_per_file_junit as D  # noqa: E402
from _session_floor import stall_window  # noqa: E402

_PROGRAMS = Path(__file__).resolve().parents[1]


def _corpus(tmp_path: Path, name: str, body: str) -> Path:
    root = tmp_path / "corpus"
    root.mkdir(exist_ok=True)
    (root / name).write_text(body, encoding="utf-8")
    return root


def _run(corpus: Path, junit: Path, window: float):
    return D.run_one(
        [sys.executable, "-m", "pytest", "-p", "no:terminal",
         "-p", "no:cacheprovider"],
        [p.name for p in corpus.glob("test_*.py")][0], junit, window,
        str(corpus))


# ---------------------------------------------------------------------------
# THE DEFECT: a session WE stopped is graded as if it had failed
# ---------------------------------------------------------------------------
def test_a_killed_session_is_not_measured_rather_than_incomplete(
        tmp_path, monkeypatch):
    """A subject that never renews is killed — and that is CORRECT. What must
    not follow is a claim that its protocol was incomplete."""
    window = stall_window(0.5)
    # Sleeps far past the window without emitting anything: the lease expires,
    # the supervisor kills it. This is the kill direction working as designed.
    corpus = _corpus(tmp_path, "test_silent.py",
                     "import time\n\n\ndef test_silent():\n"
                     f"    time.sleep({window * 6:.3f})\n")
    monkeypatch.setattr(D, "DEFAULT_POLL_S", 0.05)
    rc, out, incomplete = _run(corpus, tmp_path / "m.xml", window)

    assert "WATCHDOG_STALLED" in out, out[-3000:]
    assert incomplete is True, "a killed session must still be incomplete"
    assert "PROGRESS_PROTOCOL_NOT_MEASURED" in out, out[-3000:]
    assert "PROGRESS_PROTOCOL_INCOMPLETE" not in out, out[-3000:]
    # The observed state is re-attributed, not withheld.
    assert "stage=running" in out or "terminal event missing" in out, out[-3000:]


def test_the_verdict_itself_does_not_move(tmp_path, monkeypatch):
    """NOTHING IS RELAXED, pinned separately from the wording.

    If this change ever made a killed session look acceptable, that would be a
    relaxation wearing an honesty label. `incomplete` is asserted True above and
    the rc is asserted non-zero here."""
    window = stall_window(0.5)
    corpus = _corpus(tmp_path, "test_silent2.py",
                     "import time\n\n\ndef test_silent2():\n"
                     f"    time.sleep({window * 6:.3f})\n")
    monkeypatch.setattr(D, "DEFAULT_POLL_S", 0.05)
    rc, out, incomplete = _run(corpus, tmp_path / "m2.xml", window)
    assert incomplete is True
    assert rc != 0, (rc, out[-2000:])


# ---------------------------------------------------------------------------
# THE PAIRED ARM: a NATURAL exit with a short stream is still INCOMPLETE
# ---------------------------------------------------------------------------
def test_a_naturally_exited_session_still_reports_INCOMPLETE(
        tmp_path, monkeypatch):
    """The direction a rename would break.

    A subject that exits on its own with an unfinished protocol really did fail
    it, and must keep saying so. `os._exit` leaves the stream mid-run without
    the supervisor touching anything, which is the natural-exit shape.
    """
    window = stall_window(2.0)
    corpus = _corpus(
        tmp_path, "test_abrupt.py",
        "import os\n\n\ndef test_abrupt():\n"
        "    os._exit(0)\n")
    monkeypatch.setattr(D, "DEFAULT_POLL_S", 0.05)
    rc, out, incomplete = _run(corpus, tmp_path / "m3.xml", window)

    assert "WATCHDOG_STALLED" not in out, out[-3000:]
    assert incomplete is True, out[-3000:]
    assert "PROGRESS_PROTOCOL_INCOMPLETE" in out, out[-3000:]
    assert "PROGRESS_PROTOCOL_NOT_MEASURED" not in out, out[-3000:]
