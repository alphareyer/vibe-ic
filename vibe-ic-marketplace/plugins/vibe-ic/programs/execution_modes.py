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
from contextlib import contextmanager
from contextvars import ContextVar
import ast
import hashlib
import hmac
import inspect
import json
import math
import os
from pathlib import Path
import re
import resource
import secrets
import shutil
import signal
import socket
import struct
import subprocess
import threading
import textwrap
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
_ISSUED_AUTHORITY: dict[str, str] = {}
_STEP1_CALLBACK = ContextVar('issued_step1_callback', default=None)
_CALLBACK_RPC_LOCK = threading.Lock()
_CALLBACK_FD_ENV = 'VIBEIC_STEP1_CALLBACK_FD'
_HELD_HOST_LAUNCHER = ("import os,signal,sys\nos.kill(os.getpid(),signal.SIGSTOP)\n"
    "if os.read(int(sys.argv[1]),1)!=b'G': sys.exit(1)\n"
    "os.close(int(sys.argv[1]))\nos.execvpe(sys.argv[2],sys.argv[2:],os.environ)")
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
    observed = _ISSUED_AUTHORITY.get(str(path))
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
class WorkingDerivation:
    """Expected consumed bytes, recomputed by a bound source callable in the issuer."""
    inputs: Mapping[str, str | None]
    outputs: tuple[str, ...] = ()


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
    project_derivation: Callable[[Path, Path, Mapping[str, object]], WorkingDerivation] | None = None

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
                    qualification_evidence=self.qualification_evidence,
                    project_derivation=None if self.project_derivation is None else
                        self.project_derivation.__module__+'.'+self.project_derivation.__qualname__)


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
        validator_file = inspect.getsourcefile(adapter.validate)
        if not validator_file or str(Path(validator_file).resolve()) not in adapter.source_files:
            raise Refusal('VALIDATOR_SOURCE_UNBOUND', adapter.arm_id)
        if adapter.project_derivation is not None:
            derivation_file = inspect.getsourcefile(adapter.project_derivation)
            if (not inspect.isfunction(adapter.project_derivation) or adapter.project_derivation.__closure__
                    or not derivation_file or str(Path(derivation_file).resolve()) not in adapter.source_files):
                raise Refusal('WORKING_DERIVATION_SOURCE_UNBOUND', adapter.arm_id)
        if len({c.name for c in adapter.components}) != len(adapter.components):
            raise Refusal('DUPLICATE_COMPONENT', adapter.arm_id)
        self._adapters[adapter.arm_id] = adapter

    def adapters(self, step_id: str) -> list[Adapter]:
        return [a for a in self._adapters.values() if a.step_id == step_id]


def _complete_route_identity(arm: Adapter) -> dict:
    """Bound implementation and complete consumer contract, without policy labels.

    Extra argv labels and wrapper filenames do not establish another producer.
    Source-owned factories must expose the actual bound route in Components;
    this comparison neither qualifies an arm nor changes its availability.
    """
    def implementation(path, seen=()):
        path = Path(path).resolve()
        expected = arm.source_files.get(str(path))
        if expected is None or digest(path) != expected:
            raise Refusal('ADAPTER_SOURCE_MISMATCH',str(path))
        if path.suffix != '.py':
            return expected
        # Ignore comments, locations and docstrings when comparing copied
        # wrappers. Actual executable statements and constants remain bound.
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            body = getattr(node,'body',None)
            if isinstance(body,list) and body and isinstance(body[0],ast.Expr) and (
                    isinstance(body[0].value,ast.Constant) and isinstance(body[0].value.value,str)):
                del body[0]
        # A thin imported-entry wrapper is the bound callee's route. Import
        # aliases and copies under another filename add no implementation.
        if path not in seen:
            imports = {alias.asname or alias.name:(node.module,alias.name)
                for node in tree.body if isinstance(node,ast.ImportFrom) and node.level==0
                for alias in node.names}
            body = [node for node in tree.body if not isinstance(node,(ast.Import,ast.ImportFrom))]
            if len(body)==1 and isinstance(body[0],ast.If) and not body[0].orelse and (
                    ast.dump(body[0].test)==ast.dump(ast.parse("__name__ == '__main__'").body[0].value)):
                body = body[0].body
            if len(body)==1:
                call = body[0].value if isinstance(body[0],(ast.Expr,ast.Raise)) else None
                if isinstance(call,ast.Call) and isinstance(call.func,ast.Name) and call.func.id=='SystemExit' and len(call.args)==1:
                    call = call.args[0]
                if isinstance(call,ast.Call) and not call.args and not call.keywords and isinstance(call.func,ast.Name) and call.func.id in imports:
                    module,entry = imports[call.func.id]
                    target = path.parent.joinpath(*module.split('.')).with_suffix('.py')
                    if str(target) in arm.source_files:
                        target_tree = ast.parse(target.read_text())
                        definition = next((node for node in target_tree.body if isinstance(node,ast.FunctionDef) and node.name==entry),None)
                        if definition is not None:
                            definition.name = 'bound_entry'
                            return _hash(dict(program=implementation(target,(*seen,path)),
                                entry=ast.dump(definition,include_attributes=False)))
        return _hash(ast.dump(tree,include_attributes=False))

    def consumer(function):
        path = inspect.getsourcefile(function)
        if path is None or str(Path(path).resolve()) not in arm.source_files:
            raise Refusal('VALIDATOR_SOURCE_UNBOUND',arm.arm_id)
        if digest(Path(path)) != arm.source_files[str(Path(path).resolve())]:
            raise Refusal('ADAPTER_SOURCE_MISMATCH',path)
        tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
        for node in tree.body:
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                node.name = 'bound_consumer'
                if node.body and isinstance(node.body[0],ast.Expr) and isinstance(node.body[0].value,ast.Constant) and isinstance(node.body[0].value.value,str):
                    del node.body[0]
        return _hash(ast.dump(tree,include_attributes=False))

    components = []
    for component in arm.components:
        binary = shutil.which(component.argv[0])
        if binary is None:
            raise Refusal('EXECUTABLE_UNBOUND',component.argv[0])
        executable = str(Path(binary).resolve())
        files = [implementation(executable)]
        for argument in component.argv[1:]:
            if argument in arm.source_files:
                files.append(implementation(argument))
                break  # Later data/labels cannot establish another producer.
        # A Python -c body is executable source, rather than a parameter.
        if len(component.argv)>2 and component.argv[1]=='-c':
            files.append(_hash(ast.dump(ast.parse(component.argv[2]),include_attributes=False)))
        components.append(files)
    return dict(components=components,consumer=consumer(arm.validate),
        required_outputs=list(arm.required_outputs),output_contract=dict(arm.output_contract),
        objective=dict(arm.objective),project_derivation=None if arm.project_derivation is None
        else consumer(arm.project_derivation))


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


class _Step1CallbackIssuer:
    """A capability on the actual worker pipe, lasting only for its component."""
    def __init__(self, controller, context, plan, arm, inputs, outputs, cpuset):
        self.controller, self.context, self.plan, self.arm = controller, context, plan, arm
        self.inputs, self.outputs = inputs, outputs
        self.cost = dict(cpus=arm.cpus, ram_mb=arm.ram_mb, affinity=list(cpuset),
                         licenses={} if arm.license_id is None else {arm.license_id:1})
        self.effective_limits = None
        self.callers = set()
        self.descendants = {}
        self.parent, self.child = socket.socketpair(type=socket.SOCK_SEQPACKET)
        self.parent.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        self.thread = None

    def _host_ceiling(self):
        ceiling = self.cost['ram_mb'] * 1048576
        for name, path in self.context.inputs.items():
            if Path(name).name != 'lease.json':
                continue
            path = Path(path)
            lease = json.loads(path.read_text())
            api = _sys.modules.get('execution_resource_lease')
            identity = lease.get('issuer_identity') or {}
            if (api is None or self.arm.source_files.get(str(Path(api.__file__).resolve())) != digest(Path(api.__file__))
                    or path.is_symlink() or (path.parent/'closed.json').exists()
                    or lease.get('parent_pid') != os.getpid()
                    or lease.get('parent_start_ticks') != api.start_ticks(os.getpid())
                    or identity.get('resource_source_sha256') != digest(Path(api.__file__))
                    or identity.get('parent_pid') != lease['parent_pid']
                    or identity.get('parent_start_ticks') != lease['parent_start_ticks']
                    or os.environ.get(api.OWNER_ENV) != lease.get('nonce')
                    or any(identity.get(key) != value for key,value in api._host_identity().items())):
                raise Refusal('ISSUED_CHILD_HOST_SPLIT_UNBOUND', name)
            total, host, container = (lease.get(key) for key in ('ram_mb','host_ram_mb','container_ram_mb'))
            if (any(type(value) is not int or value <= 0 for value in (total,host,container))
                    or host+container != total or total < self.cost['ram_mb']):
                raise Refusal('ISSUED_CHILD_HOST_SPLIT_UNBOUND', name)
            if hasattr(api,'prepare_host_scope'):
                issued = api._share_rpc(path.parent,'placement-parent')
                if issued != dict(directory=str(path.parent.resolve()),lease_sha256=digest(path)):
                    raise Refusal('ISSUED_CHILD_HOST_SPLIT_UNBOUND', name)
            ceiling = min(ceiling,host*1048576)
        return ceiling

    def start(self, process):
        placement = getattr(process,'host_placement',None)
        if placement is not None and (placement['stopped_child']['pid'] != process.pid
                or placement['worker']['pid'] != process.pid):
            raise Refusal('ISSUED_CHILD_PLACEMENT_UNBOUND', self.arm.arm_id)
        self.child.close()
        def serve():
            try:
                while True:
                    line, credentials, flags, _ = self.parent.recvmsg(1048577, socket.CMSG_SPACE(12))
                    if not line:
                        return
                    try:
                        peers = [struct.unpack('3i', data) for level, kind, data in credentials
                                 if level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS]
                        if (flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) or len(peers) != 1
                                or peers[0][1] != os.getuid()
                                or len(line) > 1048576 or not line.endswith(b'\n')):
                            raise Refusal('STEP1_CALLBACK_UNBOUND', 'invalid worker credentials/request')
                        value = json.loads(line)
                        peer_pid = peers[0][0]
                        descendant = self.descendants.get(peer_pid)
                        if peer_pid != process.pid:
                            state = _host_child_state(peer_pid)
                            if (descendant is None or descendant.get('completion') is not None
                                    or state['ticks'] != descendant['ticks']
                                    or state['ppid'] != process.pid or state['state'] == 'Z'):
                                raise Refusal('PRODUCTION_CHILD_SCOPE_UNBOUND','unissued descendant')
                            current_argv = Path(f'/proc/{peer_pid}/cmdline').read_bytes().rstrip(b'\0').split(b'\0')
                            if current_argv != [v.encode() for v in descendant['argv']]:
                                raise Refusal('PRODUCTION_CHILD_SCOPE_UNBOUND','descendant command changed')
                        if (process.poll() is not None or value.get('pid') != peer_pid
                                or value.get('binding') != self.plan['binding']
                                or value.get('step_id') != self.context.step_id
                                or self.context.binding() != self.plan['binding']
                                or _issued(Path(self.plan['run_root'])/'issued-plan.json') != self.plan):
                            raise Refusal('STEP1_CALLBACK_UNBOUND', 'not the current issued worker')
                        self.controller._source_current(self.arm)
                        if (any(p.is_symlink() for p in self.inputs.rglob('*')) or
                                {str(p.relative_to(self.inputs)):digest(p) for p in self.inputs.rglob('*')
                                 if p.is_file()} != self.plan['binding']['inputs']):
                            raise Refusal('FROZEN_INPUT_CHANGED', self.arm.arm_id)
                        project = Path(value['project'])
                        filename = value['source_file']
                        if (project != self.outputs/'project' or project.is_symlink()
                                or self.arm.source_files.get(filename) != value.get('source_sha256')
                                or not value.get('caller')):
                            raise Refusal('STEP1_CALLBACK_UNBOUND', 'callback source/project')
                        if descendant is not None and (self.context.step_id != 'D1'
                                or filename != str(Path(__file__).resolve().parent/'phase1_doc_one_shot_runner.py')
                                or value['caller'] != 'phase1_doc_one_shot_runner.main'):
                            raise Refusal('PRODUCTION_CHILD_SCOPE_UNBOUND','canonical docs dispatch required')
                        consumed = {name.removeprefix('project/'):expected
                            for name,expected in self.plan['binding']['inputs'].items()
                            if name.startswith('project/')}
                        if not consumed:
                            raise Refusal('STEP1_CALLBACK_INPUT_UNBOUND', str(project))
                        mutable = ()
                        if self.arm.project_derivation is not None:
                            derivation = self.arm.project_derivation(self.inputs,project,self.plan['binding'])
                            if type(derivation) is not WorkingDerivation or set(derivation.inputs) != set(consumed):
                                raise Refusal('WORKING_DERIVATION_UNBOUND', self.arm.arm_id)
                            consumed, mutable = derivation.inputs, derivation.outputs
                            for pattern in mutable:
                                _relative(pattern)
                        for name, expected in consumed.items():
                            path = project/_relative(name)
                            if any(p.is_symlink() for p in (path,*path.parents)):
                                raise Refusal('STEP1_CALLBACK_INPUT_CHANGED', name)
                            current_output = self.effective_limits is not None and any(
                                Path(name).match(pattern) for pattern in mutable)
                            if current_output:
                                if path.exists() and not path.is_file():
                                    raise Refusal('STEP1_CALLBACK_INPUT_CHANGED', name)
                                continue  # Current row output still needs its canonical consumer.
                            if (expected is None and path.exists() or expected is not None and
                                    (not path.is_file() or digest(path) != expected)):
                                raise Refusal('STEP1_CALLBACK_INPUT_CHANGED', name)
                        effective = dict(affinity=sorted(os.sched_getaffinity(peer_pid)),
                                         as_limits=list(resource.prlimit(peer_pid,resource.RLIMIT_AS)))
                        soft, hard = effective['as_limits']
                        if (not effective['affinity'] or not set(effective['affinity']).issubset(self.cost['affinity'])
                                or soft <= 0 or hard <= 0 or soft > hard or hard > self._host_ceiling()):
                            raise Refusal('ISSUED_CHILD_LIMIT_EXCEEDED', self.arm.arm_id)
                        pinned = self.effective_limits if descendant is None else descendant['effective_limits']
                        if pinned is not None and effective != pinned:
                            raise Refusal('ISSUED_CHILD_BUDGET_CHANGED', self.arm.arm_id)
                        if descendant is None:
                            self.effective_limits = effective
                        extra = {}
                        operation = value.get('operation','authorize')
                        if operation in ('delegate','finish'):
                            if peer_pid != process.pid or self.context.step_id not in ('D1','0.5ic'):
                                raise Refusal('PRODUCTION_CHILD_SCOPE_UNBOUND','host software parent required')
                            pid = value['child_pid']; state = _host_child_state(pid)
                            if state['ppid'] != process.pid:
                                raise Refusal('PRODUCTION_CHILD_SCOPE_UNBOUND','different parent')
                            if operation == 'delegate':
                                argv = value['child_argv']
                                programs = {'D1':('phase1_one_shot_runner.py',),
                                    '0.5ic':('submission_template_ingest.py','tapeout_declaration_gen.py')}
                                program = Path(argv[1])
                                held = [str(Path(_sys.executable).resolve()),'-c',_HELD_HOST_LAUNCHER,
                                        str(value['barrier_fd']),*argv]
                                limits = dict(affinity=sorted(os.sched_getaffinity(pid)),
                                    as_limits=list(resource.prlimit(pid,resource.RLIMIT_AS)))
                                if (state['state'] != 'T' or state['pgid'] != pid or state['sid'] != pid
                                        or pid in self.descendants or limits != effective
                                        or argv[0] != str(Path(_sys.executable).resolve())
                                        or argv[0] not in self.arm.source_files
                                        or program.parent != Path(__file__).resolve().parent
                                        or program.name not in programs[self.context.step_id]
                                        or str(program) not in self.arm.source_files or argv[2] != str(project)
                                        or Path(f'/proc/{pid}/cmdline').read_bytes().rstrip(b'\0').split(b'\0') != [a.encode() for a in held]):
                                    raise Refusal('PRODUCTION_CHILD_SCOPE_UNBOUND','unbound held source child')
                                self.descendants[pid] = dict(ticks=state['ticks'],argv=argv,
                                    effective_limits=limits,completion=None,callers=[])
                                extra['delegated_pid'] = pid
                            else:
                                child = self.descendants.get(pid)
                                limits = dict(affinity=sorted(os.sched_getaffinity(pid)),
                                    as_limits=list(resource.prlimit(pid,resource.RLIMIT_AS)))
                                if (child is None or child['completion'] is not None
                                        or state['ticks'] != child['ticks'] or state['state'] != 'Z'
                                        or limits != child['effective_limits']):
                                    raise Refusal('PRODUCTION_CHILD_COMPLETION_UNBOUND','actual unchanged exited child required')
                                child['completion'] = dict(pid=pid,start_ticks=state['ticks'],argv=child['argv'],
                                    rc=os.waitstatus_to_exitcode(state['exit_status']),
                                    child_cost=self.cost,effective_limits=limits,native='NOT_MEASURED')
                                extra['completion'] = child['completion']
                        elif operation != 'authorize':
                            raise Refusal('PRODUCTION_CHILD_SCOPE_UNBOUND','unknown callback operation')
                        self.callers.add((value['caller'],filename))
                        if descendant is not None and [value['caller'],filename] not in descendant['callers']:
                            descendant['callers'].append([value['caller'],filename])
                        reply = dict(status='CURRENT_ISSUED_CHILD', run_id=self.plan['run_id'],
                            arm_id=self.arm.arm_id, binding=self.plan['binding'],
                            step_id=self.context.step_id, child_cost=self.cost, effective_limits=effective,
                            caller=value['caller'], source_file=filename,
                            source_sha256=value['source_sha256'], project=str(project),**extra)
                    except (Refusal,OSError,ValueError,KeyError,TypeError,IndexError) as exc:
                        reply = dict(status='REFUSED',reason=getattr(exc,'code','STEP1_CALLBACK_UNBOUND'),detail=str(exc))
                    self.parent.sendall(json.dumps(reply).encode()+b'\n')
            except OSError:
                return
        self.thread = threading.Thread(target=serve, name='issued-Step1-callback', daemon=True)
        self.thread.start()

    def close(self):
        for pid, child in self.descendants.items():
            try:
                state = _host_child_state(pid)
                if state['ticks'] == child['ticks'] and state['state'] != 'Z':
                    os.killpg(pid,signal.SIGKILL)
            except (OSError,ValueError):
                pass
        self.child.close()
        try:
            self.parent.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.parent.close()
        if self.thread is not None:
            self.thread.join(timeout=15)
            if self.thread.is_alive():
                raise Refusal('STEP1_CALLBACK_ISSUER_PENDING',self.arm.arm_id)


@contextmanager
def _step1_callback_issuer(controller, context, plan, arm, inputs, outputs, cpuset):
    issuer = _Step1CallbackIssuer(controller,context,plan,arm,inputs,outputs,cpuset) if arm.role == 'producer' else None
    try:
        yield issuer
    finally:
        if issuer is not None:
            issuer.close()


class _Step1CallbackScope:
    def __init__(self, project, binding, fd):
        self.project, self.binding, self.fd = project, binding, fd
        self.child_cost, self.effective_limits, self.callers = None, None, set()

    def authorize(self, caller, filename, **operation):
        request = dict(pid=os.getpid(),binding=self.binding,project=str(self.project),
            step_id=self.binding['step_id'],
            caller=caller,source_file=str(filename),source_sha256=digest(Path(filename)),**operation)
        with _CALLBACK_RPC_LOCK, socket.socket(fileno=os.dup(self.fd)) as peer:
            peer.settimeout(30)
            peer.sendall(json.dumps(request).encode()+b'\n')
            value = json.loads(peer.recv(1048577))
        if value.get('status') != 'CURRENT_ISSUED_CHILD':
            raise Refusal(value.get('reason','STEP1_CALLBACK_UNBOUND'),value.get('detail',''))
        if any(value.get(key) != request[key] for key in ('binding','step_id','caller','source_file','source_sha256','project')):
            raise Refusal('STEP1_CALLBACK_UNBOUND','issuer response changed')
        cost = value.get('child_cost')
        effective = value.get('effective_limits')
        if (not isinstance(cost,dict) or not isinstance(effective,dict)
                or cost.get('cpus') != len(cost['affinity']) or type(cost.get('ram_mb')) is not int
                or effective.get('affinity') != sorted(os.sched_getaffinity(0))
                or not effective['affinity'] or not set(effective['affinity']).issubset(cost['affinity'])
                or effective.get('as_limits') != list(resource.getrlimit(resource.RLIMIT_AS))
                or not 0 < effective['as_limits'][0] <= effective['as_limits'][1] <= cost['ram_mb']*1048576
                or self.child_cost is not None and self.child_cost != cost
                or self.effective_limits is not None and self.effective_limits != effective):
            raise Refusal('ISSUED_CHILD_BUDGET_CHANGED', str(cost))
        self.child_cost, self.effective_limits = cost, effective
        self.callers.add((caller,str(filename)))
        return value

    def revalidate(self):
        for caller, filename in tuple(self.callers):
            self.authorize(caller,filename)

    def launch_host_program(self, argv, **kwargs):
        """Watchdog Popen factory: actual host entrypoint, held for issuer admission."""
        argv = list(map(str,argv))
        if len(argv)<3 or argv[2] != str(self.project):
            raise Refusal('PRODUCTION_CHILD_SCOPE_UNBOUND','same project required')
        executable = shutil.which(argv[0])
        if not executable:
            raise Refusal('PRODUCTION_CHILD_SCOPE_UNBOUND','Python entrypoint unavailable')
        argv[0] = str(Path(executable).resolve())
        argv[1] = str(Path(argv[1]).resolve())
        self.revalidate()
        env = dict(os.environ if kwargs.get('env') is None else kwargs['env'])
        if (env.get('VIBEIC_EXECUTION_BINDING') != os.environ.get('VIBEIC_EXECUTION_BINDING')
                or env.get(_CALLBACK_FD_ENV) != str(self.fd)):
            raise Refusal('PRODUCTION_CHILD_SCOPE_UNBOUND','startup transport changed')
        # The child has the authenticated effective host share, while its
        # parent's entire reservation stays held. Normal CLI guards still
        # check this operational request and explicit conflicting arguments.
        import execution_policy as policy
        request = json.loads(env.get(policy.ENV,'{}'))
        cpus = request.get('cpus',len(self.effective_limits['affinity']))
        ram = request.get('ram_mb',self.child_cost['ram_mb'])
        workers = request.get('workers',min(4,cpus))
        Budget(cpus,ram,workers=workers,licenses=request.get('licenses',{}))
        cpus = min(cpus,len(self.effective_limits['affinity']))
        request.update(cpus=cpus,ram_mb=min(ram,self.effective_limits['as_limits'][1]//1048576),
                       workers=min(workers,cpus))
        env[policy.ENV] = json.dumps(request,sort_keys=True)
        read_fd, write_fd = os.pipe()
        process = None
        try:
            kwargs.update(env=env,start_new_session=True,
                pass_fds=tuple(dict.fromkeys((*kwargs.get('pass_fds',()),read_fd,self.fd))))
            process = subprocess.Popen([str(Path(_sys.executable).resolve()),'-c',
                _HELD_HOST_LAUNCHER,str(read_fd),*argv],**kwargs)
            os.close(read_fd)
            deadline = time.monotonic()+5
            while _host_child_state(process.pid)['state'] != 'T':
                if process.poll() is not None or time.monotonic()>deadline:
                    raise Refusal('PRODUCTION_CHILD_SCOPE_UNBOUND','held child unavailable')
                time.sleep(.01)
            self.authorize(__name__+'.launch_host_program',str(Path(__file__).resolve()),
                operation='delegate',child_pid=process.pid,child_argv=argv,barrier_fd=read_fd)
            os.write(write_fd,b'G'); os.kill(process.pid,signal.SIGCONT)
            return _IssuedHostProcess(process,self)
        except BaseException:
            if process is not None:
                process.kill(); process.wait()
            else:
                os.close(read_fd)
            raise
        finally:
            os.close(write_fd)


def _host_child_state(pid):
    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
    return dict(state=fields[0],ppid=int(fields[1]),pgid=int(fields[2]),sid=int(fields[3]),
                ticks=fields[19],exit_status=int(fields[49]))


class _IssuedHostProcess:
    """Observe the child's actual kernel exit before its parent reaps it."""
    def __init__(self, process, scope):
        self.process, self.scope, self.issued_completion = process, scope, None

    def __getattr__(self, name):
        return getattr(self.process,name)

    def wait(self, timeout=None):
        if self.process.returncode is not None:
            return self.process.returncode
        deadline = None if timeout is None else time.monotonic()+timeout
        while os.waitid(os.P_PID,self.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT) is None:
            if deadline is not None and time.monotonic()>=deadline:
                raise subprocess.TimeoutExpired(self.args,timeout)
            time.sleep(.01)
        refusal = None
        try:
            value = self.scope.authorize(__name__+'.launch_host_program',str(Path(__file__).resolve()),
                operation='finish',child_pid=self.pid)
            self.issued_completion = value['completion']
        except Refusal as exc:
            refusal = exc
        rc = self.process.wait()
        if self.issued_completion is not None and self.issued_completion['rc'] != rc:
            raise Refusal('PRODUCTION_CHILD_COMPLETION_UNBOUND','kernel exit and wait disagree')
        if refusal is not None:
            if rc == 0:
                raise refusal
            self.issued_scope_refusal = dict(reason=refusal.code,detail=str(refusal))
        return rc

    def poll(self):
        try:
            return self.wait(timeout=0)
        except subprocess.TimeoutExpired:
            return None


def _issued_child_scope(project: Path, step_id: str, *, issuer_depth=1):
    try:
        startup = dict(pair.split(b'=',1) for pair in Path('/proc/self/environ').read_bytes().split(b'\0') if b'=' in pair)
        fd = int(startup[_CALLBACK_FD_ENV.encode()])
        binding = json.loads(startup[b'VIBEIC_EXECUTION_BINDING'])
        if (binding.get('step_id') != step_id or binding != json.loads(os.environ['VIBEIC_EXECUTION_BINDING'])
                or binding.get('controller_sha256') != digest(Path(__file__))
                or str(fd) != os.environ.get(_CALLBACK_FD_ENV) or _STEP1_CALLBACK.get() is not None):
            raise ValueError('not current issued child startup scope')
        with socket.socket(fileno=os.dup(fd)) as peer:
            pid,uid,_ = struct.unpack('3i',peer.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
            expected_pid = os.getppid()
            if issuer_depth == 2:
                expected_pid = _host_child_state(expected_pid)['ppid']
            elif issuer_depth != 1:
                raise ValueError('unsupported issued ancestry')
            if (pid,uid) != (expected_pid,os.getuid()):
                raise ValueError('callback peer is not the actual live parent')
        scope = _Step1CallbackScope(Path(project),binding,fd)
    except (OSError,ValueError,KeyError,TypeError) as exc:
        raise Refusal('PRODUCTION_CHILD_SCOPE_UNBOUND','live Controller-issued child required') from exc
    return scope


@contextmanager
def issued_step1_callback(project: Path):
    """Bind Step1 at its actual producer callsite; no native authority."""
    scope = _issued_child_scope(project,'1')
    token = _STEP1_CALLBACK.set(scope)
    try:
        yield scope
    finally:
        _STEP1_CALLBACK.reset(token)


def current_step1_callback():
    scope = _STEP1_CALLBACK.get()
    return scope if scope is not None and scope.binding['step_id'] == '1' else None


def current_issued_child_scope():
    return _STEP1_CALLBACK.get()


def read_issued_host_software(outputs: Path, binding: Mapping[str,object]) -> dict:
    """Parent-side consumer: actual Controller wait, never worker-authored JSON."""
    path = Path(outputs)/'host-software-issued.json'
    if path.is_symlink() or not path.is_file():
        raise Refusal('PRODUCTION_CHILD_COMPLETION_UNBOUND',str(path))
    completion = _issued(path)
    if (completion.get('kind') != 'issued-host-software' or completion.get('binding') != binding
            or completion.get('output_root') != str(outputs) or not completion.get('processes')
            or any(p.get('rc') != 0 or p.get('stop_reason') or not p.get('issued_scope')
                   for p in completion['processes'])):
        raise Refusal('PRODUCTION_CHILD_COMPLETION_UNBOUND',str(path))
    return completion


@contextmanager
def issued_child_scope(project: Path, step_id: str):
    """F5 enters after its real source/INPUT checks, inside the issued worker.

    Cost comes from the live Controller and is compared with kernel limits.
    This scope grants no adoption, image, daemon or native qualification.
    """
    scope = _issued_child_scope(project,step_id)
    scope.authorize(__name__+'.issued_child_scope',str(Path(__file__).resolve()))
    token = _STEP1_CALLBACK.set(scope)
    try:
        yield scope
    except BaseException as primary:
        try:
            scope.revalidate()
        except Refusal as secondary:
            primary.issued_child_scope_refusal = dict(reason=secondary.code,detail=str(secondary))
        raise
    else:
        scope.revalidate()
    finally:
        _STEP1_CALLBACK.reset(token)


class Controller:
    def __init__(self, registry: Registry, budget: Budget, portfolio: dict | None = None):
        self.registry, self.budget = registry, budget
        self.portfolio = portfolio if portfolio is not None else load_portfolio()

    def _launch_component(self, argv, *, callback, **kwargs):
        return subprocess.Popen(argv, **kwargs)

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
        # Engine overlap does not identify complete provider routes. Prune
        # actual invocation/consumer aliases and disclose shared engines.
        if selected_mode == 'ultra-mode':
            distinct = []
            identities = []
            for arm in ready:
                identity = _complete_route_identity(arm)
                if identity in identities:
                    for row in rows:
                        if row['arm_id'] == arm.arm_id:
                            shared = any(set(arm.engine_families) & set(a.engine_families)
                                for a,key in zip(distinct,identities) if key==identity)
                            row['admission'] = 'SAME_ENGINE_FAMILY' if shared else 'SAME_PRODUCER_ROUTE'
                    continue
                distinct.append(arm)
                identities.append(identity)
            ready = distinct
        families = {family:[a.arm_id for a in ready if family in a.engine_families]
                    for a in ready for family in a.engine_families}
        return dict(mode=selected_mode, binding=binding,
                    arms=[a.arm_id for a in ready], portfolio=rows,
                    status='PLANNED', reason=reason,
                    independence={a.arm_id: list(a.engine_families) for a in ready},
                    shared_engines={family:arms for family,arms in families.items() if len(arms)>1})

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
            raise
        plan.update(run_id=run_id, budget=asdict(self.budget),
                    public_portfolio=self.portfolio,
                    public_portfolio_sha256=_hash(self.portfolio),
                    run_root=str(output),
                    superiority=None if superiority is None else {
                        **asdict(superiority),
                        'receipts': {k: str(v) for k, v in superiority.receipts.items()}})
        _write(output / 'plan.json', plan)
        _ISSUED_AUTHORITY[str(output / 'issued-plan.json')] = json.dumps(plan)
        _write(output / 'issued-plan.json', _seal(plan))
        if not plan['arms']:
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
        summary = dict(run_id=run_id, status=('AWAITING_PROGRAM_DEFAULT' if
                       plan['mode'] == 'default-mode' and context.step_id != '9'
                       else 'AWAITING_AI_SELECTION'),
                       candidate_statuses={i: r['status'] for i, r in receipts.items()},
                       mode=plan['mode'], selected=None)
        _write(output / 'result.json', summary)
        return summary

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
                       output_root=str(outputs), input_root=str(inputs),
                       status='NOT_MEASURED', reason='NOT_STARTED', processes=[],
                       evidence=None, started_ns=time.monotonic_ns())
        def frozen_binding():
            actual = {str(p.relative_to(inputs)): digest(p) for p in inputs.rglob('*') if p.is_file()}
            if actual != plan['binding']['inputs'] or any(p.is_symlink() for p in inputs.rglob('*')):
                raise Refusal('FROZEN_INPUT_CHANGED', arm.arm_id)
        try:
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
                with stdout.open('wb') as out, stderr.open('wb') as err, _step1_callback_issuer(
                        self,context,plan,arm,inputs,outputs,cpuset) as callback:
                    callback_env = dict(os.environ)
                    callback_env.pop(_CALLBACK_FD_ENV,None)
                    if callback is not None:
                        callback_env[_CALLBACK_FD_ENV] = str(callback.child.fileno())
                    process = self._launch_component(
                        [sys.executable, '-c', launcher, str(arm.ram_mb * 1024 * 1024),
                         ','.join(map(str, cpuset)), *argv],
                        callback=callback, cwd=outputs, stdout=out, stderr=err, start_new_session=True,
                        pass_fds=() if callback is None else (callback.child.fileno(),),
                        env={**callback_env, 'OMP_NUM_THREADS': str(arm.cpus),
                             'OPENBLAS_NUM_THREADS': str(arm.cpus),
                             'VIBEIC_EXECUTION_BINDING': json.dumps(plan['binding']),
                             'VIBEIC_ARM_ID': arm.arm_id})
                    record['pid'] = process.pid
                    if callback is not None:
                        callback.start(process)
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
                    if callback is not None and callback.effective_limits is not None:
                        record['issued_scope'] = dict(child_cost=callback.cost,
                            effective_limits=callback.effective_limits,callers=sorted(callback.callers),
                            descendants=[dict(pid=pid,**child) for pid,child in callback.descendants.items()])
                if stop:
                    raise Refusal(stop, component.name)
                if record['rc'] != 0:
                    raise Refusal('PROCESS_ERROR', f'{component.name}: rc={record["rc"]}')
                if callback is not None and any(child['completion'] is None or child['completion']['rc'] != 0
                        for child in callback.descendants.values()):
                    raise Refusal('PRODUCTION_CHILD_COMPLETION_UNBOUND',component.name)
                frozen_binding()
            self._source_current(arm)
            frozen_binding()
            if receipt['processes'] and all(p.get('issued_scope') for p in receipt['processes']):
                completion = dict(kind='issued-host-software',binding=plan['binding'],
                    run_id=plan['run_id'],arm_id=arm.arm_id,adapter=arm.identity(),
                    processes=receipt['processes'],input_root=str(inputs),output_root=str(outputs),
                    native='NOT_MEASURED')
                path = outputs/'host-software-issued.json'
                _ISSUED_AUTHORITY[str(path)] = json.dumps(completion)
                _write(path,_seal(completion))
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
                          ended_ns=receipt['ended_ns'], run_root=str(root))
        _ISSUED_AUTHORITY[str(directory / 'issued-completion.json')] = json.dumps(completion)
        _write(directory / 'issued-completion.json', _seal(completion))
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
        _ISSUED_AUTHORITY[str(target / 'manifest.json')] = json.dumps(manifest)
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

    def adopt_default(self, context: Context, root: Path) -> dict:
        """Import only the current issued default candidate, without an AI choice.

        This is program artifact authority. It grants no independent expert or
        native accuracy qualification, and cannot satisfy Step9's expert gate.
        The separate AI adoption contract below remains unchanged.
        """
        root = Path(root).resolve()
        adoption = dict(run_id=None, status='REFUSED', selected=None,
            authority='PROGRAM_DEFAULT', ai_choice=None,
            independent_ai_qualification='NOT_MEASURED',
            expert_qualification='NOT_MEASURED', native_accuracy='NOT_MEASURED')
        try:
            plan = json.loads((root / 'plan.json').read_text())
            adoption['run_id'] = plan['run_id']
            if context.step_id == '9':
                raise Refusal('PROGRAM_DEFAULT_EXPERT_REQUIRED', context.step_id)
            if (plan.get('mode') != 'default-mode' or plan.get('status') != 'PLANNED'
                    or not isinstance(plan.get('arms'), list) or len(plan['arms']) != 1):
                raise Refusal('PROGRAM_DEFAULT_PLAN_REQUIRED', context.step_id)
            if plan.get('binding') != context.binding():
                raise Refusal('PROGRAM_DEFAULT_INPUT_CHANGED', context.step_id)
            arm_id = plan['arms'][0]
            arm = next((a for a in self.registry.adapters(context.step_id) if a.arm_id == arm_id), None)
            if arm is None:
                raise Refusal('PROGRAM_DEFAULT_INELIGIBLE', str(arm_id))
            path = root / arm_id / 'receipt.json'
            receipt_sha = digest(path)
            receipt = json.loads(path.read_text())
            if (receipt.get('run_id') != plan['run_id'] or receipt.get('status') != 'ELIGIBLE'
                    or receipt.get('arm_id') != arm_id):
                raise Refusal('PROGRAM_DEFAULT_INELIGIBLE', arm_id)
            if (Path(receipt['output_root']).resolve() != root / arm_id / 'outputs' or
                    Path(receipt['input_root']).resolve() != root / arm_id / 'inputs'):
                raise Refusal('WRONG_ARM_OUTPUT_SPACE', arm_id)
            self._execution_authority(root, plan, receipt, arm)
            if _issued(root / arm_id / 'issued-completion.json').get('actual_status') != 'ELIGIBLE':
                raise Refusal('PROGRAM_DEFAULT_INELIGIBLE', arm_id)
            self._current_admission(context, plan, arm)
            self._eligible(receipt, context, arm)
            fresh = asdict(arm.validate(Path(receipt['output_root']), context.binding()))
            if fresh != receipt['evidence']:
                raise Refusal('EVIDENCE_CHANGED', arm_id)
            # Reconsume source, inputs, gates and logs after a possibly paused
            # validator, then bind the same issued immutable generation.
            self._eligible(receipt, context, arm)
            if digest(path) != receipt_sha:
                raise Refusal('PROGRAM_DEFAULT_RECEIPT_CHANGED', arm_id)
            generation = self._selected_generation(root, receipt)
            self._execution_authority(root, plan, receipt, arm)
            self._current_admission(context, plan, arm)
            self._eligible(receipt, context, arm)
            self._generation_current(generation)
            if digest(path) != receipt_sha:
                raise Refusal('PROGRAM_DEFAULT_RECEIPT_CHANGED', arm_id)
            adoption.update(status='ADOPTED', selected=arm_id,
                receipt_sha256=receipt_sha, evidence=receipt['evidence'],
                independence=plan['independence'], selected_generation=generation)
            _write(root / 'adoption.json', adoption)
        except (Refusal, OSError, ValueError, KeyError, TypeError) as exc:
            adoption.update(status='REFUSED', selected=None,
                reason=getattr(exc, 'code', 'INVALID_ADOPTION_EVIDENCE'), detail=str(exc))
            try:
                _write(root / 'adoption.json', adoption)
            except OSError as recording:
                raise Refusal('ADOPTION_RECORD_UNAVAILABLE', f'{exc}; {recording}') from recording
            if isinstance(exc, Refusal):
                raise
            raise Refusal('INVALID_ADOPTION_EVIDENCE', str(exc)) from exc
        return adoption

    def adopt(self, context: Context, root: Path, choice: Mapping[str, object] | None) -> dict:
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
            # A source validator can legitimately pause. Its return is not a
            # lease on the earlier input/executable/output bytes. Recheck after
            # it returns, then capture and bind the selected artifact generation.
            self._eligible(receipt, context, arm)
            generation = self._selected_generation(root, receipt)
            self._execution_authority(root, plan, receipt, arm)
            self._current_admission(context, plan, arm)
            self._eligible(receipt, context, arm)
            self._generation_current(generation)
            adoption.update(status='ADOPTED', selected=arm_id,
                            evidence=receipt['evidence'],
                            independence=plan['independence'],
                            selected_generation=generation)
            _write(root / 'adoption.json', adoption)
        except Refusal as exc:
            adoption.update(reason=exc.code, detail=str(exc))
            try:
                _write(root / 'adoption.json', adoption)
            except OSError as recording:
                raise Refusal('ADOPTION_RECORD_UNAVAILABLE', f'{exc}; {recording}') from recording
            raise
        except (OSError, ValueError, KeyError, TypeError) as exc:
            adoption.update(status='REFUSED', selected=None,
                            reason='INVALID_ADOPTION_EVIDENCE', detail=str(exc))
            try:
                _write(root / 'adoption.json', adoption)
            except OSError as recording:
                raise Refusal('ADOPTION_RECORD_UNAVAILABLE', f'{exc}; {recording}') from recording
            raise Refusal('INVALID_ADOPTION_EVIDENCE', str(exc)) from exc
        return adoption
