from __future__ import annotations

import json
from pathlib import Path
import sys
from dataclasses import replace

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import execution_modes as em
from execution_adapters_release import (
    ALL_IDS,
    EXTERNAL_IDS,
    SOFTWARE_IDS,
    UNAVAILABLE_IDS,
    _site_path,
    register_release_adapters,
    validate,
    validate_hardmacro_receipt,
)
from execution_provider_catalog import RELEASE_IDS, coverage_rows
from execution_release_worker import execute as execute_release
from execution_release_rows import ROWS

BASE = "59cd75606885b71ca7d11bbe34baf2541671be68"


def _spec(step):
    return next(row for row in coverage_rows() if row["step_id"] == step)


def test_complete_release_coverage_and_no_fake_external_producers():
    assert len(RELEASE_IDS) == 14
    assert set(EXTERNAL_IDS) == {"40", "41", "42", "43", "44"}
    assert len(SOFTWARE_IDS) == 8
    assert set(UNAVAILABLE_IDS) == {"39"}
    assert set(ALL_IDS) == set(RELEASE_IDS)
    registry = register_release_adapters(source_sha=BASE, available=False)
    assert {a.step_id for a in registry._adapters.values()} == set(RELEASE_IDS)
    assert all(a.qualified is False for a in registry._adapters.values())
    assert all(_spec(s)["disposition"] == "external" for s in EXTERNAL_IDS)


def test_release_argv_binds_hardmacro_identity_and_declared_producer():
    registry = register_release_adapters(
        source_sha=BASE, available=False,
        parameters={"pdk_name": "gf180mcuD", "pdk_root": "/pdk",
                    "design_name": "macro_top", "module_role": "hardmacro"})
    adapter = registry.adapters("37.5ip")[0]
    argv = adapter.components[0].argv
    params = json.loads(argv[argv.index("--params-json") + 1])
    assert params["pdk_name"] == "gf180mcuD"
    assert params["pdk_root"] == "/pdk"
    assert params["design_name"] == "macro_top"
    assert params["source_sha"] == BASE
    assert "step_ip_release_docs_gen" in adapter.qualification_evidence


def test_375ip_condition_boundary_uses_route_and_owner_declaration():
    unknown = register_release_adapters(source_sha=BASE, path="IP")
    assert unknown.adapters("37.5ip")[0].applicability == "unknown"
    die = register_release_adapters(source_sha=BASE, path="IP", route_receipt={"marker": "slots"},
                                    declaration={"answers": {"deliverable": "DIE"},
                                                 "answer_provenance": {"deliverable": {
                                                     "answered_by": "owner", "citation": "owner fixture"}}})
    assert die.adapters("37.5ip")[0].applicability == "inapplicable"
    ip = register_release_adapters(source_sha=BASE, path="IP", route_receipt={"marker": "slots"},
                                   declaration={"answers": {"deliverable": "HARDMACRO"}})
    assert ip.adapters("37.5ip")[0].applicability == "applicable"


def test_375ic_and_step39_keep_canonical_condition_semantics():
    ic = register_release_adapters(source_sha=BASE, path="IC", route_receipt={"marker": "SELF_TAPEOUT"},
                                   declaration={"answers": {"deliverable": "DIE"}})
    assert ic.adapters("37.5ic")[0].applicability == "applicable"
    hardmacro = register_release_adapters(source_sha=BASE, path="IC", route_receipt={"marker": "SELF_TAPEOUT"},
                                          declaration={"answers": {"deliverable": "HARDMACRO"}})
    assert hardmacro.adapters("37.5ic")[0].applicability == "inapplicable"
    assert _spec("39")["applicability"]["IP"].startswith("applicable")
    step39 = next(a for a in register_release_adapters(source_sha=BASE)._adapters.values()
                  if a.step_id == "39")
    assert step39.available is False and step39.availability_reason == "FPGA_HARDWARE_ABSENT_NOT_MEASURED"


def test_release_component_runs_through_public_controller_and_preserves_nm(tmp_path):
    source = tmp_path / "seed.txt"
    source.write_text("source-only\n")
    context = em.Context("36", BASE, {"project/input/seed.txt": source},
                         {"metric": "canonical_evidence", "direction": "max"},
                         tuple(_spec("36")["consumer_gates"]), "librelane")
    registry = register_release_adapters(em.Registry(), source_sha=BASE, available=True)
    controller = em.Controller(registry, em.Budget(cpus=1, ram_mb=512, workers=1))
    plan = controller.plan(context, "default-mode")
    root = tmp_path / "run"
    assert plan["arms"] == ["release_36"]
    controller.run(context, root, "default-mode")
    adapter = registry.adapters("36")[0]
    receipt = json.loads((root / adapter.arm_id / "receipt.json").read_text())
    assert receipt["processes"][0]["rc"] == 0
    assert receipt["status"] in {"NOT_MEASURED", "FAIL"}
    assert (root / adapter.arm_id / "outputs" / "release-evidence.json").is_file()
    # tapeout_checklist_gen is the actual Step-36 producer.  A component
    # process with rc0 is insufficient when its canonical file is absent.
    assert (root / adapter.arm_id / "outputs" / "reports/audit/tapeout_checklist.json").is_file()


def test_375ip_worker_calls_declared_hardmacro_producer_and_stays_unmeasured(tmp_path):
    # This is an explicit empty source-bound fixture.  The worker must invoke
    # the declared hardmacro producer and preserve its capability/refusal
    # receipt; it must never manufacture native views from missing EDA inputs.
    inputs = tmp_path / "inputs"
    outputs = tmp_path / "outputs"
    (inputs / "project").mkdir(parents=True)
    (inputs / "project" / "source.txt").write_text("source-bound fixture\n")
    report = execute_release(inputs, outputs, step_id="37.5ip", params={
        "input_contract": ROWS["37.5ip"]["canonical"]["required_inputs"],
        "pdk_name": "gf180mcuD",
        "source_sha": BASE,
        "design_name": "fixture",
        "module_role": "hardmacro",
    })
    producers = {r.get("program") for r in report["producer"]}
    assert "phase3_one_shot_runner._write_ip_release_docs_context" in producers
    assert "digital_hardmacro_gen.run" in producers
    assert report["native_receipts"] == []
    assert report["qualification"] in {"NOT_MEASURED", "FAIL"}


def test_current_source_bound_fixture_reaches_eligible_controller_arm(tmp_path):
    # Step 16 is a source-owned software producer/gate pair.  This explicit
    # fixture contains only a DEF and a declared SDC clock; it is not a
    # historical tapeout bundle and carries its exact input binding through
    # the public Controller.
    floorplan = tmp_path / "floorplan.def"
    floorplan.write_text("VERSION 5.8 ;\n")
    sdc = tmp_path / "clock.sdc"
    sdc.write_text("create_clock -name clk -period 10 [get_ports clk]\n")
    row = _spec("16")
    context = em.Context(
        "16", BASE,
        {
            "project/phase3/stage3/pnr/floorplan.def": floorplan,
            "project/phase2/stage2/constraints/clock.sdc": sdc,
        },
        {"metric": "canonical_evidence", "direction": "max"},
        tuple(row["consumer_gates"]), "librelane")
    registry = register_release_adapters(em.Registry(), source_sha=BASE, available=True)
    controller = em.Controller(registry, em.Budget(cpus=1, ram_mb=512, workers=1))
    plan = controller.plan(context, "default-mode")
    assert plan["arms"] == ["release_16"]
    root = tmp_path / "eligible-run"
    summary = controller.run(context, root, "default-mode")
    assert summary["candidate_statuses"]["release_16"] == "ELIGIBLE"
    receipt = json.loads((root / "release_16" / "receipt.json").read_text())
    assert receipt["evidence"]["verdict"] == "PASS"
    assert receipt["evidence"]["gates"]["clock_plan_check"] == "PASS"
    assert (root / "release_16" / "outputs" /
            "phase3/stage3/cts/clock_plan.json").is_file()


def test_hardmacro_receipt_reverse_controls_keep_fail_and_refuse_fallback():
    with pytest.raises(em.Refusal):
        validate_hardmacro_receipt({"status": "PASS", "processes": []})
    assert validate_hardmacro_receipt({"status": "FAIL", "processes": []}, route="direct_magic") == "FAIL"
    with pytest.raises(em.Refusal):
        validate_hardmacro_receipt({"status": "PASS", "processes": [{"binary": "/x/magic"}]})


def test_release_reverse_mutations_refuse_missing_symbol_and_output(tmp_path):
    with pytest.raises(em.Refusal):
        _site_path("tapeout_checklist_gen.py:missing_release_producer")
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("mutated\n")
    binding = {"required_gates": ["canonical"]}
    receipt = {"binding": binding, "qualification": "PASS", "design_verdict": "PASS",
               "gate_records": [{"gate": "canonical", "verdict": "PASS"}],
               "outputs": {"artifact.txt": "0" * 64}}
    (tmp_path / "release-evidence.json").write_text(json.dumps(receipt))
    assert validate(tmp_path, binding).verdict == "NOT_MEASURED"
    receipt["qualification"] = "FAIL"
    (tmp_path / "release-evidence.json").write_text(json.dumps(receipt))
    assert validate(tmp_path, binding).verdict == "FAIL"
    receipt["qualification"] = "PASS"
    receipt["producer_verdict"] = "FAIL"
    (tmp_path / "release-evidence.json").write_text(json.dumps(receipt))
    assert validate(tmp_path, binding).verdict == "FAIL"


def test_public_controller_rejects_removed_release_producer_call(tmp_path):
    source = tmp_path / "floorplan.def"
    source.write_text("VERSION 5.8 ;\n")
    sdc = tmp_path / "clock.sdc"
    sdc.write_text("create_clock -name clk -period 10 [get_ports clk]\n")
    row = _spec("16")
    context = em.Context("16", BASE,
                         {"project/phase3/stage3/pnr/floorplan.def": source,
                          "project/phase2/stage2/constraints/clock.sdc": sdc},
                         {"metric": "canonical_evidence", "direction": "max"},
                         tuple(row["consumer_gates"]), "librelane")
    original = register_release_adapters(source_sha=BASE, available=True).adapters("16")[0]
    worker = PROGRAMS / "execution_release_worker.py"
    mutant = tmp_path / "execution_release_worker.py"
    text = worker.read_text()
    changed = text.replace("records = produce(str(step_id), project, params)", "records = []", 1)
    assert changed != text
    mutant.write_text(changed.replace(
        "from __future__ import annotations",
        "from __future__ import annotations\nimport sys\nsys.path.insert(0, " + repr(str(PROGRAMS)) + ")", 1))
    mutant_sources = {k: v for k, v in original.source_files.items()
                      if k != str(worker.resolve())}
    mutant_sources[str(mutant.resolve())] = em.digest(mutant)
    component = replace(original.components[0], argv=tuple(
        str(mutant.resolve()) if x == str(worker.resolve()) else x
        for x in original.components[0].argv))
    mutant_adapter = replace(original, source_files=mutant_sources,
                             components=(component,))
    registry = em.Registry(); registry.register(mutant_adapter)
    summary = em.Controller(registry, em.Budget(cpus=1, ram_mb=512, workers=1)).run(
        context, tmp_path / "mutant-run", "default-mode")
    assert summary["candidate_statuses"][mutant_adapter.arm_id] in {"NOT_MEASURED", "FAIL"}


def test_release_external_rows_are_visible_classifications_without_runtime_success():
    registry = register_release_adapters(source_sha=BASE, available=False)
    for step in EXTERNAL_IDS:
        adapter = registry.adapters(step)[0]
        assert adapter.role == "complementary"
        assert adapter.available is False
        assert adapter.availability_reason == "EXTERNAL_HANDOFF_ONLY"
