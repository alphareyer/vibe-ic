"""Normal A1 enrichment preserves IDs while every current declaration is bound."""
from pathlib import Path
import json
import sys

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import execution_adapters_analog as analog
import execution_modes as em
import execution_policy as policy


def declarations(project):
    first = project / analog.DECLARATIONS[0]
    second = project / analog.DECLARATIONS[1]
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text(json.dumps({'blocks': [{'name': 'a', 'pins': ['vdd']}, {'name': 'b'}]}))
    second.write_text(json.dumps({'blocks': [{'name': 'a', 'spec': {'gain': 2}}, {'name': 'b', 'source': 'A1'}]}))
    return first, second


def test_metadata_enrichment_keeps_population_and_both_current_declaration_bytes(tmp_path):
    first, second = declarations(tmp_path)
    before = (first.read_bytes(), second.read_bytes())
    assert analog._blocks(tmp_path, first) == ('a', 'b')
    registry = em.Registry()
    analog.register_adapters(registry, project=tmp_path)
    inputs = policy._fixed_inputs(dict(project=tmp_path, registry=registry), 'A8')
    for name in analog.DECLARATIONS[:2]:
        assert inputs[name] == tmp_path / name
        assert em.digest(inputs[name]) == em.digest(tmp_path / name)
    assert registry.adapters('A8')[0].objective['declaration_sha256'] == em.digest(first)
    assert registry.adapters('A8')[0].objective['blocks'] == ['a', 'b']
    assert (first.read_bytes(), second.read_bytes()) == before


@pytest.mark.parametrize('rows', [
    [{'name': 'changed'}, {'name': 'b'}],
    [{'name': 'b'}, {'name': 'a'}],
    [{'name': 'a'}],
    [{'name': 'a'}, {'name': 'a'}],
    ['a', {'name': 'b'}],
    [{'name': 'a'}, {}],
])
def test_id_order_count_duplicates_or_malformed_row_refuse(tmp_path, rows):
    first, second = declarations(tmp_path)
    second.write_text(json.dumps({'blocks': rows}))
    with pytest.raises(em.Refusal, match='ANALOG_DECLARATION_CONFLICT'):
        analog._blocks(tmp_path, first)


def test_alternate_declaration_symlink_refuses(tmp_path):
    first, second = declarations(tmp_path)
    second.unlink()
    second.symlink_to(first)
    with pytest.raises(em.Refusal, match='ANALOG_DECLARATION_CONFLICT'):
        analog._blocks(tmp_path, first)


def test_declared_block_count_mismatch_refuses(tmp_path):
    first, second = declarations(tmp_path)
    second.write_text(json.dumps({'blocks': [{'name': 'a'}, {'name': 'b'}], 'block_count': 1}))
    with pytest.raises(em.Refusal, match='ANALOG_DECLARATION_CONFLICT'):
        analog._blocks(tmp_path, first)
