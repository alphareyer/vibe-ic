"""R-0915-71 — step 29 abandoned two simulations that were running perfectly.

MEASURED, sha256 x sky130A, lane icsha2 run15 (main 385445351), front door.
Step 29 published

    VERDICT: NOT_EXECUTED — 2 of 11 case(s) produced no verdict line at gate
    level; a missing marker is not a pass.

over `long_message_1m_bytes_of_a` and
`random_message_functional_equivalence_vs_nist_go`.  Both of them reached
`$finish` with 0 mismatches — 12 and 10008 oracle checks — roughly a hundred
minutes AFTER step 29 had read their half-written transcripts.

Timed from the artefacts' own mtimes (`<cid>_gatesim.vvp` built, to the last
byte of `<cid>.stdout.log`) against the slot the runner allowed before starting
the next case:

    nine cases       sim wall 107..137 s    slot 109..138 s
    long_message     sim wall     5980 s    slot      197 s
    random_message   sim wall     5725 s    slot      197 s

WHY.  `sdf_gate_sim._docker` ran `vvp ... > <cid>.stdout.log 2> <cid>.stderr.log`
through a bare `_progress_run.run`.  Every byte goes to a file INSIDE the command
string, so the supervised `docker exec` CLIENT emits nothing for the whole run,
and the client itself burns no CPU and does no I/O because the tool lives under
the container runtime's shim.  Output flat, cpu flat, io flat — a stall, and the
client was reaped while the simulator carried on, orphaned.

THE TWO-ARM MEASUREMENT, executed in the pinned container on this host, with a
CPU-burning job whose every byte goes to a file — the same shape, no simulator
needed, and the SAME stall window on both arms (poll_s=1.0, stall_looks=3):

    OLD  bare _pr.run over docker_exec_argv   RAISED Stalled after   4.1 s
    NEW  run_in_container_supervised          rc=0, completed at    10.8 s

vibe-ic#2083 had already measured and fixed this exact shape for a magic LEF
extraction ("on every host-side signal the client exposes, indistinguishable
from a corpse").  This call site never adopted it.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import _container_exec as _ce
import sdf_gate_sim as SG


class _Recorder:
    def __init__(self, rc=0, stdout="RC=0\n"):
        self.calls = []
        self._rc, self._out = rc, stdout

    def __call__(self, container, cmd, **kw):
        self.calls.append({"container": container, "cmd": cmd, **kw})
        import subprocess
        return subprocess.CompletedProcess([], self._rc, self._out, "")


# --------------------------------------------------------------------------
# the dispatch goes through the supervised container path
# --------------------------------------------------------------------------
def test_docker_dispatches_through_run_in_container_supervised(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(_ce, "run_in_container_supervised", rec)
    SG._docker("c", "echo hi", budget_s=900)
    assert len(rec.calls) == 1
    assert rec.calls[0]["container"] == "c"
    assert rec.calls[0]["cmd"].endswith("echo hi")


def test_the_budget_reaches_the_supervisor_as_a_recorded_ceiling(monkeypatch):
    """`ceiling_s` is a RECORDED BUDGET (vibe-ic#2051) and stops nothing — but
    it must at least ARRIVE, which the parameter it replaced never did."""
    rec = _Recorder()
    monkeypatch.setattr(_ce, "run_in_container_supervised", rec)
    SG._docker("c", "x", budget_s=900)
    assert rec.calls[0]["ceiling_s"] == 900.0


def test_the_tool_path_prelude_is_still_prepended(monkeypatch):
    """The conversion must not drop what the old argv carried."""
    rec = _Recorder()
    monkeypatch.setattr(_ce, "run_in_container_supervised", rec)
    SG._docker("c", "iverilog -v", budget_s=60)
    assert rec.calls[0]["cmd"].startswith(SG._TOOL_PATH)


def test_docker_no_longer_builds_a_bare_supervised_argv():
    """THE DEFECT ITSELF: a `_progress_run.run` over `docker_exec_argv` with no
    probe is the shape that cannot see the container's work."""
    src = inspect.getsource(SG._docker)
    assert "_pr.run" not in src
    assert "docker_exec_argv" not in src
    assert "run_in_container_supervised" in src


def test_no_call_site_still_passes_a_parameter_that_binds_nothing():
    """`timeout=` was accepted and dropped on the floor at every call site, so
    the numbers read like limits and were not.  There is no `timeout=` left in
    this module's own dispatch."""
    src = Path(SG.__file__).read_text()
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "_docker(" in code
    for line in code.splitlines():
        if "_docker(" in line:
            assert "timeout=" not in line, line


def test_the_supervised_path_is_the_one_that_carries_a_container_probe():
    """WHY the conversion is the fix, asserted against the module that provides
    it rather than restated: `run_in_container_supervised` supervises with
    `container_tree_probe`, which reads the CONTAINER's work."""
    src = inspect.getsource(_ce.run_in_container_supervised)
    assert "container_tree_probe" in src
    # and it reaps by identity inside the container, so a genuine stall does not
    # leave the orphan this defect created
    assert "kill_supervised_job" in src


# --------------------------------------------------------------------------
# a stall is now an rc the caller can name, not a raise it must guess at
# --------------------------------------------------------------------------
def test_a_stalled_case_comes_back_as_an_rc_the_namer_understands(monkeypatch):
    rec = _Recorder(rc=_ce.STALLED_RC, stdout="")
    monkeypatch.setattr(_ce, "run_in_container_supervised", rec)
    r = SG._docker("c", "x", budget_s=60)
    assert r.returncode == _ce.STALLED_RC
    named = SG.name_unverdicted_case(rc=r.returncode, size=10, truncated=False)
    assert "REAPED" in named


def test_a_reap_is_never_reported_as_a_clean_exit(monkeypatch):
    """NEGATIVE CONTROL.  `RC=0` is what the callers look for in stdout; a reap
    produces no such line, so it can never be read as a compile or a run that
    succeeded."""
    rec = _Recorder(rc=_ce.STALLED_RC, stdout="")
    monkeypatch.setattr(_ce, "run_in_container_supervised", rec)
    r = SG._docker("c", "x", budget_s=60)
    assert "RC=0" not in (r.stdout or "")


def test_a_normal_exit_is_unchanged(monkeypatch):
    """NEGATIVE CONTROL.  Nothing about the passing path moves."""
    rec = _Recorder(rc=0, stdout="RC=0\n")
    monkeypatch.setattr(_ce, "run_in_container_supervised", rec)
    r = SG._docker("c", "x", budget_s=60)
    assert r.returncode == 0 and "RC=0" in r.stdout
