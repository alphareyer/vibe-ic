#!/usr/bin/env python3
"""_invocation_credit.py — ONE resolver for "does this reference RUN the gate?"

WHY THIS MODULE EXISTS (vibe-ic#2169)
=====================================
Two instruments in this tree audit the same thing — whether anything actually
runs a gate — and each carried its OWN answer to the load-bearing question:

    gate_is_wired_check.py            "is this gate consulted by any automatic
                                       verdict?"                    (a register)
    checker_execution_wiring_audit.py "does anything but this checker's own
                                       unit test run it?"           (a register)

Their POPULATIONS had to be reconciled once already, under vibe-ic#1130.
vibe-ic#2169 is the same divergence one level in, on the RULE, and it was
measured: `gate_is_wired` has refused a DEAD import since `invocation.v1`,
while `checker_execution_wiring_audit._py_evidence` did this —

    if isinstance(node, ast.Import):
        for alias in node.names:
            invoked.add(alias.name.split(".")[0])

— adding the name on the IMPORT STATEMENT ALONE, never asking whether the
module was referenced at all. By the time vibe-ic#2141 landed `invocation.v2`
in `gate_is_wired` the gap was two rules wide: one instrument required the
reference to reach the gate's VERDICT, the other required nothing.

THE FIX IS NOT A THIRD COPY OF THE RULE. Two implementations of one predicate
diverge again the next time either is touched; that is the defect, and its
instance is only how it surfaced. So the rule lives here, once, and both
instruments call it.

WHAT MOVED, AND FROM WHERE
--------------------------
`verdict_path`, `_VERDICT_WORDS`, `_is_verdict_word`, `_target_names`,
`_called_gate`, `_verdict_consumers`, `_scope_consumers` and `_credits` are
vibe-ic#2141's `invocation.v2`, MOVED here from `gate_is_wired_check.py` and
otherwise unchanged — their docstrings are that lane's, including the measured
false accusations each clause was written from, and they are worth reading
before touching any of this. `credited()` below is the composition
`py_invocations` used to perform inline: bind the imports, read what the source
references off each one, ask `_verdict_consumers` who reads an answer, and
adjudicate. Neither caller composes it any more, so neither can compose it
differently.

    gate_is_wired_check            `py_invocations` -> `credited`
    checker_execution_wiring_audit `_py_evidence`   -> `credited`

THE RULE, IN ONE PARAGRAPH. An invocation credits a gate when it reaches
`main`; or when it reaches a STAGE whose answer the caller CONSUMES; or, for a
gate with no `main` at all, any public definition. It credits NOTHING when the
caller chains two or more stages and reads no verdict — that caller has
re-implemented the check rather than run it — and nothing for a module
CONSTANT, a `_`-private helper, a bare alias handed on, or a dead import.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Callable, Dict, NamedTuple, Optional, Set, Tuple

#: The rule this module implements, written into the register of any instrument
#: that measures under it. A POPULATION CHANGE — the instrument starting to
#: measure a different question — is distinguishable from a debt that grew only
#: because the register records which rule produced it. The id is
#: vibe-ic#2141's, unchanged, because this is that rule and not a new one:
#: `gate_is_wired_baseline.json` already records `invocation.v2` and must go on
#: comparing like with like.
RULE_ID = "invocation.v2"

#: What `checker_execution_wiring_audit` measured under BEFORE vibe-ic#2169: an
#: import credited the module unconditionally, referenced or not. Kept — and
#: reachable ONLY from that gate's one-shot `--migrate-rule-id` path, never from
#: any gate path — so a migration can say which additions the RULE CHANGE
#: produced and which were already debt under the old rule. See `RULES`.
PRIOR_RULE_ID = "credit_on_import.v0"


def verdict_path(gate_src: str) -> Tuple[Set[str], bool]:
    """`(stages, composed)` — the PUBLIC symbols on a gate's verdict path, and
    whether the gate COMPOSES that verdict itself (it defines `main`).

    vibe-ic#2141. `invocation.v1` credited a gate for ANY reference to ANY
    symbol it exports, so a module that is BOTH a library AND a gate read as
    wired on the strength of its library use alone. The register could not tell
    "imported as a library" from "run as a gate" — #2080's shape one rung up:
    credit-by-import.

    A gate's verdict entry point is `main`, which is what a flow gate clause, a
    shell argv and a spawned entry literal all ultimately name. `main` composes
    the verdict out of the module's own definitions; those are its STAGES, and
    this returns them: every PUBLIC function or class defined in the module and
    reached from `main` through the module's own references, transitively.
    `_`-private helpers are traversed but never returned, and module CONSTANTS
    are not definitions at all — referencing either is library use by
    construction.

    TWO SHAPES THIS HAD TO GET RIGHT, each found by a false accusation the
    first version made:

    * A GATE WITH NO `main`. `url_oracle_guard` and `spec_conformance_gate`
      have no CLI and never did: their PUBLIC API *is* the check surface, there
      is no composition for a caller to re-implement, and denying every
      reference would have accused two gates their callers demonstrably run.
      With no `main`, every public definition is an entry.

    * `check_text`. `benchmark/cvdp_gate._structural_finding_gate` consumes
      exactly that name from four gates and BLOCKS a delivery on any
      ERROR-severity finding it returns — a verdict, produced by the gate. In
      `valid_ready_independence_check` it is NOT on `main`'s path (the CLI
      walks files through `audit_file`), so the traversal alone would have
      accused a gate the benchmark runs on every delivery. It is named here as
      the driver interface it is.
    """
    try:
        tree = ast.parse(gate_src)
    except SyntaxError:
        return set(), True
    defined: Dict[str, ast.AST] = {}
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined[n.name] = n
    extra = {"check_text"} & set(defined)
    if "main" not in defined:
        return {n for n in defined if not n.startswith("_")} | extra, False
    seen = {"main"}
    stack = ["main"]
    while stack:
        node = defined.get(stack.pop())
        if node is None:
            continue
        for c in ast.walk(node):
            if isinstance(c, ast.Name) and c.id in defined and c.id not in seen:
                seen.add(c.id)
                stack.append(c.id)
    return ({n for n in seen if not n.startswith("_") and n != "main"} | extra,
            True)


#: A field name whose value IS the gate's answer. Reading one of these out of
#: a stage's return is the caller CONSUMING a verdict the gate produced; the
#: caller that instead takes data and decides pass/fail from parts has
#: re-implemented the gate. See `_credits`.
_VERDICT_WORDS = frozenset((
    "verdict", "passed", "status", "ok", "rc", "returncode", "exit_code",
    "findings", "violations", "blocked", "result", "severity"))


def _is_verdict_word(name: object) -> bool:
    """`overall_verdict`, `exit_code`, `n_violations` — a verdict field is not
    always spelled as the bare word. MEASURED: `signoff_ladder_run` reads
    `rep.overall_verdict` off `mpw_precheck_result_gate.evaluate`, and exact
    matching called that gate unconsumed."""
    if not isinstance(name, str):
        return False
    low = name.lower()
    return any(w in low for w in _VERDICT_WORDS)


def _target_names(node) -> Set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        out: Set[str] = set()
        for e in node.elts:
            out |= _target_names(e)
        return out
    return set()


def _called_gate(node, bound: Dict[str, Tuple[str, Optional[str]]],
                 stages: Dict[str, Tuple[Set[str], bool]]) -> Optional[str]:
    """The gate whose STAGE this Call invokes, or None."""
    if not isinstance(node, ast.Call):
        return None
    fn = node.func
    if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
        b = bound.get(fn.value.id)
        if b and b[1] is None and fn.attr in stages.get(b[0], (set(), True))[0]:
            return b[0]
    elif isinstance(fn, ast.Name):
        b = bound.get(fn.id)
        if b and b[1] is not None and b[1] in stages.get(b[0], (set(), True))[0]:
            return b[0]
    return None


def _verdict_consumers(tree, parent, bound, stages) -> Set[str]:
    """The gates whose RETURN VALUE this source reads AS A VERDICT.

    vibe-ic#2141, ruling 2. The question a wiring register must ask is not how
    many of a gate's functions a caller touches — that proxy could not be
    defended at its own boundary — it is WHO PRODUCES THE VERDICT. A caller
    that hands inputs to the gate's core and takes the core's answer is running
    the gate, however many input-preparation calls it also makes. A caller that
    takes the gate's DATA and decides pass/fail itself has re-implemented the
    gate, and #2080's ladder exists to stop that reading as wired.

    MEASURED, the two call sites that decided this rule:

        phase3_one_shot_runner.py:36652
            _grid_um = _dmg.read_mfg_grid_um(pdk.tech_lef)      # input prep
            _src     = _dmg.classify_def(_routed_def.read_text(...), _grid_um)
            extras["offgrid_source"] = _src.get("verdict")      # CONSUMED

        benchmark/cvdp_gate.py:2828
            rc, report = _ppa_area_run(original=..., optimized=..., top=...)
            verdict = report.get("verdict")
            if rc == 1 and verdict == "BLOCK": return False, ...  # CONSUMED

    Both are WIRED, and the ">= 2 stages" proxy had called both library use.
    Against them, the shape this rule refuses:

        design_one_shot_runner.py:6523 (as it stood before vibe-ic#2103)
            _sections = _lesson_consumed.parse_digest(...)
            _matches  = _lesson_consumed.match_sections(_spec_scored, _sections)
            strong_titles = [m["section"] for m in _matches if m["strong"]]
            _rec = _lesson_consumed.build_scoring_record(...); write_json(...)

    — three stages in, a list comprehension deciding what is strong, a file
    written, and not one verdict read. The runner was the second implementation
    of that check, which is why #2103 had to wire `main` for the gate to run at
    all.
    """
    out: Set[str] = set()
    # PER SCOPE, NEVER WHOLE-FILE. `verdict` and `rep` are the names a ladder
    # gives EVERY tier's result: `signoff_ladder_run` binds
    # `verdict, rep = ag.evaluate(...)` in one function and
    # `verdict, rep = th.evaluate(...)` in another. A single file-wide map is
    # last-writer-wins, so the branch that reads `verdict == "FAIL"` was
    # attributed to `thermal_screen_check` and `aging_derate_sta_check` read as
    # never consumed — a false accusation, measured, against a gate the ladder
    # runs on every signoff.
    scopes = [n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    scopes.append(tree)
    for scope in scopes:
        out |= _scope_consumers(scope, parent, bound, stages)
    return out


def _scope_consumers(tree, parent, bound, stages) -> Set[str]:
    """The gates this ONE scope both runs and reads an answer from.

    Returns the gates that are NOT re-implemented here: a gate is
    re-implemented when the scope CHAINS two or more of its stages — feeds one
    stage's return into another stage of the SAME gate — and never reads a
    verdict out of any of them. That is the scope reproducing `main`'s
    composition and judging for itself, which is exactly what
    `design_one_shot_runner` did to `lesson_consumption_check` before
    vibe-ic#2103 and what `regression_issue_intake_check` does to
    `acceptance_evidence_in_fix_comment_check` today. A single core call whose
    answer the scope reads is the gate RUNNING, however the answer is spelled.
    """
    owners: Dict[str, str] = {}
    out: Set[str] = set()
    chained: Set[str] = set()
    called: Set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            g = _called_gate(n.value, bound, stages)
            if g:
                for arg in ast.walk(n.value):
                    if (isinstance(arg, ast.Name)
                            and owners.get(arg.id) == g):
                        chained.add(g)     # a stage fed by another stage
                for t in n.targets:
                    for nm in _target_names(t):
                        owners[nm] = g
        elif isinstance(n, (ast.Return, ast.Await)) and getattr(n, "value", None):
            g = _called_gate(n.value, bound, stages)
            if g:
                out.add(g)
        elif isinstance(n, ast.Call):
            g = _called_gate(n, bound, stages)
            if g:
                # TRACKED EVEN WHEN NOTHING IS ASSIGNED. `flow_compliance_check`
                # writes `_cov0.analyze(_r0, _g0).get("ordering_violations")`,
                # a stage call read for a verdict field with no name in
                # between; keying this off assignments alone accused a gate
                # whose only caller reads its answer on one line.
                called.add(g)
                par = parent.get(id(n))
                if isinstance(par, ast.Attribute):
                    if _is_verdict_word(par.attr):
                        out.add(g)
                    elif par.attr == "get":
                        c2 = parent.get(id(par))
                        if (isinstance(c2, ast.Call) and c2.args
                                and isinstance(c2.args[0], ast.Constant)
                                and _is_verdict_word(c2.args[0].value)):
                            out.add(g)
                elif isinstance(par, ast.Subscript):
                    sl = par.slice
                    if isinstance(sl, ast.Constant) and _is_verdict_word(sl.value):
                        out.add(g)
            # `sys.exit(gate.core(...))` — the caller's whole outcome IS it.
            f = n.func
            if ((isinstance(f, ast.Attribute) and f.attr == "exit")
                    or (isinstance(f, ast.Name) and f.id == "exit")):
                for a in n.args:
                    g = _called_gate(a, bound, stages)
                    if g:
                        out.add(g)
            # HANDED TO A DRIVER. `benchmark/cvdp_gate` does
            #     from fsm_state_output_check import check_text as _f
            #     ... _structural_finding_gate(_f, label, completion)
            # and that driver calls `_f` and BLOCKS on any ERROR finding it
            # returns. The gate produces the verdict and the driver consumes
            # it, with no Call node of its own at this site — measured: four
            # gates the benchmark runs on every delivery, accused by a rule
            # that looked only for a call.
            for a in list(n.args) + [k.value for k in n.keywords]:
                if isinstance(a, ast.Name):
                    b = bound.get(a.id)
                    if (b and b[1] is not None
                            and b[1] in stages.get(b[0], (set(), True))[0]):
                        out.add(b[0])
    for nm, g in owners.items():
        # THE CALLER NAMED IT THE VERDICT. `verdict, rep = ag.evaluate(...)`,
        # `rc, report = _ppa_area_run(...)` — a binding is a statement about
        # what the value IS.
        if _is_verdict_word(nm):
            out.add(g)
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)):
            continue
        g = owners.get(n.id)
        if not g or g in out:
            continue
        par = parent.get(id(n))
        # A VERDICT FIELD READ OFF THE RESULT, and nothing looser. `if _sec:`
        # is a None-check, not a verdict; an early version counted every
        # branch on a stage result and re-credited four gates whose callers
        # demonstrably assemble the answer themselves.
        if isinstance(par, ast.Attribute):
            if _is_verdict_word(par.attr):
                out.add(g)
            elif par.attr == "get":
                call = parent.get(id(par))
                if (isinstance(call, ast.Call) and call.args
                        and isinstance(call.args[0], ast.Constant)
                        and _is_verdict_word(call.args[0].value)):
                    out.add(g)
        elif isinstance(par, ast.Subscript):
            sl = par.slice
            if isinstance(sl, ast.Constant) and _is_verdict_word(sl.value):
                out.add(g)
        elif isinstance(par, ast.Return):
            out.add(g)
    for g in (called | set(owners.values())) - chained:
        # Ran a stage, chained nothing: the gate composed its own answer and
        # this scope took it.
        out.add(g)
    return out


def _credits(refs: Set[str], stages: Set[str], composed: bool = True,
             consumed: bool = False) -> Optional[str]:
    """Does referencing `refs` of a gate INVOKE the gate? `how`, or None.

    THREE WAYS TO REACH A GATE'S VERDICT, and nothing else counts:

    * `main` is referenced — the verdict entry point itself, the thing a flow
      gate clause, a shell argv and a spawned entry literal all name.

    * A STAGE is referenced AND ITS ANSWER IS CONSUMED (`_verdict_consumers`).
      The gate produced the verdict; the caller read it. One extra
      input-preparation call does not change who produced it.

    * The gate defines NO `main` at all. `url_oracle_guard` and
      `spec_conformance_gate` have no CLI and never did: their public API IS
      the check surface, they compose nothing, and there is nothing for a
      caller to re-implement. Denying every reference would have accused two
      gates their callers demonstrably run.

    Everything else is LIBRARY USE and credits nothing: a module constant, a
    `_`-private helper (`signoff_audit` takes `MIN_REASON_LEN` and
    `_is_placeholder` from `waivers_schema_check`; `l13_bringup_contract_check`
    reads `hardware_pass_attestation_check._CRITERIA` through `getattr`), a
    bare module handed on, a dead import, and — the case vibe-ic#2141 is about
    — a caller that takes the gate's DATA and assembles the pass/fail itself.
    """
    if "main" in refs:
        return "verdict entry (main)"
    hit = refs & stages
    if not hit:
        return None
    if not composed:
        return f"library gate API ({', '.join(sorted(hit))})"
    if consumed:
        return f"verdict consumed ({', '.join(sorted(hit))})"
    return None


def credit_on_import(refs: Set[str], stages: Set[str], composed: bool = True,
                     consumed: bool = False) -> Optional[str]:
    """The SUPERSEDED rule: an import credits, referenced or not.

    This is `checker_execution_wiring_audit._py_evidence` as it shipped up to
    v1.19.32, and it is the defect vibe-ic#2169 names. It is kept callable for
    exactly one purpose: a `--migrate-rule-id` run measures BOTH rules so it can
    add to a register only the names the RULE CHANGE made visible, and leave a
    name that was already unwired under the old rule where it belongs —
    outstanding, and still failing the gate. Nothing on any gate path may reach
    it; `test_issue2169_credit_reaches_the_verdict_entry` pins that.
    """
    return "imported"


class Rule(NamedTuple):
    """A credit rule and the one thing about it a caller cannot infer.

    `dead_import_credits` is not a preference — it decides whether a module
    that is bound and never referenced is even ASKED about. Under
    `invocation.v2` it is not, because a dead import reaches nothing; under the
    rule it replaced it is, because that rule credited the statement itself.
    """
    id: str
    credits: Callable[..., Optional[str]]
    dead_import_credits: bool


RULES = {RULE_ID: Rule(RULE_ID, _credits, False),
         PRIOR_RULE_ID: Rule(PRIOR_RULE_ID, credit_on_import, True)}


def bindings(tree: "ast.AST", names) -> Dict[str, Tuple[str, Optional[str]]]:
    """`alias -> (gate, symbol)` for every import of a gate in `names`.

    `symbol` is None for `import g [as X]`, where what is referenced can only
    be read off the ATTRIBUTE at the use site.
    """
    bound: Dict[str, Tuple[str, Optional[str]]] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for al in n.names:
                if al.name in names:
                    bound.setdefault(al.asname or al.name, (al.name, None))
        elif isinstance(n, ast.ImportFrom):
            if n.module in names and not n.level:
                for al in n.names:
                    bound.setdefault(al.asname or al.name, (n.module, al.name))
    return bound


def imported_stems(tree: "ast.AST") -> Set[str]:
    """Every module name this source imports, spelled as `bindings` matches it.

    For a caller that has no population to hand — `_py_evidence` called
    directly, as its own tests call it — so the rule still decides every import
    rather than silently none of them.
    """
    out: Set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {al.name for al in n.names}
        elif isinstance(n, ast.ImportFrom):
            if n.module and not n.level:
                out.add(n.module)
    return out


def credited(tree: "ast.AST", names, stages_of, rule: "Rule" = None,
             parent: Dict[int, "ast.AST"] = None) -> Dict[str, str]:
    """`{gate: how}` for the gates this python source INVOKES through an import.

    THE WHOLE PYTHON-SIDE DECISION, in one place (vibe-ic#2169). It was
    performed inline in `gate_is_wired_check.py::py_invocations` and, in a
    second and much weaker form, in
    `checker_execution_wiring_audit.py::_py_evidence`. Both now call this, so
    the two registers cannot answer differently about the same file.

    `names` is the gate population — nothing outside it is bound, which is also
    what keeps `stages_of` from reading the source of every stdlib module the
    corpus imports. `stages_of` is a callable `name -> (stages, composed)`;
    `Stages` is the caching one both callers use. `parent` may be handed in by
    a caller that has already built the child->parent map for its own reasons,
    which `py_invocations` has.
    """
    rule = rule or RULES[RULE_ID]
    out: Dict[str, str] = {}
    bound = bindings(tree, names)
    if not bound:
        return out
    if parent is None:
        parent = {}
        for node in ast.walk(tree):
            for kid in ast.iter_child_nodes(node):
                parent[id(kid)] = node
    import_lines = {n.lineno for n in ast.walk(tree)
                    if isinstance(n, (ast.Import, ast.ImportFrom))}
    refs: Dict[str, Set[str]] = {}
    if rule.dead_import_credits:
        # The superseded rule credits the STATEMENT, so a module that is bound
        # and never referenced still has to be adjudicated. Under
        # `invocation.v2` it is not asked about at all, which is the difference
        # the two rules exist to expose.
        refs = {g: set() for g, _ in bound.values()}
    for n in ast.walk(tree):
        if (isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                and n.lineno not in import_lines):
            b = bound.get(n.id)
            if not b:
                continue
            g, sym = b
            seen = refs.setdefault(g, set())
            if sym is not None:
                seen.add(sym)
            else:
                # `import g as X` — the symbol is the ATTRIBUTE. A bare `X`
                # with no attribute (handed to `getattr`, or to another
                # module) names nothing in particular and credits nothing.
                par = parent.get(id(n))
                if isinstance(par, ast.Attribute):
                    seen.add(par.attr)
    stages = {g: stages_of(g) for g, _ in bound.values()}
    consumers = _verdict_consumers(tree, parent, bound, stages)
    for g, seen in refs.items():
        st, composed = stages.get(g, (set(), True))
        how = rule.credits(seen, st, composed, g in consumers)
        if how:
            out.setdefault(g, how)
    return out


class Stages:
    """`stem -> (stages, composed)`, read from each gate's own source ONCE.

    Both instruments ask this of hundreds of names over corpora of thousands of
    files, and a re-parse per question costs more than the walk it serves, so
    the answer is cached per programs directory. A stem with no `<stem>.py`
    resolves to `(set(), True)`, which credits nothing: this resolver answers
    about GATES, and a name that is not one has no verdict path to reach.
    """

    __slots__ = ("_programs", "_cache")

    def __init__(self, programs: Path):
        self._programs = Path(programs)
        self._cache: Dict[str, Tuple[Set[str], bool]] = {}

    def __call__(self, stem: str) -> Tuple[Set[str], bool]:
        hit = self._cache.get(stem)
        if hit is None:
            try:
                hit = verdict_path(
                    (self._programs / f"{stem}.py").read_text(errors="replace"))
            except OSError:
                hit = (set(), True)
            self._cache[stem] = hit
        return hit
