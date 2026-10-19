"""Explicit reset phase tables must reach the normal consuming layers."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))
import phase1_doc_one_shot_runner as D

FIXTURE = Path(__file__).parent / 'fixtures/phase1_local/neutral_reset_domain_phases/reset.md'


def _emit(project, text):
    source = project / 'input/docs/reset.md'
    source.parent.mkdir(parents=True)
    source.write_text(text)
    sdc = project / 'input/constraints/clocks.sdc'
    sdc.parent.mkdir(parents=True)
    sdc.write_text('create_clock -name main -period 20 [get_ports clk_i]\n'
                   'create_clock -name aux -period 32 [get_ports clk_aux_i]\n')
    extracted = {'reset.md': text}
    D.gen_l1_datasheet(project, extracted)
    D.gen_l8_timing_waveform(project, extracted)
    D.gen_l8_timing_waveform_doc(project, extracted)
    D.gen_l9_integration_spec(project, extracted, {})
    D._post_emit_typed_clock_domains(project)
    D._post_emit_sdc_constraints(project)
    D._post_emit_mirror_clock_resets_to_l9_v1_6_311(project)
    D._post_emit_l8_clock_reset_waveform(project)
    docs = {name: json.loads((project / 'phase1/generated_docs' / name).read_text())
            for name in ['L8_RTL_CONSTANTS.json', 'L8_TIMING_WAVEFORM.json',
                         'L9_INTEGRATION_SPEC.json']}
    print('RESET_DOMAIN_OBSERVATION ' + json.dumps(docs, sort_keys=True))
    return docs


def test_explicit_per_domain_assertion_and_release_reach_normal_consumers(tmp_path):
    docs = _emit(tmp_path, FIXTURE.read_text())
    l9 = docs['L9_INTEGRATION_SPEC.json']
    reset = next(r for r in l9['reset_domains'] if r['name'] == 'rst_n')
    assert reset['sync'] == 'asynchronous'
    assert reset['polarity'] == 'active_low'
    expected = {clock: {'clock': clock, 'assertion': 'asynchronous',
                       'release': 'synchronous', 'release_clock': clock,
                       'release_stages': 2, 'polarity': 'active_low'}
                for clock in ['clk_i', 'clk_aux_i']}
    actual = {row['clock']: row for row in reset['domain_bindings']}
    for clock, fields in expected.items():
        assert {key: actual[clock][key] for key in fields} == fields
        assert actual[clock]['basis'][0]['file'] == 'input/docs/reset.md'
        assert actual[clock]['basis'][0]['quote'] in FIXTURE.read_text().splitlines()
    for layer in docs.values():
        domains = {row['name']: row for row in layer.get('clock_domains', [])}
        for clock, fields in expected.items():
            assert domains[clock]['reset_strategy'] == 'asynchronous assertion; synchronous release'
            binding = domains[clock]['reset_bindings'][0]
            assert binding['reset'] == 'rst_n'
            assert {key: binding[key] for key in fields} == fields
    waveform = docs['L8_TIMING_WAVEFORM.json']['clock_and_reset_waveform']
    projected = next(row for row in waveform['resets'] if row['name'] == 'rst_n')
    assert projected['domain_bindings'] == reset['domain_bindings']


@pytest.mark.parametrize('change,field,expected', [
    ('missing_stages', 'release_stages', 'unknown'),
    ('missing_release_clock', 'release_clock', 'unknown'),
    ('conflicting_stages', 'release_stages', 'unknown'),
    ('different_domain_stages', 'release_stages', 3),
])
def test_phase_values_stay_bound_to_the_explicit_domain(tmp_path, change, field, expected):
    text = FIXTURE.read_text()
    row = '| clk_aux_i | rst_n | active-low | asynchronous | synchronous to clk_aux_i | 2 |'
    replacement = row
    if change == 'missing_stages':
        replacement = row.replace('| 2 |', '| unspecified |')
    elif change == 'missing_release_clock':
        replacement = row.replace('synchronous to clk_aux_i', 'synchronous')
    elif change == 'conflicting_stages':
        replacement = row + '\n' + row.replace('| 2 |', '| 3 |')
    elif change == 'different_domain_stages':
        replacement = row.replace('| 2 |', '| 3 |')
    docs = _emit(tmp_path, text.replace(row, replacement))
    reset = docs['L9_INTEGRATION_SPEC.json']['reset_domains'][0]
    rows = {binding['clock']: binding for binding in reset['domain_bindings']}
    assert rows['clk_aux_i'][field] == expected
    assert rows['clk_i']['release_stages'] == 2
    assert rows['clk_i']['release_clock'] == 'clk_i'
    if change == 'conflicting_stages':
        assert len(rows['clk_aux_i']['basis']) == 2


def test_table_for_another_reset_cannot_supply_this_reset_phases():
    import _reset_input_semantics as R
    text = FIXTURE.read_text().replace('| rst_n | active-low |', '| other_reset_n | active-low |')
    assert R.reset_domain_bindings('rst_n', {'reset.md': text}) == []
    assert R.reset_semantics('rst_n', text) == {'polarity': 'active_low'}


def test_historical_phase_row_does_not_replace_current_reset_declaration():
    import _reset_input_semantics as R
    text = ('| Clock | Reset | Polarity | Assertion | Release | Release synchronizer stages | Note |\n'
            '|---|---|---|---|---|---:|---|\n'
            '| clk_i | rst_n | active-low | asynchronous | synchronous to clk_i | 2 | Historical configuration only |\n'
            'rst_n is an active-high synchronous reset.\n')
    assert R.reset_semantics('rst_n', text) == {'sync': 'synchronous', 'polarity': 'active_high'}
    assert R.reset_domain_bindings('rst_n', {'reset.md': text}) == []
