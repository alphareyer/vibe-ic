"""Minimal ULTRACORE seam for the reviewed 23-row backend family.

This module registers one complete source-owned producer per canonical row in
the existing :mod:`execution_modes` registry.  It contains no native runner,
lease, dispatch, or scheduler.  A registered adapter is a reachability
description; native qualification stays ``NOT_MEASURED`` until a current
producer receipt and every downstream gate are observed.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from typing import Mapping

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import execution_modes as em
from execution_provider_catalog import BACKEND_IDS, BACKEND_ROWS, coverage_rows

HERE = Path(__file__).resolve().parent
POLICY = HERE / "data/execution_backend_policy.json"
ROWS = BACKEND_ROWS


@dataclass(frozen=True)
class Step30InstrumentPlan:
    simulators: tuple[str, ...]
    defaulted: bool
    runtime_status: str = "NOT_MEASURED"


def _site_path(site: str) -> Path:
    module, _, symbol = site.partition(":")
    path = HERE / module
    if not module or not symbol or not path.is_file():
        raise em.Refusal("BACKEND_PRODUCER_SITE_MISSING", site)
    return path


def _source_files(spec: Mapping[str, object]) -> dict[str, str]:
    paths = {HERE / "execution_modes.py", Path(__file__), HERE / "execution_provider_catalog.py"}
    paths.update(_site_path(site) for site in spec["producer_sites"])
    for gate in spec["consumer_gates"]:
        candidate = HERE / (str(gate) + ".py")
        if candidate.is_file():
            paths.add(candidate)
    paths.add(HERE / "execution_backend_snapshot.py")
    paths = {p.resolve() for p in paths if p.is_file() and not p.is_symlink()}
    paths.add(Path(sys.executable).resolve())
    return {str(p): em.digest(p) for p in sorted(paths)}


def _evidence_from_receipt(outputs: Path, binding: Mapping[str, object], *, gates: tuple[str, ...]) -> em.Evidence:
    """Read a provider receipt without treating rc0 or metadata as evidence."""
    path = outputs / "backend_result.json"
    if not path.is_file() or path.is_symlink():
        return em.Evidence(binding, "NOT_MEASURED", {g: "NOT_MEASURED" for g in gates}, {},
                           detail="NOT_MEASURED: no current backend native receipt")
    try:
        result = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return em.Evidence(binding, "NOT_MEASURED", {}, {}, detail=f"malformed receipt: {exc}")
    measured_fail = result.get("producer_verdict") == "FAIL" or any(
        value == "FAIL" for value in (result.get("gates") or {}).values())
    if result.get("binding") != dict(binding):
        return em.Evidence(binding, "FAIL" if measured_fail else "NOT_MEASURED", {}, {},
                           detail="BACKEND_RESULT_UNBOUND")
    observed = dict(result.get("gates") or {})
    status = "FAIL" if measured_fail else "NOT_MEASURED"
    if not measured_fail and result.get("producer_verdict") == "PASS" and all(
            observed.get(g) == "PASS" for g in gates) and result.get("native_receipts"):
        status = "PASS"
    outputs_hashes = {"backend_result.json": em.digest(path)}
    for name, expected in (result.get("outputs") or {}).items():
        candidate = outputs / Path(name)
        if candidate.is_file() and not candidate.is_symlink() and em.digest(candidate) == expected:
            outputs_hashes[name] = expected
        elif measured_fail:
            return em.Evidence(binding, "FAIL", observed, outputs_hashes,
                               detail="measured FAIL with changed output: " + str(name))
        else:
            return em.Evidence(binding, "NOT_MEASURED", observed, outputs_hashes,
                               detail="BACKEND_OUTPUT_UNMEASURED: " + str(name))
    return em.Evidence(binding, status, observed, outputs_hashes,
                       detail=str(result.get("detail", "")))


def validate(outputs: Path, binding: Mapping[str, object]) -> em.Evidence:
    return _evidence_from_receipt(Path(outputs), binding,
                                  gates=tuple(binding.get("required_gates", ())))


def _step30_simulators(params: Mapping[str, object] | None = None) -> tuple[str, ...]:
    """Resolve one default ngspice instrument or an explicit Xyce request."""
    import path_spice_tool as spice
    params = params or {}
    if "simulators" not in params:
        return tuple(spice.SIMULATORS[:1])
    requested = params["simulators"]
    if isinstance(requested, str):
        requested = (requested,)
    if not isinstance(requested, (tuple, list)) or not requested:
        raise em.Refusal("BACKEND_SPICE_PROVIDER_UNSTATED", repr(requested))
    values = tuple(str(x).lower() for x in requested)
    if any(x not in tuple(spice.SIMULATORS) for x in values) or len(set(values)) != len(values):
        raise em.Refusal("BACKEND_SPICE_PROVIDER_UNSTATED", repr(requested))
    return values


def step30_instrument_plan(params: Mapping[str, object] | None = None) -> Step30InstrumentPlan:
    params = params or {}
    return Step30InstrumentPlan(_step30_simulators(params), "simulators" not in params)


def _provider_route(step_id: str, params: Mapping[str, object] | None = None) -> tuple[str, tuple[str, ...]]:
    if step_id == "30":
        instruments = _step30_simulators(params)
        return instruments[0], ("opensta",) + tuple(instruments)
    spec = next(row for row in coverage_rows() if row["step_id"] == step_id)
    return str(spec["tool_id"]), tuple(spec["engine_families"])


def _step37_route_specs() -> tuple[dict, ...]:
    """Expose one streamout producer family; direct Magic is fallback refusal."""
    spec = next(row for row in coverage_rows() if row["step_id"] == "37")
    return ({"route": "librelane", "tool_id": spec["tool_id"],
             "engine_families": tuple(spec["engine_families"])},)


def validate_streamout_receipt(receipt: Mapping[str, object], *, route: str = "librelane",
                               measured_verdict: str | None = None) -> str:
    """Validate the actual streamout-engine receipt with measured FAIL priority."""
    if measured_verdict == "FAIL" or receipt.get("status") == "FAIL":
        return "FAIL"
    if route == "direct_magic":
        raise em.Refusal("BACKEND_DIRECT_MAGIC_FALLBACK_REFUSED",
                         "direct Magic fallback is not an independent Ultra arm")
    allowed = ("magic", "klayout") if route == "librelane" else ()
    expected_arm = "backend_37_librelane"
    if (receipt.get("schema") != "vibeic/step37-streamout-engine/1" or
            receipt.get("step_id") != "37" or receipt.get("arm_id") != expected_arm or
            receipt.get("route") != route or
            receipt.get("allowed_streamout_engines") != list(allowed) or
            receipt.get("streamout_engine") not in allowed or
            receipt.get("status") != "PASS"):
        raise em.Refusal("BACKEND_STEP37_STREAMOUT_ENGINE_MISMATCH", repr(dict(receipt)))
    return "PASS"


def _step37_validator(route: str):
    def validator(outputs: Path, binding: Mapping[str, object]) -> em.Evidence:
        evidence = validate(outputs, binding)
        if evidence.verdict != "PASS":
            return evidence
        receipt_path = Path(outputs) / "reports/phase3/step37_streamout/vibeic_receipt.json"
        try:
            receipt = json.loads(receipt_path.read_text())
            validate_streamout_receipt(receipt, route=route)
        except (OSError, ValueError, em.Refusal) as exc:
            return em.Evidence(binding, "NOT_MEASURED", evidence.gates, evidence.outputs,
                               detail=str(exc))
        return evidence
    return validator


def _adapter(spec: Mapping[str, object], source_sha: str, objective: Mapping[str, object], *,
             path: str, available: bool) -> em.Adapter:
    applicability = spec["applicability"].get(path, "inapplicable: path not declared")
    applicable = applicability == "applicable"
    applicability_kind = ("applicable" if applicable else
                          "inapplicable" if applicability.startswith("inapplicable") else "unknown")
    producer = _site_path(str(spec["producer_sites"][0]))
    source_files = _source_files(spec)
    gates = tuple(spec["consumer_gates"])
    output_contract = {name: (name,) for name in spec["canonical_outputs"]}
    validator = _step37_validator("librelane") if spec["step_id"] == "37" else validate
    return em.Adapter(
        arm_id=str(spec["arm_id"]), tool_id=str(spec["tool_id"]), step_id=str(spec["step_id"]),
        source_sha=source_sha, source_files=source_files,
        tool_version="source-bound; native qualification NOT_MEASURED",
        engine_families=tuple(spec["engine_families"]),
        components=(em.Component("producer", (str(Path(sys.executable).resolve()), str(producer), "{inputs}", "{outputs}"), 30),),
        validate=validator, required_outputs=tuple(spec["canonical_outputs"]),
        objective=dict(objective), applicability=applicability_kind,
        applicability_reason="" if applicable else str(applicability), role="producer",
        qualified=True, qualification_evidence=(
            "source receipt contract only; native execution NOT_MEASURED; producer site=" + str(producer)),
        available=available, availability_reason="" if available else "NATIVE_EXECUTION_NOT_MEASURED",
        cpus=1, ram_mb=256, output_contract=output_contract,
        own_no_tool_reason="One complete producer owns all row outputs and gates; checker components are complementary.",
    )


def register_backend_adapters(registry: em.Registry | None = None, *, source_sha: str,
                              objective: Mapping[str, object] | None = None,
                              path: str = "IC", available: bool = False,
                              route_receipt: Mapping[str, object] | None = None,
                              declaration: Mapping[str, object] | None = None) -> em.Registry:
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise em.Refusal("INVALID_SOURCE_SHA", source_sha)
    registry = registry or em.Registry()
    objective = objective or {"metric": "canonical_evidence", "direction": "max"}
    rows = {row["step_id"]: row for row in coverage_rows()}
    for step_id in BACKEND_IDS:
        spec = dict(rows[step_id])
        # Conditional rows are resolved from the exact current route inputs;
        # no declaration means UNKNOWN and remains visible to the controller.
        if step_id in {"15.5ic", "26.5ic"}:
            from execution_provider_catalog import _applicability
            spec["applicability"] = _applicability(step_id, release=False,
                                                     route_receipt=dict(route_receipt or {}),
                                                     declaration=dict(declaration or {}))
        if spec["applicability"].get(path, "").startswith("inapplicable"):
            continue
        registry.register(_adapter(spec, source_sha, objective, path=path, available=available))
    return registry


def build_registry(**kwargs) -> em.Registry:
    return register_backend_adapters(**kwargs)


__all__ = ["ROWS", "BACKEND_IDS", "register_backend_adapters", "build_registry",
           "step30_instrument_plan", "_step30_simulators", "_step37_route_specs",
           "validate_streamout_receipt"]
