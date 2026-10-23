"""The canonical CLI must propagate qualification refusal before native launch."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from test_flow109_admission import fixture_context


def test_canonical_entry_returns_refusal_without_launching_native(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    project.mkdir()
    context, registry = fixture_context(project, monkeypatch)
    registry['uses'][0]['status'] = 'UNSUPPORTED'
    (project / 'qualifications.json').write_text(json.dumps(registry))
    manifest = project / 'flow109-input.json'
    manifest.write_text(json.dumps({
        'features': context['features'],
        'runtime_tool_uses': context['runtime_tool_uses'],
        'qualification_registry': 'qualifications.json',
    }))
    programs = Path(__file__).resolve().parents[1]
    audit = tmp_path / 'native-launches.jsonl'
    wrapper = tmp_path / 'observe-canonical-entry.py'
    wrapper.write_text('''import json, runpy, sys
from pathlib import Path
audit_path, runner = sys.argv[1:3]
def observe(event, args):
    if event == 'subprocess.Popen':
        with open(audit_path, 'a') as handle:
            handle.write(json.dumps(str(args)) + '\\n')
sys.addaudithook(observe)
sys.argv = [runner] + sys.argv[3:]
sys.path.insert(0, str(Path(runner).parent))
runpy.run_path(runner, run_name='__main__')
''')
    env = dict(os.environ)
    env.pop('VIBEIC_FLOW109_CONTEXT', None)
    result = subprocess.run([
        sys.executable, str(wrapper), str(audit),
        str(programs / 'vibe_ic_one_shot_runner.py'), str(project),
        '--flow', '109', '--flow109-manifest', str(manifest),
        '--entry-step', 'D38', '--exit-step', 'D38',
        '--route', 'ic', '--no-dashboard',
    ], capture_output=True, text=True, env=env, timeout=60)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'QUALIFICATION_UNSUPPORTED' in result.stderr
    receipt = json.loads((project / 'reports/flow109/admission.json').read_text())
    assert receipt['reason'] == 'QUALIFICATION_UNSUPPORTED'
    assert receipt['native_started'] is False
    assert not audit.exists() or audit.read_text() == ''
    assert not (project / 'reports/flow109/requests').exists()
