#!/usr/bin/env python3
"""llv1 W16a ratchet 1: every container the LibreLane path starts is bounded
in memory AND in how it may be stopped.

The v1 contract (COMMON.md): every docker run carries a memory ceiling, and
every tool run is bounded. W15 made the ceiling a whole-repo guard
(`test_container_memory_ceiling.py`); on main 7fac744e1 none of the ten
`docker run` sites in the two landed LibreLane modules below was bounded at
all. A LibreLane step that hangs hangs the flow, and a client that gives up on
`docker run` leaves the container running.

HOW a run is bounded is the owner's ruling vibe-ic#2051/R4: no tool is ever
stopped on a clock; only a job that STOPPED making progress is killed. So there
are two bounds, and each call to `run_container` states which one it takes:
  * ``probe_deadline_s=PROBE_DEADLINE_S`` -- a probe (seconds of work): a
    short hard client deadline, and the container removed by name past it;
  * ``supervised=True`` -- a tool step: the repo's progress-stall watchdog
    (`_watchdog.run_host_supervised`; container CPU and tool output are the
    progress signals); the reap by name is the only stop, and the 24 h
    budget kills nothing.

The rule, per site, read from the AST (not by name, not by a text window):
  * the argv LIST LITERAL that starts `[<docker>, 'run', ...]` splices
    `*_dmem.docker_memory_flags()` (the ceiling), and
  * that list reaches `run_container(...)` -- directly, or through the one
    local name it is bound to in the same function;
  * every `run_container` call states exactly one bound, a probe states
    `PROBE_DEADLINE_S`, and the functions that run TOOLS are supervised.

Scope: the LibreLane modules below. `librelane_image_facts.py` (W22) runs
only probes and bounds them with its own deadline, name and reap.
"""
from __future__ import annotations

import ast
from collections import Counter
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

RATCHETED = ("librelane_contract.py", "librelane_signoff.py")
BOUNDED_RUNNER = "run_container"
#: The functions that run a TOOL (a LibreLane step, an OpenROAD session, an
#: STA run) rather than a probe. Each must be supervised, never on a clock.
#: The functions that run a PROBE (a short hard client deadline). With
#: TOOL_STEPS this is the MEMBER list: the functions that call run_container
#: must be exactly these, so a site that stops calling it (a respelled argv
#: run through subprocess.run) is named, not silently dropped from the count.
PROBES = {"librelane_contract.py": {"image_capability", "resolve_step_configs", "emit_pdn_cfg",
                                    "flow_segment", "resolve_step_config"},
          "librelane_signoff.py": set()}
TOOL_STEPS = {"librelane_contract.py": {"run_chain", "_openroad_convert"},
              "librelane_signoff.py": {"agreement", "run_sta_script"}}
# A respelled argv can evade every literal docker-run audit. Pin EACH call,
# including its method, argv expression and deadline/forwarded supervision.
# A set of owner names lost a second call inside an already pinned function.
# Counter retains multiplicity if two calls have the same shape.
SUBPROCESS_EDGES = {
    "librelane_contract.py": Counter({
        ("_reap", "run", "[binary, 'rm', '-f', name]", "_REAP_DEADLINE_S", False): 1,
        ("_Client.__init__.work", "run", "cmd", "", True): 1,
        ("run_container", "run", "named", "probe_deadline_s", True): 1,
        ("run_container.rebound", "run",
         "[binary, *cmd[1:]] if cmd and cmd[0] == 'docker' else cmd", "", True): 1,
        ("run_container.cpu_probe", "run",
         "[binary, 'inspect', '-f', '{{.State.Pid}}', name]", "_REAP_DEADLINE_S", False): 1,
        ("image_pdk_root._inspect", "run",
         "[docker, 'image', 'inspect', '--format', '{{json .Id}} {{json .Config.Env}}', ref]",
         "IMAGE_INSPECT_DEADLINE_S", False): 1,
        ("_materialise_image_pdk", "run",
         "[docker, 'create', *_dmem.docker_memory_flags(), '--network', 'none', "
         "'--entrypoint', 'true', found['image_id']]", "", False): 1,
        ("_materialise_image_pdk", "run",
         "[docker, 'cp', '-L', f'{created.stdout.strip()}:{guest}', str(scratch / pdk)]",
         "", False): 1,
        ("_materialise_image_pdk", "run",
         "[docker, 'rm', '-f', created.stdout.strip()]", "", False): 1,
        ("_docker_lines", "run", "[docker, *argv]", "DOCKER_METADATA_DEADLINE_S", False): 1,
    }),
    "librelane_signoff.py": Counter(),
}


def _is_docker_head(node: ast.AST) -> bool:
    """The binary spellings the W15 guard calibrated: the literal, `docker`,
    `docker_bin`, and a conditional expression starting with one of them."""
    if isinstance(node, ast.IfExp):
        return _is_docker_head(node.body)
    if isinstance(node, ast.Constant):
        return node.value == "docker"
    return isinstance(node, ast.Name) and node.id in ("docker", "docker_bin")


def _is_run_argv(node: ast.AST) -> bool:
    return (isinstance(node, ast.List) and len(node.elts) >= 2
            and _is_docker_head(node.elts[0])
            and isinstance(node.elts[1], ast.Constant) and node.elts[1].value == "run")


def _splices_ceiling(node: ast.List) -> bool:
    return any(isinstance(e, ast.Starred) and isinstance(e.value, ast.Call)
               and getattr(e.value.func, "attr", getattr(e.value.func, "id", "")) == "docker_memory_flags"
               for e in node.elts)


def _call_name(call: ast.Call) -> str:
    return getattr(call.func, "attr", getattr(call.func, "id", ""))


def audit(source: str) -> list[tuple[int, str]]:
    """(line, what is missing) for every docker-run argv literal in ``source``."""
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    problems = []
    for node in ast.walk(tree):
        if not _is_run_argv(node):
            continue
        missing = []
        if not _splices_ceiling(node):
            missing.append("memory ceiling")
        up = parents.get(node)
        bounded = isinstance(up, ast.Call) and _call_name(up) == BOUNDED_RUNNER and node in up.args
        if not bounded and isinstance(up, ast.Assign) and len(up.targets) == 1 \
                and isinstance(up.targets[0], ast.Name):
            name = up.targets[0].id
            scope = up
            while scope in parents and not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope = parents[scope]
            bounded = any(isinstance(c, ast.Call) and _call_name(c) == BOUNDED_RUNNER
                          and c.args and isinstance(c.args[0], ast.Name) and c.args[0].id == name
                          for c in ast.walk(scope))
        if not bounded:
            missing.append(f"bound (not run through {BOUNDED_RUNNER})")
        if missing:
            problems.append((node.lineno, ", ".join(missing)))
    return sorted(problems)


@pytest.mark.parametrize("name", RATCHETED)
def test_every_librelane_container_has_a_ceiling_and_a_bound(name):
    source = (PROGRAMS / name).read_text(encoding="utf-8")
    sites = [n for n in ast.walk(ast.parse(source)) if _is_run_argv(n)]
    assert sites, f"{name}: no docker-run site found -- the ratchet would be vacuous"
    assert audit(source) == [], f"{name}: " + "; ".join(f"line {l}: {m}" for l, m in audit(source))


def test_the_audit_sees_every_shape_it_must_judge():
    """Calibration of the instrument itself, on the shapes the real sites use."""
    bad = '''
def a(docker):
    r = subprocess.run([docker, 'run', '--rm', image], capture_output=True)
def b(docker):
    cmd = ['docker' if docker == 'docker' else docker, 'run', *_dmem.docker_memory_flags()]
    cmd += ['x']
    subprocess.run(cmd, capture_output=True)
def c(docker_bin):
    run_container(["docker", "run", "--rm", image], probe_deadline_s=5)
'''
    assert [m for _l, m in audit(bad)] == [
        "memory ceiling, bound (not run through run_container)",
        "bound (not run through run_container)",
        "memory ceiling"]
    good = '''
def a(docker):
    r = run_container([docker, 'run', *_dmem.docker_memory_flags(), '--rm', image], probe_deadline_s=5)
def b(docker):
    cmd = ['docker' if docker == 'docker' else docker, 'run', *_dmem.docker_memory_flags()]
    cmd += ['x']
    done = run_container(cmd, supervised=True)
def c():
    args = ['docker', 'image', 'inspect', 'x']
    subprocess.run(args)
'''
    assert audit(good) == []




def bound_audit(source: str, tool_steps: set[str]) -> list[tuple[int, str]]:
    """(line, problem) for every `run_container` call whose bound is wrong."""
    tree = ast.parse(source)
    problems = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name == BOUNDED_RUNNER:
            continue
        for call in ast.walk(fn):
            if not (isinstance(call, ast.Call) and _call_name(call) == BOUNDED_RUNNER):
                continue
            kws = {k.arg: k.value for k in call.keywords}
            probe = kws.get("probe_deadline_s")
            sup = kws.get("supervised")
            supervised = isinstance(sup, ast.Constant) and sup.value is True
            if (probe is None) == (not supervised):
                problems.append((call.lineno, f"{fn.name}: states no single bound"))
            elif fn.name in tool_steps and not supervised:
                problems.append((call.lineno, f"{fn.name}: a tool step on a clock"))
            elif probe is not None and not (isinstance(probe, ast.Name) and probe.id == "PROBE_DEADLINE_S"):
                problems.append((call.lineno, f"{fn.name}: probe deadline is not PROBE_DEADLINE_S"))
    return sorted(problems)


@pytest.mark.parametrize("name", RATCHETED)
def test_every_run_states_its_bound_and_no_tool_runs_on_a_clock(name):
    source = (PROGRAMS / name).read_text(encoding="utf-8")
    tree = ast.parse(source)
    defined = {f.name for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)}
    assert TOOL_STEPS[name] <= defined, f"{name}: a pinned tool step is gone -- re-pin TOOL_STEPS"
    calls = [c for c in ast.walk(tree) if isinstance(c, ast.Call) and _call_name(c) == BOUNDED_RUNNER]
    assert calls, f"{name}: no run_container call -- the audit would be vacuous"
    assert bound_audit(source, TOOL_STEPS[name]) == []


def test_the_bound_audit_sees_every_shape_it_must_judge():
    bad = '''
def run_chain(docker):
    run_container(cmd, probe_deadline_s=PROBE_DEADLINE_S)
def b():
    run_container(cmd)
def c():
    run_container(cmd, probe_deadline_s=5, supervised=True)
def d():
    run_container(cmd, probe_deadline_s=86400)
'''
    assert [m for _l, m in bound_audit(bad, {"run_chain"})] == [
        "run_chain: a tool step on a clock", "b: states no single bound",
        "c: states no single bound", "d: probe deadline is not PROBE_DEADLINE_S"]
    good = '''
def run_chain(docker):
    run_container(cmd, supervised=True, log=folder / 'invocation.log')
def b():
    run_container(cmd, probe_deadline_s=PROBE_DEADLINE_S)
'''
    assert bound_audit(good, {"run_chain"}) == []


# ── the bounded runner itself ──────────────────────────────────────────────

def test_a_run_states_exactly_one_bound():
    import librelane_contract as ll
    for kw in ({}, {"probe_deadline_s": 5, "supervised": True}):
        with pytest.raises(ValueError, match="exactly one"):
            ll.run_container(["docker", "run", "img"], **kw)


def test_a_probe_is_named_and_gets_its_deadline(monkeypatch):
    import librelane_contract as ll
    seen = []

    def fake(argv, **kw):
        seen.append((list(argv), kw))
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")
    monkeypatch.setattr(ll.subprocess, "run", fake)
    done = ll.run_container(["docker", "run", "--rm", "img"], probe_deadline_s=42)
    assert done.stdout == "ok"
    [(argv, kw)] = seen
    assert argv[:3] == ["docker", "run", "--name"] and argv[3].startswith("vibeic_ll")
    assert argv[4:] == ["--rm", "img"]
    assert kw["timeout"] == 42 and kw["capture_output"] is True and kw["text"] is True


def _probe_past_its_deadline(monkeypatch, rm_rc=0, rm_err=""):
    import librelane_contract as ll
    seen = []

    def fake(argv, **kw):
        seen.append(list(argv))
        if argv[1] == "run":
            exc = subprocess.TimeoutExpired(argv, kw.get("timeout"))
            exc.stdout, exc.stderr = b"partial-tool-log\n", b""
            raise exc
        return SimpleNamespace(returncode=rm_rc, stdout="", stderr=rm_err)
    monkeypatch.setattr(ll.subprocess, "run", fake)
    return ll, seen


def test_a_probe_past_its_deadline_is_removed_by_name_and_keeps_its_output(monkeypatch, tmp_path):
    ll, seen = _probe_past_its_deadline(monkeypatch)
    with pytest.raises(ll.Refusal, match=r"LL_TOOL_DEADLINE: .*7 s probe deadline.*removed") as refused:
        ll.run_container(["dockerX", "run", "--rm", "img"], probe_deadline_s=7)
    run, rm = seen
    assert rm == ["dockerX", "rm", "-f", run[3]]
    assert run[3] in str(refused.value) and "partial-tool-log" in str(refused.value)
    log = tmp_path / "step" / "resolution.log"
    with pytest.raises(ll.Refusal, match=f"partial output in {log}"):
        ll.run_container(["dockerX", "run", "--rm", "img"], probe_deadline_s=7, log=log)
    assert "partial-tool-log" in log.read_text()


def test_a_container_that_would_not_go_is_said_to_be_still_there(monkeypatch):
    ll, _seen = _probe_past_its_deadline(monkeypatch, rm_rc=1, rm_err="daemon wedged")
    with pytest.raises(ll.Refusal, match=r"NOT removed \(rc=1: daemon wedged\)"):
        ll.run_container(["dockerX", "run", "--rm", "img"], probe_deadline_s=7)


# ── a tool step: supervised by progress, never stopped on a clock ──────────

_FAKE_DOCKER = r'''#!/usr/bin/env python3
"""A docker stand-in with the semantics run_container relies on: `rm -f NAME`
stops what `run --name NAME` started, and `inspect -f {{.State.Pid}} NAME`
names the process it runs (empty when FAKE_DOCKER_BLIND is set)."""
import os, signal, sys
from pathlib import Path
state = Path(os.environ["FAKE_DOCKER_STATE"])
args = sys.argv[1:]
with open(state / "calls.txt", "a") as log:
    log.write(" ".join(args[:4]) + "\n")
if args[0] == "run":
    name = args[args.index("--name") + 1]
    (state / (name + ".pid")).write_text(str(os.getpid()))
    cmd = args[args.index("IMG") + 1:]
    os.execvp(cmd[0], cmd)
if args[0] == "inspect":
    pid = state / (args[-1] + ".pid")
    if pid.exists() and not os.environ.get("FAKE_DOCKER_BLIND"):
        print(pid.read_text())
if args[0] == "rm":
    pid = state / (args[-1] + ".pid")
    if not pid.exists():
        sys.exit("Error: No such container: " + args[-1])
    try:
        os.kill(int(pid.read_text()), signal.SIGKILL)
    except ProcessLookupError:
        pass
    pid.unlink()
    print(args[-1])
'''

_BUSY = "end=$(( $(date +%s) + 3 )); while [ $(date +%s) -lt $end ]; do :; done"


@pytest.fixture()
def tool(tmp_path, monkeypatch):
    """A fake docker binary and a 1 s stall grace. The container's CPU is the
    REAL host CPU of the process the fake runs, read the way run_container
    reads it (inspect -> pid -> host process tree)."""
    import librelane_contract as ll
    state = tmp_path / "docker_state"
    state.mkdir()
    binary = tmp_path / "docker"
    binary.write_text(_FAKE_DOCKER)
    binary.chmod(0o755)
    monkeypatch.setenv("FAKE_DOCKER_STATE", str(state))
    monkeypatch.setattr(ll, "TOOL_STALL_GRACE_S", 1.0)
    return SimpleNamespace(ll=ll, binary=str(binary), state=state,
                           calls=lambda: (state / "calls.txt").read_text().splitlines())


def test_a_tool_still_printing_runs_past_its_budget_and_the_crossing_is_recorded(tool, monkeypatch):
    """Output is progress; the budget (here 0.5 s) is recorded, and kills nothing."""
    monkeypatch.setattr(tool.ll, "TOOL_BUDGET_S", 0.5)
    t0 = time.monotonic()
    done = tool.ll.run_container(
        [tool.binary, "run", "--rm", "IMG", "sh", "-c",
         "for i in 1 2 3 4 5 6 7 8 9 10 11 12; do echo line$i; sleep 0.2; done"],
        supervised=True)
    assert done.returncode == 0 and "line12" in done.stdout
    assert time.monotonic() - t0 > 2.0          # longer than grace AND budget
    assert not any(c.startswith("rm ") for c in tool.calls())
    assert "VIBEIC_CEILING_CROSSED" in done.stderr and "not a kill" in done.stderr


def test_a_silent_tool_whose_container_cpu_moves_is_not_stopped(tool):
    done = tool.ll.run_container([tool.binary, "run", "--rm", "IMG", "sh", "-c", _BUSY + "; echo done"],
                                 supervised=True)
    assert done.returncode == 0 and "done" in done.stdout
    assert not any(c.startswith("rm ") for c in tool.calls())


def test_the_cpu_reading_needs_no_attach_to_the_container(tool, monkeypatch):
    """Review wave 8: the exec-based probe went blind on any image other than
    the pinned runtime. Make that probe blind and the attach refuse: a silent,
    CPU-busy job must still run past the grace."""
    import _docker_watchdog as dw
    import _eda_pin
    monkeypatch.setattr(_eda_pin, "container_attach_refusal", lambda *_a, **_k: "IMAGE_MISMATCH")
    monkeypatch.setattr(dw, "ephemeral_container_cpu_probe", lambda *_a, **_k: (lambda _p: None))
    done = tool.ll.run_container([tool.binary, "run", "--rm", "IMG", "sh", "-c", _BUSY], supervised=True)
    assert done.returncode == 0


def test_a_tool_with_flat_cpu_and_output_is_reaped_by_name_and_keeps_its_output(tool, tmp_path):
    log = tmp_path / "step" / "invocation.log"
    t0 = time.monotonic()
    with pytest.raises(tool.ll.Refusal, match=r"LL_TOOL_STALLED: .*no progress \(container CPU and "
                                              r"output flat; watched=.*\) for 1 s") as refused:
        tool.ll.run_container([tool.binary, "run", "--rm", "IMG", "sh", "-c",
                               "echo started; exec sleep 60"], supervised=True, log=log)
    assert time.monotonic() - t0 < 30            # reaped at the grace, not at the sleep's end
    run = next(c for c in tool.calls() if c.startswith("run "))
    name = run.split()[2]
    assert f"rm -f {name}" in tool.calls()
    message = str(refused.value)
    assert f"container {name} removed by the watchdog's reap" in message
    assert f"partial output in {log}" in message and "started" in log.read_text()


def test_a_stall_read_on_output_alone_says_so(tool, monkeypatch):
    monkeypatch.setenv("FAKE_DOCKER_BLIND", "1")
    with pytest.raises(tool.ll.Refusal, match=r"output flat; container CPU unreadable; watched=.*cpu:unreadable"):
        tool.ll.run_container([tool.binary, "run", "--rm", "IMG", "sh", "-c", "exec sleep 60"],
                              supervised=True)


def test_a_tool_run_is_never_given_a_clock(tool, monkeypatch):
    seen = []
    real = subprocess.run

    def spy(argv, **kw):
        seen.append((list(argv), dict(kw)))
        return real(argv, **kw)
    monkeypatch.setattr(tool.ll.subprocess, "run", spy)
    tool.ll.run_container([tool.binary, "run", "--rm", "IMG", "true"], supervised=True)
    runs = [kw for argv, kw in seen if argv[1:2] == ["run"]]
    assert runs and all("timeout" not in kw for kw in runs)


def test_a_tool_client_that_cannot_start_raises_as_subprocess_would(tmp_path):
    import librelane_contract as ll
    with pytest.raises(FileNotFoundError):
        ll.run_container([str(tmp_path / "no-docker"), "run", "IMG", "true"], supervised=True)


# ── the Tcl alias probe is best-effort ──────────────────────────────────────

def test_the_tcl_alias_probe_degrades_to_not_measured_on_its_deadline(monkeypatch):
    import librelane_contract as ll
    monkeypatch.setattr(ll, "_CAPABILITY", {})
    answers = iter([SimpleNamespace(returncode=0, stdout="", stderr=""), "deadline"])

    def fake(argv, **kw):
        got = next(answers)
        if got == "deadline":
            raise ll.Refusal("LL_TOOL_DEADLINE", "docker run passed its 600 s probe deadline")
        return got
    monkeypatch.setattr(ll, "run_container", fake)
    cap = ll.image_capability("img")
    assert cap["openroad_aliases"] == {} and cap["tcl_probe"].startswith("NOT_MEASURED: LL_TOOL_DEADLINE")


def test_the_tcl_alias_probe_still_raises_any_other_refusal(monkeypatch):
    import librelane_contract as ll
    monkeypatch.setattr(ll, "_CAPABILITY", {})
    answers = iter([SimpleNamespace(returncode=0, stdout="", stderr=""), "other"])

    def fake(argv, **kw):
        got = next(answers)
        if got == "other":
            raise ll.Refusal("LL_TOOL_STALLED", "x")
        return got
    monkeypatch.setattr(ll, "run_container", fake)
    with pytest.raises(ll.Refusal, match="LL_TOOL_STALLED"):
        ll.image_capability("img")


def member_audit(source: str, probes: set[str], tools: set[str]) -> list[str]:
    """What is wrong with WHO calls run_container: a pinned function that no
    longer does, a caller nobody pinned, a tool step with no supervised run."""
    tree = ast.parse(source)
    callers: dict[str, list[ast.Call]] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name != BOUNDED_RUNNER:
            calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and _call_name(c) == BOUNDED_RUNNER]
            if calls:
                callers[fn.name] = calls
    problems = [f"{f}: runs no container through run_container" for f in sorted((probes | tools) - set(callers))]
    problems += [f"{f}: calls run_container but is not pinned" for f in sorted(set(callers) - probes - tools)]
    for f in sorted(tools & set(callers)):
        if not any(k.arg == "supervised" and isinstance(k.value, ast.Constant) and k.value.value is True
                   for c in callers[f] for k in c.keywords):
            problems.append(f"{f}: no supervised run")
    module = next((name for name in RATCHETED
                   if probes == PROBES[name] and tools == TOOL_STEPS[name]), None)
    if module is not None:
        actual = subprocess_edge_members(source)
        expected = SUBPROCESS_EDGES[module]
        problems += [f"{edge[0]}: subprocess edge is not pinned"
                     for edge in sorted((actual - expected).elements())]
        problems += [f"{edge[0]}: pinned subprocess edge disappeared"
                     for edge in sorted((expected - actual).elements())]
    return problems


def subprocess_edge_members(source: str) -> Counter[tuple[str, str, str, str, bool]]:
    """Count each direct subprocess edge by owner, call, argv and bound."""
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree)
               for child in ast.iter_child_nodes(node)}
    members: Counter[tuple[str, str, str, str, bool]] = Counter()
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
            continue
        if not (isinstance(call.func.value, ast.Name)
                and call.func.value.id == "subprocess"
                and call.func.attr in {"run", "Popen", "check_output"}):
            continue
        names = []
        owner = parents[call]
        while not isinstance(owner, ast.Module):
            if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.append(owner.name)
            owner = parents[owner]
        owner_name = ".".join(reversed(names)) if names else "<module>"
        timeout = next((ast.unparse(kw.value) for kw in call.keywords
                        if kw.arg == "timeout"), "")
        forwarded = any(kw.arg is None for kw in call.keywords)
        argv = ast.unparse(call.args[0]) if call.args else "<missing argv>"
        members[(owner_name, call.func.attr, argv, timeout, forwarded)] += 1
    return members


@pytest.mark.parametrize("name", RATCHETED)
def test_the_pinned_functions_are_exactly_the_container_runners(name):
    source = (PROGRAMS / name).read_text(encoding="utf-8")
    assert member_audit(source, PROBES[name], TOOL_STEPS[name]) == []


_RESPELLED = """
def run_chain(docker):
    cmd = [docker, *['run'], '--rm', *_dmem.docker_memory_flags()]
    subprocess.run(cmd, capture_output=True, text=True)
def run_sta_script(docker):
    subprocess.run([docker] + ['run', '--rm'], capture_output=True, text=True)
def image_capability(docker):
    run_container([docker, 'run', *_dmem.docker_memory_flags()], probe_deadline_s=PROBE_DEADLINE_S)
def helper(docker):
    run_container([docker, 'run', *_dmem.docker_memory_flags()], probe_deadline_s=PROBE_DEADLINE_S)
def _openroad_convert(docker):
    run_container([docker, 'run', *_dmem.docker_memory_flags()], probe_deadline_s=PROBE_DEADLINE_S)
"""


def test_the_member_audit_sees_a_respelled_tool_step():
    """Review wave 8: `[docker, *['run'], ...]` run through subprocess.run left
    every literal-shape audit green. The member pin names it."""
    assert member_audit(_RESPELLED, {"image_capability"},
                        {"run_chain", "run_sta_script", "_openroad_convert"}) == [
        "run_chain: runs no container through run_container",
        "run_sta_script: runs no container through run_container",
        "helper: calls run_container but is not pinned",
        "_openroad_convert: no supervised run"]


@pytest.mark.parametrize("edge", ["run", "Popen", "check_output"])
def test_a_new_subprocess_edge_cannot_hide_behind_respelled_docker_argv(edge):
    """Pin every subprocess owner, even when no literal docker-run list appears."""
    name = "librelane_contract.py"
    source = (PROGRAMS / name).read_text(encoding="utf-8")
    mutant = source + f'''
def _unbounded_container(docker, image):
    argv = [docker, *["run"], image]
    subprocess.{edge}(argv)
'''
    assert audit(mutant) == []
    assert bound_audit(mutant, TOOL_STEPS[name]) == []
    assert member_audit(mutant, PROBES[name], TOOL_STEPS[name]) == [
        "_unbounded_container: subprocess edge is not pinned"]


def test_a_second_subprocess_edge_inside_a_pinned_function_is_detected():
    name = "librelane_contract.py"
    source = (PROGRAMS / name).read_text(encoding="utf-8")
    original = "    try:\n        done = subprocess.run([docker, *argv], capture_output=True, text=True,"
    assert source.count(original) == 1
    mutant = source.replace(
        original,
        "    subprocess.run([docker, *['run'], 'image'], capture_output=True)\n" + original)
    assert audit(mutant) == []
    assert bound_audit(mutant, TOOL_STEPS[name]) == []
    assert member_audit(mutant, PROBES[name], TOOL_STEPS[name]) == [
        "_docker_lines: subprocess edge is not pinned"]
