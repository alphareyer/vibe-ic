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
import tapeout_precheck as precheck


def profile(project):
    path = project / finishing.PROVIDER_TARGET_REL
    path.parent.mkdir(parents=True, exist_ok=True)
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


@pytest.mark.parametrize("operator_verdict", ["PASS", "FAIL", "NOT_DETERMINED"])
def test_normal_wrapper_uses_new_provider_over_retained_self_catalogue(
        tmp_path, monkeypatch, operator_verdict):
    from test_owner_self_tapeout_catalogue import fixture
    import submission_template_check as catalogue
    project, _, _ = fixture(tmp_path)
    assert catalogue.catalogue_selects_self_tapeout(project)[0]
    switch = project / "phase3/librelane_switch.json"
    switch.parent.mkdir(parents=True)
    switch.write_text(json.dumps({"steps": {step: "direct" for step in ("26", "31", "34", "37")}}))
    (project / "input/project.json").write_text(json.dumps({"pdk": "gf180mcuD"}))
    profile(project)
    seen = []
    def run(command, timeout):
        # Neutral subprocess leaves only; the real normal wrapper/merge/main
        # selects and invokes the authority and preserves its actual verdict.
        operator = command[1].endswith("tapeout_readiness_check.py")
        if operator:
            seen.append(command)
        verdict = operator_verdict if operator else "PASS"
        dest = Path(command[command.index("--json") + 1])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps({"verdict": verdict, "steps": [],
                                   "reason": "neutral source-only authority transport"}))
        return (0 if verdict == "PASS" else 1), "neutral transport", ""
    monkeypatch.setattr(precheck, "default_runner", run)
    dest = project / "merged.json"
    rc = precheck.main([str(project), "--json", str(dest)])
    report = json.loads(dest.read_text())
    operator = next(arm for arm in report["arms"] if arm["arm"] == "operator")
    assert operator["state"] == "RAN", "selected provider was hidden by old SELF catalogue"
    command, = seen
    assert command[command.index("--slot") + 1] == "1x1"
    assert operator["verdict"] == operator_verdict
    assert report["verdict"] == operator_verdict
    assert rc == (0 if operator_verdict == "PASS" else 1)
    assert "engineering" in operator["reason"]
    assert "purchased" not in operator["reason"]


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


@pytest.mark.parametrize("case,expected", [("timestamps", "PASS"), ("native_red", "FAIL"),
                                          ("changed_geometry", "NOT_MEASURED"),
                                          ("missing_count", "NOT_MEASURED")])
def test_provider_pv_reader_binds_real_gds_records_and_preserves_native_counts(
        tmp_path, case, expected):
    from test_gds_canonical_digest import _stream, _RUN_A, _RUN_B
    from wafer_space_precheck import read_signoff_evidence
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return finishing._release_file(path)
    # Neutral source-control GDS records, not claimed native PV receipts.
    subject, final = tmp_path / "subject.gds", tmp_path / "delivered.gds"
    subject.write_bytes(_stream(_RUN_A, _RUN_A, width=1400 if case == "changed_geometry" else 1000))
    final.write_bytes(_stream(_RUN_B, _RUN_B))
    files = {name: write(tmp_path / (name + ".json"), {}) for name in [
        "source_admission", "source_stream", "raw_gds", "seal_report", "sealed_gds",
        "instruction", "finished_def"]}
    ids = ["ReadLayout", "Hierarchy", "Size", "GenerateID", "Render", "KLayout.Density",
           "Checker.KLayoutDensity", "ZeroArea", "CheckerZero", "KLayout.Antenna",
           "Checker.KLayoutAntenna", "Magic.DRC", "Checker.MagicDRC", "KLayout.DRC",
           "Checker.KLayoutDRC", "WriteLayout"]
    count = 3 if case == "native_red" else None if case == "missing_count" else 0
    metrics = {"klayout__density_error__count": 0, "klayout__antenna_error__count": 0,
               "klayout__drc_error__count": count, "magic__drc_error__count": 7}
    steps = []
    for i, sid in enumerate(ids):
        folder = tmp_path / f"{i + 1:02d}-stage"
        write(folder / "state_in.json", {"gds": str(subject)})
        state = write(folder / "state_out.json", {"gds": str(subject), "metrics": metrics})
        write(folder / "config.json", {"meta": {"step": sid}, "ERROR_ON_MAGIC_DRC": False})
        (folder / "COMMANDS").write_text("neutral source-control invocation; no EDA was run\n")
        for kind in ("density", "antenna", "drc"):
            write(folder / "reports" / (kind + ".klayout.json"), {"total": count if kind == "drc" else 0})
        files[f"official_state_{i + 1}"] = state
        steps.append({"folder": str(folder), "state_out": state})
    output = finishing._release_file(final)
    files["official_precheck"] = write(tmp_path / "operator.json", {
        "verdict": "PASS", "returncode": 0, "all_steps_requested": True,
        "input_unchanged": True, "steps": steps, "expected_steps": ids, "output": output})
    release = tmp_path / "release.json"
    write(release, {"producer": "die_finishing_gen", "kind": "provider_die_assembly",
                    "verdict": "PASS", "source_project": str(tmp_path), "output": output,
                    "basis": {"files": files}})
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob("*") if p.is_file()}
    result = read_signoff_evidence(tmp_path, release)
    assert result["verdict"] == expected
    assert result["gds_sha256"] == output["sha256"]
    row = result["rows"]["drc"]
    if case == "native_red":
        assert row["value"] == 3 and row["verdict"] == "FAIL"
    elif case == "timestamps":
        assert row["value"] == 0 and row["verdict"] == "PASS"
        assert row["equivalence"]["input"]["sha256"] != output["sha256"]
        assert row["equivalence"]["input"]["canonical_digest"] == row["equivalence"]["output"]["canonical_digest"]
        assert result["disclosures"][0]["value"] == 7
        assert result["disclosures"][0]["error_on_magic_drc"] is False
    else:
        assert row["verdict"] == "NOT_MEASURED"
    after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after, "the PV consumer must be read-only"
