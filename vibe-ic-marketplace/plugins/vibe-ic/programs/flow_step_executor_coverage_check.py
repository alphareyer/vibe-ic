#!/usr/bin/env python3
"""flow_step_executor_coverage_check.py — "every step has an executor that runs it".

The COMPANION to flow_step_execution_coverage_check.py. That gate confirms a step
COMPLETED (produced output, in order). THIS gate confirms a step CAN run at all:
every canonical flow step must have a real EXECUTOR wired into a runner that will
produce its `required_outputs`. A step that is DEFINED + AUDITED but has no runner
producing its outputs is structurally orphaned — it can only ever be MISSING, and
that is the root cause of "middle steps silently skipped".

Detection (static, deterministic): output/program signals remain the existing
coverage arms. Executor entries require a reachable dispatcher associating the
canonical step/span, its actual callable and the declared output obligation.
A step is:
  - WIRED           : a bound producer dispatch, the step's output path, or
                      a program it declares in `programs:` identifies an
                      executor produces it. The `programs:` signal is a
                      REFERENCE test, never a declaration test: a step that
                      lists a program no runner invokes is still ORPHANED.
  - SKILL-ONLY-AI   : no mcp_tool / program, but declares `skills` → an AI
                      authoring step the runner WAIVES to by design (spec-to-rtl,
                      analog authoring). Not an orphan.
  - ORPHANED        : has required_outputs but NO runner references its outputs or
                      any executor for it → can never run. THIS is the gap.

The runner set is the closed list of "things that actually run a step":
vibe_ic / phase1 / phase2 / phase3 / design / analog one-shot runners +
phase3_backend_step. Gate/checker programs are NOT runners (they read outputs,
they don't produce them), so they are deliberately excluded from the search.

Usage:
    python3 flow_step_executor_coverage_check.py [--flow-def <yaml>] [--json <out>]
                                                 [--strict]
    exit 0 = every applicable step has a wired executor (or is SKILL-ONLY-AI)
    exit 1 = one or more ORPHANED steps  (only with --strict; default is advisory
             exit 0 so the gate can ship before every orphan is wired, while still
             printing the gap list)
"""
import argparse
import ast
import hashlib
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_DEFAULT_FLOW = _HERE.parent / "flow" / "phase1_phase2_phase3.yaml"

# The closed set of RUNNERS — the only modules that actually drive a step and
# write its outputs. Checker/gate programs are excluded on purpose.
_RUNNER_FILES = [
    "vibe_ic_one_shot_runner.py",
    "phase1_one_shot_runner.py",
    "phase2_one_shot_runner.py",
    "design_one_shot_runner.py",
    "phase3_one_shot_runner.py",
    "phase3_backend_step.py",
    "analog_one_shot_runner.py",
]


def _load_runner_text() -> str:
    parts = []
    for f in _RUNNER_FILES:
        p = _HERE / f
        if p.is_file():
            parts.append(p.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(parts)


def _iter_steps(doc):
    out = []

    def walk(o):
        if isinstance(o, dict):
            if "id" in o and ("name" in o or "required_outputs" in o):
                out.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(doc)
    return out


# Basenames / directory leaves so common that finding them ALONE in a runner is
# NOT evidence a specific step is wired (they belong to many steps). A step is
# only WIRED by the DISTINCTIVE last-two-component form, never a bare generic.
_GENERIC_TOKENS = {
    "results.json", "coverage.json", "pass.flag", "results.log", "results.xml",
    "summary.json", "report.json", "report.rpt", "netlist.v", "top.gds",
    "done.flag", "reports", "stage1", "stage2", "stage3", "stage4", "phase1",
    "phase2", "phase3", "sta", "sim", "synth", "gds", "pnr", "src", "output",
}


def _output_signals(required_outputs):
    """Distinctive search fragments for a step's required_outputs.

    The DISCRIMINATING signal is the last two path components joined
    (e.g. 'dft/coverage.json', 'formal/results.json') — a bare basename like
    'coverage.json' is too generic and false-matched a DIFFERENT coverage.json
    (the exact bug that made this gate call step 11-DFT WIRED when its real
    signals — scan_netlist / atpg / eda_dft — appear 0× in any runner). A bare
    basename is only kept as a fallback when it is NOT in _GENERIC_TOKENS and
    the path has a single component."""
    sigs = set()
    for ro in required_outputs or []:
        # a required_output may list acceptable ALTERNATIVES joined by ' OR '
        # (e.g. 'spice/*.sp OR sim_spice/*.sp') — each alternative is its own
        # candidate path; the step is wired if ANY alternative is produced.
        for alt in re.split(r"\s+OR\s+", str(ro).strip()):
            alt = alt.strip()
            if not alt:
                continue
            raw = [p for p in alt.split("/") if p]
            if not raw:
                continue
            leaf_is_glob = "*" in raw[-1]
            nonglob = [p for p in raw if "*" not in p]
            if not nonglob:
                continue
            # A DISTINCTIVE basename is a valid signal — runner paths are built
            # with `dir / "leaf"` (Path join), so the literal 'dir/leaf'
            # substring rarely appears even for a genuinely-wired step (that
            # over-strictness false-ORPHANed steps 10/23/27/28). Excluded when:
            #  - the raw leaf was a GLOB ('*.sp') → the surviving 'nonglob[-1]'
            #    is only the directory ('spice'), NOT a real basename; matching
            #    a bare dir word anywhere false-WIRES (the step-30 'spice' bug).
            #  - the basename is GENERIC ('coverage.json') → would false-WIRE by
            #    matching a different file with the same leaf (step-11 DFT bug).
            if not leaf_is_glob and nonglob[-1] not in _GENERIC_TOKENS:
                sigs.add(nonglob[-1])
            # the dir+leaf pair is an additional (stronger) signal
            if len(nonglob) >= 2:
                sigs.add("/".join(nonglob[-2:]))
    return sigs


def _skip_anchors(required_outputs):
    """Distinctive tokens under which a CONSCIOUS skip-sentinel would live for
    this step — the parent directory leaf (if non-generic) else the basename
    stem. Used to detect a DISCLOSED-SKIP: a runner that writes
    '<anchor>...(_not_run|_skipped).json' has consciously not-run the step with
    a recorded reason (formal_not_run.json, sdf_sim_skipped.json) — honest, not
    a silent orphan."""
    anchors = set()
    for ro in required_outputs or []:
        parts = [p for p in str(ro).split("/") if p and "*" not in p]
        if not parts:
            continue
        # immediate parent dir leaf, e.g. 'formal', 'sim_postlayout'
        if len(parts) >= 2 and parts[-2] not in _GENERIC_TOKENS:
            anchors.add(parts[-2])
        # basename stem, e.g. 'lec' from 'lec.rpt'
        stem = parts[-1].split(".")[0]
        if stem and stem not in _GENERIC_TOKENS and len(stem) >= 3:
            anchors.add(stem)
    return anchors


def _has_disclosed_skip(anchors, runner_text):
    """True if a runner writes a conscious skip-sentinel for this step:
    a token '<anchor>...(_not_run|_skipped|_skip).<ext>' or bare '<anchor>_not_run'."""
    for a in anchors:
        if re.search(rf"{re.escape(a)}[A-Za-z0-9_/]*?(?:_not_run|_skipped|_skip\b)",
                     runner_text):
            return True
    return False


#: `mcp_tools:` entry forms the flow declares (audit §3.23-1): the runner entry
#: point that performs the step, and the LibreLane step class of its tool arm.
_RUNNER_EXECUTOR = re.compile(r"^(?P<module>[A-Za-z_][A-Za-z0-9_]*)\.(?P<func>[A-Za-z_][A-Za-z0-9_]*)$")
_LIBRELANE_EXECUTOR = re.compile(r"^librelane:(?P<cls>[A-Za-z][A-Za-z0-9_]*\.[A-Za-z][A-Za-z0-9_]*)$")
_AST_CACHE: dict = {}


def _executed_nodes(node):
    """Walk a potentially executed body, excluding uncalled nested bodies.

    Unknown conditions remain conditional wiring; literal dead branches and
    statements after return are not execution paths. Lambdas are walked only
    when a dispatcher actually invokes their parameter.
    """
    if isinstance(node, list):
        for child in node:
            yield from _executed_nodes(child)
            if isinstance(child, (ast.Return, ast.Raise)):
                break
        return
    if not isinstance(node, ast.AST):
        return  # Dict unpacking has a None key; primitive fields have no body.
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                         ast.Lambda)):
        return
    yield node
    if isinstance(node, ast.If) and isinstance(node.test, ast.Constant):
        yield from _executed_nodes(node.body if node.test.value else node.orelse)
        return
    if isinstance(node, ast.IfExp) and isinstance(node.test, ast.Constant):
        yield from _executed_nodes(node.body if node.test.value else node.orelse)
        return
    for _field, value in ast.iter_fields(node):
        if isinstance(value, list):
            yield from _executed_nodes(value)
        elif isinstance(value, ast.AST):
            yield from _executed_nodes(value)


def _literal(node):
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return None


class _RunnerSource:
    """Closed source call graph plus actual canonical producer dispatches."""

    def __init__(self, module, text):
        self.module = module
        self.tree = ast.parse(text)
        self.functions = {}
        self.bindings = {}
        self.reachable = set()
        self._import_cache = {}
        self._resolve_cache = {}
        self._local_cache = {}
        self._index(self.tree.body, ())
        self._visit_function(("main",))

    def _index(self, body, scope):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                key = scope + (node.name,)
                self.functions[key] = node
                self._index(node.body, key)
            elif not isinstance(node, ast.ClassDef):
                for _field, value in ast.iter_fields(node):
                    if isinstance(value, list):
                        self._index([n for n in value if isinstance(n, ast.stmt)], scope)

    def _resolve(self, name, scope):
        cache_key = (name, scope)
        if cache_key not in self._resolve_cache:
            self._resolve_cache[cache_key] = self._resolve_uncached(name, scope)
        return self._resolve_cache[cache_key]

    def _resolve_uncached(self, name, scope):
        # A parameter/local value with the same spelling is not the global
        # producer. Explicit local function definitions still resolve.
        for size in range(len(scope), -1, -1):
            key = scope[:size] + (name,)
            if key in self.functions:
                return key
            fn = self.functions.get(scope[:size])
            if fn:
                parent = scope[:size]
                if parent not in self._local_cache:
                    self._local_cache[parent] = {
                        a.arg for a in fn.args.args + fn.args.kwonlyargs}
                    self._local_cache[parent].update(
                        n.id for n in _executed_nodes(fn.body)
                        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store))
                if name in self._local_cache[parent]:
                    return None
        return None

    def _imports(self, scope):
        if scope in self._import_cache:
            return self._import_cache[scope]
        imports = dict(self._imports(scope[:-1])) if scope else {}
        body = self.functions[scope].body if scope else self.tree.body
        nodes = list(_executed_nodes(body))
        for node in nodes:
            if isinstance(node, ast.Import):
                imports.update({a.asname or a.name: a.name for a in node.names})
            elif isinstance(node, ast.ImportFrom):
                imports.update({a.asname or a.name:
                                f"{node.module}.{a.name}" for a in node.names})
        for node in nodes:
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                imports.pop(node.id, None)
        self._import_cache[scope] = imports
        return imports

    def _module_call(self, call, scope, module, function):
        f = call.func
        imports = self._imports(scope)
        if isinstance(f, ast.Name):
            return imports.get(f.id) == f"{module}.{function}"
        return (isinstance(f, ast.Attribute) and f.attr == function
                and isinstance(f.value, ast.Name)
                and imports.get(f.value.id) == module)

    def _invoked_parameters(self, fn, returned_wrapper=False):
        params = {a.arg for a in fn.args.args}
        nodes = list(_executed_nodes(fn.body))
        if returned_wrapper:
            returned = {n.value.id for n in nodes if isinstance(n, ast.Return)
                        and isinstance(n.value, ast.Name)}
            for child in fn.body:
                if isinstance(child, ast.FunctionDef) and child.name in returned:
                    nodes.extend(_executed_nodes(child.body))
        return {n.func.id for n in nodes if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name) and n.func.id in params}

    def _producer(self, expr, scope):
        if isinstance(expr, ast.Name):
            return self._resolve(expr.id, scope)
        # The native recorder returns a wrapper which calls its fn parameter;
        # an arbitrary function receiving a callable does not establish this.
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name):
            key = self._resolve(expr.func.id, scope)
            if key:
                fn = self.functions[key]
                invoked = self._invoked_parameters(fn, returned_wrapper=True)
                for param, arg in zip(fn.args.args, expr.args):
                    if param.arg in invoked:
                        return self._producer(arg, scope)
        return None

    def _visit_function(self, key):
        if key not in self.functions or key in self.reachable:
            return
        self.reachable.add(key)
        self._visit_body(self.functions[key].body, key)

    def _visit_body(self, body, scope):
        for node in _executed_nodes(body):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                key = self._resolve(node.func.id, scope)
                if key:
                    self._visit_function(key)
                    # A real local dispatcher invokes these callback parameters.
                    fn = self.functions[key]
                    invoked = self._invoked_parameters(fn)
                    supplied = dict(zip((a.arg for a in fn.args.args), node.args))
                    supplied.update({k.arg: k.value for k in node.keywords if k.arg})
                    for param in invoked:
                        value = supplied.get(param)
                        if isinstance(value, ast.Lambda):
                            self._visit_body([value.body], scope)
                        elif isinstance(value, ast.Name):
                            target = self._resolve(value.id, scope)
                            if target:
                                self._visit_function(target)
            self._dispatch(node, scope)

    def _dispatch(self, call, scope):
        # ThreadPoolExecutor.submit(gate, ...) invokes gate with these args.
        if (isinstance(call.func, ast.Attribute) and call.func.attr == "submit"
                and isinstance(call.func.value, ast.Name) and call.args):
            pool = call.func.value.id
            fn = self.functions[scope]
            pools = {item.optional_vars.id
                     for n in _executed_nodes(fn.body) if isinstance(n, ast.With)
                     for item in n.items
                     if isinstance(item.optional_vars, ast.Name)
                     and isinstance(item.context_expr, ast.Call)
                     and self._module_call(item.context_expr, scope,
                                           "concurrent.futures", "ThreadPoolExecutor")}
            if pool in pools:
                call = ast.Call(func=call.args[0], args=call.args[1:],
                                keywords=call.keywords, lineno=call.lineno)
        if self._module_call(call, scope, "step_preflight", "gate"):
            if not _dispatcher_calls_producer("gate"):
                return
            if len(call.args) < 5 or _literal(call.args[1]) != self.module:
                return
            site = _literal(call.args[2])
            from step_preflight import RUNNER_PLANS
            plan = RUNNER_PLANS.get(self.module)
            span = dict(plan.sites).get(site, ()) if plan else ()
            expr, args = call.args[4], call.args[5:]
        elif self._module_call(call, scope, "step_preflight", "dispatch"):
            if not _dispatcher_calls_producer("dispatch"):
                return
            if len(call.args) < 2:
                return
            span = _literal(call.args[0])
            span = (span,) if isinstance(span, str) else span
            if not isinstance(span, (list, tuple)) or not all(
                    isinstance(s, str) for s in span):
                return
            expr, args = call.args[1], call.args[2:]
        else:
            return
        key = self._producer(expr, scope)
        if not key or len(key) != 1:
            return
        # A generic analog callable selects a different producer by step_name.
        # The selected branch must agree with the dispatch site's A-step.
        params = [a.arg for a in self.functions[key].args.args]
        if "step_name" in params:
            index = params.index("step_name")
            selector = (_literal(args[index]) if len(args) > index else
                        next((_literal(k.value) for k in call.keywords
                              if k.arg == "step_name"), None))
            span = [s for s in span if isinstance(selector, str)
                    and selector.startswith(s + "_")]
        for sid in span:
            self.bindings.setdefault(key[0], {}).setdefault(sid, []).append(call.lineno)
        self._visit_function(key)

    def closure(self, function):
        """Local helpers actually called by this producer, never main's siblings."""
        seen, pending = set(), [(function,)]
        while pending:
            key = pending.pop()
            if key in seen or key not in self.functions:
                continue
            seen.add(key)
            for n in _executed_nodes(self.functions[key].body):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                    target = self._resolve(n.func.id, key)
                    if target:
                        pending.append(target)
        return seen


def _dispatcher_calls_producer(name):
    """The imported dispatcher really invokes fn with the forwarded arguments."""
    path = _HERE / "step_preflight.py"
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    key = (str(path), hashlib.sha256(text.encode()).hexdigest(), name)
    if key not in _AST_CACHE:
        functions = {n.name: n for n in ast.parse(text).body
                     if isinstance(n, ast.FunctionDef)}
        pending, seen, proven = [(name, {"fn": "fn", "args": "args", "kwargs": "kwargs"})], set(), False
        while pending and not proven:
            current, bound = pending.pop()
            identity = (current, tuple(sorted(bound.items())))
            if current not in functions or identity in seen:
                continue
            seen.add(identity)
            for n in _executed_nodes(functions[current].body):
                if not isinstance(n, ast.Call) or not isinstance(n.func, ast.Name):
                    continue
                if (bound.get(n.func.id) == "fn"
                        and any(isinstance(a, ast.Starred) and isinstance(a.value, ast.Name)
                                and bound.get(a.value.id) == "args" for a in n.args)
                        and any(k.arg is None and isinstance(k.value, ast.Name)
                                and bound.get(k.value.id) == "kwargs" for k in n.keywords)):
                    proven = True
                    break
                helper = functions.get(n.func.id)
                if helper:
                    supplied = dict(zip((a.arg for a in helper.args.args), n.args))
                    supplied.update({k.arg: k.value for k in n.keywords if k.arg})
                    forwarded = {param: bound[value.id] for param, value in supplied.items()
                                 if isinstance(value, ast.Name) and value.id in bound}
                    if set(forwarded.values()) == {"fn", "args", "kwargs"}:
                        pending.append((helper.name, forwarded))
        _AST_CACHE[key] = proven
    return _AST_CACHE[key]


def _runner_ast(module: str):
    path = _HERE / f"{module}.py"
    if f"{module}.py" not in _RUNNER_FILES or not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="ignore")
    dispatcher = _HERE / "step_preflight.py"
    key = (str(path), hashlib.sha256(text.encode()).hexdigest(),
           hashlib.sha256(dispatcher.read_bytes()).hexdigest() if dispatcher.is_file() else None)
    if key not in _AST_CACHE:
        _AST_CACHE[key] = _RunnerSource(module, text)
    return _AST_CACHE[key]


def _canonical_step(step_id):
    import yaml
    text = _DEFAULT_FLOW.read_text(encoding="utf-8")
    key = ("canonical", str(_DEFAULT_FLOW), hashlib.sha256(text.encode()).hexdigest())
    if key not in _AST_CACHE:
        _AST_CACHE[key] = {str(s["id"]): s for s in _iter_steps(yaml.safe_load(text))}
    return _AST_CACHE[key].get(step_id)


def _obligation_matches(step, required_outputs):
    """Bind the requested obligation, allowing existing relative-path shorthand."""
    native = [p.strip() for ro in step.get("required_outputs", [])
              for p in str(ro).split(" OR ")]
    if required_outputs is None:
        return bool(native)
    requested = [p.strip() for ro in required_outputs for p in str(ro).split(" OR ")]
    return bool(requested) and all(
        len(p.split("/")) >= 2 and ".." not in p.split("/")
        and any(n == p or n.endswith("/" + p) for n in native)
        for p in requested)


def _librelane_classes_for(step_id: str) -> set:
    """The LibreLane step classes the import maps to this flow step."""
    try:
        sys.path.insert(0, str(_HERE))
        import librelane_import as _li                     # noqa: PLC0415
    except Exception:                                      # noqa: BLE001
        return set()
    return {r.step for r in _li.IMPORT_RULES if str(r.flow_step) == step_id}


def verify_executor(entry: str, step_id: str, required_outputs=None):
    """`(True, why)` when `entry` is PROVEN, by parse, to execute `step_id`.

    A producer must be reachable from main and handed to an actual dispatcher
    for this step/span. The requested outputs must belong to that canonical
    obligation. Definitions, arbitrary callback arguments and source/manifest
    name occurrences cannot establish this association. LibreLane additionally
    requires a live tool-chain dispatch in the bound producer's call closure.
    This is structural wiring, never proof that a particular run completed.
    """
    step_id = str(step_id)
    m = _RUNNER_EXECUTOR.match(entry)
    if m:
        facts = _runner_ast(m.group("module"))
        if facts is None:
            return False, f"{m.group('module')} is not a flow runner"
        func = m.group("func")
        if (func,) not in facts.functions:
            return False, f"{m.group('module')} defines no {m.group('func')}"
        sites = facts.bindings.get(func, {}).get(step_id)
        if not sites:
            return False, f"no reachable dispatcher binds {entry} to step {step_id}"
        step = _canonical_step(step_id)
        if not step or not _obligation_matches(step, required_outputs):
            return False, f"outputs do not belong to the canonical step {step_id} obligation"
        return True, f"step {step_id} producer dispatched at {m.group('module')}.py:{sites[0]}"
    m = _LIBRELANE_EXECUTOR.match(entry)
    if m:
        cls = m.group("cls")
        mapped = cls in _librelane_classes_for(step_id)
        step = _canonical_step(step_id)
        if not step or not _obligation_matches(step, required_outputs):
            return False, f"outputs do not belong to the canonical step {step_id} obligation"
        if not mapped and entry not in step.get("mcp_tools", []):
            return False, f"no LibreLane import rule maps {cls} to step {step_id}"
        for f in _RUNNER_FILES:
            facts = _runner_ast(f[:-3])
            if not facts:
                continue
            for producer, spans in facts.bindings.items():
                if step_id not in spans:
                    continue
                for key in facts.closure(producer):
                    body = list(_executed_nodes(facts.functions[key].body))
                    chains = [n for n in body if isinstance(n, ast.Call)
                              and facts._module_call(n, key, "librelane_contract", "run_chain")]
                    if mapped and chains:
                        return True, f"{f}.{producer} dispatches step {step_id}'s LibreLane chain"
                    # Plugin classes must flow into the executed steps list;
                    # a producer/report string literal elsewhere proves nothing.
                    lists = {n.func.value.id for n in body if isinstance(n, ast.Call)
                             and isinstance(n.func, ast.Attribute) and n.func.attr == "append"
                             and isinstance(n.func.value, ast.Name)
                             and n.args and _literal(n.args[0]) == cls}
                    for chain in chains:
                        if len(chain.args) >= 3 and lists.intersection(
                                n.id for n in ast.walk(chain.args[2]) if isinstance(n, ast.Name)):
                            return True, f"{f}.{producer} passes {cls} in step {step_id}'s executed chain"
        return False, (f"no LibreLane import rule maps {cls} to step "
                       f"{step_id} with a bound live tool-chain dispatch")
    return False, ("not an executor form (<runner>.<function> or "
                   "librelane:<StepClass>); a tool NAME wires nothing")


def classify(doc, runner_text: str):
    steps = _iter_steps(doc)
    rows = []
    for s in steps:
        sid = str(s.get("id"))
        # umbrella/stage container rows carry no required_outputs of their own
        ro = s.get("required_outputs") or []
        mcp = s.get("mcp_tools") or []
        progs = s.get("programs") or []
        skills = s.get("skills") or []
        if not ro and str(s.get("stage", "")).startswith("stage_") is False \
                and sid.lower().startswith("stage") is False and not mcp \
                and not progs and not skills:
            # bare container / grouping node — skip
            continue
        out_sigs = _output_signals(ro)
        wired_by = None
        matched = None
        # 1) does a runner reference this step's OUTPUT?
        for sig in sorted(out_sigs, key=len, reverse=True):
            if sig and sig in runner_text:
                wired_by, matched = "output", sig
                break
        # 2) or name an EXECUTOR that is proven by PARSE to run it?
        #
        # A NAME IN RUNNER TEXT IS NOT WIRING (audit §3.23-1, R-0929-TOOL-DEFAULT).
        # This used to credit a step whenever one of its `mcp_tools:` names
        # appeared anywhere in the runner source. 25 step slots named mcp-eda
        # handlers (eda_synth, eda_sta, eda_gds, eda_extraction, ...) that no
        # runner calls; the names occurred in comments and error strings, so the
        # steps read WIRED to a parallel flow that is never gated. An entry now
        # counts only when `verify_executor` proves it: a `<runner>.<function>`
        # the runner module DEFINES and CALLS, or a `librelane:<StepClass>` the
        # LibreLane import maps to this very step. Anything else is reported
        # (`unverified_executors`) and wires nothing. Definitions/calls alone
        # are insufficient: the actual dispatch must bind THIS step/span and
        # its declared output obligation, including wrapped callbacks.
        verified, unverified = [], []
        for t in mcp:
            ok, why = verify_executor(str(t), sid, ro)
            (verified if ok else unverified).append(
                str(t) if ok else f"{t}: {why}")
        if not wired_by and verified:
            wired_by, matched = "executor", verified[0]
        # 3) or DELEGATE to the plugin PROGRAM it declares?
        #
        # THE THIRD WAY A STEP IS EXECUTED, AND THIS GATE COULD NOT SEE IT.
        # A runner may drive a step by shelling out to a program named in the
        # step's `programs:` list, and let THAT program write the step's
        # declared outputs. The runner then contains neither the output path
        # (the program builds it) nor an mcp tool name (there is none), so both
        # signals above miss and a fully-wired step is reported ORPHANED.
        #
        # MEASURED on origin/main a4caccefe against the shipped flow:
        #
        #   26.5ic die finishing   `phase3_one_shot_runner._die_finishing`
        #                          invokes `die_finishing_gen.py` — 5 references
        #   37.5ip hardmacro gen   `phase3_one_shot_runner.
        #                          step_digital_hardmacro_gen` invokes
        #                          `digital_hardmacro_gen.py` — 8 references
        #
        # Both were listed as digital-main-track steps that "can only ever be
        # MISSING" while a real run executes them. A gate that reports two
        # working steps as broken is a gate people learn to read past, which is
        # the same end state as not having it.
        #
        # THE DISCIPLINE IS THE ONE ALREADY APPLIED TO `skills`, AND FOR THE
        # SAME REASON. A declared name is not a wiring: step 12 lists
        # `synth-doctor`, no runner invokes it, and that step is correctly an
        # orphan. So a `programs:` entry counts only when a RUNNER ACTUALLY
        # REFERENCES IT. Declaring `programs: [foo_gen]` and wiring nothing
        # still comes out ORPHANED — measured, on this same flow: 15.5ic
        # (`pad_assignment_gen`, `pad_ring_gen`) and 37.5ic (`tapeout_docs_gen`)
        # appear 0 times in every runner and stay in the ORPHANED set.
        if not wired_by:
            for prog in progs:
                if prog and prog in runner_text:
                    wired_by, matched = "program", prog
                    break
        stage = str(s.get("stage", ""))
        is_mfg = stage == "stage5_manufacturing" or sid in {
            "40", "41", "42", "43", "44"}
        is_cond_track = (re.fullmatch(r"[AM]\d+", sid) is not None
                         or stage in ("stage_analog", "stage_mixed_signal"))
        disclosed_skip = (not wired_by
                          and _has_disclosed_skip(_skip_anchors(ro), runner_text))
        if wired_by:
            cls = "WIRED"
        elif not ro:
            cls = "CONTAINER"
        elif is_mfg:
            # post-silicon: fab/sort/package/test/qual happen at the foundry/lab.
            # There is no software executor by nature — only an ATTESTATION gate.
            cls = "SILICON-EXTERNAL"
        elif is_cond_track:
            # analog / mixed-signal track: runs only when that track is active;
            # no digital runner drives it. A real gap for A/M designs, but
            # conditional — surfaced separately, not a digital-main-track orphan.
            cls = "CONDITIONAL-TRACK"
        elif disclosed_skip:
            # NO real producer, BUT a runner consciously writes a skip-sentinel
            # naming the capability gap (formal_not_run.json, sdf_sim_skipped.json).
            # This is HONEST — the step is not silently dropped; the compliance
            # gate maps it to SKIPPED-CONDITION. Acceptable, NOT an orphan.
            cls = "DISCLOSED-SKIP"
        elif (skills and not progs and not mcp
              and any(sk and sk in runner_text for sk in skills)):
            # A genuine AI-authoring handoff: the step declares ONLY skills AND
            # a runner actually WAIVES to one of them (references the skill name,
            # e.g. spec-to-rtl @ 12 refs). A step that merely LISTS a skill no
            # runner ever invokes (step 12 post-DFT lists synth-doctor, 0 refs)
            # is NOT a real handoff — it falls through to ORPHANED below.
            cls = "SKILL-ONLY-AI"
        else:
            # THE DISEASE: a digital main-track step whose output NO runner
            # produces AND which is not even consciously skipped — it can only
            # ever be MISSING (silent skip). Steps 11 DFT / 12 post-DFT /
            # 13 LEC / 30 post-layout SPICE. Wire an executor or a skip-sentinel.
            cls = "ORPHANED"
        rows.append({
            "id": sid, "name": s.get("name", ""),
            "required_outputs": ro, "mcp_tools": mcp,
            "skills": skills, "classification": cls,
            "wired_by": wired_by, "matched_signal": matched,
            "verified_executors": verified,
            "unverified_executors": unverified,
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--flow-def", default=str(_DEFAULT_FLOW))
    ap.add_argument("--json")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 on any ORPHANED step (default: advisory exit 0)")
    args = ap.parse_args()

    import yaml
    doc = yaml.safe_load(Path(args.flow_def).read_text())
    rows = classify(doc, _load_runner_text())

    def _by(c):
        return [r for r in rows if r["classification"] == c]
    orphaned = _by("ORPHANED")
    skill_ai = _by("SKILL-ONLY-AI")
    wired = _by("WIRED")
    silicon = _by("SILICON-EXTERNAL")
    cond = _by("CONDITIONAL-TRACK")
    disclosed = _by("DISCLOSED-SKIP")

    if args.json:
        Path(args.json).write_text(json.dumps({
            "counts": {"total": len(rows), "wired": len(wired),
                       "skill_only_ai": len(skill_ai), "orphaned": len(orphaned),
                       "silicon_external": len(silicon),
                       "conditional_track": len(cond),
                       "disclosed_skip": len(disclosed)},
            "orphaned": orphaned, "conditional_track": cond,
            "disclosed_skip": disclosed, "rows": rows,
        }, indent=2, ensure_ascii=False))

    print("=== flow step-executor coverage ===")
    print(f"steps={len(rows)}  WIRED={len(wired)}  SKILL-ONLY-AI={len(skill_ai)}"
          f"  DISCLOSED-SKIP={len(disclosed)}  CONDITIONAL-TRACK={len(cond)}"
          f"  SILICON-EXTERNAL={len(silicon)}  ORPHANED={len(orphaned)}")
    if disclosed:
        print("\nDISCLOSED-SKIP (no real producer, but a runner consciously "
              "writes a skip-sentinel naming the gap — honest, not silent):")
        for r in disclosed:
            print(f"  [{r['id']}] {r['name'][:56]}")
    if orphaned:
        print("\nORPHANED (digital main-track step whose output NO runner "
              "produces — can only ever be MISSING; wire an executor):")
        for r in orphaned:
            ex = ",".join(r["mcp_tools"]) or ",".join(r["skills"]) or "(none)"
            print(f"  [{r['id']}] {r['name'][:52]:52} executor={ex}")
    if cond:
        print("\nCONDITIONAL-TRACK (analog/mixed — no digital runner drives it; "
              "a real gap for A/M designs):")
        for r in cond:
            print(f"  [{r['id']}] {r['name'][:56]}")
    verdict = "PASS" if not orphaned else ("FAIL" if args.strict else "ADVISORY")
    print(f"\nVERDICT: {verdict}")
    return 1 if (orphaned and args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
