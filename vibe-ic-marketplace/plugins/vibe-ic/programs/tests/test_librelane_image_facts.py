"""librelane_image_facts: image facts are read from the image at run time.

Docker is substituted at the process edge; everything above it is the real
module. The probe replies below are the real output of the shipped probes on
vibeic-eda 0.3.83 (2026-09-28, 8HD-9), with the flow step lists shortened and
the provenance map cut to two tools.
"""
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
facts_mod = importlib.import_module('librelane_image_facts')
contract = importlib.import_module('librelane_contract')

IMAGE_ID = 'sha256:' + 'a' * 64
REAL_PROBE = {
    'flows': {'Classic': ['Verilator.Lint', 'Checker.LintTimingConstructs', 'Yosys.Synthesis'],
              'Chip': ['Verilator.Lint', 'Checker.LintTimingConstructs', 'Yosys.Synthesis',
                       'OpenROAD.PadRing']},
    'cli_options': [
        {'opts': ['--pdk-root'], 'envvar': ['PDK_ROOT'], 'default': None,
         'bypass_env': {'PDK_ROOT': '/foss/pdks'}},
        {'opts': ['-p', '--pdk'], 'envvar': ['PDK'], 'default': 'sky130A',
         'bypass_env': {'PDK': None}},
        {'opts': ['-s', '--scl'], 'envvar': ['STD_CELL_LIBRARY'], 'default': None,
         'bypass_env': {'STD_CELL_LIBRARY': None}},
        {'opts': ['--pad'], 'envvar': ['PAD_CELL_LIBRARY'], 'default': None,
         'bypass_env': {'PAD_CELL_LIBRARY': None}}],
    'provenance': {'librelane': {'ref': '9cf849541fb02082c5890cbbf2cab4d796a9f8f9',
                                 'repo': 'https://github.com/vibeic/librelane.git',
                                 'tool': 'librelane'},
                   'openroad': {'ref': 'da11f47e14', 'tool': 'openroad'}},
    'not_measured': {},
    'librelane_version': '3.1.0.dev1',
    'openroad_version': '26Q3-3002-gda11f47e14',
    'orfs_commit': 'c9c22caf9bf9cfe46c5a4236c6ec7e7ae9863cc3'}
REAL_LOGIN = ('[INFO] USER_ID: 1000, GROUP_ID: 0\n'
              '[INFO] SKIPPING UI STARTUP\n'
              "[INFO] Executing command: 'python3 -c ...'\n"
              'VIBEIC_LOGIN_ENV {"PAD_CELL_LIBRARY": null, "PDK": "ihp-sg13g2", '
              '"PDK_ROOT": "/foss/pdks", "STD_CELL_LIBRARY": "sg13g2_stdcell"}\n')


class FakeDocker:
    """Answers the four docker calls image_facts makes, and records each."""

    def __init__(self, probe=REAL_PROBE, probe_rc=0, login=REAL_LOGIN, login_rc=0,
                 labels=None):
        self.calls = []
        self.probe, self.probe_rc = probe, probe_rc
        self.login, self.login_rc = login, login_rc
        self.labels = {'org.opencontainers.image.version': '0.3.83'} if labels is None else labels

    def __call__(self, argv, **_kw):
        argv = list(argv)
        self.calls.append(argv)
        if argv[1:3] == ['image', 'inspect'] and '{{json .Id}}' in argv[4]:
            return SimpleNamespace(returncode=0, stderr='',
                                   stdout=f'"{IMAGE_ID}" ["PDK_ROOT=/foss/pdks","PATH=/bin"]\n')
        if argv[1:3] == ['image', 'inspect']:
            return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps(self.labels))
        if argv[1] == 'run' and '--entrypoint' in argv:
            out = self.probe if isinstance(self.probe, str) else json.dumps(self.probe)
            return SimpleNamespace(returncode=self.probe_rc, stdout=out + '\n', stderr='boom')
        if argv[1] == 'run':
            return SimpleNamespace(returncode=self.login_rc, stdout=self.login, stderr='')
        raise AssertionError(f'unexpected docker call {argv}')


@pytest.fixture
def docker(monkeypatch):
    monkeypatch.setattr(facts_mod, '_FACTS', {})
    monkeypatch.setenv('VIBEIC_DOCKER_MEMORY', '3g')
    fake = FakeDocker()
    monkeypatch.setattr(facts_mod.subprocess, 'run', fake)
    monkeypatch.setattr(contract.subprocess, 'run', fake)
    return fake


def test_facts_come_from_the_image_through_two_capped_containers(docker):
    record = facts_mod.image_facts('repo@sha256:' + 'b' * 64)
    runs = [c for c in docker.calls if c[1] == 'run']
    assert len(runs) == 2, docker.calls
    bypass, login = runs
    for argv in runs:
        assert argv[argv.index('--memory') + 1] == '3g', argv
        assert argv[argv.index('--memory-swap') + 1] == '3g', argv
        assert argv.index('--memory') < argv.index(IMAGE_ID), argv
        assert argv[argv.index('--network') + 1] == 'none', argv
    assert bypass[bypass.index('--entrypoint') + 1] == 'python3'
    # the login environment is only reachable through the image's own entrypoint
    assert '--entrypoint' not in login and login[login.index(IMAGE_ID) + 1] == '--skip', login
    assert sorted(login[login.index('-c') + 2:]) == [
        'PAD_CELL_LIBRARY', 'PDK', 'PDK_ROOT', 'STD_CELL_LIBRARY'], login
    assert record['image_id'] == IMAGE_ID
    assert record['image_version_label'] == '0.3.83'
    assert record['librelane_version'] == '3.1.0.dev1'
    assert record['provenance']['librelane']['ref'].startswith('9cf849541f')
    assert record['orfs_commit'].startswith('c9c22caf9')
    assert record['not_measured'] == {}
    pdk = next(o for o in record['cli_options'] if '--pdk' in o['opts'])
    assert pdk['login_env'] == {'PDK': 'ihp-sg13g2'} and pdk['bypass_env'] == {'PDK': None}


def test_facts_are_read_once_per_reference_and_again_for_another(docker):
    facts_mod.image_facts('ref-one')
    first = len(docker.calls)
    facts_mod.image_facts('ref-one')
    assert len(docker.calls) == first
    facts_mod.image_facts('ref-two')
    assert len(docker.calls) == 2 * first


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
    assert all(o['login_env'] is None for o in record['cli_options'])
    rows = facts_mod.implicit_cli_values(record, 'login')
    assert {r['source'] for r in rows} == {'environment NOT_MEASURED'}
    with pytest.raises(contract.Refusal, match='LL_IMAGE_ENV_DEFAULT'):
        facts_mod.require_explicit_cli_options(
            record, ['--pdk-root', '/pdk', '--pdk', 'x', '--scl', 'y', 'cfg.yaml'], 'login')


def test_a_missing_label_is_named(docker):
    docker.labels = {}
    assert 'image_version_label' in facts_mod.image_facts('ref')['not_measured']


def test_the_login_environment_cannot_choose_the_pdk_or_the_cell_library(docker):
    """CMP3's first LibreLane attempt: through the image's entrypoint the CLI
    took the image's PDK and cell library from the environment."""
    record = facts_mod.image_facts('ref')
    base = ['--manual-pdk', '--pdk-root', '/pdk', 'config.yaml']
    with pytest.raises(contract.Refusal, match='LL_IMAGE_ENV_DEFAULT') as refused:
        facts_mod.require_explicit_cli_options(record, base, 'login')
    text = str(refused.value)
    assert "--pdk would take 'ihp-sg13g2' from env PDK" in text
    assert "--scl would take 'sg13g2_stdcell' from env STD_CELL_LIBRARY" in text
    with pytest.raises(contract.Refusal, match=r'--scl would take'):
        facts_mod.require_explicit_cli_options(record, base + ['--pdk', 'target'], 'login')
    facts_mod.require_explicit_cli_options(
        record, base + ['--pdk', 'target', '--scl', 'lib'], 'login')
    facts_mod.require_explicit_cli_options(
        record, ['--manual-pdk', '--pdk-root=/pdk', '-p', 'target', '-s', 'lib', 'c.yaml'], 'login')


def test_bypassing_the_entrypoint_still_leaves_the_pdk_root_and_the_builtin_pdk(docker):
    """An overridden --entrypoint drops the login PDK, but PDK_ROOT is image
    configuration and --pdk has a built-in default: both are still implicit."""
    record = facts_mod.image_facts('ref')
    rows = {tuple(r['opts']): r for r in facts_mod.implicit_cli_values(record, 'bypass')}
    assert rows[('--pdk-root',)] == {'opts': ['--pdk-root'], 'source': 'env PDK_ROOT',
                                     'value': '/foss/pdks'}
    assert rows[('-p', '--pdk')]['source'] == 'CLI default'
    assert ('-s', '--scl') not in rows
    with pytest.raises(contract.Refusal, match=r'--pdk-root would take'):
        facts_mod.require_explicit_cli_options(record, ['--pdk', 't', 'c.yaml'], 'bypass')
    facts_mod.require_explicit_cli_options(
        record, ['--pdk-root', '/pdk', '--pdk', 't', 'c.yaml'], 'bypass')
    with pytest.raises(contract.Refusal, match='LL_INVALID_ENTRYPOINT'):
        facts_mod.implicit_cli_values(record, 'shell')


def test_the_cli_writes_the_record_atomically(docker, tmp_path, capsys):
    out = tmp_path / 'facts.json'
    assert facts_mod.main(['ref', '--out', str(out)]) == 0
    assert json.loads(out.read_text())['image_id'] == IMAGE_ID
    assert json.loads(capsys.readouterr().out)['image_version_label'] == '0.3.83'
    docker.probe_rc = 1
    facts_mod._FACTS.clear()
    assert facts_mod.main(['ref']) == 2
    assert 'LL_IMAGE_FACTS_UNREADABLE' in capsys.readouterr().err
