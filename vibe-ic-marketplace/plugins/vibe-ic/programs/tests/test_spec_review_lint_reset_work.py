"""Issue #3489: source-stated reset stimulus is not one literal phrase."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).parents[1] / 'spec_review_lint.py'


def lint_reset(tmp_path, stimulus, scenario=''):
    # Verbatim architecture and protocol/verification fragments from the
    # original nine-document regression fixture, not its normalized successor.
    docs = {
        'L2.md': 'The synchronous active-high reset rst clears all computational state.',
        'L4.md': stimulus,
        'L7.md': scenario,
    }
    paths = []
    for name, text in docs.items():
        path = tmp_path / name
        path.write_text(text)
        paths.append(str(path))
    result = tmp_path / 'findings.json'
    proc = subprocess.run([sys.executable, str(SCRIPT), '--strict', '--json',
                           str(result), *paths], capture_output=True, text=True,
                          cwd=tmp_path)
    assert proc.returncode in (0, 1), proc.stderr
    return [row for row in json.loads(result.read_text())['findings']
            if row['code'] == 'corner-case-uncovered'
            and "item 'reset-during-operation'" in row['message']]


@pytest.mark.parametrize('stimulus,scenario', [
    ('Reset may be asserted at startup or during an active multiplication.', ''),
    ('', 'During reset, reset release, and reset assertion during calculation'),
    ('Reset may be asserted at startup or during an active multiplication.',
     'During reset, reset release, and reset assertion during calculation'),
    ('Reset can be applied during an ongoing computation.', ''),
])
def test_reset_during_named_work_is_covered(tmp_path, stimulus, scenario):
    assert not lint_reset(tmp_path, stimulus, scenario)


@pytest.mark.parametrize('stimulus,scenario', [
    ('Reset may be asserted at startup.', ''),
    ('Reset is synchronous and active-high.', ''),
    ('No reset assertion during calculation is specified.', ''),
    ('Reset assertion during calculation remains unspecified.', ''),
    ('Reset assertion during calculation is not documented.', ''),
    ('Not specified: reset assertion during calculation.', ''),
    ('Without reset assertion during calculation being specified.', ''),
    ('Multiplication starts after reset release.', ''),
    ('Reset may be asserted at startup.', 'Multiplication is active.'),
    ('Reset assertion at startup.', 'Data is sampled during calculation.'),
    ('Reset may be asserted at startup. Data is sampled during an active multiplication.', ''),
])
def test_startup_or_unrelated_work_does_not_cover_reset_during_work(tmp_path, stimulus, scenario):
    assert len(lint_reset(tmp_path, stimulus, scenario)) == 1
