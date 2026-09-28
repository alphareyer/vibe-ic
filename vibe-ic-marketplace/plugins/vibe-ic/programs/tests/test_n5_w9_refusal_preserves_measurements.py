"""Wave 9: failures after a partial native STA sweep retain measured rows."""
import errno
import json
import re
from pathlib import Path

import pytest

import phase3_one_shot_runner as p3
from test_declared_process_sta_producer import scene  # noqa: F401
from test_n5_declared_process_sta_supply_ports_and_record import (
    _CENSUS_WITH_SUPPLY_PORTS, _def, _native_double,
)

_REAL_DOCKER_EXEC = p3._docker_exec


def _record(scene):
    return json.loads((scene['tmp_path'] /
        'reports/phase3/sta/declared_process_sta.json').read_text())


def _assert_finished(rec, ff_setup=-0.8):
    assert rec['status'] == 'REFUSED'
    for corner, setup in [('FF', ff_setup), ('SS', 1.25)]:
        for role, expected in [('setup', setup), ('hold', 0.40)]:
            row = rec['corners'][corner][role]
            assert (row['status'], row['wns_ns'], row['promoted']) == (
                'MEASURED', expected, False), row


@pytest.mark.parametrize('stop_rc', [p3._RC_STALLED, 124])
def test_real_dispatch_isolation_keeps_finished_corners(scene, monkeypatch, stop_rc):
    s = scene
    _def(s['tmp_path'])
    base = _native_double(_CENSUS_WITH_SUPPLY_PORTS)

    def supervisor(container, cmd, marker, **kw):
        tcl = Path(marker).read_text()
        out = Path(re.search(r'set _f \[open \{([^}]+)\}', tcl).group(1))
        if 'process=TT ===' in tcl:
            with out.open('a') as f:
                f.write('=== SETUP corner: process=TT ===\nworst slack max 9.99\n')
            return stop_rc, '', 'WATCHDOG_STALLED: fixture stopped TT'
        result = base(container, cmd, marker=marker, isolate=[out])
        if 'process=FF ===' in tcl:
            out.write_text(out.read_text().replace('worst slack max 1.25',
                                                   'worst slack max -0.80'))
        return result

    monkeypatch.setattr(p3._dwd, 'run_docker_supervised', supervisor)
    monkeypatch.setattr(p3, '_docker_exec', _REAL_DOCKER_EXEC)
    assert not s['emit']()
    rec = _record(s)
    _assert_finished(rec)
    att = rec['attempt_report']
    path = s['tmp_path'] / att['path']
    assert path.name.endswith('.timeout.partial') and path.is_file()
    assert att['sha256'] == p3._file_sha256(path).split(':', 1)[1]
    assert 'worst slack max -0.80' in path.read_text()
    assert att['native_logs'] and (s['tmp_path'] / att['native_logs'][0]).is_file()
    for role in ('setup', 'hold'):
        row = rec['corners']['TT'][role]
        assert row['status'] == 'NOT_MEASURED' and f'rc={stop_rc} at TT' in row['reason']


def test_first_corner_failure_before_report_open_has_corner_reasons(scene, monkeypatch):
    s = scene
    _def(s['tmp_path'])
    monkeypatch.setattr(p3, '_docker_exec', lambda *a, **kw: (1, 'Error: unreadable STA input', ''))
    assert not s['emit']()
    rec = _record(s)
    assert rec['attempt_report'] is not None
    assert rec['attempt_report']['sha256'] is None
    assert rec['attempt_report']['native_logs']
    assert 'rc=1 at FF' in rec['corners']['FF']['setup']['reason']
    for c in ('SS', 'TT'):
        assert rec['corners'][c]['setup']['reason'] == 'not run: sweep stopped at FF'


def test_header_write_failure_preserves_every_value(scene, monkeypatch):
    s = scene
    _def(s['tmp_path'])
    monkeypatch.setattr(p3, '_docker_exec', _native_double(_CENSUS_WITH_SUPPLY_PORTS))
    real_write = Path.write_text

    def fail_header(path, data, *args, **kwargs):
        if (path.name.startswith('sta_spef_based.rpt.attempt-')
                and not path.name.endswith('.population.json')):
            raise OSError(errno.ENOSPC, 'No space left on device')
        return real_write(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, 'write_text', fail_header)
    assert not s['emit']()
    rec = _record(s)
    assert rec['status'] == 'REFUSED' and 'disclose' in rec['reason']
    for c in ('FF', 'SS', 'TT'):
        for role, value in [('setup', 1.25), ('hold', 0.40)]:
            row = rec['corners'][c][role]
            assert (row['status'], row['wns_ns'], row['promoted']) == (
                'MEASURED', value, False)
            assert row['annotation_census'] == 'COMPLETE'


def test_later_script_write_failure_keeps_previous_corner(scene, monkeypatch):
    s = scene
    _def(s['tmp_path'])
    monkeypatch.setattr(p3, '_docker_exec', _native_double(_CENSUS_WITH_SUPPLY_PORTS))
    real_write = Path.write_text

    def fail_ss_script(path, data, *args, **kwargs):
        if path.name == 'sta_declared_ss.tcl':
            raise OSError(errno.ENOSPC, 'No space left on device')
        return real_write(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, 'write_text', fail_ss_script)
    assert not s['emit']()
    rec = _record(s)
    assert rec['corners']['FF']['setup']['wns_ns'] == 1.25
    assert rec['corners']['FF']['hold']['status'] == 'MEASURED'
    assert 'No space left on device' in rec['corners']['SS']['setup']['reason']
    assert rec['corners']['TT']['setup']['reason'] == 'not run: sweep stopped at SS'
