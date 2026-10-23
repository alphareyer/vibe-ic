"""Source-owned 109-flow selection and existing Default dispatch bindings.

Selection is not qualification or IC acceptance.  Unimplemented rows remain
visible, and bounded native execution retains the old receipts unchanged.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Mapping

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
REGISTRY = HERE.parent / "flow" / "flow109.json"
CONTEXT_ENV = "VIBEIC_FLOW109_CONTEXT"


class Flow109Error(ValueError):
    def __init__(self, code: str, detail: str):
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}")


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def predicate(expression: str, features: Mapping[str, bool]) -> bool:
    """Evaluate the published Boolean grammar; no Python eval or false default."""
    try:
        node = ast.parse(expression, mode="eval").body
    except (SyntaxError, TypeError) as exc:
        raise Flow109Error("FLOW109_PREDICATE_INVALID", str(expression)) from exc

    def visit(item):
        if isinstance(item, ast.Name):
            if item.id not in features or type(features[item.id]) is not bool:
                raise Flow109Error("FLOW109_FEATURE_UNRESOLVED", item.id)
            return features[item.id]
        if isinstance(item, ast.Constant) and type(item.value) is bool:
            return item.value
        if isinstance(item, ast.UnaryOp) and isinstance(item.op, ast.Not):
            return not visit(item.operand)
        if isinstance(item, ast.BoolOp) and isinstance(item.op, (ast.And, ast.Or)):
            # Visit all names, including ones hidden behind a false prefix.
            values = [visit(value) for value in item.values]
            return all(values) if isinstance(item.op, ast.And) else any(values)
        raise Flow109Error("FLOW109_PREDICATE_INVALID", expression)

    return visit(node)


def load_registry() -> dict:
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    if data.get("schema") != "vibeic.flow109.registry.v1":
        raise Flow109Error("FLOW109_REGISTRY_INVALID", "unsupported schema")
    rows = data["rows"]
    if (len({row["id"] for row in rows}) != len(rows)
            or len({row["key"] for row in rows}) != len(rows)
            or [row["position"] for row in rows] != list(range(1, len(rows) + 1))):
        raise Flow109Error("FLOW109_REGISTRY_INVALID", "duplicate IDs or reordered positions")
    native = HERE.parent / data["legacy_flow_path"]
    if _file_digest(native) != data["legacy_flow_sha256"]:
        raise Flow109Error("FLOW109_LEGACY_MAP_STALE", "canonical source changed; review the mapping")
    return data


def resolve_features(supplied: Mapping[str, bool], registry: dict) -> dict:
    if not isinstance(supplied, Mapping):
        raise Flow109Error("FLOW109_FEATURES_INVALID", "features must be an object")
    names = set(registry["predicate_names"])
    unknown = sorted(set(supplied) - names)
    invalid = sorted(str(k) for k, value in supplied.items() if type(value) is not bool)
    if unknown or invalid:
        raise Flow109Error("FLOW109_FEATURES_INVALID", f"unknown={unknown}, non_boolean={invalid}")
    definitions = registry["predicate_definitions"]
    missing = sorted(names - set(definitions) - set(supplied) - {"always"})
    if missing:
        raise Flow109Error("FLOW109_FEATURE_UNRESOLVED", ", ".join(missing))
    result = dict(supplied)
    if result.get("always", True) is not True:
        raise Flow109Error("FLOW109_FEATURES_INVALID", "always must be true")
    result["always"] = True
    pending = dict(definitions)
    while pending:
        progressed = False
        for name, expression in list(pending.items()):
            try:
                # Derived names supplied by a caller do not override definitions.
                available = {k: v for k, v in result.items() if k not in pending}
                expected = predicate(expression, available)
            except Flow109Error as exc:
                if exc.code == "FLOW109_FEATURE_UNRESOLVED":
                    continue
                raise
            if name in supplied and supplied[name] != expected:
                raise Flow109Error("FLOW109_FEATURE_CONFLICT", name)
            result[name] = expected
            del pending[name]
            progressed = True
        if not progressed:
            raise Flow109Error("FLOW109_DEFINITION_CYCLE", ", ".join(pending))
    endpoints = [name for name in registry["endpoints"] if result[name]]
    if len(endpoints) != 1:
        raise Flow109Error("FLOW109_ENDPOINT_CONFLICT", str(endpoints))
    for antecedent, consequences in registry["feature_implications"].items():
        absent = [name for name in consequences if result[antecedent] and not result[name]]
        if absent:
            raise Flow109Error("FLOW109_FEATURE_CONFLICT", f"{antecedent} requires {absent}")
    for group in registry["feature_exclusions"]:
        if all(result[name] for name in group):
            raise Flow109Error("FLOW109_FEATURE_CONFLICT", "mutually exclusive: " + ", ".join(group))
    return result


def _selected_profile(row: dict, features: dict, override: str | None) -> dict:
    choices = [p for p in row["profiles"] if p["select_when"] != "otherwise"
               and predicate(p["select_when"], features)]
    if not choices:
        choices = [p for p in row["profiles"] if p["select_when"] == "otherwise"]
    if len(choices) != 1:
        raise Flow109Error("FLOW109_PROFILE_AMBIGUOUS", row["id"])
    choice = choices[0]
    if override is not None and override != choice["id"]:
        raise Flow109Error("FLOW109_PROFILE_INAPPLICABLE", f"{row['id']}.{override}")
    return copy.deepcopy(choice)


def _tool_uses(row: dict, profile: dict) -> list[dict]:
    """Use identities are source-owned purposes, not caller-selected tool names."""
    dispatch = profile.get("dispatch")
    if dispatch is None:
        return []
    return [{"use_id": f"flow109.{row['id']}.{profile['id']}.{tool}",
             "tool_id": tool, "purpose": f"{row['id']}.{profile['id']}",
             "native_step_id": dispatch["native_step_id"],
             "native_steps": dispatch.get("engine_steps", {}).get(tool, profile.get("native_steps", [])),
             "executable_name": dispatch.get("engine_executables", {}).get(tool, tool),
             "qualification_required": True}
            for tool in dispatch["engine_families"]]


def resolve_plan(features: Mapping[str, bool], step_profiles: Mapping[str, str] | None = None) -> dict:
    registry = load_registry()
    selected_features = resolve_features(features, registry)
    if step_profiles is not None and (not isinstance(step_profiles, Mapping)
                                     or any(not isinstance(v, str) for v in step_profiles.values())):
        raise Flow109Error("FLOW109_PROFILE_INVALID", "step_profiles must map stable IDs to profile names")
    overrides = dict(step_profiles or {})
    known = {r["id"] for r in registry["rows"]}
    if set(overrides) - known:
        raise Flow109Error("FLOW109_STEP_UNKNOWN", str(sorted(set(overrides) - known)))
    rows = []
    for spec in registry["rows"]:
        if not predicate(spec["when"], selected_features):
            if spec["id"] in overrides:
                raise Flow109Error("FLOW109_STEP_NOT_SELECTED", spec["id"])
            continue
        row = copy.deepcopy(spec)
        row["profile"] = _selected_profile(row, selected_features, overrides.get(row["id"]))
        row["selected_profile"] = row["profile"]["id"]
        row["tool_uses"] = _tool_uses(row, row["profile"])
        rows.append(row)
    selected = {r["id"] for r in rows}
    for row in rows:
        for dep in row["data_deps"]:
            required = (dep["step"] in selected if dep["when"] == "producer_selected"
                        else predicate(dep["when"], selected_features))
            if required and dep["step"] not in selected:
                raise Flow109Error("FLOW109_DEPENDENCY_NOT_SELECTED", f"{row['id']} <- {dep['step']}")
    return {"schema": "vibeic.flow109.plan.v1", "flow_id": "109",
            "status": "PLAN_RESOLVED_NOT_EXECUTED", "revision": registry["revision"],
            "registry_sha256": _file_digest(REGISTRY),
            "planner_sha256": _file_digest(Path(__file__)),
            "legacy_source_commit": registry["legacy_source_commit"],
            "legacy_flow_sha256": registry["legacy_flow_sha256"],
            "features": selected_features, "step_profiles": overrides,
            "phases": registry["phases"], "stages": registry["stages"],
            "gate_layers": registry["gate_layers"], "namespaces": registry["namespaces"],
            "rows": rows, "selected_count": len(rows),
            "runtime_verdict": "NOT_MEASURED", "flow109_acceptance": "NOT_MEASURED"}


def resolve_step(plan: Mapping, step_id: str) -> dict:
    row = next((r for r in plan["rows"] if r["id"] == step_id), None)
    if row is None:
        raise Flow109Error("FLOW109_STEP_NOT_SELECTED", str(step_id))
    return copy.deepcopy(row)


def resolve_execution_window(plan: Mapping, entry: str | None, exit_: str | None) -> dict:
    """Bind one existing native component without skipping new responsibilities.

    Multi-row migration requires actual new acceptance adapters. Translating
    the first/last IDs into an old span would silently execute a different DAG.
    """
    if not entry or exit_ != entry:
        raise Flow109Error("FLOW109_WINDOW_UNIMPLEMENTED",
                           "this tranche supports one explicit entry/exit component; full 109 acceptance is unimplemented")
    row = resolve_step(plan, entry)
    dispatch = row["profile"].get("dispatch")
    if not dispatch:
        raise Flow109Error("FLOW109_PRODUCER_UNIMPLEMENTED", f"{entry}.{row['selected_profile']}")
    return {"requested_step": entry, "resolved_row": row,
            "native_step_id": dispatch["native_step_id"]}


_CONTEXT_KEYS = {"schema", "registry_sha256", "planner_sha256", "legacy_flow_sha256",
                 "features", "step_profiles", "requested_step", "resolved_row",
                 "native_step_id", "runtime_tool_uses", "qualification_registry", "context_sha256"}


def execution_context(plan: dict, entry: str, exit_: str, *,
                      runtime_tool_uses=None, qualification_registry=None) -> dict:
    window = resolve_execution_window(plan, entry, exit_)
    context = {"schema": "vibeic.flow109.execution-context.v1",
               **{key: plan[key] for key in ("registry_sha256", "planner_sha256", "legacy_flow_sha256",
                                             "features", "step_profiles")},
               **window, "runtime_tool_uses": copy.deepcopy(runtime_tool_uses),
               "qualification_registry": qualification_registry}
    context["context_sha256"] = digest(context)
    return context


def validate_execution_context(context: Mapping) -> dict:
    if not isinstance(context, Mapping) or set(context) != _CONTEXT_KEYS:
        raise Flow109Error("FLOW109_CONTEXT_INVALID", "unexpected or missing fields")
    if context["schema"] != "vibeic.flow109.execution-context.v1":
        raise Flow109Error("FLOW109_CONTEXT_INVALID", "schema")
    payload = {key: value for key, value in context.items() if key != "context_sha256"}
    if context["context_sha256"] != digest(payload):
        raise Flow109Error("FLOW109_CONTEXT_STALE", "request identity changed")
    current = resolve_plan(context["features"], context["step_profiles"])
    expected = execution_context(current, context["requested_step"], context["requested_step"],
                                 runtime_tool_uses=context["runtime_tool_uses"],
                                 qualification_registry=context["qualification_registry"])
    if dict(context) != expected:
        raise Flow109Error("FLOW109_CONTEXT_STALE", "source registry, producer or selection changed")
    return expected


def prepare_front_door(args, project: Path) -> int | None:
    """Return an exit code for planning/refusal, or continue the real front door."""
    if args.flow != "109":
        if args.flow109_manifest or args.flow109_plan_only:
            raise Flow109Error("FLOW109_SELECTION_REQUIRED", "use --flow 109")
        # A stale parent selection cannot turn a later 70-flow launch into 109.
        os.environ.pop(CONTEXT_ENV, None)
        return None
    # The existing policy parser uses None for an omitted mode and resolves
    # it to Default. Preserve that CLI contract rather than demanding a flag.
    if args.execution_mode not in (None, "default"):
        raise Flow109Error("FLOW109_MODE_UNIMPLEMENTED", "109 currently selects Default only")
    if not args.flow109_manifest:
        raise Flow109Error("FLOW109_MANIFEST_REQUIRED", "supply explicit resolved features")
    try:
        manifest = json.loads(Path(args.flow109_manifest).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Flow109Error("FLOW109_MANIFEST_INVALID", str(exc)) from exc
    if not isinstance(manifest, dict):
        raise Flow109Error("FLOW109_MANIFEST_INVALID", "expected object")
    if "tool_uses" in manifest and "runtime_tool_uses" in manifest:
        raise Flow109Error("FLOW109_MANIFEST_INVALID", "duplicate runtime tool identity fields")
    plan = resolve_plan(manifest.get("features"), manifest.get("step_profiles"))
    from _delivery_route import report_label
    route = report_label(project).get("delivery_route")
    expected = "IC" if plan["features"]["ic"] else "IP"
    if route != expected:
        raise Flow109Error("FLOW109_ROUTE_CONFLICT", f"manifest endpoint requires {expected}, owner declared {route}")
    from _atomic_artefact import write_json
    destination = project / "reports" / "flow109"
    write_json(destination / "plan.json", plan)
    print(f"FLOW109 PLAN: {plan['selected_count']} selected; runtime NOT_MEASURED")
    if args.flow109_plan_only:
        return 0
    context = execution_context(plan, args.entry_step, args.exit_step,
                                runtime_tool_uses=manifest.get("runtime_tool_uses", manifest.get("tool_uses")),
                                qualification_registry=manifest.get("qualification_registry"))
    # Admission is intentionally immediately before actual existing dispatch.
    # There is no marker process or substitute runner behind this branch.
    from flow109_admission import require_admission
    admission = require_admission(project, context)
    write_json(destination / "admission.json", admission)
    if not isinstance(admission, dict) or admission.get("status") != "PASS":
        raise Flow109Error("FLOW109_ADMISSION_REFUSED", "qualification did not return PASS")
    path = destination / "requests" / (context["context_sha256"] + ".json")
    write_json(path, context)
    os.environ[CONTEXT_ENV] = str(path.resolve())
    args.entry_step = context["native_step_id"]
    args.exit_step = context["native_step_id"]
    return None
