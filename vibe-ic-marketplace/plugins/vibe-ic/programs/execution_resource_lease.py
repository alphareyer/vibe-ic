"""One host admission lock and recoverable, individually owned OCI containers.

No daemon and no licensed executor. A crashed owner is recovered before another
production dispatch can acquire this host's lease. Docker exec is unsupported.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, asdict
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import threading
import uuid
from typing import Mapping

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from _atomic_artefact import write_json
from execution_modes import Budget, Refusal

LABEL = 'io.vibeic.execution-lease'
OWNER_ENV = 'VIBEIC_RESOURCE_LEASE_OWNER'
BROKER_ALLOWANCE_MB = 64
_SELECTED_SHARE = ContextVar('vibeic_issued_command_share', default=None)
_DAEMON_LOCK = threading.Lock()
_SHARE_MESSAGE_BYTES = 1048576
_HOST_SCOPES = {}
_SOURCE_FIXTURE_SCOPE = ContextVar('vibeic_source_fixture_scope', default=None)
_SOURCE_FIXTURE_SCOPES = {}
_SOURCE_FIXTURE_SCOPE_LOCK = threading.RLock()


@dataclass
class SourceFixtureScope:
    """Live source-owned callable capability for an isolated fixture lease.

    The scope is an in-process handle created by ``host_lease``.  Its lease
    manifest and issuer identity are revalidated on every authorization; a
    copied JSON file, stale directory, or generic environment marker cannot
    manufacture this capability.
    """
    directory: Path
    nonce: str
    parent_pid: int
    parent_start_ticks: str
    authorized: tuple | None = None
    derived_current_call: tuple | None = None

    def _manifest(self) -> dict:
        path = self.directory / 'lease.json'
        if (self.directory.is_symlink() or path.is_symlink() or
                (self.directory / 'closed.json').exists() or not path.is_file()):
            raise Refusal('SOURCE_FIXTURE_SCOPE_UNBOUND', str(self.directory))
        manifest = json.loads(path.read_text())
        if (manifest.get('resource_scope') != 'source-fixture'
                or manifest.get('nonce') != self.nonce
                or manifest.get('parent_pid') != self.parent_pid
                or manifest.get('parent_start_ticks') != self.parent_start_ticks
                or os.getpid() != self.parent_pid
                or start_ticks(os.getpid()) != self.parent_start_ticks
                or os.environ.get(OWNER_ENV) != self.nonce):
            raise Refusal('SOURCE_FIXTURE_SCOPE_UNBOUND', str(self.directory))
        identity = manifest.get('issuer_identity') or {}
        source = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        if (identity.get('resource_source_sha256') != source
                or identity.get('parent_pid') != self.parent_pid
                or identity.get('parent_start_ticks') != self.parent_start_ticks
                or any(identity.get(k) != v for k, v in _host_identity().items())):
            raise Refusal('SOURCE_FIXTURE_SCOPE_UNBOUND', 'issuer identity changed')
        return manifest

    def authorize(self, *, step_ids, project: Path, caller: str, source: str) -> None:
        manifest = self._manifest()
        if (not isinstance(step_ids, tuple) or not step_ids or
                any(not isinstance(step, str) or not step for step in step_ids)
                or not isinstance(caller, str) or not caller
                or not isinstance(source, str) or not source):
            raise Refusal('SOURCE_FIXTURE_SCOPE_UNBOUND', 'typed callable binding required')
        project = Path(project).resolve()
        source_path = Path(source).resolve()
        if (project.is_symlink() or source_path.is_symlink() or not source_path.is_file()):
            raise Refusal('SOURCE_FIXTURE_SCOPE_UNBOUND', 'source changed or missing')
        binding = (tuple(step_ids), str(project), caller, str(source_path),
                   hashlib.sha256(source_path.read_bytes()).hexdigest())
        if self.authorized is None:
            self.authorized = binding
        elif self.authorized != binding:
            raise Refusal('SOURCE_FIXTURE_SCOPE_REBOUND', repr(binding))
        # Keep an explicit source-only budget contract visible to callers;
        # this scope never grants native Docker or production placement.
        if (any(type(manifest.get(key)) is not int or manifest[key] <= 0
                for key in ('ram_mb', 'host_ram_mb', 'container_ram_mb'))
                or manifest.get('host_ram_mb') + manifest.get('container_ram_mb') != manifest['ram_mb']):
            raise Refusal('SOURCE_FIXTURE_SCOPE_UNBOUND', 'invalid admitted source budget')

    def assert_authorized(self, *, lease_path: Path, step_id: str,
                          project: Path, caller: str, source: str) -> None:
        """Revalidate the live issuer and exact callable before callback limits."""
        self._manifest()
        lease_path = Path(lease_path)
        active_lease = self.directory / 'lease.json'
        if (lease_path.is_symlink() or not lease_path.is_file()
                or hashlib.sha256(lease_path.read_bytes()).hexdigest() !=
                   hashlib.sha256(active_lease.read_bytes()).hexdigest()):
            raise Refusal('SOURCE_FIXTURE_SCOPE_UNBOUND', 'copied lease is not the live issued bytes')
        source_path = Path(source).resolve()
        if source_path.is_symlink() or not source_path.is_file():
            raise Refusal('SOURCE_FIXTURE_SCOPE_UNBOUND', str(source_path))
        binding = (str(step_id), str(Path(project).resolve()), caller,
                   str(source_path), hashlib.sha256(source_path.read_bytes()).hexdigest())
        expected = self.authorized
        if (expected is None
                or expected != (tuple([step_id]), binding[1], binding[2], binding[3], binding[4])):
            raise Refusal('SOURCE_FIXTURE_SCOPE_UNBOUND', 'callback is not the live authorized source')

    def authorize_current_call(self, *, plan_binding: Mapping[str, object], worker_pid: int,
                               step_id: str, project: Path, caller: str, source: str) -> dict:
        """Issue one derived semantic-call handle from the live bootstrap grant.

        The derived tuple is a single immutable current-call binding.  It is
        created only by the live callback issuer after its Controller plan and
        worker checks; it is never reconstructed from a witness or ENV value.
        """
        manifest = self._manifest()
        if not isinstance(plan_binding, Mapping) or not plan_binding:
            raise Refusal('SOURCE_FIXTURE_SCOPE_UNBOUND', 'live Controller plan required')
        if (plan_binding.get('step_id') != step_id
                or not re.fullmatch(r'[0-9a-f]{40}', str(plan_binding.get('source_sha', '')))
                or not re.fullmatch(r'[0-9a-f]{64}', str(plan_binding.get('controller_sha256', '')))
                or not isinstance(plan_binding.get('inputs'), Mapping)
                or not plan_binding['inputs']):
            raise Refusal('SOURCE_FIXTURE_SCOPE_UNBOUND', 'current Controller binding changed')
        if type(worker_pid) is not int or worker_pid <= 0 or worker_pid == os.getpid():
            raise Refusal('SOURCE_FIXTURE_SCOPE_UNBOUND', 'current worker identity required')
        worker_ticks = start_ticks(worker_pid)
        state = _process_state(worker_pid, worker_ticks)
        if state['fate'] != 'live':
            raise Refusal('SOURCE_FIXTURE_SCOPE_UNBOUND', 'current worker is not live')
        owner = (OWNER_ENV + '=' + self.nonce).encode()
        try:
            if owner not in Path(f'/proc/{worker_pid}/environ').read_bytes().split(b'\0'):
                raise Refusal('SOURCE_FIXTURE_SCOPE_UNBOUND', 'worker lease owner changed')
        except OSError as exc:
            raise Refusal('SOURCE_FIXTURE_SCOPE_UNBOUND', str(worker_pid)) from exc
        if (not isinstance(step_id, str) or not step_id or not isinstance(caller, str) or not caller
                or not isinstance(source, str) or not source):
            raise Refusal('SOURCE_FIXTURE_SCOPE_UNBOUND', 'typed current callable required')
        project = Path(project).resolve()
        source_path = Path(source).resolve()
        if (project.is_symlink() or source_path.is_symlink() or not source_path.is_file()
                or not project.is_dir()):
            raise Refusal('SOURCE_FIXTURE_SCOPE_UNBOUND', 'current project/source changed')
        source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
        if self.authorized is None or self.authorized[0] != (step_id,) or self.authorized[1] != str(project):
            raise Refusal('SOURCE_FIXTURE_SCOPE_UNBOUND', 'bootstrap grant missing')
        plan_sha = hashlib.sha256(json.dumps(dict(plan_binding), sort_keys=True,
                                              separators=(',', ':')).encode()).hexdigest()
        binding = (plan_sha, worker_pid, worker_ticks, step_id, str(project), caller,
                   str(source_path), source_sha)
        if self.derived_current_call is None:
            self.derived_current_call = binding
        elif self.derived_current_call != binding:
            raise Refusal('SOURCE_FIXTURE_SCOPE_REBOUND', repr(binding))
        return dict(kind='SOURCE_FIXTURE_CURRENT_CALL', plan_sha256=plan_sha,
                    worker_pid=worker_pid, worker_start_ticks=worker_ticks,
                    step_id=step_id, project=str(project), caller=caller,
                    source=str(source_path), source_sha256=source_sha,
                    lease_sha256=hashlib.sha256((self.directory / 'lease.json').read_bytes()).hexdigest(),
                    nonce=manifest['nonce'])


def current_source_fixture_scope() -> SourceFixtureScope | None:
    scope = _SOURCE_FIXTURE_SCOPE.get()
    if scope is not None:
        scope._manifest()
    return scope


def active_source_fixture_scope(directory: Path | None = None) -> SourceFixtureScope | None:
    """Return a live scope for this issuer process, including child threads."""
    wanted = None if directory is None else Path(directory).resolve()
    owner = os.environ.get(OWNER_ENV)
    current = _SOURCE_FIXTURE_SCOPE.get()
    # Nested fixture leases shadow an outer lease.  The ContextVar is the
    # typed selection when this thread has it; the owner nonce selects the
    # already-live registry entry for Controller worker threads.
    ordered = [current] if current is not None else []
    with _SOURCE_FIXTURE_SCOPE_LOCK:
        scopes = list(_SOURCE_FIXTURE_SCOPES.values())
    ordered.extend(scope for scope in scopes if scope is not current)
    for scope in ordered:
        if (scope.parent_pid != os.getpid()
                or (wanted is not None and scope.directory != wanted)
                or (owner is not None and scope.nonce != owner)):
            continue
        scope._manifest()
        return scope
    return None


@dataclass(frozen=True)
class ShareRequest:
    """Requested costs, never authority; the live F1 parent admits the totals."""
    arm_id: str
    instrument_id: str
    command_id: str
    cpus: int
    host_ram_mb: int
    container_ram_mb: int
    licenses: dict[str, int] = field(default_factory=dict)

    def __post_init__(self):
        if (any(not isinstance(getattr(self, key), str) or not getattr(self, key).strip()
                for key in ('arm_id', 'instrument_id', 'command_id'))
                or any(type(getattr(self, key)) is not int or getattr(self, key) <= 0
                       for key in ('cpus', 'host_ram_mb', 'container_ram_mb'))
                or not isinstance(self.licenses, dict)
                or any(not isinstance(key, str) or not key.strip() or type(value) is not int or value <= 0
                       for key, value in self.licenses.items())):
            raise Refusal('RESOURCE_SHARE_REQUEST_INVALID', repr(self))


NATIVE_CAPABILITY_TARGET = '/run/vibeic/native-capability.sock'
_NATIVE_SOCKET_PARENT = Path('/tmp')
_NATIVE_SOCKET_PREFIX = '.vibeic-native-'


def _native_socket_path(directory: Path, manifest: dict, *, create_parent: bool = False) -> Path:
    """Return the bounded source-owned host socket for one production lease.

    The lease directory is an evidence namespace and may be arbitrarily long.
    Native Docker mounts therefore use a short private parent keyed by the
    authenticated lease nonce; this is a transport location, never authority.
    """
    raw_directory = Path(directory)
    if not raw_directory.is_absolute() or raw_directory.is_symlink():
        raise Refusal('RESOURCE_NATIVE_CAPABILITY_UNAVAILABLE', str(raw_directory))
    directory = raw_directory.resolve()
    nonce = manifest.get('nonce')
    if not isinstance(nonce, str) or not re.fullmatch(r'[0-9a-f]{32}', nonce):
        raise Refusal('RESOURCE_NATIVE_CAPABILITY_UNAVAILABLE', 'missing current nonce')
    parent = _NATIVE_SOCKET_PARENT / (_NATIVE_SOCKET_PREFIX + str(os.getuid()))
    if create_parent:
        if parent.is_symlink():
            raise Refusal('RESOURCE_NATIVE_CAPABILITY_CONFLICT', str(parent))
        if not parent.exists():
            parent.mkdir(mode=0o700)
        try:
            stat = parent.stat()
        except OSError as exc:
            raise Refusal('RESOURCE_NATIVE_CAPABILITY_UNAVAILABLE', str(parent)) from exc
        if stat.st_uid != os.getuid() or stat.st_mode & 0o077:
            raise Refusal('RESOURCE_NATIVE_CAPABILITY_UNAVAILABLE', str(parent))
        parent.chmod(0o700)
    elif parent.exists():
        if parent.is_symlink():
            raise Refusal('RESOURCE_NATIVE_CAPABILITY_CONFLICT', str(parent))
        stat = parent.stat()
        if stat.st_uid != os.getuid() or stat.st_mode & 0o077:
            raise Refusal('RESOURCE_NATIVE_CAPABILITY_UNAVAILABLE', str(parent))
    lease_key = hashlib.sha256((str(directory) + '\0' + nonce).encode()).hexdigest()[:24]
    path = parent / (nonce + '-' + lease_key + '.sock')
    if len(os.fsencode(str(path))) >= 104:
        raise Refusal('RESOURCE_NATIVE_CAPABILITY_UNAVAILABLE', 'bounded socket path exceeded')
    return path


def _cleanup_native_socket(directory: Path, manifest: dict) -> None:
    """Remove only this lease's source-owned native socket, if present."""
    if manifest.get('resource_scope') != 'production':
        return
    path = _native_socket_path(directory, manifest)
    if path.is_symlink():
        raise Refusal('RESOURCE_NATIVE_CAPABILITY_CONFLICT', str(path))
    try:
        path.unlink()
    except FileNotFoundError:
        return


@dataclass(frozen=True)
class NativeChildBinding:
    """Typed facts a native child may present to the live resource issuer."""
    step_id: str
    source_sha256: str
    image_id: str
    input_hashes: Mapping[str, str]
    output_root: str

    def __post_init__(self):
        if (not isinstance(self.step_id, str) or not self.step_id
                or not re.fullmatch(r'[0-9a-f]{40}', self.source_sha256)
                or not re.fullmatch(r'(?:.+@)?sha256:[0-9a-f]{64}', self.image_id)
                or not isinstance(self.input_hashes, Mapping) or not self.input_hashes
                or any(not isinstance(k, str) or not isinstance(v, str)
                       or not re.fullmatch(r'[0-9a-f]{64}', v)
                       for k, v in self.input_hashes.items())
                or not isinstance(self.output_root, str) or not self.output_root.startswith('/')):
            raise Refusal('RESOURCE_NATIVE_BINDING_INVALID', repr(self))


@dataclass(frozen=True)
class IssuedShare:
    """Opaque current-parent grant. Self-authored/copied JSON grants no quota."""
    share_id: str
    binding: str


@dataclass(frozen=True)
class PreparedHostScope:
    """Live issuer-owned placement handle; copied JSON is never authority."""
    scope_id: str
    binding: str


def _kernel_limits(group: Path) -> dict:
    return {name: (group/name).read_text().strip() for name in
            ('cpu.max', 'cpuset.cpus.effective', 'memory.max', 'memory.swap.max')}


def _scope_identity(broker: Path, job: Path, host: Path) -> dict:
    return {name: dict(path=str(path), device=path.stat().st_dev, inode=path.stat().st_ino)
            for name, path in (('broker', broker), ('job', job), ('host', host))}


@contextmanager
def prepare_host_scope(budget: Budget, *, expected_source_sha256: str):
    """Before host_lease: prepare this issuer's finite delegated job, never a supplied path.

    Capacity may pre-provision the same real topology. A fresh domain parent
    must contain only this issuer; no supervisor, worker or arbitrary PID moves.
    Existing limits are only retained or narrowed. Permission failure refuses.
    """
    handle = None
    try:
        source = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        if type(budget) is not Budget or expected_source_sha256 != source:
            raise Refusal('RESOURCE_HOST_PLACEMENT_UNBOUND', 'current source and typed budget required')
        current = _process_cgroup(os.getpid())
        existing = current.name == 'broker' and (current.parent/'host').is_dir()
        job = current.parent if existing else current
        broker, host = job/'broker', job/'host'
        limits = _kernel_limits(job)
        broker_current_mb = int((job/'memory.current').read_text()) // 1048576
        quota, period = limits['cpu.max'].split()
        required_memory = (budget.ram_mb + BROKER_ALLOWANCE_MB) * 1048576
        if (job == Path('/sys/fs/cgroup') or limits['memory.max'] == 'max'
                or int(limits['memory.max']) < required_memory or quota == 'max'
                or broker_current_mb > BROKER_ALLOWANCE_MB
                or int(quota) < budget.cpus*int(period) or int(period) <= 0
                or limits['memory.swap.max'] != '0' or (job/'cgroup.type').read_text().strip() != 'domain'):
            raise Refusal('RESOURCE_HOST_PLACEMENT_UNBOUND', 'finite admitted job limits required')
        cpus = sorted(os.sched_getaffinity(0))[:budget.cpus]
        host_mb = min(512, budget.ram_mb//2)  # the existing whole-lease split
        if len(cpus) != budget.cpus or host_mb <= 0:
            raise Refusal('RESOURCE_HOST_PLACEMENT_UNBOUND', 'actual admitted CPU/host portion required')
        if not existing:
            if (set((job/'cgroup.procs').read_text().split()) != {str(os.getpid())}
                    or any(path.is_dir() for path in job.iterdir())):
                raise Refusal('RESOURCE_HOST_PLACEMENT_UNBOUND', 'fresh job must contain only its issuer')
            ticks = start_ticks(os.getpid())
            _require_process_identity(os.getpid(), ticks)
            broker.mkdir(); host.mkdir()
            if start_ticks(os.getpid()) != ticks or _process_cgroup(os.getpid()) != job:
                raise Refusal('RESOURCE_HOST_PLACEMENT_UNBOUND', 'issuer changed before placement')
            (broker/'cgroup.procs').write_text(str(os.getpid()))
            (job/'cgroup.subtree_control').write_text('+cpu +memory +cpuset')
            for name in ('cpu.max', 'memory.max', 'memory.swap.max'):
                (broker/name).write_text(limits[name])
            (broker/'cpuset.cpus').write_text(limits['cpuset.cpus.effective'])
        host_events = dict(row.split() for row in (host/'cgroup.events').read_text().splitlines())
        if (job/'cgroup.procs').read_text().strip() or host_events.get('populated') != '0':
            raise Refusal('RESOURCE_HOST_PLACEMENT_UNBOUND', 'domain parent and host must be empty before launch')
        wanted = {'memory.max': str(host_mb*1048576), 'memory.swap.max':'0',
                  'cpu.max': f'{budget.cpus*int(period)} {period}',
                  'cpuset.cpus': ','.join(map(str, cpus))}
        old = _kernel_limits(host)
        if old['memory.max'] != 'max':
            wanted['memory.max'] = str(min(int(old['memory.max']), int(wanted['memory.max'])))
        old_quota, old_period = old['cpu.max'].split()
        if old_quota != 'max' and int(old_quota)*int(period) < budget.cpus*int(period)*int(old_period):
            wanted['cpu.max'] = old['cpu.max']
        actual_cpuset = set()
        for part in old['cpuset.cpus.effective'].split(','):
            ends = list(map(int, part.split('-'))); actual_cpuset.update(range(ends[0], ends[-1]+1))
        cpus = sorted(set(cpus).intersection(actual_cpuset))
        if not cpus:
            raise Refusal('RESOURCE_HOST_PLACEMENT_UNBOUND', 'empty actual host CPU intersection')
        wanted['cpuset.cpus'] = ','.join(map(str, cpus))
        for name, value in wanted.items():
            # An already correct capacity-owned scope needs no cap write.
            current_value = (host/name).read_text().strip()
            if name == 'cpuset.cpus' and set(cpus) == actual_cpuset:
                continue
            if current_value != value:
                (host/name).write_text(value)
        if not all(os.access(path, os.W_OK) for path in (host/'cgroup.procs', job/'cgroup.procs')):
            raise Refusal('RESOURCE_HOST_PLACEMENT_PERMISSION', 'delegated host/common-parent placement permission required')
        payload = dict(parent_pid=os.getpid(), parent_start_ticks=start_ticks(os.getpid()),
                       source_sha256=source, budget=asdict(budget),
                       broker_allowance_mb=BROKER_ALLOWANCE_MB,
                       broker_memory_current_mb=broker_current_mb,
                       required_job_ram_mb=budget.ram_mb + BROKER_ALLOWANCE_MB,
                       scope=_scope_identity(broker, job, host))
        handle = PreparedHostScope(uuid.uuid4().hex, json.dumps(payload, sort_keys=True))
        _HOST_SCOPES[handle.scope_id] = handle
        yield handle
    except (PermissionError, OSError) as exc:
        if handle is not None:
            raise  # caller-body errors are not preparation permission failures
        raise Refusal('RESOURCE_HOST_PLACEMENT_PERMISSION', str(exc)) from exc
    finally:
        if handle is not None:
            _HOST_SCOPES.pop(handle.scope_id, None)


def _live_host_scope(handle: PreparedHostScope, directory: Path, expected_sha256: str) -> tuple[dict, dict]:
    if type(handle) is not PreparedHostScope or _HOST_SCOPES.get(handle.scope_id) is not handle:
        raise Refusal('RESOURCE_HOST_PLACEMENT_UNBOUND', 'not a live issuer-owned scope')
    payload = json.loads(handle.binding)
    path = directory/'lease.json'
    manifest = json.loads(path.read_text())
    if (hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256 or (directory/'closed.json').exists()
            or manifest['resource_scope'] != 'production'
            or manifest['parent_pid'] != os.getpid() or payload['parent_pid'] != os.getpid()
            or manifest['parent_start_ticks'] != start_ticks(os.getpid())
            or payload['parent_start_ticks'] != start_ticks(os.getpid())
            or payload['source_sha256'] != hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
            or payload.get('broker_allowance_mb') != BROKER_ALLOWANCE_MB
            or payload.get('required_job_ram_mb') != payload['budget']['ram_mb'] + BROKER_ALLOWANCE_MB
            or manifest.get('broker_allowance_mb') != BROKER_ALLOWANCE_MB
            or manifest.get('required_job_ram_mb') != manifest.get('ram_mb', 0) + BROKER_ALLOWANCE_MB
            or type(manifest.get('broker_memory_current_mb')) is not int
            or manifest.get('broker_memory_current_mb') < 0
            or manifest.get('broker_memory_current_mb') > BROKER_ALLOWANCE_MB
            or manifest['issuer_identity']['resource_source_sha256'] != payload['source_sha256']
            or any(manifest['issuer_identity'].get(key) != value for key,value in _host_identity().items())
            or any(manifest[key] != payload['budget'][key] for key in ('cpus','ram_mb','licenses'))
            or manifest['host_ram_mb'] != min(512, payload['budget']['ram_mb']//2)
            or manifest['host_ram_mb']+manifest['container_ram_mb'] != manifest['ram_mb']):
        raise Refusal('RESOURCE_HOST_PLACEMENT_UNBOUND', 'current source/issuer/lease/whole budget changed')
    broker = _process_cgroup(os.getpid()); job = broker.parent; host = job/'host'
    if int((job/'memory.max').read_text()) < (payload['budget']['ram_mb'] + BROKER_ALLOWANCE_MB) * 1048576:
        raise Refusal('RESOURCE_HOST_PLACEMENT_UNBOUND', 'finite job lacks broker allowance')
    if (str(broker) != manifest['issuer_identity']['cgroup']
            or _scope_identity(broker, job, host) != payload['scope']):
        raise Refusal('RESOURCE_HOST_PLACEMENT_UNBOUND', 'actual pinned broker/job/host changed')
    authority = _share_rpc(directory, 'placement-parent')
    if authority != dict(directory=str(directory), lease_sha256=expected_sha256):
        raise Refusal('RESOURCE_HOST_PLACEMENT_UNBOUND', 'directory was not issued by the live parent')
    return payload, manifest


def _launch_line(fd: int) -> dict:
    line = b''; deadline = time.monotonic()+5
    while not line.endswith(b'\n') and len(line) <= _SHARE_MESSAGE_BYTES:
        if not select.select([fd], [], [], max(0, deadline-time.monotonic()))[0]:
            raise Refusal('RESOURCE_HOST_LAUNCH_HANDSHAKE', 'handshake timeout')
        chunk = os.read(fd, 4096)
        if not chunk:
            raise Refusal('RESOURCE_HOST_LAUNCH_HANDSHAKE', 'child closed handshake')
        line += chunk
    if len(line)>_SHARE_MESSAGE_BYTES:
        raise Refusal('RESOURCE_HOST_LAUNCH_HANDSHAKE', 'oversized handshake')
    value = json.loads(line)
    if value.get('status') != 'OK':
        raise Refusal(value.get('reason','RESOURCE_HOST_LAUNCH_HANDSHAKE'), value.get('detail',''))
    return value['value']


def launch_host_worker(directory: Path, scope: PreparedHostScope, argv: list[str], *,
                       expected_lease_sha256: str, env: dict[str, str], cwd=None,
                       readonly_mounts=(), stdout=None, stderr=None, pass_fds=()) -> subprocess.Popen:
    """Source-owned stopped-child placement; return the existing caller's Popen protocol.

    Only the child created here is moved. Domain argv executes after kernel
    readback and actual registration. No sudo, controller patch or fallback.
    Explicit open caller descriptors survive both execs; the caller keeps them
    open until return and owns their lifetime. Environment numbers grant nothing.
    """
    directory = Path(directory).resolve()
    payload, manifest = _live_host_scope(scope, directory, expected_lease_sha256)
    if (not isinstance(argv,list) or not argv or any(not isinstance(item,str) or '\0' in item for item in argv)
            or not isinstance(env,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in env.items())
            or env.get(OWNER_ENV,manifest['nonce']) != manifest['nonce']
            or not hasattr(os,'pidfd_open') or not hasattr(signal,'pidfd_send_signal')):
        raise Refusal('RESOURCE_HOST_LAUNCH_HANDSHAKE', 'bound argv/environment and pidfd required')
    if (type(pass_fds) not in (tuple, list) or any(type(fd) is not int or fd < 3 for fd in pass_fds)
            or len(set(pass_fds)) != len(pass_fds)):
        raise Refusal('RESOURCE_HOST_LAUNCH_FD_INVALID', 'distinct open caller descriptors required')
    try:
        for fd in pass_fds:
            os.fstat(fd)  # validate before allocating the distinct private handshake pipes
    except (OSError, ValueError, OverflowError) as exc:
        raise Refusal('RESOURCE_HOST_LAUNCH_FD_INVALID', str(exc)) from exc
    pass_fds = tuple(pass_fds)
    host = Path(payload['scope']['host']['path'])
    ack_read, ack_write = os.pipe(); release_read, release_write = os.pipe()
    token = uuid.uuid4().hex
    child_argv = [sys.executable,str(Path(__file__).resolve()),'--held-host-worker',str(directory),
                  str(ack_write),str(release_read),token,json.dumps(list(readonly_mounts)),*argv]
    process = None; pidfd = None
    try:
        process = subprocess.Popen(child_argv, env={**env,OWNER_ENV:manifest['nonce']}, cwd=cwd,
            stdout=stdout, stderr=stderr, start_new_session=True, pass_fds=(*pass_fds,ack_write,release_read))
        os.close(ack_write); ack_write=None; os.close(release_read); release_read=None
        pidfd = os.pidfd_open(process.pid)
        ready = _launch_line(ack_read)
        ticks = ready['start_ticks']
        deadline = time.monotonic()+5
        while _process_state(process.pid,ticks).get('state') != 'T' and time.monotonic()<deadline:
            time.sleep(.01)
        state = _process_state(process.pid,ticks)
        expected = dict(pid=process.pid,start_ticks=ticks,token=token,source_sha256=payload['source_sha256'],
                        lease_sha256=expected_lease_sha256,inputs=_share_inputs(manifest,process.pid))
        fields = Path(f'/proc/{process.pid}/stat').read_text().rsplit(')',1)[1].split()
        command = Path(f'/proc/{process.pid}/cmdline').read_bytes().rstrip(b'\0').split(b'\0')
        if (ready != expected or state.get('state') != 'T' or state['fate']!='live'
                or state['session'] != process.pid or state['process_group'] != process.pid
                or int(fields[1]) != os.getpid() or command != [item.encode() for item in child_argv]):
            raise Refusal('RESOURCE_HOST_LAUNCH_HANDSHAKE', 'actual stopped issued child differs')
        before = str(_process_cgroup(process.pid))
        _live_host_scope(scope,directory,expected_lease_sha256)
        (host/'cgroup.procs').write_text(str(process.pid))
        actual_scope = _require_share_kernel(manifest,worker_pid=process.pid,expected_scope=payload['scope'])
        if _process_state(process.pid,ticks).get('state') != 'T':
            raise Refusal('RESOURCE_HOST_LAUNCH_HANDSHAKE', 'child was released outside its issuer')
        _live_host_scope(scope,directory,expected_lease_sha256)
        release = dict(token=token,scope=actual_scope,lease_sha256=expected_lease_sha256,
                       source_sha256=payload['source_sha256'])
        os.write(release_write,(json.dumps(dict(status='OK',value=release))+'\n').encode())
        signal.pidfd_send_signal(pidfd,signal.SIGCONT)
        registered = _launch_line(ack_read)
        if registered['pid'] != process.pid or registered['start_ticks'] != ticks:
            raise Refusal('RESOURCE_HOST_LAUNCH_HANDSHAKE','registered child identity changed')
        process.host_placement = dict(stopped_child=ready,stopped_state=state,before_cgroup=before,
            after_cgroup=str(host),scope=actual_scope,limits=_kernel_limits(host),worker=registered,
            argv_sha256=hashlib.sha256(json.dumps(argv).encode()).hexdigest())
        return process
    except (PermissionError,OSError) as exc:
        if process is not None and process.poll() is None:
            if pidfd is not None: signal.pidfd_send_signal(pidfd,signal.SIGKILL)
            else: process.kill()  # only this newly created, unreaped child
            process.wait(timeout=5)
        raise Refusal('RESOURCE_HOST_PLACEMENT_PERMISSION',str(exc)) from exc
    except Exception:
        if process is not None and process.poll() is None:
            if pidfd is not None: signal.pidfd_send_signal(pidfd,signal.SIGKILL)
            else: process.kill()
            process.wait(timeout=5)
        raise
    finally:
        for fd in (ack_read,ack_write,release_read,release_write,pidfd):
            if fd is not None: os.close(fd)


def _held_host_worker():
    directory, ack, release, token, mounts = Path(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5], json.loads(sys.argv[6])
    try:
        manifest = json.loads((directory/'lease.json').read_text())
        lease_sha = hashlib.sha256((directory/'lease.json').read_bytes()).hexdigest()
        source = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        if source != manifest['issuer_identity']['resource_source_sha256']:
            raise Refusal('RESOURCE_ISSUER_IDENTITY_UNKNOWN','launch resource source changed')
        ready = dict(pid=os.getpid(),start_ticks=start_ticks(os.getpid()),token=token,
                     source_sha256=source,lease_sha256=lease_sha,inputs=_share_inputs(manifest,os.getpid()))
        os.write(ack,(json.dumps(dict(status='OK',value=ready))+'\n').encode())
        os.kill(os.getpid(),signal.SIGSTOP)
        issued = _launch_line(release)
        if (issued['token'] != token or issued['source_sha256'] != hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
                or issued['lease_sha256'] != hashlib.sha256((directory/'lease.json').read_bytes()).hexdigest()):
            raise Refusal('RESOURCE_HOST_LAUNCH_HANDSHAKE','current launch source/lease changed')
        _require_share_kernel(manifest,expected_scope=issued['scope'])
        worker = record_worker(directory,mounts)
        _registered_worker(directory,manifest,os.getpid())
        os.write(ack,(json.dumps(dict(status='OK',value=worker))+'\n').encode())
        os.close(ack); os.close(release)
        os.execvpe(sys.argv[7],sys.argv[7:],os.environ)
    except Exception as exc:
        try:
            os.write(ack,(json.dumps(dict(status='REFUSED',reason=getattr(exc,'code','RESOURCE_HOST_LAUNCH_HANDSHAKE'),detail=str(exc)))+'\n').encode())
        except OSError:
            pass
        raise


def _share_socket(manifest: dict) -> str:
    nonce = manifest.get('nonce')
    if not isinstance(nonce, str) or not re.fullmatch('[0-9a-f]{32}', nonce):
        raise Refusal('RESOURCE_SHARE_UNBOUND', 'missing current nonce')
    return '\0vibeic-issued-share-' + nonce


def _share_rpc(directory: Path, operation: str, **values) -> dict:
    manifest = json.loads((directory/'lease.json').read_text())
    _require_process_identity(manifest.get('parent_pid'), manifest.get('parent_start_ticks'))
    if (directory/'closed.json').exists():
        raise Refusal('RESOURCE_LEASE_CLOSED', str(directory))
    if _process_state(manifest['parent_pid'], manifest['parent_start_ticks'])['fate'] != 'live':
        raise Refusal('RESOURCE_PARENT_GONE', str(directory))
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
        peer.settimeout(10)
        try:
            peer.connect(_share_socket(manifest))
            pid, uid, _ = struct.unpack('3i', peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if pid != manifest['parent_pid'] or uid != os.getuid():
                raise Refusal('RESOURCE_SHARE_ISSUER_UNBOUND', str(pid))
            payload = json.dumps(dict(operation=operation, nonce=os.environ.get(OWNER_ENV),
                                      thread_id=threading.get_native_id(), **values))
            peer.sendall(payload.encode()+b'\n')
            line = peer.makefile('rb').readline(_SHARE_MESSAGE_BYTES+1)
            if len(line)>_SHARE_MESSAGE_BYTES or not line.endswith(b'\n'):
                raise ValueError('oversized/truncated issuer response')
            result = json.loads(line)
            if not isinstance(result,dict):
                raise ValueError('invalid issuer response')
        except (OSError, ValueError) as exc:
            raise Refusal('RESOURCE_SHARE_ISSUER_UNAVAILABLE', str(directory)) from exc
    if result.get('status') != 'OK':
        raise Refusal(result.get('reason', 'RESOURCE_SHARE_UNBOUND'), result.get('detail', ''))
    return result['value']


def issue_share(directory: Path, request: ShareRequest, *, expected_lease_sha256: str) -> IssuedShare:
    """BLOCKING live-parent issuance; actual worker/session and exact lease required."""
    if type(request) is not ShareRequest or not isinstance(expected_lease_sha256, str) or not re.fullmatch(
            '[0-9a-f]{64}', expected_lease_sha256):
        raise Refusal('RESOURCE_SHARE_REQUEST_INVALID', 'typed request and current lease SHA required')
    return IssuedShare(**_share_rpc(directory, 'issue', request=asdict(request), lease_sha256=expected_lease_sha256))


def _share_value(directory: Path, share: IssuedShare, operation='validate') -> dict:
    if type(share) is not IssuedShare or not isinstance(share.share_id, str) or not isinstance(share.binding, str):
        raise Refusal('RESOURCE_SHARE_UNBOUND', 'not an issued typed share')
    return _share_rpc(directory, operation, share=asdict(share))


@contextmanager
def command_share(directory: Path, share: IssuedShare):
    """One command per issued grant; selection is local to the invoking thread."""
    if _SELECTED_SHARE.get() is not None:
        raise Refusal('RESOURCE_SHARE_DUPLICATE', 'nested command selection')
    _share_value(directory, share, 'begin')
    token = _SELECTED_SHARE.set((Path(directory).resolve(), share))
    try:
        yield share
    finally:
        _SELECTED_SHARE.reset(token)
        _share_value(directory, share, 'end')


def close_share(directory: Path, share: IssuedShare) -> dict:
    """Release only a finished grant with exact absent native handles; UNKNOWN holds costs."""
    return _share_value(directory, share, 'close')


def _process_cgroup(pid: int) -> Path:
    rows = Path(f'/proc/{pid}/cgroup').read_text().splitlines()
    if len(rows) != 1 or not rows[0].startswith('0::/'):
        raise ValueError('unified kernel cgroup identity required')
    group = Path('/sys/fs/cgroup') / rows[0][3:].lstrip('/')
    if group.resolve(strict=True) != group:
        raise ValueError('noncanonical kernel cgroup')
    return group


def _require_share_kernel(manifest: dict, *, worker_pid: int | None = None,
                          expected_scope: dict | None = None) -> dict:
    """Bind the actual aggregate host subtree, separately from its broker leaf.

    Capacity owns placement. The broker and host are siblings beneath the same
    bounded job cgroup: cgroup v2 domain controllers cannot bound a child while
    the broker occupies its parent. No caller supplies a scope path or limits.
    """
    pid = os.getpid() if worker_pid is None else worker_pid
    group = None
    try:
        identity = manifest['issuer_identity']
        _require_process_identity(pid, start_ticks(pid))
        parent = _process_state(manifest['parent_pid'], manifest['parent_start_ticks'])
        if parent['fate'] != 'live':
            raise ValueError('issuer is not live')
        broker = _process_cgroup(manifest['parent_pid'])
        if str(broker) != identity['cgroup']:
            raise ValueError('issuer moved from its admitted broker cgroup')
        # A peer's path/JSON is insufficient: the invoking process must still
        # descend from the exact live issuer that registered this worker.
        ancestor = pid
        seen = set()
        while ancestor != manifest['parent_pid']:
            if ancestor <= 1 or ancestor in seen:
                raise ValueError('worker does not descend from the issuer')
            seen.add(ancestor)
            ancestor = int(Path(f'/proc/{ancestor}/stat').read_text().rsplit(')', 1)[1].split()[1])
        job = broker.parent
        worker_group = _process_cgroup(pid)
        if job == Path('/sys/fs/cgroup') or not worker_group.is_relative_to(job):
            raise ValueError('worker has no common bounded job scope')
        group = worker_group
        while group != job and group.parent != job:
            group = group.parent
        if group in (job, broker):
            raise ValueError('worker occupies the broker instead of a host subtree')
        # Refuse an ambient/unbounded parent such as system.slice. The actual
        # reservation continues to use broker/ancestor availability unchanged.
        job_memory = (job/'memory.max').read_text().strip()
        job_quota, job_period = (job/'cpu.max').read_text().split()
        if (job_memory == 'max' or int(job_memory) <= 0 or job_quota == 'max'
                or int(job_quota) <= 0 or int(job_period) <= 0
                or (job/'memory.swap.max').read_text().strip() != '0'
                or (group/'cgroup.type').read_text().strip() != 'domain'):
            raise ValueError('host/job cgroup is not an enforced domain')
        scope = {}
        for name, path in (('broker', broker), ('job', job), ('host', group)):
            state = path.stat()
            scope[name] = dict(path=str(path), device=state.st_dev, inode=state.st_ino)
        if expected_scope is not None and scope != expected_scope:
            raise ValueError('host aggregate scope changed')
        memory = (group/'memory.max').read_text().strip()
        swap = (group/'memory.swap.max').read_text().strip()
        cpu_quota, cpu_period = (group/'cpu.max').read_text().split()
        cpuset = set()
        for part in (group/'cpuset.cpus.effective').read_text().strip().split(','):
            ends = list(map(int,part.split('-')))
            cpuset.update(range(ends[0],ends[-1]+1))
        parent_affinity = set(os.sched_getaffinity(manifest['parent_pid']))
        if (memory == 'max' or int(memory) <= 0
                or int(memory) > manifest['host_ram_mb'] * 1048576 or swap != '0'
                or cpu_quota == 'max' or int(cpu_quota) <= 0 or int(cpu_period) <= 0
                or int(cpu_quota) > manifest['cpus'] * int(cpu_period)
                or not cpuset or len(cpuset) > manifest['cpus']
                or not cpuset.issubset(parent_affinity.intersection(identity['affinity']))
                or not set(os.sched_getaffinity(pid)).issubset(cpuset)
                or (pid == os.getpid() and not set(os.sched_getaffinity(0)).issubset(cpuset))):
            raise ValueError('host aggregate cgroup does not enforce the admitted host portion')
        return scope
    except (Refusal, OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise Refusal('RESOURCE_SHARE_KERNEL_BOUNDARY_UNBOUND', str(group)) from exc


def _share_absent(directory: Path, cid_path: str) -> None:
    path = Path(cid_path)
    if path.parent != directory or not path.is_file() or path.is_symlink():
        raise Refusal('RESOURCE_SHARE_RELEASE_UNKNOWN', cid_path)
    cid = path.read_text().strip()
    if not re.fullmatch('[0-9a-f]{64}', cid):
        raise Refusal('RESOURCE_SHARE_RELEASE_UNKNOWN', cid_path)
    _bind_daemon(directory)
    result = _command(['docker', 'inspect', '--format', '{{json .Config.Labels}}', cid])
    if not (result.returncode and re.fullmatch(r'(?:error(?: response from daemon)?:\s*)?no such '
            r'(?:object|container):\s*'+re.escape(cid), result.stderr.strip(), re.IGNORECASE)):
        raise Refusal('RESOURCE_SHARE_RELEASE_UNKNOWN', cid)
    _bind_daemon(directory)


def _share_inputs(manifest: dict, pid: int) -> dict:
    """Bind actual startup INPUT, not caller-authored claim JSON."""
    if manifest['resource_scope'] == 'source-fixture':
        # The finite fixture INPUT is the real invoking process command line.
        # This explicit scope cannot enter native_boundary or a real daemon.
        return dict(kind='SOURCE_FIXTURE',
                    command_line_sha256=hashlib.sha256(Path(f'/proc/{pid}/cmdline').read_bytes()).hexdigest())
    environment = dict(pair.split(b'=',1) for pair in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0') if b'=' in pair)
    try:
        binding = json.loads(environment[b'VIBEIC_EXECUTION_BINDING'])
        arm_id = environment[b'VIBEIC_ARM_ID'].decode()
        if (not isinstance(binding,dict) or not re.fullmatch('[0-9a-f]{40}',binding.get('source_sha',''))
                or binding.get('controller_sha256') != hashlib.sha256(Path(sys.modules['execution_modes'].__file__).read_bytes()).hexdigest()
                or not isinstance(binding.get('inputs'),dict) or not binding['inputs']
                or any(not isinstance(name,str) or not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value)
                       for name,value in binding['inputs'].items())
                or not binding.get('objective') or not binding.get('required_gates') or not arm_id):
            raise ValueError('incomplete current execution binding')
    except (KeyError,ValueError,TypeError) as exc:
        raise Refusal('RESOURCE_SHARE_INPUT_UNBOUND', str(pid)) from exc
    return dict(kind='CURRENT_CONTROLLER_INPUT',arm_id=arm_id,binding=binding)


@contextmanager
def _share_issuer(directory: Path, manifest: dict):
    """The existing lease's live parent owns allocation state, never worker JSON.

    A local peer-credential socket lasts only for this lease; there is no daemon
    or additional executor. Crash recovery still retains the complete host lease.
    """
    lease_sha = hashlib.sha256((directory/'lease.json').read_bytes()).hexdigest()
    parent_state = _process_state(manifest['parent_pid'],manifest['parent_start_ticks'])
    parent_session = {key:parent_state[key] for key in ('pid','expected_start_ticks','process_group','session')}
    grants, whole_commands = {}, []
    host_scope = None
    stopped = threading.Event()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(_share_socket(manifest)); server.listen(); server.settimeout(.1)
    native_server = None
    native_path = None
    if manifest.get('resource_scope') == 'production':
        native_path = _native_socket_path(directory, manifest, create_parent=True)
        if native_path.exists() or native_path.is_symlink():
            raise Refusal('RESOURCE_NATIVE_CAPABILITY_CONFLICT', str(native_path))
        native_server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        native_server.bind(str(native_path)); native_server.listen(); native_server.settimeout(.1)
        native_path.chmod(0o600)

    def save(row):
        path = directory/'shares'/(row['share_id']+'.json')
        write_json(path, {key: value for key, value in row.items() if key != 'record_sha256'})
        row['record_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()

    def handle(pid, uid, message):
        nonlocal host_scope
        if not isinstance(message,dict):
            raise Refusal('RESOURCE_SHARE_REQUEST_INVALID','request must be an object')
        if uid != os.getuid() or message.get('nonce') != manifest['nonce']:
            raise Refusal('RESOURCE_SHARE_WORKER_UNBOUND', str(pid))
        if hashlib.sha256((directory/'lease.json').read_bytes()).hexdigest() != lease_sha:
            raise Refusal('RESOURCE_LEASE_CHANGED', str(directory))
        current_parent = _process_state(manifest['parent_pid'],manifest['parent_start_ticks'])
        if current_parent['fate'] != 'live' or any(current_parent[key]!=value for key,value in parent_session.items()):
            raise Refusal('RESOURCE_SHARE_ISSUER_UNBOUND','parent process/session changed')
        if (manifest['issuer_identity']['resource_source_sha256'] != hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
                or any(manifest['issuer_identity'].get(key) != value for key, value in _host_identity().items())):
            raise Refusal('RESOURCE_ISSUER_IDENTITY_UNKNOWN', str(directory))
        tid = message.get('thread_id')
        if type(tid) is not int or not Path(f'/proc/{pid}/task/{tid}').is_dir():
            raise Refusal('RESOURCE_SHARE_THREAD_UNBOUND', str(tid))
        for row in grants.values():
            path = directory/'shares'/(row['share_id']+'.json')
            if not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != row['record_sha256']:
                raise Refusal('RESOURCE_SHARE_RECORD_CHANGED', str(path))
        operation = message.get('operation')
        if operation == 'placement-parent':
            if pid != manifest['parent_pid']:
                raise Refusal('RESOURCE_SHARE_ISSUER_UNBOUND', 'placement is issuer-owned')
            return dict(directory=str(directory.resolve()), lease_sha256=lease_sha)
        if operation == 'whole-command':
            if any(row['status'] != 'RELEASED' for row in grants.values()):
                raise Refusal('RESOURCE_SHARE_WHOLE_LEASE_CONFLICT', str(directory))
            if pid != manifest['parent_pid']:
                _registered_worker(directory, manifest, pid)
            whole_commands.append(message['cid_path'])
            return {}
        worker = _registered_worker(directory, manifest, pid)
        worker_inputs = _share_inputs(manifest,pid)
        worker_path = directory/'workers'/f"{pid}-{worker['start_ticks']}.json"
        current_scope = None
        if manifest['resource_scope'] != 'source-fixture':
            current_scope = _require_share_kernel(manifest, worker_pid=pid, expected_scope=host_scope)
            # Every live registered worker participates in this same aggregate
            # host boundary; separate per-worker caps cannot replace its sum.
            for path in directory.glob('workers/*.json'):
                registered = json.loads(path.read_text())
                state = _process_state(registered.get('pid'), registered.get('start_ticks'))
                if state['fate'] == 'live':
                    _registered_worker(directory, manifest, state['pid'])
                    _require_share_kernel(manifest, worker_pid=state['pid'], expected_scope=current_scope)
        if operation == 'issue':
            if message.get('lease_sha256') != lease_sha:
                raise Refusal('RESOURCE_LEASE_CHANGED', str(directory))
            request = asdict(ShareRequest(**message['request']))
            if worker_inputs['kind'] == 'CURRENT_CONTROLLER_INPUT' and request['arm_id'] != worker_inputs['arm_id']:
                raise Refusal('RESOURCE_SHARE_INPUT_UNBOUND', request['arm_id'])
            for cid_path in whole_commands:
                try:
                    _share_absent(directory, cid_path)
                except Refusal as exc:
                    raise Refusal('RESOURCE_SHARE_WHOLE_LEASE_CONFLICT', str(exc)) from exc
            key = tuple(request[name] for name in ('arm_id', 'instrument_id', 'command_id'))
            if any(tuple(row['payload']['request'][name] for name in ('arm_id','instrument_id','command_id')) == key
                   for row in grants.values()):
                raise Refusal('RESOURCE_SHARE_DUPLICATE', repr(key))
            fields = ('cpus', 'host_ram_mb', 'container_ram_mb')
            if (any(type(manifest.get(name)) is not int or manifest[name] <= 0 for name in (*fields,'ram_mb'))
                    or manifest['host_ram_mb'] + manifest['container_ram_mb'] != manifest['ram_mb']):
                raise Refusal('RESOURCE_SHARE_PARENT_BUDGET_UNBOUND', str(directory))
            held = [row['payload'] for row in grants.values() if row['status'] != 'RELEASED']
            if (any(sum(row['request'][name] for row in held)+request[name] > manifest[name] for name in fields)
                    or sum(row['request']['host_ram_mb']+row['request']['container_ram_mb'] for row in held)
                       + request['host_ram_mb'] + request['container_ram_mb'] > manifest['ram_mb']
                    or any(sum(row['request']['licenses'].get(name,0) for row in held)+count >
                           manifest['licenses'].get(name,0) for name,count in request['licenses'].items())):
                raise Refusal('RESOURCE_SHARE_CAPACITY_UNAVAILABLE', json.dumps(request,sort_keys=True))
            occupied = {cpu for row in held for cpu in row['cpuset']}
            available = sorted(set(manifest['issuer_identity']['affinity']).intersection(os.sched_getaffinity(pid))-occupied)
            if len(available) < request['cpus']:
                raise Refusal('RESOURCE_SHARE_CAPACITY_UNAVAILABLE', 'actual invoking worker cpuset')
            share_id = uuid.uuid4().hex
            payload = dict(share_id=share_id, lease_sha256=lease_sha, nonce=manifest['nonce'],
                           issuer_identity=manifest['issuer_identity'], parent_session=parent_session, worker=worker,
                           worker_inputs=worker_inputs, issued_by_thread_id=tid,
                           worker_record_sha256=hashlib.sha256(worker_path.read_bytes()).hexdigest(),
                           request=request, cpuset=available[:request['cpus']])
            if current_scope is not None:
                host_scope = current_scope
                payload['host_scope'] = current_scope
            binding = json.dumps(payload,sort_keys=True,separators=(',',':'))
            row = dict(share_id=share_id,payload=payload,binding=binding,status='RESERVED',cid_path=None,
                       used=False,active_thread_id=None)
            grants[share_id] = row; save(row)
            return dict(share_id=share_id,binding=binding)
        share = message.get('share')
        if not isinstance(share,dict) or not isinstance(share.get('share_id'),str) or not isinstance(share.get('binding'),str):
            raise Refusal('RESOURCE_SHARE_UNBOUND', 'missing issued identity')
        row = grants.get(share['share_id'])
        if row is None or share['binding'] != row['binding']:
            raise Refusal('RESOURCE_SHARE_UNBOUND', str(share.get('share_id')))
        if row['payload']['worker'] != worker or hashlib.sha256(worker_path.read_bytes()).hexdigest() != row['payload']['worker_record_sha256']:
            raise Refusal('RESOURCE_SHARE_WORKER_UNBOUND', str(pid))
        if row['payload']['worker_inputs'] != worker_inputs:
            raise Refusal('RESOURCE_SHARE_INPUT_UNBOUND', str(pid))
        if row['status'] in ('RELEASED','UNKNOWN'):
            raise Refusal('RESOURCE_SHARE_UNBOUND', row['status'])
        if operation == 'begin':
            if row['status'] != 'RESERVED' or row['used']:
                raise Refusal('RESOURCE_SHARE_DUPLICATE', row['share_id'])
            row['status']='ACTIVE'; row['used']=True; row['active_thread_id']=tid; save(row)
        elif operation == 'end':
            if row['status'] != 'ACTIVE' or row['active_thread_id'] != tid:
                raise Refusal('RESOURCE_SHARE_UNBOUND', row['status'])
            row['status']='FINISHED'; save(row)
        elif operation == 'command':
            cid_path = Path(message['cid_path'])
            if row['status'] != 'ACTIVE' or row['cid_path'] is not None or row['active_thread_id'] != tid:
                raise Refusal('RESOURCE_SHARE_DUPLICATE', row['share_id'])
            if cid_path.parent != directory or not re.fullmatch(r'[0-9a-f]{32}\.cid', cid_path.name):
                raise Refusal('RESOURCE_SHARE_UNBOUND', str(cid_path))
            row['cid_path']=str(cid_path); save(row)
        elif operation == 'close':
            if row['status'] == 'ACTIVE':
                raise Refusal('RESOURCE_SHARE_DUPLICATE', row['share_id'])
            try:
                if row['cid_path'] is not None:
                    _share_absent(directory,row['cid_path'])
            except Refusal:
                row['status']='UNKNOWN'; save(row)
                raise
            row['status']='RELEASED'; save(row)
            return dict(status='RESOURCE_SHARE_RELEASED',share_id=row['share_id'],native='NOT_MEASURED')
        elif operation != 'validate':
            raise Refusal('RESOURCE_SHARE_UNBOUND', str(operation))
        elif row['status'] == 'ACTIVE' and row['active_thread_id'] != tid:
            raise Refusal('RESOURCE_SHARE_THREAD_UNBOUND', str(tid))
        return row['payload']

    def native_handle(pid, uid, message):
        """Authenticate a mounted native child against live Docker state."""
        if uid != os.getuid() or not isinstance(message, dict) or message.get('operation') != 'native-handshake':
            raise Refusal('RESOURCE_NATIVE_CHILD_REFUSED', 'typed peer handshake required')
        if manifest.get('resource_scope') != 'production':
            raise Refusal('RESOURCE_FIXTURE_NATIVE_FORBIDDEN', str(directory))
        if hashlib.sha256((directory/'lease.json').read_bytes()).hexdigest() != lease_sha:
            raise Refusal('RESOURCE_LEASE_CHANGED', str(directory))
        outer_pid = message.get('outer_pid')
        if type(outer_pid) is not int:
            raise Refusal('RESOURCE_NATIVE_CHILD_REFUSED', 'outer worker PID claim missing')
        worker = _registered_worker(directory, manifest, outer_pid)
        worker_inputs = _share_inputs(manifest, outer_pid)
        binding = message.get('binding')
        if (worker_inputs.get('kind') != 'CURRENT_CONTROLLER_INPUT'
                or not isinstance(binding, dict) or binding != worker_inputs.get('binding')
                or message.get('step_id') != binding.get('step_id')
                or message.get('source_sha256') != binding.get('source_sha')):
            raise Refusal('RESOURCE_NATIVE_INPUT_UNBOUND', str(outer_pid))
        image_id = message.get('image_id')
        if not isinstance(image_id, str) or not re.fullmatch(r'(?:.+@)?sha256:[0-9a-f]{64}', image_id):
            raise Refusal('RESOURCE_NATIVE_IMAGE_UNBOUND', repr(image_id))
        claimed_inputs = message.get('inputs')
        claimed_outputs = message.get('outputs')
        expected_inputs = binding.get('inputs')
        if (not isinstance(claimed_inputs, dict) or claimed_inputs != expected_inputs
                or not isinstance(claimed_outputs, dict)
                or any(not isinstance(k, str) or not isinstance(v, str)
                       or not re.fullmatch(r'[0-9a-f]{64}', v)
                       for k, v in claimed_outputs.items())):
            raise Refusal('RESOURCE_NATIVE_INPUT_UNBOUND', 'input/output hash map changed')
        candidates = [row for row in grants.values()
                      if row['payload'].get('worker', {}).get('pid') == outer_pid
                      and row['status'] == 'ACTIVE' and row.get('cid_path')]
        if len(candidates) != 1:
            raise Refusal('RESOURCE_NATIVE_CID_UNBOUND', str(outer_pid))
        row = candidates[0]
        cid_path = Path(row['cid_path'])
        if cid_path.parent != directory or cid_path.is_symlink() or not cid_path.is_file():
            raise Refusal('RESOURCE_NATIVE_CID_UNBOUND', str(cid_path))
        cid = cid_path.read_text().strip()
        if not re.fullmatch(r'[0-9a-f]{64}', cid):
            raise Refusal('RESOURCE_NATIVE_CID_UNBOUND', cid)
        _bind_daemon(directory)
        inspected = _command(['docker', 'inspect', '--format', '{{json .}}', cid])
        if inspected.returncode:
            raise Refusal('RESOURCE_NATIVE_CID_UNBOUND', inspected.stderr[-300:])
        try:
            state = json.loads(inspected.stdout)
        except (ValueError, TypeError) as exc:
            raise Refusal('RESOURCE_NATIVE_CID_UNBOUND', 'invalid docker inspect') from exc
        labels = ((state.get('Config') or {}).get('Labels') or {})
        runtime = state.get('State') or {}
        host_config = state.get('HostConfig') or {}
        if (labels.get(LABEL) != manifest.get('nonce') or runtime.get('Running') is not True
                or type(runtime.get('Pid')) is not int or runtime['Pid'] != pid):
            raise Refusal('RESOURCE_NATIVE_PEER_UNBOUND', str(pid))
        observed_image = state.get('Image')
        expected_image = image_id.split('@', 1)[-1]
        if observed_image != expected_image and ((state.get('Config') or {}).get('Image') != image_id):
            raise Refusal('RESOURCE_NATIVE_IMAGE_UNBOUND', str(observed_image))
        request = row['payload']['request']
        if (host_config.get('NetworkMode') not in ('none', '')
                or int(host_config.get('Memory', 0)) != request['container_ram_mb'] * 1048576
                or int(host_config.get('MemorySwap', 0)) != request['container_ram_mb'] * 1048576):
            raise Refusal('RESOURCE_NATIVE_LIMITS_UNBOUND', str(cid))
        peer_state = _process_state(pid, start_ticks(pid))
        if peer_state['fate'] != 'live':
            raise Refusal('RESOURCE_NATIVE_PEER_UNBOUND', str(pid))
        try:
            peer_group = _process_cgroup(pid)
            peer_limits = _kernel_limits(peer_group)
            if (peer_limits['memory.max'] == 'max' or peer_limits['memory.swap.max'] != '0'
                    or peer_limits['cpu.max'].split()[0] == 'max'
                    or peer_limits['cpuset.cpus.effective'] == ''):
                raise Refusal('RESOURCE_NATIVE_LIMITS_UNBOUND', str(peer_group))
        except (OSError, ValueError, Refusal) as exc:
            if isinstance(exc, Refusal):
                raise
            raise Refusal('RESOURCE_NATIVE_LIMITS_UNBOUND', str(pid)) from exc
        witness = dict(schema=1, lease_sha256=lease_sha, nonce=manifest['nonce'], cid=cid,
                       cid_path=str(cid_path), peer_pid=pid, peer_start_ticks=start_ticks(pid),
                       peer_uid=uid, outer_pid=outer_pid, outer_start_ticks=worker['start_ticks'],
                       image_id=expected_image, binding=binding,
                       inputs=claimed_inputs, outputs=claimed_outputs,
                       docker_limits={'host_config': host_config, 'kernel': peer_limits},
                       daemon=json.loads((directory/'daemon.json').read_text()),
                       authenticated_ns=time.monotonic_ns())
        witness_path = directory / 'native-witness.json'
        if witness_path.exists() or witness_path.is_symlink():
            try:
                old = json.loads(witness_path.read_text())
            except (OSError, ValueError) as exc:
                raise Refusal('RESOURCE_NATIVE_WITNESS_CHANGED', str(witness_path)) from exc
            if old != witness:
                raise Refusal('RESOURCE_NATIVE_WITNESS_CHANGED', str(witness_path))
        else:
            write_json(witness_path, witness)
        return witness

    def serve():
        while not stopped.is_set():
            try:
                peer,_ = server.accept()
            except socket.timeout:
                continue
            with peer:
                peer.settimeout(5)
                try:
                    pid,uid,_ = struct.unpack('3i',peer.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
                    line = peer.makefile('rb').readline(_SHARE_MESSAGE_BYTES+1)
                    if len(line)>_SHARE_MESSAGE_BYTES or not line.endswith(b'\n'):
                        raise Refusal('RESOURCE_SHARE_REQUEST_INVALID','oversized/truncated request')
                    result = dict(status='OK',value=handle(pid,uid,json.loads(line)))
                except (Refusal,OSError,ValueError,KeyError,TypeError) as exc:
                    result = dict(status='REFUSED',reason=getattr(exc,'code','RESOURCE_SHARE_UNBOUND'),detail=str(exc))
                try:
                    peer.sendall(json.dumps(result).encode()+b'\n')
                except OSError:
                    pass  # any allocated grant remains charged in parent memory

    def native_serve():
        if native_server is None:
            return
        while not stopped.is_set():
            try:
                peer, _ = native_server.accept()
            except socket.timeout:
                continue
            with peer:
                peer.settimeout(30)
                try:
                    pid, uid, _ = struct.unpack('3i', peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                    line = peer.makefile('rb').readline(_SHARE_MESSAGE_BYTES + 1)
                    if len(line) > _SHARE_MESSAGE_BYTES or not line.endswith(b'\n'):
                        raise Refusal('RESOURCE_NATIVE_CHILD_REFUSED', 'oversized/truncated handshake')
                    result = dict(status='NATIVE_CHILD_AUTHORIZED', witness=native_handle(pid, uid, json.loads(line)))
                except (Refusal, OSError, ValueError, KeyError, TypeError) as exc:
                    result = dict(status='REFUSED', reason=getattr(exc, 'code', 'RESOURCE_NATIVE_CHILD_REFUSED'), detail=str(exc))
                try:
                    peer.sendall(json.dumps(result, sort_keys=True).encode() + b'\n')
                except OSError:
                    pass
    thread=threading.Thread(target=serve,name='F1-current-lease-issuer',daemon=True)
    thread.start()
    native_thread = None
    if native_server is not None:
        native_thread=threading.Thread(target=native_serve,name='F1-native-capability-issuer',daemon=True)
        native_thread.start()
    try:
        yield
    finally:
        stopped.set(); thread.join(timeout=6)
        if native_thread is not None:
            native_thread.join(timeout=6)
        server.close()
        if native_server is not None:
            native_server.close()
        if native_path is not None:
            _cleanup_native_socket(directory, manifest)
        if thread.is_alive() or (native_thread is not None and native_thread.is_alive()):
            raise Refusal('RESOURCE_SHARE_ISSUER_UNKNOWN', str(directory))


def start_ticks(pid: int) -> str | None:
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def _command(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=15)


_ORIGINAL_COMMAND = _command
_ORIGINAL_SUBPROCESS_RUN = subprocess.run


def _host_identity() -> dict:
    return dict(host=socket.gethostname(),
                machine_id_sha256=hashlib.sha256(Path('/etc/machine-id').read_bytes()).hexdigest(),
                boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())


def _issuer_identity() -> dict:
    host = _host_identity()
    group = Path('/sys/fs/cgroup') / Path('/proc/self/cgroup').read_text().strip().split('::')[-1].lstrip('/')
    return dict(**host, parent_pid=os.getpid(), parent_start_ticks=start_ticks(os.getpid()),
                cgroup=str(group), affinity=sorted(os.sched_getaffinity(0)),
                limits={name: (group/name).read_text().strip() for name in
                        ('cpu.max', 'memory.max', 'memory.swap.max') if (group/name).is_file()},
                resource_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())


def _require_process_identity(pid: int, ticks: str) -> None:
    if type(pid) is not int or pid <= 0 or not isinstance(ticks, str) or not ticks.isdigit():
        raise Refusal('RESOURCE_PROCESS_IDENTITY_UNKNOWN', repr((pid, ticks)))


def _process_state(pid: int, ticks: str) -> dict:
    _require_process_identity(pid, ticks)
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        actual = fields[19]
    except FileNotFoundError:
        return dict(pid=pid, expected_start_ticks=ticks, fate='absent')
    except (OSError, IndexError) as exc:
        raise Refusal('RESOURCE_PROCESS_STATE_UNKNOWN', str(pid)) from exc
    return dict(pid=pid, expected_start_ticks=ticks, actual_start_ticks=actual,
                state=fields[0], process_group=int(fields[2]), session=int(fields[3]),
                fate='live' if actual == ticks and fields[0] != 'Z' else
                     'zombie' if actual == ticks else 'recycled')


def _docker_identity(binary: str = 'docker') -> dict:
    result = _command([binary, 'info', '--format', '{{json .ID}}'])
    if result.returncode:
        raise Refusal('RESOURCE_DAEMON_IDENTITY_UNKNOWN', result.stderr[-300:])
    try:
        daemon_id = json.loads(result.stdout)
    except ValueError as exc:
        raise Refusal('RESOURCE_DAEMON_IDENTITY_UNKNOWN', result.stdout[-300:]) from exc
    if not isinstance(daemon_id, str) or not daemon_id.strip():
        raise Refusal('RESOURCE_DAEMON_IDENTITY_UNKNOWN', repr(daemon_id))
    return dict(machine_id_sha256=_host_identity()['machine_id_sha256'], daemon_id=daemon_id)


def _bind_daemon(directory: Path, binary: str = 'docker') -> dict:
    with _DAEMON_LOCK:
        path = directory / 'daemon.json'
        manifest = json.loads((directory/'lease.json').read_text())
        if manifest.get('resource_scope') == 'source-fixture' and _command is _ORIGINAL_COMMAND:
            raise Refusal('RESOURCE_FIXTURE_NATIVE_FORBIDDEN', str(directory))
        current = _docker_identity(binary)
        if path.is_file():
            if json.loads(path.read_text()) != current:
                raise Refusal('RESOURCE_DAEMON_IDENTITY_CHANGED', str(directory))
        else:
            write_json(path, current)
        return current


def record_worker(directory: Path, readonly_mounts=()) -> dict:
    """Record each actual worker, retaining all handles during concurrent arms."""
    manifest = json.loads((directory/'lease.json').read_text())
    if (directory/'closed.json').exists():
        raise Refusal('RESOURCE_LEASE_CLOSED', str(directory))
    if os.environ.get(OWNER_ENV) != manifest['nonce']:
        raise Refusal('RESOURCE_WORKER_OWNER_UNBOUND', str(os.getpid()))
    ticks = start_ticks(os.getpid())
    state = _process_state(os.getpid(), ticks)
    if state['process_group'] != os.getpid() or state['session'] != os.getpid():
        raise Refusal('RESOURCE_WORKER_GROUP_UNBOUND', str(os.getpid()))
    record = dict(pid=os.getpid(), start_ticks=ticks, process_group=state['process_group'],
                  session=state['session'], readonly_mounts=list(readonly_mounts))
    write_json(directory/'workers'/f'{os.getpid()}-{ticks}.json', record)
    write_json(directory/'worker.json', record)
    return record


def _handle_hashes(directory: Path) -> dict:
    paths = [directory/'lease.json', directory/'daemon.json', directory/'worker.json', directory/'commands.jsonl',
             *directory.glob('*.cid'), *directory.glob('workers/*.json'), *directory.glob('shares/*.json')]
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths if path.is_file()}


def _closed_current(directory: Path) -> bool:
    path = directory/'closed.json'
    if not path.is_file() or path.is_symlink():
        return False
    try:
        closed = json.loads(path.read_text())
        manifest = json.loads((directory/'lease.json').read_text())
        return (closed.get('status') == 'RESOURCE_RELEASED'
                and closed.get('nonce') == manifest['nonce']
                and closed.get('handle_hashes') == _handle_hashes(directory))
    except (OSError, ValueError, KeyError):
        return False


def recover(directory: Path, *, issuer_closing: bool = False) -> list[dict]:
    """BLOCKING: release only the same issuer/daemon's accounted handles.

    Missing identity or unresolved handles retain UNKNOWN/reservation; lower
    case exact-CID absence is evidence only on the originally bound daemon.
    Resource release never supplies a native result, rc or peak measurement.
    """
    manifest = json.loads((directory / 'lease.json').read_text())
    nonce = manifest['nonce']
    observations = []
    try:
        identity = manifest.get('issuer_identity')
        current_host = _host_identity()
        if (not isinstance(identity, dict) or not identity.get('boot_id')
                or identity.get('machine_id_sha256') != current_host['machine_id_sha256']
                or identity.get('parent_pid') != manifest.get('parent_pid')
                or identity.get('parent_start_ticks') != manifest.get('parent_start_ticks')):
            raise Refusal('RESOURCE_ISSUER_IDENTITY_UNKNOWN', str(directory))
        _require_process_identity(manifest.get('parent_pid'), manifest.get('parent_start_ticks'))
        if identity['boot_id'] == current_host['boot_id']:
            parent = _process_state(manifest['parent_pid'], manifest['parent_start_ticks'])
            if parent['fate'] == 'live':
                if not (issuer_closing and parent['pid'] == os.getpid()):
                    raise Refusal('RESOURCE_PARENT_LIVE', str(parent['pid']))
                parent['fate'] = 'issuer-current-exit'
        else:
            parent = dict(pid=manifest['parent_pid'], expected_start_ticks=manifest['parent_start_ticks'],
                          fate='recycled-by-host-boot')
        observations.append(dict(handle='parent', **parent))
        # Recovery owns only the deterministic socket for this lease.  A
        # missing/stale lease directory never authorizes deleting another
        # lease's transport.
        _cleanup_native_socket(directory, manifest)
        workers = [directory/'worker.json', *sorted(directory.glob('workers/*.json'))]
        seen = set()
        for worker in workers:
            if not worker.is_file():
                continue
            owner = json.loads(worker.read_text())
            pid, ticks = owner.get('pid'), owner.get('start_ticks')
            _require_process_identity(pid, ticks)
            if (pid, ticks) in seen:
                continue
            seen.add((pid, ticks))
            if identity['boot_id'] != current_host['boot_id']:
                observations.append(dict(handle='worker', pid=pid, expected_start_ticks=ticks,
                                         fate='recycled-by-host-boot'))
                continue
            state = _process_state(pid, ticks)
            if state['fate'] == 'live':
                if pid == os.getpid() or state['process_group'] != pid or state['session'] != pid:
                    raise Refusal('RESOURCE_WORKER_GROUP_UNBOUND', str(pid))
                owned = (OWNER_ENV + '=' + nonce).encode()
                try:
                    present = owned in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0')
                except OSError as exc:
                    raise Refusal('RESOURCE_WORKER_OWNER_UNBOUND', str(pid)) from exc
                if not present:
                    raise Refusal('RESOURCE_WORKER_OWNER_UNBOUND', str(pid))
                # The exact kernel start tick and group are rechecked at signal.
                if _process_state(pid, ticks)['fate'] != 'live':
                    raise Refusal('RESOURCE_WORKER_STATE_CHANGED', str(pid))
                os.killpg(pid, signal.SIGTERM)
                deadline = time.monotonic() + 2
                while _process_state(pid, ticks)['fate'] == 'live' and time.monotonic() < deadline:
                    time.sleep(.02)
                if _process_state(pid, ticks)['fate'] == 'live':
                    os.killpg(pid, signal.SIGKILL)
                state = _process_state(pid, ticks)
                if state['fate'] == 'live':
                    raise Refusal('RESOURCE_WORKER_RECOVERY_PENDING', str(pid))
            # A leader's absence alone cannot hide surviving process-group members.
            if state['fate'] != 'recycled':
                for proc in Path('/proc').iterdir():
                    if not proc.name.isdigit():
                        continue
                    try:
                        fields = (proc/'stat').read_text().rsplit(')', 1)[1].split()
                        if int(fields[2]) == pid and fields[0] != 'Z':
                            raise Refusal('RESOURCE_WORKER_GROUP_REMAINS', str(proc.name))
                    except FileNotFoundError:
                        continue
                    except (PermissionError, IndexError) as exc:
                        raise Refusal('RESOURCE_WORKER_GROUP_UNKNOWN', str(proc.name)) from exc
            observations.append(dict(handle='worker', **state))
        cid_paths = sorted(directory.glob('*.cid'))
        commands = directory/'commands.jsonl'
        if commands.is_file():
            known_workers = {manifest['parent_pid'], *(pid for pid, _ in seen)}
            for line in commands.read_text().splitlines():
                row = json.loads(line)
                if row.get('worker_pid') not in known_workers:
                    raise Refusal('RESOURCE_COMMAND_WORKER_UNKNOWN', str(row.get('worker_pid')))
                argv = row.get('submitted_argv', [])
                if len(argv)>1 and argv[1]=='run':
                    if '--cidfile' not in argv or argv.index('--cidfile')+1>=len(argv):
                        raise Refusal('RESOURCE_COMMAND_CONTAINER_UNKNOWN', 'missing recorded cidfile')
                    cid_path = Path(argv[argv.index('--cidfile')+1])
                    if cid_path.parent != directory or cid_path not in cid_paths:
                        raise Refusal('RESOURCE_COMMAND_CONTAINER_UNKNOWN', str(cid_path))
        daemon = None
        if cid_paths:
            if manifest.get('resource_scope') == 'source-fixture' and _command is _ORIGINAL_COMMAND:
                raise Refusal('RESOURCE_FIXTURE_NATIVE_FORBIDDEN', str(directory))
            daemon_path = directory/'daemon.json'
            if not daemon_path.is_file() or daemon_path.is_symlink():
                raise Refusal('RESOURCE_DAEMON_IDENTITY_UNKNOWN', str(directory))
            daemon = json.loads(daemon_path.read_text())
            if not daemon or _docker_identity() != daemon:
                raise Refusal('RESOURCE_DAEMON_IDENTITY_CHANGED', str(directory))
        for path in cid_paths:
            cid = path.read_text().strip()
            if not re.fullmatch('[0-9a-f]{64}', cid):
                raise Refusal('RESOURCE_CONTAINER_ID_UNBOUND', str(path))
            inspected = _command(['docker', 'inspect', '--format', '{{json .Config.Labels}}', cid])
            absent = bool(re.fullmatch(r'(?:error(?: response from daemon)?:\s*)?no such '
                r'(?:object|container):\s*' + re.escape(cid), inspected.stderr.strip(), re.IGNORECASE))
            if inspected.returncode:
                if not absent:
                    raise Refusal('RESOURCE_RECOVERY_UNMEASURED', inspected.stderr[-300:])
                observations.append(dict(handle='container', cid=cid, fate='already-absent',
                    rc=inspected.returncode, stdout=inspected.stdout, stderr=inspected.stderr))
                continue
            labels = json.loads(inspected.stdout)
            if not isinstance(labels, dict) or labels.get(LABEL) != nonce:
                raise Refusal('RESOURCE_CONTAINER_OWNER_MISMATCH', cid)
            removed = _command(['docker', 'rm', '-f', cid])
            if removed.returncode:
                raise Refusal('RESOURCE_RECOVERY_FAILED', removed.stderr[-300:])
            terminal = _command(['docker', 'inspect', '--format', '{{json .Config.Labels}}', cid])
            if not (terminal.returncode and re.fullmatch(r'(?:error(?: response from daemon)?:\s*)?'
                    r'no such (?:object|container):\s*'+re.escape(cid),terminal.stderr.strip(),re.IGNORECASE)):
                raise Refusal('RESOURCE_CONTAINER_RECOVERY_PENDING', cid)
            observations.append(dict(handle='container', cid=cid, fate='removed', rc=removed.returncode,
                                     terminal_rc=terminal.returncode, terminal_stderr=terminal.stderr))
        if daemon is not None and _docker_identity() != daemon:
            raise Refusal('RESOURCE_DAEMON_IDENTITY_CHANGED', str(directory))
        write_json(directory / 'recovery.json', observations)
        write_json(directory / 'closed.json', dict(status='RESOURCE_RELEASED', nonce=nonce, recovered=True,
            handle_hashes=_handle_hashes(directory), observations=observations,
            tool_outcome='NOT_MEASURED', raw_native_rc=None, peak_rss_gib=None))
        return observations
    except (Refusal, OSError, ValueError, KeyError, TypeError) as exc:
        write_json(directory/'recovery.json', observations)
        write_json(directory/'recovery-status.json',dict(status='UNKNOWN', reservation_retained=True,
            reason=getattr(exc,'code','RESOURCE_RECOVERY_UNMEASURED'),detail=str(exc),
            handle_hashes=_handle_hashes(directory),observations=observations,
            tool_outcome='NOT_MEASURED',raw_native_rc=None,peak_rss_gib=None))
        raise


def _reservation(directory: Path) -> dict | None:
    """Called under admission lock: active and UNKNOWN both keep whole costs."""
    if _closed_current(directory):
        return None
    reason, status = 'RESOURCE_PARENT_LIVE', 'ACTIVE'
    try:
        manifest=json.loads((directory/'lease.json').read_text())
        identity=manifest.get('issuer_identity')
        _require_process_identity(manifest.get('parent_pid'),manifest.get('parent_start_ticks'))
        if (not isinstance(identity,dict) or any(identity.get(key)!=value for key,value in _host_identity().items())
                or identity.get('parent_pid')!=manifest['parent_pid']
                or identity.get('parent_start_ticks')!=manifest['parent_start_ticks']
                or not isinstance(manifest.get('nonce'),str) or not re.fullmatch('[0-9a-f]{32}',manifest['nonce'])
                or _process_state(manifest['parent_pid'],manifest['parent_start_ticks'])['fate']!='live'):
            raise Refusal('RESOURCE_ISSUER_IDENTITY_UNKNOWN',str(directory))
    except (Refusal,OSError,ValueError,KeyError,TypeError):
        try:
            recover(directory)
            return None
        except (Refusal,OSError,ValueError,KeyError,TypeError) as exc:
            reason=getattr(exc,'code','RESOURCE_RECOVERY_UNMEASURED');status='UNKNOWN'
    try:
        prior=json.loads((directory/'lease.json').read_text())
        held=Budget(prior['cpus'],prior['ram_mb'],workers=1,licenses=prior['licenses'])
    except (Refusal,OSError,ValueError,KeyError,TypeError) as exc:
        raise Refusal('RESOURCE_RESERVATION_UNBOUND',str(directory)) from exc
    return dict(directory=str(directory),status=status,reason=reason,cpus=held.cpus,ram_mb=held.ram_mb,
                broker_allowance_mb=int(prior.get('broker_allowance_mb', 0)),
                licenses=dict(held.licenses),lease_sha256=hashlib.sha256((directory/'lease.json').read_bytes()).hexdigest())


@contextmanager
def host_lease(budget: Budget, *, fixture_root: Path | None = None,
               total_licenses: dict[str, int] | None = None):
    # Captured source controls have an explicit isolated namespace and can
    # never authorize native execution. Production keeps its original root.
    if fixture_root is None and (_command is not _ORIGINAL_COMMAND
                                 or subprocess.run is not _ORIGINAL_SUBPROCESS_RUN):
        raise Refusal('RESOURCE_SYNTHETIC_BOUNDARY_REQUIRES_FIXTURE_ROOT', 'production namespace')
    root = Path(fixture_root).resolve() if fixture_root is not None else (
        Path(tempfile.gettempdir()) / f'vibeic-execution-{os.getuid()}')
    if fixture_root is not None and root == Path('/tmp') / f'vibeic-execution-{os.getuid()}':
        raise Refusal('RESOURCE_FIXTURE_NAMESPACE_UNSAFE', str(root))
    root.mkdir(mode=0o700, exist_ok=True)
    if root.is_symlink() or root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077:
        raise Refusal('RESOURCE_NAMESPACE_UNSAFE', str(root))
    lock_path = root / 'admission.lock'
    if lock_path.is_symlink():
        raise Refusal('RESOURCE_NAMESPACE_UNSAFE', str(lock_path))
    with lock_path.open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        reservations = []
        for previous in sorted(root.glob('lease-*')):
            held = _reservation(previous)
            if held is not None:
                reservations.append(held)
        meminfo = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
        available_mb = int(meminfo['MemAvailable'].split()[0]) // 1024
        group = Path('/sys/fs/cgroup') / Path('/proc/self/cgroup').read_text().strip().split('::')[-1].lstrip('/')
        if (group / 'memory.max').is_file() and (group / 'memory.max').read_text().strip() != 'max':
            available_mb = min(available_mb, (int((group/'memory.max').read_text()) -
                                             int((group/'memory.current').read_text())) // 1048576)
        total_cpus = len(os.sched_getaffinity(0))
        for ancestor in (group, *group.parents):
            if ancestor == Path('/sys/fs'):
                break
            cpu = ancestor/'cpu.max'
            if cpu.is_file():
                quota, period = cpu.read_text().split()
                if quota != 'max':
                    total_cpus = min(total_cpus, int(quota)//int(period))
            memory = ancestor/'memory.max'
            if memory.is_file() and memory.read_text().strip() != 'max':
                available_mb = min(available_mb, max(0, int(memory.read_text()) -
                                  int((ancestor/'memory.current').read_text()))//1048576)
        license_pool = dict(budget.licenses if total_licenses is None else total_licenses)
        Budget(1, 1, workers=1, licenses=license_pool)
        held_cpus = sum(row['cpus'] for row in reservations)
        held_ram = sum(row['ram_mb'] for row in reservations)
        # Broker residency is a shared admission reservation for this finite
        # namespace.  Count the existing allowance once, while worker leases
        # remain additive.
        held_broker_ram = max((row.get('broker_allowance_mb', 0) for row in reservations), default=0)
        held_licenses = {}
        for row in reservations:
            for name, count in row['licenses'].items():
                held_licenses[name] = held_licenses.get(name, 0) + count
        broker_allowance_mb = 0 if fixture_root is not None else BROKER_ALLOWANCE_MB
        broker_current_mb = 0
        if fixture_root is None:
            try:
                broker_current_mb = int((group/'memory.current').read_text()) // 1048576
            except (OSError, ValueError):
                raise Refusal('RESOURCE_HOST_CAPACITY_UNAVAILABLE', 'broker memory.current unavailable')
        incremental_broker_mb = max(0, broker_allowance_mb - held_broker_ram)
        broker_residual_mb = max(0, incremental_broker_mb - broker_current_mb)
        accounting = dict(total_cpus=total_cpus, available_ram_mb=available_mb,
            broker_allowance_mb=broker_allowance_mb, broker_memory_current_mb=broker_current_mb,
            required_job_ram_mb=budget.ram_mb + broker_allowance_mb,
            broker_incremental_mb=incremental_broker_mb, broker_residual_mb=broker_residual_mb,
            total_licenses=license_pool, reservations=reservations,
            reserved_cpus=held_cpus, reserved_ram_mb=held_ram,
            reserved_broker_ram_mb=held_broker_ram, reserved_licenses=held_licenses,
            requested=dict(cpus=budget.cpus, ram_mb=budget.ram_mb, licenses=dict(budget.licenses)),
            license_authority='explicit admitted pool counts; no license availability inferred')
        if (held_cpus + budget.cpus > total_cpus
                or held_ram + budget.ram_mb + broker_residual_mb > available_mb
                or any(held_licenses.get(name, 0) + count > license_pool.get(name, 0)
                       for name, count in budget.licenses.items() if count)):
            write_json(root/'last-admission.json', dict(status='REFUSED', **accounting))
            raise Refusal('RESOURCE_HOST_CAPACITY_UNAVAILABLE', json.dumps(accounting, sort_keys=True))
        directory = root / ('lease-' + uuid.uuid4().hex)
        directory.mkdir(mode=0o700)
        manifest = dict(nonce=uuid.uuid4().hex, parent_pid=os.getpid(),
                        parent_start_ticks=start_ticks(os.getpid()),
                        cpus=budget.cpus, ram_mb=budget.ram_mb,
                        host_ram_mb=min(512, budget.ram_mb // 2),
                        container_ram_mb=budget.ram_mb - min(512, budget.ram_mb // 2),
                        broker_allowance_mb=broker_allowance_mb,
                        broker_memory_current_mb=broker_current_mb,
                        required_job_ram_mb=budget.ram_mb + broker_allowance_mb,
                        licenses=dict(budget.licenses), issuer_identity=_issuer_identity(),
                        resource_scope='source-fixture' if fixture_root is not None else 'production')
        write_json(directory / 'lease.json', manifest)
        write_json(directory / 'admission.json', dict(status='ADMITTED', **accounting))
        # The durable whole reservation is visible before independent jobs
        # enter. The admission mutex never spans producer/worker lifetime.
        fcntl.flock(lock, fcntl.LOCK_UN)
        previous_owner = os.environ.get(OWNER_ENV)
        os.environ[OWNER_ENV] = manifest['nonce']
        fixture_token = None
        if fixture_root is not None:
            fixture_scope = SourceFixtureScope(
                directory=directory.resolve(), nonce=manifest['nonce'],
                parent_pid=manifest['parent_pid'],
                parent_start_ticks=manifest['parent_start_ticks'])
            fixture_token = _SOURCE_FIXTURE_SCOPE.set(fixture_scope)
            with _SOURCE_FIXTURE_SCOPE_LOCK:
                _SOURCE_FIXTURE_SCOPES[id(fixture_scope)] = fixture_scope
        try:
            with _share_issuer(directory, manifest):
                yield directory
        finally:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    recover(directory, issuer_closing=True)
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
            finally:
                if previous_owner is None:
                    os.environ.pop(OWNER_ENV, None)
                else:
                    os.environ[OWNER_ENV] = previous_owner
                if fixture_token is not None:
                    with _SOURCE_FIXTURE_SCOPE_LOCK:
                        _SOURCE_FIXTURE_SCOPES.pop(id(_SOURCE_FIXTURE_SCOPE.get()), None)
                    _SOURCE_FIXTURE_SCOPE.reset(fixture_token)


def _registered_worker(directory: Path, manifest: dict, pid: int) -> dict:
    """The issuer and boundary use the actual PID, never the last-worker alias."""
    ticks = start_ticks(pid)
    _require_process_identity(pid, ticks)
    path = directory/'workers'/f'{pid}-{ticks}.json'
    if not path.is_file() or path.is_symlink():
        raise Refusal('RESOURCE_WORKER_RECORD_UNKNOWN', str(path))
    try:
        worker = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise Refusal('RESOURCE_WORKER_RECORD_UNKNOWN', str(path)) from exc
    if not isinstance(worker, dict):
        raise Refusal('RESOURCE_WORKER_RECORD_UNKNOWN', str(path))
    _require_process_identity(worker.get('pid'), worker.get('start_ticks'))
    state = _process_state(pid, ticks)
    if (worker.get('pid') != pid or worker.get('start_ticks') != ticks
            or state['fate'] != 'live' or state['process_group'] != pid or state['session'] != pid
            or worker.get('process_group') != pid or worker.get('session') != pid):
        raise Refusal('RESOURCE_WORKER_RECORD_MISMATCH', str(path))
    owned = (OWNER_ENV+'='+manifest['nonce']).encode()
    if owned not in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0'):
        raise Refusal('RESOURCE_WORKER_OWNER_UNBOUND', str(pid))
    mounts = worker.get('readonly_mounts')
    if (not isinstance(mounts, list) or any(
            not isinstance(pair, (list, tuple)) or len(pair) != 2 or any(
                not isinstance(value, str) or not Path(value).is_absolute()
                or any(char in value for char in (':', '\0', '\n')) for value in pair)
            for pair in mounts)):
        raise Refusal('RESOURCE_WORKER_MOUNTS_UNBOUND', str(path))
    return worker


def _invoking_worker(directory: Path, manifest: dict) -> dict:
    return _registered_worker(directory, manifest, os.getpid())


def _selected_share(directory: Path) -> dict | None:
    selected = _SELECTED_SHARE.get()
    if selected is None:
        return None
    if selected[0] != Path(directory).resolve():
        raise Refusal('RESOURCE_SHARE_UNBOUND', 'different current lease')
    return _share_value(directory, selected[1])


def native_client_contract(directory: Path, *, binding: NativeChildBinding,
                           expected_lease_sha256: str) -> dict:
    """Describe the resource-owned child transport; claims do not grant it.

    Frontend and analog clients use this descriptor to build their Docker
    argv.  The mounted socket is only a locator.  The issuer must authenticate
    SO_PEERCRED, the owned CID, live Docker state, image and kernel limits,
    then compare the complete ``NativeChildBinding`` before replying.
    """
    if type(binding) is not NativeChildBinding or not re.fullmatch(
            r'[0-9a-f]{64}', expected_lease_sha256):
        raise Refusal('RESOURCE_NATIVE_BINDING_INVALID', 'typed binding and lease SHA required')
    directory = Path(directory).resolve()
    lease_path = directory / 'lease.json'
    if (directory.is_symlink() or lease_path.is_symlink() or not lease_path.is_file()
            or hashlib.sha256(lease_path.read_bytes()).hexdigest() != expected_lease_sha256):
        raise Refusal('RESOURCE_LEASE_CHANGED', str(directory))
    lease = json.loads(lease_path.read_text())
    if lease.get('resource_scope') != 'production':
        raise Refusal('RESOURCE_FIXTURE_NATIVE_FORBIDDEN', str(directory))
    _require_process_identity(lease.get('parent_pid'), lease.get('parent_start_ticks'))
    if (_process_state(lease['parent_pid'], lease['parent_start_ticks'])['fate'] != 'live'
            or os.environ.get(OWNER_ENV) != lease.get('nonce')):
        raise Refusal('RESOURCE_PARENT_GONE', str(directory))
    return dict(socket_host_path=str(_native_socket_path(directory, lease)),
                socket_container_path=NATIVE_CAPABILITY_TARGET,
                locator_env='VIBEIC_NATIVE_CAPABILITY_SOCKET',
                locator_value=NATIVE_CAPABILITY_TARGET,
                owner_env=OWNER_ENV, owner_value=lease['nonce'],
                outer_pid=os.getpid(), lease_sha256=expected_lease_sha256,
                binding=asdict(binding),
                authentication=('live issuer authenticates peer credentials, owned CID label/state, '
                                'immutable image, binding, cgroup limits, and cleanup'))


def native_argv(argv: list[str], directory: Path, *, binding: NativeChildBinding,
                expected_lease_sha256: str, parent_query: bool = False) -> list[str]:
    """Build a native argv with the exact read-only capability locator mount."""
    contract = native_client_contract(directory, binding=binding,
                                      expected_lease_sha256=expected_lease_sha256)
    result = bounded_argv(argv, directory, parent_query=parent_query)
    if len(result) < 2 or result[1] != 'run' or parent_query:
        return result
    mount = contract['socket_host_path'] + ':' + contract['socket_container_path'] + ':ro'
    conflicting = [item for item in result if isinstance(item, str)
                   and (':' + contract['socket_container_path'] + ':') in item
                   and item != mount]
    if conflicting:
        raise Refusal('RESOURCE_NATIVE_CAPABILITY_CONFLICT', repr(conflicting))
    if mount not in result:
        result = [*result[:2], '-v', mount,
                  '-e', contract['locator_env'] + '=' + contract['locator_value'],
                  '-e', contract['owner_env'] + '=' + contract['owner_value'],
                  '-e', 'VIBEIC_NATIVE_OUTER_PID=' + str(contract['outer_pid']), *result[2:]]
    return result


def native_child_handshake(*, step_id: str, source_sha256: str, image_id: str,
                           binding: Mapping[str, object], inputs: Mapping[str, str],
                           outputs: Mapping[str, str], socket_path: str | None = None,
                           outer_pid: int | None = None) -> dict:
    """Ask the live host issuer to authenticate this native child.

    ``socket_path`` and ``outer_pid`` are transport claims only.  The server
    rechecks the peer PID/UID, CID, Docker label/state, image, cgroup and the
    owned worker before returning a witness.  No environment variable is an
    authority grant.
    """
    path = socket_path or os.environ.get('VIBEIC_NATIVE_CAPABILITY_SOCKET')
    if path != NATIVE_CAPABILITY_TARGET:
        raise Refusal('RESOURCE_NATIVE_CAPABILITY_UNAVAILABLE', 'exact capability locator required')
    if type(outer_pid or os.environ.get('VIBEIC_NATIVE_OUTER_PID')) not in (int, str):
        raise Refusal('RESOURCE_NATIVE_BINDING_INVALID', 'outer PID claim missing')
    if (not isinstance(binding, Mapping) or not isinstance(inputs, Mapping)
            or not isinstance(outputs, Mapping)):
        raise Refusal('RESOURCE_NATIVE_BINDING_INVALID', 'typed binding/input/output maps required')
    payload = dict(operation='native-handshake', step_id=step_id,
                   source_sha256=source_sha256, image_id=image_id,
                   binding=dict(binding), inputs=dict(inputs), outputs=dict(outputs),
                   outer_pid=int(outer_pid or os.environ['VIBEIC_NATIVE_OUTER_PID']))
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
            peer.settimeout(30)
            peer.connect(path)
            peer.sendall(json.dumps(payload, sort_keys=True).encode() + b'\n')
            line = peer.makefile('rb').readline(_SHARE_MESSAGE_BYTES + 1)
    except (OSError, ValueError) as exc:
        raise Refusal('RESOURCE_NATIVE_CAPABILITY_UNAVAILABLE', str(exc)) from exc
    if len(line) > _SHARE_MESSAGE_BYTES or not line.endswith(b'\n'):
        raise Refusal('RESOURCE_NATIVE_CAPABILITY_UNAVAILABLE', 'truncated issuer witness')
    try:
        response = json.loads(line)
    except ValueError as exc:
        raise Refusal('RESOURCE_NATIVE_CAPABILITY_UNAVAILABLE', 'invalid issuer witness') from exc
    if response.get('status') != 'NATIVE_CHILD_AUTHORIZED' or not isinstance(response.get('witness'), dict):
        raise Refusal(response.get('reason', 'RESOURCE_NATIVE_CHILD_REFUSED'), response.get('detail', ''))
    return response['witness']


def bounded_argv(argv: list[str], directory: Path, *, parent_query: bool = False) -> list[str]:
    """Source-owned native run boundary; no shell or arbitrary text parser."""
    lease = json.loads((directory / 'lease.json').read_text())
    if (directory/'closed.json').exists():
        raise Refusal('RESOURCE_LEASE_CLOSED', str(directory))
    if _process_state(lease.get('parent_pid'), lease.get('parent_start_ticks'))['fate'] != 'live':
        raise Refusal('RESOURCE_PARENT_GONE', str(directory))
    identity = lease.get('issuer_identity')
    host = _host_identity()
    if (not isinstance(identity, dict) or any(identity.get(key) != value for key, value in host.items())
            or identity.get('parent_pid') != lease.get('parent_pid')
            or identity.get('parent_start_ticks') != lease.get('parent_start_ticks')
            or identity.get('resource_source_sha256') != hashlib.sha256(Path(__file__).read_bytes()).hexdigest()):
        raise Refusal('RESOURCE_ISSUER_IDENTITY_UNKNOWN', str(directory))
    if os.environ.get(OWNER_ENV) != lease.get('nonce'):
        raise Refusal('RESOURCE_WORKER_OWNER_UNBOUND', str(os.getpid()))
    parent = os.getpid() == lease['parent_pid'] and start_ticks(os.getpid()) == lease['parent_start_ticks']
    if len(argv) < 2 or Path(argv[0]).name != 'docker':
        raise Refusal('RESOURCE_NATIVE_COMMAND_UNSUPPORTED', repr(argv[:2]))
    if argv[1] != 'run':
        # The actual issuer may query ownership without borrowing a worker.
        # Registered workers retain their own authority for native queries.
        if not parent:
            _invoking_worker(directory, lease)
        if argv[1] in ('inspect', 'info', 'version') or argv[1:3] == ['image', 'inspect']:
            return list(argv)
        raise Refusal('RESOURCE_NATIVE_COMMAND_UNSUPPORTED', repr(argv[:2]))
    if parent:
        # Only the source-owned facts probe slot grants a parent container
        # query. A producer run still requires its actual Controller worker.
        if parent_query is not True:
            raise Refusal('RESOURCE_PARENT_RUN_UNBOUND', str(directory))
        readonly = ()
    else:
        readonly = _invoking_worker(directory, lease)['readonly_mounts']
    issued = _selected_share(directory)
    if issued is not None and lease.get('resource_scope') != 'source-fixture':
        if not isinstance(issued.get('host_scope'), dict):
            raise Refusal('RESOURCE_SHARE_KERNEL_BOUNDARY_UNBOUND', 'missing issuer-bound host scope')
        _require_share_kernel(lease, expected_scope=issued['host_scope'])
    _bind_daemon(directory, argv[0])
    result = list(argv)
    # Preserve caller argv. Only add absent admission controls; refuse a
    # conflicting quota instead of silently rewriting another executor.
    additions = []
    quota = issued['request'] if issued is not None else lease
    cpuset = issued['cpuset'] if issued is not None else sorted(os.sched_getaffinity(0))[:lease['cpus']]
    if not set(cpuset).issubset(os.sched_getaffinity(0)):
        raise Refusal('RESOURCE_SHARE_WORKER_UNBOUND', 'current worker affinity changed')
    wanted = {'--cpus': str(quota['cpus']),
              '--cpuset-cpus': ','.join(map(str, cpuset)),
              '--memory': f"{quota['container_ram_mb']}m",
              '--memory-swap': f"{quota['container_ram_mb']}m"}
    for flag, expected in wanted.items():
        values = [result[i+1] for i, item in enumerate(result[:-1]) if item == flag]
        values += [item.split('=',1)[1] for item in result if item.startswith(flag+'=')]
        if values and (len(values) != 1 or values[0] != expected):
            raise Refusal('RESOURCE_CALLER_QUOTA_CONFLICT', flag)
        if not values:
            additions += [flag, expected]
    cid = directory / (uuid.uuid4().hex + '.cid')
    mounts = []
    # Mounts belong to this invocation, even if a sibling replaced worker.json.
    for source, target in readonly:
        mounts += ['-v', source + ':' + target + ':ro']
    for flag, expected in (('--network','none'),('--user',f'{os.getuid()}:{os.getgid()}')):
        values = [result[i+1] for i,item in enumerate(result[:-1]) if item==flag]
        values += [item.split('=',1)[1] for item in result if item.startswith(flag+'=')]
        if values and (len(values)!=1 or values[0]!=expected):
            raise Refusal('RESOURCE_CALLER_ISOLATION_CONFLICT',flag)
        if not values:
            additions += [flag,expected]
    selected = _SELECTED_SHARE.get()
    if selected is not None:
        _share_rpc(directory, 'command', share=asdict(selected[1]), cid_path=str(cid))
    else:
        _share_rpc(directory, 'whole-command', cid_path=str(cid))
    return [*result[:2], *additions,
            '--label', LABEL + '=' + lease['nonce'], '--cidfile', str(cid), *mounts, *result[2:]]


def skip_first_argv(argv: list[str]) -> list[str]:
    """Port only the existing LC/facts entrypoint slot to the image's --skip.

    No arbitrary argv is rewritten: unknown option shapes or entrypoints
    refuse. The boundary records both submitted native and admitted argv.
    """
    if len(argv)<2 or argv[1]!='run' or '--entrypoint' not in argv:
        return list(argv)
    valued = {'-v','--volume','-e','--env','--name','--network','--user','--label',
              '--cidfile','--memory','--memory-swap','--cpus','--cpuset-cpus','--entrypoint'}
    unary = {'--rm','-i','-t','--read-only'}
    prefix, entrypoint, i = list(argv[:2]), None, 2
    while i<len(argv) and argv[i].startswith('-'):
        item=argv[i]; flag=item.split('=',1)[0]
        if flag in valued:
            value=item.split('=',1)[1] if '=' in item else argv[i+1]
            if flag=='--entrypoint':
                if entrypoint is not None: raise Refusal('RESOURCE_ENTRYPOINT_AMBIGUOUS',repr(argv))
                entrypoint=value
            else: prefix.extend([flag,value])
            i+=1 if '=' in item else 2
        elif flag in unary:
            prefix.append(item);i+=1
        else:
            raise Refusal('RESOURCE_NATIVE_OPTION_UNSUPPORTED',item)
    if i>=len(argv) or entrypoint not in ('python3','sh','bash','timeout'):
        raise Refusal('RESOURCE_ENTRYPOINT_UNSUPPORTED',str(entrypoint))
    image=argv[i]
    if not (re.fullmatch(r'sha256:[0-9a-f]{64}',image) or re.fullmatch(r'.+@sha256:[0-9a-f]{64}',image)):
        raise Refusal('RESOURCE_IMAGE_NOT_IMMUTABLE',image)
    return [*prefix,image,'--skip',entrypoint,*argv[i+1:]]


@contextmanager
def native_boundary(directory: Path, expected_sha256: str | None = None,
                    *, binding: NativeChildBinding | None = None):
    """Bound both existing native step and image-facts probe executors."""
    if socket.gethostname().split('.')[0].lower() in ('8hd-8',):
        raise Refusal('RESOURCE_HOST_SOURCE_ONLY', socket.gethostname())
    import librelane_contract as lc
    import librelane_image_facts as facts
    import _docker_memory as dmem
    original_run, original_facts = lc.run_container, facts._run
    original_memory = dmem.docker_memory_flags
    quota = json.loads((directory/'lease.json').read_text())
    if quota.get('resource_scope') == 'source-fixture':
        raise Refusal('RESOURCE_FIXTURE_NATIVE_FORBIDDEN', str(directory))
    expected_sha256 = expected_sha256 or hashlib.sha256((directory / 'lease.json').read_bytes()).hexdigest()

    def memory_flags(env=None):
        # Each sibling's selection is a ContextVar; no environment quota grant.
        issued = _selected_share(directory)
        memory = (issued['request'] if issued is not None else quota)['container_ram_mb']
        return ['--memory', f'{memory}m', '--memory-swap', f'{memory}m']

    def observed(function, argv, *args, parent_query=False, **kwargs):
        if hashlib.sha256((directory / 'lease.json').read_bytes()).hexdigest() != expected_sha256:
            raise Refusal('RESOURCE_LEASE_CHANGED', str(directory))
        issued = _selected_share(directory)
        bounded = skip_first_argv(
            native_argv(argv, directory, binding=binding,
                        expected_lease_sha256=expected_sha256, parent_query=parent_query)
            if binding is not None else
            bounded_argv(argv, directory, parent_query=parent_query))
        record = dict(original_native_argv=list(argv), submitted_argv=bounded, worker_pid=os.getpid(),
                      native_route='source-owned LC/facts slot to pinned image --skip; no entrypoint override',
                      started_ns=time.monotonic_ns(), rc=None,
                      resource_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      native_source_sha256=hashlib.sha256(Path(lc.__file__).read_bytes()).hexdigest())
        if issued is not None:
            record.update(issued_share_id=issued['share_id'],
                issued_share_sha256=hashlib.sha256(_SELECTED_SHARE.get()[1].binding.encode()).hexdigest(),
                issued_share_binding=issued)
        if binding is not None:
            record['native_binding'] = asdict(binding)
        try:
            completed = function(bounded, *args, **kwargs)
            record.update(rc=completed.returncode,
                stdout_sha256=hashlib.sha256((completed.stdout or '').encode()).hexdigest(),
                stderr_sha256=hashlib.sha256((completed.stderr or '').encode()).hexdigest())
            return completed
        except Exception as exc:
            record.update(refusal=str(exc))
            raise
        finally:
            record['ended_ns'] = time.monotonic_ns()
            with (directory / 'commands.jsonl').open('a') as stream:
                stream.write(json.dumps(record) + '\n')
            if hashlib.sha256((directory / 'lease.json').read_bytes()).hexdigest() != expected_sha256:
                raise Refusal('RESOURCE_LEASE_CHANGED', str(directory))

    def run(argv, **kwargs):
        return observed(original_run, argv, **kwargs)

    def probe(argv, *args, **kwargs):
        return observed(original_facts, argv, *args, parent_query=True, **kwargs)

    lc.run_container, facts._run = run, probe
    dmem.docker_memory_flags = memory_flags
    try:
        yield
    finally:
        lc.run_container, facts._run = original_run, original_facts
        dmem.docker_memory_flags = original_memory


if __name__ == '__main__' and len(sys.argv)>1 and sys.argv[1]=='--held-host-worker':
    _held_host_worker()
