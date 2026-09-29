"""One host admission lock and recoverable, individually owned OCI containers.

No daemon and no licensed executor. A crashed owner is recovered before another
production dispatch can acquire this host's lease. Docker exec is unsupported.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time
import uuid

from _atomic_artefact import write_json
from execution_modes import Budget, Refusal

LABEL = 'io.vibeic.execution-lease'
OWNER_ENV = 'VIBEIC_RESOURCE_LEASE_OWNER'


def start_ticks(pid: int) -> str | None:
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def _command(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=15)


def recover(directory: Path) -> list[dict]:
    manifest = json.loads((directory / 'lease.json').read_text())
    nonce = manifest['nonce']
    observations = []
    worker = directory / 'worker.json'
    if worker.is_file():
        owner = json.loads(worker.read_text())
        pid = owner['pid']
        if (type(pid) is int and pid != os.getpid() and
                start_ticks(pid) == owner['start_ticks']):
            # Kernel-held initial child environment, not an editable PID row,
            # establishes ownership. Read only our field; never log environment.
            owned = (OWNER_ENV + '=' + nonce).encode()
            try:
                present = owned in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0')
            except OSError:
                present = False
            if not present:
                raise Refusal('RESOURCE_WORKER_OWNER_UNBOUND', str(pid))
            if os.getpgid(pid) != pid:
                raise Refusal('RESOURCE_WORKER_GROUP_UNBOUND', str(pid))
            os.killpg(pid, signal.SIGTERM)
            deadline = time.monotonic() + 2
            while start_ticks(pid) == owner['start_ticks'] and time.monotonic() < deadline:
                time.sleep(.02)
            if start_ticks(pid) == owner['start_ticks']:
                os.killpg(pid, signal.SIGKILL)
    for path in sorted(directory.glob('*.cid')):
        cid = path.read_text().strip()
        if not re.fullmatch('[0-9a-f]{64}', cid):
            raise Refusal('RESOURCE_CONTAINER_ID_UNBOUND', str(path))
        inspected = _command(['docker', 'inspect', '--format', '{{json .Config.Labels}}', cid])
        if inspected.returncode:
            if 'No such' not in inspected.stderr:
                raise Refusal('RESOURCE_RECOVERY_UNMEASURED', inspected.stderr[-300:])
            observations.append(dict(cid=cid, fate='already-absent'))
            continue
        labels = json.loads(inspected.stdout)
        if labels.get(LABEL) != nonce:
            raise Refusal('RESOURCE_CONTAINER_OWNER_MISMATCH', cid)
        removed = _command(['docker', 'rm', '-f', cid])
        if removed.returncode:
            raise Refusal('RESOURCE_RECOVERY_FAILED', removed.stderr[-300:])
        observations.append(dict(cid=cid, fate='removed', rc=removed.returncode))
    write_json(directory / 'recovery.json', observations)
    write_json(directory / 'closed.json', dict(nonce=nonce, recovered=True))
    return observations


@contextmanager
def host_lease(budget: Budget):
    root = Path(tempfile.gettempdir()) / f'vibeic-execution-{os.getuid()}'
    root.mkdir(mode=0o700, exist_ok=True)
    if root.is_symlink() or root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077:
        raise Refusal('RESOURCE_NAMESPACE_UNSAFE', str(root))
    lock_path = root / 'admission.lock'
    if lock_path.is_symlink():
        raise Refusal('RESOURCE_NAMESPACE_UNSAFE', str(lock_path))
    with lock_path.open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Refusal('RESOURCE_HOST_BUSY', str(root)) from exc
        for previous in root.glob('lease-*'):
            if not (previous / 'closed.json').exists():
                recover(previous)
        available_mb = os.sysconf('SC_AVPHYS_PAGES') * os.sysconf('SC_PAGE_SIZE') // 1048576
        if budget.cpus > len(os.sched_getaffinity(0)) or budget.ram_mb > available_mb:
            raise Refusal('RESOURCE_HOST_CAPACITY_UNAVAILABLE', repr(budget))
        if budget.licenses:
            raise Refusal('DURABLE_LICENSE_EXECUTOR_NOT_IMPLEMENTED', repr(budget.licenses))
        directory = root / ('lease-' + uuid.uuid4().hex)
        directory.mkdir(mode=0o700)
        manifest = dict(nonce=uuid.uuid4().hex, parent_pid=os.getpid(),
                        parent_start_ticks=start_ticks(os.getpid()),
                        cpus=budget.cpus, ram_mb=budget.ram_mb,
                        host_ram_mb=min(512, budget.ram_mb // 2),
                        container_ram_mb=budget.ram_mb - min(512, budget.ram_mb // 2), licenses={})
        write_json(directory / 'lease.json', manifest)
        previous_owner = os.environ.get(OWNER_ENV)
        os.environ[OWNER_ENV] = manifest['nonce']
        try:
            yield directory
        finally:
            try:
                recover(directory)
            finally:
                if previous_owner is None:
                    os.environ.pop(OWNER_ENV, None)
                else:
                    os.environ[OWNER_ENV] = previous_owner


def bounded_argv(argv: list[str], directory: Path) -> list[str]:
    """Source-owned native run boundary; no shell or arbitrary text parser."""
    lease = json.loads((directory / 'lease.json').read_text())
    if start_ticks(lease['parent_pid']) != lease['parent_start_ticks']:
        raise Refusal('RESOURCE_PARENT_GONE', str(directory))
    if len(argv) < 2 or Path(argv[0]).name != 'docker':
        raise Refusal('RESOURCE_NATIVE_COMMAND_UNSUPPORTED', repr(argv[:2]))
    if argv[1] != 'run':
        # Read-only image/ownership queries do not create a resource consumer.
        if argv[1] in ('inspect', 'image', 'info', 'version'):
            return list(argv)
        raise Refusal('RESOURCE_NATIVE_COMMAND_UNSUPPORTED', repr(argv[:2]))
    result = list(argv)
    # Native helpers already declare memory. Remove their quota options before
    # applying the admitted lease; the actual command is always a typed argv.
    for flag in ('--memory', '--memory-swap', '--cpus', '--cpuset-cpus'):
        i = 2
        while i < len(result):
            if result[i] == flag:
                del result[i:i + 2]
            elif result[i].startswith(flag + '='):
                del result[i]
            else:
                i += 1
    cid = directory / (uuid.uuid4().hex + '.cid')
    mounts = []
    worker_path = directory / 'worker.json'
    if worker_path.is_file():
        # Native inputs are mounted after the parent project mount in Docker's
        # destination hierarchy; these are source-owned immutable subtrees.
        for source, target in json.loads(worker_path.read_text()).get('readonly_mounts', []):
            mounts += ['-v', source + ':' + target + ':ro']
    return [*result[:2], '--cpus', str(lease['cpus']),
            '--cpuset-cpus', ','.join(map(str, sorted(os.sched_getaffinity(0))[:lease['cpus']])),
            '--memory', f"{lease['container_ram_mb']}m", '--memory-swap', f"{lease['container_ram_mb']}m",
            '--label', LABEL + '=' + lease['nonce'], '--cidfile', str(cid), *mounts, *result[2:]]


@contextmanager
def native_boundary(directory: Path, expected_sha256: str | None = None):
    """Bound both existing native step and image-facts probe executors."""
    import librelane_contract as lc
    import librelane_image_facts as facts
    original_run, original_facts = lc.run_container, facts._run
    expected_sha256 = expected_sha256 or hashlib.sha256((directory / 'lease.json').read_bytes()).hexdigest()

    def observed(function, argv, *args, **kwargs):
        if hashlib.sha256((directory / 'lease.json').read_bytes()).hexdigest() != expected_sha256:
            raise Refusal('RESOURCE_LEASE_CHANGED', str(directory))
        bounded = bounded_argv(argv, directory)
        record = dict(submitted_argv=bounded, worker_pid=os.getpid(),
                      started_ns=time.monotonic_ns(), rc=None,
                      resource_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      native_source_sha256=hashlib.sha256(Path(lc.__file__).read_bytes()).hexdigest())
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
        return observed(original_facts, argv, *args, **kwargs)

    lc.run_container, facts._run = run, probe
    try:
        yield
    finally:
        lc.run_container, facts._run = original_run, original_facts
