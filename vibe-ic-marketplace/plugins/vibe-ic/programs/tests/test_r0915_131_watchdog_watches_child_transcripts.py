#!/usr/bin/env python3
"""R-0915-131 — a parent silent during a CHILD leg is not a stall.

THE MEASUREMENT this deck is written from (subservient x gf180mcuD, phase 3 on
main 48bf6576b, lane icsub5, killed 2026-09-23 01:22:58 after 7 h 20 m):

    WATCHDOG_STALLED: configured forward-progress signals did not advance for
    > 1800s — killed as hung, not slow. watched=output+log+cpu
    since_last_progress_s=1820.137 elapsed_s=25133.522

The job was not hung. PnR had handed post-route DRV repair to an SDR child
session and was waiting on it, so the watched transcript — the PARENT's
`pnr/openroad.log` — stopped at 00:47:00, and the parent's own argv marker and
CPU went quiet with it. Meanwhile the CHILD's transcript,
`sdr_child_def_leg_postroute_drv_repair.log`, was written at 01:17:34, thirty
minutes past the watchdog's last-progress mark and five minutes before the
kill, with its DRT-0199 counts still falling 10688 -> 9918 -> 9355 -> 9252 and
OpenROAD at ~500% CPU. Seven hours of place-and-route were discarded because
the one file being watched belonged to the process that had nothing to say.

The rules this deck pins, in both directions:
  * a CHILD transcript advancing while the parent is silent is PROGRESS;
  * ALL transcripts silent is still a STALL (the negative arm — the watchdog
    must not become unable to kill anything);
  * child transcripts are resolved AT EVERY LOOK, because they do not exist
    when the parent launches;
  * a DRT-0199 line counts as progress even when size and mtime cannot show it;
  * the kill DISCLOSES every transcript it read and when each last changed.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

PROG = Path(__file__).resolve().parents[1]
if str(PROG) not in sys.path:
    sys.path.insert(0, str(PROG))

import _watchdog as WD  # noqa: E402


def _touch(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as fh:
        fh.write(text)
    # stat mtime has coarse granularity on some filesystems; make the advance
    # unambiguous so the test pins the RULE, not the clock.
    t = time.time() + 10
    import os
    os.utime(p, (t, t))


# ── 1. the meter: a set, resolved at every look ───────────────────────────
def test_a_child_transcript_that_appears_later_is_picked_up(tmp_path):
    """The child does not exist at launch. A set resolved once would watch an
    empty set forever — which is exactly the failure."""
    parent = tmp_path / "openroad.log"
    parent.write_text("start\n")
    m = WD.TranscriptMeter(lambda: sorted(tmp_path.glob("*.log")))
    first = m.sample()
    child = tmp_path / "sdr_child_def_leg.log"
    _touch(child, "[INFO DRT-0199]   Number of violations = 9252.\n")
    second = m.sample()
    assert second > first, (first, second)
    assert any("sdr_child" in r["transcript"] for r in m.inventory())


def test_a_silent_parent_and_a_writing_child_is_PROGRESS(tmp_path):
    """THE POSITIVE ARM, and the exact shape of the measurement above."""
    parent = tmp_path / "openroad.log"
    parent.write_text("SDR_CANDIDATE_DEF_TRIED: ...\n")
    child = tmp_path / "sdr_child_def_leg.log"
    child.write_text("[INFO DRT-0199]   Number of violations = 10688.\n")
    m = WD.TranscriptMeter(lambda: sorted(tmp_path.glob("*.log")))
    before = m.sample()
    # parent says nothing at all; only the child advances
    _touch(child, "[INFO DRT-0199]   Number of violations = 9252.\n")
    after = m.sample()
    assert after > before
    assert parent.stat().st_size == len("SDR_CANDIDATE_DEF_TRIED: ...\n")


def test_all_transcripts_silent_is_still_NOT_progress(tmp_path):
    """THE NEGATIVE ARM. The watchdog must still be able to kill something."""
    (tmp_path / "openroad.log").write_text("x\n")
    (tmp_path / "sdr_child_def_leg.log").write_text("y\n")
    m = WD.TranscriptMeter(lambda: sorted(tmp_path.glob("*.log")))
    first = m.sample()
    second = m.sample()
    assert second == first


def test_a_DRT0199_line_counts_even_when_the_file_was_rewritten(tmp_path):
    """Size and mtime cannot show progress in a transcript truncated and
    rewritten in place; the marker count only ever increases, so it can."""
    log = tmp_path / "openroad.log"
    log.write_text("[INFO DRT-0199]   Number of violations = 500.\n" * 4)
    m = WD.TranscriptMeter(lambda: [log])
    first = m.sample()
    log.write_text("[INFO DRT-0199]   Number of violations = 400.\n")  # smaller
    second = m.sample()
    assert log.stat().st_size < 184      # the file on disk got SMALLER
    assert second[2] > first[2]          # marker count went UP
    assert second > first                # and the fused signal still advances


def test_an_unreadable_or_absent_path_never_kills_the_probe(tmp_path):
    m = WD.TranscriptMeter(lambda: [tmp_path / "nope.log"])
    assert m.sample() == (0, 0.0, 0.0)
    m2 = WD.TranscriptMeter(lambda: (_ for _ in ()).throw(OSError("boom")))
    assert m2.paths() == []


# ── 2. the disclosure ─────────────────────────────────────────────────────
def test_the_inventory_names_every_transcript_and_its_last_write(tmp_path):
    a = tmp_path / "openroad.log"
    a.write_text("a\n")
    b = tmp_path / "sdr_child_def_leg.log"
    b.write_text("b\n")
    m = WD.TranscriptMeter(lambda: sorted(tmp_path.glob("*.log")))
    m.sample()
    inv = m.inventory()
    assert {Path(r["transcript"]).name for r in inv} == {
        "openroad.log", "sdr_child_def_leg.log"}
    assert all(r["mtime_iso"] for r in inv)
    note = WD._transcripts_note({"transcripts": inv})
    assert "openroad.log@" in note and "sdr_child_def_leg.log@" in note


def test_the_note_says_so_when_nothing_was_wired():
    assert WD._transcripts_note({}) == "none wired"


# ── 3. the fused signal drives the supervisor, both directions ────────────
def _run(tmp_path, *, child_writes: bool):
    """A job that exits only when killed, with a parent log that never grows."""
    parent = tmp_path / "openroad.log"
    parent.write_text("quiet\n")
    child = tmp_path / "sdr_child_def_leg.log"
    child.write_text("[INFO DRT-0199]   Number of violations = 10688.\n")
    state = {"n": 0}

    def paths():
        if child_writes:
            state["n"] += 1
            _touch(child, f"[INFO DRT-0199]   violations = {10000 - state['n']}.\n")
        return sorted(tmp_path.glob("*.log"))

    res = WD.run_supervised(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        log_path=parent, progress_paths=paths,
        output_progress=False, stall_grace_s=0.6, poll_s=0.1,
        hard_ceiling_s=20, as_text=True)
    return res


def test_a_working_child_keeps_the_job_alive_past_the_grace(tmp_path):
    res = _run(tmp_path, child_writes=True)
    assert res.outcome != "stalled", res.err
    assert res.elapsed_s > 0.6


def test_with_nothing_writing_the_job_is_still_killed_as_hung(tmp_path):
    res = _run(tmp_path, child_writes=False)
    assert res.outcome == "stalled"
    assert "WATCHDOG_STALLED" in res.err
    # and the kill NAMES what it read
    assert "transcripts=[" in res.err
    assert "openroad.log@" in res.err
    assert "sdr_child_def_leg.log@" in res.err


def test_a_caller_that_wires_nothing_is_byte_identical_to_before(tmp_path):
    """No `progress_paths` and no `log_path` => no transcript signal at all,
    exactly as before this change."""
    res = WD.run_supervised(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        output_progress=False, stall_grace_s=0.4, poll_s=0.1,
        hard_ceiling_s=20, as_text=True)
    assert res.outcome == "stalled"
    assert "transcripts=[none wired]" in res.err
