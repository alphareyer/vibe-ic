"""Native Default producer/consumer boundaries for backend rows 18--20."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import execution_backend_producers as producers  # noqa: E402
import execution_backend_worker as worker  # noqa: E402
from execution_provider_catalog import BACKEND_SITES  # noqa: E402


def _write(project: Path, relative: str, text: str) -> Path:
    path = project / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _native_outputs(project: Path, step: str) -> None:
    if step == "18":
        _write(project, "phase3/stage3/pnr/post_spare.def",
               "VERSION 5.8 ;\nDESIGN top ;\nEND DESIGN\n")
        _write(project, "phase3/stage3/pnr/spare_cells.json",
               json.dumps({"spare_count": 4, "target_density": 0.01}))
        _write(project, "reports/spare_cell_coverage.json",
               json.dumps({"verdict": "PASS", "spare_count": 4}))
    elif step == "19":
        _write(project, "phase3/stage3/pnr/post_cts.def",
               "VERSION 5.8 ;\nDESIGN top ;\nEND DESIGN\n")
        _write(project, "phase3/stage3/cts/clock_tree.rpt",
               "clock latency 1.0 skew 0.1 buffers 2\n")
    else:
        _write(project, "phase3/stage3/pnr/post_hold.def",
               "VERSION 5.8 ;\nDESIGN top ;\nEND DESIGN\n")


def _manifest(project: Path, relative: str) -> dict:
    path = project / relative
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"declarations": [{"from": "external", "path": relative}],
            "files": {relative: digest}, "missing": []}


def _registry_outputs(project: Path, step: str) -> list[str]:
    from execution_adapters_backend import ROWS
    paths = []
    for spec in ROWS[step]['canonical_row']['required_outputs']:
        raw = str(spec).split(' OR ')[0]
        if '*' in raw:
            raw = raw.replace('*', 'fixture')
        path = _write(project, raw, '{}\n' if raw.endswith('.json') else 'fixture\n')
        paths.append(str(path.relative_to(project)))
    return paths


def test_native_rows_create_current_receipt_after_producer(tmp_path, monkeypatch):
    for step, predecessor in (("18", "placed.def"),
                              ("19", "placed.def"),
                              ("20", "post_cts.def")):
        project = tmp_path / step
        _write(project, f"phase3/stage3/pnr/{predecessor}", "input\n")
        events = []

        def native(current_project, _params, *, _events=events, _step=step):
            _events.append("producer")
            _native_outputs(current_project, _step)
            return {"verdict": "PASS", "detail": "native fixture"}

        monkeypatch.setattr(producers, "produce_pnr", native)
        result = producers.produce(
            project, {"step_id": step,
                      "resolved_input_manifest": _manifest(
                          project, f"phase3/stage3/pnr/{predecessor}")})
        current = result["current_step_declared_producers"]
        assert events == ["producer"]
        assert current["schema"] == "vibeic/native-current-step-receipt/1"
        assert current["verdict"] == "PRODUCED"
        assert current["step_id"] == step
        assert producers._current_step_producer_authority(
            current, project=project) == (True, "", "PASS")


def test_native_receipt_mutation_blocks_consumer(tmp_path, monkeypatch):
    project = tmp_path / "project"
    predecessor = "phase3/stage3/pnr/placed.def"
    _write(project, predecessor, "input\n")
    events = []

    def native(current_project, _params):
        events.append("producer")
        _native_outputs(current_project, "18")
        return {"verdict": "PASS", "detail": "native fixture"}

    monkeypatch.setattr(producers, "produce_pnr", native)
    result = producers.produce(
        project, {"step_id": "18",
                  "resolved_input_manifest": _manifest(project, predecessor)})
    current = result["current_step_declared_producers"]
    output = project / "phase3/stage3/pnr/spare_cells.json"
    output.write_text(output.read_text() + "mutated\n")
    assert producers._current_step_producer_authority(
        current, project=project) == (
            False, "native current producer output changed: "
            "phase3/stage3/pnr/spare_cells.json", "NOT_MEASURED")


def test_worker_calls_consumer_only_after_native_receipt(tmp_path, monkeypatch):
    inputs = tmp_path / "inputs" / "project"
    predecessor = inputs / "phase3/stage3/pnr/placed.def"
    predecessor.parent.mkdir(parents=True)
    predecessor.write_text("input\n")
    outputs = tmp_path / "outputs"
    events = []

    def native(project, _params):
        events.append("producer")
        _native_outputs(project, "18")
        return {"verdict": "PASS", "detail": "native fixture"}

    monkeypatch.setattr(producers, "produce_pnr", native)
    monkeypatch.setattr(worker, "evaluate",
                        lambda _project, _row: events.append("consumer") or
                        {"status": "PASS", "ledger": []})
    monkeypatch.setenv("VIBEIC_EXECUTION_BINDING", json.dumps({
        "required_gates": [], "source_sha": "fixture"}))
    result = worker.execute(inputs.parent, outputs, step_id="18", params={})
    assert events == ["producer", "consumer"]
    assert result["verdict"] == "PASS"
    assert result["canonical_receipts"][0]["schema"] == \
        "vibeic/backend-producer-receipt/1"


def test_registry_rows_create_receipts_from_catalog_sites(tmp_path, monkeypatch):
    for step in ('27', '32'):
        project = tmp_path / step
        input_path = _write(project, 'inputs/current.txt', 'current\n')
        outputs = _registry_outputs(project, step)

        def native(current_project, _params, _outputs=outputs):
            for raw in _outputs:
                _write(current_project, raw,
                       '{"fresh": 1}\n' if raw.endswith('.json') else 'fresh\n')
            return {'verdict': 'PASS', 'detail': 'registry fixture'}

        monkeypatch.setattr(producers, 'produce_signoff', native)
        result = producers.produce(
            project, {'step_id': step,
                      'resolved_input_manifest': _manifest(
                          project, str(input_path.relative_to(project)))})
        current = result['current_step_declared_producers']
        assert result['verdict'] == 'PASS'
        assert current['verdict'] == 'PRODUCED'
        assert current['producer'] == list(BACKEND_SITES[step])
        assert set(current['preexisting_outputs']) == set(outputs)
        assert producers._current_step_producer_authority(
            current, project=project) == (True, '', 'PASS')


def test_registry_receipt_rejects_unchanged_preexisting_output(tmp_path, monkeypatch):
    project = tmp_path / 'stale'
    input_path = _write(project, 'inputs/current.txt', 'current\n')
    _registry_outputs(project, '27')

    monkeypatch.setattr(producers, 'produce_signoff',
                        lambda _project, _params: {'verdict': 'PASS'})
    result = producers.produce(
        project, {'step_id': '27',
                  'resolved_input_manifest': _manifest(
                      project, str(input_path.relative_to(project)))})
    current = result['current_step_declared_producers']
    assert result['verdict'] == 'NOT_MEASURED'
    assert current['reason'].startswith('NATIVE_CURRENT_OUTPUT_UNCHANGED:')


def test_registry_receipt_rejects_missing_output(tmp_path, monkeypatch):
    project = tmp_path / 'missing'
    input_path = _write(project, 'inputs/current.txt', 'current\n')
    monkeypatch.setattr(producers, 'produce_signoff',
                        lambda _project, _params: {'verdict': 'PASS'})
    result = producers.produce(
        project, {'step_id': '32',
                  'resolved_input_manifest': _manifest(
                      project, str(input_path.relative_to(project)))})
    current = result['current_step_declared_producers']
    assert result['verdict'] == 'NOT_MEASURED'
    assert current['reason'].startswith('NATIVE_CURRENT_OUTPUT_CONTRACT_UNAVAILABLE:')


def test_registry_measured_fail_survives_missing_outputs(tmp_path, monkeypatch):
    project = tmp_path / 'fail'
    input_path = _write(project, 'inputs/current.txt', 'current\n')
    monkeypatch.setattr(producers, 'produce_signoff',
                        lambda _project, _params: {
                            'verdict': 'FAIL', 'detail': 'native failure'})
    result = producers.produce(
        project, {'step_id': '32',
                  'resolved_input_manifest': _manifest(
                      project, str(input_path.relative_to(project)))})
    assert result['verdict'] == 'FAIL'


def test_registry_receipt_rejects_wrong_identity_and_mutated_input(tmp_path, monkeypatch):
    project = tmp_path / 'mutation'
    input_path = _write(project, 'inputs/current.txt', 'current\n')
    outputs = _registry_outputs(project, '32')

    def native(current_project, _params):
        for raw in outputs:
            _write(current_project, raw, 'fresh\n')
        return {'verdict': 'PASS'}

    monkeypatch.setattr(producers, 'produce_signoff', native)
    current = producers.produce(
        project, {'step_id': '32',
                  'resolved_input_manifest': _manifest(
                      project, str(input_path.relative_to(project)))})[
                          'current_step_declared_producers']
    wrong = dict(current, producer=['wrong-site'])
    assert producers._current_step_producer_authority(
        wrong, project=project) == (
            False, 'native current producer identity is invalid', 'NOT_MEASURED')
    input_path.write_text('mutated\n')
    assert producers._current_step_producer_authority(
        current, project=project) == (
            False, 'native current producer input changed: inputs/current.txt',
            'NOT_MEASURED')


def test_worker_does_not_promote_entire_tree_to_producer_inputs(tmp_path, monkeypatch):
    inputs = tmp_path / 'inputs' / 'project'
    inputs.mkdir(parents=True)
    _write(inputs, 'unrelated.txt', 'must not enter receipt\n')
    outputs = tmp_path / 'outputs'
    seen = {}

    def fake_produce(_project, params):
        seen['files'] = params['resolved_input_manifest']['files']
        return {'verdict': 'NOT_MEASURED', 'current_step_declared_producers': None}

    monkeypatch.setattr(worker, 'produce', fake_produce)
    monkeypatch.setenv('VIBEIC_EXECUTION_BINDING',
                       json.dumps({'required_gates': []}))
    worker.execute(inputs.parent, outputs, step_id='32', params={})
    assert seen['files'] == {}


def _retained_default_stream(tmp_path, monkeypatch):
    """Original owner/validators; only native process edges are neutral data."""
    import klayout.db as db
    import librelane_contract as LC
    import librelane_fill_dfm as LF
    import phase3_one_shot_runner as R
    import _runner_measurement
    import test_default_stream_epoch_order as transport
    geometry = transport._geometry

    def substantive_geometry(path, number=0):
        geometry(path, number)
        layout = db.Layout(); layout.read(str(path))
        top = layout.cell('cal_pg')
        # Real neutral geometry, not padding or a relaxed size threshold.
        for index in range(2000):
            top.shapes(layout.layer(1, 0)).insert(
                db.Box(index * 2, 10, index * 2 + 1, 11))
        layout.write(str(path))
    monkeypatch.setattr(transport, '_geometry', substantive_geometry)
    project, pdk, calls, _ = transport._transport(tmp_path, monkeypatch)
    first = LF.default_stream(project, 'top', pdk, '')
    assert calls == ['primary', 'seal', 'filler-density']
    # A core-only delivery does not owe the independent pad-ring report.
    (project / 'input/submission_template/slots/neutral.yaml').unlink()
    _write(project, 'input/submission_template/tapeout_declaration.json',
           json.dumps({'answers': {'deliverable': 'HARDMACRO'}}))
    _write(project, 'FINAL_REPORT.md', '# SOURCE_FIXTURE\nNo completion claims.\n')
    _write(project, 'plugin_output/declaration.json', '{}\n')
    canonical = Path(first['canonical'])
    relative = str(canonical.relative_to(project))
    _write(project, 'provenance.jsonl', json.dumps({
        'record': 'invocation', 'tool': 'klayout', 'exit_code': 0,
        'scope': 'SOURCE_FIXTURE controlled native process',
        'outputs': {relative: 'sha256:' + LC.digest(canonical)},
        'measurement': _runner_measurement.derive(project, relative, 'klayout'),
    }) + '\n')
    monkeypatch.setattr(producers, '_parameters', lambda *_: (R, LC, pdk, ''))
    monkeypatch.setattr(R, 'step_prestream_gate', lambda *_: R.StepResult(
        'prestream', 'PASS', 0.0, 'SOURCE_FIXTURE upstream admission',
        extras={'layout_digest': LC.digest(project / 'phase3/stage3/pnr/routed.def')}))
    monkeypatch.setattr(R, '_layout_basis', lambda *_: ('fixture-layout', None))
    monkeypatch.setattr(R, '_vacuous_on_unrouted', lambda *_: None)
    monkeypatch.setattr(R._ga, 'stream_refusal', lambda *_: None)
    monkeypatch.setattr(R._ga, 'admit_gds', lambda *_: None)
    monkeypatch.setattr(R, '_layout_basis_paths', lambda *_: [])
    monkeypatch.setattr(R, '_chip_path_requests_pad_ring', lambda *_: False)
    monkeypatch.setattr(R, '_librelane_antenna_gds', lambda *_: None)
    # Only the optional native antenna tool edge is neutral. The actual full
    # consumer, size/substance/port/provenance programs and LF/LC readers run.
    import flow_compliance_check as fcc
    gate_process = fcc._watchdog.run_host_supervised

    def native_gate_edge(argv, **kwargs):
        if len(argv) > 1 and Path(argv[1]).name == 'gds_antenna_deck_check.py':
            report = project / argv[argv.index('--json') + 1]
            _write(project, str(report.relative_to(project)), json.dumps({
                'verdict': 'PASS', 'evidence_kind': 'SOURCE_FIXTURE native process',
                'gds_sha256': LC.digest(canonical)}))
            return fcc._watchdog.SupervisedResult(
                0, 'SOURCE_FIXTURE optional native antenna process\n', '', 'natural')
        return gate_process(argv, **kwargs)
    monkeypatch.setattr(fcc._watchdog, 'run_host_supervised', native_gate_edge)
    params = {'step_id': '37', 'top': 'top', 'pdk_name': pdk.name,
              'pdk_root': str(project / 'fixture_pdk'), 'image_id': first['image'],
              'execution_mode': 'default',
              'resolved_input_manifest': _manifest(
                  project, 'phase3/stage3/pnr/routed.def')}
    return project, params, first, calls


@pytest.mark.parametrize('scenario', [
    'current_reuse', 'changed_log', 'changed_gds', 'changed_readset',
    'changed_recipe', 'missing_native_proof', 'density_not_current'])
def test_default_step37_whole_reuse_reaches_current_consumer(
        tmp_path, monkeypatch, scenario):
    import librelane_fill_dfm as LF
    import phase3_one_shot_runner as R
    import step_output_collector as versions
    project, params, first, calls = _retained_default_stream(tmp_path, monkeypatch)
    log = project / 'phase3/stage3/pnr/stream_out.log'
    before = log.read_bytes(), log.stat().st_mtime_ns

    def after_stream(*_):
        # Mutate one actual retained artefact after the normal owner returned,
        # before the native-current collector. Do not stub its validators.
        targets = {
            'changed_log': log, 'changed_gds': Path(first['canonical']),
            'changed_readset': project / 'phase3/stage3/pnr/top.stream_inputs.json',
            'changed_recipe': project / 'phase3/stage3/pnr/stream_out.py',
            'missing_native_proof': Path(first['stream_state']).parent / 'vibeic_receipt.json',
            'density_not_current': project / 'reports/phase3/metal_density.json'}
        if scenario == 'missing_native_proof':
            targets[scenario].unlink()
        elif scenario in targets:
            targets[scenario].write_bytes(targets[scenario].read_bytes() + b'\nchanged\n')
    monkeypatch.setattr(R, '_librelane_antenna_gds', after_stream)
    # Real produce_signoff -> real step_gds -> existing whole-reuse owner;
    # omitting finishing_receipt at that return site must break the positive.
    attempt = versions.begin_step(project, '37', params['resolved_input_manifest']['files'])
    result = producers.produce(project, params)
    current = result['current_step_declared_producers']
    if result['verdict'] == 'PASS':
        versions.publish_producer_outputs(project, attempt)
    consumed = worker.execute_current_step(project, step_id='37', producer=result)
    assert calls == ['primary', 'seal', 'filler-density'], 'no second native epoch'
    if scenario != 'current_reuse':
        assert result['verdict'] == 'NOT_MEASURED'
        assert 'NATIVE_CURRENT_OUTPUT_UNCHANGED' in current['reason']
        assert consumed['verdict'] == 'NOT_MEASURED'
        return
    assert result['verdict'] == 'PASS', result
    assert result['finishing_receipt'] == str(project / LF.DEFAULT_STREAM_REL)
    assert (log.read_bytes(), log.stat().st_mtime_ns) == before
    adopted = current['retained_outputs']['outputs']
    assert str(log.relative_to(project)) in adopted
    assert set(adopted) <= set(current['outputs'])
    assert consumed['verdict'] == 'PASS', consumed
    assert consumed['canonical_step_result']['status'] == 'PASS'
    assert any(row.get('gate') == 'gds_substance_check'
               and row.get('rc') == 0 for row in consumed['gate_ledger'])
    # Consumer re-derives promotion/density proof, not just the output hashes.
    promotion = project / LF.DEFAULT_STREAM_REL
    promotion.unlink()
    refused = worker.execute_current_step(project, step_id='37', producer=result)
    assert refused['verdict'] == 'NOT_MEASURED'
    assert 'native retained stream refused' in refused['gate_ledger'][0]['reason']
