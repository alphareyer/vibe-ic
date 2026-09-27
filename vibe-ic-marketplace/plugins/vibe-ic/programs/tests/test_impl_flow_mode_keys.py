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
        spreads = [v for k, v in zip(cfg.keys, cfg.values) if k is None]
        assert any("dispatch_config_entry" in ast.unparse(v) for v in spreads), (
            f"{fname}:{call.lineno}: admission config does not carry the mode")


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
    default = _run_to_admission(monkeypatch, module_name, a, wired=False)
    assert "impl" not in default
    assert not IF.record_path(a).exists()   # the default wrote nothing

    flagged = _run_to_admission(monkeypatch, module_name, b, "--librelane")
    assert flagged == {**default, "impl": "librelane"}
    rec = IF.read_record(b)
    assert rec["impl"] == "librelane" and rec["resolved_by"] == module_name


def test_the_mode_code_is_in_the_program_identity(tmp_path):
    names = {p.name for p in CRA.canonical_program_paths(PROGRAMS)}
    assert {"_impl_flow.py", "librelane_contract.py"} <= names
    assert all(p.is_file() for p in CRA.canonical_program_paths(PROGRAMS))
