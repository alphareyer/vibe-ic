"""Issue #2856 normal-runner integration and receipt binding."""
from __future__ import annotations

import json
import hashlib
import shutil
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pulse_width_rearm"
CONTRACT = FIXTURES / "contract.json"
GOOD = FIXTURES / "compliant.sv"
BAD = FIXTURES / "level_reacquire_bad.sv"

import sys
sys.path.insert(0, str(PROGRAMS))
import design_one_shot_runner as runner  # noqa: E402
import pulse_width_rearm_conformance_check as checker  # noqa: E402


@pytest.mark.skipif(not (shutil.which("iverilog") and shutil.which("vvp")),
                    reason="normal consumer requires native iverilog/vvp")
def test_normal_reference_tb_consumer_gates_on_actual_rtl(tmp_path):
    project = tmp_path / "project"
    (project / "phase1/input_prompt").mkdir(parents=True)
    (project / "phase2/stage1/rtl").mkdir(parents=True)
    (project / "phase1/input_prompt/contract.json").write_bytes(CONTRACT.read_bytes())
    rtl = project / "phase2/stage1/rtl/pulse_width_rearm_fixture.sv"
    rtl.write_bytes(GOOD.read_bytes())

    rc, report, report_path = runner._run_pulse_width_rearm_normal_gate(
        project, "pulse_width_rearm_fixture", None, "normal-test-image")
    assert rc == 0
    assert report["verdict"] == "PASS"
    assert report["normal_consumer"] is True
    assert report_path.is_file()

    rtl.write_bytes(BAD.read_bytes())
    rc, report, _ = runner._run_pulse_width_rearm_normal_gate(
        project, "pulse_width_rearm_fixture", None, "normal-test-image")
    assert rc == 1
    assert report["verdict"] == "FAIL"
    assert report["measured"] is True
    assert (report.get("counts") or {}).get("bad", 0) >= 1


def test_normal_consumer_report_is_hash_bound(tmp_path):
    project = tmp_path / "project"
    (project / "phase1/input_prompt").mkdir(parents=True)
    (project / "phase2/stage1/rtl").mkdir(parents=True)
    (project / "phase1/input_prompt/contract.json").write_bytes(CONTRACT.read_bytes())
    rtl = project / "phase2/stage1/rtl/pulse_width_rearm_fixture.sv"
    rtl.write_bytes(GOOD.read_bytes())
    # The report is emitted by the production helper and must carry the exact
    # source/RTL/typed-contract identities consumed by the checker.
    report_path = project / "reports/phase2/gates/pulse_width_rearm_conformance.json"
    rc, report, _ = runner._run_pulse_width_rearm_normal_gate(
        project, "pulse_width_rearm_fixture", None, "normal-test-image")
    if report.get("verdict") == "NOT_MEASURED":
        pytest.skip("native simulator unavailable")
    assert rc == 0
    disk = json.loads(report_path.read_text())
    assert disk["source_contract_sha256"] == disk["source_file_sha256"]
    assert disk["rtl_sha256"]
    assert disk["contract"]["source_sha256"] == disk["source_file_sha256"]


@pytest.mark.skipif(not (shutil.which("iverilog") and shutil.which("vvp")),
                    reason="normal consumer requires native iverilog/vvp")
def test_reference_tb_wrapper_does_not_let_ordinary_pass_hide_pulse_fail(tmp_path,
                                                                         monkeypatch):
    project = tmp_path / "project"
    (project / "phase1/input_prompt").mkdir(parents=True)
    (project / "phase2/stage1/rtl").mkdir(parents=True)
    (project / "phase1/input_prompt/contract.json").write_bytes(CONTRACT.read_bytes())
    (project / "phase2/stage1/rtl/pulse_width_rearm_fixture.sv").write_bytes(BAD.read_bytes())
    monkeypatch.setattr(
        runner, "_step_reference_tb_without_pulse_gate",
        lambda *args, **kwargs: runner.StepResult("reference_tb", "PASS", detail="ordinary TB PASS"))
    result = runner.step_reference_tb(
        project, "pulse_width_rearm_fixture", None, "normal-test-image")
    assert result.status == "FAIL"
    assert "measured FAIL" in result.detail


def test_reference_tb_refuses_applicable_native_skip_before_ordinary_tb(tmp_path,
                                                                        monkeypatch):
    """An applicable native refusal cannot be laundered by the ordinary TB."""
    project = tmp_path / "project"
    project.mkdir()
    report_path = project / "pulse.json"
    report_path.write_text("{}\n")
    ordinary_called = {"value": False}

    def ordinary_pass(*args, **kwargs):
        ordinary_called["value"] = True
        return runner.StepResult("reference_tb", "PASS", detail="ordinary TB PASS")

    monkeypatch.setattr(
        runner, "_run_pulse_width_rearm_normal_gate",
        lambda *args, **kwargs: (0, {
            "verdict": "SKIP", "measured": False,
            "reason": "iverilog/vvp unavailable; native result is NOT_MEASURED",
        }, report_path),
    )
    monkeypatch.setattr(runner, "_step_reference_tb_without_pulse_gate", ordinary_pass)
    result = runner.step_reference_tb(project, "pulse_width_rearm_fixture", None,
                                      "normal-test-image")
    assert result.status == "NOT_MEASURED"
    assert "SKIP" in result.detail or "NOT_MEASURED" in result.detail
    assert ordinary_called["value"] is False


@pytest.mark.parametrize(
    "pulse_rc,pulse_report",
    [
        (0, {"verdict": "PASS", "measured": False, "reason": "unmeasured"}),
        (0, {"verdict": "MYSTERY", "measured": True, "reason": "unknown"}),
        (7, {"verdict": "PASS", "measured": True, "reason": "bad rc"}),
    ],
)
def test_reference_tb_rejects_every_unusable_native_state(tmp_path, monkeypatch,
                                                           pulse_rc, pulse_report):
    project = tmp_path / "project"
    project.mkdir()
    report_path = project / "pulse.json"
    report_path.write_text("{}\n")
    ordinary_called = {"value": False}

    def ordinary_pass(*args, **kwargs):
        ordinary_called["value"] = True
        return runner.StepResult("reference_tb", "PASS", detail="ordinary TB PASS")

    monkeypatch.setattr(runner, "_run_pulse_width_rearm_normal_gate",
                        lambda *args, **kwargs: (pulse_rc, pulse_report, report_path))
    monkeypatch.setattr(runner, "_step_reference_tb_without_pulse_gate", ordinary_pass)
    result = runner.step_reference_tb(project, "pulse_width_rearm_fixture", None,
                                      "normal-test-image")
    assert result.status == "NOT_MEASURED"
    assert ordinary_called["value"] is False


def test_reference_tb_allows_ordinary_tb_only_for_explicit_not_applicable(tmp_path,
                                                                           monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    report_path = project / "pulse.json"
    report_path.write_text("{}\n")
    ordinary_called = {"value": False}

    def ordinary_pass(*args, **kwargs):
        ordinary_called["value"] = True
        return runner.StepResult("reference_tb", "PASS", detail="ordinary TB PASS")

    monkeypatch.setattr(
        runner, "_run_pulse_width_rearm_normal_gate",
        lambda *args, **kwargs: (0, {"verdict": "NOT_APPLICABLE", "measured": False,
                                     "reason": "no declared pulse contract"}, report_path),
    )
    monkeypatch.setattr(runner, "_step_reference_tb_without_pulse_gate", ordinary_pass)
    result = runner.step_reference_tb(project, "pulse_width_rearm_fixture", None,
                                      "normal-test-image")
    assert result.status == "PASS"
    assert ordinary_called["value"] is True


def test_run_project_refuses_live_replacement_before_publishing_pass(tmp_path, monkeypatch):
    """A post-measurement source/RTL replacement must invalidate the receipt."""
    project = tmp_path / "project"
    (project / "phase1/input_prompt").mkdir(parents=True)
    rtl_dir = project / "phase2/stage1/rtl"
    rtl_dir.mkdir(parents=True)
    (project / "phase1/input_prompt/contract.json").write_bytes(CONTRACT.read_bytes())
    rtl = rtl_dir / "pulse_width_rearm_fixture.sv"
    rtl.write_bytes(GOOD.read_bytes())
    report_path = project / "reports/phase2/gates/pulse.json"
    source = project / "phase1/input_prompt/contract.json"
    original_source = source.read_bytes()
    original_source_hash = hashlib.sha256(original_source).hexdigest()
    original_hash = hashlib.sha256(rtl.read_bytes()).hexdigest()
    replacement_source = original_source.replace(b'"max_width_cycles": 4',
                                                  b'"max_width_cycles": 5')

    def replace_after_measurement(*args, **kwargs):
        source.write_bytes(replacement_source)
        rtl.write_bytes(BAD.read_bytes())
        return 0, {
            "verdict": "PASS", "measured": True,
            "source_file_sha256": original_source_hash,
            "rtl_sha256": original_hash,
            "context_sha256": {},
            "contract": {"source_sha256": original_source_hash},
            "stimulus": {"tb_sha256": "frozen-stimulus"},
        }

    monkeypatch.setattr(checker, "run_check", replace_after_measurement)
    rc, report = checker.run_project(
        project, [rtl], "pulse_width_rearm_fixture", report_path=report_path,
        runtime_identity={"container": "normal-test-image"})
    assert rc != 0
    assert report["verdict"] == "NOT_MEASURED"
    assert report["measured"] is False
    assert "changed" in report["reason"].lower() or "stable" in report["reason"].lower()
    assert report["source_file_sha256"] == original_source_hash
    assert report["source_contract_sha256"] == original_source_hash
    assert report["rtl_sha256"] == original_hash
    assert report["rtl_file_sha256"][str(rtl)] == original_hash
    assert report_path.is_file()
    published = json.loads(report_path.read_text())
    assert published["verdict"] == "NOT_MEASURED"
    assert published["source_contract_sha256"] == original_source_hash
    assert published["rtl_sha256"] == original_hash


def test_run_project_measured_fail_survives_publication_drift(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / "phase1/input_prompt").mkdir(parents=True)
    rtl_dir = project / "phase2/stage1/rtl"
    rtl_dir.mkdir(parents=True)
    source = project / "phase1/input_prompt/contract.json"
    source.write_bytes(CONTRACT.read_bytes())
    rtl = rtl_dir / "pulse_width_rearm_fixture.sv"
    rtl.write_bytes(GOOD.read_bytes())
    original_source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    original_rtl_hash = hashlib.sha256(rtl.read_bytes()).hexdigest()
    replacement_source = source.read_bytes().replace(b'"max_width_cycles": 4',
                                                      b'"max_width_cycles": 5')

    def measured_fail_then_drift(*args, **kwargs):
        source.write_bytes(replacement_source)
        rtl.write_bytes(BAD.read_bytes())
        return 1, {
            "verdict": "FAIL", "measured": True, "reason": "measured pulse violation",
            "source_file_sha256": original_source_hash,
            "rtl_sha256": original_rtl_hash, "context_sha256": {},
            "contract": {"source_sha256": original_source_hash},
            "stimulus": {"tb_sha256": "frozen-stimulus"},
        }

    monkeypatch.setattr(checker, "run_check", measured_fail_then_drift)
    report_path = project / "reports/phase2/gates/pulse.json"
    rc, report = checker.run_project(
        project, [rtl], "pulse_width_rearm_fixture", report_path=report_path)
    assert rc == 1
    assert report["verdict"] == "FAIL"
    assert report["measured"] is True
    assert report["input_stability"]["stable"] is False
    assert "drift" in report["reason"]
    assert json.loads(report_path.read_text())["verdict"] == "FAIL"
