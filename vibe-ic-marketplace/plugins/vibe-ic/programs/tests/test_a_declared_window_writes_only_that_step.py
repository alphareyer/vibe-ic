#!/usr/bin/env python3
"""A run that declares a window writes only that window's own work.

R-0924-1, the owner's ask: when a gate is wrong, re-run THAT step -- only it. The window was already honoured for DISPATCH; the FINALIZE
TAIL was not, and no flag reached it.

MEASURED before this change, on a copy of benchmark-data/ic/subservient at main
69abc2734, `design_one_shot_runner . --entry-step 4 --exit-step 4`:
    766 files touched, 432 of them OUTSIDE step 4's own workspace
    23 flow_compliance_check invocations, 3 of them over the WHOLE flow
    reports/final_summary.md republished with a fence reading "Steps: 70 total"
for a run that dispatched one step. The leak is not the window: every
out-of-window dispatch site correctly reports NOT_APPLICABLE and names the flag
that pruned it.

WHAT THESE ARMS PIN. Each one fails if the tail is re-enabled for a bounded run:
the five `emit_final_summary` sites, the whole-flow `final_audit`, the
declared-producer sweep, the two gate-identity sweeps and the whole-tree steps
view. The behavioural arms drive the real narrowing predicates rather than
grepping for them, because a source substring cannot tell you a branch bites.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
RUNNER = PROGRAMS / "design_one_shot_runner.py"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, PROGRAMS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod          # by-path import must be self-consistent
    spec.loader.exec_module(mod)
    return mod


def _main_node() -> ast.FunctionDef:
    tree = ast.parse(RUNNER.read_text())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return node
    raise AssertionError("design_one_shot_runner.main not found")


def _calls(node: ast.AST):
    return [n for n in ast.walk(node) if isinstance(n, ast.Call)]


def _dotted(call: ast.Call) -> str:
    f = call.func
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
        return f"{f.value.id}.{f.attr}"
    if isinstance(f, ast.Name):
        return f.id
    return ""


def _guarded_by_bounded(root: ast.AST, target: str) -> bool:
    """Is EVERY call to `target` inside `root` reached only through a test that
    reads `_bounded`? Walks If nodes rather than trusting indentation."""
    found = [c for c in _calls(root) if _dotted(c) == target]
    if not found:
        return False
    guarded = []
    for node in ast.walk(root):
        if not isinstance(node, ast.If):
            continue
        names = {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}
        if "_bounded" not in names:
            continue
        for branch in (node.body, node.orelse):
            for stmt in branch:
                guarded.extend(c for c in _calls(stmt) if _dotted(c) == target)
    return all(any(g is c for g in guarded) for c in found)


# --------------------------------------------------------------------------- #
# 0. the decision itself — DRIVEN, and bound to nothing else
# --------------------------------------------------------------------------- #
def test_declared_window_flags_reports_what_the_operator_typed():
    """The flags are the DISCLOSURE's subject, not the decision's. Whether the
    window prunes anything is a separate question — see
    test_a_flag_that_prunes_nothing_is_not_a_window, which is the property review
    w437hob32 corrected: an earlier version of this arm asserted that any flag
    bounds the run, and that is false for `--exit-step 13` and `--entry-step 1`."""
    dsr = _load("design_one_shot_runner")
    assert dsr.declared_window_flags("4", None) == ("--entry-step 4",)
    assert dsr.declared_window_flags(None, "4") == ("--exit-step 4",)
    assert dsr.declared_window_flags("4", "4") == ("--entry-step 4", "--exit-step 4")
    assert dsr.declared_window_flags(None, None) == ()


def test_a_flag_that_prunes_nothing_is_not_a_window():
    """DRIVEN, review w437hob32 MEDIUM. This runner's last dispatch site heads
    step 11, and the front door forwards --exit-step 13/23/31/33/37 verbatim.
    Such a run dispatches the WHOLE phase exactly as a flagless run does, so its
    whole-flow documents are exactly as true — and treating it as bounded booked
    the phase-2 audit NOT_APPLICABLE, which run_verdict reads as green, so an
    audit FAIL a flagless run would raise DISAPPEARED."""
    dsr = _load("design_one_shot_runner")
    order = ["rtl_gen", "rtl_validate", "sim", "yosys_synth", "dft_lec_chain"]
    # nothing pruned, entry at the first site (or absent) -> NOT bounded
    assert dsr.run_is_bounded(None, [], order) is False
    assert dsr.run_is_bounded(None, None, order) is False
    assert dsr.run_is_bounded("rtl_gen", [], order) is False
    # real pruning, or an entry that moved off the first site -> bounded
    assert dsr.run_is_bounded(None, ["dft_lec_chain"], order) is True
    assert dsr.run_is_bounded("sim", [], order) is True
    assert dsr.run_is_bounded("sim", ["yosys_synth"], order) is True


def test_the_flag_level_rule_agrees_with_the_dispatch_level_one():
    """ANTI-DRIFT. `run_is_bounded` asks the run's dispatch signals;
    `step_preflight.window_is_effective` asks the same question of the flags,
    for the front door, which decides before any runner has those signals. Two
    spellings of one rule is how they come to disagree, so this pins them over
    the windows that actually occur — benchmark_dispatch sends --exit-step 2/4/9
    and --entry-step 2; the front door forwards 13/23/31/33/37."""
    dsr = _load("design_one_shot_runner")
    spf = _load("step_preflight")
    RUNNER = "design_one_shot_runner"
    plan = spf.RUNNER_PLANS[RUNNER]
    order = [n for n, _ in plan.sites]
    for exit_step in ("2", "4", "9", "11", "13", "23", "31", "33", "37"):
        pruned = spf.exit_pruned_sites(plan.sites, exit_step) or []
        assert dsr.run_is_bounded(None, pruned, order) == \
            spf.window_is_effective(exit_step=exit_step, runner=RUNNER), exit_step
    for entry_step in ("1", "2", "4", "9"):
        site = spf.site_for_step(RUNNER, entry_step)
        assert dsr.run_is_bounded(site, [], order) == \
            spf.window_is_effective(entry_step=entry_step, runner=RUNNER), entry_step
    assert spf.window_is_effective() is False


def test_the_front_door_bounds_its_own_tail():
    """Review w437hob32 MEDIUM: the entry the owner actually uses forwarded the
    window and then ran its own tail unconditionally, so the leak survived and
    the phase-2 report's `not_refreshed_here` was contradicted on disk moments
    later by THIS runner rewriting the file it named."""
    src = (PROGRAMS / "vibe_ic_one_shot_runner.py").read_text()
    tree = ast.parse(src)
    main = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    for target in ("_pl.emit_final_summary", "_pl.emit_steps_view"):
        calls = [c for c in _calls(main) if _dotted(c) == target]
        assert calls, f"{target} vanished from the front door"
        guarded = []
        for node in ast.walk(main):
            if not isinstance(node, ast.If):
                continue
            if "_fd_bounded" not in {n.id for n in ast.walk(node.test)
                                     if isinstance(n, ast.Name)}:
                continue
            for branch in (node.body, node.orelse):
                for stmt in branch:
                    guarded.extend(c for c in _calls(stmt)
                                   if _dotted(c) == target)
        assert all(any(g is c for g in guarded) for c in calls), (
            f"{target} runs at the front door without consulting _fd_bounded")
    binds = [n for n in ast.walk(main) if isinstance(n, ast.Assign)
             and any(isinstance(tg, ast.Name) and tg.id == "_fd_bounded"
                     for tg in n.targets)]
    assert binds, "_fd_bounded is never computed"
    assert not any(isinstance(b.value, ast.Constant) and b.value.value
                   for b in binds), (
        "_fd_bounded is bound to a truthy literal; the tail is unconditionally "
        "skipped and every arm here still passes")


def test_refresh_only_does_not_rewrite_the_runs_own_report():
    """HIGH, review w437hob32. MEASURED before the fix: a bounded run's report
    carrying 36 step rows, `declared_window` and 5 disclosures was replaced by a
    2-row document. benchmark_dispatch reads that exact file and walks steps[]
    for the rtl_gen WAIVED row's `extras.fallback_skill`, so the erasure also
    destroys the AI-backup handover contract."""
    tree = ast.parse(RUNNER.read_text())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
              and n.name == "_run_refresh_only")
    called = {_dotted(c) for c in _calls(fn)}
    assert "_pl.publish_report_then_steps_view" not in called, (
        "--refresh-only publishes its summary to the RUN's report path, erasing "
        "the verdicts of a run it measured none of")
    # NAMING the run report is fine and wanted -- the refresh PRINTS that it
    # left the file alone. Passing it to anything else is the defect.
    strings = {n.value for n in ast.walk(fn)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    for call in _calls(fn):
        if _dotted(call) == "print":
            continue
        for arg in ast.walk(call):
            if (isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                    and "phase2_one_shot" in arg.value):
                raise AssertionError(
                    f"--refresh-only passes the run report's name to "
                    f"{_dotted(call)!r} at line {call.lineno}")
    assert any("phase2_one_shot" in s for s in strings), (
        "the refresh should SAY it left the run report alone")
    assert "_pl.emit_steps_view" in called, (
        "the refresh must still rebuild the view, against the report on disk")
    assert any("refresh_only.json" in s for s in strings), (
        "the refresh must publish its own record")


def test_bounded_is_computed_by_that_function_and_never_a_constant():
    """THE ARM THAT BITES THE OBVIOUS MUTATION.

    Every guard in the finalize tail reads `_bounded`. An arm that only checks
    `step_final_audit` sits inside `if _bounded:` stays GREEN when someone
    writes `_bounded = False` and re-enables the whole tail — MEASURED: that
    one-line mutation left 15 of these 16 arms green before this arm existed.
    So the BINDING is pinned too: `_bounded` is assigned exactly once in
    main(), from a call to the drivable decision, never from a literal."""
    main = _main_node()
    binds = [n for n in ast.walk(main)
             if isinstance(n, ast.Assign)
             and any(isinstance(tg, ast.Name) and tg.id == "_bounded"
                     for tg in n.targets)]
    assert len(binds) == 1, (
        f"_bounded is assigned {len(binds)} times in main() "
        f"(lines {[b.lineno for b in binds]}); one guard cannot be reasoned "
        f"about if the flag is rebound")
    value = binds[0].value
    assert not isinstance(value, ast.Constant), (
        f"_bounded is bound to the literal {getattr(value, 'value', '?')!r} at "
        f"line {binds[0].lineno}: the whole finalize tail is re-enabled and "
        f"every structural arm here still passes")
    assert isinstance(value, ast.Call) and _dotted(value) == "run_is_bounded", (
        "_bounded must come from run_is_bounded, the decision a test can drive")
    flags = [n for n in ast.walk(main)
             if isinstance(n, ast.Assign)
             and any(isinstance(tg, ast.Name) and tg.id == "_window_flags"
                     for tg in n.targets)]
    assert len(flags) == 1 and isinstance(flags[0].value, ast.Call), (
        "_window_flags must be computed once, by declared_window_flags")


# --------------------------------------------------------------------------- #
# 1. the five report-card sites
# --------------------------------------------------------------------------- #
def test_no_site_in_main_calls_emit_final_summary_directly():
    """Every site goes through the bounded-aware helper.

    `emit_final_summary` runs final_report_generate.py, which defaults to
    run_audit=True: each call costs a whole-flow `flow_compliance_check
    --strict`. Five call sites were unconditional, and a bounded run reached
    two of them."""
    main = _main_node()
    helper = next((n for n in ast.walk(main)
                   if isinstance(n, ast.FunctionDef)
                   and n.name == "_emit_final_summary_or_disclose"), None)
    assert helper is not None, "the bounded-aware helper is gone"
    inside_helper = {id(c) for c in _calls(helper)}
    direct = [c for c in _calls(main)
              if _dotted(c) == "_pl.emit_final_summary"
              and id(c) not in inside_helper]
    assert direct == [], (
        f"{len(direct)} site(s) in main() still call _pl.emit_final_summary "
        f"directly (lines {[c.lineno for c in direct]}); a bounded run reaches "
        f"them and pays for a whole-flow audit it did not ask for")
    routed = [c for c in _calls(main)
              if _dotted(c) == "_emit_final_summary_or_disclose"]
    assert len(routed) == 5, (
        f"expected the 5 known call sites to be routed, found {len(routed)}")


def test_the_helper_refuses_to_write_the_report_card_when_bounded():
    """Driven, not read: the helper returns False and discloses, and the
    document is not written."""
    helper = next((n for n in ast.walk(_main_node())
                   if isinstance(n, ast.FunctionDef)
                   and n.name == "_emit_final_summary_or_disclose"), None)
    assert helper is not None, "the bounded-aware helper is gone"
    body = ast.unparse(helper)
    assert "_bounded" in body, (
        "the helper does not consult the window at all")
    assert "_disclose_whole_flow_skip" in body, (
        "the helper skips the report card without disclosing that it did")
    assert "return False" in body, (
        "the bounded branch must report that no summary was written, so the "
        "run's own `final summary:` line cannot claim one")


# --------------------------------------------------------------------------- #
# 2. the whole-flow audit, the producer sweep
# --------------------------------------------------------------------------- #
def test_the_whole_flow_final_audit_is_guarded_by_the_window():
    """A whole-flow verdict over a tree 69 of whose 70 steps did not run is the
    PREVIOUS run's state re-attributed to this one."""
    assert _guarded_by_bounded(_main_node(), "step_final_audit"), (
        "step_final_audit is reachable without consulting _bounded")


def test_the_declared_producer_sweep_is_guarded_by_the_window():
    """It executes the producers the flow declares for EVERY step."""
    main = _main_node()
    sweeps = [c for c in _calls(main)
              if any(isinstance(a, ast.Call) or isinstance(a, ast.List)
                     for a in c.args)
              and "flow_declared_producer_run.py" in ast.dump(c)]
    assert sweeps, "the declared-producer sweep call was not found in main()"
    guarded = []
    for node in ast.walk(main):
        if not isinstance(node, ast.If):
            continue
        if "_bounded" not in {n.id for n in ast.walk(node.test)
                              if isinstance(n, ast.Name)}:
            continue
        for branch in (node.body, node.orelse):
            for stmt in branch:
                guarded.extend(c for c in _calls(stmt)
                               if "flow_declared_producer_run.py" in ast.dump(c))
    assert all(any(g is c for g in guarded) for c in sweeps), (
        "the declared-producer sweep runs without consulting _bounded")


def test_the_narrowed_refreshes_are_disclosed_as_narrowed_not_as_skipped():
    """A narrowing is not a skip. The gate-identity sweep and the steps view
    still RUN on a bounded run, over this run's own work, and a reader told
    only about the skips would read their smaller output as a loss."""
    main = _main_node()
    narrowed = set()
    for c in _calls(main):
        if _dotted(c) != "_disclose_narrowed_refresh":
            continue
        assert c.args and isinstance(c.args[0], ast.Constant)
        narrowed.add(c.args[0].value)
    assert narrowed == {"stamp_gate_reports", "steps_view"}, narrowed
    src = RUNNER.read_text()
    assert '"kind": "narrowed"' in src and '"kind": "skipped"' in src, (
        "the two kinds must be distinguishable in the record")


def test_the_run_report_carries_the_window_and_the_disclosures():
    """The report is where a reader learns what this run did NOT do."""
    src = RUNNER.read_text()
    for key in ('"declared_window"', '"bounded_disclosures"',
                '"not_refreshed_here"', '"dispatched_step_ids"'):
        assert key in src, f"the run report does not carry {key}"
    assert "_pl.published_here(project / _rel, _run_started_at)" in src, (
        "staleness of the whole-flow documents is decided by something other "
        "than the one owner of that question")


def test_the_disclosed_refresh_names_are_exactly_the_approved_set():
    """The operator is owed a disclosure per refresh, by name."""
    main = _main_node()
    names = set()
    for c in _calls(main):
        if _dotted(c) != "_disclose_whole_flow_skip":
            continue
        assert c.args and isinstance(c.args[0], ast.Constant), (
            "a disclosure whose name is computed cannot be audited")
        names.add(c.args[0].value)
    assert names == {"emit_final_summary", "final_audit",
                     "flow_declared_producer_run"}, names


# --------------------------------------------------------------------------- #
# 3. the narrowed gate-identity sweeps — DRIVEN
# --------------------------------------------------------------------------- #
def test_the_stamp_sweep_skips_a_gate_json_this_run_did_not_write(tmp_path):
    """`written_after` narrows the sweep to the jsons THIS invocation wrote.

    The unnarrowed sweep re-dates every gate json in the tree, including ones a
    bounded run has no business rewriting."""
    dsr = _load("design_one_shot_runner")
    gates = tmp_path / "reports" / "phase2" / "gates"
    gates.mkdir(parents=True)
    old = gates / "some_gate.json"
    old.write_text(json.dumps({"verdict": "PASS"}) + "\n")
    stale = time.time() - 10_000
    os.utime(old, (stale, stale))

    skipped = dsr._stamp_gate_report_dirs(tmp_path, written_after=time.time())
    assert skipped == [], (
        f"a bounded sweep stamped {skipped}, a file written before the run")

    swept = dsr._stamp_gate_report_dirs(tmp_path, written_after=None)
    assert [Path(s).name for s in swept] == ["some_gate.json"], (
        f"the unnarrowed sweep must still stamp everything; got {swept}")


def test_the_stamp_sweep_still_stamps_what_this_run_did_write(tmp_path):
    """The narrowing must not turn the sweep off — a fresh json is stamped."""
    dsr = _load("design_one_shot_runner")
    gates = tmp_path / "reports" / "phase2" / "gates"
    gates.mkdir(parents=True)
    started = time.time()
    fresh = gates / "fresh_gate.json"
    fresh.write_text(json.dumps({"verdict": "PASS"}) + "\n")

    swept = dsr._stamp_gate_report_dirs(tmp_path, written_after=started)
    assert [Path(s).name for s in swept] == ["fresh_gate.json"], (
        f"a gate json this run wrote must still be stamped; got {swept}")


# --------------------------------------------------------------------------- #
# 4. the bounded steps view — DRIVEN
# --------------------------------------------------------------------------- #
def test_a_bounded_steps_view_carries_the_rows_it_did_not_refresh(tmp_path):
    """"The step's own row only" must not mean "delete the rest of the view".

    A partial index that dropped the other rows would also arm
    `_prune_stale_folders` to delete their folders — a bounded refresh turning
    into a deletion of the whole tree."""
    soc = _load("step_output_collector")
    steps_root = tmp_path / "steps"
    prior = steps_root / "phase1" / "stage_phase1" / "9_older_step"
    prior.mkdir(parents=True)
    (prior / "outputs.json").write_text("{}\n")
    steps_root.mkdir(parents=True, exist_ok=True)
    (steps_root / "index.json").write_text(json.dumps({"steps": [
        {"id": "9", "name": "older step", "status": "PASS", "phase": "phase1",
         "stage": "stage_phase1", "folder": "phase1/stage_phase1/9_older_step",
         "n_outputs": 0}]}) + "\n")

    res = soc.materialize(tmp_path, only_steps={"4"})
    idx = json.loads((steps_root / "index.json").read_text())
    ids = {r["id"]: r for r in idx["steps"]}

    assert "9" in ids, (
        "the row this call did not refresh was dropped; a reader of steps/ "
        "after a bounded run is owed the tree they had before it")
    assert ids["9"].get("refreshed_here") is False, (
        "a carried row must say it was not refreshed here")
    assert prior.is_dir(), "a bounded refresh DELETED an out-of-window folder"
    assert res["bounded_to"] == ["4"]
    assert idx["not_refreshed_here"] == ["9"]


def test_a_whole_flow_steps_view_is_byte_identical_to_before(tmp_path):
    """only_steps=None must keep the unbounded payload exactly as it was: no
    `refreshed_here`, no `bounded_to`, and pruning still armed."""
    soc = _load("step_output_collector")
    res = soc.materialize(tmp_path)
    idx = json.loads((tmp_path / "steps" / "index.json").read_text())
    assert set(idx.keys()) == {"steps"}, (
        f"the whole-flow index grew keys: {sorted(idx.keys())}")
    assert all("refreshed_here" not in r for r in idx["steps"])
    assert "bounded_to" not in res and "n_not_refreshed" not in res


def test_only_steps_naming_nothing_is_refused():
    """An empty window is a typo, not a request to refresh zero steps."""
    r = subprocess.run(
        [sys.executable, str(PROGRAMS / "step_output_collector.py"),
         str(PROGRAMS), "--only-steps", " , "],
        capture_output=True, text=True, timeout=120)
    assert r.returncode == 2, (r.returncode, r.stdout[-400:], r.stderr[-400:])
    assert "names no step id" in r.stderr


# --------------------------------------------------------------------------- #
# 5. ONE staleness rule, and the refresh the operator can ask for
# --------------------------------------------------------------------------- #
def test_published_here_has_one_owner_and_the_front_door_asks_it():
    """#2572/#2575's freshness predicate, asked by both readers.

    The bounded run needs it for arbitrary paths; the front door needs it for a
    phase report. Two implementations is how two staleness rules come to
    disagree about one file."""
    pl = _load("_path_layout")
    assert callable(pl.published_here) and hasattr(pl, "FRESHNESS_TOLERANCE_S")
    src = (PROGRAMS / "vibe_ic_one_shot_runner.py").read_text()
    assert "_pl.published_here(" in src, (
        "the front door stopped asking the owner and grew its own copy")
    fn = src.index("def _published_here")
    body = src[fn:src.index("\ndef ", fn + 10)]
    assert "st_mtime" not in body, (
        "the phase-report reader re-implements the mtime comparison instead of "
        "delegating to the owner")


def test_published_here_fails_closed(tmp_path):
    pl = _load("_path_layout")
    started = time.time()
    f = tmp_path / "doc.md"
    f.write_text("x")
    assert pl.published_here(f, started) is True
    old = started - 10_000
    os.utime(f, (old, old))
    assert pl.published_here(f, started) is False
    assert pl.published_here(tmp_path / "absent.md", started) is False
    assert pl.published_here(tmp_path, started) is False   # a directory


def test_refresh_only_refuses_a_window(tmp_path):
    """A refresh of the whole flow has no window."""
    r = subprocess.run(
        [sys.executable, str(RUNNER), str(tmp_path),
         "--refresh-only", "--exit-step", "4"],
        capture_output=True, text=True, timeout=300)
    assert r.returncode == 2, (r.returncode, r.stdout[-500:])
    assert "has no window" in r.stderr


def test_refresh_only_dispatches_no_step():
    """It restates what is on disk; it must not be able to produce design
    evidence."""
    src = RUNNER.read_text()
    fn = src.index("def _run_refresh_only")
    body = src[fn:src.index("\ndef main(", fn)]
    for forbidden in ("step_rtl_gen", "step_yosys_synth", "step_verilator_coverage",
                      "step_dft_lec_chain", "step_sim"):
        assert forbidden not in body, (
            f"--refresh-only dispatches {forbidden}; it must refresh documents "
            f"only")
    assert '"dispatched_steps": []' in body
