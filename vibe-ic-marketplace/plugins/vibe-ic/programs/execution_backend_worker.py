#!/usr/bin/env python3
"""One frozen backend job, source-bound, leased, finite, with raw native evidence."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import json
import os
import resource
import shutil
import sys
import time
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import execution_modes as em
import execution_backend_snapshot as snap
from execution_adapters_backend import ROWS
from execution_backend_gates import evaluate, contracts, states


def _translate(value, mappings):
    if isinstance(value, list):
        return [_translate(x, mappings) for x in value]
    if isinstance(value, dict):
        return {k: _translate(v, mappings) for k, v in value.items()}
    if isinstance(value, str):
        for old, new in sorted(mappings, key=lambda x: -len(x[0])):
            if value == old or value.startswith(old + '/'):
                return new + value[len(old):]
    return value


def _enforce_issued_host_share(spec):
    """Bind the real issued lease and cap this process to its host RAM share."""
    lease_value = spec.get('lease_directory')
    expected_sha = spec.get('lease_sha256')
    if not isinstance(lease_value, str) or not Path(lease_value).is_absolute():
        raise em.Refusal('BACKEND_ISSUED_LEASE_UNAVAILABLE', 'lease directory is not absolute')
    if (not isinstance(expected_sha, str) or len(expected_sha) != 64
            or any(c not in '0123456789abcdef' for c in expected_sha)):
        raise em.Refusal('BACKEND_ISSUED_LEASE_UNAVAILABLE', 'lease SHA-256 is invalid')
    lease = Path(lease_value)
    lease_file = lease / 'lease.json'
    if lease.is_symlink() or lease_file.is_symlink() or not lease_file.is_file():
        raise em.Refusal('BACKEND_ISSUED_LEASE_UNAVAILABLE', str(lease_file))
    if snap.sha(lease_file) != expected_sha:
        raise em.Refusal('BACKEND_LIVE_LEASE_CHANGED', str(lease_file))
    try:
        issued = json.loads(lease_file.read_text())
    except (OSError, ValueError) as exc:
        raise em.Refusal('BACKEND_ISSUED_LEASE_UNAVAILABLE', str(exc)) from exc
    total = issued.get('ram_mb')
    host = issued.get('host_ram_mb')
    container = issued.get('container_ram_mb')
    if any(type(value) is not int or value <= 0 for value in (total, host, container)):
        raise em.Refusal('BACKEND_ISSUED_HOST_SHARE_INVALID', 'RAM shares must be positive integers')
    parameters = spec.get('parameters')
    if (not isinstance(parameters, dict) or type(parameters.get('ram_mb')) is not int
            or parameters['ram_mb'] != total):
        raise em.Refusal('BACKEND_ISSUED_HOST_SHARE_UNBOUND', 'request total differs from issued lease')
    if host + container != total:
        raise em.Refusal('BACKEND_ISSUED_HOST_SHARE_UNBOUND',
                         f'issued RAM split does not match request total: {total}={host}+{container}')
    share_bytes = host * 1024 * 1024
    try:
        old_soft, old_hard = resource.getrlimit(resource.RLIMIT_AS)
    except (AttributeError, OSError, ValueError) as exc:
        raise em.Refusal('BACKEND_HOST_RLIMIT_UNAVAILABLE', str(exc)) from exc
    infinity = resource.RLIM_INFINITY
    def bounded(limit):
        if type(limit) is not int:
            raise em.Refusal('BACKEND_HOST_RLIMIT_INVALID', repr(limit))
        if limit == infinity:
            return share_bytes
        if limit <= 0:
            raise em.Refusal('BACKEND_HOST_RLIMIT_INVALID', repr(limit))
        return min(limit, share_bytes)
    new_soft, new_hard = bounded(old_soft), bounded(old_hard)
    if new_soft > new_hard:
        raise em.Refusal('BACKEND_HOST_RLIMIT_INVALID', 'soft RLIMIT_AS exceeds hard limit')
    try:
        resource.setrlimit(resource.RLIMIT_AS, (new_soft, new_hard))
        actual_soft, actual_hard = resource.getrlimit(resource.RLIMIT_AS)
    except (OSError, ValueError) as exc:
        raise em.Refusal('BACKEND_HOST_RLIMIT_SET_FAILED', str(exc)) from exc
    if (type(actual_soft) is not int or type(actual_hard) is not int
            or actual_soft <= 0 or actual_hard <= 0
            or actual_soft != new_soft or actual_hard != new_hard
            or actual_soft > share_bytes or actual_hard > share_bytes):
        raise em.Refusal('BACKEND_HOST_RLIMIT_READBACK_FAILED', repr((actual_soft, actual_hard)))
    return lease, {
        'pid': os.getpid(), 'resource': 'RLIMIT_AS',
        'issued_lease_sha256': expected_sha,
        'issued_total_ram_mb': total, 'issued_host_ram_mb': host,
        'issued_container_ram_mb': container,
        'previous_soft_bytes': old_soft, 'previous_hard_bytes': old_hard,
        'actual_soft_bytes': actual_soft, 'actual_hard_bytes': actual_hard,
        'per_process_address_space_only': True,
        'aggregate_host_cgroup_memory_max_bytes': None,
        'aggregate_host_cgroup_memory_status': 'NOT_ENFORCED_BY_RLIMIT_AS; capacity must provide host cgroup',
    }


@contextmanager
def _native_producer_scope(binding, step_id):
    """Authorize nested native callables only for this bound producer step."""
    marker = 'VIBEIC_EXECUTION_NATIVE_STEP'
    raw_binding = os.environ.get('VIBEIC_EXECUTION_BINDING')
    try:
        live_binding = json.loads(raw_binding) if raw_binding is not None else None
    except (TypeError, ValueError) as exc:
        raise em.Refusal('BACKEND_NATIVE_REENTRY_UNBOUND', 'execution binding is invalid') from exc
    if (not isinstance(binding, dict) or not isinstance(live_binding, dict)
            or live_binding != binding or binding.get('step_id') != step_id
            or not isinstance(step_id, str) or not step_id):
        raise em.Refusal('BACKEND_NATIVE_REENTRY_UNBOUND', str(step_id))
    previous = os.environ.get(marker)
    os.environ[marker] = step_id
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(marker, None)
        else:
            os.environ[marker] = previous


def _finish_step17_canonical_pending(producer, gates):
    """Close the runner helper's pending result only on current full evidence."""
    if not producer.get('canonical_validation_pending'):
        return producer
    if producer.get('producer_verdict') == 'FAIL' or (gates or {}).get('status') == 'FAIL':
        producer['verdict'] = 'FAIL'
    elif (producer.get('producer_verdict') == 'PASS'
            and producer.get('measurement_verdict') == 'PASS'
            and (gates or {}).get('status') == 'PASS'):
        producer['verdict'] = 'PASS'
    else:
        producer['verdict'] = 'NOT_MEASURED'
    return producer


def execute(inputs: Path, outputs: Path):
    binding = json.loads(os.environ['VIBEIC_EXECUTION_BINDING'])
    spec = json.loads((inputs / 'request.json').read_text())
    sid = spec['step_id']
    if sid != binding['step_id'] or spec['source_sha'] != binding['source_sha']:
        raise em.Refusal('BACKEND_WORKER_UNBOUND', sid)
    actual = {str(p.relative_to(inputs)): snap.sha(p) for p in inputs.rglob('*') if p.is_file()}
    if actual != binding['inputs'] or any(p.is_symlink() for p in inputs.rglob('*')):
        raise em.Refusal('BACKEND_FROZEN_INPUT_CHANGED', sid)
    for path, expected in spec['source_files'].items():
        p = Path(path)
        if not p.is_file() or p.is_symlink() or snap.sha(p) != expected:
            raise em.Refusal('BACKEND_WORKER_SOURCE_CHANGED', path)
    from execution_adapters_backend import POLICY
    if snap.sha(POLICY) != spec['policy_sha256']:
        raise em.Refusal('BACKEND_POLICY_CHANGED', sid)
    # Enforce this process's issued host share before copying or producing.
    lease, host_share = _enforce_issued_host_share(spec)
    project = outputs / 'project'
    shutil.copytree(inputs / 'project', project)
    for name, row in spec['lexical'].items():
        if name.startswith('project/') and row['kind'] == 'directory':
            (project / snap.relative(name.removeprefix('project/'))).mkdir(parents=True, exist_ok=True)
    for p in project.rglob('*'):
        if p.is_file():
            p.chmod(0o600)
    mappings = [(spec['project'], str(project)), (spec['pdk_root'], str(inputs / 'pdk'))]
    for name, old in spec['extra_inputs'].items():
        mappings.append((str(old), str(inputs / 'external' / name)))
    params = _translate(spec['parameters'], mappings)
    params.update(step_id=sid, image_id=spec['image_id'], pdk_root=str(inputs / 'pdk'))
    if sid == '17':
        params['_step17_original_project'] = spec['project']
        params['_step17_path_mappings'] = mappings
    params['native_facts'] = spec['parameters']['native_facts']
    params['native_facts_file'] = Path(inputs / 'external/native_facts.json')
    from execution_adapters_backend import _native_facts
    if _native_facts(params, {})['image_id'] != spec['image_id']:
        raise em.Refusal('BACKEND_FROZEN_NATIVE_FACTS_CHANGED', sid)
    if snap.sha(Path(params['native_facts_file'])) != spec['native_facts_sha256']:
        raise em.Refusal('BACKEND_FROZEN_NATIVE_FACTS_CHANGED', sid)
    # Path-valued manifests must refer to this job's frozen inputs; nested
    # relative references are preserved. Producer logs/receipts remain raw.
    for p in project.rglob('*.json'):
        try:
            old = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        new = _translate(old, mappings)
        if new != old:
            snap.dump(p, new)
    before_inputs = {}
    before = snap.population(project, 'project', before_inputs)
    row = ROWS[sid]['canonical_row']
    # Clear prior claims at this row's output paths. In-place INPUT/OUTPUT
    # overlap is retained, but must appear in this run's native handoff.
    required_inputs = [x.get('path', '') for x in row.get('required_inputs', [])]
    for pattern in row.get('required_outputs', []):
        for alternative in pattern.split(' OR '):
            for p in project.glob(alternative.strip()):
                if p.is_file() and str(p.relative_to(project)) not in required_inputs:
                    p.unlink()
    if snap.sha(lease / 'lease.json') != spec['lease_sha256']:
        raise em.Refusal('BACKEND_LIVE_LEASE_CHANGED', sid)
    # NO fallback: absent shared enforcement is not admission.
    import execution_resource_lease as lease_api
    from execution_backend_producers import produce
    started = time.monotonic_ns()
    lease_api.record_worker(
        lease, readonly_mounts=[[str(inputs.resolve()), str(inputs.resolve())]])
    try:
        with lease_api.native_boundary(lease, expected_sha256=spec['lease_sha256']):
            with em.issued_child_scope(project, sid):
                with _native_producer_scope(binding, sid):
                    producer = produce(project, params)
                    gates = evaluate(project, row)
                    if sid == '17' and isinstance(producer, dict):
                        producer = _finish_step17_canonical_pending(producer, gates)
    except Exception as exc:
        producer = {'verdict': 'NOT_MEASURED', 'detail': type(exc).__name__ + ': ' + str(exc)}
        gates = {'status': 'NOT_MEASURED', 'ledger': [], 'step': {'status': 'NOT_MEASURED'}}
    after_inputs = {}
    after = snap.population(project, 'project', after_inputs)
    changed = {k: v for k, v in after.items() if v.get('kind') == 'file' and before.get(k) != v}
    # Native receipts must come from this invocation and be included in the
    # exact selected generation; old receipts in the snapshot cannot vote.
    receipts = [k.removeprefix('project/') for k in changed if k.endswith(
                ('vibeic_receipt.json', 'native_provenance.json', 'invocation.log', '.native.json'))]
    try:
        resolved = contracts(project, row)
    except em.Refusal as exc:
        resolved = {}
        producer = {'verdict': 'FAIL' if producer['verdict'] == 'FAIL' else 'NOT_MEASURED',
                    'detail': producer.get('detail', '') + '; ' + str(exc)}
    touched_contract = {name for files in resolved.values() for name in files}
    for name in touched_contract:
        if 'project/' + name not in changed:
            producer = {'verdict': 'FAIL' if producer['verdict'] == 'FAIL' else 'NOT_MEASURED',
                        'detail': producer.get('detail', '') + '; UNCHANGED_OUTPUT_WITHOUT_CURRENT_PRODUCER: ' + name}
    output_hashes = {k: v['sha256'] for k, v in changed.items()}
    if not receipts:
        producer = {'verdict': 'FAIL' if producer['verdict'] == 'FAIL' else 'NOT_MEASURED',
                    'detail': 'NO_CURRENT_NATIVE_RECEIPT; ' + producer.get('detail', '')}
    native_commands = []
    commands_file = lease / 'commands.jsonl'
    if commands_file.is_file():
        for line in commands_file.read_text().splitlines():
            command = json.loads(line)
            if command.get('worker_pid') != os.getpid() or command.get('started_ns', 0) < started:
                continue
            argv = command.get('submitted_argv', [])
            if '--cidfile' in argv:
                cidfile = Path(argv[argv.index('--cidfile') + 1])
                command['cid'] = cidfile.read_text().strip() if cidfile.is_file() else ''
            native_commands.append(command)
    publication, removed = {}, []
    for key in changed:
        name = key.removeprefix('project/')
        try:
            snap.adoption_target(name, tuple(spec['adoption_paths']))
        except em.Refusal:
            # Selected-generation scratch remains hash-bound evidence, but is
            # not copied into canonical project state by the consumer.
            continue
        publication[name] = key
    for key, value in before.items():
        if value.get('kind') != 'file' or key in after:
            continue
        name = key.removeprefix('project/')
        try:
            snap.adoption_target(name, tuple(spec['adoption_paths']))
        except em.Refusal:
            continue
        removed.append(name)
    result = {'schema': 'execution_backend_result/1', 'binding': binding, 'step_id': sid,
              'image_id': spec['image_id'], 'native_commands': native_commands,
              'producer_verdict': producer['verdict'], 'detail': producer.get('detail', ''),
              'gates': states(binding, gates, producer['verdict']), 'gate_consumer': gates,
              'contracts': resolved, 'outputs': output_hashes, 'native_receipts': receipts,
              'publication': publication, 'removed': removed,
              'resource': {'pid': os.getpid(), 'started_ns': started, 'ended_ns': time.monotonic_ns(),
                           'affinity': sorted(os.sched_getaffinity(0)),
                           'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                           'issued_host_share_rlimit_as': host_share}}
    snap.dump(outputs / 'backend_result.json', result)
    # Protocol completion != design PASS. Controller consumes result verdict.
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--inputs', required=True, type=Path)
    p.add_argument('--outputs', required=True, type=Path)
    a = p.parse_args()
    return execute(a.inputs, a.outputs)


if __name__ == '__main__':
    raise SystemExit(main())
