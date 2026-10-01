"""Production request transport. Native direct/librelane/dual is a separate fact."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from execution_modes import Budget, Refusal, Superiority

ENV = 'VIBEIC_EXECUTION_REQUEST'
DEFAULT_FACTORIES = ('execution_adapters_frontend', 'execution_adapters_backend',
                     'execution_adapters_release', 'execution_adapters_analog')


def request() -> dict:
    value = json.loads(os.environ[ENV]) if ENV in os.environ else {}
    if not isinstance(value, dict):
        raise Refusal('INVALID_EXECUTION_REQUEST', repr(value))
    mode = value.get('mode', 'default')
    if mode not in ('default', 'ultra'):
        raise Refusal('INVALID_EXECUTION_MODE', repr(mode))
    cpus = value.get('cpus', min(4, len(os.sched_getaffinity(0))))
    ram = value.get('ram_mb', 4096)
    workers = value.get('workers', min(4, cpus))
    licenses = value.get('licenses', {})
    selection = value.get('factory_selection', 'explicit' if 'factories' in value else 'default')
    factories = value.get('factories', list(DEFAULT_FACTORIES))
    if selection not in ('default', 'explicit') or (selection == 'default'
            and factories != list(DEFAULT_FACTORIES)):
        raise Refusal('INVALID_EXECUTION_REGISTRATION', repr(value))
    if (not isinstance(licenses, dict) or any(not isinstance(k, str) or not k for k in licenses)
            or not isinstance(factories, list) or any(not isinstance(n, str) for n in factories)
            or len(set(factories)) != len(factories)
            or any(not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*', n) for n in factories)):
        raise Refusal('INVALID_EXECUTION_REGISTRATION', repr(value))
    Budget(cpus, ram, workers=workers, licenses=licenses)
    if cpus > len(os.sched_getaffinity(0)):
        raise Refusal('HOST_CPU_BUDGET_UNAVAILABLE', str(cpus))
    wait = value.get('choice_wait_s', 60)
    if type(wait) is not int or not 0 <= wait <= 3600:
        raise Refusal('INVALID_CHOICE_WAIT', repr(wait))
    choice = value.get('choice')
    if choice is not None and (not isinstance(choice, str) or not Path(choice).is_absolute()):
        raise Refusal('INVALID_CHOICE_PATH', repr(choice))
    facts = value.get('native_facts')
    if facts is not None and (not isinstance(facts,str) or not Path(facts).is_absolute()):
        raise Refusal('INVALID_NATIVE_FACTS_PATH',repr(facts))
    promotion = value.get('superiority')
    if promotion is not None and (not isinstance(promotion, str) or not Path(promotion).is_absolute()):
        raise Refusal('SUPERIORITY_UNBOUND', 'absolute comparison document required')
    if promotion is not None and mode != 'default':
        raise Refusal('SUPERIORITY_UNBOUND', 'promotion applies to default mode')
    result = dict(mode=mode, cpus=cpus, ram_mb=ram, workers=workers, licenses=licenses,
                factories=factories, factory_selection=selection, choice=choice,
                native_facts=facts,
                choice_wait_s=wait, authority='one-live-parent-interpreter',
                native_identity='direct/librelane/dual is separate from execution policy')
    if promotion is not None:
        result['superiority'] = promotion
    return result


def load_superiority(filename: str | None) -> Superiority | None:
    """Decode the existing typed comparison; the controller owns its proof.

    This operational request never changes the candidate INPUT binding or
    supplies an AI selection. Current receipt/validator/metric equality and
    measured improvement remain the frozen controller's decisions.
    """
    if filename is None:
        return None
    path = Path(filename)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise Refusal('SUPERIORITY_UNBOUND', str(path))
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise Refusal('SUPERIORITY_UNBOUND', str(path)) from exc
    fields = {'binding', 'preferred', 'reference', 'metric', 'direction', 'receipts'}
    if (not isinstance(value, dict) or set(value) != fields
            or not isinstance(value['binding'], dict) or not value['binding']
            or any(not isinstance(value[key], str) or not value[key].strip()
                   for key in ('preferred', 'reference', 'metric'))
            or value['direction'] not in ('min', 'max')
            or not isinstance(value['receipts'], dict)):
        raise Refusal('SUPERIORITY_UNBOUND', str(path))
    receipts = {}
    for name, filename in value['receipts'].items():
        if (not isinstance(name, str) or not name or not isinstance(filename, str)
                or not Path(filename).is_absolute() or Path(filename).is_symlink()):
            raise Refusal('SUPERIORITY_UNBOUND', 'comparison receipt path')
        receipts[name] = Path(filename)
    return Superiority(value['binding'], value['preferred'], value['reference'],
                       value['metric'], value['direction'], receipts)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--execution-mode', choices=('default', 'ultra'), default=None,
                        help='Production adapter policy; omitted means default. '
                             'Unimplemented steps retain their disclosed native producer.')
    parser.add_argument('--execution-cpus', type=int, default=None)
    parser.add_argument('--execution-ram-mb', type=int, default=None)
    parser.add_argument('--execution-workers', type=int, default=None)
    parser.add_argument('--execution-licenses', default=None, help='Admitted license counts as JSON object')
    parser.add_argument('--execution-factories', default=None, help='Explicit source module names as JSON list')
    parser.add_argument('--execution-native-facts', default=None, help='Current capacity/tool facts JSON; no inferred qualification')
    parser.add_argument('--execution-superiority', default=None,
                        help='Serialized receipt-bound Superiority JSON for default promotion; no AI selection.')
    parser.add_argument('--execution-choice', default=None,
                        help='Explicit AI decision JSON for this live invocation; no automatic adoption.')
    parser.add_argument('--execution-choice-wait-s', type=int, default=None)


def configure(args: argparse.Namespace) -> dict:
    value = request()
    for key, attr in (('mode', 'execution_mode'), ('cpus', 'execution_cpus'),
                      ('ram_mb', 'execution_ram_mb'), ('choice', 'execution_choice'),
                      ('workers', 'execution_workers'), ('licenses', 'execution_licenses'),
                      ('factories', 'execution_factories'),
                      ('native_facts', 'execution_native_facts'),
                      ('superiority', 'execution_superiority'),
                      ('choice_wait_s', 'execution_choice_wait_s')):
        given = getattr(args, attr, None)
        if given is not None:
            value[key] = (str(Path(given).resolve()) if key in ('choice','native_facts','superiority') else
                          json.loads(given) if key in ('licenses', 'factories') else given)
            if key == 'factories':
                value['factory_selection'] = 'explicit'
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
               ('--execution-workers', value['workers']),
               ('--execution-licenses', json.dumps(value['licenses'], sort_keys=True)),
               ('--execution-factories', json.dumps(value['factories'])),
               ('--execution-choice-wait-s', value['choice_wait_s']))
    if value['choice']:
        options += (('--execution-choice', value['choice']),)
    if value['native_facts']:
        options += (('--execution-native-facts', value['native_facts']),)
    if value.get('superiority'):
        options += (('--execution-superiority', value['superiority']),)
    for flag, expected in options:
        existing = None
        for i, item in enumerate(result):
            if item == flag:
                existing = result[i + 1] if i + 1 < len(result) else ''
            elif item.startswith(flag + '='):
                existing = item.split('=', 1)[1]
        if existing is not None and existing != str(expected):
            raise Refusal('CHILD_EXECUTION_POLICY_CONFLICT', flag)
        omitted_default = (flag == '--execution-workers' and expected == min(4,value['cpus'])
                           or flag == '--execution-licenses' and not value['licenses']
                           or flag == '--execution-factories' and value['factory_selection'] == 'default')
        if existing is None and not omitted_default:
            result += [flag, str(expected)]
    return result


def child_environment(base: dict | None = None) -> dict:
    result = dict(os.environ if base is None else base)
    result[ENV] = json.dumps(request(), sort_keys=True)
    return result
