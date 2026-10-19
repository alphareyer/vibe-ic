"""Three independent public document-only contracts through the ordinary CLI."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / 'fixtures/phase1_local'


def _run_public(project, fixture, filename):
    source = project / 'input/docs' / filename
    source.parent.mkdir(parents=True)
    shutil.copyfile(FIXTURES / fixture / filename, source)
    argv = [sys.executable, str(PROGRAMS / 'phase1_doc_one_shot_runner.py'),
            str(project), '--strict', '--ic-name', 'neutral_reg', '--pdk', 'sky130A']
    run = subprocess.run(argv, capture_output=True, text=True, check=False)
    (project / 'cli.stdout').write_text(run.stdout)
    (project / 'cli.stderr').write_text(run.stderr)
    print('PUBLIC_CLI_PROCESS ' + json.dumps({'argv': argv, 'returncode': run.returncode}))
    assert run.returncode == 0, run.stdout + run.stderr
    return project / 'phase1/generated_docs'


def test_public_mmio_transport_reaches_the_real_l10_consumer(tmp_path):
    docs = _run_public(tmp_path, 'neutral_mmio_transport', 'design.md')
    l9 = json.loads((docs / 'L9_INTEGRATION_SPEC.json').read_text())
    l10 = json.loads((docs / 'L10_TEST_CASES.json').read_text())
    print('PUBLIC_MMIO_OBSERVATION ' + json.dumps({
        'interface_type': l9.get('interface_type'),
        'interface_types': l9.get('interface_types'),
        'transport': l10['known_answer_vector_census']['transport']}, sort_keys=True))
    transport = l10['known_answer_vector_census']['transport']
    assert transport['kind'] != 'undeclared', transport
    assert l9.get('interface_type') or l9.get('interface_types'), 'source MMIO transport lost'


def test_public_reserved_bit_rows_remain_owned_by_each_register(tmp_path):
    docs = _run_public(tmp_path, 'neutral_reserved_bitrows', 'L5_register_map.md')
    l4 = json.loads((docs / 'L4_REGMAP.json').read_text())
    regs = {row['name']: row for row in l4['registers']}
    print('PUBLIC_RESERVED_OBSERVATION ' + json.dumps(regs, sort_keys=True))
    for name in ['CTRL', 'STATUS']:
        fields = regs[name].get('fields', [])
        assert {row['field_name'] for row in fields} == {
            'START' if name == 'CTRL' else 'READY', 'reserved'}
        reserved = [row for row in fields if
                    'reserved' in str(row.get('field_name', '')).lower()
                    or row.get('role') == 'reserved']
        assert reserved, (name, fields)
        assert any(str(row.get('bits', '')).replace(' ', '') in
                   ['1-31', '31:1', '31-1', '1:31'] for row in reserved), reserved


def test_public_electrical_targets_reach_the_typed_l24_checklist(tmp_path):
    docs = _run_public(tmp_path, 'neutral_electrical_signoff', 'signoff.md')
    rows = json.loads((docs / 'L24_SIGNOFF.json').read_text())['fields']['signoff_requirements']
    stated = [row for row in rows if row.get('stated')]
    print('PUBLIC_ELECTRICAL_OBSERVATION ' + json.dumps(stated, sort_keys=True))
    assert any(row.get('check') == 'DRC' for row in stated)
    assert {term for term in ['slew', 'cap', 'fanout'] if
            any(term in str(row.get('check', '')).lower() for row in stated)} == {
                'slew', 'cap', 'fanout'}
    for term in ['slew', 'cap', 'fanout']:
        assert any(term in str(row.get('check', '')).lower() for row in stated), (term, stated)
