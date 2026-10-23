"""Complete existing arithmetic declarations without inventing encoding choices."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import arith_declaration_emit as emitter
from arith_oracle_tb_gen import read_declared_conventions
from test_arith_declaration_emit_equals_separator import _make_run_dir

EQUIVALENT = "N-bit unsigned and signed two's-complement operands have identical modulo output bit patterns."


def setup_project(tmp_path, prose, choice):
    root = _make_run_dir(tmp_path, '// Algorithm: serial_product\n')
    (root / 'phase1/generated_docs/L2_FRS.json').write_text(json.dumps({'content': prose}))
    prior = {'integer_encoding': choice, 'latency_cycles_origin': 'pre_nba_sample_offset',
             'transaction_rearm': 'clocked_reset', 'rearm_reset_cycles': 1,
             'verification_stimuli': {'arithmetic_scenarios': {'consecutive_transactions': {'transaction_count': 6}}}}
    target = root / 'plugin_output/declaration.json'
    target.parent.mkdir()
    target.write_text(json.dumps(prior))
    return root, target, prior


@pytest.mark.parametrize('choice', ['unsigned', 'signed_2c'])
def test_complete_equivalent_encoding_preserves_producer_contract(tmp_path, choice):
    root, target, prior = setup_project(tmp_path, EQUIVALENT, choice)
    assert emitter.main([str(root)]) == 0
    result = json.loads(target.read_text())
    assert result['integer_encoding'] == choice
    assert result['size_param'] == 13
    assert result['verification_stimuli'] == prior['verification_stimuli']
    consumer = read_declared_conventions(root, [])
    assert consumer['signed'] is (choice == 'signed_2c')
    assert consumer['settled_latency'] == 7  # never reinterpret as pre-NBA 6
    assert consumer['transaction_rearm'] == 'clocked_reset'
    assert consumer['rearm_reset_cycles'] == 1


@pytest.mark.parametrize('prose,choice', [
    ('Unsigned or signed operands may be considered.', 'unsigned'),
    ('N-bit unsigned and signed two\'s-complement operands do not have identical modulo output bit patterns.', 'unsigned'),
    ('It is not true that ' + EQUIVALENT, 'unsigned'),
    (EQUIVALENT, None),
    (EQUIVALENT, 'signed_1c'),
    ('operands are signed_2c', 'unsigned'),
])
def test_ambiguous_missing_or_conflicting_encoding_preserves_previous_file(tmp_path, prose, choice):
    root, target, prior = setup_project(tmp_path, prose, choice)
    before = target.read_bytes()
    assert emitter.main([str(root)]) == 1
    assert target.read_bytes() == before
