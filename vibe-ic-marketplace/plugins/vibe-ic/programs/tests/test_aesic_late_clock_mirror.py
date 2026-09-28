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
