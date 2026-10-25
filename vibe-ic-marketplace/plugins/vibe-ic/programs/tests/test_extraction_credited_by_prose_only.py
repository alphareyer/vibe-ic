"""The Phase-1 coverage number credits a literal that was COPIED, not read.

`extraction_patterns.json` says in its own header that it is the coverage
denominator, and a literal is credited when its verbatim string appears anywhere
in the L*.json payload. Several L fields carry the input prose unchanged, so a
literal sitting in one of those satisfies the credit test without having been
extracted into anything.

Measured on a plain-English spec for a 4-channel PWM: 35 denominator, 100.0%
reported, 8 credited only by a verbatim-prose field — and one of the eight is
`25 MHz`, the system clock, in a run whose `L8.clock_mhz` came out 10.0. The
metric certified as extracted the number the extractor got wrong.
"""
from __future__ import annotations

import json
import hashlib
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))


def _project(tmp_path: Path, denominator, structured: dict, prose: dict) -> Path:
    p = tmp_path / "proj"
    (p / "phase1" / "generated_docs").mkdir(parents=True)
    (p / "phase1" / "extraction_patterns.json").write_text(json.dumps({
        "_comment": "this file is the coverage denominator",
        "design_description.txt": [{"literal": l, "label": "auto"}
                                   for l in denominator],
    }))
    (p / "phase1" / "generated_docs" / "L2_FRS.json").write_text(
        json.dumps({"frs_sections": prose.get("frs_sections", [])}))
    (p / "phase1" / "generated_docs" / "L8_RTL_CONSTANTS.json").write_text(
        json.dumps(structured))
    return p


def _audit(p: Path):
    from extraction_credited_by_prose_only_check import audit
    return audit(p)


def test_a_literal_carried_only_by_prose_is_named(tmp_path):
    """`0xDEADBEEF` sits inside a requirement SENTENCE and nowhere structured.
    The published metric credits it; this one does not."""
    p = _project(
        tmp_path,
        denominator=["0xDEADBEEF", "0x2A"],
        structured={"parameters": [{"name": "ID_VALUE", "value": "0x2A"}]},
        prose={"frs_sections": [{"text": "the trim code is 0xDEADBEEF"}]},
    )
    rep = _audit(p)
    assert rep["prose_only"] == ["0xDEADBEEF"], rep
    assert rep["structured"] == 1 and rep["denominator"] == 2
    assert rep["structured_pct"] == 50.0


def test_a_structured_literal_is_credited(tmp_path):
    """Nothing that is genuinely extracted may be penalised — otherwise the
    gate is a complaint rather than a measurement."""
    p = _project(
        tmp_path,
        denominator=["0x2A"],
        structured={"parameters": [{"name": "ID_VALUE", "value": "0x2A"}]},
        prose={"frs_sections": []},
    )
    rep = _audit(p)
    assert rep["prose_only"] == [] and rep["structured_pct"] == 100.0


def test_the_same_literal_in_both_places_counts_as_structured(tmp_path):
    """A number quoted in a sentence AND carried in a typed field was
    extracted; the prose copy beside it does not take that away."""
    p = _project(
        tmp_path,
        denominator=["0x2A"],
        structured={"parameters": [{"name": "ID_VALUE", "value": "0x2A"}]},
        prose={"frs_sections": [{"text": "the ID register reads 0x2A"}]},
    )
    assert _audit(p)["structured_pct"] == 100.0


def test_a_denominator_literal_in_no_doc_at_all_is_separated(tmp_path):
    """"copied instead of read" and "absent entirely" are different failures
    and are reported as different lists."""
    p = _project(
        tmp_path,
        denominator=["0xFEEDFACE"],
        structured={"parameters": []},
        prose={"frs_sections": []},
    )
    rep = _audit(p)
    assert rep["uncredited"] == ["0xFEEDFACE"] and rep["prose_only"] == []


def test_a_zero_denominator_refuses(tmp_path):
    """The house rule: nothing to examine is not the same as everything
    examined and clean, so it exits 2 rather than 0."""
    from extraction_credited_by_prose_only_check import main
    p = tmp_path / "empty"
    (p / "phase1" / "generated_docs").mkdir(parents=True)
    assert main([str(p)]) == 2


def _native_census_clause(project):
    from test_issue306_advisory_gate_slot import _adv, _flow, _records
    command = ('extraction_credited_by_prose_only_check . --json '
               'reports/phase1/extraction_credited_by_prose_only.json')
    passed, reasons = _flow._evaluate_gate(project, _adv(command))
    record, = _records(reasons)
    return passed, record


def test_explicit_empty_census_reaches_native_not_applicable(tmp_path):
    p = _project(tmp_path, [], {}, {})
    passed, record = _native_census_clause(p)
    assert (record['verdict'], record['exit_code']) == ('NOT_APPLICABLE', 2), record
    assert passed and record['reason_class'] == 'NOT_APPLICABLE_BY_STRUCTURE', record
    report = json.loads((p / 'reports/phase1/extraction_credited_by_prose_only.json').read_text())
    assert report['structured_pct'] is None
    assert report['denominator'] == 0 and report['verdict'] == 'NOT_APPLICABLE'
    scope = report['census_scope']
    assert scope['path'] == 'phase1/extraction_patterns.json'
    assert scope['sha256'] == hashlib.sha256((p / scope['path']).read_bytes()).hexdigest()
    assert scope['declared_literal_count'] == 0
    assert scope['declared_source_groups'] == ['design_description.txt']
    assert report['structural_absence']['scanned'] == 1
    assert report['structural_absence']['found'] == 0


@pytest.mark.parametrize('damage', [
    'missing_census', 'malformed_census', 'nonobject_census', 'duplicate_census',
    'nonlist_scope', 'invalid_literal', 'missing_ldocs',
    'malformed_ldoc', 'nonobject_ldoc', 'unreadable_ldoc',
])
def test_absent_or_invalid_census_inputs_never_become_not_applicable(tmp_path, monkeypatch, damage):
    from extraction_credited_by_prose_only_check import main
    p = _project(tmp_path, [], {}, {})
    census = p / 'phase1/extraction_patterns.json'
    doc = p / 'phase1/generated_docs/L8_RTL_CONSTANTS.json'
    if damage == 'missing_census':
        census.unlink()
    elif damage == 'malformed_census':
        census.write_text('{')
    elif damage == 'nonobject_census':
        census.write_text('[]')
    elif damage == 'duplicate_census':
        census.write_text('{"design_description.txt":["0x3F"],"design_description.txt":[]}')
    elif damage == 'nonlist_scope':
        census.write_text(json.dumps({'unknown_public_scope': {'count': 0}}))
    elif damage == 'invalid_literal':
        census.write_text(json.dumps({'design_description.txt': [{'literal': None}]}))
    elif damage == 'missing_ldocs':
        for path in doc.parent.glob('*.json'):
            path.unlink()
    elif damage == 'malformed_ldoc':
        doc.write_text('{')
    elif damage == 'nonobject_ldoc':
        doc.write_text('[]')
    else:
        read_bytes = Path.read_bytes
        def unreadable(path):
            if path == doc:
                raise OSError('neutral unreadable generated document')
            return read_bytes(path)
        monkeypatch.setattr(Path, 'read_bytes', unreadable)
    report = p / 'reports/refused_census.json'
    assert main([str(p), '--json', str(report)]) == 2
    value = json.loads(report.read_text())
    assert value['verdict'] == 'NOT_MEASURED', value
    assert value['reason_class'] != 'NOT_APPLICABLE_BY_STRUCTURE', value
    assert 'structural_absence' not in value


def test_positive_prose_only_census_keeps_existing_advisory_policy(tmp_path):
    from extraction_credited_by_prose_only_check import main
    p = _project(tmp_path, ['0xDEADBEEF'], {}, {
        'frs_sections': [{'content': 'The trim value is 0xDEADBEEF.'}]})
    report = p / 'reports/positive_census.json'
    assert main([str(p), '--json', str(report)]) == 0
    value = json.loads(report.read_text())
    assert value['denominator'] == 1 and value['prose_only'] == ['0xDEADBEEF']
    assert value['structured_pct'] == 0.0
    assert value.get('verdict') != 'NOT_APPLICABLE'
    assert 'structural_absence' not in value


@pytest.mark.parametrize('clocked', [False, True])
def test_native_d1_accepts_declared_empty_census_without_false_checker_pass(tmp_path, clocked):
    import execution_backend_gates as gates
    from test_issue306_advisory_gate_slot import _native_extraction_project, _shipped_step
    p = _native_extraction_project(tmp_path, clocked=clocked, literal=False)
    result = gates.evaluate(p, _shipped_step('D1'))
    (p / 'native_empty_census_d1.json').write_text(json.dumps(result, indent=2))
    record, = [r for r in result['step']['advisory_gate_records']
               if r['gate'] == 'extraction_credited_by_prose_only_check']
    assert (record['verdict'], record['exit_code']) == ('NOT_APPLICABLE', 2), record
    assert record['reason_class'] == 'NOT_APPLICABLE_BY_STRUCTURE', record
    assert record['structured_verdict'] == 'NOT_APPLICABLE', record
    assert result['status'] == 'PASS', result


def test_empty_census_never_overrides_independent_current_d1_fact_loss(tmp_path):
    import ai_signed_judgement as ai
    import execution_backend_gates as gates
    import phase1_expert_parse_track as track
    from test_issue306_advisory_gate_slot import _native_extraction_project, _shipped_step
    p = _native_extraction_project(tmp_path, literal=False)
    doc = p / 'phase1/generated_docs/L9_INTEGRATION_SPEC.json'
    value = json.loads(doc.read_text())
    for key in ('ports', 'top_ports', 'top_module_pins'):
        value[key] = [row for row in value[key] if row['name'] != 'result_o']
    doc.write_text(json.dumps(value))
    answer = p / 'reports/audit/phase1/expert_parse_track_pack/l_doc_expectations.json'
    answer.write_text(json.dumps({'expectations': [{
        'id': 'neutral_source_output', 'layer': 'L9_INTEGRATION_SPEC',
        'field_path': 'ports', 'requirement': 'Retain the declared result_o output.',
        'expected_tokens': ['result_o'],
        'evidence': ['input/docs/interface.md:2 output [5:0] result_o'],
    }]}))
    assert track.main([str(p), '--invoked-by', 'phase1_one_shot_runner']) == 0
    reading = json.loads((p / 'reports/audit/phase1/expert_parse_track.json').read_text())
    assert reading['ai_convergence']['disagreed'] == 1, reading
    (p / 'reports/audit/ai_judgements/D1.json').write_text(json.dumps({
        'schema': ai.SCHEMA, 'step_id': 'D1', 'verdict': 'FAIL',
        'signed_by': 'independent neutral source reviewer',
        'judgement': 'The source declares result_o, but the typed L9 interface lost it.',
        'evidence_sha256': ai.evidence_sha256(p, 'D1'),
    }))
    assert ai.check(p, 'D1', expected_verdict='FAIL')[0]
    result = gates.evaluate(p, _shipped_step('D1'))
    record, = [r for r in result['step']['advisory_gate_records']
               if r['gate'] == 'extraction_credited_by_prose_only_check']
    assert record['verdict'] == 'NOT_APPLICABLE', record
    assert result['status'] not in ('PASS', 'PASS_WITH_WAIVERS'), result
    assert any('FAIL' in reason and 'judgement' in reason.lower()
               for reason in result['step']['reasons']), result
