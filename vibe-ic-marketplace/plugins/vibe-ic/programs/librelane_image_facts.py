#!/usr/bin/env python3
"""What the vibeic-eda image in hand says about its own LibreLane: read at run time, never stored.

The image moved 0.3.67 -> 0.3.83 in seven days (plan W22). Every fact a
LibreLane driver or importer depends on is therefore read from the image it is
about to run, once per image reference, and nothing here is a constant copied
out of some earlier image:

* the LibreLane version, and each flow's step ids in the image's own order
  (``Flow.factory``), so drivers and importers key on step ids;
* the image's tool provenance files, the OpenROAD version, and the ORFS commit
  that OpenROAD was built from, found next to the ``openroad`` binary;
* which LibreLane CLI options fall back to an environment variable or to a
  built-in default, and what value each would silently take.

That last fact is the environment half of W22. MEASURED on 0.3.83: the image's
own entrypoint (the login environment, reached with ``<image> --skip ...``)
exports ``PDK=ihp-sg13g2`` and ``STD_CELL_LIBRARY=sg13g2_stdcell``, and the
LibreLane CLI reads exactly those variables as the defaults of ``--pdk`` and
``--scl`` (the first CMP3 LibreLane attempt died on them). Under an overridden
``--entrypoint`` neither is set, but ``PDK_ROOT`` still is, and ``--pdk``
still defaults to a built-in PDK. ``require_explicit_cli_options`` refuses an
argv that would let any such value in unstated.

Nothing is guessed: a fact the probe could not read is ``None`` and is named in
``not_measured`` with its reason. A probe that cannot run at all refuses by
name. Every container carries the memory ceiling.

chip-AGNOSTIC: no PDK, cell library or design literal selects a branch; the
flow names default to the two v1 uses (decision 22) and are a parameter.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _docker_memory as _dmem  # noqa: E402 — every `docker run` carries the ceiling
from librelane_contract import Refusal, image_pdk_root  # noqa: E402

#: Decision 22: Chip for DIE, Classic for HARDMACRO.
DEFAULT_FLOWS = ('Classic', 'Chip')
#: Where a vibeic-eda image records the fork ref of each tool it ships.
IMAGE_PROVENANCE_DIR = '/vibeic/provenance'
#: The line the login-environment probe prints; the entrypoint prints its own
#: banner around it on the same stream.
LOGIN_ENV_MARKER = 'VIBEIC_LOGIN_ENV '

_FACTS: dict[tuple[str, str, tuple[str, ...]], dict[str, Any]] = {}

_PROBE = r'''
import json, os, shutil, subprocess, sys
flows, provenance_dir = sys.argv[1].split(","), sys.argv[2]
out = {"flows": {}, "cli_options": [], "provenance": {}, "not_measured": {}}
def miss(key, why):
    out["not_measured"][key] = str(why)[:300]
try:
    import librelane
    out["librelane_version"] = getattr(librelane, "__version__", None)
    if out["librelane_version"] is None:
        miss("librelane_version", "librelane has no __version__")
except Exception as exc:
    out["librelane_version"] = None
    miss("librelane_version", repr(exc))
try:
    from librelane.flows import Flow
    for name in flows:
        flow = Flow.factory.get(name)
        out["flows"][name] = None if flow is None else [s.id for s in flow.Steps]
        if flow is None:
            miss("flows." + name, "Flow.factory has no " + name)
except Exception as exc:
    for name in flows:
        out["flows"][name] = None
    miss("flows", repr(exc))
try:
    import librelane.__main__ as main
    for p in main.cli.params:
        env = getattr(p, "envvar", None)
        if not env:
            continue
        env = [env] if isinstance(env, str) else list(env)
        default = None if callable(p.default) else p.default
        out["cli_options"].append({
            "opts": list(p.opts), "envvar": env,
            "default": None if default is None else str(default),
            "bypass_env": {v: os.environ.get(v) for v in env}})
except Exception as exc:
    out["cli_options"] = None
    miss("cli_options", repr(exc))
if os.path.isdir(provenance_dir):
    for name in sorted(os.listdir(provenance_dir)):
        if name.endswith(".json"):
            try:
                with open(os.path.join(provenance_dir, name)) as fh:
                    out["provenance"][name[:-5]] = json.load(fh)
            except Exception as exc:
                miss("provenance." + name, repr(exc))
else:
    out["provenance"] = None
    miss("provenance", provenance_dir + " absent")
binary = shutil.which("openroad")
out["openroad_version"] = out["orfs_commit"] = None
if binary:
    try:
        done = subprocess.run([binary, "-version"], capture_output=True, text=True, timeout=120)
        text = (done.stdout or "").strip().splitlines()
        out["openroad_version"] = text[0].strip() if done.returncode == 0 and text else None
        if out["openroad_version"] is None:
            miss("openroad_version", "rc=%s" % done.returncode)
    except Exception as exc:
        miss("openroad_version", repr(exc))
    commit = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(binary))), "ORFS_COMMIT")
    if os.path.isfile(commit):
        out["orfs_commit"] = open(commit).read().strip() or None
    if out["orfs_commit"] is None:
        miss("orfs_commit", commit + " absent or empty")
else:
    miss("openroad_version", "no openroad on PATH")
    miss("orfs_commit", "no openroad on PATH")
print(json.dumps(out))
'''

_LOGIN_PROBE = ('import json,os,sys;'
                'print(%r+json.dumps({v: os.environ.get(v) for v in sys.argv[1:]}))'
                % LOGIN_ENV_MARKER)


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, text=True)
    except OSError as exc:
        raise Refusal('LL_IMAGE_FACTS_UNREADABLE', f'{argv[0]}: {exc}') from None


def image_facts(image: str, docker: str = 'docker',
                flows: tuple[str, ...] = DEFAULT_FLOWS) -> dict[str, Any]:
    """The facts above for ``image``, read from it now (cached per reference).

    Two capped containers: one with ``--entrypoint python3`` (the way the
    contract runs LibreLane), and one through the image's own entrypoint, to
    measure what its login environment exports for the variables the CLI reads.
    """
    key = (image, docker, tuple(flows))
    if key in _FACTS:
        return _FACTS[key]
    ident = image_pdk_root(image, docker)
    labels = _run([docker, 'image', 'inspect', '--format', '{{json .Config.Labels}}',
                   ident['image_id']])
    try:
        label_map = json.loads(labels.stdout) if labels.returncode == 0 else None
    except ValueError:
        label_map = None
    probe = _run([docker, 'run', *_dmem.docker_memory_flags(), '--rm', '--network', 'none',
                  '--entrypoint', 'python3', ident['image_id'], '-c', _PROBE,
                  ','.join(flows), IMAGE_PROVENANCE_DIR])
    try:
        facts = json.loads((probe.stdout or '').strip().splitlines()[-1])
    except (IndexError, ValueError):
        facts = None
    if probe.returncode or not isinstance(facts, dict):
        raise Refusal('LL_IMAGE_FACTS_UNREADABLE',
                      f'{image}: rc={probe.returncode} {(probe.stderr or "")[-400:]}')
    not_measured = dict(facts.get('not_measured') or {})
    version = (label_map or {}).get('org.opencontainers.image.version')
    if not version:
        not_measured['image_version_label'] = (
            'no org.opencontainers.image.version label' if label_map is not None
            else f'docker image inspect rc={labels.returncode}')
    names = sorted({v for option in facts.get('cli_options') or [] for v in option['envvar']})
    login: dict[str, Any] | None = None
    if names:
        shell = _run([docker, 'run', *_dmem.docker_memory_flags(), '--rm', '--network', 'none',
                      ident['image_id'], '--skip', 'python3', '-c', _LOGIN_PROBE, *names])
        line = next((ln for ln in (shell.stdout or '').splitlines()
                     if ln.startswith(LOGIN_ENV_MARKER)), None)
        try:
            login = json.loads(line[len(LOGIN_ENV_MARKER):]) if line else None
        except ValueError:
            login = None
        if shell.returncode or not isinstance(login, dict):
            login = None
            not_measured['login_env'] = (f'entrypoint --skip probe rc={shell.returncode}: '
                                         f'{(shell.stderr or shell.stdout or "")[-300:]}')
    for option in facts.get('cli_options') or []:
        option['login_env'] = None if login is None else {v: login.get(v) for v in option['envvar']}
    record = {'image': image, 'image_id': ident['image_id'], 'image_version_label': version,
              'pdk_root_env': ident['pdk_root'], **{k: v for k, v in facts.items()
                                                      if k != 'not_measured'},
              'not_measured': not_measured,
              'read_by': 'librelane_image_facts.image_facts (two capped docker runs)'}
    _FACTS[key] = record
    return record


def flow_steps(facts: dict[str, Any], flow: str) -> list[str]:
    """The image's own step ids for ``flow``, or a refusal; never a stored list."""
    steps = (facts.get('flows') or {}).get(flow)
    if not steps:
        raise Refusal('LL_FLOW_UNRESOLVED',
                      f"{flow}: {(facts.get('not_measured') or {}).get('flows.' + flow) or 'not read'}")
    return list(steps)


def implicit_cli_values(facts: dict[str, Any], entrypoint: str) -> list[dict[str, Any]]:
    """Each CLI option that takes a value nobody passed, under ``entrypoint``.

    ``entrypoint`` is ``login`` (the image's own entrypoint, ``<image> --skip``)
    or ``bypass`` (an overridden ``--entrypoint``). An environment value wins
    over the option's built-in default, as it does in the CLI. An environment
    that was not measured is reported as unknown, which is not "unset".
    """
    if entrypoint not in ('login', 'bypass'):
        raise Refusal('LL_INVALID_ENTRYPOINT', entrypoint)
    options = facts.get('cli_options')
    if options is None:
        raise Refusal('LL_IMAGE_FACTS_UNREADABLE',
                      f"cli_options: {(facts.get('not_measured') or {}).get('cli_options')}")
    found = []
    for option in options:
        env = option.get(f'{entrypoint}_env')
        if env is None:
            found.append({'opts': option['opts'], 'source': 'environment NOT_MEASURED',
                          'value': None})
            continue
        named = next(((v, env[v]) for v in option['envvar'] if env.get(v)), None)
        if named:
            found.append({'opts': option['opts'], 'source': f'env {named[0]}',
                          'value': named[1]})
        elif option.get('default') is not None:
            found.append({'opts': option['opts'], 'source': 'CLI default',
                          'value': option['default']})
    return found


def require_explicit_cli_options(facts: dict[str, Any], argv: list[str],
                                 entrypoint: str) -> None:
    """Refuse ``LL_IMAGE_ENV_DEFAULT`` unless every implicit value is overridden.

    ``argv`` is the LibreLane CLI argv the driver is about to run. An option is
    explicit when one of its spellings appears as a word (``--pdk X``) or as the
    head of ``--pdk=X``.
    """
    words = set(argv) | {a.split('=', 1)[0] for a in argv if a.startswith('--')}
    left = [row for row in implicit_cli_values(facts, entrypoint)
            if not any(opt in words for opt in row['opts'])]
    if left:
        raise Refusal('LL_IMAGE_ENV_DEFAULT', '; '.join(
            f"{'/'.join(row['opts'])} would take {row['value']!r} from {row['source']}"
            for row in left))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('image', help='image reference (a digest-pinned reference, or an image ID)')
    parser.add_argument('--docker', default='docker')
    parser.add_argument('--flow', action='append', dest='flows',
                        help=f'flow name (repeatable; default {", ".join(DEFAULT_FLOWS)})')
    parser.add_argument('--out', type=Path, help='write the record here (atomically) as well')
    args = parser.parse_args(argv)
    try:
        facts = image_facts(args.image, args.docker, tuple(args.flows or DEFAULT_FLOWS))
    except Refusal as exc:
        print(f'REFUSED {exc}', file=sys.stderr)
        return 2
    text = json.dumps(facts, indent=2, sort_keys=True)
    if args.out:
        from _atomic_artefact import write_text
        write_text(args.out, text + '\n')
    print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
