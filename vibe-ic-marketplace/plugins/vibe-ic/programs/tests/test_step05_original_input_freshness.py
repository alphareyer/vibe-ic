"""Real retained route producer and canonical consumer; no native acquisition."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path

import pytest

import execution_policy as policy
import phase1_one_shot_runner as phase1
import step_output_collector as versions
import submission_template_fetch as fetch


def _write(project, relative, text):
    path = project / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _write(project, "input/docs/design.md", "Target PDK: gf180mcuD\nDigital source-control fixture.\n")
    _write(project, "input_doc/legacy.txt", "Neutral original legacy document.\n")
    _write(project, "input/project.json", '{"pdk":"gf180mcuD"}')
    answers = {
        "answers": {"deliverable": "DIE", "top_cell": "neutral"},
        "answer_provenance": {
            "deliverable": {"answered_by": "owner", "citation": "Neutral owner declares a DIE for this source control."},
            "synthesis_area_budget": {"answered_by": "owner", "citation":
                "Neutral owner explicitly declares no die/core area-budget limit in this source control."},
        },
        "operator_template": {
            "absent_reason": "This neutral owner does not purchase an operator slot and declares self tapeout."},
        "synthesis_area_budget": {"status": "NOT_APPLICABLE", "rationale":
            "The neutral input specifies no die or core area limit; no area-budget comparison is declared."},
    }
    _write(project, "input/step_0_5ic_answers.json", json.dumps(answers))
    return project


def _external_fetch_boundary(monkeypatch, *, fail=False):
    """Keep fetch logic, replacing only external technology acquisition."""
    monkeypatch.setattr(policy, "request", lambda: {"mode": "default"})
    monkeypatch.setattr(policy, "_ordinary_runtime", None)
    monkeypatch.setattr(fetch, "technology_facts", lambda *_a, **_k: {
        "database_unit_um": {"value": None, "unavailable": "No native acquisition in source control."}})
    monkeypatch.setattr(fetch._theirs, "shuttle_for_pdk", lambda *_a: None)
    supervised = phase1._wd.run_host_supervised

    def run(argv, **kwargs):
        if Path(argv[1]).name != "submission_template_fetch.py":
            return supervised(argv, **kwargs)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = fetch.main(argv[2:])
        return phase1._wd.SupervisedResult(1 if fail else rc, out.getvalue(), err.getvalue(), "natural")

    monkeypatch.setattr(phase1._wd, "run_host_supervised", run)


@pytest.mark.parametrize("case", ["current", "owner_changed", "document_changed", "legacy_doc_changed", "auto_config_changed", "own_outputs_changed"])
def test_real_retained_route_keeps_original_input_freshness(tmp_path, monkeypatch, case):
    project = _project(tmp_path)
    _external_fetch_boundary(monkeypatch)
    assert phase1._run_step_0_5ic(project, pdk="" if case == "auto_config_changed" else "gf180mcuD") == 0
    accepted = versions.latest_pass(project, "0.5ic")
    assert accepted is not None and accepted["canonical_pass"], accepted
    before = accepted["inputs"]
    assert "input/step_0_5ic_answers.json" in before, before
    assert "input/docs/design.md" in before, before
    assert "input_doc/legacy.txt" in before, before
    assert "input/project.json" in before, before
    assert not any(name.startswith("input/submission_template/") for name in before), before
    if case == "owner_changed":
        path = project / "input/step_0_5ic_answers.json"
        owner = json.loads(path.read_text())
        owner["answers"]["top_cell"] = "changed_owner_top"
        path.write_text(json.dumps(owner))
    elif case == "document_changed":
        (project / "input/docs/design.md").write_text("Changed original design requirement.\n")
    elif case == "legacy_doc_changed":
        (project / "input_doc/legacy.txt").write_text("Changed original legacy requirement.\n")
    elif case == "auto_config_changed":
        (project / "input/project.json").write_text('{"pdk":"sky130A"}')
    elif case == "own_outputs_changed":
        _write(project, "input/submission_template/operator_answers.json", '{"new":"producer output"}')
        _write(project, "input/submission_template/tapeout_declaration.json", '{"new":"producer output"}')
    current = versions.latest_pass(project, "0.5ic")
    assert current["inputs"] == before
    assert current["current_inputs"] is (case in {"current", "own_outputs_changed"}), current
    _write(project, "API_OBSERVATION.json", json.dumps({
        "case": case, "canonical_pass": current["canonical_pass"],
        "current_inputs": current["current_inputs"], "bound_inputs": before,
        "consumer": accepted["result"]["canonical_consumer"],
        "scope": "real retained caller/route producers/complete canonical consumer; no native acquisition",
    }, indent=2))


@pytest.mark.parametrize("location", ["relative", "project_absolute", "external_absolute", "external_relative", "external_symlink"])
def test_explicit_operator_source_keeps_existing_execution_and_honest_freshness(tmp_path, monkeypatch, location):
    project = _project(tmp_path)
    source = tmp_path / "external_operator" if location.startswith("external_") else project / "input/operator_original"
    source.mkdir(parents=True)
    original = source / "neutral.yaml"
    original.write_text('DIE_AREA: [0, 0, 100, 100]\nCORE_AREA: [10, 10, 90, 90]\nSEAL_RING_WIDTH: 10\nFP_SIZING: absolute\npads: [neutral_pad]\n')
    original_bytes, original_inode = original.read_bytes(), original.stat().st_ino
    answers = project / "input/step_0_5ic_answers.json"
    own = json.loads(answers.read_text())
    own["operator_template"]["path"] = "input/operator_original" if location == "relative" else str(source)
    if location == "external_relative":
        own["operator_template"]["path"] = "../external_operator"
    elif location == "external_symlink":
        (project / "operator_link").symlink_to(source, target_is_directory=True)
        own["operator_template"]["path"] = "operator_link"
    own["operator_template"]["slot"] = "neutral"
    answers.write_text(json.dumps(own))
    _external_fetch_boundary(monkeypatch)
    assert phase1._run_step_0_5ic(project, pdk="gf180mcuD") == 0
    accepted = versions.latest_pass(project, "0.5ic")
    assert accepted is not None and accepted["canonical_pass"], accepted
    assert original.read_bytes() == original_bytes and original.stat().st_ino == original_inode
    assert not any(Path(name).is_absolute() for name in accepted["inputs"])
    assert not any(name.startswith("input/submission_template/") for name in accepted["inputs"])
    if location.startswith("external_"):
        assert accepted["inputs"] == {} and accepted["current_inputs"] is False
        assert accepted.get("input_freshness") == "NOT_MEASURED"
        assert accepted.get("input_freshness_reason") == "EXTERNAL_TEMPLATE_OUTSIDE_PROJECT"
    else:
        assert "input/operator_original/neutral.yaml" in accepted["inputs"]
        assert accepted["current_inputs"] is True
        original.write_text(original.read_text() + "# Operator changed its terms.\n")
        assert versions.latest_pass(project, "0.5ic")["current_inputs"] is False


@pytest.mark.parametrize("case", ["producer_failed", "missing_declaration"])
def test_failed_or_missing_route_evidence_never_promotes(tmp_path, monkeypatch, case):
    project = _project(tmp_path)
    _external_fetch_boundary(monkeypatch, fail=case == "producer_failed")
    if case == "missing_declaration":
        import execution_backend_gates as gates
        evaluate = gates.evaluate

        def consume(root, row):
            (root / "input/submission_template/tapeout_declaration.json").unlink()
            return evaluate(root, row)

        monkeypatch.setattr(gates, "evaluate", consume)
    rc = phase1._run_step_0_5ic(project, pdk="gf180mcuD")
    assert rc == (1 if case == "producer_failed" else 0)
    assert versions.latest_pass(project, "0.5ic") is None
    row = next(r for r in versions._read_index(project)["steps"] if r["id"] == "0.5ic")
    retained = json.loads((project / "steps" / row["folder"] / "outputs.json").read_text())
    assert retained["canonical_pass"] is False
    assert retained["status"] != "PASS"


@pytest.mark.parametrize("retarget", [False, True], ids=["current", "retarget"])
def test_contained_operator_alias_tracks_the_template_the_producer_reads(tmp_path, monkeypatch, retarget):
    import submission_template_ingest as ingest

    project = _project(tmp_path)
    a = _write(project, "input/operator_A/neutral.yaml",
               "DIE_AREA: [0, 0, 100, 100]\nCORE_AREA: [10, 10, 90, 90]\nSEAL_RING_WIDTH: 10\nFP_SIZING: absolute\npads: [neutral_pad]\n")
    b = _write(project, "input/operator_B/neutral.yaml",
               "DIE_AREA: [0, 0, 200, 200]\nCORE_AREA: [10, 10, 190, 190]\nSEAL_RING_WIDTH: 10\nFP_SIZING: absolute\npads: [neutral_pad]\n")
    alias = project / "operator_link"
    alias.symlink_to(a.parent, target_is_directory=True)
    answers = project / "input/step_0_5ic_answers.json"
    own = json.loads(answers.read_text())
    own["operator_template"].update(path="operator_link", slot="neutral")
    answers.write_text(json.dumps(own))
    owner_before, a_before = answers.read_bytes(), a.read_bytes()
    _external_fetch_boundary(monkeypatch)
    assert phase1._run_step_0_5ic(project, pdk="gf180mcuD") == 0
    original = versions.latest_pass(project, "0.5ic")
    assert original is not None and original["canonical_pass"]
    assert original["current_inputs"] is True
    if retarget:
        alias.unlink()
        alias.symlink_to(b.parent, target_is_directory=True)
    readback = ingest.build_record(project, str(alias), "neutral", "Neutral declared operator source.")
    expected_root = b.parent if retarget else a.parent
    assert Path(readback["lookup"]["template_root"]) == expected_root.resolve()
    assert readback["slots"] and Path(readback["slots"][0]["source_file"]) == expected_root / "neutral.yaml"
    current = versions.latest_pass(project, "0.5ic")
    assert answers.read_bytes() == owner_before and a.read_bytes() == a_before
    assert current["inputs"] == original["inputs"]
    _write(project, "API_OBSERVATION_ALIAS.json", json.dumps({
        "retarget": retarget, "bound_inputs": current["inputs"],
        "current_inputs": current["current_inputs"],
        "real_ingest_template_root": readback["lookup"]["template_root"],
        "real_ingest_source_file": readback["slots"][0]["source_file"],
        "original_owner_and_A_unchanged": True,
    }, indent=2))
    assert current["current_inputs"] is (not retarget), current


def test_parent_component_operator_alias_preserves_producer_and_unknown_freshness(tmp_path, monkeypatch):
    import submission_template_ingest as ingest

    project = _project(tmp_path)
    template = "DIE_AREA: [0, 0, 100, 100]\nCORE_AREA: [10, 10, 90, 90]\nSEAL_RING_WIDTH: 10\nFP_SIZING: absolute\npads: [neutral_pad]\n"
    a = _write(project, "sub/operator_A/neutral.yaml", template)
    actual = _write(project, "sub/operator_B/neutral.yaml", template)
    decoy = _write(project, "operator_B/neutral.yaml", template + "# Different lexical parent.\n")
    alias = project / "operator_link"
    alias.symlink_to(a.parent, target_is_directory=True)
    spelling = "operator_link/../operator_B"
    answers = project / "input/step_0_5ic_answers.json"
    own = json.loads(answers.read_text())
    own["operator_template"].update(path=spelling, slot="neutral")
    answers.write_text(json.dumps(own))
    owner_before, decoy_before = answers.read_bytes(), decoy.read_bytes()
    _external_fetch_boundary(monkeypatch)
    assert phase1._run_step_0_5ic(project, pdk="gf180mcuD") == 0
    original = versions.latest_pass(project, "0.5ic")
    assert original is not None and original["canonical_pass"]
    actual.write_text(actual.read_text() + "# Actual producer source changed.\n")
    readback = ingest.build_record(project, str(project / spelling), "neutral", "Neutral declared operator source.")
    assert Path(readback["lookup"]["template_root"]) == actual.parent.resolve()
    assert readback["slots"] and Path(readback["slots"][0]["source_file"]) == actual
    current = versions.latest_pass(project, "0.5ic")
    assert answers.read_bytes() == owner_before and decoy.read_bytes() == decoy_before
    _write(project, "API_OBSERVATION_PARENT_ALIAS.json", json.dumps({
        "bound_inputs": current["inputs"], "current_inputs": current["current_inputs"],
        "original_current_inputs": original["current_inputs"],
        "input_freshness": current.get("input_freshness"),
        "input_freshness_reason": current.get("input_freshness_reason"),
        "real_ingest_template_root": readback["lookup"]["template_root"],
        "real_ingest_source_file": readback["slots"][0]["source_file"],
        "owner_and_decoy_unchanged": True,
    }, indent=2))
    assert current["current_inputs"] is False, current
    assert current["input_freshness"] == "NOT_MEASURED"
    assert current["input_freshness_reason"] == "TEMPLATE_PARENT_COMPONENT_UNBOUND"
