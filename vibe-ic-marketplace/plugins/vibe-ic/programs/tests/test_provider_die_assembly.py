"""Provider continuation source contracts; no native geometry or PV claims."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import die_finishing_gen as finishing
import execution_policy as policy
import tapeout_readiness_check as readiness


def profile(project):
    path = project / finishing.PROVIDER_TARGET_REL
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"provider": "wafer.space", "technical_target": {
        "slot": "1x1", "dimensions_um": [400, 500],
        "packaging": "bare_die", "purchase_or_reserved_slot": False},
        "precheck_source": "input/submission_template/official"}))
    return path


def test_provider_target_uses_existing_frozen_input_directory(tmp_path):
    parameters = {}
    runtime = {"project": tmp_path, "policy": {"mode": "default"}}
    absent = policy._fixed_inputs(runtime, "37.5ic", parameters=parameters)
    assert finishing.PROVIDER_TARGET_REL not in absent
    path = profile(tmp_path)
    official = path.parent / "official"
    official.mkdir()
    entry = official / "precheck.py"
    entry.write_text("# neutral official source dependency\n")
    selected = policy._fixed_inputs(runtime, "37.5ic", parameters=parameters)
    assert selected[finishing.PROVIDER_TARGET_REL] == path
    assert parameters["resolved_input_manifest"]["files"][finishing.PROVIDER_TARGET_REL] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert selected.keys() - absent.keys() == {
        finishing.PROVIDER_TARGET_REL, str(entry.relative_to(tmp_path))}
    assert parameters["resolved_input_manifest"]["files"][str(entry.relative_to(tmp_path))] == hashlib.sha256(entry.read_bytes()).hexdigest()


def test_normal_finishing_uses_declared_engineering_target(tmp_path):
    routed = tmp_path / "phase3/stage3/pnr/routed.def"
    routed.parent.mkdir(parents=True)
    routed.write_text("UNITS DISTANCE MICRONS 1000 ;\nDIEAREA ( 0 0 ) ( 100000 100000 ) ;\nEND DESIGN\n")
    assert finishing.die_size(tmp_path, tmp_path / "a.gds", None, None)[:2] == (100, 100)
    path = profile(tmp_path)
    before = path.read_bytes()
    assert finishing.die_size(tmp_path, tmp_path / "a.gds", None, None)[:2] == (400, 500)
    assert finishing.die_size(tmp_path, tmp_path / "a.gds", 600, 700)[:2] == (600, 700)
    state = finishing.die_id_state({}, {}, tmp_path)
    assert state["state"] == "NOT_APPLICABLE"
    assert "non-CoB" in state["reason"] and "no operator" not in state["reason"]
    assert path.read_bytes() == before


def test_normal_native_service_entry_uses_official_adapter_without_skip(tmp_path):
    profile(tmp_path)
    gds = tmp_path / "chip_top.gds"
    gds.write_bytes(b"neutral source-only layout placeholder")
    d = tmp_path / finishing._DEF_REL
    d.parent.mkdir(parents=True)
    d.write_text("DIEAREA ( 0 0 ) ( 400000 500000 ) ;\nEND DESIGN\n")
    seen = []
    def runner(command, timeout):
        seen.append(command)
        return 1, "neutral tool refusal", ""
    def image_resolver(*args):
        pytest.fail("an explicit native official checkout does not dispatch another image")
    result = readiness.evaluate(tmp_path, layout=gds, top="chip_top", runner=runner,
                                image_resolver=image_resolver)
    assert result.verdict != "PASS"
    command, = seen
    assert command[:2] == [sys.executable, str(Path(readiness.__file__).with_name("wafer_space_precheck.py"))]
    assert command[command.index("--official-source") + 1] == str(tmp_path / "input/submission_template/official")
    assert "--with-initial-state" in command
    assert not {"--skip", "--from", "--to", "--cob", "--id"}.intersection(command)
    state = json.loads(Path(command[command.index("--with-initial-state") + 1]).read_text())
    assert state["def"] == str(d)


def test_malformed_engineering_dimensions_refuse(tmp_path):
    path = profile(tmp_path)
    value = json.loads(path.read_text())
    value["technical_target"]["dimensions_um"] = ["400", 500]
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="positive finite"):
        finishing.die_size(tmp_path, tmp_path / "a.gds", None, None)


@pytest.mark.parametrize("case", ["current", "changed_gds", "refused_precheck", "missing_step"])
def test_release_reader_requires_current_complete_operator_evidence(tmp_path, case):
    def evidence(name, value):
        path = tmp_path / name
        path.write_text(json.dumps(value))
        return finishing._release_file(path)
    gds = evidence("neutral.gds", {"neutral": True})
    files = {name: evidence(name + ".json", {}) for name in [
        "source_admission", "source_stream", "raw_gds", "seal_report", "sealed_gds",
        "instruction", "finished_def"]}
    steps = []
    for i in range(16):
        state = evidence(f"state-{i}.json", {"metrics": {}})
        files[f"official_state_{i + 1}"] = state
        steps.append({"state_out": state})
    precheck = {"verdict": "FAIL" if case == "refused_precheck" else "PASS",
                "returncode": 0, "all_steps_requested": True, "input_unchanged": True,
                "steps": steps[:-1] if case == "missing_step" else steps, "output": gds}
    files["official_precheck"] = evidence("operator.json", precheck)
    record = {"producer": "die_finishing_gen", "kind": "provider_die_assembly",
              "verdict": "PASS", "source_project": str(tmp_path), "output": gds,
              "basis": {"files": files}}
    path = tmp_path / "release.json"
    path.write_text(json.dumps(record))
    if case == "changed_gds":
        (tmp_path / "neutral.gds").write_text("changed")
    if case == "current":
        assert finishing.read_admitted_release(tmp_path, path)["gds"] == tmp_path / "neutral.gds"
    else:
        with pytest.raises(ValueError):
            finishing.read_admitted_release(tmp_path, path)
