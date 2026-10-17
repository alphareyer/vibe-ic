"""Real Default finishing/readback; controlled native process writes, no IC claim."""
import json
import hashlib
import shutil
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import execution_policy as policy
import librelane_contract as LC
import librelane_fill_dfm as LF
import librelane_step37 as native37
import phase3_one_shot_runner as R
import step_output_collector as versions
import _default_backend_fixtures as BF
from test_default_finishing import subject, density_recipe, refresh_receipt, neutral_declared_windows


def _geometry(path, number=0):
    import klayout.db as db
    layout = db.Layout(); layout.dbu = 0.0005
    top = layout.create_cell('cal_pg')
    for layer in range(1, 6):
        top.shapes(layout.layer(layer, 0)).insert(db.Box(0, 0, 10000 + number, 10000))
    for name, x in [('a', 1000), ('y', 2000)]:
        top.shapes(layout.layer(2, 0)).insert(db.Text(name, db.Trans(x, 2000)))
    layout.write(str(path))


def _transport(tmp_path, monkeypatch, *, finished_geometry=False):
    neutral_declared_windows.__wrapped__(tmp_path, monkeypatch)
    monkeypatch.setattr(policy, 'request', lambda: {'mode': 'default'})
    monkeypatch.setattr(policy, '_ordinary_runtime', None)
    p, old, _ = subject(tmp_path)
    pnr = p / 'phase3/stage3/pnr'
    text = (pnr / 'routed.def').read_text()
    for name, x, direction in [('a', 1000, 'INPUT'), ('y', 2000, 'OUTPUT')]:
        text = text.replace(f'- {name} + NET {name} + DIRECTION {direction} + USE SIGNAL ;',
            f'- {name} + NET {name} + DIRECTION {direction} + USE SIGNAL + PORT + LAYER Metal2 ( -100 -100 ) ( 100 100 ) + PLACED ( {x} 2000 ) N ;')
    for name in ('routed.def', 'top.def'):
        (pnr / name).write_text(text)
    fill = BF.fill_record(p)
    BF.consume_fill(p, fill)
    LF.update_record(p, 'odb', {'shipped': 'librelane', 'tool': fill,
                              'def_sha256': LC.digest(pnr / 'routed.def')})
    BF.put(p / 'input/submission_template/tapeout_declaration.json', {'answers': {'deliverable': 'DIE'}})
    BF.put(p / 'input/submission_template/slots/neutral.yaml', {})
    # Begin with no old promotion: the first successful call below is real.
    (p / LF.DEFAULT_STREAM_REL).unlink()
    doc = json.loads((p / LF.RECORD_REL).read_text()); doc.pop('default_stream')
    BF.put(p / LF.RECORD_REL, doc)
    root = p / 'fixture_pdk'
    files = {}
    for flag, name in [('--input-lef', 'stream.lef'), ('--lyt', 'stream.lyt'),
                       ('--lyp', 'stream.lyp'), ('--lym', 'stream.lym')]:
        f = root / 'neutral' / name; f.write_text('SOURCE_FIXTURE ' + name + '\n')
        files[flag] = '/pdk/neutral/' + name
    native_script = '/native/stream_out.py'
    recipe_text = '# SOURCE_FIXTURE controlled native stream process\n'
    calls = []
    failed = {'stream': False}
    monkeypatch.setattr(R, '_librelane_step_ctx', lambda *a: (BF.IMAGE, root))

    def configs(project, image, pdk, ids, **kwargs):
        return {sid: p / 'phase3/librelane/34-config' / (sid + '.json') for sid in ids}
    monkeypatch.setattr(LF, 'resolve_step_configs', configs)

    def image_transport(argv, **kwargs):
        if 'get_script_dir' in argv[-1]:
            payload = {'path': native_script, 'text': recipe_text}
        else:
            paths = json.loads(argv[-1])
            payload = {path: hashlib.sha256(recipe_text.encode()).hexdigest() if path == native_script
                       else LC.digest(root / Path(path).relative_to('/pdk')) for path in paths}
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), '')
    monkeypatch.setattr(LF, 'run_container', image_transport)

    def stream(project, image, steps, **kwargs):
        calls.append('primary')
        if failed['stream']:
            raise LC.Refusal('LL_STEP_FAILED', 'controlled failed new native stream')
        assert [s[0] for s in steps] == ['KLayout.StreamOut']
        before = json.loads(Path(steps[0][2]).read_text())
        target = p / 'phase3/librelane/37-default-stream/01-klayout-streamout/top.gds'
        _geometry(target, len(calls))
        folder, _ = BF.producer(p, 'KLayout.StreamOut', before,
            {'def': before['def'], 'klayout_gds': str(target), 'metrics': {}},
            lane='37-default-stream/01-klayout-streamout', name='cal_pg')
        cfg = json.loads((folder / 'config.json').read_text())
        cfg.update(TECH_LEFS=[files['--input-lef']], KLAYOUT_TECH=files['--lyt'],
                   KLAYOUT_PROPERTIES=files['--lyp'], KLAYOUT_DEF_LAYER_MAP=files['--lym'])
        BF.put(folder / 'config.json', cfg); BF.put(steps[0][1], cfg)
        fp = json.loads((folder / 'input_fingerprint.json').read_text())
        fp.update(config=LC.digest(steps[0][1]), config_files=LC.config_file_hashes(cfg, [(root, '/pdk')]))
        BF.put(folder / 'input_fingerprint.json', fp)
        argv = ['python3', native_script, before['def'], '--output', str(target), '--top', 'cal_pg']
        for flag, value in files.items(): argv.extend([flag, value])
        (folder / 'COMMANDS').write_text(shlex.join(argv) + '\n')
        refresh_receipt(folder)
        return [folder]
    monkeypatch.setattr(LF, 'run_chain', stream)

    def seal(project, top, pdk, target, container):
        calls.append('seal')
        # This is the native process write seam. The original retained
        # finishing consumer, output contracts and report reader run below.
        BF.put(p / 'reports/phase3/die_finishing.json', {'producer': 'die_finishing_gen',
            'seal_ring': {'state': 'PASS', 'consumed_into': str(target.relative_to(p)),
                          'staged_sha256': LC.digest(target)},
            'die_id': {'state': 'NOT_APPLICABLE'}})
        (pnr / 'die_finished.def').write_bytes((pnr / 'routed.def').read_bytes())
        return True, 'controlled native seal measurement', 'PASS'
    monkeypatch.setattr(R, '_die_finishing_native', seal)

    def density(project, image, root, pdk, *, gds, lane, **kwargs):
        calls.append('filler-density')
        target = p / 'phase3/librelane/34-fill/01-klayout-filler/top.gds'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(gds, target)
        if finished_geometry:
            _geometry(target, 100)
        folder, _ = BF.producer(p, 'KLayout.Filler', {'gds': str(gds), 'metrics': {}},
            {'gds': str(target), 'metrics': {}}, lane='34-fill/01-klayout-filler')
        arm = BF.density_record(p, target, 0)
        density_recipe(p, arm)
        arm['filler'] = {'state': str(folder / 'state_out.json'), 'state_sha256': LC.digest(folder / 'state_out.json'),
            'gds_in': str(gds), 'gds_in_sha256': LC.digest(gds), 'filled_gds': str(target),
            'filled_sha256': LC.digest(target), 'script': 'SOURCE_FIXTURE native filler'}
        return arm
    monkeypatch.setattr(LF, 'run_density', density)

    def gate_process(argv, **kwargs):
        # Same official-image Python process boundary, actual gate programs.
        command = argv[argv.index('--skip') + 1:]
        return subprocess.run(command, capture_output=True, text=True, check=False)
    monkeypatch.setattr(native37, 'run_container', gate_process)
    return p, SimpleNamespace(name='neutral'), calls, failed


@pytest.mark.parametrize('retry', ['success', 'native_fail'])
def test_real_default_stream_retries_a_changed_route_and_retains_prior_epoch(tmp_path, monkeypatch, retry):
    p, pdk, calls, failed = _transport(tmp_path, monkeypatch)
    first = LF.default_stream(p, 'top', pdk, '')
    first_bytes = (p / LF.DEFAULT_STREAM_REL).read_bytes()
    first_stream = Path(first['stream']).read_bytes()
    assert LF.validate_default_stream(p)['canonical_sha256'] == LC.digest(Path(first['canonical']))
    LF.default_stream(p, 'top', pdk, '')
    assert calls == ['primary', 'seal', 'filler-density'], calls
    routed = p / 'phase3/stage3/pnr/routed.def'
    routed.write_text(routed.read_text() + '\n# legitimate new route epoch\n')
    # The existing fill producer must bind the new route before streaming it.
    def refill(project, top, pdk, container, target, notes):
        record = BF.fill_record(p, subject=routed)
        BF.consume_fill(p, record)
        LF.update_record(p, 'odb', {'shipped': 'librelane', 'tool': record,
                                  'def_sha256': LC.digest(routed)})
        return True
    monkeypatch.setattr(R, '_emit_metal_fill', refill)
    failed['stream'] = retry == 'native_fail'
    if failed['stream']:
        with pytest.raises(LC.Refusal, match='controlled failed new native stream'):
            LF.default_stream(p, 'top', pdk, '')
        assert calls.count('primary') == 2
        assert (p / LF.DEFAULT_STREAM_REL).read_bytes() == first_bytes
        with pytest.raises((LC.Refusal, ValueError, KeyError)):
            LF.validate_default_stream(p)
    else:
        second = LF.default_stream(p, 'top', pdk, '')
        assert calls == ['primary', 'seal', 'filler-density'] * 2
        assert Path(second['stream']).read_bytes() != first_stream
        assert LF.validate_default_stream(p) == json.loads((p / LF.DEFAULT_STREAM_REL).read_text())
        assert versions.latest_pass(p, '26.5ic')['current_inputs']
    old = []
    for before in versions._versions_root(p).glob('*/*/*/before/outputs.json'):
        old.extend((before.parent / row['snapshot']).read_bytes()
                   for row in json.loads(before.read_text()).get('outputs', [])
                   if row['rel'] == LF.DEFAULT_STREAM_REL)
    assert first_bytes in old, 'prior promotion must remain in existing before history'


@pytest.mark.parametrize('damage', ['current', 'wrong_def', 'changed_filled',
                                    'changed_material', 'missing_readset', 'changed_recipe'])
def test_actual_373_owner_only_reaches_transport_with_current_bound_filled_def(
        tmp_path, monkeypatch, damage):
    import _native373_current as native
    import gds_xor_check as xor
    import librelane_pv_signoff as pv
    p, pdk, calls, _ = _transport(tmp_path, monkeypatch)
    record = LF.default_stream(p, 'top', pdk, '')
    pnr = p / 'phase3/stage3/pnr'
    filled = pnr / 'filled.def'
    (pnr / 'cal_pg_pnr.v').write_text('module cal_pg; endmodule\n')
    (pnr / 'constraint.sdc').write_text('create_clock -period 10 [get_ports clk]\n')
    BF.put(p / 'reports/phase3/technology_units.json', {'pdk': 'neutral'})
    root = p / 'fixture_pdk'
    monkeypatch.setattr(LC, 'pdk_root_resolution', lambda *a, **k: {'path': str(root)})
    monkeypatch.setattr(pv, 'tech_lef_overlay', lambda *a: {})
    observed = []
    class ReachedNativeTransport(Exception):
        pass
    def configurations(project, image, pdk, steps, **kwargs):
        return {step: p / 'phase3/librelane/34-config' / (step + '.json') for step in steps}
    def bridge(project, image, config, views, *args, **kwargs):
        observed.append({name: str(path) for name, path in views.items()})
        assert kwargs['mounts'] == [(root, '/pdk')]
        raise ReachedNativeTransport
    monkeypatch.setattr(LC, 'resolve_step_configs', configurations)
    monkeypatch.setattr(LC, 'state_from_direct', bridge)
    if damage == 'changed_filled': filled.write_text(filled.read_text() + '\n# changed current source\n')
    elif damage == 'changed_material': (root / 'neutral/stream.lef').write_text('changed actual native readset\n')
    elif damage == 'missing_readset': (pnr / 'top.stream_inputs.json').unlink()
    elif damage == 'changed_recipe': (pnr / 'stream_out.py').write_text('changed actual recipe\n')
    if damage == 'current':
        shipped, source, attestation = xor.shipped_and_source(p)
        assert source == filled and source != pnr / 'routed.def'
        assert shipped == Path(record['canonical'])
        with pytest.raises(ReachedNativeTransport): native.produce(p, source)
        assert observed == [{'def': str(filled), 'nl': str(pnr / 'cal_pg_pnr.v'),
                             'sdc': str(pnr / 'constraint.sdc')}]
    else:
        with pytest.raises((LC.Refusal, OSError, ValueError, KeyError)):
            native.produce(p, pnr / 'routed.def' if damage == 'wrong_def' else filled)
        assert observed == []
    assert calls == ['primary', 'seal', 'filler-density']


@pytest.mark.parametrize('boundary', ['recipe', 'inputs'])
def test_default_stream_metadata_probes_reach_the_real_execution_contract(
        tmp_path, monkeypatch, boundary):
    """Real caller/guard/current reader; only the metadata process is controlled.

    Both queries precede publication of the bound stream readset. The actual
    stream/fill process seams retain the existing neutral fixture; no EDA runs.
    """
    p, pdk, calls, _ = _transport(tmp_path, monkeypatch)
    metadata_process = LF.run_container
    observed = []
    guarded = []

    def local_metadata(argv, *, probe_deadline_s, supervised, **kwargs):
        assert probe_deadline_s == LC.PROBE_DEADLINE_S
        assert supervised is False
        guarded.append(boundary)
        return metadata_process(argv)

    def metadata_boundary(argv, **kwargs):
        query = 'recipe' if 'get_script_dir' in argv[-1] else 'inputs'
        observed.append(query)
        if query == boundary:
            return LC.run_container(argv, **kwargs)
        return metadata_process(argv, **kwargs)

    monkeypatch.setattr(LC, '_provider_for_image', lambda image: object())
    monkeypatch.setattr(LC, '_provider_route_mismatch', lambda image: None)
    monkeypatch.setattr(LC, '_run_local', local_metadata)
    monkeypatch.setattr(LF, 'run_container', metadata_boundary)
    record = LF.default_stream(p, 'top', pdk, '')
    assert observed == ['recipe', 'inputs']
    assert guarded == [boundary]
    assert calls == ['primary', 'seal', 'filler-density']
    assert LF.validate_default_stream(p) == json.loads(
        (p / LF.DEFAULT_STREAM_REL).read_text())
    assert LC.digest(Path(record['canonical'])) == record['canonical_sha256']
    readset = json.loads((p / 'phase3/stage3/pnr/top.stream_inputs.json').read_text())
    assert readset['native_cli']['inputs']
    assert Path(record['canonical']).stat().st_size > 0
