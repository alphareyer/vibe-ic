"""Actual D62 ODB -> native LL stream-out geometry, never GDS hand repair.

Synthetic source inputs and qualification records are developer controls,
not production PDK/fill/finishing qualification. No benchmark inputs are read.
"""
from __future__ import annotations
import copy
import json
from pathlib import Path
import shutil
import subprocess

import pytest
import flow109
import flow109_admission as admission
import flow109_density_fill as density
import librelane_fill_stream as stream
from librelane_contract import Refusal
from programs.tests._hostpaths import require_repo


def _put(project, name, value):
    path = project / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value))
    return stream._record(project, path)


def _native_case(project, *, fill_width=1.0):
    import klayout.db as db
    fixtures = require_repo('vibe-ic-marketplace', 'plugins', 'vibe-ic', 'programs', 'tests', 'fixtures', 'flow109_density_fill')
    for name in ('tech.lef', 'routed.def'):
        _put(project, 'inputs/' + name, (fixtures / name).read_text())
    rules = json.loads((fixtures / 'rules.json').read_text())
    rules['layers']['unit_metal']['non-opc']['datatype'] = 4
    rules['layers']['unit_metal']['non-opc']['width'] = [fill_width]
    rule_ref = _put(project, 'inputs/rules.json', rules)
    init = project / 'initialize.tcl'
    init.write_text('read_lef inputs/tech.lef\nread_def inputs/routed.def\nwrite_db inputs/input.odb\n')
    native = subprocess.run(['openroad', '-no_init', '-exit', str(init)], cwd=project, capture_output=True, text=True)
    assert native.returncode == 0, native.stdout + native.stderr
    pdk = _put(project, 'inputs/pdk.json', {'scope': 'synthetic developer geometry control only'})
    manifest = {'schema': density.SCHEMA, 'input_odb': stream._record(project, project / 'inputs/input.odb'),
                'rules': rule_ref, 'pdk': pdk}
    _put(project, density.INPUT, manifest)
    operation = _put(project, 'inputs/operation.json', {'schema': density.SCHEMA, 'operation': 'density_fill', 'area': 'core'})
    features = next(p['features'] for p in flow109.load_registry()['reference_profiles'] if p['name'] == 'Small digital IC without DFT')
    plan = flow109.resolve_plan(features)
    use = flow109.resolve_step(plan, 'D62')['tool_uses'][0]
    executable = stream._record(project, Path(shutil.which('openroad')).resolve())
    identity = {'use_id': use['use_id'], 'tool_id': use['tool_id'], 'version': 'native developer geometry control',
        'executable': executable, 'build_sha256': executable['sha256'], 'config': operation, 'pdk': pdk,
        'deck': rule_ref, 'corner': 'synthetic_geometry', 'semantics': {'purpose': use['purpose'],
            'native_steps': use['native_steps'], 'operation_sources': density.operation_sources()}}
    qualified = {'use_id': use['use_id'], 'status': 'QUALIFIED', 'identity': identity,
        'reference_evidence': [_put(project, 'inputs/qualification-control.json',
            {'scope': 'developer contract controls, not real PDK qualification'})]}
    _put(project, 'qualifications.json', {'schema': 'vibeic.flow109.qualifications.v1', 'uses': [qualified]})
    context = flow109.execution_context(plan, 'D62', 'D62', runtime_tool_uses=[{**identity,
        'qualification_sha256': admission.canonical_sha(qualified)}], qualification_registry='qualifications.json')
    result = density.execute(project, context)
    assert result['component_status'] == 'PASS', result
    d62 = stream._record(project, project / result['receipt']['path'])
    tech = db.Technology(); tech.name = 'fill-stream-control'; tech.dbu = 0.001
    tech.save(str(project / 'inputs/control.lyt'))
    seal = db.Layout(); seal.dbu = 0.001
    cell = seal.create_cell('synthetic_seal_source')
    cell.shapes(seal.layer(99, 0)).insert(db.Box(-1000, -1000, 21000, 21000))
    seal.write(str(project / 'inputs/seal-source.gds'))
    inputs = {'base_map': _put(project, 'inputs/base.map',
        'M1 NET,SPNET,PIN,VIA 1 0\nNAME M1/LABEL 1 10\nDIEAREA ALL 0 0\n'),
        'lyt': stream._record(project, project / 'inputs/control.lyt'),
        'lyp': _put(project, 'inputs/control.lyp', '<layer-properties/>'),
        'lefs': [stream._record(project, project / 'inputs/tech.lef')],
        'gds_files': [], 'seal_gds': stream._record(project, project / 'inputs/seal-source.gds')}
    return d62, inputs


def test_native_filled_odb_enters_gds_with_exact_purpose_and_preserved_core_seal_hierarchy(tmp_path):
    import klayout.db as db
    d62, inputs = _native_case(tmp_path)
    calls = []
    ref = stream.stream_d62(tmp_path, d62, inputs, tmp_path / 'stream', before_launch=lambda: calls.append('native'))
    record = json.loads((tmp_path / ref['path']).read_text())
    result = stream.validate_bridge(tmp_path, ref)
    assert calls == ['native', 'native', 'native', 'native']
    assert result['odb'] == json.loads((tmp_path / d62['path']).read_text())['outputs']['filled.odb']
    assert result['d62_receipt'] == d62
    assert result['geometry_proof']['fill_count'] > 0
    assert result['geometry_proof']['layers'] == {'M1': [1, 4]}
    assert result['geometry_proof']['checked_cells'] == 2
    assert result['geometry_proof']['nonfill_geometry_preserved']
    assert result['geometry_proof']['hierarchy_preserved']
    assert result['final_geometry_adoption'] == 'NOT_MEASURED'
    layout = db.Layout(); layout.read(str(tmp_path / result['gds']['path']))
    top = layout.cell('fill_smoke')
    fill_cell = layout.cell(result['geometry_proof']['native_fill_cells'][0])
    assert db.Region(fill_cell.shapes(layout.layer(1, 4))).count() == result['geometry_proof']['fill_count']
    assert any(i.cell.name == fill_cell.name for i in top.each_inst())
    assert layout.cell('synthetic_seal_source') is not None
    assert any(i.cell.name == 'synthetic_seal_source' for i in top.each_inst())
    assert stream._record(tmp_path, tmp_path / inputs['seal_gds']['path']) == inputs['seal_gds']
    assert 'M1 FILL 1 4' in (tmp_path / record['outputs']['fill.map']['path']).read_text()
    assert 'FILL' not in (tmp_path / inputs['base_map']['path']).read_text()


def test_original_native_streamer_rc_zero_silently_drops_fills_and_consumer_rejects(tmp_path):
    d62, inputs = _native_case(tmp_path)
    ref = stream.stream_d62(tmp_path, d62, inputs, tmp_path / 'good', before_launch=lambda: None)
    record = json.loads((tmp_path / ref['path']).read_text())
    source = json.loads((tmp_path / d62['path']).read_text())['outputs']['filled.def']
    paths = {k: stream._bound(tmp_path, inputs[k]) for k in ('lyt', 'lyp', 'base_map', 'seal_gds')}
    paths.update(lefs=[stream._bound(tmp_path, p) for p in inputs['lefs']], gds_files=[])
    output = tmp_path / 'unfixed.gds'
    cmd = stream._stream_command(paths, stream._bound(tmp_path, source), output, paths['base_map'])
    cmd[1] = str(stream.native_script())
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0 and output.is_file()
    assert "No mapping for layer 'M1', purpose 'FILL'" in proc.stdout + proc.stderr
    with pytest.raises(Refusal, match='FILL_STREAM_FILL_GEOMETRY_MISMATCH'):
        stream.geometry_proof(stream._bound(tmp_path, source), stream._bound(tmp_path, record['outputs']['bare.gds']),
                              output, record['mapping']['layer_map'])


@pytest.mark.parametrize('damage', ['missing_route_map', 'ambiguous_rule', 'masked_def'])
def test_unresolved_mapping_or_masks_refuse_before_stream_launch(tmp_path, damage):
    d62, inputs = _native_case(tmp_path)
    source = json.loads((tmp_path / d62['path']).read_text())
    filled = stream._bound(tmp_path, source['outputs']['filled.def'])
    base, rules = stream._bound(tmp_path, inputs['base_map']), stream._bound(tmp_path, source['inputs']['rules'])
    if damage == 'missing_route_map':
        base.write_text('DIEAREA ALL 0 0\n')
    elif damage == 'ambiguous_rule':
        doc = json.loads(rules.read_text());doc['layers']['unit_metal']['non-opc']['datatype'] = [4, 5]
        rules.write_text(json.dumps(doc))
    else:
        filled.write_text(filled.read_text().replace('LAYER M1 RECT', 'LAYER M1 + MASK 1 RECT'))
    with pytest.raises(Refusal, match='FILL_STREAM_'):
        stream.derive_map(base, rules, filled, tmp_path / 'derived.map')
    assert not (tmp_path / 'derived.map').exists()


def test_changed_producer_rules_refuse_before_new_native_stream(tmp_path):
    d62, inputs = _native_case(tmp_path)
    (tmp_path / 'inputs/rules.json').write_text('{}')
    calls = []
    with pytest.raises(admission.AdmissionRefusal, match='STALE|CHANGED'):
        stream.stream_d62(tmp_path, d62, inputs, tmp_path / 'bad', before_launch=lambda: calls.append('native'))
    assert calls == []


def test_existing_authoritative_fill_map_works_without_109_or_d62_identity(tmp_path):
    d62, inputs = _native_case(tmp_path)
    source = json.loads((tmp_path / d62['path']).read_text())
    inputs['base_map'] = _put(tmp_path, 'inputs/legacy.map', 'M1 NET,SPNET,PIN,VIA 1 0\nM1 FILL 1 4\nDIEAREA ALL 0 0\n')
    ref = stream.stream_component(tmp_path, source['outputs']['filled.def'], None, inputs,
        tmp_path / 'legacy', before_launch=lambda: None)
    result = stream.validate_bridge(tmp_path, ref)
    assert result['geometry_proof']['fill_geometry_matches_def']
    assert 'd62_receipt' not in result
    record = json.loads((tmp_path / ref['path']).read_text())
    assert record['mapping']['added_purposes'] == []


def test_wrong_gds_or_changed_rule_after_publication_is_not_reusable(tmp_path):
    d62, inputs = _native_case(tmp_path)
    ref = stream.stream_d62(tmp_path, d62, inputs, tmp_path / 'stream', before_launch=lambda: None)
    record = json.loads((tmp_path / ref['path']).read_text())
    target = stream._bound(tmp_path, record['outputs']['filled.gds'])
    shutil.copyfile(stream._bound(tmp_path, record['outputs']['bare.gds']), target)
    with pytest.raises(Refusal, match='FILL_STREAM_INPUT_CHANGED'):
        stream.validate_bridge(tmp_path, ref)


def test_unknown_native_script_refuses_instead_of_patching_unreviewed_source():
    with pytest.raises(Refusal, match='FILL_STREAM_NATIVE_VERSION_UNSUPPORTED'):
        stream.patched_native_source('print("unreviewed")\n')


@pytest.mark.parametrize('damage', ['transform', 'extra_shape'])
def test_reader_fill_cell_does_not_authorize_changed_position_or_other_geometry(tmp_path, damage):
    import klayout.db as db
    parsed = {'units': 1000, 'top': 'neutral', 'shapes': [{'layer': 'M', 'rect': [1000, 2000, 3000, 4000]}]}
    bare = db.Layout(); bare.dbu = 0.001; bare.create_cell('neutral')
    filled = db.Layout(); filled.dbu = 0.001; top = filled.create_cell('neutral')
    child = filled.create_cell('reader_created_geometry')
    child.shapes(filled.layer(10, 4)).insert(db.Box(1000, 2000, 3000, 4000))
    if damage == 'extra_shape':
        child.shapes(filled.layer(10, 0)).insert(db.Box(0, 0, 100, 100))
    top.insert(db.CellInstArray(child.cell_index(), db.Trans(10, 0) if damage == 'transform' else db.Trans()))
    with pytest.raises(Refusal, match='FILL_STREAM_NATIVE_CELL_TRANSFORM|FILL_STREAM_FILL_GEOMETRY_MISMATCH'):
        stream._native_fill_child(bare, filled, parsed, {'M': [10, 4]})


def test_native_librelane_subclass_retains_state_and_executes_verified_fill_producer(tmp_path):
    import sys
    sys.path.insert(0, str(stream.HERE / 'librelane_plugins'))
    from librelane_plugin_vibeic.fill_stream import FillAwareStreamOut
    from librelane.config import Config
    from librelane.state import State, DesignFormat
    from librelane.common import Path as LLPath
    d62, inputs = _native_case(tmp_path)
    source = json.loads((tmp_path / d62['path']).read_text())
    filled = stream._bound(tmp_path, source['outputs']['filled.def'])
    mapping = tmp_path / 'inputs/derived.map'
    stream.derive_map(stream._bound(tmp_path, inputs['base_map']),
                      stream._bound(tmp_path, source['inputs']['rules']), filled, mapping)
    config, _ = Config.load({'DESIGN_NAME': 'fill_smoke', 'PDK': 'gf180mcuD',
        'STD_CELL_LIBRARY': 'gf180mcu_fd_sc_mcu7t5v0',
        'TECH_LEFS': {'nom_*': str(tmp_path / 'inputs/tech.lef')},
        'CELL_LEFS': [], 'CELL_GDS': [], 'PRIMARY_GDSII_STREAMOUT_TOOL': 'klayout',
        'KLAYOUT_TECH': str(tmp_path / 'inputs/control.lyt'),
        'KLAYOUT_PROPERTIES': str(tmp_path / 'inputs/control.lyp'),
        'KLAYOUT_DEF_LAYER_MAP': str(mapping)}, FillAwareStreamOut.get_all_config_variables(),
        design_dir=str(tmp_path), pdk_root='/foss/pdks')
    step = FillAwareStreamOut(config=config, state_in=State({DesignFormat.DEF: LLPath(str(filled))}))
    folder = tmp_path / 'native-step'
    state = step.start(step_dir=str(folder))
    gds = Path(state[DesignFormat.KLAYOUT_GDS])
    result = stream.validate_native_stage(folder, filled, gds)
    assert result['geometry_proof']['fill_count'] == source['measurements']['fill_count_after']
    assert state[DesignFormat.GDS] == str(folder / 'fill_smoke.gds')
    command_text = (folder / 'COMMANDS').read_text()
    assert str(stream.HERE / 'librelane_fill_stream_native.py') in command_text
    assert 'fill-proof-bare.def' in command_text and str(filled) in command_text


def test_default_stream_selects_fill_producer_only_from_actual_fills_and_bound_rule_authority(tmp_path, monkeypatch):
    import librelane_fill_dfm as finishing
    d62, inputs = _native_case(tmp_path)
    source = json.loads((tmp_path / d62['path']).read_text())
    filled = stream._bound(tmp_path, source['outputs']['filled.def'])
    config_path = tmp_path / 'base-config.json'
    config_path.write_text(json.dumps({'KLAYOUT_DEF_LAYER_MAP': str(tmp_path / inputs['base_map']['path'])}))
    configs = {'KLayout.StreamOut': config_path}
    calls = []
    def resolve(project, image, pdk, steps, **kwargs):
        calls.append((steps, kwargs))
        assert steps == [finishing.FILL_STREAM_STEP]
        derived = Path(kwargs['overlay']['KLAYOUT_DEF_LAYER_MAP'][0])
        assert 'M1 FILL 1 4' in derived.read_text()
        return {finishing.FILL_STREAM_STEP: derived}
    monkeypatch.setattr(finishing, 'resolve_step_configs', resolve)
    _put(tmp_path, 'reports/flow109/components/D62.json', {'receipt': d62})
    (tmp_path / 'phase3/librelane/34-config').mkdir(parents=True)
    selected, _ = finishing._select_fill_stream(tmp_path, 'test_image', tmp_path, 'test_pdk', filled, configs)
    assert selected == finishing.FILL_STREAM_STEP and len(calls) == 1
    before = stream._bound(tmp_path, source['outputs']['before.def'])
    assert finishing._select_fill_stream(tmp_path, 'test_image', tmp_path, 'test_pdk', before, configs) == ('KLayout.StreamOut', configs)
    assert len(calls) == 1
    filled.write_text(filled.read_text() + '\n# changed after producer\n')
    with pytest.raises(admission.AdmissionRefusal, match='STALE'):
        finishing._select_fill_stream(tmp_path, 'test_image', tmp_path, 'test_pdk', filled, configs)
    assert len(calls) == 1


@pytest.mark.parametrize('changed', ['base_map', 'rules', 'filled_def'])
def test_default_fill_mapping_reuse_keeps_original_rule_and_pdk_authority(tmp_path, monkeypatch, changed):
    import librelane_fill_dfm as finishing
    d62, inputs = _native_case(tmp_path)
    source = json.loads((tmp_path / d62['path']).read_text())
    filled = stream._bound(tmp_path, source['outputs']['filled.def'])
    base = stream._bound(tmp_path, inputs['base_map'])
    cfg = tmp_path / 'config.json'; cfg.write_text(json.dumps({'KLAYOUT_DEF_LAYER_MAP': str(base)}))
    _put(tmp_path, 'reports/flow109/components/D62.json', {'receipt': d62})
    (tmp_path / 'phase3/librelane/34-config').mkdir(parents=True)
    monkeypatch.setattr(finishing, 'resolve_step_configs', lambda *a, **k: {finishing.FILL_STREAM_STEP: cfg})
    finishing._select_fill_stream(tmp_path, 'image', tmp_path, 'neutral', filled, {'KLayout.StreamOut': cfg})
    authority = tmp_path / 'phase3/librelane/34-config/explicit-fill.authority.json'
    record = {'fill_mapping_authority': str(authority), 'fill_mapping_authority_sha256': stream._sha(authority),
              'filled_def': str(filled)}
    finishing._validate_fill_stream_authority(tmp_path, record)
    target = {'base_map': base, 'rules': stream._bound(tmp_path, source['inputs']['rules']), 'filled_def': filled}[changed]
    target.write_text(target.read_text() + '\n')
    with pytest.raises(Refusal, match='LL_FILL_STREAM_AUTHORITY_CHANGED|LL_FILL_STREAM_WRONG_D62_SUBJECT'):
        finishing._validate_fill_stream_authority(tmp_path, record)


def test_historical_stream_receipt_cannot_adopt_actual_explicit_fills(tmp_path):
    import librelane_fill_dfm as finishing
    d62, _ = _native_case(tmp_path)
    source = json.loads((tmp_path / d62['path']).read_text())
    filled = stream._bound(tmp_path, source['outputs']['filled.def'])
    with pytest.raises(Refusal, match='LL_FILL_STREAM_LEGACY_LOSSY_RECEIPT'):
        finishing._retained_stream_step({'filled_def': str(filled)})
    assert finishing._retained_stream_step({'filled_def': str(filled),
        'stream_step': finishing.FILL_STREAM_STEP}) == finishing.FILL_STREAM_STEP
    before = stream._bound(tmp_path, source['outputs']['before.def'])
    assert finishing._retained_stream_step({'filled_def': str(before)}) == 'KLayout.StreamOut'


def test_two_valid_native_d62_bridges_cannot_splice_gds_and_unrelated_odb(tmp_path, monkeypatch):
    # Absolute typed references are supported by the real readers. Only their
    # serialization changes here so both independently produced project roots
    # can be consumed together; no measured tool output/proof is mocked.
    absolute = lambda project, path: {'path': str(Path(path).resolve()), 'sha256': stream._sha(path)}
    monkeypatch.setattr(stream, '_record', absolute)
    monkeypatch.setattr(density, '_record', absolute)
    cases = []
    for name, width in [('a', 1.0), ('b', 2.0)]:
        project = tmp_path / name; project.mkdir()
        d62, inputs = _native_case(project, fill_width=width)
        bridge = stream.stream_d62(project, d62, inputs, project / 'native-stream', before_launch=lambda: None)
        valid = stream.validate_bridge(project, bridge)
        assert density.validate_filled_odb(project, d62)['odb'] == valid['odb']
        cases.append((project, d62, bridge, valid))
    a, b = cases
    assert a[3]['geometry_proof']['fill_count'] != b[3]['geometry_proof']['fill_count']
    assert a[3]['gds']['sha256'] != b[3]['gds']['sha256']
    # Keep all A source geometry, native logs and proof intact, while naming
    # B's separately valid ODB/D62. Two independent PASS chains are not one.
    forged = json.loads(stream._bound(a[0], a[2]).read_text())
    forged.update(d62_receipt=b[1], odb=b[3]['odb'])
    ref = _put(tmp_path, 'spliced-d62-odb.json', forged)
    with pytest.raises(Refusal, match='FILL_STREAM_D62_CONSUMED_SOURCE_MISMATCH'):
        stream.validate_bridge(tmp_path, ref)
    # Rules are a consumed producer input too, not an interchangeable valid file.
    forged = json.loads(stream._bound(a[0], a[2]).read_text())
    forged['fill_rules'] = json.loads(stream._bound(b[0], b[1]).read_text())['inputs']['rules']
    ref = _put(tmp_path, 'spliced-rules.json', forged)
    with pytest.raises(Refusal, match='FILL_STREAM_D62_CONSUMED_SOURCE_MISMATCH'):
        stream.validate_bridge(tmp_path, ref)
    assert stream.validate_bridge(a[0], a[2])['odb'] == a[3]['odb']
    assert stream.validate_bridge(b[0], b[2])['odb'] == b[3]['odb']


def test_reviewed_native_clearer_retains_exact_fill_and_rejects_shape_reverse(monkeypatch):
    import ast
    import hashlib
    source = stream.native_script().read_text()
    assert stream.patched_native_source(source) == source
    assert stream.operation_sources()['native/librelane/scripts/klayout/stream_out.py'] == hashlib.sha256(source.encode()).hexdigest()
    tree = ast.parse(source)
    clearer = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'should_clear_cell')
    namespace = {}
    exec(compile(ast.Module(body=[clearer], type_ignores=[]), '<native-clearer>', 'exec'), namespace)
    clear = namespace['should_clear_cell']
    assert not clear('neutral', 'neutral', 0, 0)
    assert not clear('VIA_generated', 'neutral', 0, 1)
    assert not clear('neutral_DEF_FILL', 'neutral', 0, 2)
    assert clear('foreign_DEF_FILL', 'neutral', 0, 3)
    assert clear('ordinary_macro', 'neutral', 0, 4)
    damaged = source.replace('if cell_name == f"{design_name}_DEF_FILL":', 'if False:')
    # Even a mistakenly updated digest must not authorize changed clearing logic.
    monkeypatch.setattr(stream, 'NATIVE_FILL_STREAM_SHA256', hashlib.sha256(damaged.encode()).hexdigest())
    with pytest.raises(Refusal, match='FILL_STREAM_NATIVE_VERSION_UNSUPPORTED'):
        stream.patched_native_source(damaged)
