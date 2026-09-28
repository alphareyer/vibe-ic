"""W2 (`--librelane` v1): admission and the step cache are keyed on the mode.

Contracts:
  1. With no flag, the admission `dispatch_config` carries no `impl` key and
     the step-cache knobs carry no `impl` knob: a default identity does not
     move. Under a recorded mode both carry it.
  2. The step cache's inputs component differs between the two modes, so a
     DEF (or netlist, or GDS) made by one flow is never reused by the other.
  3. Every canonical admission site in the runners spreads
     `dispatch_config_entry`, and the real phase3 and phase2 mains pass it.
  4. A wired runner records the mode under its lock, once; the default
     records nothing.
  5. The mode record and the LibreLane contract are in the program identity.
  6. Every runner that spawns children (front door, phase1, design, phase3,
     phase23) records the mode before any `child_argv`, directly after its
     project-lock check; the front door's, phase1's and phase23's real mains
     prove it, and the first spawned child carries the flag.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import _impl_flow as IF  # noqa: E402
import _step_identity as si  # noqa: E402
import canonical_run_admission as CRA  # noqa: E402


class _Args:
    spare_density = 0.02
    container = ""


class _Captured(Exception):
    def __init__(self, config):
        super().__init__("admission captured")
        self.config = config


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return _make(tmp_path / "proj")


def _make(p: Path) -> Path:
    for rel, text in (
            ("input/spec.md", "a design input\n"),
            ("input/submission_template/tapeout_declaration.json",
             '{"answers": {"deliverable": "DIE"}}\n'),
            ("phase2/stage1/rtl/top.v", "module top(); endmodule\n"),
            ("phase2/stage2/synth/top_synth.v", "module top(); endmodule\n"),
            ("phase3/stage3/pnr/top.def", "VERSION 5.8 ;\nEND DESIGN\n")):
        f = p / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    return p


def _r():
    import phase3_one_shot_runner as R
    return R


@pytest.mark.parametrize("kind", ["synth", "pnr", "gds"])
def test_the_step_cache_keys_on_the_mode(project, kind):
    R = _r()
    si.set_declaration_flow_keys(R._DECLARATION_PUBLISH_KEYS)
    inputs, knobs, _bad = R._step_inputs(project, kind, "top", _Args())
    assert "impl" not in knobs
    default_digest, _ = si.resolved_inputs_digest(project, inputs, knobs)

    IF.write_record(project, "librelane", resolved_by="test")
    inputs2, knobs2, _bad2 = R._step_inputs(project, kind, "top", _Args())
    assert knobs2 == {**knobs, "impl": "librelane"}
    assert inputs2 == inputs
    flagged_digest, _ = si.resolved_inputs_digest(project, inputs2, knobs2)
    assert default_digest and flagged_digest
    assert flagged_digest != default_digest, (
        f"{kind}: a {kind} artefact made by one flow would be reused by the "
        "other")


def test_an_unreadable_record_leaves_the_step_uncacheable(project):
    R = _r()
    path = IF.record_path(project)
    path.parent.mkdir(parents=True)
    path.write_text("{damaged")
    _i, knobs, bad = R._step_inputs(project, "pnr", "top", _Args())
    assert "impl" not in knobs
    assert any("_impl_flow.recorded_impl" in b for b in bad), bad


def test_dispatch_config_entry_follows_the_record(project):
    assert IF.dispatch_config_entry(IF.recorded_impl(project)) == {}
    IF.write_record(project, "librelane", resolved_by="test")
    assert IF.dispatch_config_entry(IF.recorded_impl(project)) == {
        "impl": "librelane"}


_ADMITTING = ("vibe_ic_one_shot_runner.py", "design_one_shot_runner.py",
              "phase3_one_shot_runner.py")


@pytest.mark.parametrize("fname", _ADMITTING)
def test_every_admission_site_spreads_the_mode(fname):
    tree = ast.parse((PROGRAMS / fname).read_text())
    configs = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_admission_args"
                for t in node.targets):
            configs["_admission_args"] = node.value
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr == "admit_span"]
    assert calls, fname
    for call in calls:
        cfg = call.args[4]
        if isinstance(cfg, ast.Name):
            cfg = configs[cfg.id]
        assert isinstance(cfg, ast.Dict), (fname, call.lineno)
        spreads = [ast.unparse(v) for k, v in zip(cfg.keys, cfg.values)
                   if k is None]
        # EXACTLY the project's recorded mode, not merely the helper's name:
        # a constant argument would spread a mode the project never recorded.
        assert ("_impl_flow.dispatch_config_entry("
                "_impl_flow.recorded_impl(project))") in spreads, (
            f"{fname}:{call.lineno}: admission config does not carry the "
            f"project's recorded mode ({spreads})")


def _run_to_admission(monkeypatch, module_name, project, *argv, wired=True):
    import importlib
    mod = importlib.import_module(module_name)
    if wired:
        monkeypatch.setattr(IF, "WIRED_RUNNERS",
                            frozenset(IF.WIRED_RUNNERS | {module_name}))

    def _admit(project, span, programs_dir, container, config):
        raise _Captured(dict(config))

    monkeypatch.setattr(mod._canonical_admission, "admit_span", _admit)
    if hasattr(mod, "_delivery_admission_refusal"):
        monkeypatch.setattr(mod, "_delivery_admission_refusal", lambda p: None)
    monkeypatch.setattr(sys, "argv", [module_name, str(project), *argv])
    with pytest.raises(_Captured) as ei:
        mod.main()
    return ei.value.config


@pytest.mark.parametrize("module_name", ["phase3_one_shot_runner",
                                         "design_one_shot_runner"])
def test_the_real_main_admits_with_the_mode(monkeypatch, tmp_path, module_name):
    # Two identical projects: each main takes (and keeps) its project lock.
    a, b = _make(tmp_path / "a"), _make(tmp_path / "b")
    # W24: under the flag a runner that takes --pdk must name an in-scope
    # one; both arms name it, so the configs differ by the mode alone.
    pdk = (["--pdk", "gf180mcuD"] if module_name == "phase3_one_shot_runner"
           else [])
    default = _run_to_admission(monkeypatch, module_name, a, *pdk, wired=False)
    assert "impl" not in default
    assert not IF.record_path(a).exists()   # the default wrote nothing

    flagged = _run_to_admission(monkeypatch, module_name, b, "--librelane", *pdk)
    # The mode is spread on top of the default config, unchanged. W3 (the
    # flow-mode layer) adds its own two keys to the same identity under a
    # flag -- named here, so any OTHER extra key is still a failure.
    assert {k: flagged[k] for k in default} == default
    assert flagged["impl"] == "librelane"
    assert set(flagged) - set(default) - {"impl"} <= {
        "librelane_impl_layer", "librelane_contract_sha256"}
    rec = IF.read_record(b)
    assert rec["impl"] == "librelane" and rec["resolved_by"] == module_name


def test_the_mode_code_is_in_the_program_identity(tmp_path):
    names = {p.name for p in CRA.canonical_program_paths(PROGRAMS)}
    assert {"_impl_flow.py", "librelane_contract.py"} <= names
    assert all(p.is_file() for p in CRA.canonical_program_paths(PROGRAMS))


# ── wave-4b review (W2): every runner that spawns records the mode first ─────

_RECORDING_RUNNERS = ("vibe_ic_one_shot_runner", "phase1_one_shot_runner",
                      "design_one_shot_runner", "phase3_one_shot_runner",
                      "phase23_one_shot_runner")


def _main_body(module_name):
    tree = ast.parse((PROGRAMS / f"{module_name}.py").read_text())
    return next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "main")


def _is_record_call(stmt, runner):
    return (isinstance(stmt, ast.Expr) and ast.unparse(stmt.value) ==
            f"_impl_flow.record_after_lock(project, args, runner={runner!r})")


@pytest.mark.parametrize("runner", _RECORDING_RUNNERS)
def test_every_spawning_runner_records_before_it_spawns(runner):
    """Structural: the record call names this runner, sits DIRECTLY after the
    project-lock None-check where the runner takes one, and precedes every
    `child_argv` in main (which reads only the record)."""
    main = _main_body(runner)
    body = main.body
    idx = [i for i, st in enumerate(body) if _is_record_call(st, runner)]
    assert len(idx) == 1, f"{runner}: record_after_lock calls in main: {idx}"
    rec = body[idx[0]]
    prev = body[idx[0] - 1]
    if "acquire_or_reenter" in ast.unparse(main):
        assert (isinstance(prev, ast.If) and "is None" in ast.unparse(prev.test)
                and "return 3" in ast.unparse(prev)), (
            f"{runner}: record_after_lock is not the first statement after the "
            "project-lock check")
    else:
        assert "gate_or_exit" in ast.unparse(body[idx[0] - 2]) and \
            "_impl_rc" in ast.unparse(prev), f"{runner}: not right after the gate"
    for node in ast.walk(main):
        if isinstance(node, ast.Call) and ast.unparse(node.func) == \
                "_impl_flow.child_argv":
            assert node.lineno > rec.lineno, (
                f"{runner}:{node.lineno}: child_argv before the mode is recorded")


class _Spawned(Exception):
    def __init__(self, args):
        super().__init__("spawn captured")
        self.args_ = list(args)


def _wire(monkeypatch, runner):
    monkeypatch.setattr(IF, "WIRED_RUNNERS",
                        frozenset(IF.WIRED_RUNNERS | {runner}))


def test_the_front_door_records_and_its_first_child_carries_the_flag(
        monkeypatch, tmp_path):
    import vibe_ic_one_shot_runner as V

    def _spawn(label, runner, args, env=None):
        raise _Spawned(args)

    monkeypatch.setattr(V, "_run_phase", _spawn)
    monkeypatch.setattr(V, "_capture_container_image",
                        lambda *a, **k: {"verdict": "SKIP"})
    for flagged, name in ((False, "a"), (True, "b")):
        proj = _make(tmp_path / name)
        if flagged:
            _wire(monkeypatch, "vibe_ic_one_shot_runner")
        argv = [str(proj), "--no-dashboard"] + (
            ["--librelane", "--pdk", "gf180mcuD"] if flagged else [])
        monkeypatch.setattr(sys, "argv", ["vibe_ic_one_shot_runner", *argv])
        with pytest.raises(_Spawned) as ei:
            V.main()
        if flagged:
            rec = IF.read_record(proj)
            assert rec["impl"] == "librelane"
            assert rec["resolved_by"] == "vibe_ic_one_shot_runner"
            assert "--librelane" in ei.value.args_
        else:
            assert not IF.record_path(proj).exists()
            assert "--librelane" not in ei.value.args_


def test_phase1_records_under_its_lock(monkeypatch, tmp_path):
    import phase1_one_shot_runner as P1

    class _Past(Exception):
        pass

    def _stop(*a, **k):
        raise _Past()

    monkeypatch.setattr(P1, "_run_step_0_5ic", _stop)
    for flagged, name in ((False, "a"), (True, "b")):
        proj = _make(tmp_path / name)
        if flagged:
            _wire(monkeypatch, "phase1_one_shot_runner")
        monkeypatch.setattr(sys, "argv", ["phase1_one_shot_runner", str(proj)]
                            + (["--librelane"] if flagged else []))
        with pytest.raises(_Past):
            P1.main()
        if flagged:
            assert IF.read_record(proj)["resolved_by"] == "phase1_one_shot_runner"
        else:
            assert not IF.record_path(proj).exists()


def test_phase23_records_before_its_children_are_told(monkeypatch, tmp_path):
    """Wave-4b review: phase23 forwarded `child_argv`, which reads only the
    record, and never wrote one -- so its children were told nothing."""
    import phase23_one_shot_runner as P23

    def _spawn(name, runner, args):
        raise _Spawned(args)

    monkeypatch.setattr(P23, "_run_phase", _spawn)
    for flagged, name in ((False, "a"), (True, "b")):
        proj = _make(tmp_path / name)
        if flagged:
            _wire(monkeypatch, "phase23_one_shot_runner")
        monkeypatch.setattr(sys, "argv", ["phase23_one_shot_runner", str(proj)]
                            + (["--librelane", "--pdk", "gf180mcuD"]
                               if flagged else []))
        with pytest.raises(_Spawned) as ei:
            P23.main()
        if flagged:
            assert IF.read_record(proj)["resolved_by"] == "phase23_one_shot_runner"
            assert ei.value.args_[-1] == "--librelane"
        else:
            assert not IF.record_path(proj).exists()
            assert "--librelane" not in ei.value.args_
