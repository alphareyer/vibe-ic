"""vibe-ic#2113 O3 — `watched=` named the WIRED signals, not the READ ones.

`ProgressMeter.watched()` was computed from `fn is not None`, so it reported
the meter's WIRING. On the run that raised this issue it printed

    watched=output+cpu

while the CPU probe returned None on 7 of 12 looks. Every one of those looks
supervised the job on captured OUTPUT alone — during exactly the long silent
SAT solve the CPU probe was written to see — and the one line a reader would
consult to catch that said the probe was there. A blind probe hid behind a
healthy-looking record.

TWO THINGS THIS FILE HOLDS, and the second is why the obvious fix is not
enough. Reporting only the LAST look would still print `output+cpu` whenever
the twelfth look happened to succeed, and the measured case survives. So the
blind LOOK COUNT rides the line as well:

    output                 wired, and read at every one of N looks
    cpu:blind(7/12)        read at the last look; 7 of 12 gave nothing
    cpu:unreadable(8/12)   the LAST look gave nothing (8 of 12 blind)
    cpu:unread             wired, but no look has been taken yet
    NOTHING                nothing wired at all

The FUSION ARITHMETIC IS UNTOUCHED: what a meter counts as progress decides
when a job is reaped, and this change is about what the record SAYS, not about
when anything is stopped. The carry-forward / strict-increase / None-flap
properties are re-asserted here against the same sequences `test_watchdog.py`
uses, so a change to the score cannot ride in behind a change to the sentence.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

_PROGRAMS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROGRAMS))

import _watchdog as W               # noqa: E402


# ── 1. THE MEASURED CASE ───────────────────────────────────────────────────
def test_a_probe_blind_on_7_of_12_looks_is_named_as_such():
    """THE ISSUE'S OWN NUMBERS. The last look SUCCEEDS here on purpose: that is
    the arrangement in which a last-look-only report would still say
    `output+cpu` and the defect would survive its own fix."""
    blind_at = {1, 2, 4, 6, 8, 9, 11}          # 7 of the 12, last look reads
    look = {"n": 0}

    def cpu():
        look["n"] += 1
        return None if look["n"] in blind_at else float(look["n"])

    m = W.ProgressMeter(size_fn=lambda: 0, cpu_fn=cpu)
    for _ in range(12):
        m.sample()
    got = m.watched()
    assert got == "output+cpu:blind(7/12)", got
    assert got != "output+cpu", (
        "the record still claims a CPU signal that was unavailable on 7 of 12 "
        "looks — this is vibe-ic#2113 O3 restored")


def test_a_probe_blind_at_the_last_look_says_unreadable():
    seq = iter([1.0, 2.0, None])
    m = W.ProgressMeter(size_fn=lambda: 0, cpu_fn=lambda: next(seq))
    for _ in range(3):
        m.sample()
    assert m.watched() == "output+cpu:unreadable(1/3)", m.watched()


def test_a_healthy_meter_reads_exactly_as_it_always_did():
    """THE OTHER DIRECTION, and the one that keeps this change cheap: when
    every wired signal is readable the string is byte-identical to the one
    this method returned before, so no consumer of the happy path moves."""
    m = W.ProgressMeter(size_fn=lambda: 5, cpu_fn=lambda: 1.0)
    m.sample()
    assert m.watched() == "output+cpu"
    m2 = W.ProgressMeter(size_fn=lambda: 5, log_fn=lambda: (1, 1.0),
                         cpu_fn=lambda: 1.0)
    m2.sample()
    assert m2.watched() == "output+log+cpu"


def test_before_any_look_the_line_says_so_rather_than_guessing():
    """"Could not read it" is not "read it and it was empty", and "not yet
    asked" is neither. Three states, three words."""
    m = W.ProgressMeter(size_fn=lambda: 0, cpu_fn=lambda: 1.0)
    assert m.watched() == "output:unread+cpu:unread"


def test_nothing_wired_still_says_NOTHING():
    assert W.ProgressMeter().watched() == "NOTHING"


def test_a_wired_but_always_blind_probe_cannot_look_healthy():
    m = W.ProgressMeter(size_fn=lambda: 0, cpu_fn=lambda: None)
    for _ in range(5):
        m.sample()
    assert m.watched() == "output+cpu:unreadable(5/5)", m.watched()


def test_a_raising_probe_is_no_reading_not_a_silent_success():
    def boom():
        raise OSError("no /proc")

    m = W.ProgressMeter(size_fn=lambda: 0, cpu_fn=boom)
    m.sample()
    assert m.watched() == "output+cpu:unreadable(1/1)", m.watched()


def test_a_log_probe_that_returns_None_is_counted_blind():
    seq = iter([None, (1, 1.0)])
    m = W.ProgressMeter(log_fn=lambda: next(seq))
    m.sample()
    m.sample()
    assert m.watched() == "log:blind(1/2)", m.watched()


# ── 2. THE SAME FACTS AS NUMBERS ───────────────────────────────────────────
def test_readings_reports_the_counts_a_consumer_can_use():
    """A consumer that wants the fact rather than the sentence must not have
    to parse the sentence — that is how a format change becomes a silent
    behaviour change one layer up."""
    seq = iter([None, 2.0, None])
    m = W.ProgressMeter(size_fn=lambda: 0, cpu_fn=lambda: next(seq))
    for _ in range(3):
        m.sample()
    r = m.readings()
    assert set(r) == {"output", "cpu"}, r
    assert r["cpu"] == {"looks": 3, "blind": 2, "read_last": False}, r
    assert r["output"] == {"looks": 3, "blind": 0, "read_last": True}, r


# ── 3. THE FUSION ARITHMETIC IS UNTOUCHED ──────────────────────────────────
# Same sequences `test_watchdog.py` pins. A change to what a meter counts as
# progress is a change to when jobs are reaped; this is not that change, and
# the only way to say so is to re-measure it here.
def test_cpu_still_counts_only_strict_increase_and_carries_forward():
    seq = iter([0.0, 100.0, 100.0, 90.0, 150.0])
    m = W.ProgressMeter(size_fn=lambda: 0, cpu_fn=lambda: next(seq))
    assert [m.sample() for _ in range(5)] == [0.0, 100.0, 100.0, 100.0, 150.0]


def test_the_None_flap_is_still_never_progress():
    seq = iter([None, 500.0, None, 500.0, None, 500.0])
    m = W.ProgressMeter(size_fn=lambda: 0, cpu_fn=lambda: next(seq, 500.0))
    s = [m.sample() for _ in range(6)]
    assert s == [0.0, 500.0, 500.0, 500.0, 500.0, 500.0], s
    # ...and the record now SAYS the probe was blind on half of them, which is
    # the whole point: the carry-forward that keeps a hung job from squatting
    # is invisible in the score and used to be invisible in the record too.
    assert m.watched() == "output+cpu:blind(3/6)", m.watched()


def test_output_growth_and_log_events_still_score_as_before():
    box = {"n": 0}
    m = W.ProgressMeter(size_fn=lambda: box["n"])
    a = m.sample()
    box["n"] = 10
    assert m.sample() > a
    box2 = {"sig": (0, 0.0)}
    m2 = W.ProgressMeter(log_fn=lambda: box2["sig"])
    x = m2.sample()
    box2["sig"] = (10, 1.0)
    y = m2.sample()
    box2["sig"] = (10, 2.0)
    assert m2.sample() > y > x


# ── 4. END TO END: the stall message a reader actually gets ────────────────
def test_a_real_stall_message_names_the_blind_probe(tmp_path):
    """EXECUTED on a real process. The subject stops itself dead, so every
    signal is flat and the grace trips; the CPU probe is wired and always
    blind. The stall line — the one sentence a reader gets about why a job was
    killed — must say the probe gave nothing, not that it was watching."""
    res = W.run_supervised(
        ["bash", "-c", "echo started; kill -STOP $$; sleep 600"],
        cpu_probe=lambda _proc: None,
        stall_grace_s=1.0, poll_s=0.25)
    assert res.rc == W.RC_STALLED, res.err
    assert "WATCHDOG_STALLED" in res.err
    assert "watched=output+cpu:unreadable(" in res.err, (
        f"the stall line still claims a CPU signal that never gave a reading: "
        f"{res.err[-300:]}")
    assert res.supervision["signal_reads"]["cpu"]["read_last"] is False
    assert res.supervision["signal_reads"]["cpu"]["blind"] >= 1


def test_a_healthy_supervised_run_records_both_signals_as_read(tmp_path):
    """THE CONTROL. A probe that WORKS must not be reported as blind, or the
    new grammar is just noise on every line."""
    res = W.run_supervised(
        ["bash", "-c", "for i in 1 2 3; do echo tick; sleep 0.2; done"],
        cpu_probe=lambda _proc: 1.0, stall_grace_s=30.0, poll_s=0.1)
    assert res.rc == 0, res.err
    assert res.supervision["watched"] == "output+cpu", res.supervision
    assert res.supervision["signal_reads"]["cpu"]["blind"] == 0


# ── 5. THE MUTATION ARM: revert the fix, require the finding ────────────────
# The pristine-file arm (recorded in the lane's LAND.md: 10 of these 14 red
# against base 94617408759e) proves the file as a whole reproduces. This one
# rides IN the suite, so a future edit that quietly restores the wiring-only
# line is caught by a test rather than by someone remembering to re-measure.
_OLD_WATCHED_BODY = '''        names = [n for n, fn in (("output", self._size_fn),
                                 ("log", self._log_fn),
                                 ("cpu", self._cpu_fn)) if fn is not None]
        return "+".join(names) if names else "NOTHING"
'''


def _module_with_the_fix_reverted():
    """This module's own source with `watched()` put back to the WIRING-ONLY
    implementation, executed as a real module.

    Source surgery rather than a hand-copied class, so the control cannot
    drift from what the file actually says; `assert mutated != src` fails
    loudly the moment the anchors stop matching. The module is registered in
    `sys.modules` before exec because `@dataclass` resolves `cls.__module__`
    through it — without that, `SupervisedResult` raises at import."""
    import types
    src = (_PROGRAMS / "_watchdog.py").read_text()
    head = ('        parts = []\n'
            '        for name, fn in (("output", self._size_fn),')
    tail = '        return "+".join(parts) if parts else "NOTHING"\n'
    i = src.index(head)
    j = src.index(tail) + len(tail)
    mutated = src[:i] + _OLD_WATCHED_BODY + src[j:]
    assert mutated != src, "the mutation did not apply — update this control"
    name = "_watchdog_pre_v2113"
    mod = types.ModuleType(name)
    mod.__file__ = str(_PROGRAMS / "_watchdog.py")
    sys.modules[name] = mod
    exec(compile(mutated, "_watchdog.py<pre-v2113>", "exec"), mod.__dict__)
    return mod


def _blind_7_of_12(meter_cls):
    blind_at = {1, 2, 4, 6, 8, 9, 11}
    look = {"n": 0}

    def cpu():
        look["n"] += 1
        return None if look["n"] in blind_at else float(look["n"])

    m = meter_cls(size_fn=lambda: 0, cpu_fn=cpu)
    for _ in range(12):
        m.sample()
    return m


def test_reverting_watched_to_the_wiring_reproduces_the_defect():
    """THE CONTROL. With the fix line put back, the SAME 7-of-12 probe reports
    `output+cpu` — the exact string vibe-ic#2113 O3 was filed about. A guard
    that cannot fail is not a guard."""
    pre = _module_with_the_fix_reverted()
    assert _blind_7_of_12(pre.ProgressMeter).watched() == "output+cpu", (
        "the reverted implementation did not reproduce the reported string, "
        "so this control is not measuring the defect it names")


def test_the_two_implementations_disagree_on_exactly_this_case():
    """And they must AGREE where nothing is blind — otherwise the fix would be
    a rewrite of the healthy path wearing a bug fix's name."""
    pre = _module_with_the_fix_reverted()
    assert _blind_7_of_12(pre.ProgressMeter).watched() \
        != _blind_7_of_12(W.ProgressMeter).watched()
    healthy = dict(size_fn=lambda: 5, cpu_fn=lambda: 1.0)
    a, b = pre.ProgressMeter(**healthy), W.ProgressMeter(**healthy)
    a.sample(), b.sample()
    assert a.watched() == b.watched() == "output+cpu"


def test_the_mutation_is_SCOPED_to_the_sentence():
    """A mutation arm must revert the FIX LINE, not the commit — otherwise it
    proves the test notices a large change rather than the specific one.

    `readings()` and the per-look recording in `sample()` are deliberately left
    IN the mutated module. So the only thing that differs is the sentence, and
    the disagreement above is attributable to it and to nothing else."""
    pre = _module_with_the_fix_reverted()
    assert hasattr(pre.ProgressMeter, "readings"), (
        "the mutation reverted more than the fix line")
    # The mutated meter still RECORDS what it read — it just refuses to say so.
    assert _blind_7_of_12(pre.ProgressMeter).readings()["cpu"] == {
        "looks": 12, "blind": 7, "read_last": True}
