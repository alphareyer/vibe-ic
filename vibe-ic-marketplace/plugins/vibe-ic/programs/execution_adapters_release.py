"""Minimal ULTRACORE seam for the reviewed 14-row release family."""
from __future__ import annotations

import json
import ast
from pathlib import Path
import sys
from typing import Mapping

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import execution_modes as em
from execution_provider_catalog import (RELEASE_IDS, RELEASE_SITES, coverage_rows,
                                        current_source_identity, current_source_tree_identity,
                                        source_closure)
from execution_release_rows import ROWS

HERE = Path(__file__).resolve().parent
PROGRAMS = HERE
EXTERNAL_IDS = ("40", "41", "42", "43", "44")
UNAVAILABLE_IDS = ("39",)
SOFTWARE_IDS = tuple(s for s in RELEASE_IDS
                     if s not in EXTERNAL_IDS and s not in UNAVAILABLE_IDS and RELEASE_SITES[s])
ALL_IDS = RELEASE_IDS
EXECUTION_DEPENDENCIES = (
    "digital_hardmacro_gen.py", "phase3_one_shot_runner.py",
    "ip_release_docs_gen.py", "digital_hardmacro_check.py", "release_docs_check.py",
    "_release_docs_build.py", "_release_docs_contract.py",
    "tapeout_checklist_gen.py", "foundry_handoff_pack_gen.py",
)

RELEASE_PARAMETER_DEFAULTS = {
    "pdk_name": None, "pdk_root": None, "design_name": None,
    "source_sha": None, "module_role": None, "container": "",
    "cell_lef": "", "metal_prefix": "met", "full_lef": False,
    "pinonly": False,
    "source_tree_sha": None,
}


def _site_path(site: str) -> Path:
    module, _, symbol = site.partition(":")
    path = HERE / module
    if not module or not symbol or not path.is_file():
        raise em.Refusal("RELEASE_PRODUCER_SITE_MISSING", site)
    try:
        tree = ast.parse(path.read_text())
    except (OSError, SyntaxError) as exc:
        raise em.Refusal("RELEASE_PRODUCER_SITE_UNREADABLE", site) from exc
    symbols = {node.name for node in ast.walk(tree)
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    if symbol not in symbols:
        raise em.Refusal("RELEASE_PRODUCER_SYMBOL_MISSING", site)
    return path


def _source_files(spec: Mapping[str, object]) -> dict[str, str]:
    paths = {Path(__file__), HERE / "execution_modes.py", HERE / "execution_provider_catalog.py",
             HERE / "execution_release_rows.py", HERE / "execution_release_worker.py",
             HERE / "execution_backend_worker.py", HERE / "execution_backend_producers.py",
             HERE / "execution_backend_gates.py", HERE / "execution_backend_snapshot.py"}
    paths.update(HERE / name for name in EXECUTION_DEPENDENCIES)
    paths.update(_site_path(site) for site in spec["producer_sites"])
    for gate in spec["consumer_gates"]:
        candidate = HERE / (str(gate) + ".py")
        if candidate.is_file():
            paths.add(candidate)
    paths.update({HERE / "data/execution_modes_portfolio.json",
                  HERE.parent / "flow/phase1_phase2_phase3.yaml",
                  HERE.parent / "benchmark/CAPTURE_ROUTING.json"})
    paths.update(HERE.glob("_atomic*.py"))
    paths = source_closure({p.resolve() for p in paths
                            if p.is_file() and not p.is_symlink()})
    paths.add(Path(sys.executable).resolve())
    return {str(p): em.digest(p) for p in sorted(paths)}


def validate(outputs: Path, binding: Mapping[str, object]) -> em.Evidence:
    path = Path(outputs) / "release-evidence.json"
    if not path.is_file() or path.is_symlink():
        return em.Evidence(binding, "NOT_MEASURED", {}, {},
                           detail="NOT_MEASURED: no current release producer receipt")
    try:
        report = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return em.Evidence(binding, "NOT_MEASURED", {}, {}, detail=str(exc))
    measured_fail = (report.get("producer_verdict") == "FAIL" or
                     report.get("design_verdict") == "FAIL" or
                     report.get("qualification") == "FAIL" or any(
                         row.get("verdict") == "FAIL"
                         for row in report.get("gate_records", ())
                         if isinstance(row, dict) and row.get("blocking", True)))
    # Requirements come from the issued context, never a mutable report label.
    native_required = binding.get("step_id") == "37.5ip"
    native_verdicts = []
    if native_required or report.get("native_receipts"):
        native_receipts = report.get("native_receipts") or []
        if not isinstance(native_receipts, list):
            native_receipts = [None]
        for native in native_receipts:
            try:
                value = validate_hardmacro_receipt(native)
                native_verdicts.append(value if value in ("PASS", "FAIL") else "NOT_MEASURED")
            except (em.Refusal, AttributeError, TypeError, ValueError):
                native_verdicts.append("NOT_MEASURED")
        measured_fail = measured_fail or "FAIL" in native_verdicts
    if report.get("binding") != dict(binding):
        return em.Evidence(binding, "FAIL" if measured_fail else "NOT_MEASURED", {}, {},
                           detail="RELEASE_RESULT_UNBOUND")
    if report.get("step_id") != binding.get("step_id"):
        return em.Evidence(binding, "FAIL" if measured_fail else "NOT_MEASURED", {}, {},
                           detail="RELEASE_STEP_IDENTITY_MISMATCH")
    if binding.get("step_id") == "39" and report.get("excluded_from_verdict") is not True:
        return em.Evidence(binding, "NOT_MEASURED", {}, {},
                           detail="STEP39_EXCLUSION_DECLARATION_MISSING")
    gates: dict[str, str] = {}
    for row in report.get("gate_records", ()):
        if not isinstance(row, dict) or not row.get("gate") or not row.get("blocking", True):
            continue
        name, verdict = str(row["gate"]), str(row.get("verdict", "NOT_MEASURED"))
        previous = gates.get(name)
        # Duplicate canonical invocations aggregate fail-closed instead of
        # letting the last invocation hide an earlier failure.
        if previous == "FAIL" or verdict == "FAIL":
            gates[name] = "FAIL"
        elif previous == "NOT_MEASURED" or verdict == "NOT_MEASURED":
            gates[name] = "NOT_MEASURED"
        else:
            gates[name] = "PASS"
    outputs_hashes = {"release-evidence.json": em.digest(path)}
    for name, expected in (report.get("outputs") or {}).items():
        file = Path(outputs) / name
        if file.is_file() and not file.is_symlink() and em.digest(file) == expected:
            outputs_hashes[name] = expected
        elif measured_fail:
            return em.Evidence(binding, "FAIL", gates, outputs_hashes,
                               detail="measured FAIL with changed output: " + str(name))
        else:
            return em.Evidence(binding, "NOT_MEASURED", gates, outputs_hashes,
                               detail="RELEASE_OUTPUT_UNMEASURED: " + str(name))
    verdict = "FAIL" if measured_fail else (
        "PASS" if report.get("qualification") == "PASS" and not report.get("missing_outputs") and
        (not native_required or native_verdicts and all(v == "PASS" for v in native_verdicts))
        else "NOT_MEASURED")
    return em.Evidence(binding, verdict, gates, outputs_hashes,
                       detail=str(report.get("reason", "")))


def validate_hardmacro_receipt(receipt: Mapping[str, object], *, route: str = "native") -> str:
    """Require the generator's typed Magic/OpenSTA facts; never relabel rc0."""
    if receipt.get("status") == "FAIL":
        return "FAIL"
    if route == "direct_magic":
        raise em.Refusal("RELEASE_DIRECT_MAGIC_FALLBACK_REFUSED",
                         "direct Magic is not an independent hardmacro provider")
    processes = receipt.get("processes") or []
    binaries = {Path(str(row.get("binary", row.get("tool", "")))).name
                for row in processes if isinstance(row, dict)}
    typed = all(isinstance(row, dict) and row.get("rc") == 0 and
                isinstance(row.get("version"), str) and row.get("version") and
                isinstance(row.get("argv"), (list, tuple)) and row.get("argv") and
                isinstance(row.get("output_sha256"), str) and
                len(row.get("output_sha256")) == 64
                for row in processes)
    has_sta = "sta" in binaries or "opensta" in binaries
    if (receipt.get("status") != "PRODUCED" or
            "magic" not in binaries or not has_sta or not typed):
        raise em.Refusal("RELEASE_NATIVE_ENGINE_RECEIPT_UNMEASURED", repr(dict(receipt)))
    return "PASS"


def _adapter(spec: Mapping[str, object], source_sha: str, objective: Mapping[str, object], *,
             path: str, available: bool, route_receipt: Mapping[str, object] | None,
             declaration: Mapping[str, object] | None,
             parameters: Mapping[str, object] | None = None,
             native_qualified: bool = False) -> em.Adapter:
    source_sha = current_source_identity()
    from execution_provider_catalog import _applicability
    step_id = str(spec["step_id"])
    app = _applicability(step_id, release=True,
                         route_receipt=dict(route_receipt or {}), declaration=dict(declaration or {})).get(path, "unknown")
    applicability = ("applicable" if app == "applicable" or app.startswith("applicable:") else
                     "inapplicable" if app.startswith("inapplicable") else "unknown")
    worker = HERE / "execution_release_worker.py"
    source_files = _source_files(spec)
    contract = {name: ("release-evidence.json",) for name in spec["canonical_outputs"]}
    params = dict(RELEASE_PARAMETER_DEFAULTS)
    if parameters:
        for key, value in parameters.items():
            if key not in params:
                raise em.Refusal("RELEASE_PARAMETER_UNDECLARED", str(key))
            if isinstance(value, Path):
                value = str(value)
            params[key] = value
    # source identity is controller-derived and cannot be overridden through
    # the release argv parameter map.
    params["source_sha"] = source_sha
    params["source_tree_sha"] = current_source_tree_identity()
    params.update({"route": path, "row": step_id, "runtime_status": "NOT_MEASURED",
                   "external_handoff": step_id in EXTERNAL_IDS,
                   "input_contract": ROWS[step_id].get("canonical", {}).get("required_inputs")})
    external = step_id in EXTERNAL_IDS
    unavailable = step_id in UNAVAILABLE_IDS
    role = "complementary" if external else "producer"
    reason = ("EXTERNAL_HANDOFF_ONLY" if external else
              "FPGA_HARDWARE_ABSENT_NOT_MEASURED" if unavailable else
              "" if available else "NATIVE_EXECUTION_NOT_MEASURED")
    return em.Adapter(
        arm_id=str(spec["arm_id"]), tool_id=str(spec["tool_id"]), step_id=str(spec["step_id"]),
        source_sha=source_sha, source_files=source_files,
        tool_version="source-bound; native qualification NOT_MEASURED",
        engine_families=tuple(spec["engine_families"]),
        components=(em.Component("release-producer", (str(Path(sys.executable).resolve()), str(worker),
                    "{inputs}", "{outputs}", "--step-id", str(spec["step_id"]),
                    "--params-json", json.dumps(params, sort_keys=True)), 60),),
        validate=validate, required_outputs=("release-evidence.json",), output_contract=contract,
        objective=dict(objective), applicability=applicability,
        applicability_reason="" if applicability == "applicable" else str(app),
        qualified=native_qualified, qualification_evidence=(
                                                 "current-tree native qualification receipt bound" if native_qualified else
                                                 "source-bound component; producer sites=" + ",".join(spec["producer_sites"]) +
                                                 "; native qualification NOT_MEASURED" if not external else
                                                 "external handoff classification only; no software producer"),
        available=available and not unavailable and not external, availability_reason=reason,
        cpus=1, ram_mb=256,
        role=role,
        own_no_tool_reason="One complete release producer owns the row; physical/external evidence remains outside software.",
    )


def register_release_adapters(registry: em.Registry | None = None, *, source_sha: str,
                              objective: Mapping[str, object] | None = None,
                              path: str = "IC", available: bool = False,
                              route_receipt: Mapping[str, object] | None = None,
                              declaration: Mapping[str, object] | None = None,
                              parameters: Mapping[str, object] | None = None) -> em.Registry:
    source_sha = current_source_identity()
    registry = registry or em.Registry()
    objective = objective or {"metric": "canonical_evidence", "direction": "max"}
    rows = {row["step_id"]: row for row in coverage_rows()}
    for step_id in ALL_IDS:
        registry.register(_adapter(rows[step_id], source_sha, objective, path=path,
                                   available=available, route_receipt=route_receipt,
                                   declaration=declaration, parameters=parameters))
    return registry


def build_registry(**kwargs) -> em.Registry:
    return register_release_adapters(**kwargs)


__all__ = ["ROWS", "RELEASE_IDS", "SOFTWARE_IDS", "UNAVAILABLE_IDS", "EXTERNAL_IDS", "ALL_IDS",
           "register_release_adapters", "build_registry", "validate_hardmacro_receipt"]
