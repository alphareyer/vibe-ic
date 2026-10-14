"""Canonical subject identity through actual M1/M3 consumers, without an issued controller."""
import json

import pytest

import execution_mixed_subject as identity
import mixed_signal_m3_run as m3
import mixed_signal_top_lvs_run as m1


def test_ordinary_mixed_consumers_keep_resolved_local_identity(tmp_path, monkeypatch):
    monkeypatch.delenv("VIBEIC_EXECUTION_BINDING", raising=False)
    project = tmp_path / "local"
    project.mkdir()
    spelling = project / "."
    assert identity.project_subject(spelling, "M1") == str(project.resolve())
    assert m3.subject(spelling, "neutral_top") == {"project": str(project.resolve()), "top": "neutral_top"}
    assert m1._project_subject(spelling, "M1") == str(project.resolve())


@pytest.mark.parametrize("step", ["M1", "M2", "M3", "M4"])
def test_matching_worker_scope_resolves_exact_original_subject(tmp_path, monkeypatch, step):
    canonical = tmp_path / "canonical"
    staged = tmp_path / "staged"
    canonical.mkdir(); staged.mkdir()
    binding = {"step_id": step, "objective": {"project_subject": str(canonical.resolve())}}
    monkeypatch.setenv("VIBEIC_EXECUTION_BINDING", json.dumps(binding))
    assert identity.project_subject(staged, step) == str(canonical.resolve())
    assert m3.subject(staged, "neutral_top")["project"] == str(canonical.resolve())
    if step == "M1":
        assert m1._project_subject(staged, "M1") == str(canonical.resolve())
    assert not list(staged.iterdir())


@pytest.mark.parametrize("shape,reason", [
    ("malformed_json", "MIXED_ISSUED_BINDING_INVALID"),
    ("non_object", "MIXED_ISSUED_SUBJECT_MISMATCH"),
    ("unknown_row", "MIXED_ISSUED_SUBJECT_MISMATCH"),
    ("wrong_row", "MIXED_ISSUED_SUBJECT_MISMATCH"),
    ("missing_subject", "MIXED_ISSUED_SUBJECT_MISSING"),
    ("relative_subject", "MIXED_ISSUED_SUBJECT_MISSING"),
    ("file_subject", "MIXED_ISSUED_SUBJECT_INVALID"),
    ("noncanonical_subject", "MIXED_ISSUED_SUBJECT_INVALID"),
])
def test_subject_binding_refuses_wrong_scope_or_noncanonical_identity(tmp_path, monkeypatch, shape, reason):
    project = tmp_path / "canonical"
    project.mkdir()
    binding = {"step_id": "M1", "objective": {"project_subject": str(project.resolve())}}
    if shape == "malformed_json":
        raw = '{"step_id":'
    elif shape == "non_object":
        raw = "[]"
    else:
        if shape == "unknown_row":
            binding["step_id"] = "A8"
        elif shape == "wrong_row":
            binding["step_id"] = "M3"
        elif shape == "missing_subject":
            binding["objective"] = {}
        elif shape == "relative_subject":
            binding["objective"]["project_subject"] = "canonical"
        elif shape == "file_subject":
            file = tmp_path / "not_directory"
            file.write_text("source control\n")
            binding["objective"]["project_subject"] = str(file)
        elif shape == "noncanonical_subject":
            binding["objective"]["project_subject"] = str(project) + "/."
        raw = json.dumps(binding)
    monkeypatch.setenv("VIBEIC_EXECUTION_BINDING", raw)
    with pytest.raises(ValueError, match="^" + reason + "$"):
        identity.project_subject(project, "M1")


def test_m1_blocking_consumer_reports_row_identity_mismatch_before_other_material(tmp_path, monkeypatch):
    canonical = tmp_path / "canonical"
    staged = tmp_path / "staged"
    canonical.mkdir(); staged.mkdir()
    receipt = staged / "phase3/mixed_signal/m1_merge_receipt.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({"schema": "vibeic.mixed_signal.m1.v1", "subject": str(canonical.resolve())}))
    monkeypatch.setenv("VIBEIC_EXECUTION_BINDING", json.dumps({
        "step_id": "M3", "objective": {"project_subject": str(canonical.resolve())}}))
    got = m1.validate_current_receipt(staged, {})
    assert got == "M1 current receipt unavailable: MIXED_ISSUED_SUBJECT_MISMATCH"
    assert not (staged / "reports").exists()
