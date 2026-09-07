#!/usr/bin/env python3
"""Forward progress *inside* one hygiene gate, so a slow gate is not a hung one.

WHY THIS MODULE EXISTS (vibe-ic#2177)
=====================================
The hygiene shard is supervised by ``_owned_process_supervisor``, and in that
mode the ONLY forward-progress signal is one attestation row per COMPLETED
GATE (``_owned_process_supervisor._AttestationProgressProbe``); stdout is
explicitly refused as a renewal (``output_progress=False``).  The lease is
spent between two consecutive validated events, so the unit that gets killed is
whatever sits between them -- and here that unit is a WHOLE GATE.

That makes the stall grace a wall clock for any single gate, which is exactly
what it was written not to be.  Measured, from this repo's own shipped cost
profile ``hygiene_gate_profile.json`` at v1.19.39::

    gates are host-independent      2556 s
    an argued direction is pinned    646 s

against the two graces actually in force::

    repo_hygiene_parallel.DEFAULT_STALL_GRACE_S   300 s   (a direct run)
    gatekeeper_review._HYGIENE_STALL_GRACE_S     1800 s   (the review's run)

Two gates are above the first and one is above the second, so on the shipped
profile the kill is not a possibility, it is an arithmetic certainty.  MEASURED
on 8HD-6, three landing logs of 2026-08-27 (``~/j1786/baseev/base_land.log``
and the two under ``~/j1786/tmp/gk_land_diff.8oX709/``), all three::

    arm A shard 0: PROGRESS_PROTOCOL_INCOMPLETE: attestation progress ended
    before assigned gates completed: an argued direction is pinned; progress
    watchdog outcome=stalled, rc=199; the shard did not complete naturally

and the landing then reported ``16 wiring error(s) in the hygiene gate
declarations, so the set certifies nothing``.  None of those sixteen was a
defect in the tree; every one was the fallout of a healthy 646 s gate being
killed at 300 s.

RAISING THE NUMBER IS NOT THE FIX, and that is the whole point of this file.
A bigger wall clock moves the cliff; it does not remove it, and it still kills
healthy slow work under load -- which is when the fleet is busiest and the
measurement matters most.  "How long has this taken" and "has this stopped
making progress" are different questions.  The answer is the one this repo
already reached for the pytest arm (``tools/ci/test_nested_progress_item_
granularity.py``) and for the routed-DEF checkers (``_routed_checker_
progress``): SUBDIVIDE THE OPAQUE UNIT.  A gate that is working through 140
probes says so 140 times; a gate that is wedged says nothing and is still
killed, by name.

WHAT IS AND IS NOT PROGRESS HERE
================================
Progress is a NEWLY COMPLETED, DECLARED-FINITE sub-unit of the gate in flight.
It is not output bytes, not CPU time, not "the process still exists" -- each of
those is renewed by a job that is going nowhere, which is the failure mode the
supervisor exists to catch.  A unit stream is finite BY CONSTRUCTION: the first
``gate_unit`` row for a label declares ``units`` (how many there will be),
every later row must strictly increase ``unit``, and ``unit`` may never exceed
``units``.  So a gate cannot hold its lease open indefinitely by chattering:
it can renew at most ``units`` times, and then it must finish.

THE CHANNEL IS OPT-IN AND BYTE-FOR-BYTE INERT WITHOUT IT
========================================================
``emit`` is a no-op unless the owning dispatcher exported
``VIBE_IC_GATE_INFLIGHT_FILE``.  Running any gate by hand, or through a
``repo_hygiene_gates.sh`` that nobody is supervising, writes nothing and
behaves exactly as before.  An emit that fails NEVER propagates: a gate must
not change its verdict because a progress file could not be written.  The cost
of a lost emit is a lease that is not renewed, which the supervisor already
reports honestly as a stall.

WHY IT IS A SEPARATE FILE FROM THE ATTESTATION JSONL
====================================================
The attestation file is consumed by ``repo_hygiene_parallel.merge_records``,
``hygiene_shard_aggregate``, ``gate_host_independence_check`` and the landing
record.  Every one of those reads it as "one row per completed gate".  Adding a
second row shape to it would change what all of them count while looking like a
progress fix.  This channel is written by the same processes, under its own
lock, and read by exactly one consumer.
"""
from __future__ import annotations

# --- sibling-import path (vibe-ic#2104) ------------------------------------
# `programs/` is a flat directory whose modules import each other by BARE
# name. Python puts a file's own directory on `sys.path` only when that file
# is run as `__main__`; under `importlib.util.spec_from_file_location` it does
# not, so every bare sibling import below raises ModuleNotFoundError.
import os as _os                                                    # noqa: E402
import sys as _sys                                                  # noqa: E402

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
# ---------------------------------------------------------------------------

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

SCHEMA = 1

#: The dispatcher exports these into the environment of the gate it is running.
#: Both must be present for `emit` to do anything: a path with no label cannot
#: be attributed, and a label with no path has nowhere to go.
ENV_PATH = "VIBE_IC_GATE_INFLIGHT_FILE"
ENV_LABEL = "VIBE_IC_GATE_INFLIGHT_LABEL"

STARTED = "gate_started"
UNIT = "gate_unit"
#: The WEAKER of the two renewal signals, and it is spelled differently on
#: purpose. `gate_unit` is a finite declared sub-unit of the gate's own work —
#: the strong claim, available only from a gate that knows its own denominator.
#: `gate_alive` is the DISPATCHER's observation that the gate it launched did
#: work in the last interval: its captured output grew, or its process tree
#: consumed CPU. Neither of those is available to a wedged gate (no output, no
#: CPU), and each row must PROVE its own advance — the reader refuses a row
#: whose byte and CPU counters both equal the previous one's, so an emitter
#: cannot renew by repeating itself.
#:
#: WHY IT IS ACCEPTED AT ALL. It is exactly the signal `_watchdog` accepts by
#: default for every other supervised job in this repo ("every readable
#: forward-progress signal — output, the process tree's CPU, its block I/O"),
#: and the vibe-ic#2051 ruling is explicit that a job still burning CPU is not
#: stopped by a clock. Without it, the 152 gates that do NOT know their own
#: denominator stay exactly as they were: silent for their whole life and
#: killed for being slow. MEASURED on the frozen base a1f3685837ca on 8HD-6 at
#: load1 ~30, `repo_hygiene_parallel --stall-grace 300`: FOUR shards killed at
#: rc 199, and the gate in flight for two of them was `every program is
#: reachable` — a gate the shipped cost profile does not even list among its
#: heavy rows. Instrumenting the two known-heavy gates would not have saved it.
ALIVE = "gate_alive"
FINISHED = "gate_finished"
_EVENTS = (STARTED, UNIT, ALIVE, FINISHED)

#: Resource bounds. A progress channel that can grow without limit is a way to
#: fill a disk, and this one is read in full on every poll.
MAX_ROWS = 200_000
MAX_BYTES = 16 * 1024 * 1024
#: The most sub-units one gate may declare. `gates are host-independent` probes
#: on the order of 140 gates over two trees; four figures is generous and still
#: finite.
MAX_UNITS = 100_000
MAX_LABEL_BYTES = 512
#: The most liveness rows ONE gate may contribute. Unlike `gate_unit`, a
#: liveness row has no declared denominator to be finite against, so the bound
#: is stated here instead of derived. At the dispatcher's 5 s cadence this is
#: just over three days for a single gate — far above the 2556 s of the
#: heaviest row this repo ships, and far below "forever".
MAX_ALIVE_ROWS = 60_000


class InflightProtocolError(ValueError):
    """The in-flight channel said something the protocol does not allow."""


def _canonical(row: Dict[str, object]) -> str:
    return json.dumps(row, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def _strict_loads(line: str) -> Dict[str, object]:
    """Parse one row, refusing every JSON shape that is not a plain object.

    `parse_constant` refuses NaN/Infinity: a non-finite number in a progress
    count would compare in ways no reader expects, and this file is read by a
    supervisor that decides whether to kill a process.
    """
    value = json.loads(
        line,
        parse_constant=lambda tok: (_ for _ in ()).throw(
            InflightProtocolError(f"non-finite JSON constant {tok}")))
    if not isinstance(value, dict):
        raise InflightProtocolError("in-flight progress row is not an object")
    return value


def _require_int(row: Dict[str, object], key: str) -> int:
    value = row.get(key)
    # `type(...) is not int` and not `isinstance`: True is an int to
    # `isinstance`, and a bool reaching a counter would compare as 0/1.
    if type(value) is not int:
        raise InflightProtocolError(f"{key} is not an integer")
    return value


def build(event: str, label: str, index: int, of: int,
          unit: Optional[int] = None,
          units: Optional[int] = None,
          bytes_seen: Optional[int] = None,
          cpu_ticks: Optional[int] = None) -> Dict[str, object]:
    """One protocol row, validated at construction so a writer cannot emit junk."""
    if event not in _EVENTS:
        raise InflightProtocolError(f"unknown in-flight event {event!r}")
    if not isinstance(label, str) or not label.strip():
        raise InflightProtocolError("in-flight row carries no gate label")
    if len(label.encode("utf-8")) > MAX_LABEL_BYTES:
        raise InflightProtocolError("in-flight gate label exceeds its bound")
    if type(index) is not int or type(of) is not int:
        raise InflightProtocolError("in-flight index/of are not integers")
    if of < 1 or index < 1 or index > of:
        raise InflightProtocolError("in-flight index is outside 1..of")
    row: Dict[str, object] = {"schema": SCHEMA, "event": event,
                              "label": label, "index": index, "of": of}
    if event == ALIVE:
        if type(bytes_seen) is not int or type(cpu_ticks) is not int:
            raise InflightProtocolError(
                "a liveness row must carry both counters it is judged on")
        if bytes_seen < 0 or cpu_ticks < 0:
            raise InflightProtocolError("liveness counters are negative")
        row["bytes"] = bytes_seen
        row["cpu_ticks"] = cpu_ticks
        return row
    if bytes_seen is not None or cpu_ticks is not None:
        raise InflightProtocolError(
            f"{event} carries liveness counters it does not own")
    if event == UNIT:
        if type(unit) is not int or type(units) is not int:
            raise InflightProtocolError("a unit row needs unit and units")
        if units < 1 or units > MAX_UNITS:
            raise InflightProtocolError("declared units outside 1..MAX_UNITS")
        if unit < 1 or unit > units:
            raise InflightProtocolError("unit is outside 1..units")
        row["unit"] = unit
        row["units"] = units
    elif unit is not None or units is not None:
        raise InflightProtocolError(
            f"{event} carries a unit count it does not own")
    return row


def append(path: str, row: Dict[str, object]) -> None:
    """Append one row under an exclusive lock keyed to the target.

    O_APPEND alone is enough for the write itself on Linux for a line this
    short, but the reader validates a byte-exact append-only PREFIX, and two
    writers whose lines interleave would make it refuse the whole channel. The
    lock is the same idiom `_gate_dispatch.sh:_gate_attest_locked` already uses
    for the attestation file, and for the same measured reason.
    """
    payload = (_canonical(row) + "\n").encode("utf-8")
    lock = path + ".lock"
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0),
                 0o600)
    try:
        try:
            import fcntl  # noqa: PLC0415 — absent only on non-POSIX hosts
        except ImportError:
            fcntl = None  # type: ignore[assignment]
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        out = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(out, payload)
        finally:
            os.close(out)
    finally:
        os.close(fd)


def emit(unit: int, units: int, *, env: Optional[Dict[str, str]] = None,
         index: int = 1, of: int = 1) -> bool:
    """A gate says it finished sub-unit `unit` of a declared `units`.

    Returns True when a row was written.  Every failure is swallowed and
    reported as False: a gate must never change its verdict, its output or its
    exit code because a liveness channel was unwritable.  The cost of a
    swallowed failure is a lease that does not get renewed, and the supervisor
    already reports that honestly as a stall rather than as a gate result.

    `index`/`of` default to 1/1 because the CHILD does not know its position in
    the shard's queue; the dispatcher's own `gate_started` row carries the real
    N-of-M and the reader takes it from there.
    """
    environ = os.environ if env is None else env
    path = (environ.get(ENV_PATH) or "").strip()
    label = (environ.get(ENV_LABEL) or "").strip()
    if not path or not label:
        return False
    try:
        append(path, build(UNIT, label, index, of, unit=unit, units=units))
    except (OSError, InflightProtocolError, ValueError, TypeError):
        return False
    return True


def enabled(env: Optional[Dict[str, str]] = None) -> bool:
    """True when a supervisor is listening.  Lets a gate skip the bookkeeping."""
    environ = os.environ if env is None else env
    return bool((environ.get(ENV_PATH) or "").strip()
                and (environ.get(ENV_LABEL) or "").strip())


@dataclass
class _LabelState:
    index: int
    of: int
    started: bool = False
    finished: bool = False
    unit: int = 0
    units: Optional[int] = None
    #: The last accepted liveness observation for this gate. `None` until the
    #: first one; after that a new row must beat one of the two or be refused.
    bytes_seen: Optional[int] = None
    cpu_ticks: Optional[int] = None
    alive_rows: int = 0


@dataclass
class InflightReader:
    """Validate the channel and score it.  The score is the renewal signal.

    The score is the number of ACCEPTED rows, which is monotone by
    construction: rows are append-only, `unit` strictly increases inside a
    label, and `unit <= units`, so the total number of rows a healthy run can
    ever produce is bounded by `2*of + sum(units)`.  A wedged gate that keeps
    the channel open cannot renew forever.
    """

    path: str
    expected_labels: Tuple[str, ...]
    size: int = 0
    identity: Optional[Tuple[int, int]] = None
    rows: List[str] = field(default_factory=list)
    labels: Dict[str, _LabelState] = field(default_factory=dict)
    of: Optional[int] = None
    error: str = ""

    def _fail(self, reason: str) -> int:
        if not self.error:
            self.error = reason
        return len(self.rows)

    def _accept(self, row: Dict[str, object]) -> None:
        """Fold one already-parsed row into the state, or raise."""
        if row.get("schema") != SCHEMA:
            raise InflightProtocolError("unknown in-flight schema")
        event = row.get("event")
        if event not in _EVENTS:
            raise InflightProtocolError(f"unknown in-flight event {event!r}")
        label = row.get("label")
        if not isinstance(label, str) or label not in self.expected_labels:
            raise InflightProtocolError(
                f"unassigned gate label in in-flight progress: {label!r}")
        index = _require_int(row, "index")
        of = _require_int(row, "of")
        state = self.labels.get(label)
        if event == STARTED:
            if state is not None and state.started:
                raise InflightProtocolError(
                    f"gate started twice: {label}")
            if of < 1 or index < 1 or index > of:
                raise InflightProtocolError("in-flight index outside 1..of")
            if self.of is None:
                self.of = of
            elif of != self.of:
                # The shard's queue length is fixed when the shard starts. A
                # second value means two runs are writing one file, and the
                # scores would then be nonsense rather than merely wrong.
                raise InflightProtocolError(
                    "in-flight queue length changed mid-run")
            self.labels[label] = _LabelState(index=index, of=of, started=True)
            return
        if state is None or not state.started:
            raise InflightProtocolError(
                f"in-flight row before its gate started: {label}")
        if state.finished:
            raise InflightProtocolError(
                f"in-flight row after its gate finished: {label}")
        if event == FINISHED:
            state.finished = True
            return
        if event == ALIVE:
            observed_bytes = _require_int(row, "bytes")
            observed_cpu = _require_int(row, "cpu_ticks")
            if observed_bytes < 0 or observed_cpu < 0:
                raise InflightProtocolError("liveness counters are negative")
            if state.bytes_seen is not None:
                # EVERY ROW PROVES ITS OWN ADVANCE. A repeated observation is
                # not a renewal, and refusing it here — rather than trusting
                # the emitter to have checked — is what makes this signal
                # something the supervisor VERIFIES instead of something it is
                # told. Neither counter may go backwards: a shrinking capture
                # file or a resetting CPU total means the emitter is looking at
                # a different subject than it was.
                if (observed_bytes < state.bytes_seen
                        or observed_cpu < state.cpu_ticks):
                    raise InflightProtocolError(
                        f"liveness counters went backwards: {label}")
                if (observed_bytes == state.bytes_seen
                        and observed_cpu == state.cpu_ticks):
                    raise InflightProtocolError(
                        f"liveness row shows no advance: {label}")
            if state.alive_rows >= MAX_ALIVE_ROWS:
                raise InflightProtocolError(
                    f"liveness rows exceed their bound: {label}")
            state.bytes_seen = observed_bytes
            state.cpu_ticks = observed_cpu
            state.alive_rows += 1
            return
        # UNIT. The CHILD does not know its N-of-M, so its index/of are not
        # compared against the dispatcher's; the label is the attribution and
        # the dispatcher's own started row already carries the position.
        unit = _require_int(row, "unit")
        units = _require_int(row, "units")
        if units < 1 or units > MAX_UNITS:
            raise InflightProtocolError("declared units outside 1..MAX_UNITS")
        if state.units is None:
            state.units = units
        elif units != state.units:
            # Finiteness is the whole guarantee. A denominator that moves is a
            # denominator that can be moved upward forever.
            raise InflightProtocolError(
                f"declared unit count changed mid-gate: {label}")
        if unit <= state.unit:
            raise InflightProtocolError(
                f"in-flight unit did not advance: {label}")
        if unit > units:
            raise InflightProtocolError(
                f"in-flight unit exceeds its declared total: {label}")
        state.unit = unit

    def sample(self) -> int:
        """Re-read and re-validate.  Returns the monotone renewal score."""
        if self.error:
            return len(self.rows)
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            # Not yet created is the normal state before the first gate starts.
            # Disappearing AFTER it existed is a protocol violation.
            if self.identity is not None:
                return self._fail("in-flight progress file disappeared")
            return len(self.rows)
        except OSError as exc:
            return self._fail(f"in-flight progress file unreadable: {exc}")
        observed = (st.st_dev, st.st_ino)
        if self.identity is None:
            self.identity = observed
        elif observed != self.identity:
            return self._fail("in-flight progress file identity changed")
        if st.st_size < self.size:
            return self._fail("in-flight progress file was truncated")
        if st.st_size > MAX_BYTES:
            return self._fail("in-flight progress file exceeds resource limit")
        try:
            with open(self.path, "rb") as handle:
                raw = handle.read()
        except OSError as exc:
            return self._fail(f"in-flight progress file unreadable: {exc}")
        complete, separator, _tail = raw.rpartition(b"\n")
        if not separator:
            complete = b""
        lines = complete.splitlines()
        if len(lines) > MAX_ROWS:
            return self._fail("in-flight progress exceeds its row bound")
        if len(lines) < len(self.rows):
            return self._fail("in-flight progress history was rewritten")
        canonical: List[str] = []
        # Re-fold from scratch: the state machine is cheap and folding only the
        # tail would trust a prefix this poll has not re-read.
        saved_labels, saved_of = self.labels, self.of
        self.labels, self.of = {}, None
        try:
            for lineno, line in enumerate(lines, 1):
                if not line.strip():
                    raise InflightProtocolError(
                        f"empty in-flight progress line {lineno}")
                row = _strict_loads(line.decode("utf-8"))
                self._accept(row)
                canonical.append(_canonical(row))
        except (InflightProtocolError, ValueError, TypeError,
                UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.labels, self.of = saved_labels, saved_of
            return self._fail(f"invalid in-flight progress protocol: {exc}")
        if canonical[:len(self.rows)] != self.rows:
            self.labels, self.of = saved_labels, saved_of
            return self._fail("in-flight progress history was rewritten")
        self.rows = canonical
        self.size = st.st_size
        return len(self.rows)

    def describe(self) -> str:
        """Name what was in flight, for a kill message that a reader can act on.

        "killed as hung" with no subject sends the reader to the whole set. The
        supervisor knows exactly which gate held the lease and how far into its
        own declared work it had got, and saying so is the difference between a
        report and an accusation.
        """
        live = [(state, label) for label, state in self.labels.items()
                if state.started and not state.finished]
        if not live:
            done = sum(1 for s in self.labels.values() if s.finished)
            if self.of:
                return (f"no gate was in flight; {done} of {self.of} "
                        "assigned gate(s) had completed")
            return "no gate was in flight and none had started"
        live.sort(key=lambda pair: (pair[0].index, pair[1]))
        done = sum(1 for s in self.labels.values() if s.finished)
        parts = []
        for state, label in live:
            if state.units is not None:
                parts.append(f"{label!r} (gate {state.index} of {state.of}, "
                             f"{state.unit} of {state.units} declared "
                             "sub-unit(s) reported)")
            elif state.alive_rows:
                parts.append(f"{label!r} (gate {state.index} of {state.of}, "
                             "no declared sub-units; "
                             f"{state.alive_rows} liveness observation(s), "
                             f"last {state.bytes_seen} output byte(s) and "
                             f"{state.cpu_ticks} CPU tick(s))")
            else:
                parts.append(f"{label!r} (gate {state.index} of {state.of}, "
                             "reported nothing at all since it started)")
        return ("in flight: " + "; ".join(parts)
                + f"; {done} of {self.of or '?'} assigned gate(s) completed")


def _started_count(path: str) -> int:
    """How many gates this shard has already announced.  Callers hold the lock."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except FileNotFoundError:
        return 0
    seen = 0
    complete, separator, _tail = raw.rpartition(b"\n")
    if not separator:
        return 0
    for line in complete.splitlines():
        if not line.strip():
            continue
        try:
            row = _strict_loads(line.decode("utf-8"))
        except (InflightProtocolError, ValueError, UnicodeDecodeError):
            # A malformed tail is the reader's problem to report, not this
            # writer's to repair. Counting what parses keeps `index` moving
            # forward, which is all this number is for.
            continue
        if row.get("event") == STARTED:
            seen += 1
    return seen


def append_event(path: str, event: str, label: str, of: int) -> None:
    """Append one dispatcher event, deriving `index` under the channel's lock.

    THE INDEX IS DERIVED HERE AND NOT PASSED IN, and that is not tidiness. The
    shell dispatcher runs its gates in a background POOL when `JOBS > 1`, and a
    counter incremented inside a `&` subshell does not survive back to the
    parent — so a shell-side index would silently repeat. Counting the rows
    already in the file, while holding the same lock the append takes, is the
    one place where the answer cannot race.
    """
    lock = path + ".lock"
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0),
                 0o600)
    try:
        try:
            import fcntl  # noqa: PLC0415
        except ImportError:
            fcntl = None  # type: ignore[assignment]
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        if event == STARTED:
            index = min(_started_count(path) + 1, of)
        else:
            # A finish belongs to a gate already announced; its index is not
            # read by the protocol beyond the 1..of bound, and the label is the
            # attribution.
            index = min(max(_started_count(path), 1), of)
        payload = (_canonical(build(event, label, index, of)) + "\n")
        out = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(out, payload.encode("utf-8"))
        finally:
            os.close(out)
    finally:
        os.close(fd)


#: Exit code of `--alive` when the subject did NOT advance. Deliberately not 1
#: (which the shell would read as a broken helper) and deliberately not 0: the
#: caller must be able to tell "I looked and it had moved" from "I looked and it
#: had not", because the second is the state a supervisor eventually acts on.
RC_NO_ADVANCE = 3


def _tree_cpu_ticks(root_pid: int, exclude_pid: int = 0) -> int:
    """Cumulative CPU of `root_pid`'s tree, minus one excluded subtree.

    utime + stime + cutime + cstime, so a gate that spawns and reaps hundreds of
    short-lived children — which is exactly what the heavy gates do — still
    shows its work: the reaped children's time is charged to the parent's
    `cutime`/`cstime` and would otherwise vanish between two polls.

    `exclude_pid` IS LOAD-BEARING AND NOT AN OPTIMISATION. Without it the poller
    that calls this function is itself inside the tree it measures, so its own
    CPU — and that of the `python3` running this very line — would renew the
    lease. A liveness probe that is renewed by its own existence is not a probe;
    it is a heartbeat on a timer wearing one's clothes, and this repo has
    already ruled that out.

    Unreadable `/proc` entries are skipped rather than guessed at: a process
    that exited between the listing and the read contributes nothing, which is
    the truthful answer and never an invented one.
    """
    children: Dict[int, List[int]] = {}
    ticks: Dict[int, int] = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return 0
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            with open(f"/proc/{pid}/stat", "rb") as handle:
                raw = handle.read()
        except OSError:
            continue
        # The command name is in parentheses and may itself contain spaces and
        # parentheses, so the split is anchored on the LAST ')' — the same
        # reading `_owned_process_supervisor._read_proc_identity` uses.
        close = raw.rfind(b")")
        if close < 0:
            continue
        fields = raw[close + 2:].split()
        if len(fields) < 15:
            continue
        try:
            ppid = int(fields[1])
            ticks[pid] = sum(int(fields[i]) for i in (11, 12, 13, 14))
        except (ValueError, IndexError):
            continue
        children.setdefault(ppid, []).append(pid)
    total = 0
    stack = [root_pid]
    seen = set()
    while stack:
        pid = stack.pop()
        if pid in seen or pid == exclude_pid:
            continue
        seen.add(pid)
        total += ticks.get(pid, 0)
        stack.extend(children.get(pid, ()))
    return total


def _last_alive(path: str, label: str) -> Optional[Tuple[int, int]]:
    """The counters of the most recent accepted liveness row for `label`."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except FileNotFoundError:
        return None
    except OSError:
        return None
    complete, separator, _tail = raw.rpartition(b"\n")
    if not separator:
        return None
    last: Optional[Tuple[int, int]] = None
    for line in complete.splitlines():
        if not line.strip():
            continue
        try:
            row = _strict_loads(line.decode("utf-8"))
        except (InflightProtocolError, ValueError, UnicodeDecodeError):
            continue
        if row.get("event") == ALIVE and row.get("label") == label:
            try:
                last = (int(row["bytes"]), int(row["cpu_ticks"]))
            except (KeyError, TypeError, ValueError):
                continue
    return last


def append_alive(path: str, label: str, of: int, capture: str,
                 root_pid: int, exclude_pid: int) -> int:
    """Observe the gate in flight; append a row ONLY if it actually advanced.

    Returns 0 when a row was written and `RC_NO_ADVANCE` when it was not. The
    whole decision — read the counters, compare with the last accepted row,
    write or refuse — happens under the channel's lock, so two pollers can
    never both decide they are the one that saw the advance.
    """
    lock = path + ".lock"
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0),
                 0o600)
    try:
        try:
            import fcntl  # noqa: PLC0415
        except ImportError:
            fcntl = None  # type: ignore[assignment]
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            size = os.stat(capture).st_size if capture else 0
        except OSError:
            size = 0
        cpu = _tree_cpu_ticks(root_pid, exclude_pid)
        previous = _last_alive(path, label)
        if previous is not None:
            before_bytes, before_cpu = previous
            size = max(size, before_bytes)
            cpu = max(cpu, before_cpu)
            if size == before_bytes and cpu == before_cpu:
                return RC_NO_ADVANCE
        index = min(max(_started_count(path), 1), of)
        row = build(ALIVE, label, index, of, bytes_seen=size, cpu_ticks=cpu)
        out = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(out, (_canonical(row) + "\n").encode("utf-8"))
        finally:
            os.close(out)
    finally:
        os.close(fd)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """`--started` / `--alive` / `--finished` for the dispatcher; `--read` for
    humans."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--path", required=True)
    parser.add_argument("--label")
    parser.add_argument("--of", type=int, default=1,
                        help="how many gates this shard was assigned")
    parser.add_argument("--capture", default="",
                        help="the file the gate's combined output is tee'd to")
    parser.add_argument("--root-pid", type=int, default=0,
                        help="the shard shell whose descendants ARE the gate")
    parser.add_argument("--exclude-pid", type=int, default=0,
                        help="the poller's own subtree, which must never renew")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--started", action="store_true")
    mode.add_argument("--finished", action="store_true")
    mode.add_argument("--alive", action="store_true")
    mode.add_argument("--read", action="store_true")
    args = parser.parse_args(argv)
    if args.read:
        reader = InflightReader(args.path, ())
        print(json.dumps({"rows": reader.sample(), "error": reader.error},
                         sort_keys=True))
        return 0
    if not args.label:
        parser.error("--started/--alive/--finished need --label")
    if args.alive:
        if args.root_pid < 1:
            parser.error("--alive needs --root-pid")
        try:
            return append_alive(args.path, args.label, args.of, args.capture,
                                args.root_pid, args.exclude_pid)
        except (OSError, InflightProtocolError, ValueError, TypeError) as exc:
            print(f"in-flight liveness not recorded: {exc}", file=sys.stderr)
            return 1
    try:
        append_event(args.path, STARTED if args.started else FINISHED,
                     args.label, args.of)
    except (OSError, InflightProtocolError, ValueError, TypeError) as exc:
        # rc 1 and a named reason. The dispatcher turns this into a printed
        # warning and keeps going: a gate must not be skipped because its
        # liveness row could not be written.
        print(f"in-flight progress not recorded: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
