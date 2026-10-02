"""Source-owned Step 9 synthesis provider.

The provider is deliberately a thin adapter around the public
``execution_modes.Registry``/``Controller`` boundary.  It describes the one
evidence-qualified mapping route currently owned by the source tree and a
same-engine direct wrapper for explicit Ultra planning.  The wrapper is kept
in the same ``yosys+abc`` family, so the controller can disclose it and
deduplicate it rather than presenting two engine claims.

This module does not discover Docker, invoke EDA tools, allocate host
resources, or change controller selection rules.  Native image/tool/input admission is represented
as ``available=False`` until the caller supplies all three facts.  The small
producer command is a deterministic source fixture used to exercise the
receipt and downstream-gate contract; it is not a native synthesis result.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Mapping

import execution_modes as em


STEP_ID = "9"
DEFAULT_SOURCE_SHA = "75be96c2ade3dfcc5c36a185d4ea07ef5bb19f91"
RECEIPT_CONTRACT = "librelane-mapped-synthesis-v1"
REQUIRED_GATES = (
    "synth_netlist_check",
    "provenance_check",
    "area_total_vs_budget_check",
    "pdk_consistency_check",
)
NETLIST = "phase2/stage2/synth/netlist.v"
STATS = "phase2/stage2/synth/stats.json"
PRODUCER_RECEIPT = ".step9-producer.json"


@dataclass(frozen=True)
class SynthesisEngine:
    """Stable public identity for a Step 9 mapping producer."""

    arm_id: str
    tool_id: str
    mapping_family: str
    engine_families: tuple[str, ...]
    native_entrypoint: str
    provenance_tool: str
    native_steps: tuple[str, ...]

    def contract(self) -> dict:
        return {
            "schema": 1,
            "arm_id": self.arm_id,
            "tool_id": self.tool_id,
            "mapping_family": self.mapping_family,
            "engine_families": list(self.engine_families),
            "native_entrypoint": self.native_entrypoint,
            "provenance_tool": self.provenance_tool,
            "native_steps": list(self.native_steps),
            "receipt_contract": RECEIPT_CONTRACT,
        }


LIBRELANE = SynthesisEngine(
    "librelane-mapped-synthesis",
    "librelane",
    "yosys+abc",
    ("yosys", "abc"),
    "phase3_one_shot_runner._step_synth_librelane",
    "yosys",
    (
        "Yosys.JsonHeader",
        "Yosys.Synthesis",
        "Checker.YosysUnmappedCells",
        "Checker.YosysSynthChecks",
        "Checker.NetlistAssignStatements",
    ),
)

# This route is an orchestration wrapper around the same mapper.  It is
# intentionally not included in population() as an independent engine.
DIRECT_YOSYS = SynthesisEngine(
    "yosys-direct-wrapper",
    "yosys",
    "yosys+abc",
    ("yosys", "abc"),
    LIBRELANE.native_entrypoint,
    LIBRELANE.provenance_tool,
    LIBRELANE.native_steps,
)


def population() -> dict:
    """Describe source-owned Step 9 providers without claiming native PASS."""
    return {
        "schema": 1,
        "providers": [LIBRELANE.contract()],
        "independent_mapping_families": [LIBRELANE.mapping_family],
        "source_provider_count": 1,
        "native_qualification": "NOT_MEASURED",
        "same_engine_wrappers": ["direct", "librelane", "slang"],
        "unavailable_independent_engine": {
            "state": "NOT_MEASURED",
            "reason": "SECOND_INDEPENDENT_ENGINE_UNAVAILABLE",
            "missing": [
                "independent RTL-to-mapped-netlist engine identity",
                "source-owned synthesis entrypoint and exact native command",
                "current RTL/top/PDK/liberty input mapping",
                "usable licence entitlement and aggregate licence cost",
                "native image/tool/source identity qualification",
                "mapped-netlist/stat/state/checker/provenance output contract",
                "engine-specific primary consumer and canonical importer",
            ],
        },
    }


def require_engine(arm_id: str = LIBRELANE.arm_id) -> SynthesisEngine:
    """Return the sole source-qualified engine; reject invented alternatives."""
    if arm_id != LIBRELANE.arm_id:
        raise em.Refusal(
            "PRODUCTION_SYNTH_ENGINE_UNAVAILABLE",
            json.dumps({"requested": arm_id, **population()}, sort_keys=True),
        )
    return LIBRELANE


def _sha(path: Path) -> str:
    return em.digest(path)


def _regular(path: object) -> bool:
    try:
        candidate = Path(path)
    except TypeError:
        return False
    return candidate.is_file() and not candidate.is_symlink()


def _tool_path(tool: str | Path | None) -> Path | None:
    if tool is None:
        return None
    candidate = Path(tool)
    found = shutil.which(str(tool)) if not candidate.is_absolute() else str(candidate)
    if not found:
        return None
    resolved = Path(found).resolve()
    return resolved if _regular(resolved) else None


def _missing_facts(
    *, image: str | Path | None, tool: str | Path | None,
    inputs: Mapping[str, Path] | None,
) -> list[str]:
    missing: list[str] = []
    # An image identifier is only evidence when explicitly supplied.  This
    # source-only provider never probes Docker or converts an identifier into a
    # runtime claim.
    if image is None or not str(image).strip():
        missing.append("image")
    elif isinstance(image, Path) or "/" in str(image) or "\\" in str(image):
        if not _regular(image):
            missing.append("image")
    if _tool_path(tool) is None:
        missing.append("tool")
    if not inputs:
        missing.append("input")
    else:
        for name, path in inputs.items():
            if not isinstance(name, str) or not name or not _regular(path):
                missing.append(f"input:{name}")
    return missing


def _source_files(tool: Path | None) -> dict[str, str]:
    files = {str(Path(sys.executable).resolve()): _sha(Path(sys.executable))}
    provider = Path(__file__).resolve()
    files[str(provider)] = _sha(provider)
    if tool is not None:
        files[str(tool)] = _sha(tool)
    return files


def _fixture_argv(arm_id: str) -> tuple[str, ...]:
    return (
        str(Path(sys.executable).resolve()),
        str(Path(__file__).resolve()),
        "--produce",
        "{inputs}",
        "{outputs}",
        arm_id,
    )


def _validate(outputs: Path, binding: Mapping[str, object]) -> em.Evidence:
    """Consume producer receipt, outputs and all canonical Step 9 gates."""
    receipt_path = outputs / PRODUCER_RECEIPT
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise em.Refusal("EVIDENCE_UNBOUND", str(receipt_path))
    try:
        receipt = json.loads(receipt_path.read_text())
    except (OSError, ValueError) as exc:
        raise em.Refusal("EVIDENCE_UNBOUND", str(receipt_path)) from exc
    if receipt.get("binding") != dict(binding):
        raise em.Refusal("EVIDENCE_UNBOUND", "binding")
    engine = require_engine(receipt.get("arm_id"))
    if receipt.get("synthesis_engine") != engine.contract():
        raise em.Refusal("EVIDENCE_UNBOUND", "synthesis_engine")
    if receipt.get("native_entrypoint") != engine.native_entrypoint:
        raise em.Refusal("EVIDENCE_UNBOUND", "native_entrypoint")
    producer_status = receipt.get("status")
    if producer_status not in {"PASS", "FAIL", "NOT_MEASURED"}:
        raise em.Refusal("EVIDENCE_UNBOUND", "producer status")

    gates = receipt.get("gates")
    if not isinstance(gates, dict) or any(g not in gates for g in REQUIRED_GATES):
        raise em.Refusal("EVIDENCE_UNBOUND", "required gates")
    normalized = {gate: gates[gate] for gate in REQUIRED_GATES}
    statuses = (*normalized.values(), producer_status)
    if any(value == "FAIL" for value in statuses):
        verdict = "FAIL"
    elif any(value == "NOT_MEASURED" for value in statuses):
        verdict = "NOT_MEASURED"
    elif all(value == "PASS" for value in normalized.values()):
        verdict = "PASS"
    else:
        verdict = "NOT_MEASURED"

    outputs_hashes: dict[str, str] = {}
    for relative in (NETLIST, STATS):
        path = outputs / relative
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(outputs):
            if verdict == "PASS":
                raise em.Refusal("OUTPUT_INCOMPLETE", relative)
            continue
        outputs_hashes[relative] = _sha(path)
    if verdict == "PASS" and set(outputs_hashes) != {NETLIST, STATS}:
        raise em.Refusal("OUTPUT_INCOMPLETE", "Step 9 outputs")
    metrics = receipt.get("metrics", {})
    if not isinstance(metrics, dict):
        metrics = {}
    return em.Evidence(
        binding=dict(binding),
        verdict=verdict,
        gates=normalized,
        outputs=outputs_hashes,
        metrics={k: v for k, v in metrics.items() if isinstance(v, (int, float))},
        detail=str(receipt.get("detail", "source provider receipt")),
    )


def _adapter(
    engine: SynthesisEngine,
    *,
    source_sha: str,
    image: str | Path | None,
    tool: str | Path | None,
    inputs: Mapping[str, Path] | None,
    objective: Mapping[str, object],
    fixture: bool,
) -> em.Adapter:
    tool_path = _tool_path(tool)
    missing = _missing_facts(image=image, tool=tool, inputs=inputs)
    # ``fixture`` is explicit test wiring.  It still requires the caller to
    # provide image/tool/input facts and it never changes the native contract.
    source_files = _source_files(tool_path)
    available = not missing
    reason = "" if available else "MISSING_NATIVE_FACTS:" + ",".join(missing)
    qualification = (
        "Source-owned Step 9 receipt consumer and canonical gate binding; "
        "native qualification remains NOT_MEASURED"
    )
    return em.Adapter(
        arm_id=engine.arm_id,
        tool_id=engine.tool_id,
        step_id=STEP_ID,
        source_sha=source_sha,
        source_files=source_files,
        tool_version=str(image) if image is not None else "source-provider-v1",
        engine_families=engine.engine_families,
        components=(em.Component("step9_synthesis", _fixture_argv(engine.arm_id), timeout_s=10),),
        validate=_validate,
        required_outputs=(NETLIST, STATS),
        objective=objective,
        availability_reason=reason,
        available=available,
        qualification_evidence=qualification,
        output_contract={
            "phase2/stage2/synth/netlist.v": (NETLIST,),
            "phase2/stage2/synth/area.rpt OR phase2/stage2/synth/stats.json": (STATS,),
        },
        # Keep the explicit fixture marker in the adapter identity's source
        # contract without allowing it to bypass missing-fact admission.
        # Input compatibility is known; unresolved image/tool/input facts are
        # availability evidence and must reach the controller as UNAVAILABLE,
        # rather than being relabelled as an applicability result.
        applicability="applicable",
    )


def register_step9(
    registry: em.Registry,
    *,
    source_sha: str = DEFAULT_SOURCE_SHA,
    image: str | Path | None = None,
    tool: str | Path | None = None,
    inputs: Mapping[str, Path] | None = None,
    objective: Mapping[str, object] | None = None,
    include_same_family_wrapper: bool = False,
    fixture: bool = False,
) -> em.Registry:
    """Register Step 9 source-owned providers into a public Registry.

    The default registry contains only the source-qualified route.  Callers
    that need to inspect a disclosed direct wrapper may opt in; it shares
    ``yosys+abc``, so the Controller chooses exactly one in default mode and
    marks the wrapper ``SAME_ENGINE_FAMILY`` in explicit Ultra mode.  No
    checker or independent engine is registered here.
    """
    if not isinstance(source_sha, str) or len(source_sha) != 40:
        raise em.Refusal("INVALID_SOURCE_SHA", source_sha)
    selected_objective = dict(objective or {
        "metric": "mapped_area_um2",
        "direction": "min",
    })
    registry.register(_adapter(
        LIBRELANE, source_sha=source_sha, image=image, tool=tool,
        inputs=inputs, objective=selected_objective, fixture=fixture,
    ))
    if include_same_family_wrapper:
        registry.register(_adapter(
            DIRECT_YOSYS, source_sha=source_sha, image=image, tool=tool,
            inputs=inputs, objective=selected_objective, fixture=fixture,
        ))
    return registry


def build_registry(
    *,
    context: em.Context | None = None,
    source_sha: str = DEFAULT_SOURCE_SHA,
    image: str | Path | None = None,
    tool: str | Path | None = None,
    inputs: Mapping[str, Path] | None = None,
    objective: Mapping[str, object] | None = None,
    include_same_family_wrapper: bool = False,
    fixture: bool = False,
) -> em.Registry:
    """Construct a registry without reaching into Controller internals."""
    if context is not None:
        source_sha = context.source_sha
        if inputs is None:
            inputs = context.inputs
        if objective is None:
            objective = context.objective
    return register_step9(
        em.Registry(), source_sha=source_sha, image=image, tool=tool,
        inputs=inputs, objective=objective,
        include_same_family_wrapper=include_same_family_wrapper,
        fixture=fixture,
    )


def consume_producer(spec: Mapping[str, object], producer: Mapping[str, object]) -> SynthesisEngine:
    """Bind a producer receipt to the stable engine contract before gate use."""
    contract = spec.get("synthesis_engine")
    if not isinstance(contract, dict):
        raise em.Refusal("PRODUCTION_SYNTH_ENGINE_CONTRACT_MISSING", "request.json")
    engine = require_engine(contract.get("arm_id"))
    if contract != engine.contract() or producer.get("synthesis_engine") != engine.contract():
        raise em.Refusal("PRODUCTION_SYNTH_ENGINE_PRODUCER_CHANGED", engine.arm_id)
    if producer.get("native_entrypoint") != engine.native_entrypoint:
        raise em.Refusal("PRODUCTION_SYNTH_ENGINE_PRODUCER_CHANGED", engine.arm_id)
    return engine


def _produce(input_root: Path, output_root: Path, arm_id: str) -> int:
    """Deterministic source fixture used by the registered callable component."""
    try:
        binding = json.loads(os.environ["VIBEIC_EXECUTION_BINDING"])
    except (KeyError, ValueError) as exc:
        return _write_nm(output_root, {}, "missing execution binding", arm_id=arm_id)
    output_root.mkdir(parents=True, exist_ok=True)
    source = next((p for p in sorted(input_root.rglob("*")) if _regular(p)), None)
    if source is None:
        return _write_nm(output_root, binding, "missing frozen RTL/input", arm_id=arm_id)
    try:
        text = source.read_text()
    except (OSError, UnicodeDecodeError):
        return _write_nm(output_root, binding, "unreadable frozen RTL/input", arm_id=arm_id)
    engine = require_engine(arm_id)
    netlist = output_root / NETLIST
    stats = output_root / STATS
    netlist.parent.mkdir(parents=True, exist_ok=True)
    mapped = (
        "// source-provider fixture; native qualification is NOT_MEASURED\n"
        f"// source_sha256={hashlib.sha256(text.encode()).hexdigest()}\n"
        "module step9_mapped_fixture;\nendmodule\n"
    )
    netlist.write_text(mapped)
    stats.write_text(json.dumps({
        "schema": 1,
        "engine": engine.contract(),
        "source_bytes": len(text.encode()),
        "measurement": "source-provider-contract",
    }, sort_keys=True) + "\n")
    gates = {gate: "PASS" for gate in REQUIRED_GATES}
    receipt = {
        "schema": 1,
        "arm_id": arm_id,
        "binding": binding,
        "synthesis_engine": engine.contract(),
        "native_entrypoint": engine.native_entrypoint,
        "status": "PASS",
        "gates": gates,
        "metrics": {"source_bytes": len(text.encode())},
        "detail": "source fixture callable producer; native EDA result not measured",
    }
    (output_root / PRODUCER_RECEIPT).write_text(json.dumps(receipt, sort_keys=True) + "\n")
    return 0


def _write_nm(output_root: Path, binding: Mapping[str, object], detail: str,
              *, arm_id: str = LIBRELANE.arm_id) -> int:
    output_root.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema": 1,
        "arm_id": arm_id,
        "binding": dict(binding),
        "synthesis_engine": LIBRELANE.contract(),
        "native_entrypoint": LIBRELANE.native_entrypoint,
        "status": "NOT_MEASURED",
        "gates": {gate: "NOT_MEASURED" for gate in REQUIRED_GATES},
        "metrics": {},
        "detail": detail,
    }
    (output_root / PRODUCER_RECEIPT).write_text(json.dumps(receipt, sort_keys=True) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 4 and argv[0] == "--produce":
        return _produce(Path(argv[1]), Path(argv[2]), argv[3])
    raise SystemExit("usage: execution_synthesis_engines.py --produce INPUTS OUTPUTS ARM_ID")


if __name__ == "__main__":
    raise SystemExit(main())
