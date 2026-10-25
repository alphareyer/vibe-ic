"""PR #3594: bind the fixed refinement input at actual dispatcher snapshots."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import benchmark_dispatch as bd  # noqa: E402


REFINEMENT = Path("phase1/behavioral_refinement.facts.yaml")


def _project(root: Path) -> Path:
    project = root / "project"
    prompt = project / "input/phase1_prompt.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("Design a neutral state machine.\n")
    rtl = project / "phase2/stage1/rtl/dut.v"
    rtl.parent.mkdir(parents=True)
    rtl.write_text("module dut; endmodule\n")
    return project


def _author(project: Path, raw: bytes = b"facts: []\n") -> Path:
    path = project / REFINEMENT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def _invocation(project: Path, monkeypatch) -> tuple[list[str], bd._ProcessOutcome]:
    calls = []

    def stopped(argv, **kwargs):
        calls.append((list(argv), kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bd.subprocess, "run", stopped)
    argv = [sys.executable, str(PROGRAMS / "vibe_ic_one_shot_runner.py"),
            str(project), "--no-dashboard"]
    outcome = bd._RunnerBudget(1, 1, 0).run(argv)
    assert len(calls) == 1
    assert outcome.invocation["material_before"] == outcome.invocation["material_after"]
    return argv, outcome


def _d1_project(root: Path) -> Path:
    project = _project(root)
    docs = project / "phase1/generated_docs"
    docs.mkdir(parents=True)
    (docs / "L1.json").write_text('{"schema": 1}\n')
    report = project / "reports/orchestrator/phase1_one_shot.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({"verdict": "PASS"}) + "\n")
    return project


def test_absence_preserves_the_existing_invocation_material_shape(tmp_path, monkeypatch):
    project = _project(tmp_path)
    before = bd._runner_material_snapshot(project)
    assert "behavioral_refinement_input" not in before
    argv, outcome = _invocation(project, monkeypatch)
    diagnostic = bd._runner_diagnostics(
        outcome, argv, project, require_phase2=False)
    assert diagnostic["status"] == "NO_FRESH_REPORTS"
    assert "reason" not in diagnostic


def test_first_proposal_and_changed_bytes_invalidate_actual_invocation(tmp_path,
                                                                      monkeypatch):
    project = _project(tmp_path)
    argv, absent = _invocation(project, monkeypatch)
    path = _author(project)
    first = bd._runner_diagnostics(absent, argv, project, require_phase2=False)
    assert first["status"] == "MATERIAL_MISMATCH"
    assert first["reason_class"] == "RUNNER_MATERIAL_UNBOUND"

    argv, proposed = _invocation(project, monkeypatch)
    path.write_bytes(b"facts:\n  - changed\n")
    changed = bd._runner_diagnostics(proposed, argv, project, require_phase2=False)
    assert changed["status"] == "MATERIAL_MISMATCH"
    assert changed["reason_class"] == "RUNNER_MATERIAL_UNBOUND"


def test_identical_author_bytes_retain_actual_invocation_currentness(tmp_path,
                                                                    monkeypatch):
    project = _project(tmp_path)
    path = _author(project)
    argv, outcome = _invocation(project, monkeypatch)
    raw = path.read_bytes()
    path.write_bytes(raw)
    diagnostic = bd._runner_diagnostics(
        outcome, argv, project, require_phase2=False)
    assert diagnostic["status"] == "NO_FRESH_REPORTS"
    assert "reason" not in diagnostic


def test_foreign_or_linked_author_input_is_refused_before_runner(tmp_path,
                                                                 monkeypatch):
    project = _project(tmp_path)
    foreign = tmp_path / "foreign.yaml"
    foreign.write_text("facts: []\n")
    path = project / REFINEMENT
    path.parent.mkdir(parents=True)
    path.symlink_to(foreign)
    calls = []
    monkeypatch.setattr(
        bd.subprocess, "run",
        lambda *args, **kwargs: calls.append((args, kwargs)))
    argv = [sys.executable, str(PROGRAMS / "vibe_ic_one_shot_runner.py"),
            str(project), "--no-dashboard"]
    outcome = bd._RunnerBudget(1, 1, 0).run(argv)
    assert calls == []
    assert "RUNNER_MATERIAL_UNBOUND" in outcome.error


def test_d1_launch_snapshot_binds_absence_addition_and_changed_generation(tmp_path):
    project = _d1_project(tmp_path)
    absent = bd._capture_d1_evidence(project)
    assert getattr(absent, "author_inputs", ()) == ()
    bd._assert_d1_snapshot_current(absent)

    path = _author(project)
    with pytest.raises(ValueError, match="D1_SNAPSHOT_AUTHOR_INPUT_SET_CHANGED"):
        bd._assert_d1_snapshot_current(absent)

    proposed = bd._capture_d1_evidence(project)
    assert [row[0] for row in getattr(proposed, "author_inputs", ())] == [path]
    bd._assert_d1_snapshot_current(proposed)
    path.write_bytes(path.read_bytes() + b"# changed\n")
    with pytest.raises(ValueError, match="D1_SNAPSHOT_GENERATION_CHANGED"):
        bd._assert_d1_snapshot_current(proposed)
