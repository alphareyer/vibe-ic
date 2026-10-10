"""Phase23 keeps the Phase2 child on the caller's declared route.

These tests exercise the chained runner's real child invocation seam.  They
do not run an EDA tool or author any generated design documents.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import execution_policy as policy  # noqa: E402
import phase23_one_shot_runner as phase23  # noqa: E402
from _hostpaths import require_repo  # noqa: E402


def _flag(argv: list[str], name: str, default=None):
    return argv[argv.index(name) + 1] if name in argv else default


def _run_chain(monkeypatch, tmp_path: Path, extra: list[str], *, p2_verdict="PASS"):
    project = tmp_path / "project"
    project.mkdir()
    calls: list[tuple[str, list[str]]] = []

    def fake_run(command, *, pass_fds=()):
        assert pass_fds == ()
        runner, argv = Path(command[1]), list(command[2:])
        calls.append((runner.name, list(argv)))
        report_name = ("phase2_one_shot.json"
                       if runner.name == "design_one_shot_runner.py"
                       else "phase3_one_shot.json")
        report = phase23._pl.report_path(project, report_name)
        report.parent.mkdir(parents=True, exist_ok=True)
        verdict = p2_verdict if runner.name == "design_one_shot_runner.py" else "PASS"
        report.write_text(json.dumps({"verdict": verdict}) + "\n")
        return SimpleNamespace(returncode=0 if verdict == "PASS" else 1)

    monkeypatch.delenv(policy.ENV, raising=False)
    monkeypatch.delenv(policy._CAPABILITY_FD_ENV, raising=False)
    monkeypatch.setattr(phase23.subprocess, "run", fake_run)
    monkeypatch.setattr(phase23._pl, "emit_steps_view",
                        lambda *_args, **_kwargs: {"status": "OK"})
    monkeypatch.setattr(phase23._pl, "emit_final_summary",
                        lambda *_args, **_kwargs: True)
    monkeypatch.setattr(phase23, "_update_streak", lambda *_args: None)
    monkeypatch.setattr(
        sys, "argv", ["phase23_one_shot_runner.py", str(project), *extra])

    assert phase23.main() == (0 if p2_verdict == "PASS" else 1)
    return project, calls


def test_phase2_receives_explicit_pdk_and_default_mode(monkeypatch, tmp_path):
    _project, calls = _run_chain(
        monkeypatch, tmp_path,
        ["--pdk", "gf180mcuD", "--execution-mode", "default"])

    p2_name, p2_argv = calls[0]
    p3_name, p3_argv = calls[1]
    assert p2_name == "design_one_shot_runner.py"
    assert _flag(p2_argv, "--pdk", "auto") == "gf180mcuD"
    assert _flag(p2_argv, "--execution-mode", "default-implicit") == "default"
    assert p3_name == "phase3_one_shot_runner.py"
    assert _flag(p3_argv, "--pdk") == "gf180mcuD"


def test_explicit_default_mode_reaches_phase2_without_policy_transport(monkeypatch, tmp_path):
    _project, calls = _run_chain(
        monkeypatch, tmp_path, ["--execution-mode", "default"])
    assert _flag(calls[0][1], "--execution-mode", "default-implicit") == "default"
    assert policy.ENV not in phase23._os.environ


def test_phase2_parser_consumes_the_forwarded_selection(monkeypatch, tmp_path):
    import design_one_shot_runner as design
    _project, calls = _run_chain(
        monkeypatch, tmp_path,
        ["--pdk", "gf180mcuD", "--execution-mode", "default"])
    observed = {}

    class SelectionObserved(Exception):
        pass

    def observe(_policy, project, **parameters):
        observed.update(project=str(project), **parameters,
                        mode=_policy.request()["mode"])
        raise SelectionObserved

    # Stop at the first owning Phase-2 boundary, after its real argparse and
    # execution-policy configuration, before any design/EDA producer runs.
    monkeypatch.setattr(design, "_bootstrap_execution_policy", observe)
    monkeypatch.setattr(sys, "argv", [str(design.__file__), *calls[0][1]])
    with pytest.raises(SelectionObserved):
        design.main()
    assert observed["pdk_name"] == "gf180mcuD"
    assert observed["mode"] == "default"


def test_report_records_actual_child_argv(monkeypatch, tmp_path):
    project, calls = _run_chain(
        monkeypatch, tmp_path,
        ["--pdk", "gf180mcuD", "--execution-mode", "default"])
    report = json.loads(phase23._pl.report_path(project, "phase23_one_shot.json").read_text())
    for name, (runner, argv) in zip(("phase2", "phase3"), calls):
        assert report[name]["invocation"]["argv"] == [
            sys.executable, str(PROGRAMS / runner), *argv]


def test_skip_phase3_is_also_forwarded_to_phase2(monkeypatch, tmp_path):
    _project, calls = _run_chain(
        monkeypatch, tmp_path, ["--skip-phase3", "--skip-hardware"])

    assert len(calls) == 1
    name, p2_argv = calls[0]
    assert name == "design_one_shot_runner.py"
    assert "--skip-phase3" in p2_argv
    assert "--skip-hardware" in p2_argv


def test_phase2_failure_still_halts_phase3(monkeypatch, tmp_path):
    project, calls = _run_chain(monkeypatch, tmp_path, [], p2_verdict="FAIL")
    assert len(calls) == 1
    report = json.loads(phase23._pl.report_path(project, "phase23_one_shot.json").read_text())
    assert report["phase3"].get("invocation") is None
    assert report["phase3"]["verdict"] == "SKIPPED"


def test_omitted_route_and_mode_keep_existing_defaults(monkeypatch, tmp_path):
    _project, calls = _run_chain(monkeypatch, tmp_path, [])

    p2_argv = calls[0][1]
    p3_argv = calls[1][1]
    assert "--pdk" not in p2_argv
    assert "--execution-mode" not in p2_argv
    assert _flag(p3_argv, "--pdk") == "auto"


def test_registered_pdks_are_forwarded_without_substitution(monkeypatch, tmp_path):
    registry = require_repo("vibe-ic-marketplace", "plugins", "vibe-ic",
                            "programs", "pdk_registry.json")
    names = [row["name"] for row in json.loads(registry.read_text())["pdks"]]
    assert names
    for index, name in enumerate(names):
        case = tmp_path / str(index)
        case.mkdir()
        _project, calls = _run_chain(monkeypatch, case, ["--pdk", name])
        assert [_flag(argv, "--pdk", "auto") for _runner, argv in calls] == [name, name]


def test_existing_phase2_options_and_phase3_geometry_survive(monkeypatch, tmp_path):
    _project, calls = _run_chain(monkeypatch, tmp_path, [
        "--pdk", "gf180mcuD", "--top-name", "fixture_top",
        "--container", "fixture-container", "--max-rtl-repair-retries", "2",
        "--skip-hardware", "--force-rtl-regen", "--die-um", "800x600",
        "--util", "0.35", "--allow-oss-pdk-fallback"])
    p2, p3 = [argv for _name, argv in calls]
    assert _flag(p2, "--max-rtl-repair-retries") == "2"
    assert "--skip-hardware" in p2 and "--force-rtl-regen" in p2
    for argv in (p2, p3):
        assert _flag(argv, "--top-name") == "fixture_top"
        assert _flag(argv, "--container") == "fixture-container"
    assert _flag(p3, "--die-um") == "800x600"
    assert _flag(p3, "--util") == "0.35"
    assert "--allow-oss-pdk-fallback" in p3
