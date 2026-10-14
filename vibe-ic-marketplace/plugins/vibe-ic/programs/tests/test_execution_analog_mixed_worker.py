"""Real neutral Python process and source-path controls; no characterization tools."""
import hashlib
import json
import sys
import time

import pytest

import execution_analog_mixed_worker as worker
import execution_analog_local_transport as transport
import execution_modes as em
from test_execution_analog_local_transport import transport_case


@pytest.mark.parametrize("returncode", [0, 7])
def test_actual_worker_run_retains_real_process_outputs_hashes_and_failure(transport_case, returncode):
    project, parameters, receipts = transport_case
    records = project / "component-records"
    command = [sys.executable, "-c",
        "import sys; print('neutral component output'); print('neutral stderr',file=sys.stderr); raise SystemExit(" + str(returncode) + ")"]
    with transport.local_transport(receipts, time.monotonic() + 20, project=project, parameters=parameters):
        if returncode:
            with pytest.raises(em.Refusal) as refused:
                worker._run(command, records, "python-component", 20)
            assert refused.value.code == "ANALOG_NATIVE_PROCESS_FAILED"
        else:
            observed = worker._run(command, records, "python-component", 20)
    receipt = json.loads((records / "python-component.receipt.json").read_text())
    stdout = (records / "python-component.stdout").read_bytes()
    stderr = (records / "python-component.stderr").read_bytes()
    assert receipt["argv"] == command and receipt["rc"] == returncode
    assert stdout.endswith(b"neutral component output\n") and stderr == b"neutral stderr\n"
    assert receipt["stdout_sha256"] == hashlib.sha256(stdout).hexdigest()
    assert receipt["stderr_sha256"] == hashlib.sha256(stderr).hexdigest()
    assert receipt["observer_pid"] > 0
    if not returncode:
        assert observed == receipt
    transport_row = json.loads(receipts.read_text())
    assert transport_row["rc"] == receipt["rc"]
    assert transport_row["stdout"].encode() == stdout and transport_row["stderr"].encode() == stderr
    assert not (project / "phase3").exists()


def test_absolute_original_input_rebinds_to_current_staged_bytes(tmp_path, monkeypatch):
    original = tmp_path / "original"
    staged = tmp_path / "staged"
    original.mkdir(); staged.mkdir()
    (original / "deck.sp").write_text("* original source bytes\n")
    current = staged / "deck.sp"
    current.write_text("* current staged source bytes\n")
    binding = {"objective": {"original_project": str(original), "parameters": {"pdk_root": "input/pdk"}}}
    monkeypatch.setenv("VIBEIC_EXECUTION_BINDING", json.dumps(binding))
    got = worker._path(staged, str(original / "deck.sp"))
    assert got == current
    assert got.read_bytes() == b"* current staged source bytes\n"
    assert em.digest(got) != em.digest(original / "deck.sp")
    assert worker._path(staged, "deck.sp") == current


@pytest.mark.parametrize("shape,code", [
    ("absent", "ANALOG_NATIVE_INPUT_MISSING"),
    ("symlink", "ANALOG_NATIVE_INPUT_MISSING"),
    ("parent_escape", "UNSAFE_RELATIVE_PATH"),
    ("foreign_absolute", "ANALOG_EXTERNAL_NATIVE_INPUT_UNBOUND"),
])
def test_worker_refuses_unbound_or_noncurrent_input_paths(tmp_path, monkeypatch, shape, code):
    project = tmp_path / "project"
    project.mkdir()
    binding = {"objective": {"original_project": str(project), "parameters": {"pdk_root": "input/pdk"}}}
    monkeypatch.setenv("VIBEIC_EXECUTION_BINDING", json.dumps(binding))
    if shape == "symlink":
        outside = tmp_path / "external.sp"
        outside.write_text("* foreign source\n")
        (project / "linked.sp").symlink_to(outside)
        value = "linked.sp"
    elif shape == "parent_escape":
        value = "../external.sp"
    elif shape == "foreign_absolute":
        value = str(tmp_path / "external.sp")
    else:
        value = "absent.sp"
    with pytest.raises(em.Refusal) as refused:
        worker._path(project, value)
    assert refused.value.code == code


@pytest.mark.parametrize("plan", [{}, {"block": "neutral", "pins": {"a": {}}, "arcs": []},
                                  {"block": "other", "pins": {"a": {}}, "arcs": [{"deck": "must-not-run.sp"}]}])
def test_characterization_refuses_incomplete_or_foreign_plan_before_any_process(tmp_path, plan):
    planfile = tmp_path / "phase3/analog/neutral/characterization_plan.json"
    planfile.parent.mkdir(parents=True)
    planfile.write_text(json.dumps(plan))
    before = planfile.read_bytes()
    records = tmp_path / "records"
    with pytest.raises(em.Refusal) as refused:
        worker.characterize(tmp_path, "neutral", {"timeout_s": 20}, records)
    assert refused.value.code == "ANALOG_CHARACTERIZATION_PLAN_REQUIRED"
    assert planfile.read_bytes() == before
    assert not records.exists() and not (tmp_path / "phase3/analog/hardmacro").exists()
