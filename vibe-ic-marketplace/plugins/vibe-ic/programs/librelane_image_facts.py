#!/usr/bin/env python3
"""What the vibeic-eda image in hand says about its own LibreLane: read at run time, never stored.

The image moved 0.3.67 -> 0.3.83 in seven days (plan W22). Every fact a
LibreLane driver or importer depends on is therefore read from the image it is
about to run, once per image ID, and nothing here is a constant copied out of
some earlier image:

* the LibreLane version, and each flow's step ids in the image's own order
  (``Flow.factory``), so drivers and importers key on step ids;
* the image's tool provenance files, the OpenROAD version, and the ORFS commit
  that OpenROAD was built from, found next to the ``openroad`` binary;
* every option of the two LibreLane CLIs vibe-ic invokes -- the flow CLI
  (``python3 -m librelane``) and the per-step CLI (``python3 -m
  librelane.steps run``) -- with its environment variable, its built-in
  default and its option group, and the value it would silently take;
* ``config_variables``: every configuration variable the read flows and their
  steps declare, with its declared default, type, PDK flag and flows. A
  default the declaration does not fix (a callable, a PDK-sourced variable,
  an Optional with no default, declarations that disagree) is
  ``default_measured: False`` with ``default`` None and a
  ``not_measured_reason``; ``tool_registry(facts)`` hands the rows to
  `staged_tool_config.resolve`.

That last fact is the environment half of W22. MEASURED on 0.3.83: the image's
own entrypoint (the login environment, reached with ``<image> --skip ...``)
exports ``PDK=ihp-sg13g2`` and ``STD_CELL_LIBRARY=sg13g2_stdcell``, and the
flow CLI reads exactly those variables as the defaults of ``--pdk`` and
``--scl`` (the first CMP3 LibreLane attempt died on them). Under an overridden
``--entrypoint`` neither is set, but ``PDK_ROOT`` still is; ``--pdk`` still
defaults to a built-in PDK; ``--ciel-pdk/--manual-pdk`` defaults to fetching
the PDK through Ciel at LibreLane's own pinned version; and the per-step CLI's
``--pdk-root`` defaults to the PDK_ROOT it read at import time.

``require_explicit_cli_options`` guards the options that decide which PDK a run
uses: every option with an environment variable, and every option in the same
option group as one (the CLI's own PDK group, derived, never named). It refuses
an argv that would let any of them take a value nobody passed -- from the
environment, from a built-in default, or from a default the probe could not
read (a callable one) -- and a facts record read from another image.

Nothing is guessed: a fact the probe could not read is ``None`` and is named in
``not_measured`` with its reason. A probe that cannot run, or runs past its
deadline, refuses by name; its container is removed by the name it was given.
Every container carries the memory ceiling and a deadline.

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
#: The two CLIs vibe-ic invokes: the whole flow, and one step (`run_chain`).
CLIS = ('flow', 'step')
#: Where a vibeic-eda image records the fork ref of each tool it ships.
IMAGE_PROVENANCE_DIR = '/vibeic/provenance'
#: The line the login-environment probe prints; the entrypoint prints its own
#: banner around it on the same stream.
LOGIN_ENV_MARKER = 'VIBEIC_LOGIN_ENV '
#: Wall-clock deadline of each probe container. The measured cost is about one
#: second each on 0.3.83; the bound exists for a hang (an entrypoint that stops
#: honouring `--skip`, a stalled daemon), not for a slow host.
DEFAULT_DEADLINE_S = 600
#: The container kills its own command this long before the client gives up,
#: so the client normally sees the container's rc rather than its own timeout.
_INNER_MARGIN_S = 10
_INSPECT_DEADLINE_S = 60
_REAP_DEADLINE_S = 30

_FACTS: dict[tuple[str, str, tuple[str, ...]], dict[str, Any]] = {}

_PROBE = r'''
import json, os, shutil, subprocess, sys
flows, provenance_dir = sys.argv[1].split(","), sys.argv[2]
out = {"flows": {}, "clis": {}, "provenance": {}, "not_measured": {}}
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
def options(command, cli):
    groups = {}
    for group in getattr(command, "option_groups", None) or []:
        for option in group.options:
            groups[option.name] = group.title
    rows = []
    for p in command.params:
        if getattr(p, "param_type_name", "option") != "option":
            continue
        env = getattr(p, "envvar", None)
        env = [] if not env else ([env] if isinstance(env, str) else list(env))
        row = {"name": p.name,
               "opts": list(dict.fromkeys(list(p.opts) + list(getattr(p, "secondary_opts", []) or []))),
               "envvar": env, "group": groups.get(p.name), "default_measured": True,
               "bypass_env": {v: os.environ.get(v) for v in env}}
        if callable(p.default):
            row["default"], row["default_measured"] = None, False
            miss("clis.%s.%s.default" % (cli, p.name), "a callable default; its value is decided at run time")
        else:
            row["default"] = None if p.default is None else json.loads(json.dumps(p.default, default=str))
        rows.append(row)
    return rows
try:
    import typing
    from librelane.flows import Flow
    # A declared default is the value the tool applies only when nothing else
    # decides it. Each case below is a default the declaration does NOT fix.
    WHY = {"callable_defaults": "a function decides the default at run time",
           "pdk_defaults": "the PDK supplies the value at run time (Variable.pdk)",
           "runtime_defaults": "Optional with no declared default: the step or the tool decides at run time",
           "differing_defaults": "declared with different defaults; see defaults_by_flow"}

    def optional(t):
        return typing.get_origin(t) is typing.Union and type(None) in typing.get_args(t)
    registry, first, by_flow = {}, {}, {}
    for name in flows:
        flow = Flow.factory.get(name)
        if flow is None:
            continue
        declared = list(getattr(flow, "config_vars", None) or [])
        for step in flow.Steps:
            declared += list(step.get_all_config_variables())
        for v in declared:
            pdk = bool(getattr(v, "pdk", False))
            if callable(v.default):
                kind = "callable_defaults"
            elif pdk:
                kind = "pdk_defaults"
            elif v.default is None and optional(v.type):
                kind = "runtime_defaults"
            else:
                kind = None
            default = None if kind else json.loads(json.dumps(v.default, default=str))
            shown = default if kind is None else "<" + kind + ">"
            by_flow.setdefault(v.name, {}).setdefault(name, shown)
            row = registry.get(v.name)
            if row is None:
                row = registry[v.name] = {"default": default, "default_measured": kind is None,
                                          "type": str(v.type), "pdk": pdk, "flows": []}
                if kind:
                    row["not_measured_reason"] = WHY[kind]
                first[v.name] = shown
            elif shown != first[v.name] and "defaults_by_flow" not in row:
                # Two declarations disagree: neither is THE default, so the
                # row says so itself (a resolver reads rows, not this record).
                row.update(default=None, default_measured=False,
                           not_measured_reason=WHY["differing_defaults"],
                           defaults_by_flow=by_flow[v.name])
            if name not in row["flows"]:
                row["flows"].append(name)
    out["config_variables"] = registry
    for kind, why in WHY.items():
        names = sorted(k for k, r in registry.items() if r.get("not_measured_reason") == why)
        if names:
            miss("config_variables." + kind,
                 f"{len(names)} variable(s): {why}; e.g. {', '.join(names[:5])}")
except Exception as exc:
    out["config_variables"] = None
    miss("config_variables", repr(exc))
for cli, loader in (("flow", lambda: __import__("librelane.__main__", fromlist=["cli"]).cli),
                    ("step", lambda: __import__("librelane.steps.__main__", fromlist=["cli"]).cli.commands["run"])):
    try:
        out["clis"][cli] = options(loader(), cli)
    except Exception as exc:
        out["clis"][cli] = None
        miss("clis." + cli, repr(exc))
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


class _Deadline(Exception):
    """A tool run passed its deadline; the message names the deadline."""


def _run(argv: list[str], deadline_s: float, container: str | None = None,
         docker: str = 'docker') -> subprocess.CompletedProcess:
    """One tool run with a deadline. A `docker run` past it leaves a container
    behind (killing the client does not stop it), so that container is removed
    by the name it was started with before `_Deadline` is raised."""
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=deadline_s)
    except subprocess.TimeoutExpired:
        if container:
            try:
                subprocess.run([docker, 'rm', '-f', container], capture_output=True,
                               text=True, timeout=_REAP_DEADLINE_S)
            except (OSError, subprocess.SubprocessError):
                pass  # cleanup; the refusal below is the record
        raise _Deadline(f'no answer within the {deadline_s:g} s deadline'
                        + (f'; container {container} removed' if container else '')) from None
    except OSError as exc:
        raise Refusal('LL_IMAGE_FACTS_UNREADABLE', f'{argv[0]}: {exc}') from None


def _probe_argv(docker: str, image_id: str, container: str, deadline_s: float,
                entrypoint: bool, command: list[str]) -> list[str]:
    """A capped, named, network-less `docker run` whose command bounds itself:
    through `--entrypoint timeout` (bypass), or through the image's own
    entrypoint and `--skip timeout` (login)."""
    inner = ['--kill-after=5', str(max(1, int(deadline_s) - _INNER_MARGIN_S)), *command]
    head = [docker, 'run', *_dmem.docker_memory_flags(), '--rm', '--name', container,
            '--network', 'none']
    if entrypoint:
        return [*head, '--entrypoint', 'timeout', image_id, *inner]
    return [*head, image_id, '--skip', 'timeout', *inner]


def image_facts(image: str, docker: str = 'docker',
                flows: tuple[str, ...] = DEFAULT_FLOWS,
                deadline_s: float = DEFAULT_DEADLINE_S) -> dict[str, Any]:
    """The facts above for ``image``, read from it now (cached per image ID).

    The reference is resolved to its image ID first (a local `docker image
    inspect`), so a tag that has moved is read again rather than answered from
    the image it used to name. Then two capped, named containers with a
    deadline: one with ``--entrypoint`` overridden (the way the contract runs
    LibreLane), and one through the image's own entrypoint, to measure what its
    login environment exports for every variable either CLI reads.
    """
    import _docker_watchdog as _dw  # one naming rule for ephemeral containers
    ident = image_pdk_root(image, docker)
    key = (ident['image_id'], docker, tuple(flows))
    if key in _FACTS:
        return {**_FACTS[key], 'image': image}
    not_measured: dict[str, str] = {}
    try:
        labels = _run([docker, 'image', 'inspect', '--format', '{{json .Config.Labels}}',
                       ident['image_id']], _INSPECT_DEADLINE_S)
        label_map = json.loads(labels.stdout) if labels.returncode == 0 else None
        if label_map is None:
            not_measured['image_version_label'] = f'docker image inspect rc={labels.returncode}'
    except _Deadline as exc:
        label_map = None
        not_measured['image_version_label'] = f'docker image inspect: {exc}'
    except ValueError:
        label_map = None
        not_measured['image_version_label'] = 'docker image inspect printed no JSON'
    name = _dw.ephemeral_container_name('vibeic_llfacts')
    try:
        probe = _run(_probe_argv(docker, ident['image_id'], name, deadline_s, True,
                                 ['python3', '-c', _PROBE, ','.join(flows), IMAGE_PROVENANCE_DIR]),
                     deadline_s, name, docker)
    except _Deadline as exc:
        raise Refusal('LL_IMAGE_FACTS_UNREADABLE', f'{image}: facts probe: {exc}') from None
    try:
        facts = json.loads((probe.stdout or '').strip().splitlines()[-1])
    except (IndexError, ValueError):
        facts = None
    if probe.returncode or not isinstance(facts, dict):
        raise Refusal('LL_IMAGE_FACTS_UNREADABLE',
                      f'{image}: rc={probe.returncode} {(probe.stderr or "")[-400:]}')
    not_measured.update(facts.get('not_measured') or {})
    version = (label_map or {}).get('org.opencontainers.image.version')
    if not version and 'image_version_label' not in not_measured:
        not_measured['image_version_label'] = 'no org.opencontainers.image.version label'
    clis = facts.get('clis') or {}
    names = sorted({v for rows in clis.values() for o in rows or [] for v in o['envvar']})
    login: dict[str, Any] | None = None
    if names:
        name = _dw.ephemeral_container_name('vibeic_llfacts_login')
        try:
            shell = _run(_probe_argv(docker, ident['image_id'], name, deadline_s, False,
                                     ['python3', '-c', _LOGIN_PROBE, *names]),
                         deadline_s, name, docker)
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
        except _Deadline as exc:
            not_measured['login_env'] = f'entrypoint --skip probe: {exc}'
    for rows in clis.values():
        for option in rows or []:
            option['login_env'] = None if login is None else {v: login.get(v) for v in option['envvar']}
    record = {'image': image, 'image_id': ident['image_id'], 'image_version_label': version,
              'pdk_root_env': ident['pdk_root'],
              **{k: v for k, v in facts.items() if k != 'not_measured'},
              'not_measured': not_measured, 'deadline_s': deadline_s,
              'read_by': 'librelane_image_facts.image_facts (two capped, named docker runs with a deadline)'}
    _FACTS[key] = record
    return record


def flow_steps(facts: dict[str, Any], flow: str) -> list[str]:
    """The image's own step ids for ``flow``, or a refusal; never a stored list."""
    steps = (facts.get('flows') or {}).get(flow)
    if not steps:
        raise Refusal('LL_FLOW_UNRESOLVED',
                      f"{flow}: {(facts.get('not_measured') or {}).get('flows.' + flow) or 'not read'}")
    return list(steps)


def tool_registry(facts: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    """The image's LibreLane variable registry, in the shape
    `staged_tool_config.resolve(tool_registry=)` takes: ``{VAR: {"default",
    "default_measured", "type", "pdk", "flows"}}`` for every variable every
    step of the read flows (and the flows themselves) declares. None when it
    was not read (named in ``not_measured``), which the resolver records as
    "registry not supplied" rather than inventing defaults.

    ``default_measured: False`` means the declaration does not fix the value
    the tool applies, and ``default`` is then None and is NOT that value. The
    row says why in ``not_measured_reason``: a callable default, a PDK-sourced
    variable (``pdk``), an Optional variable with no declared default (the
    step or the tool decides at run time), or declarations that disagree
    (``defaults_by_flow`` names each one). Each group is also counted in
    ``not_measured`` as ``config_variables.<group>``."""
    registry = facts.get('config_variables')
    return registry if isinstance(registry, dict) else None


def guarded_options(facts: dict[str, Any], cli: str = 'flow') -> list[dict[str, Any]] | None:
    """The options of ``cli`` that decide which PDK a run uses: every option
    with an environment variable, and every option in the same option group as
    one. The group is DERIVED from the flow CLI's environment-defaulted options,
    never named. The per-step CLI declares no option groups (measured on
    0.3.83), so there an option is also guarded when it is the same PARAMETER
    as a guarded flow-CLI option (its `--pdk-root`). None when the scope cannot
    be derived (a CLI was not read, has no such option, or declares no groups)."""
    if cli not in CLIS:
        raise Refusal('LL_INVALID_CLI', f'{cli}: one of {CLIS}')
    clis = facts.get('clis') or {}
    flow_rows, rows = clis.get('flow'), clis.get(cli)
    if not flow_rows or not rows:
        return None
    groups = {o['group'] for o in flow_rows if o['envvar'] and o['group']}
    if not groups:
        return None
    flow_guard = [o for o in flow_rows if o['envvar'] or o['group'] in groups]
    names = {o['name'] for o in flow_guard}
    return [o for o in rows if o['envvar'] or o['group'] in groups or o['name'] in names]


def implicit_cli_values(facts: dict[str, Any], entrypoint: str,
                        cli: str = 'flow') -> list[dict[str, Any]]:
    """Each guarded option of ``cli`` that takes a value nobody passed, under
    ``entrypoint``: ``login`` (the image's own entrypoint, ``<image> --skip``)
    or ``bypass`` (an overridden ``--entrypoint``). An environment value wins
    over the built-in default, as in click. An environment, a default or a
    guard scope that was not measured is reported as unknown, never as unset.
    """
    if entrypoint not in ('login', 'bypass'):
        raise Refusal('LL_INVALID_ENTRYPOINT', entrypoint)
    options = guarded_options(facts, cli)
    if options is None:
        return [{'opts': [], 'source': f'{cli} CLI guard scope NOT_MEASURED', 'value': None}]
    found = []
    for option in options:
        if option['envvar']:
            env = option.get(f'{entrypoint}_env')
            if env is None:
                found.append({'opts': option['opts'], 'source': 'environment NOT_MEASURED',
                              'value': None})
                continue
            named = next(((v, env[v]) for v in option['envvar'] if env.get(v)), None)
            if named:
                found.append({'opts': option['opts'], 'source': f'env {named[0]}',
                              'value': named[1]})
                continue
        if not option.get('default_measured', False):
            found.append({'opts': option['opts'], 'source': 'CLI default NOT_MEASURED',
                          'value': None})
        elif option.get('default') is not None:
            found.append({'opts': option['opts'], 'source': 'CLI default',
                          'value': option['default']})
    return found


def require_explicit_cli_options(facts: dict[str, Any], argv: list[str], entrypoint: str, *,
                                 image_id: str, cli: str = 'flow') -> None:
    """Refuse ``LL_IMAGE_ENV_DEFAULT`` unless every implicit value is overridden.

    ``argv`` is the ``cli`` argv the driver is about to run on ``image_id``;
    facts read from another image refuse ``LL_IMAGE_FACTS_STALE``. An option is
    explicit when one of its spellings (a flag pair counts both) appears as a
    word (``--pdk X``) or as the head of ``--pdk=X``.
    """
    if facts.get('image_id') != image_id:
        raise Refusal('LL_IMAGE_FACTS_STALE',
                      f"facts read from {facts.get('image_id')}, run on {image_id}")
    words = set(argv) | {a.split('=', 1)[0] for a in argv if a.startswith('--')}
    left = [row for row in implicit_cli_values(facts, entrypoint, cli)
            if not any(opt in words for opt in row['opts'])]
    if left:
        raise Refusal('LL_IMAGE_ENV_DEFAULT', f'{cli} CLI: ' + '; '.join(
            f"{'/'.join(row['opts']) or '(scope)'} would take {row['value']!r} from {row['source']}"
            for row in left))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('image', help='image reference (a digest-pinned reference, or an image ID)')
    parser.add_argument('--docker', default='docker')
    parser.add_argument('--flow', action='append', dest='flows',
                        help=f'flow name (repeatable; default {", ".join(DEFAULT_FLOWS)})')
    parser.add_argument('--deadline', type=float, default=DEFAULT_DEADLINE_S,
                        help='seconds each probe container may run (default %(default)s)')
    parser.add_argument('--out', type=Path, help='write the record here (atomically) as well')
    args = parser.parse_args(argv)
    try:
        facts = image_facts(args.image, args.docker, tuple(args.flows or DEFAULT_FLOWS),
                            args.deadline)
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
