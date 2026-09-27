"""W24 (`--librelane` v1): the v1 scope is refused by name, before any tool runs.

Owner ruling 2026-09-28: v1 is gf180mcuD, digital, no macros; analog is out
(decision 13). Contracts:
  1. Under the flag the real mains refuse, each by name and with the remedy,
     and write nothing: the analog runner (IMPL_ANALOG_UNSUPPORTED), a named
     PDK outside the scope (IMPL_PDK_UNSUPPORTED), staged macro views
     (IMPL_MACROS_UNSUPPORTED). A named in-scope PDK reaches the next check.
  2. The default flow is never refused for any of these.
  3. A PDK left to `auto` is judged where phase3 resolves it: the real phase3
     main refuses an out-of-scope resolution under a recorded mode.
  4. The front door judges analog on its own `run_analog` decision.
"""
from __future__ import annotations

import ast
import hashlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import _impl_flow as IF  # noqa: E402


def _tree(project: Path) -> dict:
    return {str(p.relative_to(project)): (hashlib.sha256(p.read_bytes()).hexdigest()
                                          if p.is_file() else "dir")
            for p in sorted(project.rglob("*"))}


def _make(p: Path) -> Path:
    for rel, text in (("input/spec.md", "a design input\n"),
                      ("input/submission_template/tapeout_declaration.json",
                       '{"answers": {"deliverable": "DIE"}}\n')):
        f = p / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    return p


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return _make(tmp_path / "proj")


def _run_main(runner: str, project: Path, *argv: str):
    return subprocess.run(
        [sys.executable, str(PROGRAMS / f"{runner}.py"), str(project), *argv],
        capture_output=True, text=True, timeout=600,
        env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1",
             "HOME": str(project.parent)})


def _refused(cp, cls):
    assert cp.returncode == 2, cp.stderr[-2000:]
    assert f"REFUSED: {cls}" in cp.stderr, cp.stderr[-2000:]
    assert "run the default flow" in cp.stderr


def test_the_analog_runner_is_refused_by_name(project):
    before = _tree(project)
    _refused(_run_main("analog_one_shot_runner", project, "--librelane"),
             IF.IMPL_ANALOG_UNSUPPORTED)
    assert _tree(project) == before


@pytest.mark.parametrize("runner", ["vibe_ic_one_shot_runner",
                                    "phase23_one_shot_runner",
                                    "phase3_one_shot_runner"])
def test_a_named_pdk_outside_the_scope_is_refused(project, runner):
    before = _tree(project)
    _refused(_run_main(runner, project, "--librelane", "--pdk", "sky130A"),
             IF.IMPL_PDK_UNSUPPORTED)
    cp = _run_main(runner, project, "--librelane", "--pdk", "gf180mcuD")
    assert f"REFUSED: {IF.IMPL_NOT_YET_WIRED}" in cp.stderr, cp.stderr[-2000:]
    assert _tree(project) == before


@pytest.mark.parametrize("runner", sorted(set(IF.KNOBS) - {"analog_one_shot_runner"}))
def test_staged_macros_are_refused(project, runner):
    lef = project / "input" / "pdk_local" / "vendor" / "sram_1k.lef"
    lef.parent.mkdir(parents=True)
    lef.write_text("MACRO sram_1k\nEND sram_1k\n")
    before = _tree(project)
    cp = _run_main(runner, project, "--librelane")
    _refused(cp, IF.IMPL_MACROS_UNSUPPORTED)
    assert "input/pdk_local/vendor/sram_1k.lef" in cp.stderr
    assert _tree(project) == before


def test_the_default_is_never_refused_for_scope(project):
    (project / "input" / "pdk_local" / "v").mkdir(parents=True)
    (project / "input" / "pdk_local" / "v" / "m.gds").write_bytes(b"\0")
    args = SimpleNamespace(librelane=False, orfs=False, pdk="sky130A")
    for runner in IF.KNOBS:
        IF.refuse_out_of_scope(project, args, runner=runner)
    assert IF.scope_exit_after_pdk(project, "sky130A") is None
    assert IF.scope_exit_if_analog(project, True) is None
    IF.require_no_analog(IF.IMPL_DEFAULT, True, "x")


def test_scope_after_resolution_follows_the_record(project, capsys):
    IF.write_record(project, "librelane", resolved_by="t")
    assert IF.scope_exit_after_pdk(project, "gf180mcuD") is None
    assert IF.scope_exit_after_pdk(project, "sky130A") == 2
    assert f"REFUSED: {IF.IMPL_PDK_UNSUPPORTED}" in capsys.readouterr().err
    assert IF.scope_exit_if_analog(project, False) is None
    assert IF.scope_exit_if_analog(project, True) == 2
    assert f"REFUSED: {IF.IMPL_ANALOG_UNSUPPORTED}" in capsys.readouterr().err


def test_phase3_judges_the_pdk_auto_resolved_to(monkeypatch, tmp_path, capsys):
    """Drive the real phase3 main, wired, to the point where it has resolved
    `auto`: a resolution outside the scope refuses, one inside proceeds."""
    import phase3_one_shot_runner as R

    class _Past(Exception):
        pass

    monkeypatch.setattr(IF, "WIRED_RUNNERS",
                        frozenset({"phase3_one_shot_runner"}))
    monkeypatch.setattr(R._canonical_admission, "admit_span",
                        lambda *a, **k: SimpleNamespace(admitted=True,
                                                        reason="ADMITTED",
                                                        detail=""))
    monkeypatch.setattr(R, "_delivery_admission_refusal", lambda p: None)

    def _run(project, name):
        monkeypatch.setattr(R, "_detect_pdk",
                            lambda p, o=None: SimpleNamespace(name=name))

        def _past(*a, **k):
            raise _Past()
        monkeypatch.setattr(R, "commercial_pdk_fallback_guard", _past)
        monkeypatch.setattr(sys, "argv", ["phase3_one_shot_runner",
                                          str(project), "--librelane"])
        try:
            return R.main()
        except _Past:
            return "past"

    assert _run(_make(tmp_path / "a"), "sky130A") == 2
    assert f"REFUSED: {IF.IMPL_PDK_UNSUPPORTED}" in capsys.readouterr().err
    assert _run(_make(tmp_path / "b"), "gf180mcuD") == "past"


def test_the_front_door_judges_its_own_run_analog():
    """The check follows `run_analog = _need_analog(...)` directly: one
    decision, not a second copy of it."""
    tree = ast.parse((PROGRAMS / "vibe_ic_one_shot_runner.py").read_text())
    main = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    body = main.body
    i = next(k for k, st in enumerate(body)
             if isinstance(st, ast.Assign)
             and ast.unparse(st.targets[0]) == "run_analog")
    assert "_need_analog" in ast.unparse(body[i].value)
    nxt = ast.unparse(body[i + 1])
    assert "_impl_flow.scope_exit_if_analog(project, run_analog)" in nxt
    assert "return _impl_rc" in ast.unparse(body[i + 2])
