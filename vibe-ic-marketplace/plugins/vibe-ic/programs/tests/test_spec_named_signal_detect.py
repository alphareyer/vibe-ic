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
