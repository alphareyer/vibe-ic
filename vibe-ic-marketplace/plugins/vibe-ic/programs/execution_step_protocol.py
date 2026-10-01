"""Shared source-bound factory API for the canonical production front door.

Factories are registered explicitly, not discovered by guessing module names.
The frozen execution_modes controller remains the sole live adoption issuer.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
import argparse
import glob
import importlib
import inspect
import json
import math
import os
from pathlib import Path
import re
import sys
from types import ModuleType
from typing import Callable, Mapping, TypedDict

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import execution_modes as em


@dataclass(frozen=True)
class StepRequest:
    step_id: str
    project: Path
    parameters: Mapping[str, object]
    lease: Path
    record: Path
    source_sha: str
    source_files: Mapping[str, str]

    def __post_init__(self):
        if self.step_id not in {row['id'] for row in em.load_portfolio()['steps']}:
            raise em.Refusal('UNKNOWN_CANONICAL_STEP', self.step_id)
        if not re.fullmatch(r'[0-9a-f]{40}', self.source_sha):
            raise em.Refusal('INVALID_SOURCE_SHA', self.source_sha)
        if not isinstance(self.parameters, Mapping) or not self.source_files:
            raise em.Refusal('INCOMPLETE_STEP_REQUEST', self.step_id)
        for name in ('project', 'lease', 'record'):
            path = getattr(self, name)
            if not isinstance(path, Path) or not path.is_absolute() or path.is_symlink():
                raise em.Refusal('STEP_REQUEST_PATH_UNBOUND', name)
        self.check_source()

    def check_source(self) -> None:
        for filename, expected in self.source_files.items():
            path = Path(filename)
            if (not path.is_absolute() or path.is_symlink() or not path.is_file()
                    or not re.fullmatch(r'[0-9a-f]{64}', expected)
                    or em.digest(path) != expected):
                raise em.Refusal('ADAPTER_SOURCE_MISMATCH', filename)


@dataclass(frozen=True)
class PreparedStep:
    context: em.Context | None
    registry: em.Registry | None
    consume: Callable[[Path, em.Context, em.Controller, Path, dict], dict] | None
    disposition: str = 'execute'
    reason: str = ''
    handoff: Mapping[str, object] = field(default_factory=dict)
    # Canonical paths written by consume; shared orchestration journals them.
    # Relative paths only, including complete directories when appropriate.
    adoption_paths: tuple[str, ...] = ()


class ImportedStep(TypedDict):
    status: str
    step_id: str
    source_sha: str
    selected_generation: dict
    binding: dict
    copied: dict[str, str]
    primary_gates: dict[str, str]
    design_verdict: str
    consumer_detail: dict


def imported_result(*, step_id: str, source_sha: str, selected_generation: dict,
                    binding: dict, copied: Mapping[str, str],
                    primary_gates: Mapping[str, str], design_verdict: str,
                    consumer_detail: dict) -> ImportedStep:
    """Canonical result of actual domain consumption; never issues adoption.

    A nested domain CONSUMED report is detail. The shared live controller still
    verifies the source, binding, generation, copied bytes and blocking gates.
    """
    return dict(status='IMPORTED', step_id=step_id, source_sha=source_sha,
                selected_generation=selected_generation, binding=binding,
                copied=dict(copied), primary_gates=dict(primary_gates),
                design_verdict=design_verdict, consumer_detail=consumer_detail)


def json_parameters(parameters: Mapping[str, object]) -> dict:
    """Explicit snapshot encoding of current typed facts, without new facts.

    Paths retain their exact spelling, dataclass fields and Namespace values
    are encoded recursively. Live StepRequest parameters retain their types;
    the declaration/facts digests belong in the issued INPUT binding.
    """
    def encode(value):
        if isinstance(value, Path):
            return os.fspath(value)
        if is_dataclass(value) and not isinstance(value, type):
            return encode(asdict(value))
        if isinstance(value, argparse.Namespace):
            return encode(vars(value))
        if isinstance(value, Mapping):
            if any(not isinstance(key, str) for key in value):
                raise em.Refusal('PRODUCTION_PARAMETER_UNSERIALIZABLE', 'non-string mapping key')
            return {key: encode(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [encode(item) for item in value]
        if value is None or isinstance(value, (str, bool, int)):
            return value
        if isinstance(value, float) and math.isfinite(value):
            return value
        raise em.Refusal('PRODUCTION_PARAMETER_UNSERIALIZABLE', type(value).__name__)
    return encode(parameters)


def tree(inputs: dict[str, Path], population: dict, prefix: str, root: Path) -> None:
    """Bind lexical population, direct aliases, resolved files and missing roots."""
    boundary = root.resolve()

    def visit(path: Path, name: str, ancestors: tuple[Path, ...]) -> None:
        if not path.exists() and not path.is_symlink():
            population[name] = dict(path=str(path), kind='missing')
            return
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise em.Refusal('PRODUCTION_INPUT_LINK_ESCAPE', str(path)) from exc
        if not resolved.is_relative_to(boundary):
            raise em.Refusal('PRODUCTION_INPUT_LINK_ESCAPE', str(path))
        identity = dict(path=str(path), resolved=str(resolved),
                        alias=os.readlink(path) if path.is_symlink() else None)
        population[name] = identity
        if path.is_dir():
            if resolved in ancestors:
                raise em.Refusal('PRODUCTION_INPUT_LINK_CYCLE', str(path))
            identity['kind'] = 'directory'
            for child in sorted(path.iterdir()):
                visit(child, name + '/' + child.name, (*ancestors, resolved))
        elif path.is_file():
            identity.update(kind='file', sha256=em.digest(resolved))
            inputs[name] = resolved
        else:
            raise em.Refusal('PRODUCTION_INPUT_NOT_REGULAR', str(path))

    visit(root, prefix, ())


def input_population(roots: Mapping[str, Path]) -> tuple[dict[str, Path], str]:
    if not roots:
        raise em.Refusal('PRODUCTION_INPUT_ROOTS_MISSING', 'explicit consumed roots required')
    inputs, population = {}, {}
    for name, root in sorted(roots.items()):
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise em.Refusal('PRODUCTION_INPUT_NAME_UNSAFE', name)
        tree(inputs, population, name, Path(root))
    return inputs, json.dumps(population, sort_keys=True)


@dataclass(frozen=True)
class PopulationContext(em.Context):
    """Domains supply exact consumed roots, excluding their mutable outputs."""
    roots: Mapping[str, Path] = field(default_factory=dict)
    population: str = ''
    source_files: Mapping[str, str] = field(default_factory=dict)

    def binding(self) -> dict:
        _, current = input_population(self.roots)
        if not self.population or current != self.population:
            raise em.Refusal('PRODUCTION_INPUT_POPULATION_CHANGED', self.step_id)
        for name, expected in self.source_files.items():
            if not Path(name).is_file() or Path(name).is_symlink() or em.digest(Path(name)) != expected:
                raise em.Refusal('ADAPTER_SOURCE_MISMATCH', name)
        return super().binding()


class FactoryRegistry:
    def __init__(self):
        self._factories: dict[str, tuple[ModuleType, Callable]] = {}

    def register(self, module: ModuleType | str) -> None:
        module = importlib.import_module(module) if isinstance(module, str) else module
        ids, factory = getattr(module, 'STEP_IDS', None), getattr(module, 'prepare', None)
        canonical = {row['id'] for row in em.load_portfolio()['steps']}
        if (not isinstance(module, ModuleType) or not isinstance(ids, tuple) or not ids
                or len(set(ids)) != len(ids) or not set(ids).issubset(canonical)
                or not callable(factory) or not inspect.getsourcefile(factory)):
            raise em.Refusal('PRODUCTION_FACTORY_INCOMPLETE', str(module))
        for step in ids:
            old = self._factories.get(step)
            if old and old != (module, factory):
                raise em.Refusal('PRODUCTION_FACTORY_DUPLICATE', step)
        for step in ids:
            self._factories[step] = (module, factory)

    def coverage(self) -> dict:
        return {step: dict(module=module.__name__, factory=factory.__name__,
                           source=str(Path(inspect.getsourcefile(factory)).resolve()),
                           sha256=em.digest(Path(inspect.getsourcefile(factory))))
                for step, (module, factory) in self._factories.items()}

    def prepare(self, request: StepRequest) -> PreparedStep:
        request.check_source()
        registered = self._factories.get(request.step_id)
        if registered is None:
            raise em.Refusal('PRODUCTION_FACTORY_NOT_REGISTERED', request.step_id)
        _, factory = registered
        filename = str(Path(inspect.getsourcefile(factory)).resolve())
        if filename not in request.source_files:
            raise em.Refusal('PRODUCTION_FACTORY_SOURCE_UNBOUND', filename)
        prepared = factory(request)
        if not isinstance(prepared, PreparedStep):
            raise em.Refusal('PRODUCTION_FACTORY_RESULT_INVALID', request.step_id)
        if prepared.disposition == 'execute':
            if (not isinstance(prepared.context, em.Context)
                    or not isinstance(prepared.registry, em.Registry) or not callable(prepared.consume)
                    or prepared.context.step_id != request.step_id
                    or prepared.context.source_sha != request.source_sha
                    or not prepared.registry.adapters(request.step_id)):
                raise em.Refusal('PRODUCTION_FACTORY_RESULT_INCOMPLETE', request.step_id)
            consumer_file = str(Path(inspect.getsourcefile(prepared.consume)).resolve())
            if consumer_file not in request.source_files:
                raise em.Refusal('PRODUCTION_CONSUMER_SOURCE_UNBOUND', consumer_file)
            if dict(getattr(prepared.context, 'source_files', {}) or {}) != dict(request.source_files):
                raise em.Refusal('PRODUCTION_CONTEXT_SOURCE_CHANGED', request.step_id)
            if (not isinstance(prepared.adoption_paths, tuple) or not prepared.adoption_paths
                    or any(not isinstance(path, str) or not path or Path(path).is_absolute()
                           or '..' in Path(path).parts or path == '.'
                           for path in prepared.adoption_paths)):
                raise em.Refusal('PRODUCTION_ADOPTION_PATHS_MISSING', request.step_id)
            if any(glob.has_magic(path) for path in prepared.adoption_paths):
                raise em.Refusal('PRODUCTION_ADOPTION_PATH_UNSAFE', 'wildcard journal destination')
            for arm in prepared.registry.adapters(request.step_id):
                if arm.source_sha != request.source_sha or dict(arm.source_files) != dict(request.source_files):
                    raise em.Refusal('PRODUCTION_FACTORY_SOURCE_CHANGED', arm.arm_id)
            prepared.context.binding()
        elif prepared.disposition in ('declared_inapplicable', 'external_handoff'):
            declaration = prepared.handoff.get('declaration')
            expected = prepared.handoff.get('declaration_sha256')
            if (not prepared.reason or not declaration or not expected
                    or not Path(declaration).is_file() or Path(declaration).is_symlink()
                    or em.digest(Path(declaration)) != expected
                    or not prepared.handoff.get('facts')):
                raise em.Refusal('PRODUCTION_DISPOSITION_UNBOUND', request.step_id)
            # The declaration must be supplied by the current front door.
            if str(declaration) != str(request.parameters.get('declaration')):
                raise em.Refusal('PRODUCTION_DECLARATION_NOT_REQUESTED', request.step_id)
        else:
            raise em.Refusal('PRODUCTION_DISPOSITION_INVALID', prepared.disposition)
        request.check_source()
        return prepared


FACTORIES = FactoryRegistry()


def register_factory(module: ModuleType | str) -> None:
    FACTORIES.register(module)


def step_input_fingerprint(image: str, config: Path, state_path: Path, step_id: str) -> dict:
    """Reconstruct the existing native synthesis/checker consumer contract.

    Kept here so porting the original repair never replaces librelane_contract
    or its independently accepted RCX, STA, PDK and signoff extensions.
    """
    import librelane_contract as lc
    state = lc._load(state_path)
    return dict(image=image, config=lc.digest(config), state=lc.digest(state_path),
                state_files={str(p): lc.digest(p) for p in lc._walk_paths(
                    {k: v for k, v in state.items() if k != 'metrics'})},
                config_files={str(p): lc.digest(p) for p in lc._walk_paths(lc._load(config))
                              if p.is_file()}, step=step_id)


def step_output_hashes(folder: Path, step_id: str) -> dict:
    import librelane_contract as lc
    state = lc._load(folder / 'state_out.json')
    hashes = {'state_out.json': lc.digest(folder / 'state_out.json')}
    for path in lc._walk_paths({k: v for k, v in state.items() if k != 'metrics'}):
        if path.is_relative_to(folder):
            hashes[str(path.relative_to(folder))] = lc.digest(path)
    for path in folder.rglob('*'):
        if path.is_file() and path.name != 'vibeic_receipt.json' and path.suffix in ('.json', '.rpt'):
            hashes[str(path.relative_to(folder))] = lc.digest(path)
        if step_id == 'OpenROAD.STAPostPNR' and path.is_file() and path.name == 'sta.log':
            hashes[str(path.relative_to(folder))] = lc.digest(path)
    return hashes
