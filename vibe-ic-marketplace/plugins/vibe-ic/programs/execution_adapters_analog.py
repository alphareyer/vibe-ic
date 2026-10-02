"""Source-bound analog provider catalog for A1-A9 and M1-M4.

This module is intentionally a small adapter over the public
``execution_modes.Registry``. It describes canonical inputs, outputs, real
producer entrypoints, engine families, gates, and applicability. Registration
does not qualify a native run and does not execute EDA. A1-A9 and reachable M1
are registered as source-only adapters whose Controller admission remains
``NOT_MEASURED``. M2-M4 stay visible in the catalog but are not registered
because their canonical producer contracts are incomplete.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Callable, Mapping

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import execution_modes as em
from _analog_a_check_common import MAX_DEGRADATION_PCT


PROGRAMS = Path(__file__).resolve().parent
COVERAGE_FILE = PROGRAMS / "data" / "execution_analog_coverage.json"
PORTFOLIO_FILE = PROGRAMS / "data" / "execution_modes_portfolio.json"
WORKER = PROGRAMS / "execution_analog_worker.py"
FLOW_FILE = PROGRAMS.parent / "flow" / "phase1_phase2_phase3.yaml"
ROWS = tuple(json.loads(COVERAGE_FILE.read_text(encoding="utf-8"))["rows"])
STEP_IDS = tuple(row["step_id"] for row in ROWS)
ANALOG_STEPS = STEP_IDS[:9]
MIXED_SIGNAL_STEPS = STEP_IDS[9:]
IMPLEMENTED_STEPS = tuple(row["step_id"] for row in ROWS if row["dispatch"])
if STEP_IDS != tuple(f"A{i}" for i in range(1, 10)) + tuple(f"M{i}" for i in range(1, 5)):
    raise RuntimeError("execution analog coverage must contain the canonical A1-A9/M1-M4 rows")


@dataclass(frozen=True)
class ProviderResult:
    step_id: str
    status: str
    reason: str
    outputs: tuple[str, ...] = ()

    @property
    def state(self) -> str:
        """Compatibility spelling used by the other source-only catalogs."""
        return self.status


@dataclass(frozen=True)
class AnalogProvider:
    step_id: str
    producer_entrypoints: tuple[str, ...]
    canonical_inputs: tuple[str, ...]
    canonical_outputs: tuple[str, ...]
    source_files: tuple[str, ...]
    engine_family: tuple[str, ...]
    mandatory_gates: tuple[str, ...]
    applicability: str
    dispatch: bool
    certification: str
    known_gap: str = ""

    @property
    def inputs(self) -> tuple[str, ...]:
        return self.canonical_inputs

    @property
    def outputs(self) -> tuple[str, ...]:
        return self.canonical_outputs

    @property
    def engines(self) -> tuple[str, ...]:
        return self.engine_family

    @property
    def source_family(self) -> str:
        return "analog-programs" if self.step_id.startswith("A") else "mixed-signal-programs"

    @property
    def factory(self) -> Callable[..., ProviderResult]:
        return lambda project, **kwargs: produce(self.step_id, project, **kwargs)


def _row(step_id: str) -> dict:
    for row in ROWS:
        if row["step_id"] == step_id:
            return row
    raise KeyError(step_id)


def _provider(row: dict) -> AnalogProvider:
    return AnalogProvider(
        step_id=row["step_id"],
        producer_entrypoints=tuple(row["producer_entrypoints"]),
        canonical_inputs=tuple(row["canonical_inputs"]),
        canonical_outputs=tuple(row["canonical_outputs"]),
        source_files=tuple(row["source_files"]),
        engine_family=tuple(row["engine_family"]),
        mandatory_gates=tuple(row["mandatory_gates"]),
        applicability=row["applicability"],
        dispatch=bool(row["dispatch"]),
        certification=row["certification"],
        known_gap=row.get("known_gap", ""),
    )


PROVIDERS = {row["step_id"]: _provider(row) for row in ROWS}
PRODUCERS = {step_id: provider.producer_entrypoints for step_id, provider in PROVIDERS.items()}
ENGINE_FAMILIES = {step_id: provider.engine_family for step_id, provider in PROVIDERS.items()}


def _repo_source_sha() -> str:
    """Return the issued commit only when the source tree is clean."""
    source_files = {str(Path(__file__).resolve()): em.digest(Path(__file__).resolve())}
    commit, _ = em._verified_git_identity(source_files)
    return commit


def _source_identity() -> tuple[str, str]:
    """Return the clean commit/tree identity used for provider issuance."""
    source_files = {str(Path(__file__).resolve()): em.digest(Path(__file__).resolve())}
    return em._verified_git_identity(source_files)


def _head_identity() -> tuple[str, str]:
    """Read the declared HEAD/tree for validation diagnostics only.

    Qualification uses ``_source_identity`` and therefore refuses a dirty
    tree.  Validation may still inspect a dirty tree so it can name the
    authority blob that diverged instead of silently treating it as current.
    """
    try:
        commit = subprocess.check_output(
            ["git", "-C", str(PROGRAMS), "rev-parse", "HEAD"],
            text=True, stderr=subprocess.DEVNULL).strip()
        tree = subprocess.check_output(
            ["git", "-C", str(PROGRAMS), "rev-parse", "HEAD^{tree}"],
            text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise em.Refusal("ANALOG_PROVIDER_SOURCE_UNAVAILABLE", str(PROGRAMS)) from exc
    if len(commit) != 40 or len(tree) != 40:
        raise em.Refusal("ANALOG_PROVIDER_SOURCE_IDENTITY_INVALID", str(PROGRAMS))
    return commit, tree


def _repo_root() -> Path:
    try:
        value = subprocess.check_output(
            ["git", "-C", str(PROGRAMS), "rev-parse", "--show-toplevel"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise em.Refusal("ANALOG_PROVIDER_SOURCE_UNAVAILABLE", str(PROGRAMS)) from exc
    root = Path(value).resolve()
    if not root.is_dir():
        raise em.Refusal("ANALOG_PROVIDER_SOURCE_UNAVAILABLE", str(root))
    return root


def _git_blob(commit: str, path: Path) -> bytes:
    root = _repo_root()
    try:
        relative = path.resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise em.Refusal("ANALOG_PROVIDER_SOURCE_OUTSIDE_REPO", str(path)) from exc
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "show", f"{commit}:{relative}"],
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise em.Refusal("ANALOG_PROVIDER_AUTHORITY_MISSING", relative) from exc


def _authority_documents(commit: str) -> tuple[dict, dict, dict]:
    """Load coverage, flow and portfolio from the declared Git commit."""
    try:
        coverage = json.loads(_git_blob(commit, COVERAGE_FILE).decode("utf-8"))
        import yaml
        flow = yaml.safe_load(_git_blob(commit, FLOW_FILE).decode("utf-8"))
        portfolio = json.loads(_git_blob(commit, PORTFOLIO_FILE).decode("utf-8"))
    except (ImportError, UnicodeDecodeError, ValueError, TypeError) as exc:
        raise em.Refusal("ANALOG_PROVIDER_AUTHORITY_INVALID", str(exc)) from exc
    if not isinstance(coverage, Mapping) or not isinstance(flow, Mapping) or not isinstance(portfolio, Mapping):
        raise em.Refusal("ANALOG_PROVIDER_AUTHORITY_INVALID", "top-level authority is not a mapping")
    return dict(coverage), dict(flow), dict(portfolio)


def _authority_source_errors(commit: str) -> list[str]:
    """Reject any working-tree authority bytes outside the issued Git blobs."""
    errors: list[str] = []
    for label, path in (("COVERAGE", COVERAGE_FILE), ("FLOW", FLOW_FILE),
                        ("PORTFOLIO", PORTFOLIO_FILE)):
        try:
            expected = _git_blob(commit, path)
            actual = path.read_bytes()
        except OSError:
            errors.append(label + "_AUTHORITY_UNREADABLE")
            continue
        if actual != expected:
            errors.append(label + "_AUTHORITY_BLOB_MISMATCH")
    return errors


def _source_path(name: str) -> Path:
    path = PROGRAMS / name
    if not path.is_file() or path.is_symlink():
        raise em.Refusal("ANALOG_PROVIDER_SOURCE_MISSING", str(path))
    return path


def _local_import_path(module: str, current: Path, level: int = 0) -> Path | None:
    """Resolve a statically named import to a local ``programs`` module."""
    if level:
        try:
            relative = current.resolve().relative_to(PROGRAMS.resolve())
        except ValueError:
            return None
        parts = list(relative.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        package = parts[:-1]
        if level > len(package) + 1:
            return None
        parts = package[:len(package) - (level - 1)] + ([module] if module else [])
    else:
        parts = module.split(".") if module else []
    if not parts or any(part in ("", ".", "..") for part in parts):
        return None
    candidate = PROGRAMS.joinpath(*parts)
    for path in (candidate.with_suffix(".py"), candidate / "__init__.py"):
        if path.is_file() and not path.is_symlink():
            return path.resolve()
    return None


_IMPORT_CACHE: dict[str, tuple[Path, ...]] = {}
_DIRECT_IMPORT_CACHE: dict[str, tuple[Path, ...]] = {}


def _python_import_closure(paths: list[Path]) -> tuple[Path, ...]:
    """Return the complete local Python import closure for a source route."""
    def closure(seed: Path) -> tuple[Path, ...]:
        key = str(seed.resolve())
        cached = _IMPORT_CACHE.get(key)
        if cached is not None:
            return cached
        pending = [seed.resolve()]
        seen: dict[str, Path] = {}
        while pending:
            path = pending.pop()
            key = str(path)
            if key in seen:
                continue
            seen[key] = path
            direct = _DIRECT_IMPORT_CACHE.get(key)
            if direct is None:
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                except (OSError, SyntaxError, UnicodeError):
                    direct = ()
                else:
                    imports: list[tuple[str, int]] = []
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            imports.extend((alias.name, 0) for alias in node.names)
                        elif isinstance(node, ast.ImportFrom):
                            imports.append((node.module or "", node.level))
                    direct = tuple(
                        target for module, level in imports
                        if (target := _local_import_path(module, path, level)) is not None
                    )
                _DIRECT_IMPORT_CACHE[key] = direct
            for target in direct:
                if target is not None and str(target) not in seen:
                    pending.append(target)
        result = tuple(seen.values())
        _IMPORT_CACHE[str(seed.resolve())] = result
        return result

    merged: dict[str, Path] = {}
    for seed in paths:
        if seed.suffix == ".py":
            merged.update({str(path): path for path in closure(seed)})
    return tuple(merged.values())


def _source_manifest(provider: AnalogProvider) -> dict[str, str]:
    paths = [Path(__file__), WORKER, COVERAGE_FILE, FLOW_FILE, PORTFOLIO_FILE]
    paths.extend(_source_path(name) for name in provider.source_files)
    paths.extend(_source_path(gate + ".py") for gate in provider.mandatory_gates)
    paths.append(_source_path("execution_modes.py"))
    non_python = [path for path in paths if path.suffix != ".py"]
    paths = non_python + list(_python_import_closure(paths))
    python = shutil.which("python3") or sys.executable
    paths.append(Path(python).resolve())
    return {str(path.resolve()): em.digest(path.resolve()) for path in dict.fromkeys(paths)}


def _required_outputs(provider: AnalogProvider) -> tuple[str, ...]:
    # The public Controller treats the canonical expression as the contract
    # key. Globs/OR expressions are still source declarations, never evidence.
    return tuple(provider.canonical_outputs)


def _validate(outputs: Path, binding: Mapping[str, object], step_id: str) -> em.Evidence:
    provider = PROVIDERS[step_id]
    result = outputs / "provider_result.json"
    observed = None
    if result.is_file() and not result.is_symlink():
        try:
            observed = json.loads(result.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            observed = None
    status = "NOT_MEASURED"
    reason = "canonical producer and gates were not executed"
    if not provider.dispatch:
        status = "NOT_IMPLEMENTED" if not provider.producer_entrypoints else "CANNOT_CERTIFY"
        reason = provider.known_gap or "no complete canonical producer contract"
    elif observed and observed.get("status") != "NOT_MEASURED":
        status = "NOT_MEASURED"
        reason = "worker output cannot qualify a native analog run"
    return em.Evidence(
        binding=binding,
        verdict=status,
        gates={gate: "NOT_MEASURED" for gate in provider.mandatory_gates},
        outputs={},
        detail=reason,
    )


def coverage() -> dict[str, dict]:
    """Return the ordered, machine-readable 13-row/path catalog."""
    return {
        step_id: {
            "step_id": provider.step_id,
            "canonical_inputs": provider.canonical_inputs,
            "canonical_outputs": provider.canonical_outputs,
            "producer_entrypoints": provider.producer_entrypoints,
            "source_files": provider.source_files,
            "engine_family": provider.engine_family,
            "mandatory_gates": provider.mandatory_gates,
            "applicability": provider.applicability,
            "dispatch": provider.dispatch,
            "certification": provider.certification,
            "known_gap": provider.known_gap,
            "source_family": provider.source_family,
            "condition": {
                "kind": "design_dependent",
                "files_exist": ["phase1/analog/analog_block_list.json"],
            },
            "fallback": _row(step_id).get("fallback"),
            "provider_module": __name__,
        }
        for step_id, provider in PROVIDERS.items()
    }


def _fresh_rows() -> tuple[dict, ...]:
    """Read the immutable source catalog for validation controls.

    ``coverage()`` is a public, mutable mapping.  Validation must compare a
    candidate against the checked-in source contract instead of comparing it
    with another projection of that same candidate.
    """
    commit, _ = _source_identity()
    payload, _, _ = _authority_documents(commit)
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError("analog coverage rows must be a list")
    return tuple(rows)


def _freeze(value: object) -> object:
    """Make JSON-shaped values comparable across list/tuple projections."""
    if isinstance(value, Mapping):
        return tuple(sorted((str(key), _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _yaml_gate_programs(node: object) -> tuple[str, ...]:
    """Extract blocking and advisory gate program names in YAML order."""
    found: list[str] = []
    if isinstance(node, Mapping):
        for key, value in node.items():
            if key in ("program_exit_zero", "advisory_program_exit_zero"):
                command = value.get("command") if isinstance(value, Mapping) else value
                if isinstance(command, str) and command.split():
                    found.append(command.split()[0])
            elif key != "optional_program_exit_zero":
                found.extend(_yaml_gate_programs(value))
    elif isinstance(node, (list, tuple)):
        for value in node:
            found.extend(_yaml_gate_programs(value))
    return tuple(found)


def _yaml_inputs(row: Mapping[str, object]) -> tuple[str, ...]:
    """Project the flow's input declarations into the catalog vocabulary."""
    inputs: list[str] = []
    for item in row.get("required_inputs", ()) or ():
        if not isinstance(item, Mapping):
            continue
        if item.get("from") == "A8" and item.get("outputs") == "all":
            inputs.append("A8.outputs=all")
        elif item.get("from") == "external":
            inputs.append("external: PDK device models")
        elif isinstance(item.get("path"), str):
            inputs.append(item["path"])
    return tuple(inputs)


def _module_symbols(module: str) -> set[str] | None:
    """Return top-level Python symbols without importing a producer/gate."""
    path = PROGRAMS / (module + ".py")
    if not path.is_file() or path.is_symlink():
        return None
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError):
        return None
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def _validate_symbols(step_id: str, row: Mapping[str, object], errors: list[str]) -> None:
    """Require every declared producer and gate to be a real source symbol."""
    for entrypoint in row.get("producer_entrypoints", ()) or ():
        if not isinstance(entrypoint, str) or entrypoint.count(".") != 1:
            errors.append(step_id + ":PRODUCER_SYMBOL_INVALID")
            continue
        module, symbol = entrypoint.split(".")
        symbols = _module_symbols(module)
        if symbols is None or symbol not in symbols:
            errors.append(step_id + ":PRODUCER_SYMBOL_MISSING:" + entrypoint)
    for gate in row.get("mandatory_gates", ()) or ():
        if not isinstance(gate, str):
            errors.append(step_id + ":GATE_SYMBOL_INVALID")
            continue
        symbols = _module_symbols(gate)
        if symbols is None or "main" not in symbols:
            errors.append(step_id + ":GATE_SYMBOL_MISSING:" + gate)


def validate_catalog(catalog: Mapping[str, Mapping[str, object]] | None = None) -> tuple[str, ...]:
    """Return named defects in a candidate against all 13 source contracts.

    The explicit ``None`` check is deliberate: an empty mapping is a supplied
    catalog and must fail rather than silently falling back to ``coverage()``.
    """
    rows = coverage() if catalog is None else dict(catalog)
    errors: list[str] = []
    if catalog is not None and not rows:
        errors.append("CATALOG_EMPTY")
    if tuple(rows) != STEP_IDS:
        errors.append("CANONICAL_ROW_ORDER")

    try:
        commit, tree = _head_identity()
        coverage_authority, flow_authority, portfolio_authority = _authority_documents(commit)
        errors.extend(_authority_source_errors(commit))
        if coverage_authority.keys() != {"schema", "rows"}:
            errors.append("COVERAGE_TOP_LEVEL_SCHEMA_MISMATCH")
        if coverage_authority.get("schema") != "execution_analog_coverage/1":
            errors.append("COVERAGE_SCHEMA_MISMATCH")
        try:
            current_coverage = json.loads(COVERAGE_FILE.read_text(encoding="utf-8"))
            if (set(current_coverage) != set(coverage_authority) or
                    current_coverage.get("schema") != coverage_authority.get("schema")):
                errors.append("COVERAGE_SCHEMA_MISMATCH")
        except (OSError, ValueError, AttributeError):
            errors.append("COVERAGE_TOP_LEVEL_SCHEMA_MISMATCH")
        if flow_authority.keys() != {"version", "flow_name", "total_steps",
                                     "analog_steps", "stages", "steps", "final_gate"}:
            errors.append("FLOW_TOP_LEVEL_SCHEMA_MISMATCH")
        if portfolio_authority.keys() != {"schema_version", "meta", "tools", "steps"}:
            errors.append("PORTFOLIO_TOP_LEVEL_SCHEMA_MISMATCH")
        try:
            import yaml
            current_flow = yaml.safe_load(FLOW_FILE.read_text(encoding="utf-8"))
            if not isinstance(current_flow, Mapping) or set(current_flow) != set(flow_authority):
                errors.append("FLOW_TOP_LEVEL_SCHEMA_MISMATCH")
        except (ImportError, OSError, ValueError, TypeError):
            errors.append("FLOW_TOP_LEVEL_SCHEMA_MISMATCH")
        try:
            current_portfolio = json.loads(PORTFOLIO_FILE.read_text(encoding="utf-8"))
            if (not isinstance(current_portfolio, Mapping) or
                    set(current_portfolio) != set(portfolio_authority)):
                errors.append("PORTFOLIO_TOP_LEVEL_SCHEMA_MISMATCH")
        except (OSError, ValueError, TypeError):
            errors.append("PORTFOLIO_TOP_LEVEL_SCHEMA_MISMATCH")
        expected_rows = {row["step_id"]: row for row in coverage_authority["rows"]}
    except (OSError, ValueError, KeyError, TypeError, em.Refusal):
        return tuple(dict.fromkeys(errors + ["CANONICAL_SOURCE_UNREADABLE"]))

    for step_id in STEP_IDS:
        expected = expected_rows.get(step_id)
        actual = rows.get(step_id)
        if expected is None:
            errors.append(step_id + ":CANONICAL_ROW_MISSING")
            continue
        if not isinstance(actual, Mapping):
            errors.append(step_id + ":ROW_INVALID")
            continue

        expected_public_keys = set(expected) | {"known_gap", "fallback", "source_family", "provider_module"}
        if set(actual) != expected_public_keys:
            errors.append(step_id + ":ROW_SCHEMA_MISMATCH")

        # These are the public fields whose mutation can change the route or
        # make a row look complete while detaching it from the source flow.
        fields = (
            "canonical_inputs", "canonical_outputs", "mandatory_gates",
            "condition", "certification", "producer_entrypoints", "source_files",
            "engine_family", "applicability", "dispatch", "known_gap",
            "source_family", "fallback",
        )
        for field in fields:
            default = None if field == "fallback" else ""
            expected_value = expected.get(field, default)
            actual_value = actual.get(field, default)
            if _freeze(actual_value) != _freeze(expected_value):
                errors.append(step_id + ":" + field.upper() + "_MISMATCH")
        if actual.get("step_id") != step_id:
            errors.append(step_id + ":STEP_ID_MISMATCH")
        if actual.get("provider_module") != __name__:
            errors.append(step_id + ":PROVIDER_MODULE_MISMATCH")

        producer = actual.get("producer_entrypoints") or ()
        dispatch = actual.get("dispatch") is True
        if dispatch and not producer:
            errors.append(step_id + ":DISPATCH_WITHOUT_PRODUCER")
        if step_id in ("M2", "M4") and (dispatch or producer):
            errors.append(step_id + ":FALSE_PRODUCER")
        if step_id == "M3" and dispatch:
            errors.append("M3:INTERFACE_SI_UNCERTIFIED")
        for source_file in actual.get("source_files", ()) or ():
            if (not isinstance(source_file, str)
                    or not (PROGRAMS / source_file).is_file()
                    or (PROGRAMS / source_file).is_symlink()):
                errors.append(step_id + ":SOURCE_FILE_MISSING:" + str(source_file))
        _validate_symbols(step_id, actual, errors)

    # Bind the adapter view to both machine-readable execution authorities.
    try:
        import yaml
        flow = flow_authority
        flow_rows = {
            row.get("id"): row for row in flow.get("steps", ())
            if isinstance(row, Mapping) and row.get("id") in STEP_IDS
        }
    except (ImportError, OSError, ValueError, TypeError, AttributeError):
        flow_rows = {}
        errors.append("YAML_BINDING_UNREADABLE")
    try:
        portfolio = portfolio_authority
        portfolio_rows = {
            row.get("id"): row for row in portfolio.get("steps", ())
            if isinstance(row, Mapping) and row.get("id") in STEP_IDS
        }
    except (OSError, ValueError, TypeError, AttributeError):
        portfolio_rows = {}
        errors.append("PORTFOLIO_BINDING_UNREADABLE")

    for step_id in STEP_IDS:
        expected = expected_rows.get(step_id, {})
        yaml_row = flow_rows.get(step_id)
        if yaml_row is None:
            errors.append(step_id + ":YAML_ROW_MISSING")
        else:
            yaml_condition = {"kind": yaml_row.get("condition_kind")}
            yaml_condition.update(yaml_row.get("condition") or {})
            bindings = (
                ("canonical_inputs", _yaml_inputs(yaml_row)),
                ("canonical_outputs", tuple(yaml_row.get("required_outputs") or ())),
                ("mandatory_gates", _yaml_gate_programs(yaml_row.get("gate"))),
                ("condition", yaml_condition),
            )
            for field, value in bindings:
                if _freeze(value) != _freeze(expected.get(field, ())):
                    errors.append(step_id + ":YAML_" + field.upper() + "_MISMATCH")
        portfolio_row = portfolio_rows.get(step_id)
        if portfolio_row is None:
            errors.append(step_id + ":PORTFOLIO_ROW_MISSING")
        else:
            for field in ("mandatory_gate_programs", "required_output_contract"):
                if _freeze(portfolio_row.get(field)) != _freeze(expected.get(
                        "mandatory_gates" if field == "mandatory_gate_programs" else "canonical_outputs", ())):
                    errors.append(step_id + ":PORTFOLIO_" + field.upper() + "_MISMATCH")

    fallback = rows.get("A7", {}).get("fallback") if isinstance(rows.get("A7"), Mapping) else None
    if _freeze(fallback) != _freeze({
        "when_degradation_strictly_greater_than_pct": 10.0,
        "to": "A3",
    }):
        errors.append("A7:FALLBACK_AUTHORITY")
    return tuple(dict.fromkeys(errors))


def choose(step_id: str) -> AnalogProvider | None:
    return PROVIDERS.get(step_id)


def a7_fallback_for_degradation(degradation_pct: float) -> str | None:
    """Apply the shared A7 authority: only degradation strictly above 10% trips."""
    if type(degradation_pct) not in (int, float):
        raise TypeError("degradation_pct must be numeric")
    # Arbitrarily large integers are finite by construction.  ``math.isfinite``
    # converts integers through float and would raise OverflowError for them.
    if isinstance(degradation_pct, float) and not math.isfinite(degradation_pct):
        raise ValueError("A7_DEGRADATION_NONFINITE")
    return "A3" if degradation_pct > MAX_DEGRADATION_PCT else None


def a7_degradation_result(degradation_pct: float) -> ProviderResult:
    """Expose explicit NOT_MEASURED semantics for an invalid A7 measurement."""
    if type(degradation_pct) not in (int, float):
        return ProviderResult("A7", "NOT_MEASURED", "A7_DEGRADATION_INVALID")
    if isinstance(degradation_pct, float) and not math.isfinite(degradation_pct):
        return ProviderResult("A7", "NOT_MEASURED", "A7_DEGRADATION_NONFINITE")
    route = a7_fallback_for_degradation(degradation_pct)
    if route == "A3":
        return ProviderResult("A7", "NOT_MEASURED", "A7_DEGRADATION_ABOVE_THRESHOLD;fallback=A3")
    return ProviderResult("A7", "NOT_MEASURED", "A7_DEGRADATION_WITHIN_THRESHOLD")


def produce(step_id: str, project: str | Path, **kwargs: object) -> ProviderResult:
    provider = choose(step_id)
    if provider is None:
        return ProviderResult(str(step_id), "NOT_IMPLEMENTED", "unknown analog provider row")
    if not provider.dispatch:
        status = "NOT_IMPLEMENTED" if not provider.producer_entrypoints else "CANNOT_CERTIFY"
        return ProviderResult(step_id, status, provider.known_gap or "no complete producer")
    missing: list[str] = []
    if not isinstance(project, (str, Path)) or not Path(project).is_dir():
        missing.append("project")
    for key, value in kwargs.items():
        lowered = key.lower()
        if any(token in lowered for token in ("input", "pdk", "model", "tool")) and value in (None, "", (), [], {}):
            missing.append(key)
    if missing:
        return ProviderResult(
            step_id, "NOT_MEASURED", "missing " + ", ".join(dict.fromkeys(missing)) + "; producer was not executed",
            provider.canonical_outputs,
        )
    return ProviderResult(
        step_id, "NOT_MEASURED",
        "source-bound producer route recorded; canonical runner and gates were not executed",
        provider.canonical_outputs,
    )


def register_factories(registry: em.Registry, *, source_sha: str | None = None) -> tuple[str, ...]:
    """Register only complete, reachable source routes with the public Registry."""
    if not hasattr(registry, "register"):
        raise TypeError("registry must provide register")
    # ``source_sha`` remains a source-compatible keyword for older callers,
    # but it is intentionally ignored.  Production identity is issued from a
    # clean commit/tree after the authority and source manifests are checked;
    # callers cannot inject an arbitrary revision into an adapter.
    source_revision, source_tree = _source_identity()
    defects = validate_catalog()
    if defects:
        raise em.Refusal("ANALOG_CATALOG_INVALID", ",".join(defects))
    registered = []
    for step_id in IMPLEMENTED_STEPS:
        provider = PROVIDERS[step_id]
        manifest = _source_manifest(provider)
        adapter_id = "analog_" + step_id.lower()
        component = em.Component(
            "analog_worker",
            ("python3", str(WORKER), "--step", step_id,
             "--inputs", "{inputs}", "--outputs", "{outputs}"),
        )
        required = _required_outputs(provider)
        registry.register(em.Adapter(
            arm_id=adapter_id,
            tool_id="vibeic",
            step_id=step_id,
            source_sha=source_revision,
            source_files=manifest,
            source_tree=source_tree,
            tool_version="source-only",
            engine_families=provider.engine_family,
            components=(component,),
            validate=lambda outputs, binding, _step=step_id: _validate(outputs, binding, _step),
            required_outputs=required,
            objective={"step_id": step_id, "canonical_outputs": list(provider.canonical_outputs)},
            applicability="applicable",
            qualified=False,
            qualification_evidence=json.dumps({
                "status": "SOURCE_ONLY_NOT_MEASURED",
                "producer_entrypoints": list(provider.producer_entrypoints),
                "gates": list(provider.mandatory_gates),
            }, sort_keys=True),
            available=True,
            cpus=1,
            ram_mb=128,
            own_no_tool_reason="The provider is a source-bound catalog route; runtime qualification is intentionally absent.",
            output_contract={output: (output,) for output in required},
        ))
        registered.append(step_id)
    return tuple(registered)


__all__ = [
    "ANALOG_STEPS", "COVERAGE_FILE", "IMPLEMENTED_STEPS", "MIXED_SIGNAL_STEPS",
    "PROVIDERS", "ProviderResult", "STEP_IDS", "a7_degradation_result",
    "a7_fallback_for_degradation",
    "choose", "coverage", "produce", "register_factories", "validate_catalog",
]
