"""R2 Step9 provider controls: native-only, canonical and fail-closed."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import execution_modes as em
import execution_production as production
import execution_synthesis_engines as provider


def _project(tmp_path: Path):
    project = tmp_path / "project"
    (project / "input/submission_template").mkdir(parents=True)
    (project / "phase2/stage1/rtl").mkdir(parents=True)
    (project / "input/submission_template/tapeout_declaration.json").write_text(
        '{"top":"top","die_area_budget_um":1000}\n')
    (project / "phase2/stage1/rtl/top.v").write_text(
        "module top(input a, output y); assign y = a; endmodule\n")
    liberty = tmp_path / "neutral.lib"
    liberty.write_text("library(neutral) { cell(INV) { area: 1.0; } }\n")
    return project, liberty


def _ctx(tmp_path: Path):
    project, liberty = _project(tmp_path)
    inputs = {
        "project/input/submission_template/tapeout_declaration.json":
        project / "input/submission_template/tapeout_declaration.json",
        "project/phase2/stage1/rtl/top.v": project / "phase2/stage1/rtl/top.v",
        "pdk/neutral.lib": liberty,
    }
    request = tmp_path / "request.json"
    request.write_text("{}\n")
    inputs["request.json"] = request
    ctx = em.Context("9", provider.source_sha(), inputs,
                     {"metric": "mapped_area_um2", "direction": "min", "top": "top"},
                     provider.REQUIRED_GATES, native_mode="librelane")
    return project, liberty, ctx


def test_fake_python_fixture_is_never_ready(tmp_path):
    _, _, ctx = _ctx(tmp_path)
    registry = provider.build_registry(context=ctx, image="openroad:fake",
                                       tool=sys.executable, fixture=True)
    arm = registry.adapters("9")[0]
    plan = em.Controller(registry, em.Budget(1, 256)).plan(ctx)
    assert plan["status"] == "NOT_MEASURED"
    assert plan["arms"] == []
    assert arm.available is False
    assert "--produce" not in arm.components[0].argv


def test_self_reported_source_and_image_cannot_admit(tmp_path):
    _, _, ctx = _ctx(tmp_path)
    forged = em.Context("9", "0" * 40, ctx.inputs, ctx.objective,
                        ctx.required_gates, native_mode="librelane")
    registry = provider.build_registry(context=forged,
                                       image="sha256:" + "0" * 64)
    row = em.Controller(registry, em.Budget(1, 256)).plan(forged)["portfolio"][0]
    assert row["admission"] == "WRONG_SOURCE"
    assert registry.adapters("9")[0].tool_version.startswith("UNMEASURED:") or \
        registry.adapters("9")[0].available is False


def test_missing_tool_is_not_measured_even_with_a_real_image_digest(tmp_path, monkeypatch):
    _, _, ctx = _ctx(tmp_path)
    monkeypatch.setattr(production, "_tool_and_image", lambda image: (
        "sha256:" + "1" * 64, {"flows": {"Chip": list(provider.LIBRELANE.native_steps)},
                               "librelane_version": "measured"}, []))
    registry = provider.build_registry(context=ctx,
                                       image="sha256:" + "1" * 64,
                                       tool="/missing/yosys")
    plan = em.Controller(registry, em.Budget(1, 256)).plan(ctx)
    assert plan["status"] == "NOT_MEASURED"
    assert plan["portfolio"][0]["admission"] == "UNAVAILABLE"
    assert "tool_identity" in plan["portfolio"][0]["availability_reason"]


def _native_output(tmp_path: Path, *, area: bool):
    project, liberty, ctx = _ctx(tmp_path)
    output = tmp_path / "outputs"
    (output / "project/input/submission_template").mkdir(parents=True)
    (output / "project/phase2/stage1/rtl").mkdir(parents=True)
    (output / "pdk").mkdir()
    for rel in ("input/submission_template/tapeout_declaration.json",
                "phase2/stage1/rtl/top.v"):
        source = project / rel
        target = output / "project" / rel
        target.write_bytes(source.read_bytes())
    (output / "pdk/neutral.lib").write_bytes(liberty.read_bytes())
    synth = output / "project/phase2/stage2/synth"
    synth.mkdir(parents=True)
    (synth / "netlist.v").write_text("module top(input a, output y); assign y=a; endmodule\n")
    (synth / "top_synth.v").write_text("module top(input a, output y); assign y=a; endmodule\n")
    if area:
        (synth / "area.rpt").write_text("Total cell area: 1.0\n")
    else:
        (synth / "stats.json").write_text(json.dumps({
            "schema": "vibe-ic/synth-stats/1", "chip_area": 1.0,
            "netlist": provider.CANONICAL_NETLIST,
            "netlist_digest": em.digest(synth / "netlist.v"),
        }) + "\n")
    (output / "native_commands.jsonl").write_text(json.dumps({
        "executed": True, "native_entrypoint": provider.LIBRELANE.native_entrypoint,
        "image_id": "sha256:" + "1" * 64,
    }) + "\n")
    binding = ctx.binding()
    producer = {
        "binding": binding, "status": "PASS",
        "source_sha": binding["source_sha"],
        "native_entrypoint": provider.LIBRELANE.native_entrypoint,
        "synthesis_engine": provider.LIBRELANE.contract(),
        "source_files": production.source_identity()[1],
        "input_hashes": {k: v for k, v in binding["inputs"].items() if k != "request.json"},
        "native_tool_netlist": str(synth / "top_synth.v"),
    }
    (output / "producer.json").write_text(json.dumps(producer) + "\n")
    spec = {
        "source_sha": binding["source_sha"],
        "synthesis_engine": provider.LIBRELANE.contract(),
        "image_id": "sha256:" + "1" * 64,
        "pdk": {"liberty": str(output / "pdk/neutral.lib")},
        "source_files": production.source_identity()[1],
    }
    (output.parent / "inputs").mkdir()
    (output.parent / "inputs/request.json").write_text(json.dumps(spec) + "\n")
    return output, binding


@pytest.mark.parametrize("area", [True, False], ids=["area-rpt", "stats-json"])
def test_validator_consumes_both_canonical_area_paths(tmp_path, monkeypatch, area):
    output, binding = _native_output(tmp_path, area=area)
    monkeypatch.setattr(production, "_run_gate", lambda name, argv, directory: ("PASS", directory / (name + ".json")))
    evidence = production.validate_synthesis(output, binding)
    assert evidence.verdict == "PASS"
    assert all(evidence.gates[name] == "PASS" for name in provider.REQUIRED_GATES)
    assert provider.NETLIST in evidence.outputs
    assert (provider.AREA if area else provider.STATS) in evidence.outputs


def test_measured_fail_is_preserved_and_gate_text_is_ignored(tmp_path, monkeypatch):
    output, binding = _native_output(tmp_path, area=False)
    producer = json.loads((output / "producer.json").read_text())
    producer["status"] = "FAIL"
    producer["gates"] = {name: "PASS" for name in provider.REQUIRED_GATES}
    (output / "producer.json").write_text(json.dumps(producer) + "\n")
    monkeypatch.setattr(production, "_run_gate", lambda *args: pytest.fail("gates must not upgrade producer FAIL"))
    evidence = production.validate_synthesis(output, binding)
    assert evidence.verdict == "FAIL"


def test_co_mutated_receipt_and_command_journal_are_refused(tmp_path, monkeypatch):
    output, binding = _native_output(tmp_path, area=False)
    monkeypatch.setattr(production, "_run_gate", lambda name, argv, directory: ("PASS", directory / (name + ".json")))
    assert production.validate_synthesis(output, binding).verdict == "PASS"
    row = json.loads((output / "native_commands.jsonl").read_text())
    row["executed"] = False
    (output / "native_commands.jsonl").write_text(json.dumps(row) + "\n")
    with pytest.raises(em.Refusal, match="NATIVE_INVOCATION_UNPROVEN"):
        production.validate_synthesis(output, binding)


def test_only_public_controller_is_used_by_production_route():
    text = Path(production.__file__).read_text()
    assert "execution_policy" not in text
    assert "em.Controller" in text and "em.Registry" in text


def test_independent_engine_claim_remains_unavailable():
    with pytest.raises(em.Refusal, match="PRODUCTION_SYNTH_ENGINE_UNAVAILABLE"):
        provider.require_engine("genus-independent-synthesis")
    assert provider.population()["source_provider_count"] == 1
