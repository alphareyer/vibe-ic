"""Focused source checks for the pre-placement classifier50 adapters.

These tests cover disposition and declaration binding only. They deliberately
do not construct a lease, registry, native worker, or execution authority.
"""

from pathlib import Path
from types import SimpleNamespace
import json

import pytest

import execution_adapters_analog as analog
import execution_adapters_backend as backend
import execution_adapters_release as release


class Request:
    def __init__(self, step_id, project, parameters, source_sha='a' * 40):
        self.step_id = step_id
        self.project = Path(project)
        self.parameters = parameters
        self.lease = Path('/tmp/classification50-lease')
        self.record = Path('/tmp/classification50-record.json')
        self.source_sha = source_sha
        self.source_files = {'/tmp/classification50-source.py': 'b' * 64}

    def check_source(self):
        return None


def _assert_marker(prepared, disposition):
    assert prepared.disposition == disposition
    assert prepared.context is None
    assert prepared.registry is None
    assert prepared.consume is None
    assert prepared.handoff['facts']['source_sha']


def test_backend_bound_hardmacro_is_classified_without_placement(tmp_path, monkeypatch):
    declaration = tmp_path / 'declaration.json'
    declaration.write_text('{"answers":{"deliverable":"HARDMACRO"}}\n')
    request = Request('15.5ic', tmp_path, {
        'declaration': declaration,
        'declaration_sha256': backend.snap.sha(declaration),
    })
    monkeypatch.setattr(backend.subprocess, 'check_output',
                        lambda *args, **kwargs: request.source_sha + '\n')
    monkeypatch.setattr(backend.subprocess, 'run',
                        lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(backend, '_source_files', lambda value: dict(value.source_files))
    prepared = backend.classify(request)
    _assert_marker(prepared, 'declared_inapplicable')
    assert prepared.handoff['declaration_sha256'] == backend.snap.sha(declaration)


def test_backend_stale_declaration_digest_refuses(tmp_path, monkeypatch):
    declaration = tmp_path / 'declaration.json'
    declaration.write_text('{"answers":{"deliverable":"HARDMACRO"}}\n')
    request = Request('15.5ic', tmp_path, {
        'declaration': declaration, 'declaration_sha256': 'c' * 64,
    })
    monkeypatch.setattr(backend.subprocess, 'check_output',
                        lambda *args, **kwargs: request.source_sha + '\n')
    monkeypatch.setattr(backend.subprocess, 'run',
                        lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(backend, '_source_files', lambda value: dict(value.source_files))
    with pytest.raises(backend.em.Refusal):
        backend.classify(request)


def test_backend_malformed_list_refuses_before_owner_get(tmp_path, monkeypatch):
    declaration = tmp_path / 'declaration.json'
    declaration.write_text('[]\n')
    request = Request('15.5ic', tmp_path, {'declaration': declaration})
    monkeypatch.setattr(backend.subprocess, 'check_output',
                        lambda *args, **kwargs: request.source_sha + '\n')
    monkeypatch.setattr(backend.subprocess, 'run',
                        lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(backend, '_source_files', lambda value: dict(value.source_files))
    with pytest.raises(backend.em.Refusal, match='BACKEND_DECLARATION_NOT_CURRENT'):
        backend.classify(request)


def test_analog_empty_current_declaration_is_classified_na(tmp_path):
    declaration = tmp_path / 'phase1/analog/analog_block_list.json'
    declaration.parent.mkdir(parents=True)
    declaration.write_text('{"blocks":[]}\n')
    request = Request('A1', tmp_path, {
        'project': str(tmp_path), 'declaration': declaration,
    })
    prepared = analog.classify(request)
    _assert_marker(prepared, 'declared_inapplicable')
    assert prepared.handoff['declaration_sha256'] == analog.em.digest(declaration)


def test_analog_missing_declaration_refuses(tmp_path):
    request = Request('A1', tmp_path, {
        'project': str(tmp_path),
        'declaration': tmp_path / 'phase1/analog/analog_block_list.json',
    })
    with pytest.raises(analog.em.Refusal):
        analog.classify(request)


def _release_declaration(project, deliverable):
    declaration = project / 'input/submission_template/tapeout_declaration.json'
    declaration.parent.mkdir(parents=True)
    declaration.write_text(
        '{"answers":{"deliverable":"' + deliverable + '","top_cell":"top"},'
        '"answer_provenance":{"deliverable":{"answered_by":"owner",'
        '"citation":"owner declaration"}}}\n')
    return declaration


def _release_params(declaration, route, project=None):
    return {'top': 'top', 'pdk': 'pdk', 'route': route,
            'image': 'sha256:' + '0' * 64, 'declaration': declaration,
            'external_receipts': [],
            **({'input_roots': {'source': project}} if project is not None else {})}


def test_release_external_without_receipts_is_provider_unavailable(tmp_path):
    declaration = _release_declaration(tmp_path, 'DIE')
    prepared = release.classify(Request('39', tmp_path, _release_params(declaration, 'IC', tmp_path)))
    _assert_marker(prepared, 'provider_unavailable')
    assert prepared.handoff['declaration_sha256'] == release.em.digest(declaration)
    assert prepared.handoff['facts']['design_verdict'] == 'NOT_MEASURED'
    assert prepared.handoff['missing_obligations']


def test_release_external_preserves_fail_mixed_nm_receipts_and_checkers(tmp_path):
    declaration = _release_declaration(tmp_path, 'DIE')
    raw = tmp_path / 'phase3/stage5_manufacturing/htol_results.json'
    raw.parent.mkdir(parents=True)
    raw.write_text('{"units_tested":10,"stress_hours":100,"failures":1,"device_hours":1000}\n')
    subject = {release.DECLARATION: release.em.digest(declaration)}
    base = {'schema': 'vibeic.release.external.v1', 'step_id': '44',
            'identities': {'source_sha': 'a' * 40, 'top': 'top', 'pdk': 'pdk',
                           'route': 'IC', 'image': 'sha256:' + '0' * 64},
            'measurement_id': 'm1', 'issuer': 'fixture',
            'raw_files': {raw.relative_to(tmp_path).as_posix(): release.em.digest(raw)},
            'subject_inputs': subject}
    fail = tmp_path / 'external/fail.json'
    fail.parent.mkdir(parents=True)
    fail.write_text(json.dumps(dict(base, verdict='FAIL')) + '\n')
    nm = tmp_path / 'external/nm.json'
    nm.write_text(json.dumps(dict(base, measurement_id='m2', verdict='NOT_MEASURED')) + '\n')
    params = _release_params(declaration, 'IC', tmp_path)
    params['external_receipts'] = [str(fail.relative_to(tmp_path)), str(nm.relative_to(tmp_path))]
    prepared = release.classify(Request('44', tmp_path, params))
    _assert_marker(prepared, 'external_handoff')
    assert prepared.handoff['design_verdict'] == 'FAIL'
    assert len(prepared.handoff['receipts']) == 2
    assert any(row.get('verdict') == 'FAIL' for row in prepared.handoff['existing_checkers'])
    assert prepared.handoff['facts']['input_population']['project/' + release.DECLARATION]


def test_release_external_no_measurements_retains_checker_not_measured(tmp_path):
    declaration = _release_declaration(tmp_path, 'DIE')
    params = _release_params(declaration, 'IC', tmp_path)
    prepared = release.classify(Request('44', tmp_path, params))
    _assert_marker(prepared, 'provider_unavailable')
    assert prepared.handoff['design_verdict'] == 'NOT_MEASURED'
    assert any(row.get('verdict') == 'NOT_MEASURED'
               for row in prepared.handoff['existing_checkers'])


def test_release_ip_hardmacro_is_na_and_other_route_is_execute(tmp_path):
    declaration = _release_declaration(tmp_path, 'HARDMACRO')
    na = release.classify(Request('37.5ic', tmp_path, _release_params(declaration, 'IP', tmp_path)))
    _assert_marker(na, 'declared_inapplicable')
    execute = release.classify(Request('37.5ip', tmp_path, _release_params(declaration, 'IP', tmp_path)))
    _assert_marker(execute, 'execute')


def test_release_stale_declaration_digest_refuses(tmp_path):
    declaration = _release_declaration(tmp_path, 'DIE')
    params = _release_params(declaration, 'IC', tmp_path)
    params['declaration_sha256'] = 'd' * 64
    with pytest.raises(release.em.Refusal):
        release.classify(Request('39', tmp_path, params))
