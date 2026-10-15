"""Neutral Default dispatch/consumer controls; no native IC measurement."""
import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import execution_backend_producers as producers
import execution_backend_worker as worker
import execution_policy as policy
import phase3_one_shot_runner as runner
import step_output_collector as versions


def put(project, rel, data):
    path = project / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else json.dumps(data).encode())
    return path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def default_mode(monkeypatch):
    monkeypatch.setattr(policy, 'request', lambda: {'mode': 'default'})
    monkeypatch.setattr(policy, '_ordinary_runtime', None)


def owner_inputs(project):
    put(project, 'phase3/stage3/pnr/routed.def', b'current routed layout')
    put(project, 'input/submission_template/slots/neutral.yaml', b'DIE_AREA: [0,0,100,100]\n')
    return put(project, 'input/submission_template/tapeout_declaration.json',
               {'answers': {'deliverable': 'DIE'}})


@pytest.mark.parametrize('next_status', ['PASS', 'FAIL', 'NOT_MEASURED'])
def test_bounded_plan_retains_pending_finishing_and_runs_the_next_due_row(
        tmp_path, monkeypatch, next_status):
    # Historical test ID retained: router 26 now precedes 27; finishing is
    # already accepted inside stream 37 and is never a pending early row.
    source = owner_inputs(tmp_path)
    seen = []
    monkeypatch.setattr(runner, '_backend_parameter_values', lambda *a: {})
    monkeypatch.setattr(producers, '_parameters', lambda *a:
                        (runner, None, SimpleNamespace(name='neutral'), ''))

    def inputs(runtime, sid, *, parameters):
        parameters['resolved_input_manifest'] = {
            'files': {str(source.relative_to(tmp_path)): digest(source)},
            'declarations': [], 'missing': []}

    monkeypatch.setattr(policy, '_fixed_inputs', inputs)

    def native(project, params):
        sid = params['step_id']
        seen.append(('producer', sid))
        if sid == '26':
            return {'verdict': 'PASS', 'detail': 'neutral current router-antenna transport'}
        return {'verdict': next_status, 'detail': 'neutral next-row native transport'}

    def consume(project, *, step_id, producer):
        seen.append(('consumer', step_id))
        return {**producer, 'outputs': {}, 'canonical_step_result': {
            'status': producer['verdict'], 'reasons': [producer['detail']]}}

    monkeypatch.setattr(producers, 'produce', native)
    monkeypatch.setattr(worker, 'execute_current_step', consume)
    assert runner._phase3_window_sites('26', '27') == ['default_backend']
    ids = runner._phase3_window_steps('26', '27')
    assert ids == ['26', '27']
    result = runner._default_pnr_rows(tmp_path, 'neutral', None, '', ids)
    assert seen == [('producer', '26'), ('consumer', '26'), ('producer', '27'), ('consumer', '27')]
    assert result.status == next_status
    checks = result.extras['canonical_gate_checks']
    assert checks['26']['status'] == 'PASS'
    assert 'deferred_until' not in checks['26']
    assert checks['27']['status'] == next_status
    assert versions.latest_pass(tmp_path, '26.5ic') is None
    index = versions._read_index(tmp_path)
    pending = next(r for r in index['steps'] if r['id'] == '26')
    saved = json.loads((tmp_path / 'steps' / pending['folder'] / 'outputs.json').read_text())
    assert saved['status'] == 'PASS' and saved['canonical_pass']
    assert not list(tmp_path.rglob('*.gds'))


def selected_native_stream(project):
    initial = put(project, 'phase3/librelane/37-magic/unsealed.gds', b'native unsealed stream')
    sealed = put(project, 'phase3/librelane/37-magic-finish/01-seal/sealed.gds', b'native selected seal')
    state = put(project, 'phase3/librelane/37-magic-finish/01-seal/state_out.json',
                {'gds': str(sealed)})
    final_native = put(project, 'phase3/librelane/37-magic-finish/03-density/filled.gds',
                       b'native selected seal plus final density fill')
    final_state = put(project, 'phase3/librelane/37-magic-finish/03-density/state_out.json',
                      {'gds': str(final_native)})
    final = put(project, 'phase3/stage3/pnr/neutral.gds', final_native.read_bytes())
    handoff = {'gds_in': str(initial), 'gds_in_sha256': digest(initial),
               'sealed_gds': str(sealed), 'sealed_sha256': digest(sealed),
               'state': str(state), 'state_sha256': digest(state)}
    promotion = put(project, 'phase3/librelane/37-promotion.json', {
        'selection': 'magic', 'sealring': handoff,
        'streams': {'magic': str(initial)}, 'finished': {'magic': str(final_native)},
        'source': str(final_native), 'source_sha256': digest(final_native),
        'canonical': str(final),
        'canonical_sha256': digest(final)})
    return {'engine': 'magic', 'gds': final, 'state': final_state,
            'sealring': handoff, 'promotion': promotion, 'density_ratios': {}}


@pytest.mark.parametrize('defect,expected', [
    ('none', 'PASS'), ('missing_record', 'NOT_MEASURED'),
    ('changed_state', 'NOT_MEASURED'), ('wrong_selection', 'NOT_MEASURED'),
    ('consumer_fail', 'FAIL'),
])
def test_real_stream_owner_uses_selected_seal_and_keeps_the_final_fill(
        tmp_path, monkeypatch, defect, expected):
    # Exercise the selected-seal retained owner directly. Default now has
    # one primary stream; its cold/retry path has separate controls. The
    # selected seal must still be verified without changing the final fill.
    owner_inputs(tmp_path)
    result = selected_native_stream(tmp_path)
    final_before = result['gds'].read_bytes()
    pdk = SimpleNamespace(name='neutral', drc_deck=None)
    calls = []

    def verify_native(project, top, pdk, target, container, *, sealed_by, sealed_by_record):
        calls.append('existing sealed-by verification')
        assert target != result['gds']
        assert target.read_bytes() == b'native unsealed stream'
        assert sealed_by.read_bytes() == b'native selected seal'
        assert sealed_by_record == Path(result['sealring']['state'])
        put(project, 'phase3/stage3/pnr/die_finished.def', b'current finished die')
        put(project, 'reports/phase3/die_finishing.json', {
            'producer': 'die_finishing_gen',
            'seal_ring': {'state': 'FAIL' if defect == 'consumer_fail' else 'PASS'},
            'die_id': {'state': 'PRESENT'}})
        target.write_bytes(sealed_by.read_bytes())
        return True, 'neutral native verification boundary completed', 'PASS'

    monkeypatch.setattr(runner, '_die_finishing_native', verify_native)
    if defect == 'missing_record':
        result['promotion'].unlink()
    elif defect == 'changed_state':
        Path(result['sealring']['state']).write_text('{"gds":"changed"}')
    elif defect == 'wrong_selection':
        doc = json.loads(result['promotion'].read_text())
        doc['selection'] = 'klayout'
        result['promotion'].write_text(json.dumps(doc))
    started = time.time()
    produced = runner._step37_die_finishing_record(tmp_path, 'neutral', pdk, '', result)
    assert produced.status == ('PASS' if defect in ('none', 'consumer_fail') else 'NOT_MEASURED'), (
        'native verifier verdict stays separate from the retained child consumer')
    assert result['gds'].read_bytes() == final_before
    record = next(r for r in versions._read_index(tmp_path)['steps'] if r['id'] == '26.5ic')
    saved = json.loads((tmp_path / 'steps' / record['folder'] / 'outputs.json').read_text())
    assert saved['status'] == expected, saved.get('result')
    assert saved['canonical_pass'] is (expected == 'PASS')
    accepted = [row for row in versions.current_acceptance(tmp_path, started)
                if row['id'] == '26.5ic']
    assert (accepted[-1]['canonical_pass'] if accepted else False) is (expected == 'PASS')
    assert (accepted[-1]['status'] if accepted else produced.status) == expected
    assert bool(calls) is (defect in ('none', 'consumer_fail'))
    if expected == 'PASS':
        assert versions.latest_pass(tmp_path, '26.5ic')['canonical_pass'] is True
    else:
        assert versions.latest_pass(tmp_path, '26.5ic') is None


def test_actual_librelane_stream_selects_and_emits_the_same_seal_stage(tmp_path, monkeypatch):
    import librelane_step37 as stream
    import librelane_fill_dfm as fill
    import librelane_pv_signoff as pv

    route = put(tmp_path, 'route.def', b'current route')
    initial = put(tmp_path, 'initial.json', {'metrics': {}})
    configs = {sid: put(tmp_path, 'configs/' + sid + '.json', {}) for sid in stream.STEPS}
    monkeypatch.setattr(stream, 'resolve_step_configs', lambda *a, **k: configs)
    monkeypatch.setattr(stream, 'declaration_config', lambda *a:
                        ({'CORE_AREA': [1, 1, 9, 9]}, {'CORE_AREA': 'neutral owner declaration'}))
    monkeypatch.setattr(stream, '_routed_state', lambda *a: initial)
    monkeypatch.setattr(pv, 'tech_lef_overlay', lambda *a: {})

    def native(project, image, root, pdk, steps, state, lane, resolved):
        folders = []
        incoming = json.loads(Path(state).read_text())
        for i, sid in enumerate(steps):
            folder = project / 'phase3/librelane' / lane / str(i)
            payload = dict(incoming)
            payload['metrics'] = dict(payload.get('metrics', {}))
            if sid in ('Magic.StreamOut', 'KLayout.StreamOut'):
                gds = put(project, str(folder.relative_to(project) / 'stream.gds'), sid.encode())
                payload['gds'] = str(gds)
                if sid == 'Magic.StreamOut':
                    payload['mag_gds'] = str(gds)
                else:
                    payload['klayout_gds'] = str(gds)
            elif sid in ('KLayout.SealRing', 'KLayout.Filler'):
                body = Path(payload['gds']).read_bytes() + (' ' + sid).encode()
                gds = put(project, str(folder.relative_to(project) / 'finished.gds'), body)
                payload['gds'] = str(gds)
            put(project, str(folder.relative_to(project) / 'state_out.json'), payload)
            incoming = payload
            folders.append(folder)
        return folders

    monkeypatch.setattr(stream, '_run', native)
    monkeypatch.setattr(stream, 'judge_step', lambda folder, keys, *a, **k:
                        {'verdict': 'PASS', 'metrics': {key: {'value': 0} for key in keys}})
    monkeypatch.setattr(fill, 'measure_density_ratios', lambda *a: {})
    monkeypatch.setattr(pv, 'run_finishing_xor', lambda *a, **k: {'verdict': 'PASS'})
    monkeypatch.setattr(stream, '_vibeic_gds_gates', lambda *a:
                        {'substance': {'rc': 0, 'sha256': 'neutral'},
                         'port_labels': {'rc': 0, 'sha256': 'neutral'}})
    monkeypatch.setattr(stream, '_measured_drc', lambda *a, **k:
                        {'magic': 0, 'klayout': 0, 'total': 0})
    target = tmp_path / 'final.gds'
    result = stream.run(tmp_path, 'neutral image', tmp_path, 'neutral',
                        route, route, route, target)
    promotion = json.loads(result['promotion'].read_text())
    handoff = result['sealring']
    assert promotion['sealring'] == handoff
    assert promotion['streams'][result['engine']] == handoff['gds_in']
    assert json.loads(Path(handoff['state']).read_text())['gds'] == handoff['sealed_gds']
    assert digest(Path(handoff['state'])) == handoff['state_sha256']
    assert digest(Path(handoff['sealed_gds'])) == handoff['sealed_sha256']
    assert b'KLayout.SealRing' in Path(handoff['sealed_gds']).read_bytes()
    assert b'KLayout.Filler' not in Path(handoff['sealed_gds']).read_bytes()
    assert b'KLayout.Filler' in target.read_bytes()


def test_existing_native_adapter_adopts_the_seal_without_resealing(tmp_path, monkeypatch):
    import librelane_contract as ll

    target = put(tmp_path, 'verify/unsealed.gds', b'private unsealed copy')
    sealed = put(tmp_path, 'native/sealed.gds', b'current native seal')
    state = put(tmp_path, 'native/state_out.json', {'gds': str(sealed)})
    pdk = SimpleNamespace(name='neutral')
    monkeypatch.setattr(runner, '_pdk_dir_of', lambda *a: str(tmp_path / 'pdk/neutral'))
    monkeypatch.setattr(runner, 'declared_die_rect', lambda *a: ([0, 0, 100, 100], 'neutral owner'))
    monkeypatch.setattr(ll, 'selected_mode', lambda *a: 'librelane')

    def forbidden(*a):
        pytest.fail('already selected native seal must not run a second SealRing')

    monkeypatch.setattr(runner, '_librelane_sealring', forbidden)
    seen = []

    def verify(argv, **kwargs):
        seen.append(argv)
        assert argv[argv.index('--gds') + 1] == str(target)
        assert argv[argv.index('--sealed-by') + 1] == str(sealed)
        assert argv[argv.index('--sealed-by-record') + 1] == str(state)
        return SimpleNamespace(returncode=0, stdout='neutral existing verifier completed', stderr='')

    monkeypatch.setattr(runner._pr, 'run_best_effort', verify)
    result = runner._die_finishing_native(
        tmp_path, 'neutral', pdk, target, '', sealed_by=sealed, sealed_by_record=state)
    assert result[0] is True and result[2] == 'PASS'
    assert len(seen) == 1
    assert target.read_bytes() == b'private unsealed copy'
