"""Public table-role input through the actual strict interface consumer.

The RTL is a nonfunctional port-extraction fixture, not a peripheral model.
No simulator, hidden reference, or EDA tool is used.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest


PROGRAMS = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / 'fixtures/iface_table_roles'
PROMPT = (FIXTURE / 'prompt.md').read_text()
RTL = (FIXTURE / 'neutral_reg.v').read_text()


def _run(root, prompt=PROMPT, rtl=RTL):
    pp, rp, jp = root / 'prompt.md', root / 'neutral_reg.v', root / 'report.json'
    pp.write_text(prompt)
    rp.write_text(rtl)
    argv = [sys.executable, str(PROGRAMS / 'iface_conformance_v2.py'),
            '--id', 'neutral_reg', '--prompt', str(pp), '--rtl', str(rp),
            '--strict', '--json', str(jp)]
    process = subprocess.run(argv, capture_output=True, text=True)
    (root / 'argv.json').write_text(json.dumps(argv) + '\n')
    (root / 'stdout.log').write_text(process.stdout)
    (root / 'stderr.log').write_text(process.stderr)
    (root / 'rc.txt').write_text(str(process.returncode) + '\n')
    report = json.loads(jp.read_text())
    assert report['files_read'] == [str(pp), str(rp)]
    return process, report


def _columns(language, direction_first):
    lines = PROMPT.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith('| 訊號 |'))
    stop = next(i for i in range(start + 2, len(lines)) if not lines[i].startswith('|'))
    headers = ['Signal', 'Width', 'Direction', 'Description'] if language == 'en' else [
        '訊號', '寬度', '方向', '描述']
    order = [0, 2, 1, 3] if direction_first else [0, 1, 2, 3]
    lines[start] = '| ' + ' | '.join(headers[i] for i in order) + ' |'
    for i in range(start + 2, stop):
        cells = [cell.strip() for cell in lines[i].strip('|').split('|')]
        lines[i] = '| ' + ' | '.join(cells[k] for k in order) + ' |'
    return '\n'.join(lines) + '\n'


def test_public_register_table_is_not_an_interface(tmp_path):
    process, report = _run(tmp_path)
    assert set(report['rtl_ports']) == {
        'clk', 'reset_n', 'cs', 'we', 'address', 'write_data', 'read_data', 'error'}
    assert process.returncode == 0, report
    assert report['conformant'] is True, report
    assert report['findings'] == []


@pytest.mark.parametrize('language,direction_first', [
    ('zh', False), ('en', False), ('zh', True), ('en', True),
], ids=['zh-width-first', 'en-width-first', 'zh-direction-first', 'en-direction-first'])
def test_width_column_order_keeps_missing_port_blocking(tmp_path, language, direction_first):
    prompt = _columns(language, direction_first)
    rtl = RTL.replace('input wire [31:0] write_data,', '')
    process, report = _run(tmp_path, prompt, rtl)
    assert 'write_data' not in report['rtl_ports']
    assert process.returncode == 1, report
    missing = [f for f in report['findings'] if f['kind'] == 'MISSING-PORT'
               and "'write_data'" in f['message']]
    assert len(missing) == 1, report
    assert missing[0]['block_eligible'] is True, missing
    assert missing[0]['advisory_note'] == '', missing
    assert report['blocking_findings'] == 1, report


@pytest.mark.parametrize('present', [True, False], ids=['present', 'missing'])
def test_same_named_real_port_is_not_hidden(tmp_path, present):
    marker = '| `error` |'
    lines = PROMPT.splitlines()
    position = next(i for i, line in enumerate(lines) if line.startswith(marker))
    lines.insert(position + 1, '| `INFO` | 1 | input | Actual external input |')
    prompt = '\n'.join(lines) + '\n'
    rtl = RTL.replace('input wire clk,', 'input wire INFO,input wire clk,') if present else RTL
    process, report = _run(tmp_path, prompt, rtl)
    assert process.returncode == (0 if present else 1), report
    if present:
        assert report['findings'] == [], report
    else:
        assert report['blocking_findings'] == 1, report
        assert len(report['findings']) == 1, report
        finding = report['findings'][0]
        assert finding['kind'] == 'MISSING-PORT'
        assert "'INFO'" in finding['message']
        assert finding['block_eligible'] is True


def test_width_first_direction_mismatch_is_blocking(tmp_path):
    rtl = RTL.replace('input wire [31:0] write_data,', 'output wire [31:0] write_data,')
    process, report = _run(tmp_path, PROMPT, rtl)
    assert process.returncode == 1, report
    mismatches = [f for f in report['findings'] if f['kind'] == 'PORT-DIRECTION'
                  and "'write_data'" in f['message']]
    assert len(mismatches) == 1, report
    assert mismatches[0]['block_eligible'] is True
    assert report['blocking_findings'] == 1, report


def test_qualified_direction_keeps_existing_structural_provenance(tmp_path):
    prompt = ('| Signal | Direction | Width |\n|---|---|---|\n'
              '| `enable` | input (active-high) | 1 |\n')
    process, report = _run(tmp_path, prompt,
                           'module neutral_reg(output wire enable); endmodule\n')
    assert process.returncode == 1, report
    assert len(report['findings']) == 1, report
    finding = report['findings'][0]
    assert finding['kind'] == 'PORT-DIRECTION', finding
    assert finding['block_eligible'] is True, finding
    assert report['blocking_findings'] == 1, report
