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


def _native_case(project):
    import klayout.db as db
    fixtures = require_repo('vibe-ic-marketplace', 'plugins', 'vibe-ic', 'programs', 'tests', 'fixtures', 'flow109_density_fill')
    for name in ('tech.lef', 'routed.def'):
        _put(project, 'inputs/' + name, (fixtures / name).read_text())
    rules = json.loads((fixtures / 'rules.json').read_text())
    rules['layers']['unit_metal']['non-opc']['datatype'] = 4
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
