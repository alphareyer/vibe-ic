"""Issue 2881 — publish output refusals without weakening artifact checks."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import design_one_shot_runner as runner  # noqa: E402


def _summary(path: str, *, path_kind: str | None = "diagnostic") -> dict:
    refusal = {
        "finding": "PHASE1_RTL_OUTPUT_PROVENANCE_REFUSED",
        "reason": "PROJECT_SYMLINK_NOT_ISOLATABLE",
        "path": path,
        "detail": "the isolated project contains an external source symlink",
    }
    if path_kind is not None:
        refusal["path_kind"] = path_kind
    return {"steps": [{}, {}, {}, {"extras": {"output_refusal": refusal}}]}


def test_diagnostic_output_refusal_path_is_published_and_preserved(tmp_path):
    project = tmp_path / "spmcanonical1006"
    project.mkdir()
    diagnostic = (
        "/tmp/vibeic-rtl-step-8xymyxxa/spmcanonical1006/source")
    out = project / "reports" / "orchestrator" / "phase2_one_shot.json"

    runner._write_phase2_report(out, _summary(diagnostic), project)

    published = json.loads(out.read_text())
    refusal = published["steps"][3]["extras"]["output_refusal"]
    assert refusal["path"] == diagnostic
    assert refusal["path_kind"] == "diagnostic"


def test_retained_artifact_path_in_refusal_is_still_rejected(tmp_path):
    project = tmp_path / "spmcanonical1006"
    project.mkdir()
    retained = (
        "/tmp/vibeic-rtl-step-8xymyxxa/spmcanonical1006/"
        "phase2/stage1/rtl/spm.v")
    out = project / "reports" / "orchestrator" / "phase2_one_shot.json"

    with pytest.raises(runner._RecordNamesRelocatedCopy):
        runner._write_phase2_report(
            out, _summary(retained, path_kind="retained_artifact"), project)
    assert not out.exists()


def test_untyped_external_refusal_path_remains_fail_closed(tmp_path):
    project = tmp_path / "spmcanonical1006"
    project.mkdir()
    retained = (
        "/tmp/vibeic-rtl-step-8xymyxxa/spmcanonical1006/"
        "phase2/stage1/rtl/spm.v")
    out = project / "reports" / "orchestrator" / "phase2_one_shot.json"

    with pytest.raises(runner._RecordNamesRelocatedCopy):
        runner._write_phase2_report(out, _summary(retained, path_kind=None), project)


def test_diagnostic_label_outside_output_refusal_remains_fail_closed(tmp_path):
    project = tmp_path / "spmcanonical1006"
    project.mkdir()
    retained = (
        "/tmp/vibeic-rtl-step-8xymyxxa/spmcanonical1006/"
        "phase2/stage1/rtl/spm.v")
    out = project / "reports" / "orchestrator" / "phase2_one_shot.json"
    summary = {"steps": [{}, {}, {}, {"extras": {
        "other_diagnostic": {"path": retained, "path_kind": "diagnostic"}
    }}]}

    with pytest.raises(runner._RecordNamesRelocatedCopy):
        runner._write_phase2_report(out, summary, project)


def test_kept_in_tree_artifact_remains_acceptable(tmp_path):
    project = tmp_path / "spmcanonical1006"
    kept = project / "phase2" / "stage1" / "rtl" / "spm.v"
    kept.parent.mkdir(parents=True)
    kept.write_text("module spm; endmodule\n")
    out = project / "reports" / "orchestrator" / "phase2_one_shot.json"

    runner._write_phase2_report(
        out,
        _summary(str(kept), path_kind="retained_artifact"),
        project,
    )
    assert json.loads(out.read_text())["steps"][3]["extras"][
        "output_refusal"]["path"] == str(kept)
