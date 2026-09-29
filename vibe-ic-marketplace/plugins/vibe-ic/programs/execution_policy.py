"""Production request transport. Native direct/librelane/dual is a separate fact."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from execution_modes import Budget, Refusal

ENV = 'VIBEIC_EXECUTION_REQUEST'


def request() -> dict:
    value = json.loads(os.environ[ENV]) if ENV in os.environ else {}
    mode = value.get('mode', 'default')
    if mode not in ('default', 'ultra'):
        raise Refusal('INVALID_EXECUTION_MODE', repr(mode))
    cpus = value.get('cpus', min(4, len(os.sched_getaffinity(0))))
    ram = value.get('ram_mb', 4096)
    Budget(cpus, ram, workers=1)
    if cpus > len(os.sched_getaffinity(0)):
        raise Refusal('HOST_CPU_BUDGET_UNAVAILABLE', str(cpus))
    wait = value.get('choice_wait_s', 60)
    if type(wait) is not int or not 0 <= wait <= 3600:
        raise Refusal('INVALID_CHOICE_WAIT', repr(wait))
    return dict(mode=mode, cpus=cpus, ram_mb=ram, choice=value.get('choice'),
                choice_wait_s=wait, authority='one-live-parent-interpreter',
                licenses={})


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--execution-mode', choices=('default', 'ultra'), default=None,
                        help='Production adapter policy; omitted means default. '
                             'Unimplemented steps retain their disclosed native producer.')
    parser.add_argument('--execution-cpus', type=int, default=None)
    parser.add_argument('--execution-ram-mb', type=int, default=None)
    parser.add_argument('--execution-choice', default=None,
                        help='Explicit AI decision JSON for this live invocation; no automatic adoption.')
    parser.add_argument('--execution-choice-wait-s', type=int, default=None)


def configure(args: argparse.Namespace) -> dict:
    value = request()
    for key, attr in (('mode', 'execution_mode'), ('cpus', 'execution_cpus'),
                      ('ram_mb', 'execution_ram_mb'), ('choice', 'execution_choice'),
                      ('choice_wait_s', 'execution_choice_wait_s')):
        given = getattr(args, attr, None)
        if given is not None:
            value[key] = str(Path(given).resolve()) if key == 'choice' else given
    os.environ[ENV] = json.dumps(value, sort_keys=True)
    value = request()
    os.environ[ENV] = json.dumps(value, sort_keys=True)
    return value


def child_arguments(argv: list[str]) -> list[str]:
    """Typed argv only. Preserve an explicit child mode, reject conflicting policy."""
    value = request()
    result = list(argv)
    options = (('--execution-mode', value['mode']), ('--execution-cpus', value['cpus']),
               ('--execution-ram-mb', value['ram_mb']),
               ('--execution-choice-wait-s', value['choice_wait_s']))
    if value['choice']:
        options += (('--execution-choice', value['choice']),)
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
