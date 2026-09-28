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

import json
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


_PDK_RUNNERS = ("vibe_ic_one_shot_runner", "phase23_one_shot_runner",
                "phase3_one_shot_runner")


def _pdk(runner):
    return ["--pdk", "gf180mcuD"] if runner in _PDK_RUNNERS else []


@pytest.mark.parametrize("runner", _PDK_RUNNERS)
def test_an_auto_pdk_is_refused_at_the_gate(project, runner):
    """Wave-5 review: `auto` resolves only after the mode is recorded, and
    phase 3's resolver never answers gf180mcuD for it. Refused HERE, before
    anything is recorded or run, with a remedy that works."""
    before = _tree(project)
    cp = _run_main(runner, project, "--librelane")
    _refused(cp, IF.IMPL_PDK_UNSUPPORTED)
    assert "name --pdk gf180mcuD" in cp.stderr
    assert _tree(project) == before
    assert not IF.record_path(project).exists()


@pytest.mark.parametrize("runner", sorted(set(IF.KNOBS) - {"analog_one_shot_runner"}))
@pytest.mark.parametrize("where", ["input/pdk_local/vendor/sram_1k.lef",
                                   "phase3/analog/hardmacro/adc/adc.lef"])
def test_staged_macros_are_refused(project, runner, where):
    lef = project / where
    lef.parent.mkdir(parents=True)
    lef.write_text("MACRO m\nEND m\n")
    before = _tree(project)
    cp = _run_main(runner, project, "--librelane", *_pdk(runner))
    _refused(cp, IF.IMPL_MACROS_UNSUPPORTED)
    assert where in cp.stderr
    assert _tree(project) == before


def test_the_default_is_never_refused_for_scope(project):
    (project / "input" / "pdk_local" / "v").mkdir(parents=True)
    (project / "input" / "pdk_local" / "v" / "m.gds").write_bytes(b"\0")
    for pdk in ("sky130A", "auto", ""):
        args = SimpleNamespace(librelane=False, orfs=False, pdk=pdk)
        for runner in IF.KNOBS:
            IF.refuse_out_of_scope(project, args, runner=runner)
    assert IF.scope_refusal_after_pdk(project, "sky130A") is None
    assert IF.analog_scope_refusal(project, True) is None
    assert IF.analog_scope_refusal(project, True, requested=None) is None
    IF.require_no_analog(IF.IMPL_DEFAULT, True, "x")


def test_a_refusal_after_the_record_names_a_remedy_that_works(tmp_path):
    """Wave-5 review: after the record, "run the default flow" on the same
    project is refused (IMPL_MODE_CONFLICT). The remedy names a fresh clone,
    and a fresh clone in the default flow resolves."""
    p = _make(tmp_path / "proj")
    IF.write_record(p, "librelane", resolved_by="t")
    assert IF.scope_refusal_after_pdk(p, "gf180mcuD") is None
    for exc in (IF.scope_refusal_after_pdk(p, "sky130A"),
                IF.analog_scope_refusal(p, True)):
        assert "fresh project clone" in str(exc)
    assert IF.analog_scope_refusal(p, False) is None
    with pytest.raises(IF.ImplRefusal):
        IF.resolve(p, None)                 # the same project: refused
    assert IF.resolve(_make(tmp_path / "clone"), None) == IF.IMPL_DEFAULT
    # before the record, the remedy is the default flow itself
    early = IF.analog_scope_refusal(_make(tmp_path / "early"), True,
                                    requested="librelane")
    assert "run the default flow (no flag), which runs it" in str(early)


@pytest.mark.parametrize("case", ["named_pdk", "auto_pdk", "macro", "analog_runner"])
def test_gate_refusal_on_a_recorded_project_names_a_fresh_clone(tmp_path, case):
    p = _make(tmp_path / case)
    IF.write_record(p, "librelane", resolved_by="test")
    runner = "analog_one_shot_runner" if case == "analog_runner" else "phase3_one_shot_runner"
    argv = ["--librelane"]
    if runner == "phase3_one_shot_runner":
        argv += ["--pdk", "auto" if case == "auto_pdk" else
                 "sky130A" if case == "named_pdk" else "gf180mcuD"]
    if case == "macro":
        macro = p / "input/pdk_local/vendor/m.lef"
        macro.parent.mkdir(parents=True)
        macro.write_text("MACRO m\nEND m\n")
    cp = _run_main(runner, p, *argv)
    assert cp.returncode == 2, cp.stderr[-2000:]
    assert "fresh project clone" in cp.stderr
    with pytest.raises(IF.ImplRefusal, match=IF.IMPL_MODE_CONFLICT):
        IF.resolve(p, None)


def test_phase3_backstop_refuses_with_a_report(monkeypatch, tmp_path, capsys):
    """The BACKSTOP behind the gate: phase 3, wired, with a named in-scope
    --pdk. Its resolver is stubbed to disagree -- the only way to reach the
    backstop now that `auto` and an out-of-scope name are refused at the gate
    -- and the refusal carries its reason class in a phase-3 report."""
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
                                          str(project), "--librelane",
                                          "--pdk", "gf180mcuD"])
        try:
            return R.main()
        except _Past:
            return "past"

    a = _make(tmp_path / "a")
    assert _run(a, "sky130A") == 4
    err = capsys.readouterr().err
    assert f"REFUSED: {IF.IMPL_PDK_UNSUPPORTED}" in err
    assert "fresh project clone" in err
    rep = json.loads((a / "reports" / "phase3" / "impl_scope_refusal.json")
                     .read_text())
    assert rep["reason_class"] == IF.IMPL_PDK_UNSUPPORTED
    assert _run(_make(tmp_path / "b"), "gf180mcuD") == "past"


def test_the_front_door_refuses_declared_analog_before_recording(
        monkeypatch, tmp_path, capsys):
    import vibe_ic_one_shot_runner as V
    monkeypatch.setattr(IF, "WIRED_RUNNERS",
                        frozenset({"vibe_ic_one_shot_runner"}))

    def _spawn(*a, **k):
        raise AssertionError("nothing may be spawned")

    monkeypatch.setattr(V, "_run_phase", _spawn)
    p = _make(tmp_path / "proj")
    (p / "input" / "analog_block_list.json").write_text('{"blocks": ["adc"]}')
    monkeypatch.setattr(sys, "argv", ["vibe_ic_one_shot_runner", str(p),
                                      "--no-dashboard", "--librelane",
                                      "--pdk", "gf180mcuD"])
    assert V.main() == 2
    err = capsys.readouterr().err
    assert f"REFUSED: {IF.IMPL_ANALOG_UNSUPPORTED}" in err
    assert "run the default flow (no flag)" in err
    assert not IF.record_path(p).exists()


def test_phase1_revealed_analog_refusal_reaches_the_report_tail(
        monkeypatch, tmp_path, capsys):
    """The real front door must stop dispatch and publish the named refusal."""
    import ai_signed_judgement as AI
    import vibe_ic_one_shot_runner as V

    project = _make(tmp_path / "revealed")
    spawned = []
    monkeypatch.setattr(IF, "WIRED_RUNNERS",
                        frozenset({"vibe_ic_one_shot_runner"}))
    monkeypatch.setattr(V, "_phase1_decision", lambda *a, **k: (True, "docs"))
    monkeypatch.setattr(V, "_capture_container_image", lambda *a, **k: {})
    monkeypatch.setattr(V, "_capture_pdk_revision", lambda *a, **k: {})
    monkeypatch.setattr(V._pl, "emit_final_summary", lambda *a, **k: True)
    monkeypatch.setattr(V._pl, "emit_steps_view",
                        lambda *a, **k: {"status": "OK"})
    monkeypatch.setattr(AI, "pending", lambda *a, **k: {})

    def _spawn(label, runner, argv, env=None):
        spawned.append(runner.name)
        if runner.name == "phase1_one_shot_runner.py":
            report = V._pl.report_path(project, "phase1_one_shot.json")
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text('{"verdict": "PASS"}\n')
            analog = V._pl.analog_dir(project) / "analog_block_list.json"
            analog.parent.mkdir(parents=True, exist_ok=True)
            analog.write_text('{"blocks": ["a"]}\n')
            return 0
        return 2

    monkeypatch.setattr(V, "_run_phase", _spawn)
    argv = ["vibe_ic_one_shot_runner", str(project), "--no-dashboard",
            "--librelane", "--pdk", "gf180mcuD"]
    monkeypatch.setattr(sys, "argv", argv)
    rc = V.main()
    report = json.loads(V._pl.report_path(project,
                                          "vibe_ic_one_shot.json").read_text())
    assert rc != 0
    assert report["verdict"] == "NOT_MEASURED"
    assert any(IF.IMPL_ANALOG_UNSUPPORTED in reason
               for reason in report["verdict_reasons"])
    assert [row["name"] for row in report["phases"]] == [
        "phase1", "phase2", "analog", "phase3", "mixed_signal"]
    assert [row["verdict"] for row in report["phases"] if row["name"] == "analog"] == [
        "REFUSED-IMPL_ANALOG_UNSUPPORTED"]
    assert spawned == ["phase1_one_shot_runner.py"]
    assert "fresh project clone" in capsys.readouterr().err

    # A second invocation sees the record and must give a usable remedy too.
    monkeypatch.setattr(sys, "argv", argv)
    assert V.main() == 2
    assert "fresh project clone" in capsys.readouterr().err
