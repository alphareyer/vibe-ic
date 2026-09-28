"""W1 (`--librelane` v1): the flags, the knob map, the child argv, the gate.

Contracts:
  1. Every gated runner's REAL parser takes `--librelane` and the reserved
     `--orfs` (mutually exclusive), and every other option it has carries a
     disposition in `_impl_flow.KNOBS` -- a new option without one is red.
  2. The gate: the default resolves to vibe-ic and writes nothing; `--orfs`
     is refused by name; a REFUSED knob the invocation set is refused by name
     before anything else is said about the flag; a flag no runner consumes
     yet is IMPL_NOT_YET_WIRED and writes nothing.
  3. Each runner's real main(), as a subprocess: `--librelane`, `--orfs`, and
     a missing flag on a librelane project each exit 2 with the named reason
     and leave the project tree byte-identical.
  4. Every runner-to-runner spawn site, the phase3 self-spawn included,
     forwards `child_argv(...)` before the call that runs it (checked per
     site); it is [] with no record: the default child argv is unchanged.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import _impl_flow as IF  # noqa: E402

RUNNERS = sorted(IF.KNOBS)


class _Captured(Exception):
    def __init__(self, parser):
        super().__init__("parser captured")
        self.parser = parser


def _real_parser(module_name: str, monkeypatch) -> argparse.ArgumentParser:
    """The parser `module.main()` builds, captured at its parse call."""
    mod = importlib.import_module(module_name)

    def _capture(self, *a, **k):
        raise _Captured(self)

    with monkeypatch.context() as m:
        m.setattr(argparse.ArgumentParser, "parse_args", _capture)
        m.setattr(argparse.ArgumentParser, "parse_known_args", _capture)
        m.setattr(sys, "argv", [module_name, "/nonexistent-project"])
        with pytest.raises(_Captured) as ei:
            mod.main()
    return ei.value.parser


def _tree(project: Path) -> dict:
    return {str(p.relative_to(project)): (hashlib.sha256(p.read_bytes()).hexdigest()
                                          if p.is_file() else "dir")
            for p in sorted(project.rglob("*"))}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    (p / "input").mkdir(parents=True)
    (p / "input" / "spec.md").write_text("a design input\n")
    return p


@pytest.mark.parametrize("runner", RUNNERS)
def test_every_knob_has_a_disposition(runner, monkeypatch):
    parser = _real_parser(runner, monkeypatch)
    dests = {a.dest for a in parser._actions if a.dest != "help"}
    assert IF.FLAG_DESTS <= dests, f"{runner} lacks the flags"
    assert dests - IF.FLAG_DESTS == set(IF.KNOBS[runner]), (
        f"{runner}: options without a disposition "
        f"{sorted(dests - IF.FLAG_DESTS - set(IF.KNOBS[runner]))}; "
        f"dispositions for no option "
        f"{sorted(set(IF.KNOBS[runner]) - dests)}")
    for dest, (disposition, why) in IF.KNOBS[runner].items():
        assert disposition in (IF.HONOURED, IF.MAPPED, IF.REFUSED), dest
        assert why.strip(), dest


@pytest.mark.parametrize("runner", RUNNERS)
def test_the_two_flags_are_mutually_exclusive(runner, monkeypatch, capsys):
    parser = _real_parser(runner, monkeypatch)
    with pytest.raises(SystemExit):
        parser.parse_args(["/p", "--librelane", "--orfs"])
    assert "not allowed with" in capsys.readouterr().err


def test_the_gate_on_the_real_phase3_parser(project, monkeypatch):
    parser = _real_parser("phase3_one_shot_runner", monkeypatch)
    before = _tree(project)

    def gate(*argv):
        return IF.gate(project, parser.parse_args([str(project), *argv]),
                       runner="phase3_one_shot_runner", parser=parser)

    assert gate() == IF.IMPL_DEFAULT
    # A window is the parser default's opposite, and a default is no request.
    assert gate("--entry-step", "17", "--exit-step", "17") == IF.IMPL_DEFAULT
    for argv, cls in ((["--orfs"], IF.IMPL_NOT_YET_SUPPORTED),
                      (["--librelane"], IF.IMPL_NOT_YET_WIRED),
                      (["--librelane", "--die-um", "400x400", "--util", "0.5",
                        "--spare-density", "0.05"], IF.IMPL_NOT_YET_WIRED),
                      (["--librelane", "--entry-step", "17", "--exit-step",
                        "17"], IF.IMPL_KNOB_UNSUPPORTED),
                      (["--librelane", "--force-step", "pnr"],
                       IF.IMPL_KNOB_UNSUPPORTED)):
        with pytest.raises(IF.ImplRefusal) as ei:
            gate(*argv)
        assert ei.value.reason_class == cls, argv
    with pytest.raises(IF.ImplRefusal) as ei:
        gate("--librelane", "--entry-step", "17", "--exit-step", "17")
    assert "--entry-step" in str(ei.value) and "--exit-step" in str(ei.value)
    assert _tree(project) == before


def test_a_wired_runner_passes_the_gate(project, monkeypatch):
    parser = _real_parser("phase3_one_shot_runner", monkeypatch)
    monkeypatch.setattr(IF, "WIRED_RUNNERS",
                        frozenset({"phase3_one_shot_runner"}))
    args = parser.parse_args([str(project), "--librelane"])
    assert IF.gate(project, args, runner="phase3_one_shot_runner",
                   parser=parser) == IF.IMPL_LIBRELANE
    assert not IF.record_path(project).exists()   # the gate never writes


def test_child_argv_follows_the_record(project):
    assert IF.child_argv(project) == []
    IF.write_record(project, "librelane", resolved_by="t")
    assert IF.child_argv(project) == ["--librelane"]


def _run_main(runner: str, project: Path, *argv: str):
    return subprocess.run(
        [sys.executable, str(PROGRAMS / f"{runner}.py"), str(project), *argv],
        capture_output=True, text=True, timeout=600,
        env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1",
             "HOME": str(project.parent)})


@pytest.mark.parametrize("runner", RUNNERS)
def test_each_real_main_refuses_by_name_and_writes_nothing(runner, project):
    before = _tree(project)
    cp = _run_main(runner, project, "--librelane")
    assert cp.returncode == 2, cp.stderr[-2000:]
    assert f"REFUSED: {IF.IMPL_NOT_YET_WIRED}" in cp.stderr
    cp = _run_main(runner, project, "--orfs")
    assert cp.returncode == 2, cp.stderr[-2000:]
    assert f"REFUSED: {IF.IMPL_NOT_YET_SUPPORTED}" in cp.stderr
    assert _tree(project) == before

    IF.write_record(project, "librelane", resolved_by="test")
    before = _tree(project)
    cp = _run_main(runner, project)      # a child that forgot the flag
    assert cp.returncode == 2, cp.stderr[-2000:]
    assert f"REFUSED: {IF.IMPL_MODE_CONFLICT}" in cp.stderr
    assert _tree(project) == before


# Runner-to-runner spawn sites. A site is a line that names a runner it will
# spawn: `_phase_runner("...")`, a quoted `*_one_shot_runner.py`, or the
# runner's own `str(Path(__file__))`. PER SITE, the mode must be forwarded
# (`_impl_flow.child_argv(`) between that line and the first call that runs the
# child. A forward on another runner's argv does not credit this site.
# The pins make a NEW site red until it is looked at.
_SPAWN = re.compile(r'_phase_runner\("|"[a-z0-9_]+_one_shot_runner\.py"'
                    r'|str\(Path\(__file__\)\)')
_RUNS_CHILD = re.compile(r"\b_run_phase\(|\b_run\(|\b_pr\.run\("
                         r"|\bsubprocess\.run\(")
_SPAWN_SITES = {
    "vibe_ic_one_shot_runner.py": 5,
    "design_one_shot_runner.py": 3,
    "phase23_one_shot_runner.py": 2,
    "phase3_one_shot_runner.py": 1,     # the enclosing window unit
    "phase1_one_shot_runner.py": 0,
    "analog_one_shot_runner.py": 0,
}
#: Named exemptions, with the reason: a spawned program that is not a gated
#: runner takes no flag.
_NOT_A_GATED_RUNNER = {
    "phase1_doc_one_shot_runner.py": "Phase 1's own doc track; Phase 1 runs "
                                     "unchanged under the flag",
}


_RUN_FUNCS = {"_run_phase", "_run", "run"}


def _is_run_call(node):
    f = node.func
    name = f.id if isinstance(f, ast.Name) else (
        f.attr if isinstance(f, ast.Attribute) else "")
    return name in _RUN_FUNCS and bool(_RUNS_CHILD.search(ast.unparse(f) + "("))


def _targets(stmt):
    if isinstance(stmt, ast.Assign):
        return {n.id for t in stmt.targets for n in ast.walk(t)
                if isinstance(n, ast.Name)}
    if isinstance(stmt, ast.AugAssign):
        return {n.id for n in ast.walk(stmt.target) if isinstance(n, ast.Name)}
    return set()


def _uses(node, name):
    return any(isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
               and n.id == name for n in ast.walk(node))


def _spawn_sites(fname):
    """(line, run-call line, forwarded?) per spawn site, read from the AST."""
    src = (PROGRAMS / fname).read_text()
    tree = ast.parse(src)
    funcs = [n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    sites = []
    for i, line in enumerate(src.splitlines(), 1):
        if line.lstrip().startswith(("#", "def ")) or not _SPAWN.search(line):
            continue
        if any(f'"{name}"' in line for name in _NOT_A_GATED_RUNNER):
            continue
        fn = min((f for f in funcs if f.lineno <= i <= f.end_lineno),
                 key=lambda f: f.end_lineno - f.lineno)
        stmt = min((st for st in ast.walk(fn) if isinstance(st, ast.stmt)
                    and st is not fn and st.lineno <= i <= st.end_lineno),
                   key=lambda st: st.end_lineno - st.lineno)
        inline_run = any(isinstance(c, ast.Call) and _is_run_call(c)
                         and c.lineno <= i <= c.end_lineno
                         for c in ast.walk(stmt))
        runner_names = (_targets(stmt) if isinstance(stmt, ast.Assign)
                        and not inline_run else set())
        assignments = [st for st in ast.walk(fn)
                       if isinstance(st, (ast.Assign, ast.AugAssign))]

        def runs_this_site(call):
            if call.lineno <= i <= call.end_lineno:
                return True
            for runner_name in runner_names:
                # The call may use the runner directly, or through one argv
                # assignment such as `cmd = [..., str(phase1), ...]`.
                if _uses(call, runner_name):
                    return True
                if any(stmt.lineno < feed.lineno < call.lineno
                       and isinstance(feed, ast.Assign)
                       and _uses(feed.value, runner_name)
                       and _targets(feed) & {n.id for n in ast.walk(call)
                                             if isinstance(n, ast.Name)}
                       for feed in assignments):
                    return True
            return False

        calls = sorted((c for c in ast.walk(fn) if isinstance(c, ast.Call)
                        and _is_run_call(c) and c.end_lineno >= i
                        and runs_this_site(c)),
                       key=lambda c: c.lineno)
        if not calls:
            # A BUILDER (e.g. phase3's `_phase3_enclosing_cmd`): it returns
            # the argv and another function runs it. The forward must then be
            # inside the statement that holds the spawn.
            sites.append((i, stmt.lineno,
                          "_impl_flow.child_argv(" in ast.unparse(stmt)))
            continue
        call = calls[0]
        text = ast.unparse(call)
        names = {n.id for n in ast.walk(call) if isinstance(n, ast.Name)}
        feeds = []
        for name in names:
            # A plain assignment replaces the argv. Forwards to an earlier
            # value of the same name cannot reach this child.
            last_bind = max((st.lineno for st in assignments
                             if isinstance(st, ast.Assign)
                             and st.lineno < call.lineno and name in _targets(st)),
                            default=i)
            lower_bound = max(i, last_bind) if runner_names else last_bind
            feeds.extend(st for st in assignments
                         if lower_bound <= st.lineno < call.lineno
                         and name in _targets(st))
        forwarded = "_impl_flow.child_argv(" in text or any(
            "_impl_flow.child_argv(" in ast.unparse(st) for st in feeds)
        sites.append((i, call.lineno, forwarded))
    return sites


@pytest.mark.parametrize("fname", sorted(_SPAWN_SITES))
def test_every_spawn_site_forwards_the_mode(fname):
    sites = _spawn_sites(fname)
    assert len(sites) == _SPAWN_SITES[fname], (
        f"{fname}: spawn sites at lines {[s[0] for s in sites]}, pinned "
        f"{_SPAWN_SITES[fname]} -- forward _impl_flow.child_argv(...) at the "
        "new one and move the pin")
    for line, run_line, forwarded in sites:
        assert run_line is not None, f"{fname}:{line}: no call runs this child"
        assert forwarded, (
            f"{fname}:{line}: the child spawned here (run at line {run_line}) "
            "is not told the project's mode")


def test_the_census_sees_each_missing_forward(tmp_path, monkeypatch):
    """Dropping any single child flag must fail its own site's contract."""
    original = PROGRAMS
    monkeypatch.setattr(sys.modules[__name__], "PROGRAMS", tmp_path)
    checked = 0
    for fname, count in _SPAWN_SITES.items():
        src = (original / fname).read_text()
        matches = list(re.finditer(r"_impl_flow\.child_argv\([^)]*\)", src))
        assert len(matches) == count, fname
        fake = tmp_path / fname
        for match in matches:
            fake.write_text(src[:match.start()] + "[]" + src[match.end():])
            assert fake.read_text() != src
            with pytest.raises(AssertionError, match="is not told the project's mode"):
                test_every_spawn_site_forwards_the_mode(fname)
            checked += 1
    assert checked == 11


def test_a_mapped_knob_does_not_claim_the_parents_default_is_explicit():
    """Wave-3 review: the parents forward their own --util/--die-um defaults
    to phase3, so "differs from phase3's default" cannot mean "declared"."""
    front = IF.KNOBS["vibe_ic_one_shot_runner"]
    for runner in ("vibe_ic_one_shot_runner", "phase23_one_shot_runner",
                   "phase3_one_shot_runner"):
        for knob in ("util", "die_um"):
            disposition, why = IF.KNOBS[runner][knob]
            assert disposition == IF.MAPPED
            assert "declared" in why and "explicit value is passed" not in why
    assert "provenance" in IF.MAPPED_PRECONDITION
    assert front["util"][0] == IF.MAPPED


def test_the_phase2_argv_builder_is_unchanged_by_default():
    import vibe_ic_one_shot_runner as V
    kw = dict(top_name="t", container="c", max_rtl_repair_retries=3,
              lec_max_completed_rungs=None, skip_hardware=True,
              skip_phase3=False, skip_analog=True, entry_step=None,
              exit_step="8")
    base = V._phase2_runner_argv(Path("/p"), **kw)
    assert base == ["/p", "--top-name", "t", "--container", "c",
                    "--max-rtl-repair-retries", "3", "--skip-hardware",
                    "--skip-analog", "--exit-step", "8"]
    assert V._phase2_runner_argv(Path("/p"), **kw, impl_argv=[]) == base
    assert V._phase2_runner_argv(Path("/p"), **kw,
                                 impl_argv=["--librelane"]) == base + ["--librelane"]
