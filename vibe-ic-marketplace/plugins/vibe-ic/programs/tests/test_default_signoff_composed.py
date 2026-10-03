"""Existing reviewed R1 Step37.4 ordinary producer/current controls."""
import json
import pytest
import phase3_one_shot_runner as R
import _default_backend_fixtures as BF


@pytest.mark.parametrize('mutation', ['none', 'source_bytes'])
def test_ordinary_aggregation_and_its_gate_bind_both_current_products(tmp_path, mutation):
    import signoff_metrics_aggregate as AGG
    import tapeout_docs_gen as TDG
    source = BF.put(tmp_path / 'reports/phase3/drc_router.json',
                    {'summary': {'real_violation_total': 3,
                                 'producers': [{'producer': 'openroad'}]}})
    result = R.step_signoff_metrics_aggregate(tmp_path)
    assert result.status == 'PASS'
    metrics = tmp_path / 'phase3/final/metrics.json'
    report = tmp_path / AGG.REPORT_REL
    assert TDG.load_metrics(metrics)['route__drc_errors'] == 3
    assert json.loads(metrics.read_text())['design__lvs_error__count'] == AGG.NOT_MEASURED
    assert AGG.main([str(tmp_path), '--check']) == 0
    if mutation == 'source_bytes':
        # The value stays 3; only the actual producer bytes changed.
        source.write_text(source.read_text() + '\n ')
    elif mutation == 'report_removed':
        report.unlink()
    elif mutation == 'wrong_project':
        doc = json.loads(report.read_text())
        doc['project'] = str(tmp_path / 'other-design')
        BF.put(report, doc)
    elif mutation == 'provenance_removed':
        doc = json.loads(metrics.read_text())
        doc.pop('__provenance__')
        BF.put(metrics, doc)
    elif mutation == 'wrong_type':
        doc = json.loads(metrics.read_text())
        doc['route__drc_errors'] = 3.0
        BF.put(metrics, doc)
    elif mutation == 'report_changed':
        doc = json.loads(report.read_text())
        doc['measured'] += 1
        BF.put(report, doc)
    assert AGG.main([str(tmp_path), '--check']) == (0 if mutation == 'none' else 1)

