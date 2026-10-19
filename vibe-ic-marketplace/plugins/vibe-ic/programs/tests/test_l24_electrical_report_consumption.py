"""L24 reads the existing combined DRV report; fixtures are not native readings."""
import hashlib
import json
from pathlib import Path

import l24_signoff_evidence_backed_check as gate
import phase1_post_process
import step_output_collector as versions

FIXTURE = (Path(__file__).parent /
           'fixtures/phase1_local/neutral_electrical_signoff/signoff.md').read_text()
ELECTRICAL = {'max_slew', 'max_capacitance', 'max_fanout'}


def project(tmp_path):
    docs = tmp_path / 'input/docs'
    docs.mkdir(parents=True)
    (docs / 'signoff.md').write_text(FIXTURE)
    generated = tmp_path / 'phase1/generated_docs'
    generated.mkdir(parents=True)
    doc = phase1_post_process.emit_l_doc_skeleton('L24', 'unknown', project_dir=tmp_path)
    (generated / 'L24_SIGNOFF.json').write_text(json.dumps(doc))
    sdc = tmp_path / 'phase2/stage2/constraints/neutral.sdc'
    sdc.parent.mkdir(parents=True)
    sdc.write_text('create_clock -period 20 [get_ports clk]\n')
    return tmp_path, sdc


def attempt(project, sdc, sid):
    inputs = {sdc.relative_to(project).as_posix(): hashlib.sha256(sdc.read_bytes()).hexdigest()}
    entry = versions.begin_step(project, sid, inputs)
    versions.finish_step(project, entry, verdict='PASS', result={'detail': 'synthetic owner attempt'})


def report(project, rel, payload):
    path = project / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def read(project):
    output = project / 'consumer-reading.json'
    rc = gate.main(['l24_signoff_evidence_backed_check', str(project), '--json', str(output)])
    payload = json.loads(output.read_text())
    rows = {row['check']: row for row in payload['requirements'] if row['check'] in ELECTRICAL}
    print('ELECTRICAL_L24_CONSUMER ' + json.dumps({'rc': rc, 'rows': rows}, sort_keys=True))
    return rc, rows


def test_existing_combined_drv_report_answers_the_three_declared_checks(tmp_path):
    p, sdc = project(tmp_path)
    attempt(p, sdc, '31')
    report(p, 'reports/phase3/drc_signoff.json', {'verdict': 'PASS'})
    # Schema/name/verdict are the existing drv_signoff_judge output contract.
    # This controlled report is a consumer fixture, not an actual DRV measurement.
    report(p, 'reports/phase3/sta/drv_signoff_verdict.json', {
        'schema_version': 1, 'name': 'DRV(tran/cap/fanout)', 'verdict': 'PASS',
        'counts': {'max_slew': 0, 'max_capacitance': 0, 'max_fanout': 0},
        'findings': [], 'failures': [], 'not_measured': [], 'waived': [],
        'capture_point': 'post_stream', 'final_signoff_capture': True})
    rc, rows = read(p)
    assert {name: row['outcome'] for name, row in rows.items()} == dict.fromkeys(ELECTRICAL, 'BACKED')
    assert rc == 0
    assert all({'path': 'reports/phase3/sta/drv_signoff_verdict.json', 'verdict': 'pass'}
               in row['records_read'] for row in rows.values())


def test_sta_only_pass_cannot_back_any_electrical_check(tmp_path):
    p, sdc = project(tmp_path)
    attempt(p, sdc, '31')
    report(p, 'reports/phase3/drc_signoff.json', {'verdict': 'PASS'})
    report(p, 'reports/phase3/sta/post_route_summary.json',
           {'program': 'STAPostPNR', 'verdict': 'PASS'})
    rc, rows = read(p)
    assert rc == 1
    assert {row['outcome'] for row in rows.values()} == {'UNMET_NO_READING'}
    assert all(not row['records_read'] for row in rows.values())


def test_electrical_reading_is_due_at_actual_step31_not_earlier_sta(tmp_path):
    p, sdc = project(tmp_path)
    attempt(p, sdc, '23')
    _, before = read(p)
    assert {row['outcome'] for row in before.values()} == {'NOT_YET_MEASURABLE'}
    assert {row['producer_step'] for row in before.values()} == {'31'}
    attempt(p, sdc, '31')
    rc, after = read(p)
    assert rc == 1
    assert {row['outcome'] for row in after.values()} == {'UNMET_NO_READING'}
