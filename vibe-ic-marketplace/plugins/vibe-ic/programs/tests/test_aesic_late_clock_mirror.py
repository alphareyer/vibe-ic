"""A late staged SDC clock must reach the Phase-2 integration consumer."""
import ast
import json
from pathlib import Path

import _path_layout
import phase1_doc_one_shot_runner as runner


def test_secondary_sdc_clock_reaches_l9_without_replacing_primary(tmp_path):
    project = tmp_path / "project"
    docs = _path_layout.generated_docs_dir(project)
    docs.mkdir(parents=True)
    (docs / "L8_RTL_CONSTANTS.json").write_text(json.dumps({
        "clock_domains": [
            {"name": "clk_main", "period_ns": 10, "domain_kind": "primary"},
            {"name": "clk_aux", "period_ns": 20, "domain_kind": "secondary",
             "source": "input/constraints/clock.sdc"},
            {"name": "clk_reference", "period_ns": 8,
             "pdk_scoped_target": "other_pdk"},
        ]}))
    l9_path = docs / "L9_INTEGRATION_SPEC.json"
    l9_path.write_text(json.dumps({
        "clock_domains": [{"name": "clk_main", "role": "master"}],
        "top_ports": [{"name": "clk_main"}, {"name": "clk_aux"}],
    }))
    (docs / "L19_CONSTRAINTS_PDK.json").write_text(json.dumps({
        "fields": {"pdk_target": "target_pdk"}}))

    runner._post_emit_mirror_clock_resets_to_l9_v1_6_311(project)
    first = json.loads(l9_path.read_text())
    assert [d["name"] for d in first["clock_domains"]] == ["clk_main", "clk_aux"]
    assert first["clock_domains"][0] == {"name": "clk_main", "role": "master"}
    assert first["clock_domains"][1]["period_ns"] == 20

    runner._post_emit_mirror_clock_resets_to_l9_v1_6_311(project)
    assert json.loads(l9_path.read_text())["clock_domains"] == first["clock_domains"]


def test_final_clock_mirror_runs_after_late_sdc_producer():
    tree = ast.parse(Path(runner.__file__).read_text())
    main = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    calls = {}
    for node in ast.walk(main):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            calls.setdefault(node.func.id, []).append(node.lineno)
    assert max(calls["_post_emit_mirror_clock_resets_to_l9_v1_6_311"]) > max(
        calls["_post_emit_sdc_constraints"])
    assert max(calls["_post_emit_mirror_clock_resets_to_l9_v1_6_311"]) > max(
        calls["_post_emit_enforce_clock_contract"])


def test_staged_sdc_replaces_stale_l9_primary_timing(tmp_path):
    project = tmp_path / "project"
    staged = project / "input" / "constraints"
    staged.mkdir(parents=True)
    (staged / "clocks.sdc").write_text(
        "create_clock -name system_clock -period 10.0 [get_ports clk_main]\n")
    docs = _path_layout.generated_docs_dir(project)
    docs.mkdir(parents=True)
    for name in ("L8_RTL_CONSTANTS", "L8_TIMING_WAVEFORM"):
        (docs / f"{name}.json").write_text(json.dumps({
            "clock_domains": [{"name": "clk_main", "source_pin": "clk_main",
                               "domain_kind": "primary", "period_ns": 20,
                               "freq_mhz": 50}],
        }))
    (docs / "L19_CONSTRAINTS_PDK.json").write_text(json.dumps({"fields": {}}))
    l9_path = docs / "L9_INTEGRATION_SPEC.json"
    l9_path.write_text(json.dumps({
        "clock_domains": [{"name": "clk_main", "period_ns": 20,
                           "freq_mhz": 50, "role": "master",
                           "reset_strategy": "synchronous"}],
        "top_ports": [{"name": "clk_main"}],
    }))

    runner._post_emit_sdc_constraints(project)
    assert runner._post_emit_enforce_clock_contract(project) == []
    l8 = json.loads((docs / "L8_RTL_CONSTANTS.json").read_text())
    assert l8["clock_domains"][0]["period_ns"] == 10
    runner._post_emit_mirror_clock_resets_to_l9_v1_6_311(project)
    l9 = json.loads(l9_path.read_text())["clock_domains"][0]
    assert l9["period_ns"] == 10, "L9 must carry the final staged-SDC period"
    assert l9["freq_mhz"] == 100 and l9["freq_hz"] == 100_000_000
    assert l9["role"] == "master" and l9["reset_strategy"] == "synchronous"
    before = l9_path.read_text()
    assert runner._post_emit_mirror_clock_resets_to_l9_v1_6_311(project) == []
    assert l9_path.read_text() == before


def test_independent_clock_declarations_are_recorded_and_refused(tmp_path):
    project = tmp_path / "project"
    docs = _path_layout.generated_docs_dir(project)
    docs.mkdir(parents=True)
    (docs / "L8_RTL_CONSTANTS.json").write_text(json.dumps({
        "clock_domains": [{"name": "clk_main", "period_ns": 10,
                           "freq_mhz": 100,
                           "source": "input/constraints/*.sdc",
                           "evidence": "input/constraints/first.sdc"}],
    }))
    l9_path = docs / "L9_INTEGRATION_SPEC.json"
    l9_path.write_text(json.dumps({
        "clock_domains": [{"name": "clk_main", "period_ns": 20,
                           "freq_mhz": 50,
                           "source": "input/constraints/second.sdc",
                           "role": "master"}],
    }))
    conflicts = runner._post_emit_mirror_clock_resets_to_l9_v1_6_311(project)
    assert len(conflicts) == 1 and "clk_main" in conflicts[0]
    l9 = json.loads(l9_path.read_text())
    assert l9["clock_domains"][0]["period_ns"] == 20
    assert l9["clock_contract_conflicts"][0]["resolution"] == "refused"
    assert sorted(l9["clock_contract_conflicts"][0]["periods_ns"]) == [10, 20]


def test_two_non_sdc_owners_of_different_periods_are_refused(tmp_path):
    project = tmp_path / "project"
    docs = _path_layout.generated_docs_dir(project)
    docs.mkdir(parents=True)
    (docs / "L8_RTL_CONSTANTS.json").write_text(json.dumps({
        "clock_domains": [{"name": "clk_main", "period_ns": 10,
                           "role": "primary"}],
    }))
    l9_path = docs / "L9_INTEGRATION_SPEC.json"
    l9_path.write_text(json.dumps({
        "clock_domains": [{"name": "clk_main", "period_ns": 20,
                           "role": "master"}],
    }))
    conflicts = runner._post_emit_mirror_clock_resets_to_l9_v1_6_311(project)
    assert len(conflicts) == 1
    l9 = json.loads(l9_path.read_text())
    assert l9["clock_domains"][0]["period_ns"] == 20
    assert l9["clock_contract_conflicts"][0]["resolution"] == "refused"


def test_two_incompatible_l9_owners_are_not_rewritten_by_sdc(tmp_path):
    project = tmp_path / "project"
    docs = _path_layout.generated_docs_dir(project)
    docs.mkdir(parents=True)
    (docs / "L8_RTL_CONSTANTS.json").write_text(json.dumps({
        "clock_domains": [{"name": "clk_main", "period_ns": 10,
                           "freq_mhz": 100,
                           "source": "input/constraints/*.sdc",
                           "evidence": "input/constraints/clocks.sdc"}],
    }))
    l9_path = docs / "L9_INTEGRATION_SPEC.json"
    l9_path.write_text(json.dumps({
        "clock_domains": [{"name": "clk_main", "period_ns": 20,
                           "role": "master"},
                          {"name": "clk_main", "period_ns": 30,
                           "role": "secondary"}],
    }))
    conflicts = runner._post_emit_mirror_clock_resets_to_l9_v1_6_311(project)
    assert len(conflicts) == 1
    l9 = json.loads(l9_path.read_text())
    assert l9["clock_contract_conflicts"][0]["periods_ns"] == [20, 30]
    assert [row["period_ns"] for row in l9["clock_domains"]] == [20, 30]
