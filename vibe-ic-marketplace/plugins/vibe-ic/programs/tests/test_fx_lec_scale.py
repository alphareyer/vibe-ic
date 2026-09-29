"""FX_AES_LEC_SCALE: a LEC rung that cannot finish inside the step budget is
stopped on the proof's OWN evidence, with its counts; it never eats the run.

MEASURED on opentitan_aes x sky130A (84,623 cells): step 13's
`equiv_induct -seq 4` over the 3,219 points `equiv_simple` left spent ~4,100 s
building its whole-design induction model (13M clauses at step 4), then decided
points one at a time at ~3.5 s each: 936 decided (711 proven, 223 failed) when
the run's outer 3 h timeout killed it. At that rate the rung needed ~11,000 s
more against a declared 7,200 s step budget. No verdict, no counts.

The budget stops nothing by itself (#2051, R-0915-48; the landed contracts in
test_progress_supervision_over_wallclock / test_r0915_48 still hold): a rung
that has decided nothing yet, or whose measured rate finishes in time, runs on.
Only the rung's own projection -- remaining points / measured decision rate
greater than what is left of the budget -- stops it, through the supervisor's
`abort_probe`, and the stop is booked on the existing no-verdict path.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
for _p in (str(PROGRAMS), str(PROGRAMS.parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import lec_run as L  # noqa: E402

CAL = PROGRAMS / "calibration"


def _induct_log(workset: int, decisions: int, *, prefix: str = "") -> str:
    lines = [prefix + "4. Executing EQUIV_INDUCT pass.",
             f"Found {workset} unproven $equiv cells in module equiv:",
             "  Proving existence of base case for step 1. (9 clauses over 3 "
             "variables)",
             "  Proof for induction step failed. Trying to prove individual "
             "$equiv from workset."]
    for i in range(decisions):
        lines.append(f"  Trying to prove $equiv for \\p{i}: "
                     + ("success!" if i % 4 else "failed."))
    return "\n".join(lines) + "\n"


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


# ── the reader ────────────────────────────────────────────────────────────

def test_the_reader_counts_the_real_per_point_decisions():
    got = L.induct_decision_progress(
        (CAL / "lec_induct_decisions_positive.log").read_text())
    assert got == {"workset": 2, "decided": 2, "proved": 1, "failed": 1}
    assert L.induct_decision_progress(
        (CAL / "lec_induct_workset_only_negative.log").read_text()) == \
        {"workset": 2, "decided": 0, "proved": 0, "failed": 0}


def test_the_reader_reads_only_the_last_induct_pass_and_not_simple():
    text = ("18. Executing EQUIV_SIMPLE pass.\n"
            "  Trying to prove $equiv for \\a:ezsat 9 clauses\n"
            + _induct_log(10, 3) + _induct_log(7, 2, prefix=""))
    assert L.induct_decision_progress(text) == \
        {"workset": 7, "decided": 2, "proved": 1, "failed": 1}
    assert L.induct_decision_progress("no pass here\n") is None


def test_the_reader_does_not_take_progress_from_an_echoed_hdl_comment():
    """A quoted source comment is not a LEC workset or a decided point."""
    quoted = ("// 4. Executing EQUIV_INDUCT pass.\n"
              "// Found 999 unproven $equiv cells in module equiv:\n"
              "//   Trying to prove $equiv for \\spoof: success!\n")
    assert L.induct_decision_progress(quoted) is None


# ── the probe ─────────────────────────────────────────────────────────────

def _probe(tmp_path, total_s, *, offset=0):
    clock = _Clock()
    budget = L.StepBudget(total_s, clock=clock)
    log = tmp_path / "live.rpt"
    log.write_text("")
    return log, clock, L.rung_projection_abort_probe(
        log, offset, budget, clock=clock)


def test_no_decisions_no_evidence_no_stop(tmp_path):
    """The model-building phase prints nothing countable: the probe stays
    silent however long it lasts."""
    log, clock, probe = _probe(tmp_path, 100)
    log.write_text(_induct_log(3219, 0))
    for t in (10, 1000, 10**6):
        clock.t = t
        assert probe() is None


def test_a_rung_that_finishes_in_budget_is_never_stopped(tmp_path):
    log, clock, probe = _probe(tmp_path, 7200)
    log.write_text(_induct_log(100, 5))
    clock.t = 10
    assert probe() is None                         # first look: the baseline
    log.write_text(_induct_log(100, 60))
    clock.t = 20                                   # 55 in 10 s: done in ~7 s
    assert probe() is None


def test_a_rung_that_cannot_finish_in_budget_is_stopped_with_its_counts(
        tmp_path):
    log, clock, probe = _probe(tmp_path, 7200)
    log.write_text(_induct_log(3219, 10))
    clock.t = 5700
    assert probe() is None
    log.write_text(_induct_log(3219, 110))
    clock.t = 5700 + 350                           # 100 in 350 s
    why = probe()
    assert why and "cannot be met at the proof's own measured rate" in why
    assert "decided 110 of 3219 points" in why
    assert "not durable" in why


def test_too_few_decisions_do_not_make_a_rate(tmp_path):
    log, clock, probe = _probe(tmp_path, 10)
    log.write_text(_induct_log(3219, 1))
    clock.t = 1
    assert probe() is None
    log.write_text(_induct_log(3219, 1 + L.PROJECTION_MIN_DECISIONS - 1))
    clock.t = 10**5
    assert probe() is None


def test_the_probe_reads_only_its_own_legs_part_of_the_log(tmp_path):
    earlier = _induct_log(3219, 500)               # a previous leg's pass
    log, clock, probe = _probe(tmp_path, 10, offset=len(earlier.encode()))
    log.write_text(earlier)
    clock.t = 1
    assert probe() is None                         # nothing of its own yet
    clock.t = 10**5
    assert probe() is None


# ── the stop is booked on the no-verdict path ─────────────────────────────

def test_an_abort_is_a_disclosed_no_verdict_never_pass_or_fail(monkeypatch):
    raw = "Yosys 0.69\n" + _induct_log(3219, 110)

    def fake_docker(container, cmd, timeout=120, marker=None, **kw):
        assert kw.get("abort_probe") is not None
        return subprocess.CompletedProcess(
            cmd, L._RC_ABORTED, raw,
            "WATCHDOG_ABORTED: the step budget cannot be met at the proof's "
            "own measured rate: equiv_induct has decided 110 of 3219 points\n")
    monkeypatch.setattr(L, "_docker", fake_docker)
    monkeypatch.setattr(L, "probe_cgroup_memory",
                        lambda c, exec_raw=None: {"oom_kills": None,
                                                  "memory_max_bytes": None})
    launched, out = L.run_yosys_equiv("c", "/x/lec.ys", timeout=7200,
                                      abort_probe=lambda: None)
    assert launched
    assert L._TIMEOUT_MARKER in out and "(projected, not elapsed)" in out
    assert "decided 110 of 3219 points" in out
    assert L.run_was_stopped(out)
    verdict = L.parse_equiv_output(out)
    word = str(verdict.get("verdict", "")).upper()
    assert word not in ("PASS", "FAIL"), verdict


# ── over a real supervised subprocess ─────────────────────────────────────

def _job(tmp_path, workset, n, tick):
    script = tmp_path / "job.py"
    lines = _induct_log(workset, 0).splitlines()
    script.write_text(
        "import sys,time\n"
        f"for l in {lines!r}:\n    print(l, flush=True)\n"
        f"for i in range({n}):\n"
        "    print('  Trying to prove $equiv for \\\\p%d: success!' % i, flush=True)\n"
        f"    time.sleep({tick})\n"
        "print('DONE', flush=True)\n")
    return script


@pytest.mark.parametrize("workset, n, total_s, stopped", [
    (100000, 400, 5, True),       # ~0.05 s/point: 100k points need ~80 min
    (60, 60, 3600, False),        # finishes in ~3 s, well inside the budget
])
def test_a_real_rung_is_stopped_only_when_it_cannot_finish(
        tmp_path, monkeypatch, workset, n, total_s, stopped):
    """The REAL supervisor (host mode), looking every 0.2 s instead of every
    30 s so the test is short; the probe and the stop are the product's."""
    import _docker_watchdog as D
    real = D.run_docker_supervised
    monkeypatch.setattr(D, "run_docker_supervised",
                        lambda *a, **kw: real(*a, **dict(kw, poll_s=0.2)))
    log = tmp_path / "live.rpt"
    log.write_text("")
    script = _job(tmp_path, workset, n, 0.05)
    budget = L.StepBudget(total_s)
    probe = L.rung_projection_abort_probe(log, 0, budget)
    cmd = (f"set -o pipefail; {sys.executable} {script} | tee -a {log}")
    r = L._docker("", cmd, timeout=total_s, marker=str(script),
                  abort_probe=probe)
    if stopped:
        assert r.returncode == L._RC_ABORTED, (r.returncode, r.stderr[-300:])
        assert "DONE" not in r.stdout
        assert "cannot be met at the proof's own measured rate" in r.stderr
    else:
        assert r.returncode == 0, r.stderr[-300:]
        assert "DONE" in r.stdout


# ── the ladder wiring ─────────────────────────────────────────────────────

def test_every_ladder_leg_runs_under_its_own_projection_probe():
    tree = ast.parse((PROGRAMS / "lec_run.py").read_text())
    main = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    legs = [n for n in ast.walk(main) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name) and n.func.id == "run_yosys_equiv"
            and any(k.arg == "live_log_path" for k in n.keywords)]
    assert len(legs) == 1
    kw = {k.arg: k.value for k in legs[0].keywords}
    assert isinstance(kw.get("abort_probe"), ast.Name)
    made = [n for n in ast.walk(main) if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == kw["abort_probe"].id
                    for t in n.targets)]
    assert made and "rung_projection_abort_probe" in ast.unparse(made[0])
