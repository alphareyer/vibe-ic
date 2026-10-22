"""Regression tests for #3477: Step 15 refreshes only wrapper provenance."""
import hashlib
import json

import phase3_one_shot_runner as runner


def _project(tmp_path):
    project = tmp_path / "project"
    wrapper = project / "phase3/stage3/pnr/chip_top_io.v"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("module chip_top; endmodule\n")
    record = project / "reports/phase3/io_pad_chip_top.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({
        "verdict": "WROTE",
        "chip_top_module": "chip_top",
        "chip_top_verilog": "phase3/stage3/pnr/chip_top_io.v",
    }))
    declaration = project / "plugin_output/declaration.json"
    declaration.parent.mkdir(parents=True)
    declaration.write_text(json.dumps({
        "unrelated": {"kept": True},
        "verification_stimuli": {"arithmetic_observation": {"chip_top": {
            "clock": "clk", "output": "p", "settle_ns": 2,
            "sample_offset_ns": 3,
            "binding": {
                str(wrapper): "0" * 64,
                "/pdk/model.v": "1" * 64,
            },
        }}},
    }))
    return project, wrapper, declaration


def test_refreshes_only_exact_canonical_wrapper_digest_and_retains_history(tmp_path):
    project, wrapper, declaration = _project(tmp_path)
    before = json.loads(declaration.read_text())
    result = runner._refresh_pad_observation_wrapper_binding(project)
    after = json.loads(declaration.read_text())

    choice = after["verification_stimuli"]["arithmetic_observation"]["chip_top"]
    assert result["verdict"] == "PASS" and result["changed"] is True
    assert choice["binding"][str(wrapper)] == hashlib.sha256(wrapper.read_bytes()).hexdigest()
    assert choice["binding"]["/pdk/model.v"] == "1" * 64
    assert {k: choice[k] for k in ("clock", "output", "settle_ns",
                                   "sample_offset_ns")} == {
        k: before["verification_stimuli"]["arithmetic_observation"]["chip_top"][k]
        for k in ("clock", "output", "settle_ns", "sample_offset_ns")}
    assert after["unrelated"] == {"kept": True}
    history = project / result["previous_declaration"]
    assert json.loads(history.read_text()) == before


def test_does_not_invent_a_missing_wrapper_binding(tmp_path):
    project, wrapper, declaration = _project(tmp_path)
    doc = json.loads(declaration.read_text())
    bindings = doc["verification_stimuli"]["arithmetic_observation"]["chip_top"]["binding"]
    bindings.pop(str(wrapper))
    declaration.write_text(json.dumps(doc))

    result = runner._refresh_pad_observation_wrapper_binding(project)
    assert result["verdict"] == "NOT_MEASURED"
    assert "no unique exact wrapper binding" in result["reason"]
    assert json.loads(declaration.read_text()) == doc
    assert not (project / "plugin_output/declaration_history").exists()


def test_functional_run_refreshes_before_generating_cases():
    source = runner.inspect.getsource(runner._full_stack_functional_after_padring)
    assert source.index("_refresh_pad_observation_wrapper_binding(project)") < source.index(
        "_fsf.generate(project, container)")
