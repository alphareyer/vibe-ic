#!/usr/bin/env python3
"""The loop goes all the way round, and every transition is really executed.

WHAT IS REAL HERE AND WHAT IS NOT — stated first, because a fixture that implies
more than it proves is the defect this lane exists to remove
==============================================================================
REAL: the registry file, the loader, the parameter typing, the precondition
evaluation, the argv construction, the `subprocess.run` of a real executable
program, the file it really writes, the snapshot, the restore, the tree digests
that PROVE the restore, the metric extraction, and every stop condition. Byte
for byte the same code path `test_ppa_closure_real_programs.py` drives with the
shipped `openroad_hold_repair_tcl_gen` and `pnr_timing_repair_completeness_check`.

SYNTHETIC: the DOMAIN. The actuator and the measurement are two small programs
this test materialises in a tmp directory, because — measured, and recorded in
the lane's findings — no shipped pair exists whose actuator moves its own
measure, and the one real pair cannot IMPROVE (its `--out` replaces where an
amendment must append). So PROMOTE and CONVERGED are exercised here and
HANDOFF_REQUIRED is exercised there, and neither file pretends to be the other.

The fixture programs are written into a tmp directory and the registry's
`programs_dir` is pointed at it. They are never written into the shipped tree:
`suite_write_guard` forbids that, and a fixture sitting in `programs/` would be
on the same shelf as a program the flow can call.
"""
from __future__ import annotations

import json
import pathlib
import sys
import textwrap

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from _ppa import closure  # noqa: E402

# --------------------------------------------------------------------------
# The fixture domain: an implementation is a file holding one integer, the
# "violation count". The actuator subtracts a bounded amount from it. The
# measurement reads it back and writes a JSON document. Both are real
# executables invoked over a real argv by the shipped controller.
# --------------------------------------------------------------------------

ACTUATOR_SRC = textwrap.dedent('''
    #!/usr/bin/env python3
    """Fixture actuator: really rewrites a real file, by a bounded amount."""
    import argparse, pathlib, sys

    ap = argparse.ArgumentParser()
    ap.add_argument("--reduce-by", type=int, required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--collateral-target", default=None)
    ap.add_argument("--collateral-add", type=int, default=0)
    ap.add_argument("--refuse", action="store_true")
    ap.add_argument("--busy-s", type=float, default=0.0)
    ap.add_argument("--hang-pid-file", default=None)
    a = ap.parse_args()
    if a.hang_pid_file:
        import os, time
        pathlib.Path(a.hang_pid_file).write_text(str(os.getpid()))
        time.sleep(600)
    if a.busy_s:
        import time
        end = time.monotonic() + a.busy_s
        while time.monotonic() < end:
            pass
    if a.refuse:
        print("fixture actuator refuses this parameter set", file=sys.stderr)
        sys.exit(1)
    p = pathlib.Path(a.target)
    p.write_text(str(max(0, int(p.read_text().strip()) - a.reduce_by)) + "\\n")
    if a.collateral_target and a.collateral_add:
        q = pathlib.Path(a.collateral_target)
        q.write_text(str(int(q.read_text().strip()) + a.collateral_add) + "\\n")
    ''').lstrip()

MEASURE_SRC = textwrap.dedent('''
    #!/usr/bin/env python3
    """Fixture measurement: rc 0/1/2 with the honest refusal on absent input."""
    import argparse, json, pathlib, sys

    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--json", dest="out", required=True)
    a = ap.parse_args()
    p = pathlib.Path(a.file)
    if not p.is_file():
        print("[CANNOT CHECK] fixture measure: %s absent" % p, file=sys.stderr)
        sys.exit(2)
    n = int(p.read_text().strip())
    pathlib.Path(a.out).write_text(json.dumps({"summary": {"violations": n}}))
    sys.exit(0 if n == 0 else 1)
    ''').lstrip()


@pytest.fixture()
def bench(tmp_path):
    """Materialise the two real programs and return a builder for registries."""
    progs = tmp_path / "progs"
    progs.mkdir()
    (progs / "fixture_actuator.py").write_text(ACTUATOR_SRC, encoding="utf-8")
    (progs / "fixture_measure.py").write_text(MEASURE_SRC, encoding="utf-8")
    impl = tmp_path / "impl"
    impl.mkdir()

    def build(plan, *, start=3, collateral_start=None, stop=None,
              satisfied=0, extra_actuator=None, remeasure=None):
        (impl / "violations.txt").write_text(f"{start}\n", encoding="utf-8")
        domains = {
            "fix.primary": {
                "metric": "fixture.violations", "unit": "count",
                "direction": "minimize", "binding": "EXECUTABLE",
                "flow_steps": ["19", "20"],
                "measure": {
                    "program": "fixture_measure",
                    "argv_template": ["--file", "{impl_root}/violations.txt",
                                      "--json", "{json_out}"],
                    "extract": {"kind": "json_pointer",
                                "pointer": "/summary/violations"},
                },
                "satisfied_when": {"op": "<=", "value": satisfied},
            },
        }
        if collateral_start is not None:
            (impl / "other.txt").write_text(f"{collateral_start}\n", encoding="utf-8")
            domains["fix.other"] = {
                "metric": "fixture.other_violations", "unit": "count",
                "direction": "minimize", "binding": "EXECUTABLE",
                "flow_steps": ["21"],
                "measure": {
                    "program": "fixture_measure",
                    "argv_template": ["--file", "{impl_root}/other.txt",
                                      "--json", "{json_out}"],
                    "extract": {"kind": "json_pointer",
                                "pointer": "/summary/violations"},
                },
                "satisfied_when": {"op": "<=", "value": 0},
            }
        params = {
            "reduce_by": {"type": "integer", "unit": "count",
                          "minimum": 0, "maximum": 10},
            "target": {"type": "path"},
            # Defaults, not bare optionals: these two appear in the argv
            # template when the collateral domain is in play, and the loader now
            # refuses a template slot that cannot always be filled.
            "collateral_target": {"type": "path", "required": False,
                                  "default": "other.txt"},
            "collateral_add": {"type": "integer", "unit": "count",
                               "minimum": 0, "maximum": 10, "required": False,
                               "default": 0},
        }
        argv = ["--reduce-by", "{reduce_by}", "--target", "{target}"]
        if collateral_start is not None:
            argv += ["--collateral-target", "{collateral_target}",
                     "--collateral-add", "{collateral_add}"]
        act = {
            "summary": "fixture", "binding": "EXECUTABLE",
            "wrapper": {"program": "fixture_actuator", "argv_template": argv},
            "parameters": params,
            "preconditions": [{"kind": "file_exists", "path": "{target}"}],
            "blast_radius": "DECK",
            "resource_ceilings": {"wall_seconds": 30,
                                  "max_invocations_per_run": 8},
            "rollback": "SNAPSHOT_RESTORE",
            "remeasure_domains": remeasure or sorted(domains),
        }
        if extra_actuator:
            act.update(extra_actuator)
        doc = {
            "schema": closure.SCHEMA_REGISTRY,
            "domains": domains,
            "actuators": {"fix.reduce": act},
            "controllers": {
                "fix.loop": {
                    "summary": "fixture", "objective_domain": "fix.primary",
                    "actuator": "fix.reduce", "plan": plan,
                    "stop": stop or {"max_iterations": 6, "plateau_patience": 2,
                                     "wall_seconds": 120},
                },
            },
            "edges": {"20": {"controller": "fix.loop"},
                      "24": {"controller": None}},
        }
        path = tmp_path / "reg.yaml"
        path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        reg = closure.load_registry(path, programs_dir=progs)
        return reg, closure.ClosureController(reg, impl, tmp_path / "work")

    build.impl = impl
    build.progs = progs
    return build


def _p(**kw):
    d = {"reduce_by": kw.pop("reduce_by", 1), "target": "violations.txt"}
    d.update(kw)
    return d


# --------------------------------------------------------------------------
# POSITIVE: the whole round trip, converging.
# --------------------------------------------------------------------------

def test_the_loop_goes_all_the_way_round_and_converges(bench):
    """baseline violation -> actuator CHANGES the implementation -> metric
    RE-MEASURED -> improvement PROMOTES -> stop. Every step asserted."""
    reg, ctl = bench([_p(reduce_by=1), _p(reduce_by=1), _p(reduce_by=1)], start=3)
    run = ctl.run_edge("20")

    assert run.outcome is closure.Outcome.CONVERGED, run.reason
    assert run.is_closed_loop_success()
    assert run.exit_code() == 0
    assert run.baseline["value"] == 3, "the baseline violation was measured"
    assert run.final["value"] == 0
    assert run.promoted == 3 and run.rolled_back == 0

    for i, it in enumerate(run.iterations):
        assert it.actuator_rc == 0
        assert it.changed_implementation, (
            f"iteration {i} promoted without changing the implementation; an "
            f"actuator that changes nothing has not actuated")
        assert it.digest_after != it.digest_before
        assert it.decision == "PROMOTED"
        assert closure.State.ACTUATED.value in it.states
        assert closure.State.REMEASURED.value in it.states
        assert closure.State.PROMOTED.value in it.states
    assert (bench.impl / "violations.txt").read_text().strip() == "0", (
        "the CONVERGED implementation is the one left on disk")


def test_the_run_record_is_a_canonical_hashable_document(bench):
    reg, ctl = bench([_p(reduce_by=3)], start=3)
    rec = ctl.run_edge("20").to_record()
    assert rec["schema"] == closure.SCHEMA_RUN
    assert rec["digest"].startswith("sha256:")
    json.dumps(rec)                       # serialisable
    assert rec["registry_digest"].startswith("sha256:")
    assert rec["baseline_all"] and rec["final_all"]


def test_flow_steps_that_were_not_rerun_in_process_are_named(bench):
    """Stated, not implied. This process re-ran the MEASUREMENTS; naming the
    steps a full re-run would additionally execute is the difference between a
    limitation and a lie."""
    reg, ctl = bench([_p(reduce_by=3)], start=3)
    rec = ctl.run_edge("20").to_record()
    assert rec["flow_steps_not_rerun_in_process"] == ["19", "20"]


# --------------------------------------------------------------------------
# NEGATIVE: regression rolls back, and the rollback is PROVED.
# --------------------------------------------------------------------------

def test_a_collateral_regression_rolls_back_and_the_tree_is_restored(bench):
    """The objective IMPROVES and a re-measured neighbour gets worse. That is a
    rollback, not a promotion -- otherwise the loop moves a violation from one
    domain to another and reports progress."""
    reg, ctl = bench(
        [_p(reduce_by=1, collateral_target="other.txt", collateral_add=5)],
        start=3, collateral_start=0)
    before = (bench.impl / "violations.txt").read_text()
    run = ctl.run_edge("20")

    it = run.iterations[0]
    assert it.actuator_rc == 0
    assert it.changed_implementation, "the actuator really did change the tree"
    assert it.decision == "ROLLED_BACK", it.decision_reason
    assert "collateral regression" in it.decision_reason
    assert "fixture.other_violations" in it.decision_reason
    # The PROOF, and it is a fact rather than a hope: the tree digest after the
    # restore equals the digest from before the action.
    assert it.digest_restored == it.digest_before
    assert (bench.impl / "violations.txt").read_text() == before
    assert (bench.impl / "other.txt").read_text().strip() == "0"
    assert run.promoted == 0 and run.rolled_back == 1
    assert not run.is_closed_loop_success()
    assert run.exit_code() == 1


def test_a_neutral_iteration_also_rolls_back(bench):
    """Leaving a neutral implementation in place would make the NEXT iteration
    measure something nobody chose."""
    reg, ctl = bench([_p(reduce_by=0), _p(reduce_by=0)], start=2,
                     stop={"max_iterations": 6, "plateau_patience": 2,
                           "wall_seconds": 120})
    run = ctl.run_edge("20")
    assert all(it.decision == "ROLLED_BACK" for it in run.iterations)
    assert all(it.digest_restored == it.digest_before for it in run.iterations)
    assert run.outcome is closure.Outcome.PLATEAU
    assert run.exit_code() == 1


def test_a_converged_objective_with_a_collateral_regression_is_not_a_success(bench):
    """The final-sweep backstop, and why it is not redundant with the
    per-iteration guard.

    The per-iteration guard compares a domain against the last PROMOTED value,
    so it catches a regression that is visible the moment the action lands. It
    cannot catch one that becomes visible LATER — and in this problem domain
    that is the normal case, not an exotic one: a re-measurement reads a report
    a tool wrote, and the number a tool writes for a domain can move after the
    action that caused it (a later stage re-runs, a queued analysis lands). So
    the run also compares every domain's FINAL value against its BASELINE, and a
    domain that finished worse than it started revokes the success even when the
    objective converged.

    A loop that improved its own number and left a neighbour worse has not
    closed a loop, it has moved a violation.
    """
    reg, ctl = bench([_p(reduce_by=3)], start=3, collateral_start=0)
    real_measure = ctl.measure
    damaged = {"done": False}

    def measure_then_damage(domain, tag):
        # The regression becomes visible only in the FINAL sweep: the
        # per-iteration guard measured `other` as 0 and was right at the time.
        if tag == "final" and not damaged["done"]:
            damaged["done"] = True
            (bench.impl / "other.txt").write_text("4\n", encoding="utf-8")
        return real_measure(domain, tag)

    ctl.measure = measure_then_damage
    run = ctl.run_edge("20")

    assert run.outcome is closure.Outcome.CONVERGED, run.reason
    assert run.final["value"] == 0, "the objective really did converge"
    assert run.collateral, "the final sweep must notice the damaged neighbour"
    assert run.collateral[0]["metric"] == "fixture.other_violations"
    assert (run.collateral[0]["from"], run.collateral[0]["to"]) == (0.0, 4.0)
    # The two assertions the whole test exists for:
    assert run.is_closed_loop_success() is False, (
        "CONVERGED plus a collateral regression is not a closed-loop success")
    assert run.exit_code() == 1, (
        "and it is a finding about the design, not the 0 the objective alone "
        "would give")
    assert run.to_record()["collateral_regressions"], (
        "the regression is named in the record, not only in the exit code")


def test_a_clean_convergence_reports_no_collateral(bench):
    """The positive control for the test above: without the damage, the same
    fixture converges, reports no collateral, and exits 0. Without this, the
    assertion above could be passing because collateral is always non-empty."""
    reg, ctl = bench([_p(reduce_by=3)], start=3, collateral_start=0)
    run = ctl.run_edge("20")
    assert run.outcome is closure.Outcome.CONVERGED
    assert run.collateral == []
    assert run.is_closed_loop_success() and run.exit_code() == 0


# --------------------------------------------------------------------------
# STOP CONDITIONS
# --------------------------------------------------------------------------

def test_plateau_stops_the_loop_and_the_residual_stays_visible(bench):
    reg, ctl = bench([_p(reduce_by=0)] * 4, start=5,
                     stop={"max_iterations": 6, "plateau_patience": 2,
                           "wall_seconds": 120})
    run = ctl.run_edge("20")
    assert run.outcome is closure.Outcome.PLATEAU
    assert run.exit_code() == 1
    assert run.residual is not None
    assert run.residual["visible"] is True
    assert run.residual["satisfied"] is False
    assert run.residual["value"] == 5, (
        "the residual violation is reported with its NUMBER, not summarised away")


def test_the_budget_stops_the_loop_before_the_plan_is_exhausted(bench):
    reg, ctl = bench([_p(reduce_by=1)] * 6, start=6,
                     stop={"max_iterations": 2, "plateau_patience": 5,
                           "wall_seconds": 120})
    run = ctl.run_edge("20")
    assert run.outcome is closure.Outcome.BUDGET_EXHAUSTED
    assert run.exit_code() == 1
    assert len(run.iterations) == 2
    assert run.residual["value"] == 4


def test_the_actuators_own_invocation_ceiling_also_stops_the_loop(bench):
    reg, ctl = bench(
        [_p(reduce_by=1)] * 6, start=6,
        stop={"max_iterations": 6, "plateau_patience": 5, "wall_seconds": 120},
        extra_actuator={"resource_ceilings": {"wall_seconds": 30,
                                              "max_invocations_per_run": 3}})
    run = ctl.run_edge("20")
    assert run.outcome is closure.Outcome.BUDGET_EXHAUSTED
    assert "max_invocations_per_run" in run.reason
    assert len(run.iterations) == 3


def test_a_wall_clock_budget_stops_the_loop(bench):
    """The clock is injected, so the assertion is about the STOP RULE and not
    about how fast this host happens to be."""
    reg, ctl = bench([_p(reduce_by=1)] * 6, start=6,
                     stop={"max_iterations": 6, "plateau_patience": 5,
                           "wall_seconds": 10})
    ticks = iter([0, 5, 50, 60, 70, 80, 90])
    ctl._now = lambda: next(ticks)
    run = ctl.run_edge("20")
    assert run.outcome is closure.Outcome.BUDGET_EXHAUSTED
    assert "wall_seconds" in run.reason


def test_the_trigger_may_simply_not_fire(bench):
    """Green, and honestly so: nothing was wrong, so nothing was repaired. The
    record must not claim a repair that did not happen."""
    reg, ctl = bench([_p(reduce_by=1)], start=0)
    run = ctl.run_edge("20")
    assert run.outcome is closure.Outcome.NOT_TRIGGERED
    assert run.exit_code() == 0
    assert run.is_closed_loop_success()
    assert run.iterations == []
    assert run.promoted == 0


# --------------------------------------------------------------------------
# The actuator is bounded by NO-PROGRESS, never by a clock (vibe-ic#2051).
# --------------------------------------------------------------------------

def _timed_actuator(extra_params, extra_argv, wall_seconds):
    params = {"reduce_by": {"type": "integer", "unit": "count",
                            "minimum": 0, "maximum": 10},
              "target": {"type": "path"}}
    params.update(extra_params)
    return {"parameters": params,
            "resource_ceilings": {"wall_seconds": wall_seconds,
                                  "max_invocations_per_run": 8},
            "wrapper": {"program": "fixture_actuator",
                        "argv_template": ["--reduce-by", "{reduce_by}",
                                          "--target", "{target}", *extra_argv]}}


def test_a_working_actuator_past_its_budget_is_not_killed(bench):
    """U7 wave 57 (minor): `wall_seconds` is a recorded budget. An actuator
    still burning CPU past it (a late hold_first retry) finishes and is judged;
    the old raw `subprocess.run(timeout=)` killed it and orphaned its tool."""
    reg, ctl = bench([_p(reduce_by=3, busy_s=3)], start=3,
                     extra_actuator=_timed_actuator(
                         {"busy_s": {"type": "number", "unit": "s",
                                     "minimum": 0, "maximum": 10}},
                         ["--busy-s", "{busy_s}"], wall_seconds=1))
    run = ctl.run_edge("20")
    assert run.iterations[0].actuator_rc == 0
    assert run.iterations[0].decision == "PROMOTED"
    assert run.outcome is closure.Outcome.CONVERGED


def test_a_stalled_actuator_is_stopped_and_its_containers_removed(
        bench, tmp_path, monkeypatch):
    """A silent, idle actuator is stopped by the stall watchdog (rc
    RC_STALLED, ACTUATOR_FAILED, never a measured result), and every container
    it started -- named `<prefix>_<its pid>_<hex>` -- is removed by name."""
    import _watchdog as _wd
    pid_file = bench.impl / "actuator.pid"
    calls = tmp_path / "docker_calls.txt"
    minted = tmp_path / "minted.txt"
    fake = tmp_path / "fake_docker"
    fake.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        echo "$@" >> {calls}
        if [ "$1" = ps ]; then
          [ -s {pid_file} ] && echo "vibeic_ll_$(cat {pid_file})_1a2b" | tee {minted}
          echo "vibeic_ll_1_ffff"
        fi
        exit 0
        """))
    fake.chmod(0o755)
    monkeypatch.setattr(closure, "_DOCKER", str(fake))
    monkeypatch.setattr(closure, "ACTUATOR_STALL_GRACE_S", 2.0)
    reg, ctl = bench([_p(reduce_by=1, hang_pid_file="actuator.pid")], start=3,
                     extra_actuator=_timed_actuator(
                         {"hang_pid_file": {"type": "path"}},
                         ["--hang-pid-file", "{hang_pid_file}"],
                         wall_seconds=14400))
    run = ctl.run_edge("20")
    it = run.iterations[0]
    assert it.actuator_rc == _wd.RC_STALLED
    assert it.decision == "ACTUATOR_FAILED"
    assert run.outcome is closure.Outcome.NOT_MEASURED
    assert "stall watchdog" in it.actuator_note
    # (the rollback restores the implementation root, pid file included, so
    # the actuator's own container name is read from what `ps` reported)
    own = minted.read_text().strip()
    assert own.startswith("vibeic_ll_") and own != "vibeic_ll_1_ffff"
    removed = [line for line in calls.read_text().splitlines()
               if line.startswith("rm -f")]
    assert removed == [f"rm -f {own}"], (
        "only the stalled actuator's own container is removed")


def test_owned_containers_match_the_minting_pid_only():
    names = ["vibeic_ll_1234_ab", "vibeic_ll_51234_cd", "x_1234_",
             "vibeic_ll_1234_ab_extra", "other_77_0f"]
    assert closure.owned_containers({1234, 77}, names) == [
        "vibeic_ll_1234_ab", "other_77_0f"]


# --------------------------------------------------------------------------
# The actuator's progress reading across container turnover (U7 follow-up).
#
# A LibreLane actuator runs one container per step, and a container's
# processes leave /proc when it exits. `ProgressMeter` counts CPU only above
# the highest total it has seen. Summing only the containers running NOW, the
# total falls when the big repair step exits, and the smaller steps after it
# never climb back over that peak: past the 1800 s grace a HEALTHY actuator
# is killed as stalled (RC_STALLED -> ACTUATOR_FAILED).
#
# REAL: `run_actuator` (its probe, its reaper, its note), `_watchdog`'s
# `run_host_supervised` / `run_supervised`, the `ProgressMeter` that fuses the
# reading, and the `supervise` loop that decides a stall, at the shipped
# default grace. SCRIPTED, because each is a boundary this file cannot own:
# the actuator process (a stand-in whose pid no Linux can hand out, so the
# reaper's group kill can never reach a real process), the clock (virtual
# seconds), `docker` (a real executable reading the scripted world, so
# `docker ps` / `docker inspect` / `docker rm` go through the shipped CLI
# glue), and /proc (`host_tree_progress`, keyed by pid).
# --------------------------------------------------------------------------

def _unreal_pid(k: int) -> int:
    """A pid above PID_MAX_LIMIT (2**22) and above this host's pid_max."""
    try:
        top = int(pathlib.Path("/proc/sys/kernel/pid_max").read_text())
    except (OSError, ValueError):
        top = 0
    return max(top, 1 << 22) + 1000 + k


class _Container:
    """One step container: alive on [start, end); its tree's cumulative CPU
    is `base + rate * (t - start)` while it is alive, and nothing after."""

    def __init__(self, k, start, end, rate, base=0.0):
        self.pid = _unreal_pid(1 + k)
        self.start, self.end, self.rate, self.base = start, end, rate, base

    def cpu(self, t):
        return self.base + self.rate * (t - self.start)


class _World:
    """Virtual seconds, and what /proc and `docker` say at each of them."""

    HORIZON_S = 40_000.0

    def __init__(self, tmp_path, *, containers, exit_at, blind=None):
        self.t = 0.0
        self.act_pid = _unreal_pid(0)
        self.containers = {f"vibeic_ll_{self.act_pid}_{k:04x}": c
                           for k, c in enumerate(containers)}
        self.exit_at = exit_at
        self.blind = blind or (lambda container, t: False)
        self.killed_at = None
        self.readings = []
        self.state = tmp_path / "docker_state"
        self.state.mkdir()
        self.calls = tmp_path / "docker_calls.txt"
        self.calls.write_text("")
        self.docker = tmp_path / "fake_docker"
        self.docker.write_text(textwrap.dedent(f"""\
            #!/bin/sh
            echo "$@" >> {self.calls}
            for last; do :; done
            case "$1" in
              ps) cat {self.state}/ps ;;
              inspect) cat "{self.state}/pid_$last" 2>/dev/null || exit 1 ;;
            esac
            exit 0
            """))
        self.docker.chmod(0o755)
        self._publish()

    def alive(self):
        return {name: c for name, c in self.containers.items()
                if c.start <= self.t < c.end}

    def _publish(self):
        alive = self.alive()
        (self.state / "ps").write_text("".join(f"{n}\n" for n in alive))
        for name, c in self.containers.items():
            pid_file = self.state / f"pid_{name}"
            if name in alive and not self.blind(c, self.t):
                pid_file.write_text(f"{c.pid}\n")
            elif pid_file.exists():
                pid_file.unlink()

    def host_tree_progress(self, pid):
        """/proc: the actuator's own tree is its python (1.0 CPU-s, idle
        while its steps run) plus one `docker run` client per live container,
        which spends a little relaying what its container does (nothing, for
        an idle one) and leaves /proc with it; a container's init pid reads
        its tree while it is alive."""
        if pid == self.act_pid:
            if self.rc() is not None:
                return None
            return 1.0 + sum(0.001 * c.cpu(self.t)
                             for c in self.alive().values())
        for c in self.alive().values():
            if c.pid == pid:
                return c.cpu(self.t)
        return None

    def rc(self):
        if self.killed_at is not None:
            return -9
        if self.exit_at is not None and self.t >= self.exit_at:
            return 0
        return None

    def clock(self):
        return self.t

    def wait(self, proc, timeout):
        if self.rc() is not None:
            return self.rc()
        step = (timeout if self.exit_at is None
                else min(timeout, self.exit_at - self.t))
        self.t += step
        assert self.t < self.HORIZON_S, (
            f"the watchdog never stopped an actuator that stopped moving "
            f"(virtual t={self.t:g} s)")
        self._publish()
        return self.rc()

    def popen(self, cmd, **kw):
        world = self

        class _Proc:
            pid = world.act_pid

            def poll(self):
                return world.rc()

            def wait(self, timeout=None):
                return world.rc()

            def kill(self):
                if world.killed_at is None:
                    world.killed_at = world.t
        return _Proc()


def _run_in(world, tmp_path, monkeypatch):
    import _watchdog as _wd
    real = _wd.run_host_supervised

    def virtual(cmd, **kw):
        probe = kw["cpu_probe"]

        def recorded(proc):
            value = probe(proc)
            world.readings.append((world.t, value))
            return value
        kw["cpu_probe"] = recorded
        return real(cmd, popen_factory=world.popen, wait_fn=world.wait,
                    clock=world.clock, **kw)
    monkeypatch.setattr(_wd, "run_host_supervised", virtual)
    monkeypatch.setattr(_wd, "host_tree_progress", world.host_tree_progress)
    monkeypatch.setattr(closure, "_DOCKER", str(world.docker))
    monkeypatch.setattr(closure, "ACTUATOR_STALL_GRACE_S", None)
    return closure.run_actuator(["the-actuator"], cwd=tmp_path,
                                budget_s=14400)


def test_a_healthy_actuator_is_not_killed_when_smaller_steps_follow_a_big_one(
        tmp_path, monkeypatch):
    """PostRouteRepair (1200 CPU-s) exits; STAPostPNR and RCX each end below
    that peak and together run 4600 s, far past the 1800 s grace. The
    actuator is working the whole time and must be let finish (rc 0), and its
    progress reading must never fall at a container boundary."""
    import _watchdog as _wd
    repair, sta, rcx = (_Container(0, 0, 1200, 1.0),
                        _Container(1, 1200, 3600, 0.3),
                        _Container(2, 3600, 5800, 0.4))
    # The fixture IS the reviewed shape: every later step ends below the
    # repair step's peak, and they outlast the grace.
    peak = repair.cpu(repair.end)
    assert all(c.cpu(c.end) < peak for c in (sta, rcx))
    assert rcx.end - repair.end > _wd.DEFAULT_STALL_GRACE_S
    world = _World(tmp_path, containers=[repair, sta, rcx], exit_at=6000)
    rc, note = _run_in(world, tmp_path, monkeypatch)
    assert rc == 0, (rc, note)
    assert world.killed_at is None
    assert "stall watchdog" not in note
    assert not [line for line in world.calls.read_text().splitlines()
                if line.startswith("rm ")], "nothing was reaped"
    looks = [(t, v) for t, v in world.readings if v is not None]
    assert len(looks) > 100 and looks[-1][0] > rcx.start, (
        "the watchdog really looked, all the way")
    falls = [(t, a, b) for (_, a), (t, b) in zip(looks, looks[1:]) if b < a]
    assert not falls, f"the progress reading fell: (t, from, to) {falls[:3]}"


def test_an_actuator_that_stops_moving_after_a_big_step_is_still_stopped(
        tmp_path, monkeypatch):
    """The repair step exits; the next container stays alive and idle, and
    its `docker inspect` fails on every other look. Nothing moves, so the
    watchdog stops the actuator once the grace has passed -- not before --
    and removes the idle container it started, and only that one."""
    import _watchdog as _wd
    repair, hung = (_Container(0, 0, 1200, 1.0),
                    _Container(1, 1200, float("inf"), 0.0, base=3.0))
    world = _World(tmp_path, containers=[repair, hung], exit_at=None,
                   blind=lambda c, t: c is hung and int(t // 30) % 2 == 0)
    hung_name = next(n for n, c in world.containers.items() if c is hung)
    rc, note = _run_in(world, tmp_path, monkeypatch)
    assert rc == _wd.RC_STALLED, (rc, note)
    assert "stall watchdog" in note
    assert world.killed_at is not None
    # when the reading last went past everything it had shown (what the
    # meter counts as progress)
    best, last_move = None, 0.0
    for t, v in world.readings:
        if v is not None and (best is None or v > best):
            last_move, best = (t if best is not None else 0.0), v
    assert world.killed_at - last_move > _wd.DEFAULT_STALL_GRACE_S
    assert world.killed_at <= last_move + _wd.DEFAULT_STALL_GRACE_S + 2 * 30
    removed = [line for line in world.calls.read_text().splitlines()
               if line.startswith("rm ")]
    assert removed == [f"rm -f {hung_name}"], removed


# --------------------------------------------------------------------------
# HANDOFF: the controller says it cannot, instead of pretending it did.
# --------------------------------------------------------------------------

def test_an_actuator_nonzero_is_unmeasured_not_a_design_handoff(bench):
    reg, ctl = bench([_p(reduce_by=1, refuse=True)], start=3,
                     extra_actuator={"parameters": {
                         "reduce_by": {"type": "integer", "unit": "count",
                                       "minimum": 0, "maximum": 10},
                         "target": {"type": "path"},
                         "refuse": {"type": "boolean", "required": False},
                     }, "wrapper": {"program": "fixture_actuator",
                                    "argv_template": ["--reduce-by", "{reduce_by}",
                                                      "--target", "{target}",
                                                      "--refuse"]}})
    run = ctl.run_edge("20")
    assert run.outcome is closure.Outcome.NOT_MEASURED
    assert run.exit_code() == 2
    assert not run.is_closed_loop_success()
    it = run.iterations[0]
    assert it.actuator_rc == 1
    assert it.decision == "ACTUATOR_FAILED" and it.measurements == {}
    assert run.rolled_back == 0 and run.promoted == 0
    assert it.digest_restored == it.digest_before, (
        "whatever a refusing actuator left behind is rolled back, so the next "
        "reader sees the baseline and not a half-applied action")
    assert run.residual["visible"] is True


def test_an_unmet_precondition_is_a_handoff_and_nothing_is_actuated(bench):
    reg, ctl = bench([_p(reduce_by=1, target="absent.txt")], start=3,
                     extra_actuator={"parameters": {
                         "reduce_by": {"type": "integer", "unit": "count",
                                       "minimum": 0, "maximum": 10},
                         "target": {"type": "path"},
                     }})
    run = ctl.run_edge("20")
    assert run.outcome is closure.Outcome.HANDOFF_REQUIRED
    assert "file_exists" in run.reason
    assert run.iterations[0].actuator_rc is None, (
        "the actuator was never invoked, so there is no exit code to report")
    assert run.iterations[0].decision == "REFUSED"


def test_a_proposal_outside_the_declared_bounds_is_a_handoff(bench):
    """Refused at LOAD when the plan is static; refused at RUN when it is not.
    Either way it is never silently clamped into an action nobody authorised."""
    reg, ctl = bench([_p(reduce_by=1)], start=3)
    act = reg.actuators["fix.reduce"]
    with pytest.raises(closure.ParameterError):
        act.bind_params({"reduce_by": 99, "target": "violations.txt"})


# --------------------------------------------------------------------------
# DECLARED_ONLY: the honesty requirement.
# --------------------------------------------------------------------------

def test_an_unbound_edge_is_declared_only_and_never_a_success(bench):
    reg, ctl = bench([_p(reduce_by=1)], start=3)
    run = ctl.run_edge("24")
    assert run.outcome is closure.Outcome.DECLARED_ONLY
    assert run.exit_code() == 2, "NOT CHECKED, never 0 and never 1"
    assert run.is_closed_loop_success() is False
    assert run.outcome.marker() == "[CANNOT CHECK]"
    assert run.iterations == []
    assert run.to_record()["closed_loop_success"] is False


def test_there_is_no_flag_that_turns_declared_only_into_a_success():
    """Asserted on the enum itself: a reporting layer cannot get this wrong by
    passing an option, because there is no option."""
    for outcome in closure.Outcome:
        if outcome in (closure.Outcome.NOT_TRIGGERED, closure.Outcome.CONVERGED):
            assert outcome.is_success()
        else:
            assert not outcome.is_success(), outcome


# --------------------------------------------------------------------------
# VACUOUS: missing input must not be 0 and must not be 1.
# --------------------------------------------------------------------------

def test_a_baseline_that_cannot_be_measured_is_not_measured_not_clean(bench):
    """"I could not read it" and "I read it and it was clean" must never produce
    the same verdict."""
    reg, ctl = bench([_p(reduce_by=1)], start=3)
    (bench.impl / "violations.txt").unlink()
    run = ctl.run_edge("20")
    assert run.outcome is closure.Outcome.NOT_MEASURED
    assert run.exit_code() == 2
    assert not run.is_closed_loop_success()
    assert run.iterations == [], "nothing was actuated over an unmeasured baseline"
    assert "value" not in run.baseline, (
        "a NOT_MEASURED record carries a reason, never a numeric sentinel")
    assert run.baseline["status"] == "NOT_MEASURED"
    assert run.baseline["reason"]


def test_a_measurement_refusal_after_actuation_rolls_the_change_back(bench):
    """If the effect of an action cannot be measured, the action is undone: an
    unmeasurable change is a change nobody can defend."""
    reg, ctl = bench([_p(reduce_by=1)], start=3,
                     extra_actuator={"wrapper": {
                         "program": "fixture_actuator",
                         "argv_template": ["--reduce-by", "{reduce_by}",
                                           "--target", "{target}"]}})
    real_measure = ctl.measure
    calls = {"n": 0}

    def flaky(domain, tag):
        calls["n"] += 1
        if tag.startswith("iter"):
            (bench.impl / "violations.txt").unlink(missing_ok=True)
        return real_measure(domain, tag)

    ctl.measure = flaky
    run = ctl.run_edge("20")
    assert run.outcome is closure.Outcome.NOT_MEASURED
    assert run.exit_code() == 2
    assert run.iterations[0].decision == "ROLLED_BACK"
    assert run.iterations[0].digest_restored == run.iterations[0].digest_before


def test_a_metric_over_an_empty_denominator_is_not_measured_not_one():
    """A fraction over a zero denominator is undefined. Reporting 1.0 for "all
    of nothing is true" is the empty-corpus green, one layer down."""
    ex = closure.Extractor(kind="bool_fraction", pointer="/findings", flag="present")
    for doc in ({"findings": []}, {}, {"findings": None}):
        value, formula = ex.extract(doc)
        assert value is None, (doc, formula)
    value, _ = ex.extract({"findings": [{"present": True}, {"present": False}]})
    assert value == 0.5


def test_an_absent_pointer_is_not_measured_not_zero():
    ex = closure.Extractor(kind="json_pointer", pointer="/summary/nope")
    assert ex.extract({"summary": {}})[0] is None
    assert ex.extract({"summary": {"nope": 0}})[0] == 0.0, (
        "a real 0 is still a real 0 -- the distinction is presence, not value")


# --------------------------------------------------------------------------
# The tree digest, which every rollback assertion above rests on.
# --------------------------------------------------------------------------

def test_the_tree_digest_notices_what_a_restore_must_restore(tmp_path):
    root = tmp_path / "t"
    (root / "d").mkdir(parents=True)
    (root / "d" / "a.txt").write_text("one", encoding="utf-8")
    first = closure.tree_digest(root)
    assert first == closure.tree_digest(root), "stable over re-reads"

    (root / "d" / "a.txt").write_text("two", encoding="utf-8")
    assert closure.tree_digest(root) != first, "content"
    (root / "d" / "a.txt").write_text("one", encoding="utf-8")
    assert closure.tree_digest(root) == first

    (root / "d" / "b.txt").write_text("", encoding="utf-8")
    assert closure.tree_digest(root) != first, "an added empty file is a change"
    (root / "d" / "b.txt").unlink()
    assert closure.tree_digest(root) == first


def test_the_tree_digest_does_not_follow_a_symlink_into_equality(tmp_path):
    """A restored tree that swapped a file for a link to it is a DIFFERENT tree
    and must say so; following the link would report them identical."""
    root = tmp_path / "t"
    root.mkdir()
    (root / "a.txt").write_text("one", encoding="utf-8")
    (root / "b.txt").write_text("one", encoding="utf-8")
    with_files = closure.tree_digest(root)
    (root / "b.txt").unlink()
    (root / "b.txt").symlink_to(root / "a.txt")
    assert closure.tree_digest(root) != with_files


def test_the_metric_scope_carries_no_absolute_path(bench):
    """docs/PPA_INTERFACES.md §2: two numbers are comparable only if their SCOPE
    matches. WHERE the measurement ran is provenance and lives in `source`; if
    it lived in `scope`, the same measurement of the same design would become
    incomparable with itself the moment the tree moved."""
    reg, ctl = bench([_p(reduce_by=3)], start=3)
    rec = ctl.run_edge("20").to_record()
    for name, m in list(rec["baseline_all"].items()) + list(rec["final_all"].items()):
        flat = json.dumps(m["scope"])
        assert "/" not in flat, (name, flat)
        assert str(bench.impl) not in flat
        assert m["source"]["implementation_root"] == str(bench.impl.resolve()), (
            "and it IS recorded, in source, where provenance belongs")
    assert rec["baseline_all"]["fix.primary"]["scope"] == {
        "stage": "closure_loop", "at": "baseline"}
