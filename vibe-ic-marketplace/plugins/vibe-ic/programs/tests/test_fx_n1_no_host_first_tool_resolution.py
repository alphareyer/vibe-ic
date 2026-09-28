"""FX-N1 ratchet — no shipped program resolves an EDA tool from the host PATH.

`_eda_tool_route` is the ONE place that decides where an EDA tool runs (the
pinned image whenever a container route exists; this filesystem's tool, with
its version recorded and checked, only when there is none). Measured on main
76a277544 before this change: 36 program files resolved yosys / iverilog / vvp /
verilator / sta / klayout / magic / sv2v on the host PATH themselves, several
of them FIRST (host, then container) — on 8HD-9 that made Step 1 fail on a
host yosys 0.9 while the pinned image elaborated the same design.

WHAT THIS SCAN FORBIDS, in `programs/` (AST, not grep):
  1. `<anything>.which("<eda tool>")` — asking the host PATH for the tool,
     also through a name bound to the tool's name (review wave 4c);
  2. `["which", "<eda tool>"]` — the same question through a subprocess;
  3. an argv literal whose argv[0] is an EDA tool (or a NAME BOUND to an EDA
     tool's name: a module constant `SIMULATOR = "verilator"`, a local
     `cand = override or "verilator"`) handed to anything that is
     not a routing function: `_tool_route.<run|argv_for|resolve|
     supervised_run|watchdog_run>`, a function of the SAME module whose body
     calls `_tool_route`, or a callee in `_ROUTED_ELSEWHERE` (each with the
     reason it is not a host PATH lookup). An argv bound to a name first
     (`cmd = ["iverilog", ...]`) is followed to the calls in the same function
     that receive that name.

Tables of tool NAMES (report signatures, keyword lists, records) are not
executions and are not flagged: only a literal that reaches a call is.

THE EXEMPTION IS BY MODULE AND IT IS SHORT: `_eda_tool_route.py` itself, and
`librelane_plugins/` — plugins LibreLane loads into its own process inside the
image, where they cannot import `programs/` and where the tools on PATH are the
image's by construction.

The census is proved non-vacuous by `test_the_scan_catches_each_forbidden_shape`
(each shape planted into a synthetic module is reported), and by construction
the scan of main is RED.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

_PROGRAMS = Path(__file__).resolve().parents[1]

TOOLS = frozenset({
    "yosys", "yosys-abc", "iverilog", "vvp", "verilator", "verilator_coverage",
    "sby", "eqy", "sta", "openroad", "magic", "klayout", "netgen", "ngspice",
    "Xyce", "sv2v",
})

_ROUTER = "_tool_route"
_ROUTER_FUNCS = frozenset({"run", "argv_for", "resolve", "supervised_run",
                           "watchdog_run"})

#: (module stem, callee name) -> why a tool argv handed to it is not a host
#: PATH lookup. Reviewed in the diff; an entry whose call site is gone is a
#: finding (`test_every_routed_elsewhere_entry_is_still_used`).
_ROUTED_ELSEWHERE: Dict[Tuple[str, str], str] = {
    ("drc_feedback_repair", "_docker"):
        "builds `docker run <image> --skip <argv>`: the tool runs in the image",
    ("path_spice_tool", "_docker"):
        "builds `docker run ... <image> <argv>`: the tool runs in the image",
    ("sim_activity_dump", "exec_fn"):
        "injected executor; its default `_local` and the runner's "
        "`_verilator_stage_exec` both route through _eda_tool_route",
    ("verilator_coverage_measure", "exec_fn"):
        "injected executor; its default is this module's `run`, which routes "
        "through _eda_tool_route, and the runner passes `_verilator_stage_exec`",
}

_EXEMPT_MODULES = {"_eda_tool_route"}
_EXEMPT_DIRS = {"librelane_plugins", "tests", "calibration"}


def _shipped(root: Path) -> List[Path]:
    out = []
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root)
        if any(part in _EXEMPT_DIRS for part in rel.parts[:-1]):
            continue
        if p.stem in _EXEMPT_MODULES or p.name.startswith("test_"):
            continue
        out.append(p)
    return out


def _tool_valued(e: ast.AST) -> Optional[str]:
    """The EDA tool an expression evaluates to by DEFAULT: a string constant
    naming one, or an `a or b or "tool"` / `x if c else "tool"` whose operand
    does. (Review wave 4c: `cand = override or env or "verilator"` then
    `which(cand)` was invisible to a scan that read only literals.)"""
    if isinstance(e, ast.Constant) and isinstance(e.value, str) and e.value in TOOLS:
        return e.value
    if isinstance(e, ast.BoolOp):
        for v in e.values:
            t = _tool_valued(v)
            if t:
                return t
    if isinstance(e, ast.IfExp):
        return _tool_valued(e.body) or _tool_valued(e.orelse)
    return None


def _tool_names(scope: ast.AST, *, top: bool) -> Dict[str, str]:
    """Names bound to an EDA tool's name in `scope`: at module level only the
    module's own statements (constants visible everywhere), in a function
    every assignment in its body."""
    out: Dict[str, str] = {}
    nodes = scope.body if top else ast.walk(scope)
    for n in nodes:
        if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None:
            t = _tool_valued(n.value)
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            if t:
                for tg in targets:
                    if isinstance(tg, ast.Name):
                        out[tg.id] = t
    return out


def _is_tool_literal(n: ast.AST, names: Optional[Dict[str, str]] = None
                     ) -> Optional[str]:
    if isinstance(n, (ast.List, ast.Tuple)) and n.elts:
        e0 = n.elts[0]
        if isinstance(e0, ast.Constant) and isinstance(e0.value, str) \
                and e0.value in TOOLS:
            return e0.value
        if names and isinstance(e0, ast.Name) and e0.id in names:
            return names[e0.id]
    return None


def _callee(call: ast.Call) -> Tuple[Optional[str], str]:
    f = call.func
    if isinstance(f, ast.Attribute):
        recv = f.value.id if isinstance(f.value, ast.Name) else None
        return recv, f.attr
    if isinstance(f, ast.Name):
        return None, f.id
    return None, "?"


#: Callees that LAUNCH a process from an argv. A tool literal handed to
#: anything else (`frozenset`, `enumerate`, `any`, a record builder, the
#: watchdog's `completed_process` adapter) is data, not an execution.
_EXEC_NAMES = frozenset({
    "run", "Popen", "call", "check_call", "check_output", "run_supervised",
    "run_host_supervised", "_run", "_sh", "exec_fn", "system", "popen",
    "spawn", "execvp", "execv",
})


def _routing_functions(tree: ast.AST) -> Set[str]:
    """Functions of this module that route: their body calls `_tool_route`
    or another routing function of the module (to a fixpoint)."""
    fns = {fn.name: fn for fn in ast.walk(tree)
           if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))}
    routing: Set[str] = set()
    changed = True
    while changed:
        changed = False
        for name, fn in fns.items():
            if name in routing:
                continue
            for n in ast.walk(fn):
                if isinstance(n, ast.Call):
                    recv, callee = _callee(n)
                    if recv == _ROUTER or (recv is None and callee in routing):
                        routing.add(name)
                        changed = True
                        break
    return routing


def _launches(call: ast.Call, routing: Set[str]) -> bool:
    """Does this call launch the argv it is handed (and not via a router)?"""
    recv, name = _callee(call)
    if recv == _ROUTER:
        return False
    if recv is None and name in routing:
        return False
    return name in _EXEC_NAMES


def _allowed(stem: str, call: ast.Call, routing: Set[str]) -> bool:
    recv, name = _callee(call)
    if recv == _ROUTER and name in _ROUTER_FUNCS:
        return True
    if recv is None and name in routing:
        return True
    if not _launches(call, routing):
        return True
    return (stem, name) in _ROUTED_ELSEWHERE


def _first_arg_names(node: ast.AST) -> Set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def scan_source(stem: str, src: str) -> List[str]:
    """Every forbidden site in one module, as '<stem>:<line> <why>'."""
    tree = ast.parse(src)
    parents: Dict[ast.AST, ast.AST] = {}
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n):
            parents[c] = n
    routing = _routing_functions(tree)
    found: List[str] = []
    module_names = _tool_names(tree, top=True)
    fn_names: Dict[ast.AST, Dict[str, str]] = {}

    def enclosing_fn(n):
        while n in parents:
            n = parents[n]
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return n
        return tree

    def names_at(n) -> Dict[str, str]:
        fn = enclosing_fn(n)
        if fn is tree:
            return module_names
        if fn not in fn_names:
            fn_names[fn] = dict(module_names, **_tool_names(fn, top=False))
        return fn_names[fn]

    for n in ast.walk(tree):
        # 1. which("<tool>"), or which(<name bound to a tool's name>)
        if isinstance(n, ast.Call) and _callee(n)[1] == "which" and n.args \
                and isinstance(n.args[0], ast.Constant) \
                and n.args[0].value in TOOLS:
            found.append(f"{stem}:{n.lineno} which({n.args[0].value!r}) asks the host PATH")
            continue
        if isinstance(n, ast.Call) and _callee(n)[1] == "which" and n.args \
                and isinstance(n.args[0], ast.Name) \
                and n.args[0].id in names_at(n):
            found.append(f"{stem}:{n.lineno} which({n.args[0].id}) asks the host "
                         f"PATH for {names_at(n)[n.args[0].id]!r}")
            continue
        # 2. ["which", "<tool>"]
        if isinstance(n, (ast.List, ast.Tuple)) and len(n.elts) >= 2 \
                and isinstance(n.elts[0], ast.Constant) and n.elts[0].value == "which" \
                and isinstance(n.elts[1], ast.Constant) and n.elts[1].value in TOOLS:
            found.append(f"{stem}:{n.lineno} ['which', {n.elts[1].value!r}] asks the host PATH")
            continue
        # 3. a tool argv literal reaching a non-routing call
        tool = _is_tool_literal(n, names_at(n))
        if not tool:
            continue
        top = n
        while parents.get(top) is not None and isinstance(
                parents[top], (ast.BinOp, ast.Starred, ast.List)):
            top = parents[top]
        par = parents.get(top)
        if isinstance(par, ast.Call) and par.args and par.args[0] is top:
            if not _allowed(stem, par, routing):
                found.append(f"{stem}:{n.lineno} [{tool!r}, ...] executed by "
                             f"{_callee(par)[1]}() off the tool route")
        elif isinstance(par, ast.Assign) and all(
                isinstance(t, ast.Name) for t in par.targets):
            names = {t.id for t in par.targets}
            for c in ast.walk(enclosing_fn(par)):
                if isinstance(c, ast.Call) and c.args \
                        and _first_arg_names(c.args[0]) & names \
                        and not _allowed(stem, c, routing):
                    found.append(f"{stem}:{c.lineno} {sorted(names)} = [{tool!r}, ...] "
                                 f"executed by {_callee(c)[1]}() off the tool route")
    return sorted(set(found))


def scan(root: Path = _PROGRAMS) -> List[str]:
    out: List[str] = []
    for p in _shipped(root):
        try:
            out += scan_source(p.stem, p.read_text(errors="replace"))
        except SyntaxError:
            continue
    return out


def test_no_program_resolves_an_eda_tool_from_the_host_path():
    offenders = scan()
    assert offenders == [], (
        f"{len(offenders)} site(s) resolve an EDA tool outside _eda_tool_route "
        "(route it through `_tool_route.run/available/...`):\n  "
        + "\n  ".join(offenders))


def test_every_routed_elsewhere_entry_is_still_used():
    """An allowance that outlived its call site is itself a finding."""
    used = set()
    for p in _shipped(_PROGRAMS):
        tree = ast.parse(p.read_text(errors="replace"))
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and n.args and _is_tool_literal(n.args[0]) \
                    or isinstance(n, ast.Call) and n.args and isinstance(n.args[0], ast.Name):
                used.add((p.stem, _callee(n)[1]))
    stale = [k for k in _ROUTED_ELSEWHERE if k not in used]
    assert stale == [], stale


def test_the_scan_catches_each_forbidden_shape():
    """Non-vacuity: each forbidden shape, planted, is reported; each routed
    shape is not."""
    planted = '''
import shutil, subprocess
def a():
    return shutil.which("yosys")
def b():
    return subprocess.run(["which", "iverilog"]).returncode
def c(x):
    return subprocess.run(["iverilog", "-o", x], capture_output=True)
def d(x):
    cmd = ["verilator", "--binary", x]
    return subprocess.run(cmd)
def e(x):
    return _tool_route.run(["iverilog", "-o", x])
def f(x):
    cmd = ["vvp", x]
    return _tool_route.run(cmd)
def _helper(cmd):
    return _tool_route.run(cmd)
def g(x):
    return _helper(["yosys", "-p", x])
TABLE = {"klayout": ["klayout", "-b"]}
SIM = "verilator"
def h(x):
    return subprocess.run([SIM, "--lint-only", x])
def i(o):
    cand = o or os.environ.get("VERILATOR") or "verilator"
    return shutil.which(cand)
def j(x):
    return _tool_route.run([SIM, x])
def k(x):
    cmd = [SIM, "--binary", x]
    return subprocess.run(cmd)
'''
    got = scan_source("planted", planted)
    lines = {int(s.split(":")[1].split()[0]) for s in got}
    assert lines == {4, 6, 8, 11, 24, 27, 32}, got
    # data, not execution: never flagged
    assert scan_source("tables", "N = frozenset(['yosys', 'sta'])\n"
                                 "for i, t in enumerate(['iverilog', 'vvp']): pass\n") == []
