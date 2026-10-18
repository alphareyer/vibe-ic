"""Synthetic source controls, not native execution or physical qualification."""
import json
import shlex
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _gds_geometry as GEOM
import _stream_input_record as SIR
import _tapeout_declaration as TD
import _owner_declared as OD
import general_precheck as GP
import librelane_contract as LC
import librelane_fill_dfm as LF
import test_general_precheck as G
from _default_backend_fixtures import producer, put


def _gds(path, *, text_type=9, text_layer=77, physical=False, text=True):
    boundaries = [(10, 0, G._rect(0, 0, 100000, 80000))]
    if physical:
        boundaries.append((77, 9, G._rect(100, 100, 200, 200)))
    G.write_gds(path, {'chip_top': {'boundaries': boundaries}})
    if text:
        label = (G._rec(0x0C, 0) + G._rec(0x0D, 2, struct.pack('>h', text_layer))
                 + G._rec(0x16, 2, struct.pack('>h', text_type))
                 + G._rec(0x10, 3, G._xy([(5, 5)]))
                 + G._rec(0x19, 6, G._ascii('synthetic library label'))
                 + G._rec(0x11, 0))
        raw = path.read_bytes()
        path.write_bytes(raw[:-8] + label + raw[-8:])
    return path


def _rebind_receipt(folder, fp):
    put(folder / 'input_fingerprint.json', fp)
    hashes = {str(p.relative_to(folder)): LC.digest(p) for p in folder.iterdir()
              if p.is_file() and p.name != 'vibeic_receipt.json'}
    put(folder / 'vibeic_receipt.json', {'input': fp, 'sha256': hashes})


def _project(tmp_path, monkeypatch, *, text_type=9, text_layer=77,
             physical=False, forbidden=None, text=True):
    project = tmp_path / 'project'
    candidate = _gds(project / 'phase3/stage4/gds/chip_top.gds',
                     text_type=text_type, text_layer=text_layer,
                     physical=physical, text=text)
    doc, ignored = TD.merge_answers(TD.blank_declaration(), {
        'deliverable': 'DIE', 'top_cell': 'chip_top', 'die_origin_um': [0, 0],
        'die_area_um': [0, 0, 100, 80], 'database_unit_um': 0.001,
        'forbidden_layers': forbidden or []})
    assert not ignored
    OD.attest(doc)
    put(project / TD.DECLARATION_REL, doc)
    filled = project / 'phase3/stage3/pnr/filled.def'
    filled.parent.mkdir(parents=True)
    filled.write_text('SOURCE_FIXTURE independent filled DEF; no native execution\n')
    folder = project / 'phase3/librelane/37-default-stream/01-klayout-streamout'
    stream = _gds(folder / 'chip_top.gds', text_type=text_type)
    folder, _ = producer(project, 'KLayout.StreamOut',
                          {'def': str(filled), 'metrics': {}},
                          {'klayout_gds': str(stream), 'metrics': {}},
                          lane='37-default-stream/01-klayout-streamout', name='chip_top')
    root = project / 'fixture_pdk'
    volume = root / 'neutral'
    library = _gds(volume / 'gds/views.gds', text_type=text_type)
    tech = volume / 'libs.tech/klayout/tech/process.lyp'
    tech.parent.mkdir(parents=True)
    tech.write_text('<layer-properties><properties><source>10/0@1</source>'
                    '</properties></layer-properties>')
    guest = '/pdk/neutral/gds/views.gds'
    config = project / 'phase3/librelane/34-config/KLayout.StreamOut.json'
    raw = json.loads(config.read_text())
    raw['EXTRA_GDS_FILES'] = [guest]
    put(config, raw)
    put(folder / 'config.json', raw)
    fp = json.loads((folder / 'input_fingerprint.json').read_text())
    fp['config'] = LC.digest(config)
    fp['config_files'] = {guest: LC.digest(library)}
    recipe = project / 'phase3/stage3/pnr/stream_out.py'
    recipe.write_text('# SOURCE_FIXTURE: not executed\n')
    argv = ['python3', str(recipe), str(filled), '--with-gds-file', guest]
    commands = folder / 'COMMANDS'
    commands.write_text(shlex.join(argv) + '\n')
    _rebind_receipt(folder, fp)
    pnr = _gds(project / 'phase3/stage3/pnr/chip_top.gds', text_type=text_type)
    inputs = SIR.build(engine='klayout', output=pnr, top='chip_top', project=project,
        project_c=str(project), recipe=recipe, def_file=filled,
        files={}, scalars={}, rcfile=None, hashes={})
    # The native role is deliberately absent from the generic CELL_GDS projection.
    assert inputs['inputs']['CELL_GDS'] == []
    inputs['native_cli'] = {'argv': argv, 'commands': str(commands),
        'commands_sha256': LC.digest(commands),
        'inputs': [{'argv_index': 4, 'path': guest, 'sha256': LC.digest(library)}]}
    inputs_path = SIR.write(pnr, inputs)
    record = {'canonical': str(candidate), 'canonical_sha256': LC.digest(candidate),
        'source_sha256': LC.digest(candidate), 'stream': str(stream),
        'stream_state': str(folder / 'state_out.json'),
        'stream_state_sha256': LC.digest(folder / 'state_out.json'),
        'image': fp['image'], 'pnr_gds': str(pnr), 'file_top': 'chip_top',
        'design_name': 'chip_top', 'stream_inputs_sha256': LC.digest(inputs_path)}
    put(project / LF.DEFAULT_STREAM_REL, record)
    put(project / LF.RECORD_REL, {'default_stream': record})
    switch = json.loads((project / 'phase3/librelane_switch.json').read_text())
    switch['steps'] = {step: 'direct' for step in ('26', '31', '34', '37')}
    put(project / 'phase3/librelane_switch.json', switch)
    monkeypatch.setenv('PDK_ROOT', str(root))
    monkeypatch.delenv('EDA_CONTAINER', raising=False)
    monkeypatch.setattr(GP, 'default_runner',
                        lambda cmd, timeout: (0, 'synthetic unrelated delegate transport', ''))
    return project, candidate, folder, config, library, inputs_path


def _cli(project, candidate):
    out = project / 'text-precheck.json'
    rc = GP.main([str(project), '--pdk', 'neutral', '--gds', str(candidate), '--json', str(out)])
    report = json.loads(out.read_text())
    row = next(r for r in report['steps'] if r['step_id'] == 'General.ForbiddenLayers')
    return rc, row


def test_parser_preserves_nonzero_texttype_in_inclusive_layer_accounting(tmp_path):
    path = _gds(tmp_path / 'library.gds', text_type=9)
    layout = GEOM.read_layout(path)
    text = next(el for el in layout.cells['chip_top'].elements if el.kind == 'TEXT')
    assert text.datatype == 9, 'TEXTTYPE was lost before layer-purpose classification'
    assert (77, 9) in GEOM.layers_used(layout)
    assert (77, 0) not in GEOM.layers_used(layout)


@pytest.mark.parametrize('text_type', [0, 9])
def test_selected_receipt_bound_pdk_text_purpose_reaches_the_real_cli(tmp_path, monkeypatch, text_type):
    project, candidate, _folder, _config, library, _inputs = _project(
        tmp_path, monkeypatch, text_type=text_type)
    rc, row = _cli(project, candidate)
    assert rc == 0, 'selected native PDK TEXT purpose was refused by the general-precheck CLI'
    assert row['verdict'] == GP.PASS
    assert row['measured']['library_text_layers'] == [f'77/{text_type}']
    assert row['measured']['annotation_authority'][0]['path'] == str(library)
    assert row['measured']['annotation_authority'][0]['sha256'] == LC.digest(library)


def test_library_annotation_never_authorizes_physical_or_forbidden_or_unknown_purpose(tmp_path, monkeypatch):
    for fault in ['physical', 'mixed', 'declared-forbidden', 'unknown-type', 'unknown-layer']:
        p, candidate, _folder, _config, _library, _inputs = _project(
            tmp_path / fault, monkeypatch,
            physical=fault in ('physical', 'mixed'), text=fault != 'physical',
            forbidden=['77/9'] if fault == 'declared-forbidden' else None)
        if fault in ('unknown-type', 'unknown-layer'):
            _gds(candidate, text_type=999 if fault == 'unknown-type' else 9,
                 text_layer=999 if fault == 'unknown-layer' else 77)
            record = json.loads((p / LF.DEFAULT_STREAM_REL).read_text())
            record['canonical_sha256'] = record['source_sha256'] = LC.digest(candidate)
            put(p / LF.DEFAULT_STREAM_REL, record)
            put(p / LF.RECORD_REL, {'default_stream': record})
        rc, row = _cli(p, candidate)
        assert rc == 1 and row['verdict'] == GP.FAIL, fault
        print('TEXT_PURPOSE_NEGATIVE', fault, row['verdict'])


def test_missing_foreign_or_stale_library_authority_cannot_grant_text_permission(tmp_path, monkeypatch):
    for fault in ['missing-receipt', 'changed-library', 'changed-config', 'changed-commands',
                  'wrong-config-digest', 'foreign-library', 'wrong-process', 'missing-entry']:
        p, candidate, folder, config, library, inputs_path = _project(tmp_path / fault, monkeypatch)
        fp = json.loads((folder / 'input_fingerprint.json').read_text())
        if fault == 'missing-receipt':
            (folder / 'vibeic_receipt.json').unlink()
        elif fault == 'changed-library':
            _gds(library, text_type=3)
        elif fault == 'changed-config':
            config.write_text(config.read_text() + ' ')
        elif fault == 'changed-commands':
            (folder / 'COMMANDS').write_text('changed native arguments\n')
        elif fault == 'wrong-config-digest':
            fp['config_files']['/pdk/neutral/gds/views.gds'] = '0' * 64
            _rebind_receipt(folder, fp)
        else:
            inputs = json.loads(inputs_path.read_text())
            if fault == 'missing-entry':
                inputs['native_cli']['inputs'] = []
            else:
                # A fully re-bound source outside the selected PDK is still foreign.
                foreign = (p / 'fixture_pdk/other/gds/views.gds'
                           if fault == 'wrong-process' else p / 'not-the-selected-pdk.gds')
                foreign.parent.mkdir(parents=True, exist_ok=True)
                foreign.write_bytes(library.read_bytes())
                inputs['native_cli']['argv'][4] = str(foreign)
                inputs['native_cli']['inputs'][0]['path'] = str(foreign)
                config_doc = json.loads(config.read_text())
                config_doc['EXTRA_GDS_FILES'] = [str(foreign)]
                if fault == 'wrong-process':
                    config_doc['PDK'] = 'other'
                put(config, config_doc)
                put(folder / 'config.json', config_doc)
                fp['config'] = LC.digest(config)
                fp['config_files'] = {str(foreign): LC.digest(foreign)}
                (folder / 'COMMANDS').write_text(shlex.join(inputs['native_cli']['argv']) + '\n')
                inputs['native_cli']['commands_sha256'] = LC.digest(folder / 'COMMANDS')
                _rebind_receipt(folder, fp)
            put(inputs_path, inputs)
            record = json.loads((p / LF.DEFAULT_STREAM_REL).read_text())
            record['stream_inputs_sha256'] = LC.digest(inputs_path)
            put(p / LF.DEFAULT_STREAM_REL, record)
            put(p / LF.RECORD_REL, {'default_stream': record})
        rc, row = _cli(p, candidate)
        assert rc == 1 and row['verdict'] == GP.FAIL, fault
        assert not row['measured'].get('library_text_layers'), fault
        print('TEXT_AUTHORITY_NEGATIVE', fault, row['verdict'])
