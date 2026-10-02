"""Typed transport for the common production execution policy.

The policy is intentionally separate from the native implementation selector
(``direct``, ``librelane`` or ``dual``).  Front doors configure one request in
their live parent and pass the same typed values to children; children cannot
silently replace a parent request with shell text or a conflicting flag.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
from pathlib import Path
import secrets
import socket
import struct
import tempfile
import threading
import uuid

from execution_modes import Budget, Refusal, _verify_route_authority

ENV = 'VIBEIC_EXECUTION_REQUEST'
_CAPABILITY_FD_ENV = 'VIBEIC_EXECUTION_CAP_FD'
PROGRAM_DEFAULT = 'PROGRAM_DEFAULT'
USER_EXPLICIT_ULTRA = 'USER_EXPLICIT_ULTRA'
DEFAULT_MODE = 'default-mode'
ULTRA_MODE = 'ultra-mode'
_REGISTERED_ISSUER = None
_ISSUER_CONTEXT: dict | None = None
_ISSUED_REQUESTS: dict[str, dict] = {}
_ISSUED_CAPABILITIES: dict[str, tuple[object, socket.socket]] = {}
_CANONICAL_FRONTDOOR = (Path(__file__).with_name('vibe_ic_one_shot_runner.py').resolve())


def _host_cpus() -> int:
    return len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else (os.cpu_count() or 1)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _process_start_ticks(pid: int) -> str:
    try:
        tail = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return tail[19]
    except (OSError, IndexError, ValueError) as exc:
        raise Refusal('REQUEST_CAPABILITY_INVALID', f'process {pid} has no start time') from exc


def _canonical_source_sha256() -> str:
    try:
        return hashlib.sha256(_CANONICAL_FRONTDOOR.read_bytes()).hexdigest()
    except OSError as exc:
        raise Refusal('ULTRA_ISSUER_NOT_ALLOWED', str(_CANONICAL_FRONTDOOR)) from exc


def _canonical_process_cmdline(pid: int) -> str:
    try:
        return Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\0', b' ').decode()
    except (OSError, UnicodeDecodeError) as exc:
        raise Refusal('REQUEST_CAPABILITY_INVALID', f'process {pid} command line unavailable') from exc


def _default_payload() -> dict:
    payload = dict(schema=1, mode='default', mode_label=DEFAULT_MODE,
                   intent_label=PROGRAM_DEFAULT, ultra_match=False,
                   issuer='program-default')
    payload['request_digest'] = _digest(payload)
    return payload


def _register_frontdoor_issuer(authority: object) -> None:
    """Install the canonical runner's process-local private authority."""
    global _REGISTERED_ISSUER, _ISSUER_CONTEXT
    caller = inspect.currentframe().f_back if inspect.currentframe() else None
    caller_path = Path(caller.f_code.co_filename).resolve() if caller else None
    if caller_path != _CANONICAL_FRONTDOOR:
        raise Refusal('ULTRA_ISSUER_NOT_ALLOWED', 'registration must originate in the canonical front door')
    if _REGISTERED_ISSUER is not None and _REGISTERED_ISSUER is not authority:
        raise Refusal('ULTRA_ISSUER_CONFLICT', 'front-door issuer already registered')
    _REGISTERED_ISSUER = authority
    if _ISSUER_CONTEXT is None:
        _ISSUER_CONTEXT = dict(
            issuer_process=dict(
                pid=os.getpid(),
                start_ticks=_process_start_ticks(os.getpid()),
                source_path=str(_CANONICAL_FRONTDOOR),
                source_sha256=_canonical_source_sha256()),
            invocation_id=secrets.token_hex(16),
            issuer_role='canonical-frontdoor')


class _CapabilityServer:
    """Parent-owned live challenge endpoint for one issued request."""

    def __init__(self, sock: socket.socket, request_digest: str,
                 issuer_context: dict):
        self._sock = sock
        self._request_digest = request_digest
        self._issuer_context = issuer_context
        self._thread = threading.Thread(target=self._serve,
                                        name='vibeic-execution-capability',
                                        daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _serve(self) -> None:
        pending = b''
        try:
            while True:
                chunk = self._sock.recv(4096)
                if not chunk:
                    return
                pending += chunk
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    try:
                        request = json.loads(line.decode())
                    except (UnicodeDecodeError, ValueError):
                        request = {}
                    ok = (request.get('request_digest') == self._request_digest and
                          request.get('invocation_id') == self._issuer_context['invocation_id'] and
                          isinstance(request.get('nonce'), str) and
                          len(request['nonce']) >= 16)
                    response = dict(ok=ok, request_digest=self._request_digest
                                    if ok else None, nonce=request.get('nonce') if ok else None,
                                    invocation_id=self._issuer_context['invocation_id']
                                    if ok else None,
                                    issuer_process=self._issuer_context['issuer_process']
                                    if ok else None)
                    self._sock.sendall(json.dumps(response, sort_keys=True).encode() + b'\n')
        except (OSError, ValueError):
            return


def _verify_parent_capability(request_digest: str, receipt: dict) -> None:
    """Require the registered issuer's live endpoint for this invocation."""
    raw_fd = os.environ.get(_CAPABILITY_FD_ENV)
    if raw_fd is None:
        raise Refusal('REQUEST_CAPABILITY_REQUIRED', request_digest)
    process = receipt.get('issuer_process')
    invocation_id = receipt.get('invocation_id')
    if (receipt.get('issuer_role') != 'canonical-frontdoor' or
            not isinstance(process, dict) or
            not isinstance(invocation_id, str) or len(invocation_id) < 16 or
            process.get('source_path') != str(_CANONICAL_FRONTDOOR) or
            process.get('source_sha256') != _canonical_source_sha256()):
        raise Refusal('REQUEST_CAPABILITY_INVALID', 'unregistered issuer invocation')
    sock = None
    try:
        fd = int(raw_fd)
        sock = socket.socket(fileno=fd)
        peer_pid = struct.unpack('3i', sock.getsockopt(socket.SOL_SOCKET,
                                                        socket.SO_PEERCRED, 12))[0]
        if (peer_pid != process.get('pid') or
                _process_start_ticks(peer_pid) != str(process.get('start_ticks')) or
                str(_CANONICAL_FRONTDOOR) not in _canonical_process_cmdline(peer_pid)):
            raise Refusal('REQUEST_CAPABILITY_INVALID', request_digest)
        nonce = secrets.token_hex(16)
        sock.settimeout(2.0)
        sock.sendall(json.dumps(dict(request_digest=request_digest,
                                     invocation_id=invocation_id, nonce=nonce),
                                sort_keys=True).encode() + b'\n')
        pending = b''
        while b'\n' not in pending:
            chunk = sock.recv(4096)
            if not chunk:
                raise Refusal('REQUEST_CAPABILITY_INVALID', 'capability peer closed before response')
            pending += chunk
            if len(pending) > 65536:
                raise Refusal('REQUEST_CAPABILITY_INVALID', 'capability response exceeded bound')
        response = json.loads(pending.split(b'\n', 1)[0].decode())
        if (response.get('ok') is not True or
                response.get('request_digest') != request_digest or
                response.get('nonce') != nonce or
                response.get('invocation_id') != invocation_id or
                response.get('issuer_process') != process):
            raise Refusal('REQUEST_CAPABILITY_INVALID', request_digest)
    except Refusal:
        raise
    except (OSError, ValueError, UnicodeDecodeError, struct.error) as exc:
        raise Refusal('REQUEST_CAPABILITY_INVALID', request_digest) from exc
    finally:
        try:
            if sock is not None:
                sock.settimeout(None)
                sock.detach()
        except OSError:
            pass


def _load_parent_receipt(value: dict) -> dict:
    """Validate a parent-issued transport without trusting its labels."""
    digest = value.get('request_digest')
    receipt = value.get('request_receipt')
    path = value.get('request_receipt_path')
    if receipt is None and path:
        try:
            receipt = json.loads(Path(path).read_text())
        except (OSError, ValueError) as exc:
            raise Refusal('REQUEST_RECEIPT_INVALID', str(path)) from exc
    if (not isinstance(digest, str) or len(digest) != 64 or not isinstance(receipt, dict)
            or _digest({k: v for k, v in receipt.items() if k != 'request_digest'}) != digest
            or receipt.get('request_digest') != digest):
        raise Refusal('REQUEST_RECEIPT_INVALID', 'digest-bound parent receipt required')
    if path:
        receipt_path = Path(path)
        try:
            path_payload = json.loads(receipt_path.read_text())
            path_sha = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        except (OSError, ValueError) as exc:
            raise Refusal('REQUEST_RECEIPT_INVALID', str(receipt_path)) from exc
        if (not receipt_path.is_absolute() or receipt_path.is_symlink() or not receipt_path.is_file()
                or _digest(path_payload) != _digest(receipt)
                or value.get('request_receipt_sha256') != path_sha):
            raise Refusal('REQUEST_RECEIPT_INVALID', str(receipt_path))
    same_process = digest in _ISSUED_REQUESTS and _ISSUED_REQUESTS[digest] == receipt
    if not same_process:
        _verify_parent_capability(digest, receipt)
    return receipt


def request() -> dict:
    try:
        value = json.loads(os.environ[ENV]) if ENV in os.environ else {}
    except (TypeError, json.JSONDecodeError) as exc:
        raise Refusal('INVALID_EXECUTION_REQUEST', str(exc)) from exc
    if not isinstance(value, dict):
        raise Refusal('INVALID_EXECUTION_REQUEST', repr(value))
    if not value:
        payload = _default_payload()
        return dict(mode='default', mode_label=DEFAULT_MODE,
                    intent_label=PROGRAM_DEFAULT, ultra_match=False,
                    mode_intent='default', mode_explicit=False,
                    request_digest=payload['request_digest'], request_receipt=payload,
                    request_receipt_path=None, request_receipt_sha256=None,
                    cpus=min(4, _host_cpus()), ram_mb=4096,
                    workers=min(4, _host_cpus()), licenses={}, choice=None,
                    choice_wait_s=60, authority='PROGRAM_DEFAULT')
    selected = value.get('mode', 'default')
    if selected not in ('default', 'ultra'):
        raise Refusal('INVALID_EXECUTION_MODE', repr(selected))
    host = _host_cpus()
    cpus = value.get('cpus', min(4, host))
    ram = value.get('ram_mb', 4096)
    workers = value.get('workers', min(4, cpus) if isinstance(cpus, int) else 1)
    licenses = value.get('licenses', {})
    if (not isinstance(licenses, dict) or
            any(not isinstance(k, str) or not k or type(v) is not int or v < 0
                for k, v in licenses.items())):
        raise Refusal('INVALID_LICENSE_BUDGET', repr(licenses))
    Budget(cpus, ram, workers=workers, licenses=licenses)
    if cpus > host:
        raise Refusal('HOST_CPU_BUDGET_UNAVAILABLE', str(cpus))
    wait = value.get('choice_wait_s', 60)
    if type(wait) is not int or not 0 <= wait <= 3600:
        raise Refusal('INVALID_CHOICE_WAIT', repr(wait))
    choice = value.get('choice')
    if choice is not None and (not isinstance(choice, str) or not Path(choice).is_absolute()):
        raise Refusal('INVALID_CHOICE_PATH', repr(choice))
    if selected == 'ultra':
        receipt = _load_parent_receipt(value)
        if receipt.get('intent_label') != USER_EXPLICIT_ULTRA or not receipt.get('ultra_match'):
            raise Refusal('ULTRA_INTENT_MISSING', 'parent receipt is not explicit Ultra evidence')
        intent = USER_EXPLICIT_ULTRA
        authority = USER_EXPLICIT_ULTRA
        ultra_match = True
    else:
        # An environment cannot relabel even the default intent.  A child may
        # transport a parent receipt only when it remains exactly PROGRAM_DEFAULT.
        if (value.get('authority') not in (None, 'PROGRAM_DEFAULT') or
                value.get('intent_label') not in (None, PROGRAM_DEFAULT) or
                value.get('mode_intent') not in (None, 'default') or
                value.get('request_receipt_path') or value.get('request_receipt_sha256')):
            raise Refusal('REQUEST_RECEIPT_INVALID', 'default transport carries foreign intent metadata')
        receipt = value.get('request_receipt')
        if receipt is not None:
            if receipt != _default_payload() or value.get('request_digest') != receipt['request_digest']:
                raise Refusal('REQUEST_RECEIPT_INVALID', 'default transport relabelled')
        elif value.get('request_digest') not in (None, _default_payload()['request_digest']):
            raise Refusal('REQUEST_RECEIPT_INVALID', 'default transport has an unbound digest')
        intent = PROGRAM_DEFAULT
        authority = 'PROGRAM_DEFAULT'
        ultra_match = False
        receipt = receipt or _default_payload()
    return dict(mode=selected, mode_label=ULTRA_MODE if selected == 'ultra' else DEFAULT_MODE,
                mode_intent=selected, intent_label=intent, ultra_match=ultra_match,
                mode_explicit=intent == USER_EXPLICIT_ULTRA,
                request_digest=value.get('request_digest') or receipt.get('request_digest'),
                request_receipt=receipt, request_receipt_path=value.get('request_receipt_path'),
                request_receipt_sha256=value.get('request_receipt_sha256'),
                cpus=cpus, ram_mb=ram, workers=workers, licenses=dict(licenses),
                choice=choice, choice_wait_s=wait,
                authority=authority)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--execution-mode', choices=('default', 'ultra'), default=None,
                        help='Production adapter policy; omitted means default.')
    parser.add_argument('--execution-cpus', type=int, default=None)
    parser.add_argument('--execution-ram-mb', type=int, default=None)
    parser.add_argument('--execution-workers', type=int, default=None)
    parser.add_argument('--execution-licenses', default=None,
                        help='JSON object of bounded license-seat counts.')
    parser.add_argument('--execution-choice', default=None,
                        help='Absolute JSON choice file for this live invocation.')
    parser.add_argument('--execution-choice-wait-s', type=int, default=None)
    parser.add_argument('--execution-request-digest', default=None, help=argparse.SUPPRESS)
    parser.add_argument('--execution-request-receipt', default=None, help=argparse.SUPPRESS)


def configure(args: argparse.Namespace, *, _issuer=None) -> dict:
    """Normalize the live request and transport it to a child.

    ``vibe_ic_one_shot_runner`` is the only issuer. Its live private authority
    is an object identity held by that front door; child calls have no issuer
    argument and can only consume an inherited capability endpoint.
    """
    # A child may receive a custom environment from its parent, so reconstruct
    # only a digest-bound parent transport before parsing local flags.
    had_transport = ENV in os.environ
    parent_digest = getattr(args, 'execution_request_digest', None)
    parent_receipt = getattr(args, 'execution_request_receipt', None)
    local_policy_fields = ('execution_mode', 'execution_cpus', 'execution_ram_mb',
                           'execution_workers', 'execution_licenses',
                           'execution_choice', 'execution_choice_wait_s')
    local_non_mode = tuple(field for field in local_policy_fields
                           if field != 'execution_mode')
    if (ENV not in os.environ and not parent_digest and not parent_receipt and
            getattr(args, 'execution_mode', None) in (None, 'default') and
            not any(getattr(args, field, None) is not None for field in local_non_mode)):
        # Omitted policy is observationally inert: do not create an
        # environment activation that changes a canonical child's argv/env.
        return request()
    if ENV not in os.environ and (parent_digest or parent_receipt):
        had_transport = True
        if not parent_digest or not parent_receipt:
            raise Refusal('PARENT_REQUEST_MISSING', 'digest and receipt must travel together')
        receipt_path = Path(parent_receipt)
        if not receipt_path.is_absolute():
            raise Refusal('REQUEST_RECEIPT_INVALID', str(receipt_path))
        try:
            receipt_sha = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        except OSError as exc:
            raise Refusal('REQUEST_RECEIPT_INVALID', str(receipt_path)) from exc
        os.environ[ENV] = json.dumps(dict(mode=getattr(args, 'execution_mode', None) or 'ultra',
                                           request_digest=parent_digest,
                                           request_receipt_path=str(receipt_path),
                                           request_receipt_sha256=receipt_sha),
                                      sort_keys=True)
    value = request()
    inherited_mode = value.get('mode')
    for key, attr in (('mode', 'execution_mode'), ('cpus', 'execution_cpus'),
                      ('ram_mb', 'execution_ram_mb'), ('workers', 'execution_workers'),
                      ('choice', 'execution_choice'),
                      ('choice_wait_s', 'execution_choice_wait_s')):
        given = getattr(args, attr, None)
        if given is not None:
            value[key] = str(Path(given).resolve()) if key == 'choice' else given
    if getattr(args, 'execution_licenses', None) is not None:
        try:
            value['licenses'] = json.loads(args.execution_licenses)
        except (TypeError, json.JSONDecodeError) as exc:
            raise Refusal('INVALID_LICENSE_BUDGET', str(exc)) from exc
    requested = getattr(args, 'execution_mode', None)
    inherited = value.get('intent_label') == USER_EXPLICIT_ULTRA
    if requested == 'ultra' and inherited:
        # Preserve the parent's exact evidence; never mint a new receipt.
        pass
    elif requested == 'ultra' and inherited_mode == 'default' and not had_transport:
        # Only the canonical top-level Phase-1 front door may issue Ultra
        # authority. A child must receive an already-issued digest-bound
        # receipt and the live parent capability endpoint.
        if _issuer is None or _issuer is not _REGISTERED_ISSUER:
            raise Refusal('ULTRA_ISSUER_NOT_ALLOWED', 'child')
        if _ISSUER_CONTEXT is None:
            raise Refusal('ULTRA_ISSUER_NOT_ALLOWED', 'front-door invocation is not registered')
        evidence = dict(schema=1, mode='ultra', mode_label=ULTRA_MODE,
                        intent_label=USER_EXPLICIT_ULTRA, ultra_match=True,
                        issuer='live-frontdoor', user_evidence='--execution-mode ultra',
                        issuer_role=_ISSUER_CONTEXT['issuer_role'],
                        issuer_process=dict(_ISSUER_CONTEXT['issuer_process']),
                        invocation_id=_ISSUER_CONTEXT['invocation_id'],
                        nonce=uuid.uuid4().hex)
        evidence['request_digest'] = _digest(evidence)
        try:
            parent_sock, child_sock = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
            child_sock.set_inheritable(True)
            server = _CapabilityServer(parent_sock, evidence['request_digest'],
                                       _ISSUER_CONTEXT)
            _ISSUED_CAPABILITIES[evidence['request_digest']] = (server, child_sock)
            os.environ[_CAPABILITY_FD_ENV] = str(child_sock.fileno())
            server.start()
        except OSError as exc:
            raise Refusal('REQUEST_CAPABILITY_UNAVAILABLE', str(exc)) from exc
        receipt_dir = Path(tempfile.mkdtemp(prefix='vibeic-execution-request-'))
        receipt_path = receipt_dir / 'request.json'
        receipt_path.write_text(json.dumps(evidence, sort_keys=True, separators=(',', ':')) + '\n')
        receipt_path.chmod(0o444)
        _ISSUED_REQUESTS[evidence['request_digest']] = evidence
        value.update(mode='ultra', mode_label=ULTRA_MODE, mode_intent='ultra',
                     intent_label=USER_EXPLICIT_ULTRA, ultra_match=True,
                     mode_explicit=True, request_digest=evidence['request_digest'],
                     request_receipt=evidence, request_receipt_path=str(receipt_path),
                     request_receipt_sha256=hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
                     authority=USER_EXPLICIT_ULTRA)
    elif requested == 'ultra':
        raise Refusal('PARENT_REQUEST_CONFLICT', 'child cannot upgrade or relabel its parent')
    elif requested == 'default' and inherited:
        raise Refusal('PARENT_REQUEST_CONFLICT', 'child cannot downgrade an explicit Ultra parent')
    os.environ[ENV] = json.dumps(value, sort_keys=True)
    # Reparse after normalization so all validation occurs on the actual child
    # transport, not merely on argparse's local values.
    value = request()
    os.environ[ENV] = json.dumps(value, sort_keys=True)
    return value


def require_explicit_ultra() -> dict:
    value = request()
    if value['mode'] == 'ultra' and value['authority'] != 'USER_EXPLICIT_ULTRA':
        raise Refusal('ULTRA_INTENT_MISSING', 'explicit --execution-mode ultra is required')
    return value


def controller_fields(*, ic_ip_path: str | None = None,
                      route_receipt: dict | None = None) -> dict:
    """Typed fields a production adapter passes into the existing Context.

    Production construction must resolve its IC/IP route at the adapter
    boundary.  Keeping a default here would silently turn an IP request into
    an IC run; neutral controller tests can continue constructing ``Context``
    directly with its compatibility default.
    """
    if ic_ip_path is None or not isinstance(route_receipt, dict) or not route_receipt:
        raise Refusal('PRODUCTION_ROUTE_REQUIRED', 'resolved ic_ip_path and route receipt are required')
    if ic_ip_path not in ('IC', 'IP'):
        raise Refusal('INVALID_IC_IP_PATH', repr(ic_ip_path))
    if (route_receipt.get('kind') != 'issued-route' or
            route_receipt.get('schema') != 1 or
            route_receipt.get('ic_ip_path') != ic_ip_path or
            any(key not in route_receipt for key in
                ('source_sha', 'project_digest', 'request_digest', 'route_digest'))):
        raise Refusal('IC_IP_ROUTE_MISMATCH', ic_ip_path)
    route_body = {k: v for k, v in route_receipt.items() if k != 'route_digest'}
    if (not isinstance(route_receipt.get('route_digest'), str) or
            _digest(route_body) != route_receipt['route_digest']):
        raise Refusal('ROUTE_RECEIPT_DIGEST_MISMATCH', ic_ip_path)
    _verify_route_authority(route_receipt)
    value = request()
    return dict(ic_ip_path=ic_ip_path,
                route_receipt=dict(route_receipt or {}),
                intent_label=value['intent_label'],
                request_digest=value['request_digest'])


def child_arguments(argv: list[str], *, supports_execution_policy: bool = True) -> list[str]:
    """Append typed request values for children that declare the policy CLI.

    A legacy child that has not adopted the typed options receives its exact
    original argv.  Passing policy flags to such a parser would turn a valid
    explicit request into an argparse rc2 before the child can consume its
    inherited receipt.
    """
    if not supports_execution_policy:
        return list(argv)
    if ENV not in os.environ:
        return list(argv)
    value = request()
    result = list(argv)
    options = [('--execution-mode', value['mode']), ('--execution-cpus', value['cpus']),
               ('--execution-ram-mb', value['ram_mb']), ('--execution-workers', value['workers']),
               ('--execution-choice-wait-s', value['choice_wait_s'])]
    if value['choice']:
        options.append(('--execution-choice', value['choice']))
    if value.get('request_digest') and value.get('request_receipt_path'):
        options.extend((('--execution-request-digest', value['request_digest']),
                        ('--execution-request-receipt', value['request_receipt_path'])))
    if value['licenses']:
        options.append(('--execution-licenses', json.dumps(value['licenses'], sort_keys=True)))
    for flag, expected in options:
        existing = None
        for i, item in enumerate(result):
            if item == flag:
                existing = result[i + 1] if i + 1 < len(result) else ''
            elif item.startswith(flag + '='):
                existing = item.split('=', 1)[1]
        if existing is not None and existing != str(expected):
            raise Refusal('CHILD_EXECUTION_POLICY_CONFLICT', flag)
        if existing is None:
            result += [flag, str(expected)]
    return result


def child_pass_fds() -> tuple[int, ...]:
    """Return only the live parent capability FD for subprocess inheritance."""
    if ENV not in os.environ:
        return ()
    raw_fd = os.environ.get(_CAPABILITY_FD_ENV)
    if raw_fd is None:
        return ()
    try:
        fd = int(raw_fd)
        os.fstat(fd)
    except (OSError, TypeError, ValueError):
        raise Refusal('REQUEST_CAPABILITY_INVALID', raw_fd)
    return (fd,)
