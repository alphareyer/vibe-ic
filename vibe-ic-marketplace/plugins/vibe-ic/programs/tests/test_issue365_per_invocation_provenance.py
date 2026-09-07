#!/usr/bin/env python3
"""#365 third ask — one provenance entry per EDA invocation, with a REAL
duration.

The ledger previously held a handful of BACK-FILLED entries per flow, written
for artefacts found on disk with a `duration_ms` nobody measured. This records
one entry per SUPERVISED tool run and measures it.

SCOPE is the runner's own signal: `_docker_exec(marker=...)` is how this file
already distinguishes an open-ended TOOL RUN from a bounded shell probe
(`command -v`, `ls`, `ps`). Logging the probes as well would bury the tool runs
in noise — the opposite of what the issue asks for.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROGRAMS))
import phase3_one_shot_runner as p3  # noqa: E402

_RUNNER = _PROGRAMS / "phase3_one_shot_runner.py"


def _entries(d: Path):
    f = d / "provenance.jsonl"
    return [json.loads(l) for l in f.read_text().splitlines()] if f.is_file() else []


def _source_span(needle: str, end: str | None = None) -> str:
    """Read a span of the runner's SOURCE, and REFUSE BY NAME when the spelling
    is not there any more.

    `str.index` raises a bare `ValueError: substring not found`. That is not a
    detail: it is how vibe-ic#2114 was reported. This module's wiring pin died
    on `src.index("res = _wd.run_supervised(")` after v1.18.43 moved phase3's
    supervised dispatch onto `_docker_watchdog.run_docker_supervised`, and the
    red said nothing about the property the test exists to guard and nothing
    about WHICH spelling had moved -- it failed before it could measure
    anything. A source probe that has lost its needle must say which needle,
    in which file, so the next reader can re-aim it instead of re-deriving it.
    """
    src = _RUNNER.read_text()
    i = src.find(needle)
    assert i >= 0, (
        "%s no longer contains the spelling %r, so this source probe is "
        "pinned to a contract that has MOVED. Re-aim it at the code that "
        "carries the property now; do not delete it, and do not read this as "
        "a statement about the property itself -- it is a statement about "
        "this probe." % (_RUNNER.name, needle))
    if end is None:
        return src[i:]
    j = src.find(end, i + 1)
    assert j >= 0, (
        "%s contains %r but no %r after it, so this source probe cannot "
        "bound its span. Re-aim it." % (_RUNNER.name, needle, end))
    return src[i:j]


class _SupervisedStub:
    """Stands in for `_docker_watchdog.run_docker_supervised` and CONSUMES a
    known amount of wall time, so the duration the ledger records can be
    compared against something this test controls."""

    def __init__(self, seconds: float, rc: int = 0):
        self.seconds, self.rc = seconds, rc
        self.calls: list = []

    def __call__(self, container, cmd, marker, **kw):
        self.calls.append((container, cmd, marker))
        time.sleep(self.seconds)
        return (self.rc, "stdout", "stderr")


def _drive_supervised(monkeypatch, sink: Path, seconds: float, rc: int = 0,
                      marker: str = "provenance_probe.tcl"):
    """Drive the RUNNING code through the supervised branch of `_docker_exec`
    with the dispatch stubbed to take `seconds`, and return (stub, result)."""
    stub = _SupervisedStub(seconds, rc)
    monkeypatch.setattr(p3._dwd, "run_docker_supervised", stub)
    # A test that reads the host is a test about the host: pin the route.
    monkeypatch.setattr(p3, "_LOCAL_EXEC_MODE", False)
    p3.set_invocation_provenance_sink(sink)
    try:
        res = p3._docker_exec("", "openroad -no_init -exit r.tcl",
                              timeout=600, marker=marker)
    finally:
        p3.set_invocation_provenance_sink(None)
    return stub, res


def test_365_an_invocation_is_recorded_with_a_measured_duration(tmp_path):
    p3.set_invocation_provenance_sink(tmp_path)
    try:
        p3._log_invocation("openroad -no_init -exit pnr.tcl", 0, 12345,
                           marker="PNR")
    finally:
        p3.set_invocation_provenance_sink(None)
    e = _entries(tmp_path)
    assert len(e) == 1
    assert e[0]["tool"] == "openroad" and e[0]["duration_ms"] == 12345
    assert e[0]["measured"] is True and e[0]["exit_code"] == 0
    assert e[0]["marker"] == "PNR"


def test_365_tool_name_is_the_commands_own_first_token(tmp_path):
    """chip/tool-AGNOSTIC: the name comes from the command, never from a
    known-tools list, and an absolute path is reduced to its basename."""
    p3.set_invocation_provenance_sink(tmp_path)
    try:
        p3._log_invocation("/foss/tools/bin/yosys -p 'synth'", 1, 42)
        p3._log_invocation("some_future_tool --flag", 0, 7)
    finally:
        p3.set_invocation_provenance_sink(None)
    assert [e["tool"] for e in _entries(tmp_path)] == ["yosys",
                                                       "some_future_tool"]


def test_365_tool_name_survives_the_runners_real_export_prologue(tmp_path):
    """REGRESSION, found on a REAL run and invisible to the test above.

    Every container command this runner emits is prefixed with
    `export PATH=... &&`, so taking argv[0] recorded `tool: "export"` for
    EVERY EDA invocation — a ledger column that looks populated while naming
    a shell builtin, which is the very defect #365 was filed about.

    The pre-existing unit test passed throughout because its fixture used a
    bare `yosys ...` command; production never looks like that. The command
    below is the shape the runner actually produced, copied from the ledger
    of a real OpenROAD run.
    """
    real = ("export PATH=/foss/tools/openroad/bin:/foss/tools/bin:$PATH && "
            "openroad -no_init -exit /w/reports/phase3/ir_em_spm.tcl 2>&1 "
            "| tee /w/reports/phase3/ir_em.log")
    p3.set_invocation_provenance_sink(tmp_path)
    try:
        p3._log_invocation(real, 0, 798)
        p3._log_invocation("cd /w && yosys -s synth.ys", 0, 12)
        p3._log_invocation("FOO=1 netgen -batch source lvs.tcl", 0, 5)
    finally:
        p3.set_invocation_provenance_sink(None)
    assert [e["tool"] for e in _entries(tmp_path)] == ["openroad", "yosys",
                                                       "netgen"]


def test_365_a_chain_that_is_only_prologue_still_names_something(tmp_path):
    """The paired half: never return an empty tool. If the whole chain is
    shell prologue there IS no program, and reporting the prologue is honest
    — inventing one would not be."""
    p3.set_invocation_provenance_sink(tmp_path)
    try:
        p3._log_invocation("export A=1 && export B=2", 0, 1)
        p3._log_invocation("   ", 0, 1)
    finally:
        p3.set_invocation_provenance_sink(None)
    assert [e["tool"] for e in _entries(tmp_path)] == ["export", "sh"]


def test_365_the_other_writers_duration_key_is_populated(tmp_path):
    """`provenance_logger.py` writes `duration_s` into this SAME file. Emitting
    only `duration_ms` would leave every existing consumer of `duration_s`
    reading nothing for these rows — a new reader-without-producer split
    (#312 family) manufactured by the fix for #365."""
    p3.set_invocation_provenance_sink(tmp_path)
    try:
        p3._log_invocation("openroad x.tcl", 0, 2500)
    finally:
        p3.set_invocation_provenance_sink(None)
    e = _entries(tmp_path)[0]
    assert e["duration_ms"] == 2500
    assert e["duration_s"] == 2.5
    assert e["record"] == "invocation"


def test_365_no_sink_means_no_writes_anywhere(tmp_path):
    """A library caller must not have entries appear in someone else's tree."""
    p3.set_invocation_provenance_sink(None)
    p3._log_invocation("openroad x", 0, 1)
    assert _entries(tmp_path) == []


def test_365_logging_never_breaks_the_run(tmp_path):
    """A ledger that can break the run it documents would be traded away the
    first time it did. An unwritable sink must be swallowed."""
    bad = tmp_path / "nope"
    bad.write_text("not a directory")          # <sink>/provenance.jsonl fails
    p3.set_invocation_provenance_sink(bad)
    try:
        p3._log_invocation("openroad x", 0, 1)   # must not raise
    finally:
        p3.set_invocation_provenance_sink(None)


def test_365_the_duration_is_measured_at_the_supervised_call_site(tmp_path,
                                                                  monkeypatch):
    """Wiring pin: the supervised branch must TIME the run and log it, or the
    feature exists in the helper and not in the flow.

    ASKED OF THE RUNNING CODE (vibe-ic#2114). This used to index phase3's
    SOURCE for `res = _wd.run_supervised(` and check that `time.monotonic()`
    and `_log_invocation(` appeared in a +-few-hundred-character window around
    it. v1.18.43 (#2051) moved the dispatch onto the shared
    `_docker_watchdog.run_docker_supervised`, the spelling went with it, and
    the pin died of `ValueError: substring not found` -- BEFORE it could
    measure anything, on a tree where the property was in fact intact. A
    proximity window is also not the property even when it hits: two
    unconnected statements near each other read the same as a clock that
    brackets a dispatch.

    So the dispatch is stubbed to consume a KNOWN amount of wall time and the
    ledger is read back. Three claims, each of which kills a different
    mutation:

      (a) the recorded duration BRACKETS the supervised dispatch -- moving the
          clock start to after the dispatch records ~0;
      (b) the entry is THIS call's -- its exit code, marker and tool come from
          the supervised result, not from a neighbouring record;
      (c) it is a MEASUREMENT and not a constant -- a slow dispatch and a fast
          one are recorded as different durations, in the right order.
    """
    SLOW_S, FAST_S = 0.40, 0.0
    slow, fast = tmp_path / "slow", tmp_path / "fast"
    slow.mkdir()
    fast.mkdir()

    stub, (rc, _out, _err) = _drive_supervised(monkeypatch, slow, SLOW_S, rc=7)
    assert stub.calls, "the supervised branch of `_docker_exec` dispatched nothing"

    e = _entries(slow)
    assert len(e) == 1, (
        "one supervised tool run must leave exactly ONE ledger entry; the "
        "supervised call site left %d: %r" % (len(e), e))
    rec = e[0]

    # (a) THE CLOCK BRACKETS THE DISPATCH. `int()` truncates the ms, so the
    #     floor is the stub's own sleep less one tick -- never less.
    assert rec["duration_ms"] >= int(SLOW_S * 1000) - 1, (
        "the recorded duration (%r ms) is shorter than the dispatch it is "
        "supposed to have timed (%d ms), so the measurement is not taken "
        "AROUND the supervised call -- the clock starts after it, or the "
        "duration comes from somewhere else."
        % (rec["duration_ms"], int(SLOW_S * 1000)))
    assert rec["measured"] is True
    assert rec["duration_s"] == round(rec["duration_ms"] / 1000.0, 3)

    # (b) IT IS THIS CALL'S RECORD, not a neighbour's.
    assert rec["exit_code"] == 7, rec
    assert rec["marker"] == "provenance_probe.tcl", rec
    assert rec["tool"] == "openroad", rec
    assert rc == 7, "the supervised result must reach the caller unchanged"

    # (c) A MEASUREMENT, NOT A CONSTANT. A hard-coded duration -- of any size,
    #     including one that would satisfy (a) -- is identical in both arms.
    _drive_supervised(monkeypatch, fast, FAST_S, rc=0)
    quick = _entries(fast)
    assert len(quick) == 1, quick
    delta = rec["duration_ms"] - quick[0]["duration_ms"]
    assert delta >= int((SLOW_S - FAST_S) * 1000 * 0.5), (
        "a %.2fs dispatch and a %.2fs one were recorded as %r ms and %r ms: "
        "the ledger's duration does not track the time the supervised call "
        "actually took, so it is a constant, not a measurement."
        % (SLOW_S, FAST_S, rec["duration_ms"], quick[0]["duration_ms"]))


def test_365_the_sink_is_pointed_at_the_project_by_the_runner():
    _source_span("set_invocation_provenance_sink(project)")


def test_365_bounded_probes_are_not_logged():
    """`_docker_exec_raw` handles the short probes; it must NOT log, or the
    ledger fills with `command -v` / `ls` / `ps` and the tool runs are lost
    in it."""
    body = _source_span("def _docker_exec_raw(", end="\ndef ")
    assert "_log_invocation(" not in body, (
        "`_docker_exec_raw` logs: the ledger will fill with `command -v` / "
        "`ls` / `ps` and the tool runs will be lost in it")
