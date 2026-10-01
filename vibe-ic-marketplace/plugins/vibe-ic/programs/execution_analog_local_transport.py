"""Local backend for legacy analog producers INSIDE one admitted OCI run.

This is a domain-owned backend binding, not a Docker executor. The outer
worker uses F1/P0 native_boundary and librelane_contract.run_container. These
callbacks keep existing producer commands intact, run them in that container,
and expose their actual rc/logs. No existing-container attachment is allowed.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
import threading

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import execution_modes as em


@contextmanager
def local_transport(receipts: Path, deadline: float):
    if os.environ.get('VIBEIC_F5_NATIVE_LOCAL') != '1':
        raise em.Refusal('ANALOG_NATIVE_LOCAL_BOUNDARY_REQUIRED', str(receipts))
    import _container_exec as ce
    import _docker_watchdog as watchdog
    original = {name: getattr(ce, name) for name in (
        'docker_exec_argv', 'run_in_container', 'run_in_container_supervised')}
    original_supervised = watchdog.run_docker_supervised
    receipts.parent.mkdir(parents=True, exist_ok=True)
    single_native = threading.RLock()

    def argv(container, *rest, opts=()):
        if container not in ('', 'host') or opts:
            raise em.Refusal('ANALOG_EXISTING_CONTAINER_FORBIDDEN', repr((container, opts)))
        return list(rest)

    def observed(container, command, *args, **kwargs):
        if container not in ('', 'host'):
            raise em.Refusal('ANALOG_EXISTING_CONTAINER_FORBIDDEN', container)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise em.Refusal('ANALOG_NATIVE_FINITE_DEADLINE', command)
        started = time.monotonic_ns()
        process = subprocess.Popen(['bash', '-lc', command], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, errors='replace')
        try:
            stdout, stderr = process.communicate(timeout=remaining)
        except subprocess.TimeoutExpired:
            # The admitted outer ephemeral container owns all descendants;
            # its source supervisor also reaps its exact CID on expiry.
            process.kill()
            stdout, stderr = process.communicate()
            with receipts.open('a') as stream:
                stream.write(json.dumps({'argv': ['bash', '-lc', command],
                    'pid': process.pid, 'rc': process.returncode, 'stop': 'DEADLINE',
                    'started_ns': started, 'ended_ns': time.monotonic_ns()}) + '\n')
            raise em.Refusal('ANALOG_NATIVE_FINITE_DEADLINE', command)
        with receipts.open('a') as stream:
            stream.write(json.dumps({'argv': ['bash', '-lc', command], 'pid': process.pid,
                'rc': process.returncode, 'started_ns': started, 'ended_ns': time.monotonic_ns(),
                'stdout': stdout, 'stderr': stderr}) + '\n')
        return subprocess.CompletedProcess(['bash', '-lc', command], process.returncode, stdout, stderr)

    def run(container, command, *args, **kwargs):
        # Even if a legacy corner planner creates threads, an author/native
        # finite reservation never infers process fan-out from CPU count.
        with single_native:
            return observed(container, command, *args, **kwargs)

    def supervised(container, command, marker, **kwargs):
        result = run(container, command)
        return result.returncode, result.stdout, result.stderr

    ce.docker_exec_argv = argv
    ce.run_in_container = ce.run_in_container_supervised = run
    watchdog.run_docker_supervised = supervised
    try:
        yield
    finally:
        for name, value in original.items():
            setattr(ce, name, value)
        watchdog.run_docker_supervised = original_supervised
