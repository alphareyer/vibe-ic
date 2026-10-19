"""Finite source declaration controls; no RTL, physical reports or native EDA."""
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest
import known_answer_vector as kav
import l24_signoff_requirements_extract as signoff

PROGRAMS = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / 'fixtures/phase1_local'
MMIO = 'The peripheral uses a memory-mapped register interface.'
PORT = {'name': 'payload', 'direction': 'input', 'width': 8,
        'evidence': 'input/docs/design.md', 'description': 'data input'}


def test_mmio_handoff_retains_actual_ports_and_the_owning_citation():
    record = kav.declared_mmio_interface({'design.md': MMIO}, [PORT])
    assert record['ports'] == [PORT]
    assert record['citation'] == {'source': 'input/docs/design.md', 'line': 1, 'quote': MMIO}
    transport = kav.resolve_transport({'interface_type': record['name'],
                                      'interface_types': [record]}, {})
    assert transport['ports'] == [PORT]
    assert transport['citation'] == record['citation']
    assert transport['author_handoff_required'] is True
    assert not {'status', 'verdict', 'driver', 'opcodes'} & set(transport)


@pytest.mark.parametrize('documents,ports', [
    ({'design.md': 'The peripheral has no stated transport.'}, [PORT]),
    ({'design.md': MMIO}, []),
    ({'design.md': MMIO + '\nThe block does not use a memory-mapped register interface.'}, [PORT]),
    ({'design.md': MMIO}, [{**PORT, 'evidence': 'input/docs/neighbor.md'}]),
    ({'design.md': MMIO, 'neighbor.md': MMIO}, [PORT]),
], ids=['absent', 'no_ports', 'contradictory', 'foreign_port_source', 'ambiguous_source'])
def test_unowned_or_unstated_mmio_transport_is_not_created(documents, ports):
    assert kav.declared_mmio_interface(documents, ports) is None


def test_existing_named_transport_stays_authoritative():
    original = {'interface_type': 'wishbone', 'interface_types': [{'name': 'wishbone'}]}
    before = copy.deepcopy(original)
    result = kav.resolve_transport(original, {})
    assert result['kind'] == 'wishbone'
    assert result['evidence'] == 'L9_INTEGRATION_SPEC.interface_type'
    assert original == before


def test_historical_mmio_does_not_reach_the_current_l10_census(tmp_path):
    text = (FIXTURES / 'neutral_mmio_transport/design.md').read_text()
    text = text.replace(
        'A register peripheral with a memory-mapped register interface using cs, we and address.',
        'Previously, the block used a memory-mapped register interface.')
    source = tmp_path / 'input/docs/design.md'
    source.parent.mkdir(parents=True)
    source.write_text(text)
    argv = [sys.executable, str(PROGRAMS / 'phase1_doc_one_shot_runner.py'),
            str(tmp_path), '--strict', '--ic-name', 'neutral_reg', '--pdk', 'sky130A']
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    (tmp_path / 'cli.stdout').write_text(result.stdout)
    (tmp_path / 'cli.stderr').write_text(result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    docs = tmp_path / 'phase1/generated_docs'
    l9 = json.loads((docs / 'L9_INTEGRATION_SPEC.json').read_text())
    l10 = json.loads((docs / 'L10_TEST_CASES.json').read_text())
    actual = {'interface_type': l9.get('interface_type'),
              'transport': l10['known_answer_vector_census']['transport']}
    print('HISTORICAL_MMIO_OBSERVATION ' + json.dumps(actual, sort_keys=True))
    assert actual['transport']['kind'] == 'undeclared', actual
    assert not l9.get('interface_types'), l9.get('interface_types')


def test_comparison_example_mmio_does_not_reach_the_current_l10_census(tmp_path):
    text = (FIXTURES / 'neutral_mmio_transport/design.md').read_text()
    text = text.replace(
        'A register peripheral with a memory-mapped register interface using cs, we and address.',
        'For comparison, the host example uses a memory-mapped register interface.')
    source = tmp_path / 'input/docs/design.md'
    source.parent.mkdir(parents=True)
    source.write_text(text)
    argv = [sys.executable, str(PROGRAMS / 'phase1_doc_one_shot_runner.py'),
            str(tmp_path), '--strict', '--ic-name', 'neutral_reg', '--pdk', 'sky130A']
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    (tmp_path / 'cli.stdout').write_text(result.stdout)
    (tmp_path / 'cli.stderr').write_text(result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    docs = tmp_path / 'phase1/generated_docs'
    l9 = json.loads((docs / 'L9_INTEGRATION_SPEC.json').read_text())
    l10 = json.loads((docs / 'L10_TEST_CASES.json').read_text())
    actual = {'interface_type': l9.get('interface_type'),
              'transport': l10['known_answer_vector_census']['transport']}
    print('COMPARISON_MMIO_OBSERVATION ' + json.dumps(actual, sort_keys=True))
    assert actual['transport']['kind'] == 'undeclared', actual
    assert not l9.get('interface_types'), l9.get('interface_types')


def rows(tmp_path, text):
    path = tmp_path / 'input/docs/spec.md'
    path.parent.mkdir(parents=True)
    path.write_text(text)
    return {row['check']: row for row in
            signoff.extract_signoff_requirements(tmp_path)['signoff_requirements']}


def test_grouped_electrical_targets_keep_shared_requirement_and_caveat(tmp_path):
    text = (FIXTURES / 'neutral_electrical_signoff/signoff.md').read_text()
    actual = rows(tmp_path, text)
    for check in ['max_slew', 'max_capacitance', 'max_fanout']:
        row = actual[check]
        assert row['stated'] is True
        assert row['threshold'] == {'value': 0.0, 'unit': 'violation'}
        assert 'subject to the measured reference baseline' in row['citation']['clause']
        assert row['citation']['document'] == 'input/docs/spec.md'
        assert row['citation']['text'] == text.splitlines()[row['citation']['line'] - 1]
        assert not {'status', 'verdict', 'result', 'passed', 'measured'} & set(row)
    assert actual['DRC']['requirement'] == 'clean'


def test_one_electrical_target_does_not_invent_the_other_two(tmp_path):
    actual = rows(tmp_path, '| Check | Requirement |\n|---|---|\n| Max slew | 0 violations |\n')
    assert actual['max_slew']['stated'] is True
    assert actual['max_capacitance']['stated'] is False
    assert actual['max_fanout']['stated'] is False


def test_undeclared_electrical_checks_stay_unspecified(tmp_path):
    actual = rows(tmp_path, 'DRC clean is required.\n')
    for check in ['max_slew', 'max_capacitance', 'max_fanout']:
        assert actual[check]['stated'] is False
        assert actual[check]['threshold'] is None


def test_conflicting_electrical_targets_do_not_choose_a_zero_winner(tmp_path):
    actual = rows(tmp_path, '| Max slew | 0 violations |\n| Max slew | 2 violations |\n')
    assert actual['max_slew']['stated'] is True
    assert actual['max_slew']['declaration_conflict'] is True
    assert actual['max_slew']['threshold'] is None
    assert actual['max_slew']['requirement'] is None
    assert actual['max_slew']['occurrences'] == 2


def test_a_generic_timing_report_cannot_answer_electrical_checks():
    for check in ['max_slew', 'max_capacitance', 'max_fanout']:
        tokens = signoff.report_tokens_for(check)
        assert tokens
        assert not signoff._path_names_token('reports/sta/summary.json', tokens)
        assert not signoff._path_names_token('reports/timing/signoff.json', tokens)


def test_a_denied_electrical_target_does_not_become_affirmative_zero(tmp_path):
    # Older vocabulary omits this check entirely; omission is not an affirmative target.
    actual = rows(tmp_path, 'No max slew 0 violations are required.\n').get(
        'max_slew', {'stated': False, 'threshold': None})
    print('DENIED_ELECTRICAL_OBSERVATION ' + json.dumps(actual, sort_keys=True))
    assert actual['stated'] is False, actual
    assert actual['threshold'] is None
