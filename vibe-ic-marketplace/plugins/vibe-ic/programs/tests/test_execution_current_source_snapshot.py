"""Current-source checks against real, committed, isolated fixture sources."""
from pathlib import Path
import os
import subprocess
import sys

import pytest
import execution_modes as em


@pytest.mark.parametrize('phase', ['within', 'between'])
@pytest.mark.parametrize('mutation', [
    'bytes', 'new_helper', 'preferred_package', 'leaf_target', 'parent_target',
])
def test_current_source_check_rejects_source_and_resolution_mutations(tmp_path, phase, mutation):
    clone = tmp_path / 'source'
    cloned = subprocess.run(['git', 'clone', '--quiet', '--local', '--no-hardlinks',
                             str(em._REPO_ROOT), str(clone)], capture_output=True, text=True)
    assert cloned.returncode == 0, cloned.stderr
    script = r'''
from dataclasses import replace
import importlib.util
from pathlib import Path
import subprocess
import sys
import execution_modes as em

phase, mutation = sys.argv[1:]
root = em._REPO_ROOT / 'current-source-fixture'
root.mkdir()
first = root / 'first'; second = root / 'second'
first.mkdir(); second.mkdir()
for directory in (first, second):
    (directory / '__init__.py').write_text('"""Fixture package."""\n')
    (directory / 'helper.py').write_text('value = 1\n')
alias = root / 'alias'
alias.symlink_to(first, target_is_directory=True)
module = root / 'selection.py'
module.write_text('value = 1\n')
entry = root / 'entry.py'
entry.write_text('import selection\nimport alias.helper\n'
                 'try:\n    import late_current_source_helper\nexcept ImportError:\n    pass\n'
                 'def validate(outputs, binding):\n    return None\n')
subprocess.run(['git', '-C', str(em._REPO_ROOT), 'add', str(root)], check=True)
subprocess.run(['git', '-C', str(em._REPO_ROOT), '-c', 'user.name=Snapshot fixture',
                '-c', 'user.email=fixture@example.invalid', 'commit', '--quiet',
                '-m', 'Commit isolated current-source fixture'], check=True)
sha = subprocess.check_output(['git', '-C', str(em._REPO_ROOT), 'rev-parse', 'HEAD'], text=True).strip()
assert not subprocess.check_output(['git', '-C', str(em._REPO_ROOT), 'status', '--porcelain'], text=True)
# Load only the pure validator definition. The producer entry remains bound
# and is parsed by the real registry and current-source resolver.
namespace = {'__name__': 'current_source_fixture', '__file__': str(entry)}
exec(compile('def validate(outputs, binding):\n    return None\n', str(entry), 'exec'), namespace)
paths = [entry, module, *(first.glob('*.py')), *(second.glob('*.py')), Path(sys.executable).resolve()]
sources = {str(path): em.digest(path) for path in paths}
arm = em.Adapter('current-source-fixture', 'fixture-python', '1', sha, sources,
                 sys.version, ('fixture-python',),
                 (em.Component('fixture', (str(Path(sys.executable).resolve()), str(entry)), 2),),
                 namespace['validate'], ('result.json',), {'goal': 'fixture'},
                 qualification_evidence='Synthetic committed current-source control only')
registry = em.Registry(); registry.register(arm)
arm = registry.adapters('1')[0]
before = em.Controller._source_current(arm)
assert str(module) in before['files'] and str(first / 'helper.py') in before['files']

def mutate():
    if mutation == 'bytes':
        module.write_text('value = 2\n')
    elif mutation == 'new_helper':
        (root / 'late_current_source_helper.py').write_text('value = 3\n')
    elif mutation == 'preferred_package':
        package = root / 'selection'; package.mkdir()
        (package / '__init__.py').write_text('value = 4\n')
    elif mutation == 'leaf_target':
        module.unlink(); module.symlink_to(second / 'helper.py')
    else:
        alias.unlink(); alias.symlink_to(second, target_is_directory=True)

if phase == 'within':
    resolver = em._provider_identity
    def resolve_then_mutate(*args, **kwargs):
        result = resolver(*args, **kwargs)
        mutate()
        return result
    # Deterministically mutate after the actual resolver has captured all
    # source/probe state, without substituting any identity or Git binding.
    em._provider_identity = resolve_then_mutate
else:
    mutate()
try:
    after = em.Controller._source_current(arm)
    if phase == 'between' and after != before:
        print(phase, mutation, 'PROVIDER_CLOSURE_CHANGED')
        raise SystemExit(0)
except em.Refusal as exc:
    expected = ({'SOURCE_AUTHORITY_DIRTY', 'SOURCE_AUTHORITY_UNAVAILABLE'} if phase == 'within'
                else {'ADAPTER_SOURCE_MISMATCH', 'ENTRY_SOURCE_CHANGED'})
    assert exc.code in expected, exc
    print(phase, mutation, exc.code)
else:
    raise AssertionError('mutation reached a current-source identity')
'''
    plugin = clone / 'vibe-ic-marketplace/plugins/vibe-ic'
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
                       PYTHONPATH=str(plugin / 'programs'))
    probe = subprocess.run([sys.executable, '-c', script, phase, mutation],
                           cwd=plugin, env=environment, capture_output=True, text=True, timeout=30)
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert probe.stdout.startswith(f'{phase} {mutation} '), probe.stdout
