"""Bounded execution-policy controller and common receipt chain.

This module has no built-in EDA executors or second production scheduler. Legacy
direct/librelane/dual switches remain owned by librelane_contract. Adapters
are trusted source-owned code, not executable commands imported from the public
portfolio. An adapter must validate its actual outputs and every required gate.
The controller binds that evidence to the inputs, implementation and process.

Production adapters register with the existing ``Registry`` and execute only
through ``Controller``.  Each run issues the frozen-work, arm, comparison and
program-adoption receipts below; the receipts add provenance to the controller
without changing canonical DAG ownership or selecting a step.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field, replace
import hashlib
import hmac
import inspect
import ast
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import threading
import time
from typing import Callable, Mapping
from types import MappingProxyType
import uuid
from functools import lru_cache

import os as _os                                                    # noqa: E402
import sys as _sys                                                  # noqa: E402

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))

from _atomic_artefact import write_bytes, write_json


# The issuer is owned by this live controller process, never a caller-supplied
# digest or a secret serialized beside editable run receipts. A new interpreter
# cannot adopt a previous issuer's run: durable external supervision is not wired.
_CONTROL_NAMES = frozenset({'.', '..', 'plan.json', 'result.json', 'adoption.json',
                            'refusal.json', 'issued-plan.json', 'selected'})


def _sealed_authority_store():
    key = secrets.token_bytes(32)
    ledger = {}

    def issue(path: Path, payload: dict) -> dict:
        document = json.loads(json.dumps(payload))
        prior = ledger.get(str(path))
        if prior is not None and prior != document:
            raise Refusal('ISSUED_AUTHORITY_REISSUE', str(path))
        ledger[str(path)] = document
        signature = hmac.new(key, _hash(document).encode(), hashlib.sha256).hexdigest()
        return dict(payload=document, signature=signature)

    def consume(path: Path) -> dict:
        document = json.loads(path.read_text())
        issued = ledger.get(str(path))
        if issued is not None and issued != document.get('payload'):
            if document.get('signature') == _hash(document.get('payload')):
                raise Refusal('ISSUED_AUTHORITY_INVALID', str(path))
            raise Refusal('ISSUED_AUTHORITY_CHANGED', str(path))
        expected = hmac.new(key, _hash(document['payload']).encode(), hashlib.sha256).hexdigest()
        if (not isinstance(document.get('signature'), str) or
                not hmac.compare_digest(expected, document['signature']) or
                issued != document['payload']):
            raise Refusal('ISSUED_AUTHORITY_INVALID', str(path))
        return document['payload']

    return issue, consume


_issue_sealed, _consume_sealed = _sealed_authority_store()


class Refusal(RuntimeError):
    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(f'{code}: {detail}')


def mode(value: str | None = None) -> str:
    if value is None:
        return 'default-mode'
    if value not in ('default-mode', 'ultra-mode'):
        raise Refusal('INVALID_EXECUTION_MODE', repr(value))
    return value


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


_REPO_ROOT = Path(__file__).resolve().parents[4]


def _git_source_authority(source_sha: str) -> tuple[str, str]:
    """Resolve a source commit and tree from the clean repository object DB."""
    if not isinstance(source_sha, str) or not re.fullmatch(r'[0-9a-f]{40}', source_sha):
        raise Refusal('INVALID_SOURCE_SHA', str(source_sha))
    try:
        commit = subprocess.run(
            ['git', '-C', str(_REPO_ROOT), 'rev-parse', '--verify', f'{source_sha}^{{commit}}'],
            check=True, capture_output=True, text=True, timeout=5).stdout.strip()
        tree = subprocess.run(
            ['git', '-C', str(_REPO_ROOT), 'rev-parse', '--verify', f'{source_sha}^{{tree}}'],
            check=True, capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise Refusal('SOURCE_AUTHORITY_UNAVAILABLE', source_sha) from exc
    return commit, tree


@lru_cache(maxsize=4096)
def _git_blob_at_commit(source_sha: str, relative: str) -> str:
    """Return a tracked blob only from the verified source commit.

    A caller-editable portfolio and a caller-rehashed working file cannot
    authenticate one another.  The source commit must itself contain the
    canonical object that is currently being consumed.
    """
    if (not isinstance(relative, str) or not relative or relative.startswith('/') or
            '..' in Path(relative).parts):
        raise Refusal('SOURCE_AUTHORITY_UNAVAILABLE', relative)
    try:
        return subprocess.run(
            ['git', '-C', str(_REPO_ROOT), 'rev-parse', '--verify',
             f'{source_sha}:{relative}'],
            check=True, capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise Refusal('SOURCE_AUTHORITY_UNAVAILABLE', f'{source_sha}:{relative}') from exc


def _verified_current_source_commit(source_sha: str) -> str:
    """Require the source commit to contain this checkout's canonical flow.

    A well-formed commit id is only an object reference.  Binding it to the
    current canonical YAML prevents a caller from placing an unrelated
    historical or fabricated source id around an otherwise valid fixture.
    """
    commit, _ = _git_source_authority(source_sha)
    canonical = _canonical_flow_path()
    relative = str(canonical.relative_to(_REPO_ROOT))
    try:
        current_blob = subprocess.run(
            ['git', '-C', str(_REPO_ROOT), 'hash-object', str(canonical)],
            check=True, capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise Refusal('SOURCE_AUTHORITY_UNAVAILABLE', relative) from exc
    if _git_blob_at_commit(commit, relative) != current_blob:
        raise Refusal('SOURCE_AUTHORITY_STALE', source_sha)
    return commit


def _canonical_flow_path() -> Path:
    return _REPO_ROOT / 'vibe-ic-marketplace/plugins/vibe-ic/flow/phase1_phase2_phase3.yaml'


def _blob_bytes(path: Path) -> str:
    content = path.read_bytes()
    return hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()


def _tracked_clean_file(path: Path) -> None:
    if path.is_symlink():
        raise Refusal('SOURCE_AUTHORITY_UNAVAILABLE', str(path))
    path = path.resolve()
    try:
        relative = str(path.relative_to(_REPO_ROOT))
        head = subprocess.check_output(['git', '-C', str(_REPO_ROOT), 'rev-parse', 'HEAD'], text=True).strip()
        expected = _git_blob_at_commit(head, relative)
        if _blob_bytes(path) != expected:
            raise Refusal('SOURCE_AUTHORITY_DIRTY', str(path))
    except (OSError, ValueError) as exc:
        raise Refusal('SOURCE_AUTHORITY_UNAVAILABLE', str(path)) from exc


def _canonical_flow_authority(flow_path: Path) -> str:
    if flow_path.resolve() != _canonical_flow_path().resolve():
        raise Refusal('PORTFOLIO_CANONICAL_UNAVAILABLE', str(flow_path))
    try:
        _tracked_clean_file(flow_path)
    except Refusal as exc:
        raise Refusal('PORTFOLIO_CANONICAL_UNTRUSTED', str(flow_path)) from exc
    return digest(flow_path)


def _local_python_imports(path: Path) -> set[Path]:
    """Resolve local modules, packages and relative imports without importing."""
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except (OSError, SyntaxError):
        return set()
    requests = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            requests.extend((0, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            requests.append((node.level, node.module or ''))
            requests.extend((node.level, '.'.join(filter(None, (node.module, alias.name))))
                            for alias in node.names if alias.name != '*')
    result = set()
    programs = Path(__file__).resolve().parent
    for level, name in requests:
        roots = [path.parent]
        if level:
            roots = [path.parent.joinpath(*(['..'] * (level - 1))).resolve()]
        else:
            roots += [programs, programs.parent]
        for root in roots:
            parts = name.split('.') if name else []
            candidate = root.joinpath(*parts)
            module = candidate.with_suffix('.py') if parts else candidate / '__init__.py'
            package = candidate / '__init__.py'
            if any(p.is_symlink() for p in (module, package)):
                raise Refusal('ENTRY_SOURCE_UNBOUND', str(candidate))
            found = {p.resolve() for p in (module, package) if p.is_file()}
            if found:
                result.update(found)
                for i in range(1, len(parts)):
                    init = root.joinpath(*parts[:i], '__init__.py')
                    if init.is_file():
                        result.add(init.resolve())
                break
    return result


def _source_closure(paths: Mapping[str, str]) -> set[Path]:
    closure = set()
    pending = [Path(path).resolve() for path in paths if str(path).endswith('.py')]
    while pending:
        path = pending.pop()
        if path in closure:
            continue
        closure.add(path)
        pending.extend(_local_python_imports(path) - closure)
    return closure


def _source_method(context_type: type, source: Path, method: str) -> Callable:
    """Require the method object itself to match its current tracked source."""
    _tracked_clean_file(source)
    compiled = compile(source.read_bytes(), str(source), 'exec', dont_inherit=True)
    def nested(code, name):
        return next(c for c in code.co_consts if inspect.iscode(c) and c.co_name == name)
    try:
        expected = nested(nested(compiled, context_type.__name__), method)
        actual = getattr(context_type, method)
        if actual.__code__ != expected:
            raise ValueError('method source identity changed')
    except (StopIteration, AttributeError, ValueError) as exc:
        raise Refusal('CONTEXT_IMPLEMENTATION_UNTRUSTED', context_type.__name__) from exc
    return actual


def _invocation_sources(component: Component) -> dict:
    """Bind executable, entry and every static file argument before execution.

    Extra argv files are reproduction inputs, not new independent engines.
    Interpreter code from stdin, inline code or an unresolved module is refused.
    """
    if not component.argv:
        raise Refusal('INVALID_COMPONENT', component.name)
    binary = shutil.which(component.argv[0])
    if not binary:
        raise Refusal('EXECUTABLE_UNBOUND', component.argv[0])
    executable = Path(binary).resolve()
    if not executable.is_relative_to(_REPO_ROOT) and executable != Path(_sys.executable).resolve():
        raise Refusal('EXECUTABLE_SOURCE_UNBOUND', str(executable))
    files = []
    for argument in component.argv[1:]:
        if '{inputs}' in argument or '{outputs}' in argument:
            continue
        value = argument.split('=', 1)[1] if argument.startswith('-') and '=' in argument else argument
        path = Path(value)
        if path.is_file():
            if path.is_symlink() or not path.is_absolute():
                raise Refusal('ENTRY_SOURCE_UNBOUND', value)
            files.append(path.resolve())
    interpreter = executable.name.lower().startswith(('python', 'pypy')) or executable.name in ('sh', 'bash', 'dash')
    if interpreter and (not files or any(v in ('-', '-c', '-m') for v in component.argv[1:])):
        raise Refusal('ENTRY_SOURCE_UNBOUND', component.name)
    entry = files[0] if interpreter else executable
    implementation = _source_closure({str(entry): digest(entry)}) if entry.suffix == '.py' else {entry}
    arguments = set(files)
    closure = arguments | _source_closure({str(p): digest(p) for p in arguments})
    return dict(executable=str(executable), executable_sha256=digest(executable),
                argv=list(component.argv), entry=str(entry),
                implementation={str(p): digest(p) for p in sorted(implementation)},
                argument_sources={str(p): digest(p) for p in sorted(closure)})


def _route_pointer(receipt: Mapping[str, object]) -> str:
    return _hash({key: receipt.get(key) for key in (
        'ic_ip_path', 'source_sha', 'project_digest', 'request_digest',
        'intent_label', 'mode_intent')})


def _verify_route_authority(receipt: Mapping[str, object]) -> None:
    from execution_authority import consume
    try:
        issued = consume()['route']
    except Refusal as exc:
        raise Refusal('ROUTE_AUTHORITY_UNAVAILABLE', str(exc)) from exc
    if dict(receipt) != issued:
        raise Refusal('ROUTE_AUTHORITY_UNAVAILABLE', 'not the current live issued route')


def _issued(path: Path) -> dict:
    return _consume_sealed(path)


def _relative(value: str) -> Path:
    p = Path(value)
    if p.is_absolute() or not p.parts or '..' in p.parts or value == '.':
        raise Refusal('UNSAFE_RELATIVE_PATH', value)
    return p


def _write(path: Path, value: object) -> None:
    write_json(path, value)


@dataclass(frozen=True)
class Budget:
    cpus: int
    ram_mb: int
    workers: int = 4
    licenses: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in
               (self.cpus, self.ram_mb, self.workers)) or any(
                type(v) is not int or v < 0 for v in self.licenses.values()):
            raise Refusal('INVALID_BUDGET', repr(self))


@dataclass(frozen=True)
class Context:
    step_id: str
    source_sha: str
    inputs: Mapping[str, Path]
    objective: Mapping[str, object]
    required_gates: tuple[str, ...]
    native_mode: str = 'direct'
    # Production contexts must resolve both fields at construction.  The
    # neutral controller fixtures pass an explicit ``neutral-test`` receipt.
    ic_ip_path: str | None = None
    route_receipt: Mapping[str, object] = field(default_factory=dict)
    project_digest: str = ''
    intent_label: str = 'PROGRAM_DEFAULT'
    request_digest: str = ''
    # Only the repository's explicit neutral test fixture may use the
    # compatibility route.  A caller-authored ``kind=neutral-test`` mapping
    # is not production authorization and cannot qualify or adopt.

    def binding(self) -> dict:
        if not re.fullmatch(r'[0-9a-f]{40}', self.source_sha):
            raise Refusal('INVALID_SOURCE_SHA', self.source_sha)
        if self.native_mode not in ('direct', 'librelane', 'dual'):
            raise Refusal('AMBIGUOUS_NATIVE_IDENTITY', self.native_mode)
        if self.ic_ip_path not in ('IC', 'IP'):
            raise Refusal('INVALID_IC_IP_PATH', self.ic_ip_path)
        if self.intent_label not in ('PROGRAM_DEFAULT', 'USER_EXPLICIT_ULTRA'):
            raise Refusal('INVALID_EXECUTION_INTENT', self.intent_label)
        if self.request_digest and not re.fullmatch(r'[0-9a-f]{64}', self.request_digest):
            raise Refusal('INVALID_REQUEST_DIGEST', self.request_digest)
        if self.intent_label == 'USER_EXPLICIT_ULTRA' and not self.request_digest:
            raise Refusal('REQUEST_INTENT_UNBOUND', 'explicit Ultra needs its issued request digest')
        if not self.route_receipt:
            raise Refusal('ROUTE_RECEIPT_REQUIRED', 'IC/IP context needs a bound route receipt')
        route_path = self.route_receipt.get('ic_ip_path')
        if route_path != self.ic_ip_path:
            raise Refusal('IC_IP_ROUTE_MISMATCH', self.step_id)
        route_kind = self.route_receipt.get('kind')
        if route_kind == 'issued-route':
            required = ('schema', 'route_digest', 'project_digest', 'source_sha',
                        'request_digest')
            if (any(key not in self.route_receipt for key in required) or
                    self.route_receipt.get('schema') != 1 or
                    not re.fullmatch(r'[0-9a-f]{64}', str(self.route_receipt['project_digest'])) or
                    not re.fullmatch(r'[0-9a-f]{64}', str(self.route_receipt['request_digest'])) or
                    not re.fullmatch(r'[0-9a-f]{64}', str(self.route_receipt['route_digest']))):
                raise Refusal('ROUTE_RECEIPT_INVALID', self.step_id)
            route_body = {k: v for k, v in self.route_receipt.items()
                          if k != 'route_digest'}
            if _hash(route_body) != self.route_receipt['route_digest']:
                raise Refusal('ROUTE_RECEIPT_DIGEST_MISMATCH', self.step_id)
            if self.route_receipt['source_sha'] != self.source_sha:
                raise Refusal('ROUTE_SOURCE_MISMATCH', self.step_id)
            if self.route_receipt['project_digest'] != self.project_digest:
                raise Refusal('ROUTE_PROJECT_MISMATCH', self.step_id)
            if self.route_receipt['request_digest'] != self.request_digest:
                raise Refusal('ROUTE_REQUEST_MISMATCH', self.step_id)
            route_intent = self.route_receipt.get('intent_label', 'PROGRAM_DEFAULT')
            route_mode = self.route_receipt.get('mode_intent', 'default')
            if (route_intent != self.intent_label or
                    route_mode != ('ultra' if self.intent_label == 'USER_EXPLICIT_ULTRA'
                                   else 'default')):
                raise Refusal('ROUTE_INTENT_MISMATCH', self.step_id)
            _verify_route_authority(self.route_receipt)
            _git_source_authority(self.source_sha)
        else:
            raise Refusal('ROUTE_RECEIPT_INVALID', self.step_id)
        if not self.inputs or not self.objective or not self.required_gates:
            raise Refusal('INCOMPLETE_CONTEXT', self.step_id)
        if len(set(self.required_gates)) != len(self.required_gates):
            raise Refusal('DUPLICATE_GATE', self.step_id)
        files = {}
        for name, path in self.inputs.items():
            _relative(name)
            if not Path(path).is_file() or Path(path).is_symlink():
                raise Refusal('INPUT_NOT_REGULAR', name)
            files[name] = digest(Path(path))
        return dict(step_id=self.step_id, source_sha=self.source_sha,
                    controller_sha256=digest(Path(__file__)),
                    inputs=files, objective=dict(self.objective),
                    required_gates=list(self.required_gates),
                    native_mode=self.native_mode,
                    ic_ip_path=self.ic_ip_path,
                    route_receipt=dict(self.route_receipt),
                    project_digest=self.project_digest,
                    route_receipt_sha256=_hash(self.route_receipt)
                    if self.route_receipt else None,
                    intent_label=self.intent_label,
                    request_digest=self.request_digest)


@dataclass(frozen=True)
class Component:
    name: str
    argv: tuple[str, ...]
    timeout_s: float = 30


@dataclass(frozen=True)
class Evidence:
    """Produced by the adapter's output consumer; never inferred from rc0."""
    binding: Mapping[str, object]
    verdict: str
    gates: Mapping[str, str]
    outputs: Mapping[str, str]  # relative output filename -> measured sha256
    metrics: Mapping[str, float] = field(default_factory=dict)
    detail: str = ''


@dataclass(frozen=True)
class Adapter:
    arm_id: str
    tool_id: str
    step_id: str
    source_sha: str
    source_files: Mapping[str, str]  # absolute local implementation -> digest
    tool_version: str
    engine_families: tuple[str, ...]
    components: tuple[Component, ...]
    validate: Callable[[Path, Mapping[str, object]], Evidence]
    required_outputs: tuple[str, ...]
    objective: Mapping[str, object]
    applicability: str = 'applicable'  # applicable | inapplicable | unknown
    applicability_reason: str = ''
    role: str = 'producer'  # checker/complementary never become candidates
    qualified: bool = True
    qualification_evidence: str = ''
    available: bool = True
    availability_reason: str = ''
    cpus: int = 1
    ram_mb: int = 128
    license_id: str | None = None
    own_no_tool_reason: str | None = None
    output_contract: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    verified_source_blobs: Mapping[str, str] = field(default_factory=dict)
    executable_receipts: Mapping[str, object] = field(default_factory=dict)
    invocation_sources: Mapping[str, object] = field(default_factory=dict)

    def identity(self) -> dict:
        return dict(arm_id=self.arm_id, tool_id=self.tool_id,
                    step_id=self.step_id, source_sha=self.source_sha,
                    source_files=dict(self.source_files),
                    verified_source_blobs=dict(self.verified_source_blobs),
                    executable_receipts=dict(self.executable_receipts),
                    invocation_sources=dict(self.invocation_sources),
                    tool_version=self.tool_version,
                    engine_families=list(self.engine_families),
                    components=[dict(name=c.name, argv=list(c.argv),
                                     timeout_s=c.timeout_s) for c in self.components],
                    required_outputs=list(self.required_outputs),
                    output_contract={k: list(v) for k, v in self.output_contract.items()},
                    objective=dict(self.objective), role=self.role,
                    qualification_evidence=self.qualification_evidence)


class Registry:
    """Explicit source-bound executors. The shipped production registry is empty."""
    def __init__(self):
        self._adapters: dict[str, Adapter] = {}
        self._version_lock = threading.Lock()

    def register(self, adapter: Adapter) -> None:
        if adapter.arm_id in self._adapters:
            raise Refusal('DUPLICATE_ARM', adapter.arm_id)
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', adapter.arm_id) or adapter.arm_id in _CONTROL_NAMES:
            raise Refusal('UNSAFE_ARM_ID', adapter.arm_id)
        if not adapter.source_files or not adapter.tool_version or not (
                adapter.engine_families and adapter.components and
                adapter.required_outputs and callable(adapter.validate)):
            raise Refusal('ADAPTER_INCOMPLETE', adapter.arm_id)
        if not adapter.qualification_evidence:
            raise Refusal('QUALIFICATION_UNBOUND', adapter.arm_id)
        _verified_current_source_commit(adapter.source_sha)
        head = subprocess.check_output(['git', '-C', str(_REPO_ROOT), 'rev-parse', 'HEAD'], text=True).strip()
        if head != adapter.source_sha:
            raise Refusal('SOURCE_AUTHORITY_STALE', adapter.source_sha)
        executable_paths = []
        for component in adapter.components:
            binary = shutil.which(component.argv[0])
            if binary:
                executable_paths.append(Path(binary).resolve())
        known_tool = adapter.tool_id in ('librelane', 'openroad', 'openroad_fork')
        if known_tool:
            # Priority is never granted to a caller's name/version or basename.
            # Native installations require an independently issued install
            # receipt; this source-only controller has no such issuer wired.
            raise Refusal('TOOL_ID_UNBOUND', 'verified native installation receipt required')
        invocations = {component.name: _invocation_sources(component) for component in adapter.components}
        if adapter.applicability not in ('applicable', 'inapplicable', 'unknown'):
            raise Refusal('INVALID_APPLICABILITY', adapter.arm_id)
        if adapter.role not in ('producer', 'checker', 'complementary'):
            raise Refusal('INVALID_ROLE', adapter.arm_id)
        if adapter.applicability != 'applicable' and not adapter.applicability_reason:
            raise Refusal('APPLICABILITY_UNEXPLAINED', adapter.arm_id)
        if not adapter.available and not adapter.availability_reason:
            raise Refusal('AVAILABILITY_UNEXPLAINED', adapter.arm_id)
        if any(type(v) is not int or v <= 0 for v in (adapter.cpus, adapter.ram_mb)):
            raise Refusal('INVALID_ARM_BUDGET', adapter.arm_id)
        for name in adapter.required_outputs:
            _relative(name)
        if any(not paths or not set(paths).issubset(adapter.required_outputs)
               for paths in adapter.output_contract.values()):
            raise Refusal('OUTPUT_CONTRACT_UNBOUND', adapter.arm_id)
        for path, expected in adapter.source_files.items():
            p = Path(path)
            if not p.is_file() or p.is_symlink() or digest(p) != expected:
                raise Refusal('ADAPTER_SOURCE_MISMATCH', path)
        declared_sources = {Path(path).resolve() for path in adapter.source_files}
        external = set(executable_paths) - {p for p in executable_paths if p.is_relative_to(_REPO_ROOT)}
        for path in declared_sources - external:
            _tracked_clean_file(path)
            relative = str(path.relative_to(_REPO_ROOT))
            if _git_blob_at_commit(adapter.source_sha, relative) != _blob_bytes(path):
                raise Refusal('ADAPTER_SOURCE_MISMATCH', str(path))
        missing_dependencies = _source_closure(adapter.source_files) - declared_sources
        if missing_dependencies:
            raise Refusal('ADAPTER_SOURCE_CLOSURE_INCOMPLETE', str(sorted(missing_dependencies)[0]))
        for component in adapter.components:
            _relative(component.name)
            if len(Path(component.name).parts) != 1 or not component.argv or (
                    not math.isfinite(component.timeout_s) or component.timeout_s <= 0):
                raise Refusal('INVALID_COMPONENT', component.name)
            binary = shutil.which(component.argv[0])
            if not binary or str(Path(binary).resolve()) not in adapter.source_files:
                raise Refusal('EXECUTABLE_UNBOUND', component.argv[0])
            invocation = invocations[component.name]
            for name, expected in invocation['argument_sources'].items():
                path = Path(name)
                if not path.is_relative_to(_REPO_ROOT) or adapter.source_files.get(name) != expected:
                    raise Refusal('ENTRY_SOURCE_UNBOUND', name)
                _tracked_clean_file(path)
            invocations[component.name] = invocation
        validator_file = inspect.getsourcefile(adapter.validate)
        if not validator_file or str(Path(validator_file).resolve()) not in adapter.source_files:
            raise Refusal('VALIDATOR_SOURCE_UNBOUND', adapter.arm_id)
        if len({c.name for c in adapter.components}) != len(adapter.components):
            raise Refusal('DUPLICATE_COMPONENT', adapter.arm_id)
        # The caller's dataclass may be frozen while its nested mappings remain
        # mutable.  Publish an immutable registration snapshot so a later
        # ``arm.source_files[path] = new_digest`` cannot rehash authority.
        source_blobs = {str(p): _git_blob_at_commit(adapter.source_sha, str(p.relative_to(_REPO_ROOT)))
                        for p in declared_sources - external}
        snapshot = replace(
            adapter,
            verified_source_blobs=MappingProxyType(source_blobs),
            executable_receipts=MappingProxyType({}),
            invocation_sources=MappingProxyType(invocations),
            source_files=MappingProxyType(dict(adapter.source_files)),
            engine_families=tuple(adapter.engine_families),
            components=tuple(adapter.components),
            required_outputs=tuple(adapter.required_outputs),
            objective=MappingProxyType(dict(adapter.objective)),
            output_contract=MappingProxyType({k: tuple(v)
                                              for k, v in adapter.output_contract.items()}),
        )
        self._adapters[adapter.arm_id] = snapshot

    def adapters(self, step_id: str) -> list[Adapter]:
        return [a for a in self._adapters.values() if a.step_id == step_id]


def load_portfolio(path: Path | None = None) -> dict:
    path = path or Path(__file__).parent / 'data/execution_modes_portfolio.json'
    data = json.loads(path.read_text())
    _validate_portfolio(data)
    return data


def _canonical_flow_requirements() -> tuple[str, dict[str, dict]]:
    flow_path = _canonical_flow_path()
    try:
        import yaml
        flow = yaml.safe_load(flow_path.read_text())
    except (OSError, ValueError, ImportError) as exc:
        raise Refusal('PORTFOLIO_CANONICAL_UNAVAILABLE', str(flow_path)) from exc
    if not isinstance(flow, dict) or not isinstance(flow.get('steps'), list):
        raise Refusal('PORTFOLIO_CANONICAL_INVALID', str(flow_path))
    flow_sha = _canonical_flow_authority(flow_path)
    requirements = {}
    for step in flow['steps']:
        step_id = str(step.get('id'))
        gates = set()
        for clause in (step.get('gate') or {}).get('all_of', []):
            for key in ('program_exit_zero', 'advisory_program_exit_zero',
                        'optional_program_exit_zero'):
                value = clause.get(key) if isinstance(clause, dict) else None
                command = value if isinstance(value, str) else (
                    value.get('command', '') if isinstance(value, dict) else '')
                if command:
                    gates.add(Path(command.split()[0]).name)
        requirements[step_id] = dict(
            gates=frozenset(gates),
            outputs=tuple(step.get('required_outputs') or ()),
        )
    if len(requirements) != 70:
        raise Refusal('PORTFOLIO_CANONICAL_INVALID', str(len(requirements)))
    return flow_sha, requirements


def _validate_portfolio(data: dict) -> None:
    if not isinstance(data, dict) or data.get('meta', {}).get('test_only'):
        return
    flow_sha, requirements = _canonical_flow_requirements()
    meta = data.get('meta') or {}
    if meta.get('canonical_flow_sha256') != flow_sha:
        raise Refusal('PORTFOLIO_STALE_CANONICAL', str(meta.get('canonical_flow_sha256')))
    ids = [s['id'] for s in data['steps']]
    if len(ids) != 70 or len(set(ids)) != 70:
        raise Refusal('PORTFOLIO_IDS_INVALID', str(len(ids)))
    for row in data['steps']:
        req = requirements.get(str(row.get('id')))
        if req is None:
            raise Refusal('PORTFOLIO_STEP_UNBOUND', str(row.get('id')))
        source = ((row.get('current_default') or {}).get('source') or {})
        expected_file = 'vibe-ic-marketplace/plugins/vibe-ic/flow/phase1_phase2_phase3.yaml'
        if ('sha' in source or source.get('canonical_file') != expected_file or
                str(source.get('canonical_step_id')) != str(row.get('id')) or
                source.get('canonical_blob') != meta.get('canonical_flow_git_blob')):
            raise Refusal('PORTFOLIO_SOURCE_UNBOUND', str(row.get('id')))
        current_blob = subprocess.check_output(['git', '-C', str(_REPO_ROOT),
                                               'hash-object', str(_canonical_flow_path())], text=True).strip()
        if current_blob != meta.get('canonical_flow_git_blob'):
            raise Refusal('PORTFOLIO_SOURCE_UNBOUND', str(row.get('id')))
        for program in source.get('programs') or ():
            program_path = _REPO_ROOT / 'vibe-ic-marketplace/plugins/vibe-ic/programs' / f'{program}.py'
            _tracked_clean_file(program_path)
        if not req['gates'].issubset(set(row.get('mandatory_gate_programs') or ())):
            raise Refusal('PORTFOLIO_GATES_STALE', str(row.get('id')))
        declared_outputs = set(row.get('required_output_contract') or ())
        if not set(req['outputs']).issubset(declared_outputs):
            raise Refusal('PORTFOLIO_OUTPUTS_STALE', str(row.get('id')))


@dataclass(frozen=True)
class Superiority:
    """Previous measured comparison; candidate evidence must share this binding."""
    binding: Mapping[str, object]
    preferred: str
    reference: str
    metric: str
    direction: str
    receipts: Mapping[str, Path]


class Controller:
    def __init__(self, registry: Registry, budget: Budget, portfolio: dict | None = None):
        self.registry, self.budget = registry, budget
        self.portfolio = portfolio if portfolio is not None else load_portfolio()
        _validate_portfolio(self.portfolio)

    def _context_binding(self, context: Context) -> dict:
        """BLOCKING: foreign implementations cannot speak for a live issuer.

        The tracked neutral protocol fixture has no production authority. Its
        exact implementation may exercise a test-only portfolio with default,
        empty transport fields; it cannot turn caller labels into a request.
        """
        context_type = type(context)
        fixture = _sys.modules.get('programs.tests.test_execution_modes')
        neutral_type = getattr(fixture, 'NeutralContext', None) if fixture else None
        neutral = (neutral_type is not None and context_type is neutral_type and
                   (self.portfolio.get('meta', {}).get('test_only') is True or
                    not self.registry.adapters(context.step_id)))
        if context_type is not Context and not neutral:
            raise Refusal('CONTEXT_IMPLEMENTATION_UNTRUSTED', context_type.__name__)
        if neutral:
            source = Path(inspect.getsourcefile(neutral_type)).resolve()
            expected = Path(__file__).parent / 'tests/test_execution_modes.py'
            if source != expected.resolve():
                raise Refusal('CONTEXT_IMPLEMENTATION_UNTRUSTED', str(source))
            binding_method = _source_method(neutral_type, source, 'binding')
            if context.route_receipt.get('kind') != 'neutral-test':
                context = Context(**{name: getattr(context, name) for name in Context.__dataclass_fields__})
            elif context.intent_label != 'PROGRAM_DEFAULT' or context.request_digest or context.project_digest:
                raise Refusal('CONTROLLER_ISSUANCE_REQUIRED', 'neutral fixture has no execution authority')
            if type(context) is neutral_type:
                return binding_method(context)
        from execution_authority import consume
        try:
            issued = consume()
        except Refusal as exc:
            raise Refusal('CONTROLLER_ISSUANCE_REQUIRED', str(exc)) from exc
        binding = Context.binding(context)
        if (dict(context.route_receipt) != issued['route'] or
                context.request_digest != issued['request']['request_digest'] or
                context.intent_label != issued['request']['intent_label']):
            raise Refusal('CONTROLLER_ISSUANCE_REQUIRED', 'context differs from canonical issued request')
        return binding

    def _execution_issuance(self, context: Context, plan: dict) -> dict:
        binding = self._context_binding(context)
        if binding != plan['binding']:
            raise Refusal('CURRENT_INPUT_CHANGED', context.step_id)
        body = {k: v for k, v in plan.items() if k != 'execution_issuance'}
        if context.route_receipt.get('kind') == 'neutral-test':
            return dict(kind='neutral-protocol-only', plan_digest=_hash(body))
        from execution_authority import consume
        issued = consume()
        return dict(kind='canonical-controller-issuance',
                    invocation_id=issued['request']['invocation_id'],
                    request_digest=issued['request']['request_digest'],
                    route_digest=issued['route']['route_digest'],
                    source_sha=issued['route']['source_sha'],
                    source_blobs=issued['source_blobs'], plan_digest=_hash(body),
                    inputs=dict(binding['inputs']),
                    argv_digest=_hash({row['arm_id']: row['components'] for row in plan['portfolio']}),
                    implementation_digest=_hash({row['arm_id']: row['invocation_sources'] for row in plan['portfolio']}))

    def _verify_execution_issuance(self, context: Context, plan: dict) -> None:
        if plan.get('execution_issuance') != self._execution_issuance(context, plan):
            raise Refusal('CONTROLLER_ISSUANCE_CHANGED', context.step_id)

    @staticmethod
    def _authority_refusal(root: Path, exc: Refusal) -> dict:
        result = dict(status='REFUSED', reason=exc.code, detail=str(exc),
                      selected=None, candidate_statuses={})
        _write(root / 'refusal.json', result)
        _write(root / 'result.json', result)
        return result

    def _measure_versions(self, context: Context) -> None:
        # Even a --version process waits for admission. Registry construction
        # only binds files; a caller-authored Context cannot start a tool there.
        self._context_binding(context)
        with self.registry._version_lock:
            for arm in self.registry.adapters(context.step_id):
                if arm.executable_receipts:
                    continue
                receipts = {}
                for invocation in arm.invocation_sources.values():
                    path = Path(invocation['executable'])
                    if str(path) in receipts:
                        continue
                    if digest(path) != arm.source_files[str(path)]:
                        raise Refusal('ADAPTER_SOURCE_MISMATCH', str(path))
                    try:
                        measured = subprocess.run([str(path), '--version'], capture_output=True,
                                                  text=True, timeout=2)
                    except (OSError, subprocess.SubprocessError) as exc:
                        raise Refusal('TOOL_VERSION_UNBOUND', str(path)) from exc
                    version = (measured.stdout + measured.stderr).strip()
                    if measured.returncode != 0 or not version:
                        raise Refusal('TOOL_VERSION_UNBOUND', str(path))
                    receipts[str(path)] = dict(executable_sha256=digest(path), version=version,
                                              argv=[str(path), '--version'], rc=measured.returncode)
                self.registry._adapters[arm.arm_id] = replace(
                    arm, executable_receipts=MappingProxyType(receipts))

    def _validate_live_portfolio(self, plan: Mapping[str, object] | None = None) -> None:
        _validate_portfolio(self.portfolio)
        if (plan is not None and not self.portfolio.get('meta', {}).get('test_only') and
                (plan.get('public_portfolio_sha256') != _hash(self.portfolio) or
                 plan.get('public_portfolio') != self.portfolio)):
            raise Refusal('PORTFOLIO_AUTHORITY_CHANGED', str(plan.get('step_id')))

    def _admission(self, adapter: Adapter, context: Context) -> str:
        if adapter.role != 'producer':
            return 'COMPLEMENTARY_CHECKER'
        if adapter.source_sha != context.source_sha:
            return 'WRONG_SOURCE'
        if adapter.applicability != 'applicable':
            return 'APPLICABILITY_' + adapter.applicability.upper()
        if not adapter.qualified:
            return 'NOT_QUALIFIED'
        if not adapter.available:
            return 'UNAVAILABLE'
        if adapter.license_id and self.budget.licenses.get(adapter.license_id, 0) < 1:
            return 'LICENSE_UNAVAILABLE'
        if (adapter.cpus > min(self.budget.cpus, len(os.sched_getaffinity(0))) or
                adapter.ram_mb > self.budget.ram_mb):
            return 'BUDGET_UNAVAILABLE'
        return 'READY'

    @staticmethod
    def _provider_identity(adapter: Adapter) -> tuple:
        """Underlying entry closure; aliases and appended files add no engine."""
        identities = set()
        for component in adapter.components:
            invocation = _invocation_sources(component)
            identities.add((invocation['executable_sha256'],
                            tuple(sorted(invocation['implementation'].values()))))
        return tuple(sorted(identities))

    @staticmethod
    def acceptance_contract(context: Context, step: Mapping[str, object]) -> dict:
        """Return the immutable contract every candidate must satisfy.

        The contract is deliberately derived from the canonical step row and
        the live request.  It is carried in receipts by digest, so changing a
        gate, objective, or output obligation cannot reuse an older result.
        """
        return dict(step_id=context.step_id,
                    required_gates=list(context.required_gates),
                    required_output_contract=list(step['required_output_contract']),
                    objective=dict(context.objective))

    @staticmethod
    def _mode_intent(selected_mode: str) -> str:
        return 'ultra' if selected_mode == 'ultra-mode' else 'default'

    def plan(self, context: Context, execution_mode: str | None = None,
             superiority: Superiority | None = None) -> dict:
        self._validate_live_portfolio()
        binding = self._context_binding(context)
        route_mode = context.route_receipt.get('mode_intent')
        if (execution_mode is None and context.intent_label == 'USER_EXPLICIT_ULTRA'):
            selected_mode = 'ultra-mode'
        else:
            selected_mode = mode(execution_mode)
        # A routed production context may enter Ultra only with the typed
        # intent issued by the canonical front door. Neutral controller
        # fixtures retain their historical direct Ultra API for protocol-only
        # tests because they carry no production route receipt.
        if (selected_mode == 'ultra-mode' and
                context.route_receipt.get('kind') != 'neutral-test' and
                context.intent_label != 'USER_EXPLICIT_ULTRA'):
            raise Refusal('ULTRA_INTENT_MISSING', context.step_id)
        if (context.route_receipt.get('kind') == 'issued-route' and
                route_mode != self._mode_intent(selected_mode)):
            raise Refusal('ROUTE_INTENT_MISMATCH', context.step_id)
        step = next((s for s in self.portfolio['steps'] if s['id'] == context.step_id), None)
        if step is None:
            raise Refusal('UNKNOWN_CANONICAL_STEP', context.step_id)
        if not set(step['mandatory_gate_programs']).issubset(context.required_gates):
            raise Refusal('REQUIRED_GATES_DROPPED', context.step_id)
        self._measure_versions(context)
        adapters = self.registry.adapters(context.step_id)
        rows = [{**a.identity(), 'admission': self._admission(a, context),
                 'applicability': a.applicability,
                 'applicability_reason': a.applicability_reason,
                 'availability_reason': a.availability_reason,
                 'available': a.available, 'qualified': a.qualified,
                 'own_no_tool_reason': a.own_no_tool_reason,
                 'cpus': a.cpus, 'ram_mb': a.ram_mb,
                 'license_id': a.license_id} for a in adapters]
        ready = [a for a, row in zip(adapters, rows) if row['admission'] == 'READY']
        external = [a for a in adapters if a.role == 'producer' and a.tool_id != 'vibeic']
        own = [a for a in ready if a.tool_id == 'vibeic']
        # An unavailable, unknown or failed external producer is still suitable;
        # it cannot justify an own implementation. Incomplete portfolio coverage
        # is NOT proof of no external tool, so the explicit scoped reason is needed.
        if own and (any(a.applicability != 'inapplicable' for a in external) or
                    any(not a.own_no_tool_reason for a in own)):
            ready = [a for a in ready if a.tool_id != 'vibeic']
            for row in rows:
                if row['tool_id'] == 'vibeic' and row['admission'] == 'READY':
                    row['admission'] = 'OWN_TOOL_NOT_JUSTIFIED'
        acceptance = self.acceptance_contract(context, step)
        common = dict(mode=selected_mode, mode_intent=self._mode_intent(selected_mode),
                      intent_label=context.intent_label, request_digest=context.request_digest,
                      binding=binding, acceptance=acceptance,
                      acceptance_digest=_hash(acceptance),
                      route_receipt=dict(context.route_receipt),
                      route_receipt_sha256=_hash(context.route_receipt)
                      if context.route_receipt else None)
        if not ready:
            return dict(**common, arms=[], portfolio=rows,
                        status='NOT_MEASURED', reason='NO_RUNNABLE_ADAPTER')
        if any(dict(a.objective) != dict(context.objective) for a in ready):
            raise Refusal('UNEQUAL_OBJECTIVES', context.step_id)
        if any(not set(step['required_output_contract']).issubset(a.output_contract)
               for a in ready):
            raise Refusal('OUTPUT_CONTRACT_UNBOUND', context.step_id)
        rank = lambda a: (0 if a.tool_id == 'librelane' else
                          1 if a.tool_id in ('openroad', 'openroad_fork') else
                          3 if a.tool_id == 'vibeic' else 2, a.arm_id)
        ready.sort(key=rank)
        reason = 'APPLICABLE_AVAILABLE_QUALIFIED_PRIORITY'
        if selected_mode == 'default-mode':
            if superiority:
                preferred = next((a for a in ready if a.arm_id == superiority.preferred), None)
                reference = next((a for a in ready if a.arm_id == superiority.reference), None)
                if not preferred or not reference or reference != ready[0] or (
                        dict(superiority.binding) != binding or
                        superiority.direction not in ('min', 'max') or
                        superiority.direction != context.objective.get('direction') or
                        superiority.metric != context.objective.get('metric')):
                    raise Refusal('SUPERIORITY_UNBOUND', context.step_id)
                measured = []
                for arm in (preferred, reference):
                    path = superiority.receipts.get(arm.arm_id)
                    if not path:
                        raise Refusal('SUPERIORITY_UNMEASURED', arm.arm_id)
                    receipt = json.loads(Path(path).read_text())
                    if receipt.get('adapter') != arm.identity() or receipt.get('binding') != binding:
                        raise Refusal('SUPERIORITY_STALE', arm.arm_id)
                    if receipt.get('status') != 'ELIGIBLE':
                        raise Refusal('SUPERIORITY_UNMEASURED', arm.arm_id)
                    self._eligible(receipt, context, arm)
                    if asdict(arm.validate(Path(receipt['output_root']), binding)) != receipt['evidence']:
                        raise Refusal('SUPERIORITY_EVIDENCE_CHANGED', arm.arm_id)
                    value = receipt.get('evidence', {}).get('metrics', {}).get(superiority.metric)
                    if type(value) not in (int, float) or not math.isfinite(value):
                        raise Refusal('SUPERIORITY_UNMEASURED', arm.arm_id)
                    measured.append(value)
                better = measured[0] < measured[1] if superiority.direction == 'min' else measured[0] > measured[1]
                if not better:
                    raise Refusal('SUPERIORITY_NOT_PROVEN', context.step_id)
                ready = [preferred]
                reason = 'CURRENT_INPUT_MEASURED_SUPERIORITY'
            else:
                ready = ready[:1]
        # Ultra executes useful distinct families. Same-family orchestration
        # wrappers remain disclosed but do not claim an independent engine.
        if selected_mode == 'ultra-mode':
            distinct = []
            identities = []
            for arm in ready:
                identity = self._provider_identity(arm)
                if (identity in identities or
                        any(set(arm.engine_families) & set(a.engine_families)
                            for a in distinct)):
                    for row in rows:
                        if row['arm_id'] == arm.arm_id:
                            row['admission'] = 'SAME_ENGINE_FAMILY'
                    continue
                distinct.append(arm)
                identities.append(identity)
            ready = distinct
        return dict(**common,
                    arms=[a.arm_id for a in ready], portfolio=rows,
                    status='PLANNED', reason=reason,
                    independence={a.arm_id: list(a.engine_families) for a in ready})

    def run(self, context: Context, output: Path, execution_mode: str | None = None,
            *, cancel: threading.Event | None = None,
            superiority: Superiority | None = None) -> dict:
        output = Path(output).resolve()
        try:
            output.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            raise Refusal('RUN_OUTPUT_UNAVAILABLE', str(output)) from exc
        run_id = uuid.uuid4().hex
        cancel = cancel or threading.Event()
        try:
            plan = self.plan(context, execution_mode, superiority)
        except Refusal as exc:
            _write(output / 'refusal.json', dict(status='REFUSED', reason=exc.code,
                                               detail=str(exc), run_id=run_id,
                                               portfolio=self.portfolio))
            if exc.code in ('CONTROLLER_ISSUANCE_REQUIRED', 'CONTEXT_IMPLEMENTATION_UNTRUSTED'):
                return self._authority_refusal(output, exc)
            raise
        plan.update(run_id=run_id, budget=asdict(self.budget),
                    public_portfolio=self.portfolio,
                    public_portfolio_sha256=_hash(self.portfolio),
                    run_root=str(output),
                    superiority=None if superiority is None else {
                        **asdict(superiority),
                        'receipts': {k: str(v) for k, v in superiority.receipts.items()}})
        frozen = dict(schema=1, run_id=run_id, step_id=context.step_id,
                      ic_ip_path=context.ic_ip_path,
                      intent_label=context.intent_label,
                      request_digest=context.request_digest,
                      source_sha=context.source_sha,
                      input_digest=_hash(plan['binding']['inputs']),
                      input_manifest=dict(plan['binding']['inputs']),
                      objective_digest=_hash(plan['binding']['objective']),
                      acceptance_digest=plan['acceptance_digest'],
                      acceptance=plan['acceptance'],
                      route_receipt=plan['route_receipt'],
                      route_receipt_sha256=plan['route_receipt_sha256'],
                      mode_intent=plan['mode_intent'])
        _write(output / 'frozen-work.json', frozen)
        plan['frozen_work_digest'] = digest(output / 'frozen-work.json')
        plan['execution_issuance'] = self._execution_issuance(context, plan)
        _write(output / 'plan.json', plan)
        _write(output / 'issued-plan.json', _issue_sealed(output / 'issued-plan.json', plan))
        if not plan['arms']:
            comparison = self._comparison_receipt(output, plan, {})
            _write(output / 'result.json', plan)
            return plan
        arms = {a.arm_id: a for a in self.registry.adapters(context.step_id)}
        condition = threading.Condition()
        usage = {'cpus': 0, 'ram_mb': 0, 'licenses': {}}
        available_cpus = set(os.sched_getaffinity(0))

        def work(arm: Adapter):
            with condition:
                def fits():
                    return (len(available_cpus) >= arm.cpus and
                            usage['cpus'] + arm.cpus <= self.budget.cpus and
                            usage['ram_mb'] + arm.ram_mb <= self.budget.ram_mb and
                            (not arm.license_id or usage['licenses'].get(arm.license_id, 0) <
                             self.budget.licenses[arm.license_id]))
                while not fits() and not cancel.is_set():
                    condition.wait(.02)
                if not cancel.is_set():
                    usage['cpus'] += arm.cpus
                    usage['ram_mb'] += arm.ram_mb
                    if arm.license_id:
                        usage['licenses'][arm.license_id] = usage['licenses'].get(arm.license_id, 0) + 1
                    cpuset = sorted(available_cpus)[:arm.cpus]
                    available_cpus.difference_update(cpuset)
                else:
                    return self._run_arm(arm, context, plan, output, cancel)
            try:
                return self._run_arm(arm, context, plan, output, cancel, cpuset)
            finally:
                with condition:
                    usage['cpus'] -= arm.cpus
                    usage['ram_mb'] -= arm.ram_mb
                    if arm.license_id:
                        usage['licenses'][arm.license_id] -= 1
                    available_cpus.update(cpuset)
                    condition.notify_all()

        receipts = {}
        with ThreadPoolExecutor(max_workers=self.budget.workers) as pool:
            futures = {pool.submit(work, arms[i]): i for i in plan['arms']}
            for future in as_completed(futures):
                receipts[futures[future]] = future.result()
        comparison = self._comparison_receipt(output, plan, receipts)
        candidate_statuses = {i: r['status'] for i, r in receipts.items()}
        if plan['mode'] == 'default-mode':
            # Default is program-selected: the planner has already admitted
            # exactly one priority arm. A sole eligible result follows the
            # same sealed adoption chain, with a controller-issued choice
            # witness so no AI choice file or review callback is required.
            eligible = comparison['eligible_arms']
            if len(plan['arms']) != 1:
                raise Refusal('DEFAULT_ARM_CARDINALITY', context.step_id)
            if len(eligible) == 1:
                arm_id = eligible[0]['arm_id']
                choice = dict(arm_id=arm_id,
                              binding=plan['binding'],
                              receipt_sha256=eligible[0]['receipt_sha256'],
                              reviewer='program-default-controller',
                              rationale='Planner priority selected the sole default arm.')
                try:
                    adopted = self.adopt(context, output, choice,
                                         _candidate_statuses=candidate_statuses)
                except Refusal as exc:
                    refusal = json.loads((output / 'adoption.json').read_text())
                    refusal.update(candidate_statuses=candidate_statuses,
                                   mode=plan['mode'], mode_intent=plan['mode_intent'],
                                   selected=None, reason=exc.code)
                    _write(output / 'result.json', refusal)
                    return refusal
                _write(output / 'result.json', adopted)
                return adopted
            # A measured failure or an unavailable result remains terminal and
            # cannot be promoted into adoption by the default path.
            status = candidate_statuses.get(plan['arms'][0], 'NOT_MEASURED')
            summary = dict(run_id=run_id, status=status, candidate_statuses=candidate_statuses,
                           mode=plan['mode'], mode_intent=plan['mode_intent'], selected=None,
                           frozen_work_digest=plan['frozen_work_digest'],
                           comparison_digest=comparison['digest'])
            _write(output / 'result.json', summary)
            return summary
        terminal_status = ('FAIL' if 'FAIL' in candidate_statuses.values()
                           else ('AWAITING_AI_SELECTION' if comparison['eligible_arms']
                                 else 'NOT_MEASURED'))
        summary = dict(run_id=run_id,
                       status=terminal_status,
                       candidate_statuses=candidate_statuses,
                       mode=plan['mode'], mode_intent=plan['mode_intent'], selected=None,
                       frozen_work_digest=plan['frozen_work_digest'],
                       comparison_digest=comparison['digest'])
        _write(output / 'result.json', summary)
        return summary

    def _objective_winner(self, plan: dict, root: Path, eligible: list[dict],
                          context: Context) -> str | None:
        """Return the deterministic objective winner among current eligible arms."""
        objective = plan.get('acceptance', {}).get('objective') or {}
        metric = objective.get('metric')
        direction = objective.get('direction')
        order = {arm_id: index for index, arm_id in enumerate(plan.get('arms', []))}
        values = []
        for row in eligible:
            arm_id = row.get('arm_id')
            try:
                receipt_path = root / str(arm_id) / 'receipt.json'
                if digest(receipt_path) != row.get('receipt_sha256'):
                    raise Refusal('ARM_RECEIPT_DIGEST_MISMATCH', str(arm_id))
                receipt = json.loads(receipt_path.read_text())
                value = (receipt.get('evidence') or {}).get('metrics', {}).get(metric)
            except (OSError, ValueError, TypeError):
                raise Refusal('OBJECTIVE_EVIDENCE_INVALID', str(arm_id))
            arm = next((candidate for candidate in self.registry.adapters(context.step_id)
                        if candidate.arm_id == arm_id), None)
            if arm is None:
                raise Refusal('OBJECTIVE_EVIDENCE_INVALID', str(arm_id))
            self._eligible(receipt, context, arm)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise Refusal('OBJECTIVE_EVIDENCE_INVALID', str(arm_id))
            values.append((value, order.get(arm_id, len(order)), arm_id))
        if not values:
            return None
        if direction == 'min':
            return min(values, key=lambda item: (item[0], item[1]))[2]
        if direction == 'max':
            return max(values, key=lambda item: (item[0], -item[1]))[2]
        raise Refusal('OBJECTIVE_INVALID', str(direction))

    @staticmethod
    def _comparison_receipt(root: Path, plan: dict, receipts: Mapping[str, dict]) -> dict:
        """Issue a complete, ordered comparison over this run's arm set.

        The comparison is descriptive until an explicit AI choice arrives.  A
        missing or edited arm cannot disappear from the ordered set without
        making adoption refuse.
        """
        ordered = []
        eligible = []
        for arm_id in plan['arms']:
            path = root / arm_id / 'receipt.json'
            if not path.is_file():
                ordered.append(dict(arm_id=arm_id, receipt_sha256=None, status='NOT_MEASURED'))
                continue
            observed = receipts.get(arm_id)
            if observed is None:
                observed = json.loads(path.read_text())
            sha = digest(path)
            item = dict(arm_id=arm_id, receipt_sha256=sha,
                        status=observed.get('status', 'NOT_MEASURED'))
            ordered.append(item)
            if item['status'] == 'ELIGIBLE':
                eligible.append(dict(arm_id=arm_id, receipt_sha256=sha))
        comparison = dict(schema=1, run_id=plan['run_id'], step_id=plan['binding']['step_id'],
                          frozen_work_digest=plan['frozen_work_digest'],
                          mode_intent=plan['mode_intent'], intent_label=plan['intent_label'],
                          request_digest=plan['request_digest'],
                          arm_receipts=ordered, eligible_arms=eligible,
                          selection_criterion=dict(objective=plan['acceptance']['objective'],
                                                   eligible_only=True,
                                                   ordered_by='objective.metric',
                                                   tie_break='plan.arm_order'),
                          status=('FAIL' if any(item['status'] == 'FAIL' for item in ordered)
                                  else ('AWAITING_AI_SELECTION'
                                        if plan['mode'] == 'ultra-mode' and eligible
                                        else ('PROGRAM_DEFAULT_READY' if eligible else 'NOT_MEASURED'))))
        _write(root / 'comparison.json', comparison)
        _write(root / 'issued-comparison.json', _issue_sealed(root / 'issued-comparison.json', comparison))
        comparison['digest'] = digest(root / 'comparison.json')
        return comparison

    def _run_arm(self, arm: Adapter, context: Context, plan: dict, root: Path,
                 cancel: threading.Event, cpuset: list[int] | None = None) -> dict:
        directory = root / arm.arm_id
        try:
            directory.mkdir()
        except OSError as exc:
            raise Refusal('ARM_OUTPUT_UNAVAILABLE', str(directory)) from exc
        inputs, outputs = directory / 'inputs', directory / 'outputs'
        inputs.mkdir(); outputs.mkdir()
        receipt = dict(run_id=plan['run_id'], arm_id=arm.arm_id,
                       adapter=arm.identity(), binding=plan['binding'],
                       frozen_work_digest=plan['frozen_work_digest'],
                       intent_label=plan['intent_label'], request_digest=plan['request_digest'],
                       executor=dict(tool_id=arm.tool_id, tool_version=arm.tool_version,
                                     source_sha=arm.source_sha),
                       engine_source_families=list(arm.engine_families),
                       resource_facts=dict(cpus=arm.cpus, ram_mb=arm.ram_mb,
                                           budget=asdict(self.budget), cpuset=cpuset),
                       admission=next((row for row in plan['portfolio']
                                       if row.get('arm_id') == arm.arm_id), {}),
                       output_root=str(outputs), input_root=str(inputs),
                       status='NOT_MEASURED', reason='NOT_STARTED', processes=[],
                       evidence=None, started_ns=time.monotonic_ns())
        def frozen_binding():
            actual = {str(p.relative_to(inputs)): digest(p) for p in inputs.rglob('*') if p.is_file()}
            if actual != plan['binding']['inputs'] or any(p.is_symlink() for p in inputs.rglob('*')):
                raise Refusal('FROZEN_INPUT_CHANGED', arm.arm_id)
        try:
            self._verify_execution_issuance(context, plan)
            if self._context_binding(context) != plan['binding']:
                raise Refusal('CURRENT_INPUT_CHANGED', arm.arm_id)
            for name, source in context.inputs.items():
                target = inputs / _relative(name)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                target.chmod(0o444)
            frozen_binding()
            for component in arm.components:
                if cancel.is_set():
                    raise Refusal('CANCELLED', arm.arm_id)
                self._source_current(arm)
                self._verify_execution_issuance(context, plan)
                frozen_binding()
                argv = [v.replace('{inputs}', str(inputs)).replace('{outputs}', str(outputs))
                        for v in component.argv]
                executable = shutil.which(argv[0])
                if not executable or str(Path(executable).resolve()) not in arm.source_files:
                    raise Refusal('EXECUTABLE_UNBOUND', argv[0])
                argv[0] = str(Path(executable).resolve())
                record = dict(component=component.name, argv=argv, rc=None,
                              executable_path=argv[0],
                              executable_sha256=digest(Path(argv[0])),
                              started_ns=time.monotonic_ns())
                receipt['processes'].append(record)
                stdout = directory / (component.name + '.stdout')
                stderr = directory / (component.name + '.stderr')
                # A separate Python launcher sets rlimits/affinity before exec.
                # No preexec_fn in a threaded parent (which can deadlock).
                launcher = ("import os,resource,sys; "
                            "resource.setrlimit(resource.RLIMIT_AS,(int(sys.argv[1]),int(sys.argv[1]))); "
                            "os.sched_setaffinity(0,set(map(int,sys.argv[2].split(',')))); "
                            "os.execvpe(sys.argv[3],sys.argv[3:],os.environ)")
                import sys
                cpuset = cpuset or sorted(os.sched_getaffinity(0))[:arm.cpus]
                with stdout.open('wb') as out, stderr.open('wb') as err:
                    process = subprocess.Popen(
                        [sys.executable, '-c', launcher, str(arm.ram_mb * 1024 * 1024),
                         ','.join(map(str, cpuset)), *argv],
                        cwd=outputs, stdout=out, stderr=err, start_new_session=True,
                        env={**os.environ, 'OMP_NUM_THREADS': str(arm.cpus),
                             'OPENBLAS_NUM_THREADS': str(arm.cpus),
                             'VIBEIC_EXECUTION_BINDING': json.dumps(plan['binding']),
                             'VIBEIC_ARM_ID': arm.arm_id})
                    record['pid'] = process.pid
                    stop = None
                    try:
                        deadline = time.monotonic() + component.timeout_s
                        while process.poll() is None:
                            if cancel.is_set() or time.monotonic() >= deadline:
                                stop = 'CANCELLED' if cancel.is_set() else 'DEADLINE'
                                break
                            time.sleep(.01)
                    finally:
                        # Reap this component's process group, including any
                        # residual descendants after a parent exits normally.
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait()
                    record.update(rc=process.returncode, ended_ns=time.monotonic_ns(),
                                  stdout=str(stdout), stderr=str(stderr),
                                  stdout_sha256=digest(stdout), stderr_sha256=digest(stderr),
                                  stop_reason=stop)
                if stop:
                    raise Refusal(stop, component.name)
                if record['rc'] != 0:
                    raise Refusal('PROCESS_ERROR', f'{component.name}: rc={record["rc"]}')
                frozen_binding()
            self._source_current(arm)
            evidence = arm.validate(outputs, plan['binding'])
            receipt['evidence'] = asdict(evidence)
            receipt['status'] = evidence.verdict
            receipt['reason'] = 'ADAPTER_EVIDENCE'
            output_digest = _hash(receipt['evidence'].get('outputs', {}))
            receipt['gate_receipts'] = {
                process['component']: dict(
                    gate=process['component'], argv=list(process['argv']),
                    argv_sha256=_hash(process['argv']),
                    executable_path=process.get('executable_path'),
                    executable_sha256=process.get('executable_sha256'),
                    source_sha=arm.source_sha, tool_id=arm.tool_id,
                    tool_version=arm.tool_version,
                    source_manifest_sha256=_hash(dict(arm.source_files)),
                    inputs=dict(plan['binding']['inputs']), rc=process['rc'],
                    output_digest=output_digest)
                for process in receipt['processes']
            }
            self._eligible(receipt, context, arm)
            receipt['status'] = 'ELIGIBLE'
        except Refusal as exc:
            receipt.update(status='FAIL' if exc.code == 'GATE_FAIL' else 'NOT_MEASURED',
                           reason=exc.code, detail=str(exc))
        except Exception as exc:
            receipt.update(status='NOT_MEASURED', reason='ADAPTER_ERROR', detail=repr(exc))
        receipt['ended_ns'] = time.monotonic_ns()
        receipt['honest_verdict'] = receipt['status']
        receipt['outputs'] = (receipt.get('evidence') or {}).get('outputs', {})
        # This completion is issued from observed Popen.wait results. Gate
        # evidence remains separately reconsumed; a source-issued process rc0
        # does not grant PASS or replace a failed/unmeasured output consumer.
        completion = {k: receipt[k] for k in ('run_id', 'arm_id', 'binding',
                      'adapter', 'processes', 'input_root', 'output_root')}
        completion['evidence'] = receipt.get('evidence')
        completion['gate_receipts'] = receipt.get('gate_receipts')
        completion.update(actual_status=receipt['status'], actual_reason=receipt['reason'],
                          ended_ns=receipt['ended_ns'], run_root=str(root))
        _write(directory / 'issued-completion.json', _issue_sealed(directory / 'issued-completion.json', completion))
        _write(directory / 'receipt.json', receipt)
        return receipt

    @staticmethod
    def _execution_authority(root: Path, plan: dict, receipt: dict, arm: Adapter) -> None:
        issued_plan = _issued(root / 'issued-plan.json')
        if issued_plan != plan or plan.get('run_root') != str(root):
            raise Refusal('ISSUED_PLAN_MISMATCH', arm.arm_id)
        completion = _issued(root / arm.arm_id / 'issued-completion.json')
        fields = ('run_id', 'arm_id', 'binding', 'adapter', 'processes', 'input_root', 'output_root')
        if (completion.get('run_root') != str(root) or
                completion.get('run_id') != plan['run_id'] or
                any(completion.get(k) != receipt.get(k) for k in fields)):
            raise Refusal('EXECUTION_AUTHORITY_MISMATCH', arm.arm_id)
        processes = completion['processes']
        if len(processes) != len(arm.components) or any(
                p.get('rc') != 0 or p.get('stop_reason') or not p.get('pid') or
                not p.get('ended_ns') for p in processes):
            raise Refusal('ISSUED_EXECUTION_INCOMPLETE', arm.arm_id)
        if (completion.get('evidence') != receipt.get('evidence') or
                completion.get('gate_receipts') != receipt.get('gate_receipts')):
            raise Refusal('EVIDENCE_CHANGED', arm.arm_id)

    def _current_admission(self, context: Context, plan: dict, arm: Adapter) -> None:
        if self._admission(arm, context) != 'READY':
            raise Refusal('CURRENT_ADMISSION_REJECTED', arm.arm_id)
        override = plan.get('superiority')
        if override:
            override = Superiority(**{**override, 'receipts': {
                k: Path(v) for k, v in override['receipts'].items()}})
        current = self.plan(context, plan['mode'], override)
        if arm.arm_id not in current['arms']:
            raise Refusal('CURRENT_POLICY_REJECTED', arm.arm_id)

    def _verify_receipt_chain(self, root: Path, plan: dict, context: Context) -> dict:
        self._validate_live_portfolio(plan)
        self._verify_execution_issuance(context, plan)
        frozen_path = root / 'frozen-work.json'
        comparison_path = root / 'comparison.json'
        if not frozen_path.is_file() or not comparison_path.is_file():
            raise Refusal('RECEIPT_CHAIN_INCOMPLETE', plan.get('run_id', ''))
        if digest(frozen_path) != plan.get('frozen_work_digest'):
            raise Refusal('FROZEN_WORK_DIGEST_MISMATCH', str(frozen_path))
        frozen = json.loads(frozen_path.read_text())
        if (frozen.get('run_id') != plan.get('run_id') or
                frozen.get('step_id') != context.step_id or
                frozen.get('source_sha') != context.source_sha or
                frozen.get('mode_intent') != plan.get('mode_intent') or
                frozen.get('intent_label') != plan.get('intent_label') or
                frozen.get('request_digest') != plan.get('request_digest') or
                frozen.get('acceptance_digest') != plan.get('acceptance_digest') or
                frozen.get('input_manifest') != plan['binding'].get('inputs')):
            raise Refusal('FROZEN_WORK_UNBOUND', context.step_id)
        if self._context_binding(context) != plan.get('binding'):
            raise Refusal('CURRENT_INPUT_CHANGED', context.step_id)
        try:
            issued_comparison = _issued(root / 'issued-comparison.json')
        except Refusal as exc:
            raise Refusal('COMPARISON_AUTHORITY_INVALID', str(exc)) from exc
        comparison = json.loads(comparison_path.read_text())
        if issued_comparison != comparison:
            raise Refusal('COMPARISON_AUTHORITY_CHANGED', context.step_id)
        if (comparison.get('run_id') != plan.get('run_id') or
                comparison.get('frozen_work_digest') != plan.get('frozen_work_digest') or
                comparison.get('mode_intent') != plan.get('mode_intent') or
                comparison.get('intent_label') != plan.get('intent_label') or
                comparison.get('request_digest') != plan.get('request_digest')):
            raise Refusal('COMPARISON_UNBOUND', context.step_id)
        rows = comparison.get('arm_receipts')
        if not isinstance(rows, list) or [row.get('arm_id') for row in rows] != plan.get('arms'):
            raise Refusal('INCOMPLETE_ARM_SET', context.step_id)
        eligible = []
        for row in rows:
            arm_id = row.get('arm_id')
            receipt_path = root / str(arm_id) / 'receipt.json'
            if not receipt_path.is_file():
                raise Refusal('INCOMPLETE_ARM_SET', str(arm_id))
            observed = json.loads(receipt_path.read_text())
            if digest(receipt_path) != row.get('receipt_sha256'):
                if observed.get('status') == 'ELIGIBLE' and row.get('status') != 'ELIGIBLE':
                    raise Refusal('EVIDENCE_CHANGED', str(arm_id))
                raise Refusal('ARM_RECEIPT_DIGEST_MISMATCH', str(arm_id))
            if observed.get('status') == 'ELIGIBLE':
                eligible.append(dict(arm_id=arm_id, receipt_sha256=row['receipt_sha256']))
            elif row.get('status') == 'ELIGIBLE':
                raise Refusal('COMPARISON_ELIGIBILITY_CHANGED', str(arm_id))
        if comparison.get('eligible_arms') != eligible:
            raise Refusal('INCOMPLETE_ARM_SET', context.step_id)
        if (comparison.get('status') == 'FAIL' and
                any(row.get('status') == 'FAIL' for row in rows)):
            raise Refusal('GATE_FAIL', context.step_id)
        return dict(frozen=frozen, comparison=comparison,
                    comparison_digest=digest(comparison_path), eligible=eligible)

    @staticmethod
    def _selected_generation(root: Path, receipt: dict) -> dict:
        parent = root / 'selected'
        if parent.is_symlink():
            raise Refusal('SELECTED_NAMESPACE_UNSAFE', str(parent))
        parent.mkdir(exist_ok=True)
        generation = uuid.uuid4().hex
        target = parent / generation
        target.mkdir()
        hashes = receipt['evidence']['outputs']
        for name, expected in hashes.items():
            source = Path(receipt['output_root']) / _relative(name)
            content = source.read_bytes()
            if hashlib.sha256(content).hexdigest() != expected:
                raise Refusal('SELECTED_ARTIFACT_CHANGED', name)
            dest = target / _relative(name)
            write_bytes(dest, content)
            dest.chmod(0o444)
        manifest = dict(generation=generation, directory=str(target),
                        run_id=receipt['run_id'], arm_id=receipt['arm_id'],
                        binding=receipt['binding'], outputs=hashes)
        _write(target / 'manifest.json', _issue_sealed(target / 'manifest.json', manifest))
        return manifest

    @staticmethod
    def _generation_current(generation: dict) -> None:
        directory = Path(generation['directory'])
        if _issued(directory / 'manifest.json') != generation:
            raise Refusal('SELECTED_MANIFEST_CHANGED', generation['generation'])
        for name, expected in generation['outputs'].items():
            path = directory / _relative(name)
            if path.is_symlink() or not path.is_file() or digest(path) != expected:
                raise Refusal('SELECTED_GENERATION_CHANGED', name)

    @staticmethod
    def _source_current(arm: Adapter) -> None:
        for name, blob in arm.verified_source_blobs.items():
            if _blob_bytes(Path(name)) != blob:
                raise Refusal('ADAPTER_SOURCE_MISMATCH', name)
        for name, expected in arm.source_files.items():
            path = Path(name)
            if not path.is_file() or path.is_symlink() or digest(path) != expected:
                raise Refusal('ADAPTER_SOURCE_MISMATCH', name)
        for component in arm.components:
            if arm.invocation_sources.get(component.name) != _invocation_sources(component):
                raise Refusal('ENTRY_SOURCE_CHANGED', component.name)

    def _eligible(self, receipt: dict, context: Context, arm: Adapter) -> None:
        binding = self._context_binding(context)
        if (receipt.get('binding') != binding or receipt.get('adapter') != arm.identity() or
                receipt.get('intent_label') != context.intent_label or
                receipt.get('request_digest') != context.request_digest):
            raise Refusal('STALE_OR_UNBOUND_RECEIPT', arm.arm_id)
        Controller._source_current(arm)
        processes = receipt.get('processes', [])
        if len(processes) != len(arm.components) or any(
                p.get('rc') != 0 or p.get('stop_reason') or not p.get('pid') or
                not p.get('ended_ns') for p in processes):
            raise Refusal('PROCESS_NOT_COMPLETE', arm.arm_id)
        for process in processes:
            for channel in ('stdout', 'stderr'):
                p = Path(process[channel])
                if not p.is_file() or digest(p) != process[channel + '_sha256']:
                    raise Refusal('PROCESS_LOG_CHANGED', arm.arm_id)
        frozen = Path(receipt['input_root'])
        if not frozen.is_dir() or any(p.is_symlink() for p in frozen.rglob('*')) or {
                str(p.relative_to(frozen)): digest(p) for p in frozen.rglob('*') if p.is_file()
                } != binding['inputs']:
            raise Refusal('FROZEN_INPUT_CHANGED', arm.arm_id)
        evidence = receipt.get('evidence') or {}
        if evidence.get('binding') != binding:
            raise Refusal('EVIDENCE_UNBOUND', arm.arm_id)
        gates = evidence.get('gates', {})
        verdict = evidence.get('verdict')
        gate_receipts = receipt.get('gate_receipts') or {}
        for gate in context.required_gates:
            gate_receipt = gate_receipts.get(gate)
            if not isinstance(gate_receipt, dict):
                raise Refusal('GATE_EXECUTION_UNBOUND', gate)
            matching = next((process for process in processes
                             if process.get('component') == gate), None)
            if (matching is None or gate_receipt.get('argv') != matching.get('argv') or
                    gate_receipt.get('argv_sha256') != _hash(matching.get('argv')) or
                    gate_receipt.get('executable_path') != matching.get('executable_path') or
                    gate_receipt.get('executable_sha256') != matching.get('executable_sha256') or
                    gate_receipt.get('source_sha') != arm.source_sha or
                    gate_receipt.get('tool_id') != arm.tool_id or
                    gate_receipt.get('tool_version') != arm.tool_version or
                    gate_receipt.get('source_manifest_sha256') != _hash(dict(arm.source_files)) or
                    gate_receipt.get('inputs') != binding['inputs'] or
                    gate_receipt.get('rc') != 0 or
                    gate_receipt.get('output_digest') != _hash(evidence.get('outputs', {}))):
                raise Refusal('GATE_EXECUTION_UNBOUND', gate)
        if verdict == 'FAIL' or any(gates.get(k) == 'FAIL' for k in context.required_gates):
            raise Refusal('GATE_FAIL', arm.arm_id)
        if verdict != 'PASS' or any(gates.get(k) != 'PASS' for k in context.required_gates):
            raise Refusal('GATE_NOT_MEASURED', arm.arm_id)
        outputs = Path(receipt['output_root']).resolve()
        hashes = evidence.get('outputs', {})
        if not hashes or not set(arm.required_outputs).issubset(hashes):
            raise Refusal('OUTPUT_INCOMPLETE', arm.arm_id)
        for name, expected in hashes.items():
            path = outputs / _relative(name)
            if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(outputs):
                raise Refusal('OUTPUT_OUTSIDE_ARM', name)
            if digest(path) != expected:
                raise Refusal('OUTPUT_DIGEST_MISMATCH', name)
        if any(type(v) not in (int, float) or not math.isfinite(v)
               for v in evidence.get('metrics', {}).values()):
            raise Refusal('INVALID_METRIC', arm.arm_id)
        metric = context.objective.get('metric')
        value = evidence.get('metrics', {}).get(metric)
        if type(value) not in (int, float) or not math.isfinite(value):
            raise Refusal('OBJECTIVE_EVIDENCE_INVALID', arm.arm_id)

    def adopt(self, context: Context, root: Path, choice: Mapping[str, object] | None,
              *, _candidate_statuses: Mapping[str, str] | None = None) -> dict:
        """AI must supply an explicit receipt-bound decision; no rc0/PASS shortcut.

        All evidence is reconsumed at adoption, including the adapter's actual
        output validator. Receipt editing cannot change that validator's answer.
        Adoption records the result; it does not overwrite native project outputs.
        """
        root = Path(root).resolve()
        adoption = dict(run_id=None, status='REFUSED', selected=None,
                        ai_choice=dict(choice) if choice is not None else None)
        published = False

        def publish(value: dict) -> None:
            """Issue adoption exactly once from this live controller call.

            There is deliberately no importable writer.  Re-signing an
            existing path would let a caller co-update a selected manifest,
            winner and adoption document after the original producer receipt.
            The sealed store rejects any second payload for the same path.
            """
            nonlocal published
            if published:
                raise Refusal('ISSUED_AUTHORITY_REISSUE', str(root))
            _write(root / 'adoption.json', value)
            _write(root / 'program_adoption.json', value)
            _write(root / 'issued-adoption.json',
                   _issue_sealed(root / 'issued-adoption.json', value))
            published = True

        try:
            self._context_binding(context)
            plan = json.loads((root / 'plan.json').read_text())
            adoption['run_id'] = plan['run_id']
            if not choice or not all(isinstance(choice.get(k), str) and choice[k].strip()
                                     for k in ('arm_id', 'receipt_sha256', 'rationale', 'reviewer')):
                raise Refusal('AI_CHOICE_MISSING', context.step_id)
            if choice.get('binding') != self._context_binding(context) or plan['binding'] != self._context_binding(context):
                raise Refusal('AI_CHOICE_UNBOUND', context.step_id)
            arm_id = choice['arm_id']
            if arm_id not in plan['arms']:
                raise Refusal('AI_CHOICE_NOT_CANDIDATE', str(arm_id))
            path = root / str(arm_id) / 'receipt.json'
            if not path.is_file() or digest(path) != choice['receipt_sha256']:
                raise Refusal('AI_RECEIPT_DIGEST_MISMATCH', str(arm_id))
            # A default run may already have completed the program adoption
            # chain.  Re-consuming that immutable chain is allowed; issuing a
            # second payload for the same path is not.  Only take this fast
            # path when all three public adoption documents still equal the
            # original sealed payload.
            adoption_path = root / 'adoption.json'
            issued_path = root / 'issued-adoption.json'
            if adoption_path.is_file() and issued_path.is_file():
                try:
                    existing = _issued(issued_path)
                    current = json.loads(adoption_path.read_text())
                    program = json.loads((root / 'program_adoption.json').read_text())
                except (OSError, ValueError, KeyError):
                    existing = None
                if (isinstance(existing, dict) and existing.get('status') == 'ADOPTED' and
                        current == existing and program == existing):
                    self.verify_adoption(context, root)
                    return existing
            receipt = json.loads(path.read_text())
            arm = next((a for a in self.registry.adapters(context.step_id) if a.arm_id == arm_id), None)
            if arm is None or receipt.get('run_id') != plan['run_id'] or receipt.get('status') != 'ELIGIBLE':
                raise Refusal('AI_CHOICE_INELIGIBLE', str(arm_id))
            if (Path(receipt['output_root']).resolve() != root / str(arm_id) / 'outputs' or
                    Path(receipt['input_root']).resolve() != root / str(arm_id) / 'inputs'):
                raise Refusal('WRONG_ARM_OUTPUT_SPACE', str(arm_id))
            self._execution_authority(root, plan, receipt, arm)
            chain = self._verify_receipt_chain(root, plan, context)
            adoption.update(frozen_work_digest=plan.get('frozen_work_digest'),
                            comparison_digest=chain['comparison_digest'],
                            arm_receipts=chain['comparison']['arm_receipts'],
                            intent_label=plan.get('intent_label'),
                            request_digest=plan.get('request_digest'))
            eligible = {row['arm_id']: row['receipt_sha256'] for row in chain['eligible']}
            if arm_id not in eligible or digest(path) != eligible[arm_id]:
                # A measured FAIL remains primary; an unmeasured arm cannot be
                # rescued by a reviewer string or a forged status edit.
                raise Refusal('AI_CHOICE_INELIGIBLE', str(arm_id))
            if plan.get('mode') == 'ultra-mode' and len(eligible) > 1:
                winner = self._objective_winner(
                    plan, root, chain['eligible'], context)
                if arm_id != winner:
                    raise Refusal('AI_CHOICE_WORSE_OBJECTIVE', str(arm_id))
            self._current_admission(context, plan, arm)
            self._eligible(receipt, context, arm)
            fresh = asdict(arm.validate(Path(receipt['output_root']), self._context_binding(context)))
            if fresh != receipt['evidence']:
                raise Refusal('EVIDENCE_CHANGED', str(arm_id))
            # A source validator can legitimately pause. Its return is not a
            # lease on the earlier input/executable/output bytes. Recheck after
            # it returns, then run the final consumer before capturing any
            # selected bytes. A callback that mutates an output after its first
            # answer is therefore observed by the second eligibility pass.
            self._eligible(receipt, context, arm)
            self._execution_authority(root, plan, receipt, arm)
            self._current_admission(context, plan, arm)
            self._eligible(receipt, context, arm)
            # The validator is the final consumer of the original result. It
            # may observe a late transform/validator failure after the earlier
            # admission checks, so its final answer is checked before copying
            # bytes into the selected generation.
            final_evidence = asdict(arm.validate(
                Path(receipt['output_root']), self._context_binding(context)))
            if (final_evidence != receipt['evidence'] or
                    final_evidence.get('verdict') != 'PASS' or
                    any(final_evidence.get('gates', {}).get(gate) != 'PASS'
                        for gate in context.required_gates)):
                raise Refusal('FINAL_EVIDENCE_CHANGED', str(arm_id))
            self._eligible(receipt, context, arm)
            generation = self._selected_generation(root, receipt)
            # Freeze/re-hash directly around publication. This catches a final
            # validator/transform that touched the selected generation and
            # leaves no successful adoption receipt in that case.
            self._generation_current(generation)
            if generation.get('outputs') != receipt['evidence'].get('outputs'):
                raise Refusal('SELECTED_GENERATION_UNBOUND', str(arm_id))
            adoption.update(status='ADOPTED', selected=arm_id,
                            evidence=receipt['evidence'],
                            independence=plan['independence'],
                            selected_generation=generation,
                            winner=dict(arm_id=arm_id, receipt_sha256=choice['receipt_sha256'],
                                        artifact_outputs=receipt['evidence']['outputs']),
                            acceptance_rerun=dict(acceptance_digest=plan['acceptance_digest'],
                                                  binding=self._context_binding(context),
                                                  evidence=final_evidence,
                                                  status='PASS'))
            if _candidate_statuses is not None:
                adoption.update(candidate_statuses=dict(_candidate_statuses),
                                mode=plan['mode'], mode_intent=plan['mode_intent'])
            publish(adoption)
            try:
                self._generation_current(generation)
            except Refusal:
                adoption.update(status='REFUSED', selected=None,
                                reason='SELECTED_GENERATION_CHANGED',
                                detail='selected bytes changed during adoption publication')
                raise
        except Refusal as exc:
            adoption.update(reason=exc.code, detail=str(exc))
            if exc.code in ('CONTROLLER_ISSUANCE_REQUIRED', 'CONTEXT_IMPLEMENTATION_UNTRUSTED'):
                return self._authority_refusal(root, exc)
            if not published:
                try:
                    publish(adoption)
                except Refusal as recording:
                    if recording.code == 'ISSUED_AUTHORITY_REISSUE':
                        # Preserve a visible refusal for a second consumer
                        # attempt without replacing the original issued PASS.
                        _write(root / 'adoption.json', adoption)
                        _write(root / 'program_adoption.json', adoption)
                    else:
                        raise Refusal('ADOPTION_RECORD_UNAVAILABLE',
                                      f'{exc}; {recording}') from recording
                except OSError as recording:
                    raise Refusal('ADOPTION_RECORD_UNAVAILABLE', f'{exc}; {recording}') from recording
            raise
        except (OSError, ValueError, KeyError, TypeError) as exc:
            adoption.update(status='REFUSED', selected=None,
                            reason='INVALID_ADOPTION_EVIDENCE', detail=str(exc))
            if not published:
                try:
                    publish(adoption)
                except Refusal as recording:
                    if recording.code == 'ISSUED_AUTHORITY_REISSUE':
                        _write(root / 'adoption.json', adoption)
                        _write(root / 'program_adoption.json', adoption)
                    else:
                        raise Refusal('ADOPTION_RECORD_UNAVAILABLE',
                                      f'{exc}; {recording}') from recording
                except OSError as recording:
                    raise Refusal('ADOPTION_RECORD_UNAVAILABLE', f'{exc}; {recording}') from recording
            raise Refusal('INVALID_ADOPTION_EVIDENCE', str(exc)) from exc
        return adoption

    def verify_adoption(self, context: Context, root: Path) -> dict:
        """Reconsume a completed adoption chain without changing project bytes."""
        root = Path(root).resolve()
        self._context_binding(context)
        try:
            adoption = json.loads((root / 'adoption.json').read_text())
            program = json.loads((root / 'program_adoption.json').read_text())
            issued = _issued(root / 'issued-adoption.json')
        except (OSError, ValueError, KeyError) as exc:
            raise Refusal('PROGRAM_ADOPTION_INVALID', str(exc)) from exc
        if adoption != program or adoption != issued:
            raise Refusal('PROGRAM_ADOPTION_CHANGED', str(root))
        plan = json.loads((root / 'plan.json').read_text())
        chain = self._verify_receipt_chain(root, plan, context)
        if adoption.get('frozen_work_digest') != plan.get('frozen_work_digest') or \
                adoption.get('comparison_digest') != chain['comparison_digest'] or \
                adoption.get('intent_label') != plan.get('intent_label') or \
                adoption.get('request_digest') != plan.get('request_digest'):
            raise Refusal('PROGRAM_ADOPTION_UNBOUND', context.step_id)
        winner = adoption.get('winner') or {}
        row = next((item for item in chain['eligible'] if item['arm_id'] == winner.get('arm_id')), None)
        if row is None or row['receipt_sha256'] != winner.get('receipt_sha256'):
            raise Refusal('PROGRAM_ADOPTION_WINNER_MISMATCH', context.step_id)
        if adoption.get('status') != 'ADOPTED':
            raise Refusal('PROGRAM_ADOPTION_NOT_ADOPTED', context.step_id)
        generation = adoption.get('selected_generation')
        if not isinstance(generation, dict):
            raise Refusal('PROGRAM_ADOPTION_GENERATION_MISSING', context.step_id)
        if (generation.get('run_id') != plan.get('run_id') or
                generation.get('arm_id') != winner.get('arm_id') or
                generation.get('outputs') != winner.get('artifact_outputs')):
            raise Refusal('PROGRAM_ADOPTION_GENERATION_UNBOUND', context.step_id)
        # Rehash every selected output on every verification.  The immutable
        # adoption receipt is not evidence that the selected bytes still match
        # the issued generation; a post-adoption mutation must refuse.
        self._generation_current(generation)
        arm_id = winner.get('arm_id')
        receipt_path = root / str(arm_id) / 'receipt.json'
        if not receipt_path.is_file() or digest(receipt_path) != winner.get('receipt_sha256'):
            raise Refusal('PROGRAM_ADOPTION_WINNER_MISMATCH', context.step_id)
        receipt = json.loads(receipt_path.read_text())
        arm = next((candidate for candidate in self.registry.adapters(context.step_id)
                    if candidate.arm_id == arm_id), None)
        if (arm is None or receipt.get('run_id') != plan.get('run_id') or
                receipt.get('status') != 'ELIGIBLE' or
                Path(receipt.get('output_root', '')).resolve() != root / str(arm_id) / 'outputs' or
                Path(receipt.get('input_root', '')).resolve() != root / str(arm_id) / 'inputs'):
            raise Refusal('PROGRAM_ADOPTION_EXECUTION_INVALID', context.step_id)
        original_outputs = (receipt.get('evidence') or {}).get('outputs')
        if (winner.get('artifact_outputs') != original_outputs or
                generation.get('outputs') != original_outputs):
            raise Refusal('PROGRAM_ADOPTION_GENERATION_UNBOUND', context.step_id)
        self._execution_authority(root, plan, receipt, arm)
        self._current_admission(context, plan, arm)
        self._eligible(receipt, context, arm)
        final_evidence = asdict(arm.validate(Path(receipt['output_root']), self._context_binding(context)))
        if (final_evidence != receipt.get('evidence') or
                final_evidence.get('verdict') != 'PASS' or
                any(final_evidence.get('gates', {}).get(gate) != 'PASS'
                    for gate in context.required_gates)):
            raise Refusal('FINAL_EVIDENCE_CHANGED', str(arm_id))
        return adoption
