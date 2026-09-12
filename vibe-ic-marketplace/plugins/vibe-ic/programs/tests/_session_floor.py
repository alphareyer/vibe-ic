"""programs/tests/_session_floor.py — the MEASURED cost of an empty pytest session.

WHY THIS EXISTS
===============
`pytest_per_file_junit` starts a forward-progress lease the moment it spawns a
subject (`_watchdog.supervise`: ``last_progress = start``), and the subject's
first validated lifecycle event — ``session_start`` from
``_pytest_progress_plugin`` — can only arrive once the interpreter and pytest
have finished starting.  Nothing the subject does can renew a window that
expires before that point.  A stall-window test whose window is shorter than
this environment's start-up is therefore not measuring renewal at all: it
measures the interpreter, and its colour is decided by which box runs it.

MEASURED 2026-09-02 at 14de9b8a36 (v1.15.97).  In the pinned image
(``vibeic-eda@sha256:66c33ff2…``, ``PYTHONDONTWRITEBYTECODE=1``) a bare
``python3 -m pytest`` reaches its own argument parser 0.43–0.45 s after spawn;
on 8HD-9's host python, 0.48 s.  The stall-window tests in
``test_pytest_per_file_junit.py`` and ``test_flow_matrix_coverage.py`` declared
windows of 0.25 / 0.30 / 0.35 / 0.45 / 0.50 s.  The three shortest were red on
BOTH lanes, 3 of 3 runs each, every one with

    WATCHDOG_STALLED: … did not advance for > 0.35s
    PROGRESS_PROTOCOL_INCOMPLETE: no pytest progress stream was produced

and an elapsed time equal to the window: killed before pytest existed.  The
0.45 / 0.50 windows flipped with host load (red in 3 of 6 container runs).
Neither the driver nor the plugin is involved: both behaved exactly as
declared, and neither is changed for this.

WHAT THIS IS NOT
================
Not a bound, and not a relaxation.  The window the driver receives is still the
number a test hands it, and every ratio the test relies on — how much of the
window one renewal consumes, how many windows the run must outlive — is
asserted by the test itself against whatever window it ends up with.
``stall_window(nominal)`` returns ``nominal`` wherever the interpreter starts
inside it, and lifts it to a fixed multiple of the MEASURED floor where it
cannot.  The kill direction is untouched: a subject that never renews is still
stopped one window after its last event, and the tests that assert THAT
direction run at the same derived window.

The floor is a reading of THIS box under THIS load, taken moments before the
test that uses it; it is cached per process so one file shares one reading.
The multiple covers the interval between the reading and the run, not host
speed — host speed is what the reading is.
"""
from __future__ import annotations

import concurrent.futures
import functools
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

_PROGRAMS = Path(__file__).resolve().parents[1]

#: How many measured floors a too-short window is lifted to.
FLOOR_MULTIPLE = 2.0

#: Environment a subject inherits from an ENCLOSING per-file driver.  The
#: calibration session must not see these: with them set, the progress plugin
#: would append this session's stream into the enclosing driver's private
#: progress directory, and that driver would then be validating a stream it
#: never spawned.  ``PYTEST_ADDOPTS`` is dropped because the tests that set it
#: (``-s``) are configuring their SUBJECT, not the floor.
_INHERITED = (
    "VIBEIC_PYTEST_PROGRESS_DIR",
    "VIBEIC_PYTEST_PROGRESS_NONCE",
    "VIBEIC_PYTEST_RUNTIME_IDENTITY",
    "PYTEST_ADDOPTS",
)


def _one_trivial_session_s(cwd: Path) -> float:
    """Spawn-to-exit seconds of a green one-test session, the driver's shape."""
    env = {k: v for k, v in os.environ.items() if k not in _INHERITED}
    env["PYTHONPATH"] = (
        str(_PROGRAMS) if not env.get("PYTHONPATH")
        else str(_PROGRAMS) + os.pathsep + env["PYTHONPATH"])
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:terminal",
         "-p", "no:cacheprovider", "-p", "_pytest_progress_plugin",
         "test_floor.py"],
        cwd=str(cwd), env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True)
    elapsed = time.monotonic() - started
    assert proc.returncode == 0, (
        "the calibration session must be a green one-test session, or the "
        f"floor is a reading of a failure, not of start-up; rc={proc.returncode}"
        f"\n{proc.stdout[-2000:]}")
    return elapsed


@functools.lru_cache(maxsize=None)
def _session_floor_at(width: int) -> float:
    """The cached reading. Split from the public name ON PURPOSE: caching the
    public signature instead would give ``trivial_session_s()`` and
    ``trivial_session_s(1)`` two DIFFERENT cache entries, i.e. two separate
    readings of the same thing, and the contract test that compares the two
    would then be comparing two measurements rather than one derivation.
    """
    with tempfile.TemporaryDirectory(prefix="vibeic-session-floor-") as d:
        cwd = Path(d)
        (cwd / "test_floor.py").write_text(
            "def test_floor():\n    assert True\n", encoding="utf-8")
        if width == 1:
            return max(_one_trivial_session_s(cwd) for _ in range(2))
        worst = 0.0
        with concurrent.futures.ThreadPoolExecutor(max_workers=width) as pool:
            for _round in range(2):
                worst = max(worst, max(pool.map(
                    _one_trivial_session_s, [cwd] * width)))
        return worst


def trivial_session_s(width: int = 1) -> float:
    """Seconds an empty one-test pytest session costs HERE, spawn to exit.

    The larger of two consecutive measurements, so one lucky start does not set
    the floor for the whole file.  An upper bound on spawn-to-``session_start``:
    the calibration session also collects, runs and tears down one item.

    ``width`` is how many such sessions start AT ONCE.  A serial reading is not
    an upper bound for a subject that starts eight interpreters into one cgroup
    at once, and this module's whole claim is that the floor is a reading of
    THIS box under THIS load -- so the reading is taken at the width the caller
    will actually run at, rather than modelled from a serial one.  ``width=1``
    is byte-identical to the original reading.
    """
    if not isinstance(width, int) or width < 1:
        raise ValueError(f"width must be a positive int, got {width!r}")
    return _session_floor_at(width)


trivial_session_s.cache_clear = _session_floor_at.cache_clear


def stall_window(nominal: float, *, starts: int = 1, width: int = 1) -> float:
    """``nominal`` where the interpreter starts inside it; else lifted.

    The lift is to ``FLOOR_MULTIPLE`` measured floors PER INTERPRETER START,
    never to a constant, so a fast host keeps the nominal window and a slow one
    gets exactly what its own start-up requires.  Callers scale their renewal
    cadence from the value returned, which keeps every ratio they assert intact.

    ``starts`` is how many interpreter start-ups happen IN SERIES between this
    lease's spawn and the first validated event that can renew it, and it may
    now only be ONE.

    WHY ``starts > 1`` IS REFUSED (vibe-ic#2219, second half).  ``starts=2``
    meant exactly one thing in this tree: the subject is a test that drives the
    per-file driver, so the events that renew the lease arrive through
    ``--progress-relay``.  #2219 MEASURED that lane and it is not two
    interpreter start-ups -- it is 2.6x one of them, and four of its five terms
    (driver spawn, supervised pytest, first validated lifecycle event, probe
    poll, reader poll) are not interpreter start-up at all.  A model that reads
    ``trivial_session_s`` therefore hands a relayed lease a window whose size is
    decided by how fast THIS box starts an interpreter, which is the defect
    #2219 exists for: RED at ``--cpus=1`` and GREEN at ``--cpus=4`` on the same
    commit.

    ``relay_window`` measures that lane instead of modelling it.  Refusing
    ``starts > 1`` here is what stops the wrong model being reachable at all:
    #2219's first half moved ONE call site and left the primitive in place, and
    the two call sites it did not move kept the defect.  MEASURED 2026-09-10 on
    8HD-6 in the pinned image at ``--cpus=1``, with ``trivial_session_s`` held
    at the 0.60 s reading #2219 itself recorded,
    ``test_nested_collect_progress_is_relayed_to_the_outer_session`` died with
    the issue's own signature -- ``WATCHDOG_STALLED ...
    since_last_progress_s=2.419``, ``terminal event missing (stage=running)``,
    ``rc 199`` -- while the repaired sibling beside it passed.

    Nothing is relaxed by this refusal: it removes a derivation that was
    measured to be too SMALL, and the replacement is never smaller.

    ``width`` is how many interpreter start-ups happen AT ONCE under this one
    lease.  A test that drives the per-file driver with ``--fallback-jobs 8``
    spawns eight supervised pytest sessions into one cgroup simultaneously, and
    each of them holds its OWN lease of this size from its OWN spawn.  Deriving
    that lease from a floor measured one session at a time is the same defect
    this module was written for, one axis over: MEASURED in the pinned image
    under `--cpus=4`, the width-1 floor was 0.51 s (window 1.03 s) while eight
    concurrent starts took 1.9 s, so four of `test_pytest_per_file_junit.py`'s
    tests reported `STALLED after 1.0268 s with no validated pytest lifecycle
    progress` about subjects that had not started.  Those four were red on
    610cae2cc and on 6883a9c93 alike -- the window read the box, not the tree.

    ``width`` REMAINS, and it is a different axis from the one refused above.
    It still models a real SERIES-of-one-start-per-worker silence, measured in
    its own shape by ``trivial_session_s(width)``.  What ``starts > 1`` modelled
    was never interpreter start-up at all, which is why one is kept and the
    other refused rather than both being swept away together.  Where the silence
    before the first renewal is a relayed lane, ``relay_window`` below is the
    instrument: that term is measured, not modelled.
    """
    if not isinstance(starts, int) or starts < 1:
        raise ValueError(f"starts must be a positive int, got {starts!r}")
    if starts > 1:
        raise ValueError(
            f"stall_window(starts={starts}) models a relayed lease as "
            f"{starts} interpreter start-ups, and vibe-ic#2219 measured that "
            "model wrong (the lane is 2.6x an interpreter start and four of "
            "its five terms are not start-up). Use relay_window(nominal) for "
            "any lease whose renewals arrive through --progress-relay.")
    if not isinstance(width, int) or width < 1:
        raise ValueError(f"width must be a positive int, got {width!r}")
    return max(float(nominal),
               FLOOR_MULTIPLE * starts * trivial_session_s(width))


# ── A NESTED DRIVER'S FIRST RELAYED EVENT (vibe-ic#2219) ────────────────────
#
# `stall_window(..., starts=N)` models the silence before a lease's first
# renewal as N pytest start-ups IN SERIES. That model is right for a subject
# that IS a pytest, and wrong for a subject whose renewals arrive through the
# per-file driver's semantic RELAY, because the relay is not N start-ups: it is
# a driver interpreter, then a supervised pytest, then that pytest's first
# validated lifecycle event, then one probe poll that turns it into a relay
# score, then one reader poll that turns the score into an outer
# `domain_progress`. Four of those five terms are not interpreter start-up and
# none of them is measured by `trivial_session_s`.
#
# MEASURED 2026-09-09 on 8HD-9, live main 6883a9c93 (v1.20.7), in the pinned
# image at `--cpus=1 --memory=8g --pids-limit=1024`, host load 12. The outer
# progress stream of
# `test_nested_validated_progress_is_relayed_to_the_outer_session` was kept
# (the driver deletes it) and every inter-event gap of the outer lease read
# off it:
#
#     1.1059  SPAWN -> session_start                (one interpreter start)
#     0.4705  collect_scan -> item_collected
#     0.0002  item_collected -> collection_finish
#     2.9234  collection_finish -> matrix-outcome-relay 1     <-- THE GAP
#     0.0000  ... 82 relay scores, all sub-millisecond ...
#     1.3084  matrix-outcome-modules 2 -> relay 83  (the second wave's lane)
#
# The lease was `stall_window(2.5, starts=2)`. The silence that killed it is
# 2.9234 s of ONE term — the nested lane's first relayed event — and it is
# 2.6x the interpreter start measured in the same run. `starts=2` at
# `FLOOR_MULTIPLE` needs `trivial_session_s` >= 0.731 s to cover it, so the
# test's colour was decided by whether this box's pytest start-up happened to
# land above or below that line. That is the machine, which is the whole
# defect. The 1.3084 s gap is the SAME quantity again at the wave boundary,
# with warm caches.
#
# So the term is MEASURED rather than modelled, in the same shape as
# `trivial_session_s`: really spawn the driver the subject spawns, on a
# one-test corpus, and time spawn to the first byte the relay carries.

#: The relay reader in `test_flow_matrix_coverage._run_one_module_outcome`
#: polls at this cadence, so the calibration polls at it too: the quantity is
#: "when could the outer lease have SEEN it", not "when was it written".
_RELAY_POLL_S = 0.1

_DRIVER = _PROGRAMS / "pytest_per_file_junit.py"

#: The subject's shape, reduced to the part that happens BEFORE it can relay.
#: `stall_window` is here because the subject calls it -- that call is two
#: pytest sessions (`max(... for _ in range(2))`) and it is the largest single
#: term in the silence measured above. Nothing after the driver spawn matters
#: to the reading, so the corpus the nested lane runs is one trivial test.
_RELAY_CALIBRATION_TEST = """\
import os
import subprocess
import sys
from pathlib import Path

import _session_floor as _floor


def test_relay_floor():
    # The subject's own preamble, inside the lease exactly as the subject
    # holds it: a lease-holder that must compute a floor before it can relay.
    _floor.stall_window(0.45)
    scratch = Path(os.environ["VIBEIC_RELAY_FLOOR_SCRATCH"])
    proc = subprocess.run(
        [sys.executable, os.environ["VIBEIC_RELAY_FLOOR_DRIVER"],
         "--selection", str(scratch / "selection.txt"),
         "--junit", str(scratch / "relay-floor-junit.xml"),
         "--aggregate-only",
         "--aggregate-stall-after", os.environ["VIBEIC_RELAY_FLOOR_STALL"],
         "--progress-relay", str(scratch / "semantic-progress.relay"),
         "--cwd", str(scratch), "--",
         sys.executable, "-m", "pytest", "-q", "--tb=no",
         "-p", "no:cacheprovider",
         "--basetemp", str(scratch / "pytest_tmp")],
        cwd=str(scratch), stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True)
    assert proc.returncode in (0, 1), proc.stdout[-2000:]
"""


def _one_trivial_relay_s(cwd: Path) -> float:
    """Spawn-to-FIRST-RELAYED-EVENT seconds, in the subject's own shape.

    Not a model of the span and not one term of it: really spawn a pytest
    that computes a floor and then drives a nested
    ``pytest_per_file_junit.py`` lane through ``--progress-relay``, and time
    from that spawn to the first byte the relay carries -- which is the first
    moment an enclosing lease could have been renewed.

    This is EXPENSIVE (three interpreter generations plus the subject's own
    two calibration sessions) and that is the price of the number being a
    reading rather than a guess. It is taken once per process, cached, and
    only by the tests that hold such a lease.
    """
    env = {k: v for k, v in os.environ.items() if k not in _INHERITED}
    env["PYTHONPATH"] = os.pathsep.join(
        [str(_PROGRAMS), str(_PROGRAMS / "tests")]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # A SHORT name on purpose: this root carries a `--basetemp` beneath it and
    # then pytest's own per-test directories, and a scratch root that is merely
    # LONG is its own class of manufactured failure in this tree.
    with tempfile.TemporaryDirectory(prefix="r-", dir=str(cwd)) as scratch_name:
        scratch = Path(scratch_name)
        (scratch / "test_relay_floor_inner.py").write_text(
            "def test_relay_floor_inner():\n    assert True\n",
            encoding="utf-8")
        (scratch / "selection.txt").write_text(
            str(scratch / "test_relay_floor_inner.py") + "\n",
            encoding="utf-8")
        outer = scratch / "test_relay_floor_outer.py"
        outer.write_text(_RELAY_CALIBRATION_TEST, encoding="utf-8")
        relay = scratch / "semantic-progress.relay"
        relay.touch(mode=0o600)
        env["VIBEIC_RELAY_FLOOR_SCRATCH"] = str(scratch)
        env["VIBEIC_RELAY_FLOOR_DRIVER"] = str(_DRIVER)
        # The lease the CALIBRATION's own nested driver holds. Nothing is
        # derived from it; it exists so a hung calibration is reported as a
        # stall instead of hanging the file, and it is deliberately far above
        # anything this can take.
        env["VIBEIC_RELAY_FLOOR_STALL"] = str(60.0 * FLOOR_MULTIPLE)
        log = scratch / "calibration.log"
        started = time.monotonic()
        with log.open("w+", encoding="utf-8") as log_file:
            proc = subprocess.Popen(
                [sys.executable, "-m", "pytest", "-q", "--tb=short",
                 "-p", "no:cacheprovider", str(outer)],
                cwd=str(scratch), stdout=log_file,
                stderr=subprocess.STDOUT, text=True, env=env)
            first: Optional[float] = None
            while proc.poll() is None:
                if relay.stat().st_size > 0:
                    first = time.monotonic() - started
                    break
                time.sleep(_RELAY_POLL_S)
            # A lane short enough to relay and finish between two polls still
            # relayed; without this read its reading would be lost, not zero.
            if first is None and relay.stat().st_size > 0:
                first = time.monotonic() - started
            proc.wait()
            log_file.flush()
            log_file.seek(0)
            diagnostic = log_file.read()
    assert proc.returncode == 0, (
        "the relay-floor calibration must be a green nested lane, or the "
        "floor is a reading of a failure, not of a relay; "
        f"rc={proc.returncode}\n{diagnostic[-3000:]}")
    assert first is not None, (
        "the relay-floor calibration produced NO relayed score, so there is "
        "nothing to read a floor from; a zero here would be an unmeasured "
        f"quantity reported as a measured one\n{diagnostic[-3000:]}")
    return first


@functools.lru_cache(maxsize=None)
def trivial_relay_s() -> float:
    """Seconds until a nested driver session's first event REACHES an outer
    lease, HERE, spawn to relayed score.

    The larger of two consecutive readings, exactly as ``trivial_session_s``:
    one lucky start must not set the floor for the whole file.
    """
    with tempfile.TemporaryDirectory(prefix="vibeic-relay-") as d:
        return max(_one_trivial_relay_s(Path(d)) for _ in range(2))


#: WHERE AN ENCLOSING DRIVER TEST PUBLISHES ITS OWN DERIVED LEASE, so a nested
#: node can MIRROR it exactly (vibe-ic#2219).  A nested node that recomputes
#: `relay_window` runs the calibration IN SERIES with the silence that lease is
#: watching -- MEASURED on 8HD-6 in the pinned image at `--cpus=1`: the lease
#: grew to 5.431 s and the silence it had to cover grew to 5.479 s, so the
#: recomputation defeated the very repair it was part of.  Spelled once, here,
#: because a producer and a consumer that spell an env key separately are one
#: rename away from silently never meeting.
OUTER_LEASE_ENV = "VIBEIC_OUTER_LEASE_S"


def relay_window(nominal: float) -> float:
    """``stall_window`` for a lease renewed by a NESTED DRIVER'S relay.

    Same construction, same guarantee, different measured term: ``nominal``
    wherever the nested lane's first relayed event arrives inside it, else
    lifted to ``FLOOR_MULTIPLE`` measured relay floors. Nothing is relaxed —
    the kill direction is untouched and a subject that never relays is still
    stopped one window after its last event — and callers scale the ratios
    they assert from the value returned.

    This is what ``stall_window(..., starts=2)`` was standing in for. Prefer
    it wherever the events that renew the lease arrive through
    ``--progress-relay`` rather than from the subject pytest itself.
    """
    return max(float(nominal), FLOOR_MULTIPLE * trivial_relay_s())


# ── A BARE-INTERPRETER CHILD (the semantic-progress lane) ───────────────────
#
# A third measured term, for a third shape of subject. `trivial_session_s`
# times a pytest session and `trivial_relay_s` times a nested driver's first
# relayed event; neither is the right instrument for a lease held over a bare
# `python3 -c` child, which is what `test_semantic_child_progress.py` spawns.
# The pytest floor is ~10x too large there and would inflate every grace in
# that file for no reason.
#
# MEASURED 2026-09-09 in the pinned image at 12 concurrent containers: the
# declared `grace=0.18` produced
#
#     WATCHDOG_STALLED: ... did not advance for > 0.18s
#     ... incomplete domain-progress relay (0/4)
#
# -- ZERO of four checkpoints, i.e. the child was killed before reaching its
# first one -- on `610cae2cc` exactly as on `6883a9c93`.


def _one_trivial_child_s(cwd: Path) -> float:
    """Spawn-to-exit seconds of a bare ``python3 -c`` that imports the module a
    semantic child imports.  NOT a pytest session.
    """
    env = {k: v for k, v in os.environ.items() if k not in _INHERITED}
    env["PYTHONPATH"] = (
        str(_PROGRAMS) if not env.get("PYTHONPATH")
        else str(_PROGRAMS) + os.pathsep + env["PYTHONPATH"])
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-c", "import _semantic_child_progress"],
        cwd=str(cwd), env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True)
    elapsed = time.monotonic() - started
    assert proc.returncode == 0, (
        "the child-floor calibration must succeed, or the floor is a reading "
        f"of a failure, not of start-up; rc={proc.returncode}\n"
        f"{proc.stdout[-2000:]}")
    return elapsed


@functools.lru_cache(maxsize=None)
def _child_floor_at(width: int) -> float:
    """The cached reading; split from the public name for the reason above."""
    with tempfile.TemporaryDirectory(prefix="vibeic-child-floor-") as d:
        cwd = Path(d)
        if width == 1:
            return max(_one_trivial_child_s(cwd) for _ in range(2))
        worst = 0.0
        with concurrent.futures.ThreadPoolExecutor(max_workers=width) as pool:
            for _round in range(2):
                worst = max(worst, max(pool.map(
                    _one_trivial_child_s, [cwd] * width)))
        return worst


def trivial_child_s(width: int = 1) -> float:
    """Seconds a bare interpreter + one plugin import costs HERE, spawn to exit.

    Same construction and same reason as ``trivial_session_s``: the larger of
    two consecutive readings, taken at the width the caller will run at, on
    THIS box under THIS load.
    """
    if not isinstance(width, int) or width < 1:
        raise ValueError(f"width must be a positive int, got {width!r}")
    return _child_floor_at(width)


trivial_child_s.cache_clear = _child_floor_at.cache_clear


def child_window(nominal: float, *, width: int = 1) -> float:
    """``stall_window`` for a lease over a BARE-INTERPRETER child.

    Callers must scale their checkpoint cadence from the value returned,
    exactly as with ``stall_window`` and ``relay_window``.
    """
    return max(float(nominal), FLOOR_MULTIPLE * trivial_child_s(width))
