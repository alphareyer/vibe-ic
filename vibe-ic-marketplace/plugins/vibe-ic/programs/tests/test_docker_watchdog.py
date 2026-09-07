"""tests/test_docker_watchdog.py — the SHARED docker glue that routes a long
in-container tool run through the general `_watchdog` primitive (v1.3.48).

Covers the docker-specific pieces INJECTED into the general supervisor:
  • parse_cputime_hms          — ps cputime token → seconds.
  • container_cpu_seconds      — marker-matched CPU sum via an injected raw exec
                                 (cputimes fast-path + cputime hms fallback +
                                 None when unavailable).
  • run_docker_supervised      — builds the SUPERVISED in-container command
                                 (identity stamp then `exec`, NO outer clock)
                                 + host/container argv, threads cpu_probe/
                                 kill/ceiling_notice into run_supervised,
                                 propagates (rc,out,err); the kill callback
                                 reaps BY IDENTITY (the stamped pid + /proc
                                 starttime) via the raw exec.

The dispatch assertion below used to read
``"timeout --kill-after=5" in captured["cmd"][-1]`` — it pinned a contract
vibe-ic#2051 had already removed. That landing took the GNU `timeout` off the
supervised path (a still-converging proof was SIGKILLed at the budget) but did
not reach this file, so main carried the red from 2026-09-07 (vibe-ic#2097).
The assertion is now the OPPOSITE and stricter: no clock reaches the tool at
all, the dispatched string IS what `supervised_container_command` builds, the
tool is the direct target of `exec`, and the ceiling the dispatch threads in is
a NOTICE that reaps nothing.

The kill assertion below used to read `any("pkill" in c and marker in c)` —
it asserted the defect. A marker is a path in the tool's argv, so that reap
matched ANY process carrying it, and on the single shared long-lived
`vibeic-eda` container one run SIGTERMed another run's healthy tool (rc=143,
zero test failures, 2026-08-27). The assertion is now the opposite AND
stronger: the reap must still fire, must carry the stamp, and must NOT carry
the marker.
No real docker: the raw exec and run_supervised are injected fakes.
"""
import shlex
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import _docker_watchdog as DW  # noqa: E402
import _watchdog as W  # noqa: E402

# A stamp path pinned for the dispatch assertions; the real one carries a
# fresh nonce (`new_job_pidfile`), so it is pinned rather than predicted.
PIDFILE = "/tmp/.vibeic-job-0123456789abcdef.pid"


def test_parse_cputime_hms():
    assert DW.parse_cputime_hms("01:02:03") == 3723
    assert DW.parse_cputime_hms("1-00:00:00") == 86400
    assert DW.parse_cputime_hms("05:30") == 330
    assert DW.parse_cputime_hms("junk") is None
    assert DW.parse_cputime_hms("") is None


def test_container_cpu_seconds_sums_marker_matches():
    # 4-column rows (pid ppid cpu args) — process-TREE accounting.
    marker = "/foss/x/pnr.tcl"
    ps = ("10 1 123 openroad -exit /foss/x/pnr.tcl\n"
          "11 1   4 bash -lc openroad -exit /foss/x/pnr.tcl\n"
          "12 1  99 klayout -b -r /other/deck\n")

    def raw(c, cmd, timeout=15):
        return (0, ps, "") if "cputimes=" in cmd else (1, "", "")

    assert DW.container_cpu_seconds("c", marker, raw) == 127.0


def test_container_cpu_seconds_counts_marked_trees_descendants():
    # The load-bearing case: yosys runs ABC in a child `yosys-abc` whose argv
    # does NOT carry the marker. Argv-only accounting reported zero progress
    # during ABC's long quiet phase and the stall watchdog killed a healthy
    # 1.8M-cell synth; tree accounting must count the descendant.
    marker = "/proj/netlist.v"
    ps = ("100 1    5 yosys -p write_verilog /proj/netlist.v\n"
          "200 100 3600 /foss/tools/yosys/bin/yosys-abc -s\n"
          "300 200  10 abc-helper\n"
          "400 1   999 klayout -b -r /other/deck\n")

    def raw(c, cmd, timeout=15):
        return (0, ps, "") if "cputimes=" in cmd else (1, "", "")

    assert DW.container_cpu_seconds("c", marker, raw) == 3615.0


def test_container_cpu_seconds_none_without_marker():
    assert DW.container_cpu_seconds("c", None, lambda *a, **k: (0, "", "")) is None


def test_container_cpu_seconds_none_when_no_match():
    def raw(c, cmd, timeout=15):
        return (0, "5 1 1 openroad -exit /other.tcl\n", "")
    assert DW.container_cpu_seconds("c", "/not/here.tcl", raw) is None


def test_container_cpu_seconds_hms_fallback():
    def raw(c, cmd, timeout=15):
        if "cputimes=" in cmd:
            return (1, "", "")     # unsupported → empty
        return (0, "7 1 00:02:00 netgen -batch lvs netlist.spice top\n", "")
    assert DW.container_cpu_seconds("c", "netlist.spice", raw) == 120.0


def test_run_docker_supervised_threads_callbacks_and_wraps(monkeypatch):
    captured = {}

    def fake_supervised(cmd, **kw):
        captured["cmd"] = cmd
        captured["kw"] = kw
        return W.SupervisedResult(0, "out", "err", "natural", 1.0)

    monkeypatch.setattr(W, "run_supervised", fake_supervised)
    # Pin the per-invocation stamp so the DISPATCHED string can be compared
    # against its producer byte for byte rather than pattern-matched.
    monkeypatch.setattr(DW, "new_job_pidfile", lambda: PIDFILE)
    raw_calls = []

    def raw(c, cmd, timeout=15):
        raw_calls.append(cmd)
        return (0, "", "")

    tool = "sta -no_init -exit /p/x.tcl"
    rc, out, err = DW.run_docker_supervised(
        "cont", tool, "/p/x.tcl",
        docker_exec_raw=raw, stall_grace_s=1234.0, hard_ceiling_s=4321.0)
    assert (rc, out, err) == (0, "out", "err")
    # CONTAINER ARGV, PINNED SO OPTS CANNOT BREAK IT. This read
    # `captured["cmd"][:3] == ["docker", "exec", "cont"]`, which assumes the
    # dispatch passes NO `opts` — and `_container_exec.docker_exec_argv` builds
    # `["docker", "exec", *opts, container, *rest]`, so the first `-e`/`-w` any
    # caller adds moves the container off index 2. vibe-ic#2105 is exactly that
    # landing: it must put `-e IIC_OSIC_TOOLS_QUIET=1` back on this shared path
    # (the flag was lost when phase3's private dispatch moved here), and `opts=`
    # is the only sanctioned way to add one. Verified by building both argvs.
    # The container is therefore named by its position relative to the TAIL,
    # which `docker_exec_argv` fixes at `bash -lc <wrapped>`, and the verb is
    # asserted separately — both hold with and without opts.
    assert captured["cmd"][:2] == ["docker", "exec"], captured["cmd"]
    assert captured["cmd"][-4:-2] == ["cont", "bash"], captured["cmd"]

    # NO OUTER CLOCK ON THE DISPATCHED COMMAND (vibe-ic#2051).
    # `test_docker_exec_timeout_orphan.py` holds the same property on the
    # PRODUCER (`supervised_container_command`) and on the source of
    # `run_docker_supervised`; this is the complementary end-to-end look — the
    # argv `run_docker_supervised` actually hands to the supervisor, which is
    # the only place a re-wrap applied AFTER the producer would show up.
    inner = captured["cmd"][-1]
    assert "timeout" not in inner, inner
    assert "--kill-after" not in inner, inner
    # the tool is the DIRECT target of `exec`: nothing is interposed in front
    # of it, so the stamped pid is the tool's own pid.
    assert inner.rstrip().endswith("exec bash -lc " + shlex.quote(tool)), inner
    # ...and the stamp the reap selects on is what is there instead of a clock.
    assert PIDFILE in inner, inner
    assert "/proc/$1/stat" in inner, inner
    # the dispatch goes through the supervised producer, not a second spelling
    assert inner == DW.supervised_container_command(tool, PIDFILE), inner

    # NEITHER WINDOW MAY BE A DEFAULT. This line read `== 1800`, which is
    # exactly `DEFAULT_STALL_GRACE_S`, so it passed even when the caller's
    # value was dropped on the floor — measured: with both windows replaced by
    # their defaults inside `run_docker_supervised`, all 21 tests over this
    # file and `test_v1_3_47_stall_watchdog.py` still passed (vibe-ic#2097).
    # The values below are deliberately not the defaults.
    assert captured["kw"]["stall_grace_s"] == 1234.0
    assert DW.DEFAULT_STALL_GRACE_S != 1234.0, "the window went back to a default"
    # the ceiling is still THREADED — as a recorded budget, not a deadline.
    assert captured["kw"]["hard_ceiling_s"] == 4321.0
    assert DW.DEFAULT_HARD_CEILING_S != 4321.0, "the budget went back to a default"
    assert callable(captured["kw"]["cpu_probe"])
    assert callable(captured["kw"]["kill"])
    assert callable(captured["kw"]["ceiling_notice"])

    # THE CEILING CROSSING IS RECORDED, NEVER A KILL. Firing the notice the
    # dispatch threaded in must issue no reap through the raw exec: at this
    # seam the budget cannot terminate anything. (What the crossing RECORDS,
    # driven through the real supervisor, is
    # `test_issue2051_the_ceiling_is_a_record_not_a_kill.py`.)
    before = len(raw_calls)
    captured["kw"]["ceiling_notice"](99999.0)
    assert not [c for c in raw_calls[before:] if "VIBEIC_REAP" in c], raw_calls

    # cpu_probe delegates to the injected raw exec (ps)
    captured["kw"]["cpu_probe"](object())
    assert any("cputime" in c for c in raw_calls)
    # kill callback reaps BY IDENTITY via the raw exec — never by pattern.
    class _P:
        def kill(self):
            self.killed = True
    p = _P()
    captured["kw"]["kill"](p, "stalled")
    reaps = [c for c in raw_calls if "VIBEIC_REAP" in c]
    # the recovery the reaper exists for still fires, and still escalates
    assert reaps, "the kill callback issued no reap at all"
    assert any("kill -TERM" in c for c in reaps)
    assert any("kill -KILL" in c for c in reaps)
    # but it never selects a victim by matching a command line
    assert not any("pkill" in c or "killall" in c for c in raw_calls)
    assert not any("/p/x.tcl" in c for c in reaps), (
        "the reap must not carry the tool's argv marker — that marker is "
        "what matched a stranger's process")
    # it selects the stamped identity and re-validates starttime first
    assert all(".vibeic-job-" in c for c in reaps)
    assert all("/proc/$1/stat" in c for c in reaps)
    assert all('[ "$VCUR" = "$VST" ]' in c for c in reaps)


def test_run_docker_supervised_host_argv(monkeypatch):
    captured = {}

    def fake_supervised(cmd, **kw):
        captured["cmd"] = cmd
        return W.SupervisedResult(0, "", "", "natural", 0.0)

    monkeypatch.setattr(W, "run_supervised", fake_supervised)
    DW.run_docker_supervised("host", "magic x.tcl", "x.tcl",
                             docker_exec_raw=lambda *a, **k: (0, "", ""))
    assert captured["cmd"][0] == "bash"       # host → no docker exec prefix


def test_run_docker_supervised_propagates_stall(monkeypatch):
    monkeypatch.setattr(
        W, "run_supervised",
        lambda cmd, **kw: W.SupervisedResult(
            W.RC_STALLED, "partial", "WATCHDOG_STALLED", "stalled", 9.0))
    rc, out, err = DW.run_docker_supervised(
        "c", "yosys -s x.ys", "x.ys", docker_exec_raw=lambda *a, **k: (0, "", ""))
    assert rc == W.RC_STALLED
    assert "WATCHDOG_STALLED" in err
