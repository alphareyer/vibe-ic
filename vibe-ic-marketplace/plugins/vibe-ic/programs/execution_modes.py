"""Bounded execution-policy controller; BLOCKING at this API boundary.

This module has no production runner hooks or built-in EDA executors. Legacy
direct/librelane/dual switches remain owned by librelane_contract. Adapters
are trusted source-owned code, not executable commands imported from the public
portfolio. An adapter must validate its actual outputs and every required gate.
The controller binds that evidence to the inputs, implementation and process.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
import hashlib
import hmac
import inspect
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
import uuid

import os as _os                                                    # noqa: E402
import sys as _sys                                                  # noqa: E402

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))

from _atomic_artefact import write_bytes, write_json


# The issuer is owned by this live controller process, never a caller-supplied
# digest or a secret serialized beside editable run receipts. A new interpreter
# cannot adopt a previous issuer's run: durable external supervision is not wired.
_COMPLETION_KEY = secrets.token_bytes(32)
def _make_authority_ledger():
    """Read-only public index; enrollment stays in supervisor call closures.

    This is an in-process API boundary, not isolation against arbitrary Python
    reflection or replacement of trusted controller code. Workers receive only
    serialized facts, never the live enrollment capability.
    """
    entries: dict[str, str] = {}
    lock = threading.Lock()

    class AuthorityLedger:
        __slots__ = ()

        def __setitem__(self, path: str, payload: str) -> None:
            code = 'ISSUED_AUTHORITY_REWRITE' if path in entries else 'ISSUED_AUTHORITY_UNAVAILABLE'
            raise Refusal(code, path)

        def __getitem__(self, path: str) -> str:
            return entries[path]

        def get(self, path: str, default=None):
            return entries.get(path, default)

    def supervised(method):
        def invoke(self, *args, **kwargs):
            if '_issue' in kwargs:
                raise Refusal('ISSUED_AUTHORITY_UNAVAILABLE', 'caller supplied enrollment')

            def issue(path, payload):
                path = str(path)
                with lock:
                    if path in entries and entries[path] != payload:
                        raise Refusal('ISSUED_AUTHORITY_REWRITE', path)
                    entries[path] = payload

            return method(self, *args, **kwargs, _issue=issue)
        return invoke

    return AuthorityLedger(), supervised


_ISSUED_AUTHORITY, _supervised = _make_authority_ledger()
def _record_authority(path: Path | str, payload: str,
                      ledger=_ISSUED_AUTHORITY) -> None:
    """Compatibility boundary: callers cannot enroll first issuance."""
    ledger[str(path)] = payload


def _authority_payload(path: Path | str, default=None,
                       ledger=_ISSUED_AUTHORITY):
    return ledger.get(str(path), default)


_CONTROL_NAMES = frozenset({'.', '..', 'plan.json', 'result.json', 'adoption.json',
                            'refusal.json', 'issued-plan.json', 'selected'})


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


def _provider_identity(adapter: 'Adapter') -> tuple:
    """Executed component roots and their source-owned import closure.

    Caller labels and unrelated source-map members cannot establish a distinct
    implementation. The source map must bind every member of this closure;
    changing a supplied digest does not change the implementation we observed.
    Native-engine independence still needs native qualification evidence.
    """
    from execution_provider_catalog import source_closure
    roots = []
    for component in adapter.components:
        executable = Path(shutil.which(component.argv[0]) or component.argv[0]).resolve()
        files = [executable]
        for token in component.argv[1:]:
            path = Path(token)
            if path.is_absolute() and path.is_file():
                files.append(path.resolve())
        roots.append(tuple(str(p) for p in files))
    closure = source_closure(Path(p) for files in roots for p in files)
    observed = []
    for path in sorted(closure):
        actual = digest(path)
        if adapter.source_files.get(str(path)) != actual:
            raise Refusal('PROVIDER_DEPENDENCY_UNBOUND', str(path))
        observed.append((str(path), actual))
    return tuple(roots), tuple(observed)


def _seal(value: dict) -> dict:
    payload = json.loads(json.dumps(value))
    signature = hmac.new(_COMPLETION_KEY, _hash(payload).encode(), hashlib.sha256).hexdigest()
    return dict(payload=payload, signature=signature)


def _issued(path: Path) -> dict:
    document = json.loads(path.read_text())
    expected = hmac.new(_COMPLETION_KEY, _hash(document['payload']).encode(), hashlib.sha256).hexdigest()
    if not isinstance(document.get('signature'), str) or not hmac.compare_digest(
            expected, document['signature']):
        raise Refusal('ISSUED_AUTHORITY_INVALID', str(path))
    observed = _authority_payload(path)
    if observed is None:
        raise Refusal('ISSUED_AUTHORITY_UNAVAILABLE', str(path))
    if json.loads(observed) != document['payload']:
        raise Refusal('ISSUED_AUTHORITY_CHANGED', str(path))
    return document['payload']


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

    def binding(self) -> dict:
        if not re.fullmatch(r'[0-9a-f]{40}', self.source_sha):
            raise Refusal('INVALID_SOURCE_SHA', self.source_sha)
        if self.native_mode not in ('direct', 'librelane', 'dual'):
            raise Refusal('AMBIGUOUS_NATIVE_IDENTITY', self.native_mode)
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
                    native_mode=self.native_mode)


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

    def identity(self) -> dict:
        return dict(arm_id=self.arm_id, tool_id=self.tool_id,
                    step_id=self.step_id, source_sha=self.source_sha,
                    source_files=dict(self.source_files),
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
        for component in adapter.components:
            _relative(component.name)
            if len(Path(component.name).parts) != 1 or not component.argv or (
                    not math.isfinite(component.timeout_s) or component.timeout_s <= 0):
                raise Refusal('INVALID_COMPONENT', component.name)
            binary = shutil.which(component.argv[0])
            if not binary or str(Path(binary).resolve()) not in adapter.source_files:
                raise Refusal('EXECUTABLE_UNBOUND', component.argv[0])
            # Python workers and shell launchers carry their real script or
            # executable as a later absolute argv member.  Bind those files
            # too; checking argv[0] alone would let a changed producer run
            # under an unchanged interpreter identity.
            for token in component.argv[1:]:
                candidate = Path(token)
                if candidate.is_absolute() and candidate.is_file() and not candidate.is_symlink():
                    resolved = str(candidate.resolve())
                    if resolved not in adapter.source_files or digest(candidate) != adapter.source_files[resolved]:
                        raise Refusal('EXECUTABLE_UNBOUND', token)
        validator_file = inspect.getsourcefile(adapter.validate)
        if not validator_file or str(Path(validator_file).resolve()) not in adapter.source_files:
            raise Refusal('VALIDATOR_SOURCE_UNBOUND', adapter.arm_id)
        if len({c.name for c in adapter.components}) != len(adapter.components):
            raise Refusal('DUPLICATE_COMPONENT', adapter.arm_id)
        self._adapters[adapter.arm_id] = adapter

    def adapters(self, step_id: str) -> list[Adapter]:
        return [a for a in self._adapters.values() if a.step_id == step_id]


def load_portfolio(path: Path | None = None) -> dict:
    path = path or Path(__file__).parent / 'data/execution_modes_portfolio.json'
    data = json.loads(path.read_text())
    ids = [s['id'] for s in data['steps']]
    if len(ids) != 70 or len(set(ids)) != 70:
        raise Refusal('PORTFOLIO_IDS_INVALID', str(len(ids)))
    return data


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

    def _admission(self, adapter: Adapter, context: Context) -> str:
        if adapter.role != 'producer':
            return 'COMPLEMENTARY_CHECKER'
        if adapter.source_sha != context.source_sha:
            return 'WRONG_SOURCE'
        if adapter.applicability != 'applicable':
            return 'APPLICABILITY_' + adapter.applicability.upper()
        if not adapter.available:
            return 'UNAVAILABLE'
        # A source-bound component may be executed when its tool/input/image
        # is explicitly available, but registration alone never grants native
        # qualification.  Native qualification remains a separate receipt
        # fact and is represented by ``qualified=True`` only when bound.
        if adapter.license_id and self.budget.licenses.get(adapter.license_id, 0) < 1:
            return 'LICENSE_UNAVAILABLE'
        if (adapter.cpus > min(self.budget.cpus, len(os.sched_getaffinity(0))) or
                adapter.ram_mb > self.budget.ram_mb):
            return 'BUDGET_UNAVAILABLE'
        return 'READY' if adapter.qualified else 'READY_SOURCE_BOUND'

    def plan(self, context: Context, execution_mode: str | None = None,
             superiority: Superiority | None = None) -> dict:
        selected_mode = mode(execution_mode)
        binding = context.binding()
        step = next((s for s in self.portfolio['steps'] if s['id'] == context.step_id), None)
        if step is None:
            raise Refusal('UNKNOWN_CANONICAL_STEP', context.step_id)
        if not set(step['mandatory_gate_programs']).issubset(context.required_gates):
            raise Refusal('REQUIRED_GATES_DROPPED', context.step_id)
        adapters = self.registry.adapters(context.step_id)
        rows = [{**a.identity(), 'admission': self._admission(a, context),
                 'applicability': a.applicability,
                 'applicability_reason': a.applicability_reason,
                 'availability_reason': a.availability_reason,
                 'available': a.available, 'qualified': a.qualified,
                 'own_no_tool_reason': a.own_no_tool_reason,
                 'cpus': a.cpus, 'ram_mb': a.ram_mb,
                 'license_id': a.license_id} for a in adapters]
        ready = [a for a, row in zip(adapters, rows)
                 if row['admission'] in ('READY', 'READY_SOURCE_BOUND')]
        external = [a for a in adapters if a.role == 'producer' and a.tool_id != 'vibeic']
        own = [a for a in ready if a.tool_id == 'vibeic']
        # An unavailable, unknown or failed external producer is still suitable;
        # it cannot justify an own implementation. Incomplete portfolio coverage
        # is NOT proof of no external tool, so the explicit scoped reason is needed.
        if own and (any(a.applicability != 'inapplicable' for a in external) or
                    any(not a.own_no_tool_reason for a in own)):
            ready = [a for a in ready if a.tool_id != 'vibeic']
            for row in rows:
                if row['tool_id'] == 'vibeic' and row['admission'] in ('READY', 'READY_SOURCE_BOUND'):
                    row['admission'] = 'OWN_TOOL_NOT_JUSTIFIED'
        if not ready:
            return dict(mode=selected_mode, binding=binding, arms=[], portfolio=rows,
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
            for arm in ready:
                if any(set(arm.engine_families) & set(a.engine_families) or
                       _provider_identity(arm) == _provider_identity(a)
                       for a in distinct):
                    for row in rows:
                        if row['arm_id'] == arm.arm_id:
                            row['admission'] = 'SAME_ENGINE_FAMILY'
                    continue
                distinct.append(arm)
            ready = distinct
        return dict(mode=selected_mode, binding=binding,
                    arms=[a.arm_id for a in ready], portfolio=rows,
                    status='PLANNED', reason=reason,
                    independence={a.arm_id: {
                        'sha256': _hash(_provider_identity(a)),
                        'declared_families': list(a.engine_families),
                        'scope': 'SOURCE_COMPONENT_CLOSURE',
                        'native_independence': 'NOT_MEASURED'} for a in ready})

    @_supervised
    def run(self, context: Context, output: Path, execution_mode: str | None = None,
            *, cancel: threading.Event | None = None,
            superiority: Superiority | None = None, _issue=None) -> dict:
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
            raise
        plan.update(run_id=run_id, budget=asdict(self.budget),
                    public_portfolio=self.portfolio,
                    public_portfolio_sha256=_hash(self.portfolio),
                    run_root=str(output),
                    superiority=None if superiority is None else {
                        **asdict(superiority),
                        'receipts': {k: str(v) for k, v in superiority.receipts.items()}})
        _write(output / 'plan.json', plan)
        _issue(output / 'issued-plan.json', json.dumps(plan))
        _write(output / 'issued-plan.json', _seal(plan))
        if not plan['arms']:
            _write(output / 'result.json', plan)
            return plan
        arms = {a.arm_id: a for a in self.registry.adapters(context.step_id)}
        condition = threading.Condition()
        usage = {'cpus': 0, 'ram_mb': 0, 'licenses': {}}
        available_cpus = set(os.sched_getaffinity(0))
        wait_deadline = time.monotonic() + 1 + sum(
            c.timeout_s for i in plan['arms'] for c in arms[i].components)

        def work(arm: Adapter):
            with condition:
                admission = self._admission(arm, context)
                if admission not in ('READY', 'READY_SOURCE_BOUND'):
                    return self._run_arm(arm, context, plan, output, cancel,
                                         _issue=_issue, start_refusal=admission)
                def fits():
                    return (len(available_cpus) >= arm.cpus and
                            usage['cpus'] + arm.cpus <= self.budget.cpus and
                            usage['ram_mb'] + arm.ram_mb <= self.budget.ram_mb and
                            (not arm.license_id or usage['licenses'].get(arm.license_id, 0) <
                             self.budget.licenses[arm.license_id]))
                while not fits() and not cancel.is_set():
                    if time.monotonic() >= wait_deadline:
                        return self._run_arm(arm, context, plan, output, cancel,
                                             _issue=_issue, start_refusal='RESOURCE_WAIT_DEADLINE')
                    condition.wait(min(.02, max(0, wait_deadline - time.monotonic())))
                if not cancel.is_set():
                    usage['cpus'] += arm.cpus
                    usage['ram_mb'] += arm.ram_mb
                    if arm.license_id:
                        usage['licenses'][arm.license_id] = usage['licenses'].get(arm.license_id, 0) + 1
                    cpuset = sorted(available_cpus)[:arm.cpus]
                    available_cpus.difference_update(cpuset)
                else:
                    return self._run_arm(arm, context, plan, output, cancel, _issue=_issue)
            try:
                return self._run_arm(arm, context, plan, output, cancel, cpuset, _issue=_issue)
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
        summary = dict(run_id=run_id, status='AWAITING_AI_SELECTION',
                       candidate_statuses={i: r['status'] for i, r in receipts.items()},
                       mode=plan['mode'], selected=None)
        _write(output / 'result.json', summary)
        return summary

    def _run_arm(self, arm: Adapter, context: Context, plan: dict, root: Path,
                 cancel: threading.Event, cpuset: list[int] | None = None,
                 *, _issue=None, start_refusal: str | None = None) -> dict:
        if _issue is None:
            raise Refusal('ISSUED_AUTHORITY_UNAVAILABLE', 'arm execution outside Controller.run')
        directory = root / arm.arm_id
        try:
            directory.mkdir()
        except OSError as exc:
            raise Refusal('ARM_OUTPUT_UNAVAILABLE', str(directory)) from exc
        inputs, outputs = directory / 'inputs', directory / 'outputs'
        inputs.mkdir(); outputs.mkdir()
        receipt = dict(run_id=plan['run_id'], arm_id=arm.arm_id,
                       adapter=arm.identity(), binding=plan['binding'],
                       output_root=str(outputs), input_root=str(inputs),
                       status='NOT_MEASURED', reason='NOT_STARTED', processes=[],
                       evidence=None, started_ns=time.monotonic_ns())
        def frozen_binding():
            actual = {str(p.relative_to(inputs)): digest(p) for p in inputs.rglob('*') if p.is_file()}
            if actual != plan['binding']['inputs'] or any(p.is_symlink() for p in inputs.rglob('*')):
                raise Refusal('FROZEN_INPUT_CHANGED', arm.arm_id)
        try:
            if start_refusal:
                raise Refusal(start_refusal, arm.arm_id)
            if context.binding() != plan['binding']:
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
                frozen_binding()
                argv = [v.replace('{inputs}', str(inputs)).replace('{outputs}', str(outputs))
                        for v in component.argv]
                executable = shutil.which(argv[0])
                if not executable or str(Path(executable).resolve()) not in arm.source_files:
                    raise Refusal('EXECUTABLE_UNBOUND', argv[0])
                argv[0] = str(Path(executable).resolve())
                record = dict(component=component.name, argv=argv, rc=None,
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
                child_env = {**os.environ, 'OMP_NUM_THREADS': str(arm.cpus),
                             'OPENBLAS_NUM_THREADS': str(arm.cpus),
                             'VIBEIC_EXECUTION_BINDING': json.dumps(plan['binding']),
                             'VIBEIC_ARM_ID': arm.arm_id}
                child_env.pop('VIBEIC_STEP37_ROUTE', None)
                if arm.step_id == '37':
                    params = json.loads(component.argv[component.argv.index('--params-json') + 1])
                    route = params.get('streamout_route')
                    if route != 'librelane' or arm.arm_id != 'backend_37_' + route:
                        raise Refusal('BACKEND_STEP37_ROUTE_UNBOUND', arm.arm_id)
                    child_env['VIBEIC_STEP37_ROUTE'] = route
                record['issued_environment'] = {k: child_env[k] for k in
                    ('VIBEIC_ARM_ID', 'VIBEIC_STEP37_ROUTE') if k in child_env}
                with stdout.open('wb') as out, stderr.open('wb') as err:
                    process = subprocess.Popen(
                        [sys.executable, '-c', launcher, str(arm.ram_mb * 1024 * 1024),
                         ','.join(map(str, cpuset)), *argv],
                        cwd=outputs, stdout=out, stderr=err, start_new_session=True,
                        env=child_env)
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
            self._eligible(receipt, context, arm)
            receipt['status'] = 'ELIGIBLE'
        except Refusal as exc:
            receipt.update(status='FAIL' if exc.code == 'GATE_FAIL' else 'NOT_MEASURED',
                           reason=exc.code, detail=str(exc))
        except Exception as exc:
            receipt.update(status='NOT_MEASURED', reason='ADAPTER_ERROR', detail=repr(exc))
        receipt['ended_ns'] = time.monotonic_ns()
        # This completion is issued from observed Popen.wait results. Gate
        # evidence remains separately reconsumed; a source-issued process rc0
        # does not grant PASS or replace a failed/unmeasured output consumer.
        completion = {k: receipt[k] for k in ('run_id', 'arm_id', 'binding',
                      'adapter', 'processes', 'input_root', 'output_root')}
        completion.update(actual_status=receipt['status'], actual_reason=receipt['reason'],
                          evidence=receipt.get('evidence'),
                          ended_ns=receipt['ended_ns'], run_root=str(root))
        _issue(directory / 'issued-completion.json', json.dumps(completion))
        _write(directory / 'issued-completion.json', _seal(completion))
        _write(directory / 'receipt.json', receipt)
        return receipt

    @staticmethod
    def _execution_authority(root: Path, plan: dict, receipt: dict, arm: Adapter) -> None:
        issued_plan = _issued(root / 'issued-plan.json')
        if issued_plan != plan or plan.get('run_root') != str(root):
            raise Refusal('ISSUED_PLAN_MISMATCH', arm.arm_id)
        completion = _issued(root / arm.arm_id / 'issued-completion.json')
        fields = ('run_id', 'arm_id', 'binding', 'adapter', 'processes', 'input_root',
                  'output_root')
        if (completion.get('run_root') != str(root) or
                completion.get('run_id') != plan['run_id'] or
                any(completion.get(k) != receipt.get(k) for k in fields)):
            raise Refusal('EXECUTION_AUTHORITY_MISMATCH', arm.arm_id)
        processes = completion['processes']
        if len(processes) != len(arm.components) or any(
                p.get('rc') != 0 or p.get('stop_reason') or not p.get('pid') or
                not p.get('ended_ns') for p in processes):
            raise Refusal('ISSUED_EXECUTION_INCOMPLETE', arm.arm_id)

    def _current_admission(self, context: Context, plan: dict, arm: Adapter) -> None:
        if self._admission(arm, context) not in ('READY', 'READY_SOURCE_BOUND'):
            raise Refusal('CURRENT_ADMISSION_REJECTED', arm.arm_id)
        override = plan.get('superiority')
        if override:
            override = Superiority(**{**override, 'receipts': {
                k: Path(v) for k, v in override['receipts'].items()}})
        current = self.plan(context, plan['mode'], override)
        if arm.arm_id not in current['arms']:
            raise Refusal('CURRENT_POLICY_REJECTED', arm.arm_id)

    @staticmethod
    def _selected_generation(root: Path, receipt: dict, _issue) -> dict:
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
        _issue(target / 'manifest.json', json.dumps(manifest))
        _write(target / 'manifest.json', _seal(manifest))
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
        for name, expected in arm.source_files.items():
            path = Path(name)
            if not path.is_file() or path.is_symlink() or digest(path) != expected:
                raise Refusal('ADAPTER_SOURCE_MISMATCH', name)

    @staticmethod
    def _eligible(receipt: dict, context: Context, arm: Adapter) -> None:
        binding = context.binding()
        if receipt.get('binding') != binding or receipt.get('adapter') != arm.identity():
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

    @_supervised
    def adopt(self, context: Context, root: Path, choice: Mapping[str, object] | None, *, _issue=None) -> dict:
        """AI must supply an explicit receipt-bound decision; no rc0/PASS shortcut.

        All evidence is reconsumed at adoption, including the adapter's actual
        output validator. Receipt editing cannot change that validator's answer.
        Adoption records the result; it does not overwrite native project outputs.
        """
        root = Path(root).resolve()
        adoption = dict(run_id=None, status='REFUSED', selected=None,
                        ai_choice=dict(choice) if choice is not None else None)
        try:
            plan = json.loads((root / 'plan.json').read_text())
            adoption['run_id'] = plan['run_id']
            if not choice or not all(isinstance(choice.get(k), str) and choice[k].strip()
                                     for k in ('arm_id', 'receipt_sha256', 'rationale', 'reviewer')):
                raise Refusal('AI_CHOICE_MISSING', context.step_id)
            if choice.get('binding') != context.binding() or plan['binding'] != context.binding():
                raise Refusal('AI_CHOICE_UNBOUND', context.step_id)
            arm_id = choice['arm_id']
            if arm_id not in plan['arms']:
                raise Refusal('AI_CHOICE_NOT_CANDIDATE', str(arm_id))
            path = root / str(arm_id) / 'receipt.json'
            if digest(path) != choice['receipt_sha256']:
                raise Refusal('AI_RECEIPT_DIGEST_MISMATCH', str(arm_id))
            receipt = json.loads(path.read_text())
            arm = next((a for a in self.registry.adapters(context.step_id) if a.arm_id == arm_id), None)
            if arm is None or receipt.get('run_id') != plan['run_id'] or receipt.get('status') != 'ELIGIBLE':
                raise Refusal('AI_CHOICE_INELIGIBLE', str(arm_id))
            if (Path(receipt['output_root']).resolve() != root / str(arm_id) / 'outputs' or
                    Path(receipt['input_root']).resolve() != root / str(arm_id) / 'inputs'):
                raise Refusal('WRONG_ARM_OUTPUT_SPACE', str(arm_id))
            self._execution_authority(root, plan, receipt, arm)
            self._current_admission(context, plan, arm)
            self._eligible(receipt, context, arm)
            fresh = asdict(arm.validate(Path(receipt['output_root']), context.binding()))
            if fresh != receipt['evidence']:
                raise Refusal('EVIDENCE_CHANGED', str(arm_id))
            completion = _issued(root / arm.arm_id / 'issued-completion.json')
            if (completion.get('actual_status') != receipt.get('status') or
                    completion.get('actual_reason') != receipt.get('reason') or
                    completion.get('evidence') != receipt.get('evidence')):
                raise Refusal('EXECUTION_AUTHORITY_MISMATCH', arm.arm_id)
            # A source validator can legitimately pause. Its return is not a
            # lease on the earlier input/executable/output bytes. Recheck after
            # it returns, then capture and bind the selected artifact generation.
            self._eligible(receipt, context, arm)
            generation = self._selected_generation(root, receipt, _issue)
            self._execution_authority(root, plan, receipt, arm)
            self._current_admission(context, plan, arm)
            self._eligible(receipt, context, arm)
            self._generation_current(generation)
            adoption.update(status='PROVISIONAL', selected=arm_id,
                            evidence=receipt['evidence'],
                            independence=plan['independence'],
                            selected_generation=generation)
            # Backend rows have one canonical downstream consumer.  Invoke it
            # on the immutable selected generation before recording adoption;
            # no consumer may read the mutable run root after this boundary.
            if 'backend_result.json' in generation.get('outputs', {}):
                import execution_backend_consumer as _backend_consumer
                consumer = _backend_consumer.import_selected(
                    Path(generation['directory']), context, self, root, adoption)
                if not isinstance(consumer, dict) or consumer.get('status') != 'CONSUMED':
                    raise Refusal('BACKEND_CANONICAL_CONSUMER_REFUSED', str(arm_id))
                adoption['consumer'] = consumer
            adoption['status'] = 'ADOPTED'
            _write(root / 'adoption.json', adoption)
        except Refusal as exc:
            adoption.update(status='REFUSED', selected=None, reason=exc.code, detail=str(exc))
            adoption.pop('selected_generation', None)
            try:
                _write(root / 'adoption.json', adoption)
            except OSError as recording:
                raise Refusal('ADOPTION_RECORD_UNAVAILABLE', f'{exc}; {recording}') from recording
            raise
        except Exception as exc:
            adoption.pop('selected_generation', None)
            adoption.update(status='REFUSED', selected=None,
                            reason='INVALID_ADOPTION_EVIDENCE', detail=str(exc))
            try:
                _write(root / 'adoption.json', adoption)
            except OSError as recording:
                raise Refusal('ADOPTION_RECORD_UNAVAILABLE', f'{exc}; {recording}') from recording
            raise Refusal('INVALID_ADOPTION_EVIDENCE', str(exc)) from exc
        return adoption

# Do not expose a decorator that callers could use to mint enrollment.
del _supervised
