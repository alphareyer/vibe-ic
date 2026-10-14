#!/usr/bin/env python3
"""Tests for spec_named_signal_detect — the named-signal-preservation advisory."""
import importlib.util
import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROGRAMS))
_spec = importlib.util.spec_from_file_location(
    "spec_named_signal_detect", _PROGRAMS / "spec_named_signal_detect.py")
_M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_M)
detect = _M.detect_named_signals


def test_fires_on_backtick_named_signals():
    # interrupt_controller shape: TB peeks internal `interrupt_idx`
    p = ("The controller drives `cpu_interrupt` and clears the serviced IRQ on "
         "`cpu_ack`. Internally, `interrupt_idx` holds the current index and "
         "`interrupt_mask` gates requests.")
    r = detect(p)
    assert r["has_named_signals"] is True
    assert "interrupt_idx" in r["named_signals"]
    assert r["requirement"] and "no child object" in r["requirement"]


def test_includes_internal_signals_not_just_ports():
    p = ("Compute `parity` from the data word. The `data_reg` shift register and "
         "the `bit_counter` track reception.")
    r = detect(p)
    assert set(r["named_signals"]) >= {"parity", "data_reg", "bit_counter"}


def test_keywords_and_clk_reset_excluded():
    p = ("A `module` with `input` `clk` and `reset`, plus `data_valid`, "
         "`frame_start`, `byte_count`.")
    r = detect(p)
    for kw in ("module", "input", "clk", "reset"):
        assert kw not in r["named_signals"]
    assert set(r["named_signals"]) >= {"data_valid", "frame_start", "byte_count"}


def test_fewer_than_three_does_not_fire():
    r = detect("Drive `foo` and `bar`.")
    assert r["has_named_signals"] is False
    assert r["requirement"] is None


def test_no_backticks_does_not_fire():
    r = detect("Design an 8-bit counter with synchronous reset and enable.")
    assert r["has_named_signals"] is False


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))


MIXED_ROLES = """# Build libraries
| Library | Use |
| `neutral_cells_a` | mapping |
| `neutral_cells_b` | fallback |

# Declaration metadata
| Field | Type | Meaning |
| `serial_order` | string | bit traversal |
| `reset_level` | string | signal polarity description |

# Parameters
| Name | Type | Value |
| `WINDOW_LIMIT` | integer | 16 |

# Timing commands
SDC command `set_case_analysis` constrains a mode.

# Floorplan settings
| Setting | Value |
| `CORE_FILL_LIMIT` | 0.4 |

# RTL interface
| Port | Direction | Width |
| `ingress_valid` | input | 1 |
| `egress_ready` | output | 1 |

# Internal signals
The internal register `stage_cursor` holds the current position.
The counter `beat_remaining` tracks pending data.

# Notes
Review `catalog_entry_7` with the expert.
"""


def test_mixed_roles_keep_ports_and_internal_names_without_metadata():
    r = detect(MIXED_ROLES)
    assert set(r['named_signals']) == {
        'ingress_valid', 'egress_ready', 'stage_cursor', 'beat_remaining'}
    assert r['has_named_signals'] is True
    for name in r['named_signals']:
        evidence = [e for e in r['signal_evidence'] if e['name'] == name]
        assert evidence and all(name in e['context'] and e['line'] > 0 for e in evidence)
    roles = {e['name']: e['role'] for e in r['non_signal_identifiers']}
    assert roles == {'neutral_cells_a': 'library', 'neutral_cells_b': 'library',
                     'serial_order': 'metadata', 'reset_level': 'metadata',
                     'WINDOW_LIMIT': 'parameter', 'set_case_analysis': 'command',
                     'CORE_FILL_LIMIT': 'setting'}
    assert [e['name'] for e in r['ambiguous_identifiers']] == ['catalog_entry_7']


def test_nonsignal_document_does_not_request_rtl_declarations():
    prompt = MIXED_ROLES.split('# RTL interface')[0]
    r = detect(prompt)
    assert r['named_signals'] == []
    assert r['has_named_signals'] is False
    assert r['requirement'] is None


def test_same_spelling_can_have_distinct_source_roles():
    prompt = ('Library `stage_cursor` is selected for mapping.\n'
              'The internal register `stage_cursor` holds the index.\n'
              'Output port `ingress_valid` and internal flag `egress_ready` signal readiness.')
    r = detect(prompt)
    assert r['named_signals'] == ['stage_cursor', 'ingress_valid', 'egress_ready']
    assert any(e['name'] == 'stage_cursor' and e['role'] == 'library'
               for e in r['non_signal_identifiers'])
    assert any(e['name'] == 'stage_cursor' and e['line'] == 2
               for e in r['signal_evidence'])


def test_backticks_alone_leave_role_for_expert_review():
    r = detect('Consider `choice_a`, `choice_b`, and `choice_c`.')
    assert r['has_named_signals'] is False
    assert r['named_signals'] == []
    assert r['requirement'] is None
    assert [e['name'] for e in r['ambiguous_identifiers']] == [
        'choice_a', 'choice_b', 'choice_c']


def test_explicit_denial_does_not_become_signal_authority():
    r = detect('`choice_a` is not a signal. `choice_b` is not a register. '
               '`choice_c` is never an RTL net.')
    assert r['named_signals'] == []
    assert r['has_named_signals'] is False
    assert r['requirement'] is None
    assert {e['role'] for e in r['non_signal_identifiers']} == {'non_signal'}


def test_conflicting_local_roles_do_not_inherit_signal_section_authority():
    r = detect('# Internal signals\nparameter `choice_a` signal\n'
               'parameter `choice_b` signal\nparameter `choice_c` signal')
    assert r['named_signals'] == []
    assert r['has_named_signals'] is False
    assert r['requirement'] is None
    assert [e['name'] for e in r['ambiguous_identifiers']] == [
        'choice_a', 'choice_b', 'choice_c']


def test_explicit_short_and_uppercase_signals_keep_their_source_names():
    r = detect('Input port `a` supplies data. Output port `Q` reports a bit. '
               'Internal register `HELD` stores the sampled value.')
    assert r['named_signals'] == ['a', 'Q', 'HELD']
    assert r['has_named_signals'] is True
    assert len(r['signal_evidence']) == 3


import pytest


@pytest.mark.parametrize('prompt', [
    '# Not RTL signals\n`choice_a`, `choice_b`, and `choice_c`.\n',
    '| Not RTL signals | Use |\n'
    '| `choice_a` | bookkeeping |\n'
    '| `choice_b` | bookkeeping |\n'
    '| `choice_c` | bookkeeping |\n',
], ids=['heading', 'table'])
def test_negated_context_does_not_request_signals_in_rtl_handoff(tmp_path, prompt):
    import json
    import ic_expert_backup_pack as pack

    detected = detect(prompt)
    handoff = pack.assemble(prompt, None, None, [], [], tmp_path,
                            output_target='rtl.sv')
    saved = json.loads((tmp_path / 'ic_expert_agent_handoff.json').read_text())
    advice = [row for row in saved['spec_requirements']
              if row['kind'] == 'preserve_named_signals']
    observed = {'prompt': prompt, 'detected': detected, 'rtl_handoff_advice': advice}
    (tmp_path / 'observed.json').write_text(json.dumps(observed, indent=2) + '\n')
    print(json.dumps(observed, sort_keys=True))

    assert saved == handoff
    assert handoff['output_target'] == 'rtl.sv'
    assert detected['named_signals'] == []
    assert detected['has_named_signals'] is False
    assert detected['requirement'] is None
    assert detected['signal_evidence'] == []
    assert [row['name'] for row in detected['non_signal_identifiers']] == [
        'choice_a', 'choice_b', 'choice_c']
    assert {row['role'] for row in detected['non_signal_identifiers']} == {'non_signal'}
    assert advice == []


# Public issue #3122 clauses, prefixed with authored interface ports. These
# reproduce the source pattern only; the reporter-held design is not replayed.
PUBLIC_CLAUSE_CONTROLS = [
    {
        'id': 'module-no-registers',
        'clause': '`serial_unit` has no SW-visible registers',
        'signals': ['x', 'y'],
        'roles': {'serial_unit': 'non_signal'},
    },
    {
        'id': 'cell-area-gate',
        'clause': 'The cell area gate is carried in `cell_area_gate`',
        'signals': ['x', 'y'],
        'roles': {'cell_area_gate': 'metadata'},
    },
    {
        'id': 'both-public-clauses',
        'clause': ('`serial_unit` has no SW-visible registers\n'
                   'The cell area gate is carried in `cell_area_gate`'),
        'signals': ['x', 'y'],
        'roles': {'serial_unit': 'non_signal', 'cell_area_gate': 'metadata'},
    },
    {
        'id': 'real-internal-register',
        'clause': 'Internal register `count` tracks the current state.',
        'signals': ['x', 'y', 'count'],
        'roles': {},
    },
    {
        'id': 'public-clauses-with-register',
        'clause': ('`serial_unit` has no SW-visible registers\n'
                   'The cell area gate is carried in `cell_area_gate`\n'
                   'Internal register `count` tracks the current state.'),
        'signals': ['x', 'y', 'count'],
        'roles': {'serial_unit': 'non_signal', 'cell_area_gate': 'metadata'},
    },
    {
        'id': 'same-spellings-real-signals',
        'clause': ('Internal register `serial_unit` tracks the current state.\n'
                   'Internal signal `cell_area_gate` gates requests.'),
        'signals': ['x', 'y', 'serial_unit', 'cell_area_gate'],
        'roles': {},
    },
    {
        'id': 'clause-local-denial-and-register',
        'clause': ('`serial_unit` has no SW-visible registers; '
                   'internal register `count` tracks the current state. '
                   'The cell area gate is carried in `cell_area_gate`.'),
        'signals': ['x', 'y', 'count'],
        'roles': {'serial_unit': 'non_signal', 'cell_area_gate': 'metadata'},
    },
    {
        'id': 'generic-no-registers',
        'clause': ('`storage_block` has no registers; '
                   'internal register `count` tracks the current state.'),
        'signals': ['x', 'y', 'count'],
        'roles': {'storage_block': 'non_signal'},
    },
]


@pytest.mark.parametrize('case', PUBLIC_CLAUSE_CONTROLS,
                         ids=[c['id'] for c in PUBLIC_CLAUSE_CONTROLS])
@pytest.mark.parametrize('surface', ['detector', 'detector-cli', 'rtl-pack', 'phase1-pack'])
def test_public_clause_roles_through_normal_consumers(tmp_path, case, surface):
    import json
    import os
    import subprocess

    prompt = 'Input port `x`.\nOutput port `y`.\n' + case['clause']
    prompt_file = tmp_path / 'public_input.md'
    prompt_file.write_text(prompt)
    advice = []
    observed = {'surface': surface, 'case': case['id'], 'prompt': prompt}
    if surface == 'detector-cli':
        argv = [sys.executable, str(_PROGRAMS / 'spec_named_signal_detect.py'),
                '--prompt', '@' + str(prompt_file)]
        proc = subprocess.run(argv, capture_output=True, text=True, check=False)
        observed.update(argv=argv, returncode=proc.returncode,
                        stdout=proc.stdout, stderr=proc.stderr)
        assert proc.returncode == 0, observed
        detected = json.loads(proc.stdout)
    else:
        detected = detect(prompt)
    observed['detected'] = detected
    if surface in {'rtl-pack', 'phase1-pack'}:
        dest = tmp_path / 'pack'
        if surface == 'rtl-pack':
            import ic_expert_backup_pack as pack
            handoff = pack.assemble(prompt, None, None, [], [], dest,
                                    output_target='rtl.sv')
        else:
            import phase1_expert_parse_track as track
            status = track.ai_subtrack(tmp_path, prompt, dest)
            observed['phase1_status'] = status
            assert status['status'] == 'HANDOFF_EMITTED', status
            handoff = status['handoff']
        saved = json.loads((dest / 'ic_expert_agent_handoff.json').read_text())
        assert saved == handoff
        if surface == 'phase1-pack':
            assert (dest / handoff['read_design_input_from']).read_text() == prompt
        advice = [r for r in saved['spec_requirements']
                  if r['kind'] == 'preserve_named_signals']
        observed.update(handoff=saved, advice=advice)

    # Retain actual values before any contract assertion, including RED arms.
    raw = json.dumps(observed, indent=2) + '\n'
    (tmp_path / 'observed.json').write_text(raw)
    artifact_dir = os.environ.get('R72_ARTIFACT_DIR')
    if artifact_dir:
        dest = Path(artifact_dir) / case['id'] / surface
        dest.mkdir(parents=True, exist_ok=True)
        (dest / 'observed.json').write_text(raw)
    print(raw)

    expected = case['signals']
    fires = len(expected) >= 3
    if surface in {'rtl-pack', 'phase1-pack'}:
        assert [r['named_signals'] for r in advice] == ([expected] if fires else [])
        if fires:
            assert advice[0]['signal_evidence'] == detected['signal_evidence']
            assert advice[0]['non_signal_identifiers'] == detected['non_signal_identifiers']
            text = advice[0]['requirement']
            if surface == 'rtl-pack':
                assert 'Declare EACH' in text
            else:
                assert 'Declare EACH' not in text
                assert 'l_doc_expectations.json' in text
            assert all(name not in text for name in case['roles'])
    assert detected['named_signals'] == expected
    assert detected['has_named_signals'] is fires
    assert bool(detected['requirement']) is fires
    assert {r['name']: r['role'] for r in detected['non_signal_identifiers']} == case['roles']
    assert [r['name'] for r in detected['signal_evidence']] == expected
    assert detected['ambiguous_identifiers'] == []
    for row in detected['signal_evidence']:
        assert row['line'] > 0 and row['name'] in row['context']


# Verbatim public multilingual INPUT; this epoch is separate from the older
# public-clause controls above and from the reporter's unavailable script.
import json as _json

LOCALIZED_PUBLIC_INPUT = _json.loads(
    (Path(__file__).parent / 'fixtures' / 'issue3122_localized_role.json').read_text())
LOCALIZED_CLAUSES = {c['id']: c for c in LOCALIZED_PUBLIC_INPUT['clauses']}
LOCALIZED_ROLES = {
    'localized_no_sw_registers': 'non_signal',
    'localized_no_analog_content': 'design',
    'area_metadata_with_source_citation': 'metadata',
}


def _localized_role_observation(tmp_path, case_id, prompt, surface):
    """Use the existing detector/pack/ai_subtrack consumer paths and retain values."""
    import hashlib
    import os

    dest = tmp_path / surface
    dest.mkdir(parents=True)
    (dest / 'public_input.md').write_text(prompt)
    detected = detect(prompt)
    observed = {'case': case_id, 'surface': surface, 'prompt': prompt,
                'input_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
                'detected': detected, 'advice': []}
    if surface in {'rtl-pack', 'phase1-pack'}:
        pack_dir = dest / 'pack'
        if surface == 'rtl-pack':
            import ic_expert_backup_pack as pack
            handoff = pack.assemble(prompt, None, None, [], [], pack_dir,
                                    output_target='rtl.sv')
        else:
            import phase1_expert_parse_track as track
            status = track.ai_subtrack(dest, prompt, pack_dir)
            observed['phase1_status'] = status
            handoff = status.get('handoff')
        saved_path = pack_dir / 'ic_expert_agent_handoff.json'
        saved = _json.loads(saved_path.read_text()) if saved_path.is_file() else None
        observed.update(handoff=handoff, saved_handoff=saved,
                        advice=[r for r in (saved or {}).get('spec_requirements', [])
                                if r['kind'] == 'preserve_named_signals'])
        if surface == 'phase1-pack' and handoff:
            observed['saved_input'] = (
                pack_dir / handoff['read_design_input_from']).read_text()

    raw = _json.dumps(observed, indent=2, ensure_ascii=False) + '\n'
    (dest / 'observed.json').write_text(raw)
    artifact_dir = os.environ.get('R74_ARTIFACT_DIR')
    if artifact_dir:
        retained = Path(artifact_dir) / case_id / surface
        retained.mkdir(parents=True, exist_ok=True)
        (retained / 'observed.json').write_text(raw)
    print(raw)

    if surface in {'rtl-pack', 'phase1-pack'}:
        assert observed['saved_handoff'] == observed['handoff']
        expected_target = 'rtl.sv' if surface == 'rtl-pack' else 'l_doc_expectations.json'
        assert observed['handoff']['output_target'] == expected_target
        if surface == 'phase1-pack':
            assert observed['phase1_status']['status'] == 'HANDOFF_EMITTED'
            assert observed['saved_input'] == prompt
    return observed


@pytest.mark.parametrize('case', LOCALIZED_PUBLIC_INPUT['clauses'],
                         ids=[c['id'] for c in LOCALIZED_PUBLIC_INPUT['clauses']])
@pytest.mark.parametrize('surface', LOCALIZED_PUBLIC_INPUT['negative_surfaces'])
def test_issue3122_localized_role_context(tmp_path, case, surface):
    prompt = LOCALIZED_PUBLIC_INPUT['real_port_prefix'] + case['clause']
    observed = _localized_role_observation(tmp_path, case['id'], prompt, surface)
    detected = observed['detected']
    expected = ['sample_in', 'sample_out']
    if surface in {'rtl-pack', 'phase1-pack'}:
        assert observed['advice'] == []
    assert detected['named_signals'] == expected
    assert detected['has_named_signals'] is False
    assert detected['requirement'] is None
    assert [r['name'] for r in detected['signal_evidence']] == expected
    assert {r['name']: r['role'] for r in detected['non_signal_identifiers']} == {
        case['nonsignal']: LOCALIZED_ROLES[case['id']]}
    assert detected['ambiguous_identifiers'] == []
    for row in detected['signal_evidence'] + detected['non_signal_identifiers']:
        assert row['line'] > 0 and row['name'] in row['context']


@pytest.mark.parametrize('case', LOCALIZED_PUBLIC_INPUT['strict_companions'],
                         ids=[c['id'] for c in LOCALIZED_PUBLIC_INPUT['strict_companions']])
def test_issue3122_same_spelling_signal_context(tmp_path, case):
    clause = LOCALIZED_CLAUSES[case['clause_id']]
    prompt = (LOCALIZED_PUBLIC_INPUT['real_port_prefix'] + clause['clause'] +
              '\n' + case['positive_line'])
    expected = ['sample_in', 'sample_out', clause['nonsignal']]
    # One node per companion, each exercises all three normal surfaces.
    for surface in LOCALIZED_PUBLIC_INPUT['negative_surfaces']:
        observed = _localized_role_observation(tmp_path, case['id'], prompt, surface)
        detected = observed['detected']
        assert detected['named_signals'] == expected
        assert detected['has_named_signals'] is True
        assert detected['requirement']
        for name, line in zip(expected, [1, 2, 4]):
            assert any(r['name'] == name and r['line'] == line and
                       name in r['context'] for r in detected['signal_evidence'])
        if surface in {'rtl-pack', 'phase1-pack'}:
            advice = observed['advice']
            assert [r['named_signals'] for r in advice] == [expected]
            assert advice[0]['signal_evidence'] == detected['signal_evidence']
            assert advice[0]['non_signal_identifiers'] == detected['non_signal_identifiers']
            text = advice[0]['requirement']
            assert all(name in text for name in expected)
            if surface == 'rtl-pack':
                assert 'Declare EACH' in text
            else:
                assert 'Declare EACH' not in text
                assert 'l_doc_expectations.json' in text
