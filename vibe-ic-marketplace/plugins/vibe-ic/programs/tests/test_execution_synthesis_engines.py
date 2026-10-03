"""R2 Step9 provider controls: native-only, canonical and fail-closed."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import sys
from dataclasses import replace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import execution_modes as em
import execution_production as production
import execution_native_installation as installation
import execution_synthesis_engines as provider
from programs.tests import test_execution_receipt_chain as R
from programs.tests import test_execution_modes as H

isolated_transport = R.isolated_transport


def _project(tmp_path: Path):
    project = tmp_path / "project"
    (project / "input/submission_template").mkdir(parents=True)
    (project / "phase2/stage1/rtl").mkdir(parents=True)
    (project / "input/submission_template/tapeout_declaration.json").write_text(
        json.dumps({"top":"top", "die_area_budget_um":1000,
                    "answers":{"deliverable":"DIE"},
                    "answer_provenance":{"deliverable":{
                        "answered_by":"owner", "citation":"bounded IC software fixture"}}})+'\n')
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
    route = R.real_entry('IC', 'default', project)['route']
    from execution_policy import controller_fields
    ctx = em.Context("9", provider.source_sha(), inputs,
                     {"metric": "mapped_area_um2", "direction": "min", "top": "top"},
                     provider.REQUIRED_GATES, native_mode="librelane",
                     project_digest=route['project_digest'],
                     **controller_fields(ic_ip_path='IC', route_receipt=route))
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
    forged = replace(ctx, source_sha="0" * 40)
    registry = provider.build_registry(context=forged,
                                       image="sha256:" + "0" * 64)
    with pytest.raises(em.Refusal, match='ROUTE_SOURCE_MISMATCH'):
        em.Controller(registry, em.Budget(1, 256)).plan(forged)
    assert registry.adapters("9")[0].tool_version.startswith("UNMEASURED:") or \
        registry.adapters("9")[0].available is False


def test_tool_image_accepts_pinned_reference_but_returns_measured_image_id(monkeypatch):
    image_id = "sha256:" + "1" * 64
    reference = "registry.invalid/tools@" + image_id
    seen = []

    import librelane_image_facts as image_facts
    monkeypatch.setattr(image_facts, "image_facts", lambda image: (
        seen.append(image) or {"image": image, "image_id": image_id,
                               "librelane_version": "measured"}))

    measured, facts, missing = production._tool_and_image(reference)
    assert seen == [reference]
    assert measured == image_id and facts["image_id"] == image_id and missing == []

    monkeypatch.setattr(image_facts, "image_facts", lambda image: {
        "image": "registry.invalid/other@sha256:" + "2" * 64,
        "image_id": image_id, "librelane_version": "measured"})
    measured, _, missing = production._tool_and_image(reference)
    assert measured == "UNMEASURED:measured_image_reference"
    assert missing == ["measured_image_reference"]


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


def test_step9_pdk_config_must_be_in_current_manifest(tmp_path):
    root = tmp_path / "pdk"
    config = root / "libs.tech/librelane/config.tcl"
    config.parent.mkdir(parents=True)
    config.write_text("# measured LibreLane config\n")
    row = {"path": "libs.tech/librelane/config.tcl",
           "sha256": production._sha(config), "size": config.stat().st_size}
    manifest = {"schema": "vibe-ic/step9-pdk-tree/1", "root": str(root),
                "files": [row]}
    assert production._require_librelane_pdk_config(root, manifest) == row["path"]
    with pytest.raises(em.Refusal, match="STEP9_LIBRELANE_PDK_CONFIG_UNAVAILABLE"):
        production._require_librelane_pdk_config(
            root, {**manifest, "files": []})
    config.write_text("# changed after snapshot\n")
    with pytest.raises(em.Refusal, match="STEP9_LIBRELANE_PDK_CONFIG_UNAVAILABLE"):
        production._require_librelane_pdk_config(root, manifest)


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
    source_files = production.source_identity()[1]
    install_payload = {
        "schema": 1, "status": "MEASURED", "step_id": "9",
        "image_id": "sha256:" + "1" * 64,
        "source_sha": binding["source_sha"], "source_files": source_files,
        "input_hashes": {k: v for k, v in binding["inputs"].items()
                         if k != "request.json"},
    }
    install_digest = hashlib.sha256(json.dumps(
        install_payload, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False).encode()).hexdigest()
    install_receipt = {**install_payload, "receipt_sha256": install_digest}
    producer = {
        "binding": binding, "status": "PASS",
        "source_sha": binding["source_sha"],
        "native_entrypoint": provider.LIBRELANE.native_entrypoint,
        "synthesis_engine": provider.LIBRELANE.contract(),
        "source_files": source_files,
        "input_hashes": {k: v for k, v in binding["inputs"].items() if k != "request.json"},
        "native_installation_receipt_sha256": install_digest,
        "native_tool_netlist": str(synth / "top_synth.v"),
    }
    (output / "producer.json").write_text(json.dumps(producer) + "\n")
    spec = {
        "source_sha": binding["source_sha"],
        "synthesis_engine": provider.LIBRELANE.contract(),
        "image_id": "sha256:" + "1" * 64,
        "pdk": {"liberty": str(output / "pdk/neutral.lib")},
        "source_files": source_files,
        "native_installation_receipt": install_receipt,
        "native_installation_receipt_sha256": install_digest,
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
    request_path = output.parent / "inputs/request.json"
    request = json.loads(request_path.read_text())
    request["native_installation_receipt"]["image_id"] = "sha256:" + "2" * 64
    request_path.write_text(json.dumps(request) + "\n")
    with pytest.raises(em.Refusal, match="STEP9_INSTALLATION_RECEIPT_UNBOUND"):
        production.validate_synthesis(output, binding)
    request["native_installation_receipt"]["image_id"] = "sha256:" + "1" * 64
    request_path.write_text(json.dumps(request) + "\n")
    row = json.loads((output / "native_commands.jsonl").read_text())
    row["executed"] = False
    (output / "native_commands.jsonl").write_text(json.dumps(row) + "\n")
    with pytest.raises(em.Refusal, match="NATIVE_INVOCATION_UNPROVEN"):
        production.validate_synthesis(output, binding)


@pytest.mark.parametrize(("producer_status", "expected"), [
    ("FAIL", "FAIL"), ("NOT_MEASURED", "NOT_MEASURED"),
    ("MISSING", "NOT_MEASURED"),
])
def test_step9_measured_failure_precedence_through_supervised_gate(
        tmp_path, producer_status, expected):
    """The real Step9 gate worker must reach the producer-aware consumer."""
    source, files = production.source_identity()
    fixture = Path(__file__).parent / "fixtures/step9_producer_status.py"
    worker = production.WORKER
    files[str(fixture.resolve())] = em.digest(fixture)
    files[str(worker.resolve())] = em.digest(worker)
    paths = {
        "request.json": tmp_path / "request.json",
        "project/input/submission_template/tapeout_declaration.json": tmp_path / "declaration.json",
        "project/phase2/stage1/rtl/top.v": tmp_path / "top.v",
        "project/phase1/generated_docs/L8_TIMING_WAVEFORM.json": tmp_path / "l8.json",
        "pdk/neutral.lib": tmp_path / "neutral.lib",
    }
    paths["request.json"].write_text(json.dumps({
        "step_id": "9", "image_id": "sha256:" + "1" * 64,
        "synthesis_engine": provider.LIBRELANE.contract(),
    }) + "\n")
    paths["project/input/submission_template/tapeout_declaration.json"].write_text("{}\n")
    paths["project/phase2/stage1/rtl/top.v"].write_text("module top; endmodule\n")
    paths["project/phase1/generated_docs/L8_TIMING_WAVEFORM.json"].write_text("{}\n")
    paths["pdk/neutral.lib"].write_text("library(neutral) {}\n")
    context = H.NeutralContext(
        "9", source, paths,
        {"metric": "mapped_area_um2", "direction": "min", "top": "top"},
        ("synth_netlist_check",), native_mode="librelane",
        ic_ip_path="IC", route_receipt={"kind": "neutral-test", "ic_ip_path": "IC"})
    facts = {"flows": {"Chip": list(provider.LIBRELANE.native_steps)}}
    real = production._native_adapter(
        context, source, files, "sha256:" + "1" * 64, facts,
        paths["request.json"], {"name": "neutral", "liberty": "neutral.lib"},
        sys.executable, installation_receipt={"test_fixture": "not-an-installation-claim"})
    producer_component = em.Component("fixture-producer", (
        str(Path(sys.executable).resolve()), str(fixture.resolve()), "{outputs}",
        producer_status), timeout_s=10)
    gate_component = next(c for c in real.components if c.name == "synth_netlist_check")
    adapter = replace(real, tool_id="fixture-producer",
                      components=(producer_component, gate_component),
                      required_outputs=("producer.json", "native_commands.jsonl"),
                      output_contract={"producer.json": ("producer.json",),
                                       "native_commands.jsonl": ("native_commands.jsonl",)})
    registry = em.Registry()
    registry.register(adapter)
    portfolio = {"meta": {"test_only": True}, "steps": [{
        "id": "9", "mandatory_gate_programs": ["synth_netlist_check"],
        "required_output_contract": ["producer.json", "native_commands.jsonl"],
    }]}
    controller = em.Controller(registry, em.Budget(1, 4096), portfolio)
    root = tmp_path / "run"
    result = controller.run(context, root, "ultra-mode")
    arm = json.loads((root / adapter.arm_id / "receipt.json").read_text())
    assert result["candidate_statuses"][adapter.arm_id] == expected
    assert arm["status"] == expected
    if producer_status == "FAIL":
        assert [row["rc"] for row in arm["processes"]] == [0, 0]
        assert arm["reason"] != "PROCESS_ERROR"
    else:
        assert arm["status"] == "NOT_MEASURED"


def test_only_public_controller_is_used_by_production_route():
    tree = ast.parse(Path(production.__file__).read_text())
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    # Production consumes the live ordinary Registry/Controller. Metadata and
    # fixed-step dispatch imports grant no right to construct another instance.
    assert not [node for node in calls if isinstance(node.func, ast.Attribute)
                and node.func.attr in ('Controller', 'Registry')]
    imported = {alias.asname or alias.name: alias.name for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module == 'execution_modes'
                for alias in node.names}
    assert not [node for node in calls if isinstance(node.func, ast.Name)
                and imported.get(node.func.id) in ('Controller', 'Registry')]
    assert not [node for node in calls if isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == 'controller'
                and node.func.attr in ('plan', 'run', 'adopt')]
    dispatch = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == 'dispatch_site')
    invoked = [node for node in ast.walk(dispatch) if isinstance(node, ast.Call)]
    fixed = [node for node in invoked if isinstance(node.func, ast.Name)
             and node.func.id == 'dispatch_fixed_step']
    assert len(fixed) == 1
    assert len(fixed[0].args) == 2 and isinstance(fixed[0].args[1], ast.Constant)
    assert fixed[0].args[1].value == '9'
    assert {node.func.id for node in invoked if isinstance(node.func, ast.Name)} <= {
        'tuple', 'map', 'str', 'Path', 'dict', 'dispatch_fixed_step'}
    assert any(isinstance(node, ast.ImportFrom) and node.module == 'execution_policy'
               and any(alias.name == 'controller_fields' for alias in node.names)
               for node in ast.walk(tree))


def test_independent_engine_claim_remains_unavailable():
    with pytest.raises(em.Refusal, match="PRODUCTION_SYNTH_ENGINE_UNAVAILABLE"):
        provider.require_engine("genus-independent-synthesis")
    assert provider.population()["source_provider_count"] == 1
