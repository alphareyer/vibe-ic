"""Source-only acceptance tests for the Step 9 provider boundary."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import execution_modes as em
import execution_synthesis_engines as provider


SOURCE_SHA = "75be96c2ade3dfcc5c36a185d4ea07ef5bb19f91"
GATES = provider.REQUIRED_GATES


def _context(tmp_path: Path) -> em.Context:
    rtl = tmp_path / "rtl.v"
    rtl.write_text("module top(input wire a, output wire y); assign y = a; endmodule\n")
    objective = {"metric": "mapped_area_um2", "direction": "min", "top": "top"}
    return em.Context("9", SOURCE_SHA, {"project/phase2/stage1/rtl/top.v": rtl},
                      objective, GATES, native_mode="librelane")


def _fixture_registry(ctx: em.Context) -> em.Registry:
    return provider.build_registry(
        context=ctx, image="source-fixture", tool=sys.executable, fixture=True,
    )


def test_default_provider_is_callable_and_binds_downstream_gates(tmp_path):
    ctx = _context(tmp_path)
    controller = em.Controller(_fixture_registry(ctx), em.Budget(1, 256))
    plan = controller.plan(ctx)
    assert plan["mode"] == "default-mode"
    assert plan["arms"] == [provider.LIBRELANE.arm_id]

    root = tmp_path / "run"
    assert controller.run(ctx, root)["status"] == "AWAITING_AI_SELECTION"
    receipt = json.loads((root / provider.LIBRELANE.arm_id / "receipt.json").read_text())
    assert receipt["status"] == "ELIGIBLE"
    assert receipt["evidence"]["verdict"] == "PASS"
    assert set(receipt["evidence"]["gates"]) == set(GATES)
    assert set(receipt["evidence"]["outputs"]) == {provider.NETLIST, provider.STATS}

    choice = {
        "arm_id": provider.LIBRELANE.arm_id,
        "binding": ctx.binding(),
        "receipt_sha256": em.digest(root / provider.LIBRELANE.arm_id / "receipt.json"),
        "reviewer": "step9 source acceptance",
        "rationale": "receipt-bound provider evidence and all canonical gates",
    }
    assert controller.adopt(ctx, root, choice)["status"] == "ADOPTED"


def test_explicit_ultra_deduplicates_same_yosys_abc_family(tmp_path):
    ctx = _context(tmp_path)
    registry = provider.build_registry(
        context=ctx, image="source-fixture", tool=sys.executable, fixture=True,
        include_same_family_wrapper=True,
    )
    controller = em.Controller(registry, em.Budget(2, 512))
    plan = controller.plan(ctx, "ultra-mode")
    assert plan["mode"] == "ultra-mode"
    assert plan["arms"] == [provider.LIBRELANE.arm_id]
    row = next(row for row in plan["portfolio"]
                if row["arm_id"] == provider.DIRECT_YOSYS.arm_id)
    assert row["admission"] == "SAME_ENGINE_FAMILY"


def test_unrecognized_mode_is_refused_by_public_controller(tmp_path):
    ctx = _context(tmp_path)
    controller = em.Controller(_fixture_registry(ctx), em.Budget(1, 256))
    with pytest.raises(em.Refusal, match="INVALID_EXECUTION_MODE"):
        controller.plan(ctx, "ultra")


def test_missing_image_tool_and_input_are_not_measured(tmp_path):
    ctx = _context(tmp_path)
    missing_input = {"project/phase2/stage1/rtl/top.v": tmp_path / "missing.v"}
    registry = provider.build_registry(context=ctx, inputs=missing_input)
    plan = em.Controller(registry, em.Budget(1, 256)).plan(ctx)
    assert plan["status"] == "NOT_MEASURED"
    for row in plan["portfolio"]:
        assert row["admission"] == "UNAVAILABLE"
        assert row["availability_reason"] == "MISSING_NATIVE_FACTS:image,tool,input:project/phase2/stage1/rtl/top.v"


def test_measured_fail_has_precedence_over_unmeasured_gate(tmp_path):
    ctx = _context(tmp_path)
    registry = _fixture_registry(ctx)
    controller = em.Controller(registry, em.Budget(1, 256))
    root = tmp_path / "run"
    controller.run(ctx, root)
    receipt_path = root / provider.LIBRELANE.arm_id / "outputs" / provider.PRODUCER_RECEIPT
    receipt = json.loads(receipt_path.read_text())
    receipt["gates"]["synth_netlist_check"] = "FAIL"
    receipt["gates"]["provenance_check"] = "NOT_MEASURED"
    receipt_path.write_text(json.dumps(receipt, sort_keys=True) + "\n")
    arm = registry.adapters("9")[0]
    evidence = arm.validate(root / provider.LIBRELANE.arm_id / "outputs", ctx.binding())
    assert evidence.verdict == "FAIL"
    assert evidence.gates["synth_netlist_check"] == "FAIL"


def test_producer_fail_is_not_upgraded_by_pass_gate_text(tmp_path):
    ctx = _context(tmp_path)
    registry = _fixture_registry(ctx)
    controller = em.Controller(registry, em.Budget(1, 256))
    root = tmp_path / "run"
    controller.run(ctx, root)
    receipt_path = root / provider.LIBRELANE.arm_id / "outputs" / provider.PRODUCER_RECEIPT
    receipt = json.loads(receipt_path.read_text())
    receipt["status"] = "FAIL"
    receipt_path.write_text(json.dumps(receipt, sort_keys=True) + "\n")
    arm = registry.adapters("9")[0]
    assert arm.validate(root / provider.LIBRELANE.arm_id / "outputs", ctx.binding()).verdict == "FAIL"


def test_independent_engine_claim_is_refused_before_native_execution():
    with pytest.raises(em.Refusal, match="PRODUCTION_SYNTH_ENGINE_UNAVAILABLE"):
        provider.require_engine("genus-independent-synthesis")
    assert provider.population()["source_provider_count"] == 1
    assert provider.population()["native_qualification"] == "NOT_MEASURED"
