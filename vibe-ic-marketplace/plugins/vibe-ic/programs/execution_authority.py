"""Canonical, isolated CLI issuer. Imported modules only consume its live receipt.

The launcher enters with -I -S, validates tracked entry objects and the owner
route, and issues exactly one immutable request/route. Its inherited memfd is a
locator secret, never an environment assertion or a Python module token.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import struct
import subprocess
import sys
import tempfile
import threading

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
FD_ENV = 'VIBEIC_EXECUTION_CAP_FD'
SOCKET_ENV = 'VIBEIC_EXECUTION_AUTH_SOCKET'


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def tracked(path):
    relative = str(path.resolve().relative_to(ROOT))
    blob = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD:' + relative], text=True).strip()
    content = path.read_bytes()
    observed = hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()
    if blob != observed or path.is_symlink():
        raise ValueError('dirty canonical entry: ' + relative)
    return blob


def read_line(sock):
    pending = b''
    sock.settimeout(2)
    while b'\n' not in pending:
        chunk = sock.recv(4096)
        if not chunk:
            raise ValueError('authority EOF')
        pending += chunk
        if len(pending) > 131072:
            raise ValueError('authority response too large')
    return json.loads(pending.split(b'\n', 1)[0])


def consume():
    """Verify the live isolated issuer and return its original issued payload."""
    from execution_modes import Refusal
    try:
        fd = int(os.environ[FD_ENV])
        credential = json.loads(os.pread(fd, 4096, 0))
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(2)
            client.connect(os.environ[SOCKET_ENV])
            pid = struct.unpack('3i', client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[0]
            argv = [s.decode() for s in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0') if s]
            exe = Path(os.readlink(f'/proc/{pid}/exe')).resolve()
            if (len(argv) < 5 or Path(argv[0]).resolve() != exe or
                    argv[1:4] != ['-I', '-S', str(HERE / 'execution_authority.py')] or
                    pid != credential['pid'] or
                    Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19] != credential['start_ticks']):
                raise ValueError('not the isolated canonical launcher')
            entry_blob = tracked(HERE / 'execution_authority.py')
            nonce = secrets.token_hex(32)
            client.sendall(json.dumps(dict(token=credential['token'], nonce=nonce)).encode() + b'\n')
            result = read_line(client)
            if (result.get('nonce') != nonce or result.get('ok') is not True or
                    result['payload']['issuer_blob'] != entry_blob):
                raise ValueError('live authority challenge refused')
            # All canonical entry objects and the exact source commit remain
            # current. Rehashed caller files cannot change the issuer's ledger.
            payload = result['payload']
            head = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
            if head != payload['route']['source_sha']:
                raise ValueError('source invocation drift')
            for relative, blob in payload['source_blobs'].items():
                content = (ROOT / relative).read_bytes()
                current = hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()
                if current != blob:
                    raise ValueError('canonical source drift')
            owner = Path(payload['route']['project']) / 'input/step_0_5ic_answers.json'
            if hashlib.sha256(owner.read_bytes()).hexdigest() != payload['route']['project_digest']:
                raise ValueError('owner route changed')
            return payload
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        raise Refusal('REQUEST_CAPABILITY_INVALID', str(exc)) from exc


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--route', choices=('ic', 'ip'), required=True)
    parser.add_argument('--execution-mode', choices=('default', 'ultra'), required=True)
    parser.add_argument('--channel-fd', type=int)
    parser.add_argument('worker_argv', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    # Isolated Python deliberately starts without sitecustomize or PYTHONPATH.
    # Install only this tracked program directory after canonical code entry.
    sys.path.insert(0, str(HERE))
    # Verify the complete local importer closure before importing route code.
    import ast
    pending = [HERE / 'execution_authority.py', HERE / '_delivery_route.py', HERE / 'execution_policy.py', HERE / 'execution_modes.py', HERE / 'vibe_ic_one_shot_runner.py']
    objects = [line.split(' ', 1) for line in subprocess.check_output(['git', '-C', str(ROOT), 'ls-tree', '-r', '--format=%(objectname) %(path)', 'HEAD'], text=True).splitlines()]
    by_path = {path: blob for blob,path in objects}
    source_blobs = {}
    while pending:
        path = pending.pop()
        if str(path.relative_to(ROOT)) in source_blobs:
            continue
        relative = str(path.relative_to(ROOT))
        content = path.read_bytes()
        blob = hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()
        if by_path.get(relative) != blob:
            raise ValueError('canonical importer is not a tracked object: ' + relative)
        source_blobs[relative] = blob
        tree_ast = ast.parse(path.read_text())
        for node in ast.walk(tree_ast):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else ([node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            for name in names:
                dependency = HERE / (name.split('.')[0] + '.py')
                if dependency.is_file(): pending.append(dependency)
    import _delivery_route
    refusal = _delivery_route.admit(args.project, args.route)
    if refusal:
        raise ValueError(_delivery_route.refusal_message(refusal))
    owner = args.project.resolve() / 'input/step_0_5ic_answers.json'
    project_digest = hashlib.sha256(owner.read_bytes()).hexdigest()
    source = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    tree = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD^{tree}'], text=True).strip()
    flow = HERE.parent / 'flow/phase1_phase2_phase3.yaml'
    source_blobs[str(flow.relative_to(ROOT))] = tracked(flow)
    token = secrets.token_hex(32)
    invocation = secrets.token_hex(32)
    request = dict(schema=1, mode=args.execution_mode,
                   mode_label=args.execution_mode + '-mode',
                   intent_label='USER_EXPLICIT_ULTRA' if args.execution_mode == 'ultra' else 'PROGRAM_DEFAULT',
                   ultra_match=args.execution_mode == 'ultra', invocation_id=invocation,
                   issuer='canonical-isolated-launcher', user_evidence='--execution-mode ' + args.execution_mode)
    request['request_digest'] = sha(request)
    route = dict(schema=1, kind='issued-route', authority='canonical-route-authority',
                 issuer='canonical-isolated-launcher', ic_ip_path=args.route.upper(), route=args.route,
                 source_sha=source, source_tree=tree, project=str(args.project.resolve()),
                 project_digest=project_digest, request_digest=request['request_digest'],
                 intent_label=request['intent_label'], mode_intent=args.execution_mode,
                 invocation_id=invocation)
    route['current_pointer'] = sha({k: route[k] for k in ('source_sha', 'project_digest', 'request_digest', 'ic_ip_path')})
    route['route_digest'] = sha(route)
    payload = dict(request=request, route=route, source_blobs=source_blobs,
                   issuer_blob=tracked(HERE / 'execution_authority.py'))
    directory = tempfile.mkdtemp(prefix='vibeic-authority-')
    socket_path = directory + '/issuer.sock'
    server = socket.socket(socket.AF_UNIX)
    server.bind(socket_path)
    server.listen(16)
    fd = os.memfd_create('vibeic-issued-capability', os.MFD_ALLOW_SEALING)
    credential = dict(pid=os.getpid(), start_ticks=Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19], token=token)
    os.write(fd, json.dumps(credential).encode())
    import fcntl
    fcntl.fcntl(fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
    def respond(client):
        with client:
            try:
                challenge = read_line(client)
                ok = secrets.compare_digest(challenge.get('token', ''), token)
                client.sendall(json.dumps(dict(ok=ok, nonce=challenge.get('nonce'), payload=payload if ok else None)).encode() + b'\n')
            except (OSError, ValueError, TypeError):
                pass
    def serve():
        while True:
            client, _ = server.accept()
            threading.Thread(target=respond, args=(client,), daemon=True).start()
    threading.Thread(target=serve, daemon=True).start()
    worker_argv = args.worker_argv[1:] if args.worker_argv[:1] == ['--'] else args.worker_argv
    environment = dict(os.environ, **{FD_ENV: str(fd), SOCKET_ENV: socket_path})
    # Credentials cross only to this fixed canonical worker, after issuer code
    # has executed. No importable API sends a newly minted credential to its caller.
    inherited = (fd,) if args.channel_fd is None else (fd, args.channel_fd)
    command = [str(Path(sys.executable).resolve()), '-I', str(HERE / 'vibe_ic_one_shot_runner.py'), *worker_argv]
    try:
        return subprocess.call(command, env=environment, pass_fds=inherited)
    finally:
        server.close()
        os.close(fd)
        Path(socket_path).unlink(missing_ok=True)
        Path(directory).rmdir()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print('REFUSED: CANONICAL_ISSUANCE_INVALID: ' + str(exc), file=sys.stderr)
        sys.exit(2)
