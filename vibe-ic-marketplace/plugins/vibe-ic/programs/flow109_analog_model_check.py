"""Consume current A02 ngspice model-smoke receipts without granting signoff."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
import re

FATAL = (
    'could not find a valid modelname', 'unable to find definition of model',
    'unknown subckt', 'unknown device type', 'no simulations run',
    'simulation interrupted due to error', 'fatal error',
)
NUMBER = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def consume(project, requirements, report):
    """Rebind every receipt to its frozen requirement and current bytes."""
    project = Path(project).resolve()
    expected = {row['id']: row for row in requirements['required_smokes']}
    rows = report.get('smokes')
    if not isinstance(rows, list) or {r.get('id') for r in rows if isinstance(r, dict)} != set(expected):
        return {'verdict': 'FAIL', 'reason': 'A02_SMOKE_COVERAGE_MISMATCH', 'smokes': []}
    checked = []
    for row in rows:
        need = expected[row['id']]
        identity = {key: row.get(key) for key in ('id', 'device', 'corner', 'model', 'deck',
                                                  'required_measurements')}
        if identity != need:
            return {'verdict': 'FAIL', 'reason': 'A02_SMOKE_IDENTITY_MISMATCH', 'smokes': checked}
        try:
            log = (project / row['log']['path']).resolve()
            if (not log.is_relative_to(project) or not log.is_file()
                    or _sha(log) != row['log']['sha256']):
                raise OSError
            for key in ('model', 'deck'):
                path = (project / row[key]['path']).resolve()
                if (not path.is_relative_to(project) or not path.is_file()
                        or _sha(path) != row[key]['sha256']):
                    raise OSError
        except (KeyError, OSError):
            return {'verdict': 'FAIL', 'reason': 'A02_CURRENT_EVIDENCE_STALE', 'smokes': checked}
        text = log.read_text(errors='replace')
        lowered = text.lower()
        if row.get('returncode') != 0 or any(marker in lowered for marker in FATAL):
            return {'verdict': 'FAIL', 'reason': 'A02_NATIVE_MODEL_LOAD_FAILED', 'smokes': checked}
        values = {}
        for name in need['required_measurements']:
            hits = re.findall(r'(?im)^\s*' + re.escape(name) + r'\s*=\s*(' + NUMBER + r')\b', text)
            if len(hits) != 1:
                return {'verdict': 'FAIL', 'reason': 'A02_NATIVE_MEASUREMENT_ABSENT', 'smokes': checked}
            value = float(hits[0])
            if not math.isfinite(value):
                return {'verdict': 'FAIL', 'reason': 'A02_NATIVE_MEASUREMENT_NONFINITE', 'smokes': checked}
            values[name] = value
        checked.append({'id': row['id'], 'device': row['device'], 'corner': row['corner'],
                        'measurements': values, 'log': row['log']})
    return {'verdict': 'PASS', 'reason': 'A02_DECLARED_NATIVE_SMOKES_COMPLETE',
            'smokes': checked,
            'scope': 'declared model/device component only; PDK and benchmark signoff NOT_MEASURED'}
