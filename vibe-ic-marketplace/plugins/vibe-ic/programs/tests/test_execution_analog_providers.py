"""Focused source-only controls for the 13-row analog provider catalog."""
from __future__ import annotations

import json
import copy
import math
from pathlib import Path
import sys
import subprocess

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import execution_adapters_analog as analog
import execution_modes as em


EXPECTED = tuple(f"A{i}" for i in range(1, 10)) + tuple(f"M{i}" for i in range(1, 5))


def test_machine_readable_coverage_has_exact_rows_and_paths():
    assert analog.STEP_IDS == EXPECTED
    rows = analog.coverage()
    assert tuple(rows) == EXPECTED
    assert rows["A7"]["canonical_outputs"] == ("phase3/analog/*/pre_vs_post.json",)
    assert rows["A7"]["mandatory_gates"] == ("analog_pre_vs_post_layout_check",)
    assert rows["A7"]["fallback"] == {"when_degradation_strictly_greater_than_pct": 10.0, "to": "A3"}
    assert rows["A4"]["source_family"] == "analog-programs"
    assert rows["M1"]["condition"]["kind"] == "design_dependent"
    assert rows["M1"]["dispatch"] is True
    assert rows["M2"]["dispatch"] is False
    assert rows["M3"]["certification"] == "CANNOT_CERTIFY"
    assert rows["M4"]["certification"] == "CANNOT_CERTIFY"


def test_registered_routes_bind_real_sources_and_keep_one_engine_chain():
    registry = em.Registry()
    registered = analog.register_factories(registry)
    assert registered == tuple(f"A{i}" for i in range(1, 10)) + ("M1",)
    assert all(len(registry.adapters(step)) == 1 for step in registered)
    assert all(not registry.adapters(step) for step in ("M2", "M3", "M4"))
    for step in registered:
        provider = analog.PROVIDERS[step]
        assert provider.producer_entrypoints
        assert all((PROGRAMS / name).is_file() for name in provider.source_files)
        assert all((PROGRAMS / (gate + ".py")).is_file() for gate in provider.mandatory_gates)
        adapter = registry.adapters(step)[0]
        assert adapter.qualified is False
        assert adapter.components[0].argv[0] == "python3"
        assert adapter.components[0].argv[2:4] == ("--step", step)
        assert len(adapter.engine_families) >= 1
        assert adapter.source_tree and len(adapter.source_tree) == 40
        assert str(PROGRAMS / "_analog_producer_common.py") in adapter.source_files
        assert str(PROGRAMS / "data/execution_modes_portfolio.json") in adapter.source_files


def test_m1_registration_is_backed_by_the_canonical_all_runner():
    runner = (PROGRAMS / "vibe_ic_one_shot_runner.py").read_text(encoding="utf-8")
    assert "_ms_dispatch" in runner
    assert "mixed_signal_top_lvs_run.py" in runner
    assert "PROGRAMS_DIR / \"mixed_signal_top_lvs_run.py\"" in runner
    assert analog.coverage()["M1"]["dispatch"] is True


def test_missing_and_incomplete_rows_never_return_pass(tmp_path):
    for step in EXPECTED:
        result = analog.produce(step, tmp_path)
        assert result.status in {"NOT_MEASURED", "NOT_IMPLEMENTED", "CANNOT_CERTIFY"}
        assert result.status != "PASS"
    assert analog.produce("opaque", tmp_path).status == "NOT_IMPLEMENTED"
    assert analog.produce("M2", tmp_path).status == "NOT_IMPLEMENTED"
    assert analog.produce("M3", tmp_path).status == "CANNOT_CERTIFY"
    assert analog.produce("M4", tmp_path).status == "NOT_IMPLEMENTED"


def test_missing_project_and_bound_resources_are_not_measured_for_implemented_rows(tmp_path):
    missing_project = tmp_path / "does-not-exist"
    for step in analog.IMPLEMENTED_STEPS:
        assert analog.produce(step, missing_project).status == "NOT_MEASURED"
    assert analog.produce("A3", tmp_path, inputs=None, pdk=None, model=None, tool=None).status == "NOT_MEASURED"
    assert analog.produce("M2", missing_project).status == "NOT_IMPLEMENTED"
    assert analog.produce("M3", missing_project).status == "CANNOT_CERTIFY"
    assert analog.produce("M4", missing_project).status == "NOT_IMPLEMENTED"


@pytest.mark.parametrize(
    ("degradation", "fallback"),
    ((9.99, None), (10.00, None), (10.01, "A3")),
)
def test_a7_boundary_uses_strict_shared_threshold(degradation, fallback):
    assert analog.a7_fallback_for_degradation(degradation) == fallback


@pytest.mark.parametrize("degradation", (math.nan, math.inf, -math.inf))
def test_a7_nonfinite_values_are_rejected_and_explicitly_unmeasured(degradation):
    with pytest.raises(ValueError, match="A7_DEGRADATION_NONFINITE"):
        analog.a7_fallback_for_degradation(degradation)
    result = analog.a7_degradation_result(degradation)
    assert result.status == "NOT_MEASURED"
    assert result.reason == "A7_DEGRADATION_NONFINITE"
    assert "A3" not in result.reason


def test_controller_plan_stays_not_measured_without_runtime_qualification(tmp_path):
    input_file = tmp_path / "input.json"
    input_file.write_text("{}\n", encoding="utf-8")
    registry = em.Registry()
    source_sha = "a" * 40
    analog.register_factories(registry, source_sha=source_sha)
    context = em.Context(
        "A4", source_sha, {"input.json": input_file},
        {"step_id": "A4", "canonical_outputs": list(analog.PROVIDERS["A4"].canonical_outputs)},
        tuple(next(s for s in em.load_portfolio()["steps"] if s["id"] == "A4")["mandatory_gate_programs"]),
    )
    planned = em.Controller(registry, em.Budget(1, 256, 1)).plan(context)
    assert planned["status"] == "NOT_MEASURED"
    assert planned["arms"] == []


def test_coverage_file_matches_loaded_catalog():
    raw = json.loads(analog.COVERAGE_FILE.read_text(encoding="utf-8"))
    assert tuple(row["step_id"] for row in raw["rows"]) == analog.STEP_IDS
    assert raw["schema"] == "execution_analog_coverage/1"


def test_catalog_reverse_control_rejects_a_false_m2_producer_and_bad_a7_route():
    assert analog.validate_catalog() == ()
    bad = analog.coverage()
    bad["M2"]["dispatch"] = True
    bad["M2"]["producer_entrypoints"] = ("power_domain_crossing_check.main",)
    bad["A7"]["fallback"] = {"when_degradation_strictly_greater_than_pct": 10.0, "to": "A4"}
    defects = analog.validate_catalog(bad)
    assert "M2:FALSE_PRODUCER" in defects
    assert "A7:FALLBACK_AUTHORITY" in defects


def test_catalog_reverse_control_rejects_an_explicit_empty_catalog():
    assert "CATALOG_EMPTY" in analog.validate_catalog({})


def test_catalog_authorities_are_bound_to_declared_git_blobs_and_schema(monkeypatch):
    original = analog._authority_documents

    def mutated(commit):
        coverage, flow, portfolio = original(commit)
        coverage = dict(coverage)
        coverage["schema"] = "execution_analog_coverage/attacker"
        return coverage, flow, portfolio

    monkeypatch.setattr(analog, "_authority_documents", mutated)
    defects = analog.validate_catalog()
    assert "COVERAGE_SCHEMA_MISMATCH" in defects


@pytest.mark.parametrize(
    ("label", "authority"),
    (("COVERAGE", analog.COVERAGE_FILE),
     ("FLOW", analog.FLOW_FILE),
     ("PORTFOLIO", analog.PORTFOLIO_FILE)),
)
def test_each_canonical_authority_blob_mutation_is_rejected(monkeypatch, label, authority):
    original = analog._git_blob

    def mutated(commit, path):
        blob = original(commit, path)
        return blob + b"\n" if path == authority else blob

    monkeypatch.setattr(analog, "_git_blob", mutated)
    defects = analog.validate_catalog()
    assert label + "_AUTHORITY_BLOB_MISMATCH" in defects


def test_registration_ignores_caller_source_sha_and_issues_clean_commit_tree():
    registry = em.Registry()
    analog.register_factories(registry, source_sha="a" * 40)
    observed = registry.adapters("A1")[0]
    commit = subprocess.check_output(
        ["git", "-C", str(PROGRAMS), "rev-parse", "HEAD"], text=True).strip()
    tree = subprocess.check_output(
        ["git", "-C", str(PROGRAMS), "rev-parse", "HEAD^{tree}"], text=True).strip()
    assert observed.source_sha == commit
    assert observed.source_tree == tree
    assert observed.source_sha != "a" * 40


def test_transitive_source_mutation_becomes_a_named_non_candidate(tmp_path, monkeypatch):
    registry = em.Registry()
    analog.register_factories(registry)
    source_sha = analog._repo_source_sha()
    source = PROGRAMS / "_analog_producer_common.py"
    original_digest = em.digest

    def mutated_digest(path):
        if Path(path).resolve() == source.resolve():
            return "0" * 64
        return original_digest(path)

    monkeypatch.setattr(em, "digest", mutated_digest)
    input_file = tmp_path / "input.json"
    input_file.write_text("{}\n", encoding="utf-8")
    context = em.Context(
        "A1", source_sha,
        {"input.json": input_file},
        {"step_id": "A1", "canonical_outputs": list(analog.PROVIDERS["A1"].canonical_outputs)},
        tuple(next(s for s in em.load_portfolio()["steps"] if s["id"] == "A1")["mandatory_gate_programs"]),
    )
    planned = em.Controller(registry, em.Budget(1, 256, 1)).plan(context)
    assert planned["arms"] == []
    assert planned["status"] == "NOT_MEASURED"
    assert any(row["admission"] == "ADAPTER_SOURCE_MISMATCH" for row in planned["portfolio"])


@pytest.mark.parametrize(
    ("step", "field", "value", "needle"),
    (
        ("A1", "canonical_inputs", ("mutated-input",), "A1:CANONICAL_INPUTS_MISMATCH"),
        ("A1", "canonical_outputs", ("mutated-output",), "A1:CANONICAL_OUTPUTS_MISMATCH"),
        ("A1", "mandatory_gates", ("mutated-gate",), "A1:MANDATORY_GATES_MISMATCH"),
        ("A1", "condition", {"kind": "always"}, "A1:CONDITION_MISMATCH"),
        ("A1", "certification", "PASS", "A1:CERTIFICATION_MISMATCH"),
        ("A1", "producer_entrypoints", ("missing_symbol.run",), "A1:PRODUCER_ENTRYPOINTS_MISMATCH"),
        ("M2", "producer_entrypoints", ("fake_m2.run",), "M2:PRODUCER_ENTRYPOINTS_MISMATCH"),
    ),
)
def test_catalog_reverse_control_rejects_mutated_contract_fields(step, field, value, needle):
    bad = copy.deepcopy(analog.coverage())
    bad[step][field] = value
    defects = analog.validate_catalog(bad)
    assert needle in defects
