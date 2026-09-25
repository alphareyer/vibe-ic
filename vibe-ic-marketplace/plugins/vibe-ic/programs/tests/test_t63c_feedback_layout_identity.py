"""The streamed mask and its feedback receipt must describe the same bytes."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import drc_feedback_repair as feedback
import phase3_one_shot_runner as runner


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _layout(tmp_path):
    project = tmp_path / 'unit'
    pnr = project / 'phase3/stage3/pnr'
    pnr.mkdir(parents=True)
    (project / 'reports/phase3').mkdir(parents=True)
    source = pnr / 'unit.def'
    gds = pnr / 'unit.gds'
    source.write_text('DESIGN unit ;\n')
    gds.write_bytes(b'finished-mask')
    receipt = project / 'reports/phase3/drc_feedback.json'
    receipt.write_text(json.dumps({
        'status': 'PASS', 'source_sha256': _sha(source.read_bytes()),
        'finished_status': 'PASS', 'finished_gds_sha256': _sha(gds.read_bytes()),
        'finished_counts': {'CO.6a': 0}}))
    return project, source, gds, receipt


def test_final_drc_refuses_feedback_for_the_old_route(tmp_path, monkeypatch):
    project, source, _, _ = _layout(tmp_path)
    source.write_text('DESIGN unit ;\n# later route writer\n')
    monkeypatch.setattr(runner, '_vacuous_on_unrouted',
                        lambda *a: runner.StepResult('drc', 'PASS', 0, 'sentinel'))
    result = runner.step_drc(project, 'unit',
                             SimpleNamespace(drc_deck='/pdk/gf180mcu.drc'), 'eda')
    assert result.status == 'FAIL'
    assert 'DIGEST_MISMATCH' in result.detail


def test_finished_gds_mutation_refused_and_exact_bytes_accepted(tmp_path):
    project, _, gds, _ = _layout(tmp_path)
    assert feedback.check_binding(project, 'unit', gds)[0]
    gds.write_bytes(b'finishing-created-a-contact-overlap')
    assert feedback.check_binding(project, 'unit', gds) == (
        False, 'FEEDBACK_LAYOUT_DIGEST_MISMATCH_OR_UNMEASURED')


def test_finished_rule_is_measured_on_the_finished_file(tmp_path, monkeypatch):
    project, _, gds, receipt = _layout(tmp_path)
    seen = []
    def measured(_image, _project, _deck, _rule, path, _top, _scratch):
        seen.append(path)
        return [{'contact_overlap': True}]
    monkeypatch.setattr(feedback, '_measure', measured)
    result = feedback.verify_streamed(
        project, 'unit', SimpleNamespace(drc_deck='/pdk/gf180mcu.drc'),
        'sha256:' + '0' * 64, gds)
    assert seen == [gds]
    assert result['finished_counts'] == {'CO.6a': 1}
    assert result['finished_status'] == 'REFUSED'
    assert not feedback.check_binding(project, 'unit', gds)[0]
    assert json.loads(receipt.read_text())['finished_reason'] == (
        'FEEDBACK_FINISHED_GDS_RULE_NONZERO')


def test_prestream_admission_refuses_old_feedback(tmp_path, monkeypatch):
    project, source, _, _ = _layout(tmp_path)
    source.write_text('DESIGN unit ;\n# changed by signoff repair\n')
    monkeypatch.setattr(runner, '_vacuous_on_unrouted', lambda *a: None)
    result = runner.step_gds(project, 'unit',
                             SimpleNamespace(drc_deck='/pdk/gf180mcu.drc'), 'eda')
    assert result.status == 'FAIL'
    assert 'FEEDBACK_ROUTE_DIGEST_MISMATCH' in result.detail


def test_port_marker_producer_keeps_mask_bytes_out_of_lvs_copy(tmp_path, monkeypatch):
    project, source, gds, _ = _layout(tmp_path)
    pdk = SimpleNamespace(liberty='', port_label_restore=True)
    monkeypatch.setattr(runner, '_tool_in_path', lambda *a: True)
    monkeypatch.setattr(runner, '_resolve_magic_gds_tech', lambda *a: None)
    monkeypatch.setattr(runner._pl, 'pnr_dir', lambda *a: gds.parent)
    def fake_eda(_container, _cmd, **_kw):
        (gds.parent / 'unit.labeled.gds').write_bytes(b'LVS-only-marker-100/8')
        return 0, 'restored: 1 I/O labels + 0 power-rail markers ()', ''
    monkeypatch.setattr(runner, '_docker_exec', fake_eda)
    before = gds.read_bytes()
    ok, _ = runner._klayout_restore_port_labels(
        project, 'unit', pdk, 'eda', gds, source, force=True)
    assert ok
    assert gds.read_bytes() == before
    assert gds.with_suffix('.lvs.gds').read_bytes() == b'LVS-only-marker-100/8'
