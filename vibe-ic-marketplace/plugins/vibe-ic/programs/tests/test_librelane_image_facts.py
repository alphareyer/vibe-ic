"""librelane_image_facts: image facts are read from the image at run time.

Docker is substituted at the process edge; everything above it is the real
module. The probe replies below are the real output of the shipped probes on
vibeic-eda 0.3.83 (2026-09-28, 8HD-9), with the flow step lists shortened, the
provenance map cut to two tools, and the CLI option lists cut to the PDK group
plus two options outside it. `test_the_probe_itself_*` runs the shipped probe
SCRIPT on the host against a stand-in `librelane` package, so what the probe
records is tested, not only what the module does with a record.
"""
import importlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
facts_mod = importlib.import_module('librelane_image_facts')
contract = importlib.import_module('librelane_contract')

IMAGE_ID = 'sha256:' + 'a' * 64
OTHER_ID = 'sha256:' + 'c' * 64


def _opt(name, opts, envvar=(), group=None, default=None, bypass=None, measured=True):
    return {'name': name, 'opts': list(opts), 'envvar': list(envvar), 'group': group,
            'default': default, 'default_measured': measured,
            'bypass_env': bypass if bypass is not None else {v: None for v in envvar}}


REAL_CLIS = {
    'flow': [
        _opt('jobs', ['-j', '--jobs'], default=32),
        _opt('use_ciel', ['--volare-pdk', '--ciel-pdk', '--manual-pdk'], group='PDK options', default=True),
        _opt('pdk_root', ['--pdk-root'], ['PDK_ROOT'], 'PDK options', bypass={'PDK_ROOT': '/foss/pdks'}),
        _opt('pdk', ['-p', '--pdk'], ['PDK'], 'PDK options', 'sky130A'),
        _opt('scl', ['-s', '--scl'], ['STD_CELL_LIBRARY'], 'PDK options'),
        _opt('pad', ['--pad'], ['PAD_CELL_LIBRARY'], 'PDK options'),
        _opt('condensed', ['--condensed', '--full'], default=False)],
    'step': [
        _opt('output', ['-o', '--output'], default='/foss/designs/STEP_RUN_<timestamp>'),
        _opt('pdk_root', ['--pdk-root'], default='/foss/pdks'),
        _opt('condensed', ['--condensed', '--full'], default=False)],
}
REAL_PROBE = {
    'flows': {'Classic': ['Verilator.Lint', 'Checker.LintTimingConstructs', 'Yosys.Synthesis'],
              'Chip': ['Verilator.Lint', 'Checker.LintTimingConstructs', 'Yosys.Synthesis',
                       'OpenROAD.PadRing']},
    'clis': REAL_CLIS,
    'provenance': {'librelane': {'ref': '9cf849541fb02082c5890cbbf2cab4d796a9f8f9',
                                 'repo': 'https://github.com/vibeic/librelane.git',
                                 'tool': 'librelane'},
                   'openroad': {'ref': 'da11f47e14', 'tool': 'openroad'}},
    'not_measured': {},
    'librelane_version': '3.1.0.dev1',
    'openroad_version': '26Q3-3002-gda11f47e14',
    'orfs_commit': 'c9c22caf9bf9cfe46c5a4236c6ec7e7ae9863cc3',
    # An EXCERPT of config_variables: 5 of the 440 rows the 0.3.83 probe records (192 measured,
    # 148 PDK-sourced, 96 decided at run time, 4 required without a default).
    # The real probe also counts the latter three groups
    # in not_measured; that is left out of this snapshot, whose empty not_measured the
    # test_facts_come_from_the_image_through_two_capped_named_containers pins.
    'config_variables': {
        'FP_CORE_UTIL': {'default': 50, 'default_measured': True, 'type': "<class 'decimal.Decimal'>",
                         'pdk': False, 'flows': ['Classic', 'Chip']},
        'PL_TARGET_DENSITY_PCT': {
            'default': None, 'default_measured': False, 'type': 'typing.Optional[decimal.Decimal]',
            'pdk': False, 'flows': ['Classic', 'Chip'],
            'not_measured_reason': 'Optional with no declared default: the step or the tool decides at run time'},
        'CTS_SINK_CLUSTERING_SIZE': {
            'default': None, 'default_measured': False, 'type': 'typing.Optional[int]',
            'pdk': False, 'flows': ['Classic', 'Chip'],
            'not_measured_reason': 'Optional with no declared default: the step or the tool decides at run time'},
        'CTS_DISTANCE_BETWEEN_BUFFERS': {'default': 0, 'default_measured': True,
                                         'type': "<class 'decimal.Decimal'>", 'pdk': False,
                                         'flows': ['Classic', 'Chip']},
        'RT_MAX_LAYER': {
            'default': None, 'default_measured': False, 'type': "<class 'str'>", 'pdk': True,
            'flows': ['Classic', 'Chip'],
            'not_measured_reason': 'the PDK supplies the value at run time (Variable.pdk)'}}}
REAL_LOGIN = ('[INFO] USER_ID: 1000, GROUP_ID: 0\n'
              '[INFO] SKIPPING UI STARTUP\n'
              "[INFO] Executing command: 'timeout --kill-after=5 590 python3 -c ...'\n"
              'VIBEIC_LOGIN_ENV {"PAD_CELL_LIBRARY": null, "PDK": "ihp-sg13g2", '
              '"PDK_ROOT": "/foss/pdks", "STD_CELL_LIBRARY": "sg13g2_stdcell"}\n')
EXPLICIT = ['--manual-pdk', '--pdk-root', '/pdk', '--pdk', 'target', '--scl', 'lib', 'c.yaml']


class FakeDocker:
    """Answers every docker call image_facts makes, records each with the
    deadline it was given, and can make one kind of call time out."""

    def __init__(self, probe=REAL_PROBE, probe_rc=0, login=REAL_LOGIN, login_rc=0, labels=None):
        self.calls, self.deadlines = [], []
        self.probe, self.probe_rc = probe, probe_rc
        self.login, self.login_rc = login, login_rc
        self.labels = {'org.opencontainers.image.version': '0.3.83'} if labels is None else labels
        self.ids = {}
        self.hang = None  # 'probe' | 'login' | 'inspect'

    def kind(self, argv):
        if argv[1:3] == ['image', 'inspect']:
            return 'id' if '{{json .Id}}' in argv[4] else 'inspect'
        if argv[1:3] == ['rm', '-f']:
            return 'rm'
        return 'probe' if '--entrypoint' in argv else 'login'

    def __call__(self, argv, **kw):
        argv = list(argv)
        self.calls.append(argv)
        self.deadlines.append(kw.get('timeout'))
        kind = self.kind(argv)
        if self.hang == kind:
            raise subprocess.TimeoutExpired(argv, kw.get('timeout'))
        if kind == 'id':
            image_id = self.ids.get(argv[-1], IMAGE_ID)
            return SimpleNamespace(returncode=0, stderr='',
                                   stdout=f'"{image_id}" ["PDK_ROOT=/foss/pdks","PATH=/bin"]\n')
        if kind == 'inspect':
            return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps(self.labels))
        if kind == 'rm':
            return SimpleNamespace(returncode=0, stderr='', stdout='')
        if kind == 'probe':
            out = self.probe if isinstance(self.probe, str) else json.dumps(self.probe)
            return SimpleNamespace(returncode=self.probe_rc, stdout=out + '\n', stderr='boom')
        return SimpleNamespace(returncode=self.login_rc, stdout=self.login, stderr='')

    def of(self, kind):
        return [c for c in self.calls if self.kind(c) == kind]


@pytest.fixture
def docker(monkeypatch):
    monkeypatch.setattr(facts_mod, '_FACTS', {})
    monkeypatch.setenv('VIBEIC_DOCKER_MEMORY', '3g')
    fake = FakeDocker()
    monkeypatch.setattr(facts_mod.subprocess, 'run', fake)
    monkeypatch.setattr(contract.subprocess, 'run', fake)
    return fake


def _value(argv, flag):
    return argv[argv.index(flag) + 1]


# ── what is read, and how ──────────────────────────────────────────────────

def test_facts_come_from_the_image_through_two_capped_named_containers(docker):
    record = facts_mod.image_facts('repo@sha256:' + 'b' * 64)
    bypass, login = docker.of('probe'), docker.of('login')
    assert len(bypass) == len(login) == 1, docker.calls
    for argv in bypass + login:
        assert _value(argv, '--memory') == _value(argv, '--memory-swap') == '3g', argv
        assert argv.index('--memory') < argv.index(IMAGE_ID), argv
        assert _value(argv, '--network') == 'none', argv
        assert _value(argv, '--name').startswith('vibeic_llfacts'), argv
    assert _value(bypass[0], '--entrypoint') == 'timeout'
    # the login environment is only reachable through the image's own entrypoint
    assert '--entrypoint' not in login[0] and login[0][login[0].index(IMAGE_ID) + 1:][:2] == ['--skip', 'timeout']
    assert sorted(login[0][login[0].index('-c') + 2:]) == [
        'PAD_CELL_LIBRARY', 'PDK', 'PDK_ROOT', 'STD_CELL_LIBRARY'], login
    assert record['image_id'] == IMAGE_ID and record['image_version_label'] == '0.3.83'
    assert record['librelane_version'] == '3.1.0.dev1'
    assert record['provenance']['librelane']['ref'].startswith('9cf849541f')
    assert record['orfs_commit'].startswith('c9c22caf9') and record['not_measured'] == {}
    pdk = next(o for o in record['clis']['flow'] if o['name'] == 'pdk')
    assert pdk['login_env'] == {'PDK': 'ihp-sg13g2'} and pdk['bypass_env'] == {'PDK': None}


def test_every_tool_run_has_a_deadline(docker):
    facts_mod.image_facts('ref')
    assert docker.deadlines and all(isinstance(d, (int, float)) and 0 < d < 10 ** 4
                                    for d in docker.deadlines), list(zip(docker.calls, docker.deadlines))


def test_the_container_bounds_its_own_command_before_the_client(docker):
    facts_mod.image_facts('ref', deadline_s=120)
    [probe], [login] = docker.of('probe'), docker.of('login')
    at = probe.index('--kill-after=5')
    assert _value(probe, '--entrypoint') == 'timeout' and probe[at - 1] == IMAGE_ID, probe
    assert 0 < int(probe[at + 1]) < 120, probe
    at = login.index('--kill-after=5')
    assert login[at - 2:at] == ['--skip', 'timeout'] and 0 < int(login[at + 1]) < 120, login


def test_a_facts_probe_past_its_deadline_is_removed_by_name_and_refused(docker):
    docker.hang = 'probe'
    with pytest.raises(contract.Refusal, match=r'LL_IMAGE_FACTS_UNREADABLE.*deadline'):
        facts_mod.image_facts('ref')
    [probe] = docker.of('probe')
    assert docker.of('rm') == [['docker', 'rm', '-f', _value(probe, '--name')]]


def test_a_login_probe_past_its_deadline_is_named_not_read_as_unset(docker):
    docker.hang = 'login'
    record = facts_mod.image_facts('ref', deadline_s=30)
    assert 'deadline' in record['not_measured']['login_env']
    [login] = docker.of('login')
    assert docker.of('rm') == [['docker', 'rm', '-f', _value(login, '--name')]]
    assert 'environment NOT_MEASURED' in {r['source'] for r in facts_mod.implicit_cli_values(record, 'login')}
    # `--pad` is left to an environment nobody measured: refused, not read as unset
    with pytest.raises(contract.Refusal, match=r'--pad would take None from environment NOT_MEASURED'):
        facts_mod.require_explicit_cli_options(record, EXPLICIT, 'login', image_id=IMAGE_ID)
    facts_mod.require_explicit_cli_options(record, EXPLICIT[:-1] + ['--pad', 'p', 'c.yaml'],
                                           'login', image_id=IMAGE_ID)


def test_a_label_inspect_past_its_deadline_is_named(docker):
    docker.hang = 'inspect'
    record = facts_mod.image_facts('ref')
    assert 'deadline' in record['not_measured']['image_version_label']


def test_facts_are_cached_per_image_id_so_a_moved_tag_is_read_again(docker):
    first = facts_mod.image_facts('repo:latest')
    probes = len(docker.of('probe'))
    assert facts_mod.image_facts('repo:latest')['image_id'] == IMAGE_ID
    assert len(docker.of('probe')) == probes
    docker.ids['repo:latest'] = OTHER_ID      # the tag moved to a new image
    moved = facts_mod.image_facts('repo:latest')
    assert moved['image_id'] == OTHER_ID and len(docker.of('probe')) == probes + 1
    assert first['image_id'] == IMAGE_ID


def test_flow_steps_are_the_images_own_order(docker):
    record = facts_mod.image_facts('ref')
    assert facts_mod.flow_steps(record, 'Chip')[-1] == 'OpenROAD.PadRing'
    assert 'OpenROAD.PadRing' not in facts_mod.flow_steps(record, 'Classic')
    with pytest.raises(contract.Refusal, match='LL_FLOW_UNRESOLVED'):
        facts_mod.flow_steps(record, 'NoSuchFlow')


def test_a_probe_that_cannot_run_refuses_by_name(docker):
    docker.probe_rc = 1
    with pytest.raises(contract.Refusal, match='LL_IMAGE_FACTS_UNREADABLE'):
        facts_mod.image_facts('ref')
    docker.probe_rc, docker.probe = 0, 'Traceback (most recent call last): ...'
    with pytest.raises(contract.Refusal, match='LL_IMAGE_FACTS_UNREADABLE'):
        facts_mod.image_facts('ref')


def test_an_unmeasured_login_environment_is_named_not_read_as_unset(docker):
    docker.login_rc, docker.login = 127, 'exec: --skip: not found\n'
    record = facts_mod.image_facts('ref')
    assert 'login_env' in record['not_measured']
    assert all(o['login_env'] is None for rows in record['clis'].values() for o in rows)
    rows = facts_mod.implicit_cli_values(record, 'login')
    assert 'environment NOT_MEASURED' in {r['source'] for r in rows}
    with pytest.raises(contract.Refusal, match='LL_IMAGE_ENV_DEFAULT'):
        facts_mod.require_explicit_cli_options(record, EXPLICIT, 'login', image_id=IMAGE_ID)


def test_a_missing_label_is_named(docker):
    docker.labels = {}
    assert 'image_version_label' in facts_mod.image_facts('ref')['not_measured']


# ── the guard ──────────────────────────────────────────────────────────────

def test_the_login_environment_cannot_choose_the_pdk_or_the_cell_library(docker):
    """CMP3's first LibreLane attempt: through the image's entrypoint the CLI
    took the image's PDK and cell library from the environment."""
    record = facts_mod.image_facts('ref')
    base = ['--manual-pdk', '--pdk-root', '/pdk', 'config.yaml']
    with pytest.raises(contract.Refusal, match='LL_IMAGE_ENV_DEFAULT') as refused:
        facts_mod.require_explicit_cli_options(record, base, 'login', image_id=IMAGE_ID)
    text = str(refused.value)
    assert "--pdk would take 'ihp-sg13g2' from env PDK" in text
    assert "--scl would take 'sg13g2_stdcell' from env STD_CELL_LIBRARY" in text
    with pytest.raises(contract.Refusal, match=r'--scl would take'):
        facts_mod.require_explicit_cli_options(record, base + ['--pdk', 'target'], 'login',
                                               image_id=IMAGE_ID)
    facts_mod.require_explicit_cli_options(record, EXPLICIT, 'login', image_id=IMAGE_ID)
    facts_mod.require_explicit_cli_options(
        record, ['--manual-pdk', '--pdk-root=/pdk', '-p', 'target', '-s', 'lib', 'c.yaml'],
        'login', image_id=IMAGE_ID)


def test_the_builtin_ciel_default_cannot_choose_the_pdk_version(docker):
    """`--ciel-pdk/--manual-pdk` reads no variable and defaults to fetching the
    PDK through Ciel at LibreLane's own pinned version, replacing an explicit
    `--pdk-root`. It is in the PDK group, so it is guarded."""
    record = facts_mod.image_facts('ref')
    argv = ['--pdk-root', '/pdk', '--pdk', 't', 'c.yaml']
    with pytest.raises(contract.Refusal, match=r'--manual-pdk would take True from CLI default'):
        facts_mod.require_explicit_cli_options(record, argv, 'bypass', image_id=IMAGE_ID)
    facts_mod.require_explicit_cli_options(record, ['--manual-pdk'] + argv, 'bypass', image_id=IMAGE_ID)
    facts_mod.require_explicit_cli_options(record, ['--ciel-pdk'] + argv, 'bypass', image_id=IMAGE_ID)
    names = {o['name'] for o in facts_mod.guarded_options(record)}
    assert names == {'use_ciel', 'pdk_root', 'pdk', 'scl', 'pad'}   # jobs, condensed are not


def test_bypassing_the_entrypoint_still_leaves_the_pdk_root_and_the_builtin_pdk(docker):
    record = facts_mod.image_facts('ref')
    rows = {tuple(r['opts']): r for r in facts_mod.implicit_cli_values(record, 'bypass')}
    assert rows[('--pdk-root',)] == {'opts': ['--pdk-root'], 'source': 'env PDK_ROOT',
                                     'value': '/foss/pdks'}
    assert rows[('-p', '--pdk')]['source'] == 'CLI default'
    assert ('-s', '--scl') not in rows
    with pytest.raises(contract.Refusal, match=r'--pdk-root would take'):
        facts_mod.require_explicit_cli_options(record, ['--manual-pdk', '--pdk', 't', 'c.yaml'],
                                               'bypass', image_id=IMAGE_ID)
    facts_mod.require_explicit_cli_options(
        record, ['--manual-pdk', '--pdk-root', '/pdk', '--pdk', 't', 'c.yaml'], 'bypass', image_id=IMAGE_ID)
    with pytest.raises(contract.Refusal, match='LL_INVALID_ENTRYPOINT'):
        facts_mod.implicit_cli_values(record, 'shell')


def test_the_per_step_cli_pdk_root_default_is_guarded(docker):
    """`librelane.steps run --pdk-root` takes its default from PDK_ROOT at
    import time (no `envvar=`); the step CLI declares no option groups, so it is
    guarded as the same parameter as the flow CLI's `--pdk-root`."""
    record = facts_mod.image_facts('ref')
    step = ['--id', 'X.Y', '-c', 'cfg.json', '-i', 'in.json', '-o', 'out']
    with pytest.raises(contract.Refusal, match=r"step CLI: --pdk-root would take '/foss/pdks' from CLI default"):
        facts_mod.require_explicit_cli_options(record, step, 'bypass', image_id=IMAGE_ID, cli='step')
    facts_mod.require_explicit_cli_options(record, step + ['--pdk-root', '/pdk'], 'bypass',
                                           image_id=IMAGE_ID, cli='step')
    with pytest.raises(contract.Refusal, match='LL_INVALID_CLI'):
        facts_mod.guarded_options(record, 'eject')


def test_a_callable_default_is_unknown_not_absent(docker):
    probe = json.loads(json.dumps(REAL_PROBE))
    scl = next(o for o in probe['clis']['flow'] if o['name'] == 'scl')
    scl['default_measured'] = False
    docker.probe = probe
    record = facts_mod.image_facts('ref')
    rows = {tuple(r['opts']): r for r in facts_mod.implicit_cli_values(record, 'bypass')}
    assert rows[('-s', '--scl')] == {'opts': ['-s', '--scl'], 'source': 'CLI default NOT_MEASURED',
                                     'value': None}
    with pytest.raises(contract.Refusal, match=r'--scl would take None from CLI default NOT_MEASURED'):
        facts_mod.require_explicit_cli_options(record, ['--manual-pdk', '--pdk-root', '/p', '--pdk', 't'],
                                               'bypass', image_id=IMAGE_ID)


def test_an_unread_cli_leaves_the_guard_scope_unknown(docker):
    probe = json.loads(json.dumps(REAL_PROBE))
    probe['clis'] = {'flow': [], 'step': None}
    docker.probe = probe
    record = facts_mod.image_facts('ref')
    assert facts_mod.guarded_options(record) is None
    with pytest.raises(contract.Refusal, match=r'LL_IMAGE_ENV_DEFAULT: flow CLI: \(scope\).*NOT_MEASURED'):
        facts_mod.require_explicit_cli_options(record, EXPLICIT, 'bypass', image_id=IMAGE_ID)


def test_facts_from_another_image_refuse(docker):
    record = facts_mod.image_facts('ref')
    with pytest.raises(contract.Refusal, match='LL_IMAGE_FACTS_STALE'):
        facts_mod.require_explicit_cli_options(record, EXPLICIT, 'bypass', image_id=OTHER_ID)


# ── the probe script itself, on a stand-in LibreLane ──────────────────────

_STAND_IN = {
    'librelane/__init__.py': "__version__ = '9.9.9'\n",
    'librelane/flows/__init__.py': textwrap.dedent('''
        import pathlib, types, typing
        def _v(name, default, type_=int, pdk=False):
            return types.SimpleNamespace(name=name, default=default, type=type_, pdk=pdk)
        class _S:
            def __init__(self, i, variables): self.id, self._v = i, variables
            def get_all_config_variables(self): return self._v
        class _F:
            config_vars = [_v('FLOW_LEVEL', 'x', str)]
            Steps = [_S('A.One', [_v('UTIL', 50), _v('ROOT', pathlib.Path('/r')), _v('SHARED', 1),
                                  _v('PDKV', None, str, pdk=True), _v('OPTV', None, typing.Optional[int]),
                                  _v('PEP604', None, int | None), _v('REQUIRED', None, int)]),
                     _S('B.Two', [_v('DYN', lambda: 3), _v('SHARED', 1)])]
        class _Factory:
            @staticmethod
            def get(name): return _F if name == 'Classic' else None
        class Flow:
            factory = _Factory
        '''),
    'librelane/__main__.py': textwrap.dedent('''
        import click, types
        @click.command()
        @click.option('--pdk-root', envvar='PDK_ROOT', default=None)
        @click.option('-p', '--pdk', envvar='PDK', default='builtinPDK')
        @click.option('--ciel-pdk/--manual-pdk', 'use_ciel', default=True)
        @click.option('-j', '--jobs', default=lambda: 4)
        @click.argument('config_files', nargs=-1)
        def cli(**kw): pass
        _g = {p.name: p for p in cli.params}
        cli.option_groups = [types.SimpleNamespace(title='PDK', options=[_g['pdk_root'], _g['pdk'], _g['use_ciel']])]
        '''),
    'librelane/steps/__init__.py': '',
    'librelane/steps/__main__.py': textwrap.dedent('''
        import click, os
        @click.group()
        def cli(): pass
        @cli.command()
        @click.option('--pdk-root', default=os.environ.pop('PDK_ROOT', None))
        @click.option('--id')
        def run(**kw): pass
        '''),
}


def _run_probe(tmp_path):
    for rel, text in _STAND_IN.items():
        path = tmp_path / 'pkg' / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    env = {**os.environ, 'PYTHONPATH': str(tmp_path / 'pkg'), 'PDK_ROOT': '/image/pdks', 'PATH': '/nonexistent'}
    done = subprocess.run([sys.executable, '-c', facts_mod._PROBE, 'Classic,Chip', str(tmp_path / 'none')],
                          capture_output=True, text=True, env=env, timeout=120)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_the_probe_itself_records_builtin_and_callable_defaults(tmp_path):
    out = _run_probe(tmp_path)
    # shape-free first: the no-variable PDK flag is recorded at all, and the
    # callable default is named as unread
    assert '--manual-pdk' in json.dumps(out), out
    assert any('jobs' in key for key in out['not_measured']), out['not_measured']
    flow = {o['name']: o for o in out['clis']['flow']}
    assert flow['use_ciel']['opts'] == ['--ciel-pdk', '--manual-pdk']
    assert (flow['use_ciel']['default'], flow['use_ciel']['group'], flow['use_ciel']['envvar']) == (True, 'PDK', [])
    assert flow['pdk']['default'] == 'builtinPDK' and flow['pdk_root']['bypass_env'] == {'PDK_ROOT': '/image/pdks'}
    assert flow['jobs']['default'] is None and flow['jobs']['default_measured'] is False
    assert 'clis.flow.jobs.default' in out['not_measured']
    assert 'config_files' not in flow    # an argument, not an option
    step = {o['name']: o for o in out['clis']['step']}
    assert step['pdk_root']['default'] == '/image/pdks' and step['pdk_root']['envvar'] == []
    assert out['flows'] == {'Classic': ['A.One', 'B.Two'], 'Chip': None}
    assert out['librelane_version'] == '9.9.9'


def test_the_probe_itself_guards_what_it_records(tmp_path):
    record = {'image_id': IMAGE_ID, **_run_probe(tmp_path)}
    for rows in record['clis'].values():
        for o in rows:
            o['login_env'] = {v: None for v in o['envvar']}
    names = {tuple(r['opts']) for r in facts_mod.implicit_cli_values(record, 'bypass')}
    assert names == {('--pdk-root',), ('-p', '--pdk'), ('--ciel-pdk', '--manual-pdk')}
    assert {tuple(r['opts']) for r in facts_mod.implicit_cli_values(record, 'bypass', 'step')} == {('--pdk-root',)}


def test_the_probe_itself_records_the_variable_registry(tmp_path):
    reg = _run_probe(tmp_path)['config_variables']
    assert reg['UTIL']['default'] == 50 and reg['UTIL']['default_measured'] is True
    assert reg['ROOT']['default'] == '/r'                     # a Path default, as text
    assert reg['FLOW_LEVEL']['default'] == 'x'                # the flow's own variables too
    assert reg['SHARED']['default'] == 1 and reg['SHARED']['default_measured'] is True
    assert reg['UTIL'] == {'default': 50, 'default_measured': True, 'type': "<class 'int'>",
                           'pdk': False, 'flows': ['Classic']}


@pytest.mark.parametrize('var, group, why, count', [
    ('PDKV', 'pdk_defaults', 'the PDK supplies', 1),
    ('OPTV', 'runtime_defaults', 'Optional with no declared default', 2),
    ('DYN', 'callable_defaults', 'a function decides', 1)])
def test_a_default_the_declaration_does_not_fix_is_not_measured(tmp_path, var, group, why, count):
    """Review W22REG: 148 PDK-sourced and 96 run-time variables on 0.3.83 were
    recorded as measured defaults (RT_MAX_LAYER: None)."""
    out = _run_probe(tmp_path)
    row = out['config_variables'][var]
    assert row['default_measured'] is False and row['default'] is None, row
    assert row['not_measured_reason'].startswith(why), row
    assert out['not_measured'][f'config_variables.{group}'].startswith(f'{count} variable(s): {why}')


def test_pep604_optional_none_is_a_runtime_default(tmp_path):
    out = _run_probe(tmp_path)
    row = out['config_variables']['PEP604']
    assert row['default'] is None and row['default_measured'] is False, row
    assert row['not_measured_reason'].startswith('Optional with no declared default')
    assert 'PEP604' in out['not_measured']['config_variables.runtime_defaults']


def test_required_none_is_not_a_measured_default(tmp_path):
    out = _run_probe(tmp_path)
    row = out['config_variables']['REQUIRED']
    assert row['default'] is None and row['default_measured'] is False, row
    assert 'MissingRequiredVariable' in row['not_measured_reason']
    assert 'REQUIRED' in out['not_measured']['config_variables.required_defaults']


def test_the_pdk_flag_is_recorded(tmp_path):
    reg = _run_probe(tmp_path)['config_variables']
    assert reg['PDKV']['pdk'] is True and reg['UTIL']['pdk'] is False


_TWO_FLOWS = textwrap.dedent('''
    import types
    def _v(name, default): return types.SimpleNamespace(name=name, default=default, type=int, pdk=False)
    class _S:
        def __init__(self, i, variables): self.id, self._v = i, variables
        def get_all_config_variables(self): return self._v
    SAME = [f'LONG_VARIABLE_NAME_{i}' for i in range(30)]
    class _Classic:
        Steps = [_S('A.One', [_v('UTIL', 50), _v('SHARED', 1)] + [_v(n, 1) for n in SAME])]
    class _Chip:
        Steps = [_S('C.One', [_v('UTIL', 50), _v('SHARED', 2)] + [_v(n, 2) for n in SAME])]
    class _Factory:
        @staticmethod
        def get(name): return {'Classic': _Classic, 'Chip': _Chip}.get(name)
    class Flow:
        factory = _Factory
    ''')


def _run_probe_with(tmp_path, overrides):
    for rel, text in {**_STAND_IN, **overrides}.items():
        path = tmp_path / 'pkg' / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    env = {**os.environ, 'PYTHONPATH': str(tmp_path / 'pkg'), 'PDK_ROOT': '/image/pdks', 'PATH': '/nonexistent'}
    done = subprocess.run([sys.executable, '-c', facts_mod._PROBE, 'Classic,Chip', str(tmp_path / 'none')],
                          capture_output=True, text=True, env=env, timeout=120)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_the_probe_uses_the_variables_own_optional_answer(tmp_path):
    flow = textwrap.dedent('''
        class _Var:
            name, default, type, pdk, optional = 'OWN_OPT', None, int, False, True
        class _Step:
            id = 'A.One'
            @staticmethod
            def get_all_config_variables(): return [_Var()]
        class _Classic:
            config_vars, Steps = [], [_Step()]
        class _Factory:
            @staticmethod
            def get(name): return _Classic if name == 'Classic' else None
        class Flow:
            factory = _Factory
        ''')
    out = _run_probe_with(tmp_path, {'librelane/flows/__init__.py': flow})
    row = out['config_variables']['OWN_OPT']
    assert row['default_measured'] is False and row['default'] is None, row
    assert row['not_measured_reason'].startswith('Optional with no declared default')


def test_declarations_that_disagree_across_flows_are_not_measured_in_the_row(tmp_path):
    """Review W22REG: the row itself carries the clash -- a resolver reads
    rows, and a `not_measured` string is cut at 300 characters."""
    out = _run_probe_with(tmp_path, {'librelane/flows/__init__.py': _TWO_FLOWS})
    reg = out['config_variables']
    shared = reg['SHARED']
    assert shared['default'] is None and shared['default_measured'] is False, shared
    clashing = [n for n, r in reg.items() if n.startswith('LONG_VARIABLE_NAME_')]
    assert len(clashing) == 30 and all(reg[n]['default_measured'] is False for n in clashing)
    assert shared['defaults_by_flow'] == {'Classic': 1, 'Chip': 2}
    assert shared['not_measured_reason'].startswith('declared with different defaults')
    assert out['not_measured']['config_variables.differing_defaults'].startswith('31 variable(s)')
    assert reg['UTIL']['default'] == 50 and reg['UTIL']['default_measured'] is True
    assert reg['UTIL']['flows'] == ['Classic', 'Chip']


def test_the_registry_is_what_the_staged_config_resolver_takes(docker):
    record = facts_mod.image_facts('ref')
    registry = facts_mod.tool_registry(record)
    assert registry['FP_CORE_UTIL']['default'] == 50
    assert all('default' in row and 'default_measured' in row for row in registry.values())
    # An unfixed default is None and says why, never a value the tool applies.
    unfixed = {k: r for k, r in registry.items() if not r['default_measured']}
    assert unfixed and all(r['default'] is None and r['not_measured_reason'] for r in unfixed.values())
    probe = json.loads(json.dumps(REAL_PROBE))
    del probe['config_variables']
    docker.probe = probe
    facts_mod._FACTS.clear()
    assert facts_mod.tool_registry(facts_mod.image_facts('ref')) is None


# ── the command line ───────────────────────────────────────────────────────

def test_the_cli_writes_the_record_atomically(docker, tmp_path, capsys):
    out = tmp_path / 'facts.json'
    assert facts_mod.main(['ref', '--deadline', '60', '--out', str(out)]) == 0
    written = json.loads(out.read_text())
    assert written['image_id'] == IMAGE_ID and written['deadline_s'] == 60
    assert json.loads(capsys.readouterr().out)['image_version_label'] == '0.3.83'
    docker.probe_rc = 1
    facts_mod._FACTS.clear()
    assert facts_mod.main(['ref']) == 2
    assert 'LL_IMAGE_FACTS_UNREADABLE' in capsys.readouterr().err
