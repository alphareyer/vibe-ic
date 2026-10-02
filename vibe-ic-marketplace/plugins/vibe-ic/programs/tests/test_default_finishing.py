"""Default Steps 34/35/37: retained-byte source controls, not native proof."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import _plugin_tree  # noqa: F401
from _hostpaths import require_repo
import _default_backend_fixtures as BF
import librelane_contract as LC
import librelane_fill_dfm as LF
import metal_fill_density_check as MFD
import dfm_screen_check as DFM
import phase3_one_shot_runner as R

PROMOTION = 'phase3/librelane/37-default-promotion.json'


def subject(tmp_path):
    p = tmp_path / 'project'
    pnr = p / 'phase3/stage3/pnr'
    pnr.mkdir(parents=True)
    artifact = require_repo('vibe-ic-marketplace', 'plugins', 'vibe-ic', 'programs',
                            'calibration', 'pg_supply_owned_negative.def')
    for name in ('top.def', 'routed.def'):
        (pnr / name).write_bytes(artifact.read_bytes())
    odb = BF.fill_record(p)
    BF.consume_fill(p, odb)
    LF.update_record(p, 'odb', {'shipped': 'librelane', 'tool': odb,
                              'def_sha256': LC.digest(pnr / 'routed.def')})
    filled = pnr / 'filled.def'
    stream = p / 'phase3/librelane/37-default-stream/01-klayout-streamout/top.gds'
    stream.parent.mkdir(parents=True)
    stream.write_bytes(b'SOURCE_FIXTURE: real stream bytes are measured separately')
    folder, _ = BF.producer(p, 'KLayout.StreamOut', {'def': str(filled), 'metrics': {}},
        {'def': str(filled), 'klayout_gds': str(stream), 'metrics': {}},
        lane='37-default-stream/01-klayout-streamout', name=LC._def_design_name(filled))
    prefill = p / 'phase3/tool_arms/34/prefill.gds'
    prefill.write_bytes(stream.read_bytes())
    source = p / 'phase3/librelane/34-fill/01-klayout-filler/top.gds'
    source.parent.mkdir(parents=True)
    source.write_bytes(stream.read_bytes() + b' plus filler')
    ff, _ = BF.producer(p, 'KLayout.Filler', {'gds': str(prefill), 'metrics': {}},
        {'gds': str(source), 'metrics': {}}, lane='34-fill/01-klayout-filler')
    arm = BF.density_record(p, source, 0)
    arm['filler'] = {'state': str(ff / 'state_out.json'),
        'state_sha256': LC.digest(ff / 'state_out.json'), 'gds_in': str(prefill),
        'gds_in_sha256': LC.digest(prefill), 'filled_gds': str(source),
        'filled_sha256': LC.digest(source)}
    canonical = p / 'phase3/stage4/gds/top.gds'
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(source.read_bytes())
    pnr_gds = pnr / 'top.gds'
    pnr_gds.write_bytes(source.read_bytes())
    record = {'file_top': 'top', 'design_name': LC._def_design_name(filled),
        'stream_state': str(folder / 'state_out.json'),
        'stream_state_sha256': LC.digest(folder / 'state_out.json')}
    for key, path in dict(filled_def=filled, stream=stream, prefill=prefill,
                          source=source, pnr_gds=pnr_gds, canonical=canonical).items():
        record[key], record[key + '_sha256'] = str(path), LC.digest(path)
    LF.update_record(p, 'gds', {'shipped': 'librelane', 'librelane': arm,
                              'shipped_sha256': LC.digest(source)})
    BF.put(p / PROMOTION, record)
    LF.update_record(p, 'default_stream', record)
    return p, record, arm


def test_current_finished_bytes_are_consumed_by_both_existing_rows(tmp_path):
    p, record, arm = subject(tmp_path)
    findings, stats = MFD.tool_arm_findings(p)
    assert findings == [] and stats['density_errors'] == 0
    ref = DFM.audit(p)['density_ref']
    assert ref['step34_pass'] is True
    assert ref.get('subject_sha256') == record['canonical_sha256']
    assert ref.get('measurement_verdict') == 'PASS'


@pytest.mark.parametrize('mutation', ['stream_execution', 'filler_execution', 'filled_def',
    'stream_bytes', 'wrong_stage', 'wrong_design', 'wrong_tool', 'image', 'pdk',
    'canonical', 'pnr_copy', 'promotion_removed', 'consumer_record_removed',
    'filler_input', 'filler_output', 'filled_def_gds_mismatch'])
def test_existing_rows_refuse_a_broken_finishing_chain(tmp_path, mutation):
    p, record, arm = subject(tmp_path)
    assert MFD.tool_arm_findings(p)[0] == []
    state = Path(record['stream_state'])
    if mutation in ('stream_execution', 'filler_execution'):
        selected = state if mutation == 'stream_execution' else Path(arm['filler']['state'])
        (selected.parent / 'invocation.log').unlink()
    elif mutation in ('filled_def', 'stream_bytes', 'canonical', 'pnr_copy', 'filler_input', 'filler_output'):
        key = {'stream_bytes': 'stream', 'pnr_copy': 'pnr_gds',
               'filler_input': 'prefill', 'filler_output': 'source'}.get(mutation, mutation)
        Path(record[key]).write_bytes(b'changed current subject')
    elif mutation == 'wrong_stage':
        foreign = p / 'phase2/foreign/state_out.json'
        foreign.parent.mkdir(parents=True)
        foreign.write_bytes(state.read_bytes())
        record.update(stream_state=str(foreign), stream_state_sha256=LC.digest(foreign))
    elif mutation in ('wrong_design', 'wrong_tool'):
        step = 'KLayout.StreamOut' if mutation == 'wrong_design' else 'Yosys.Synthesis'
        BF.producer(p, step, json.loads((state.parent / 'state_in.json').read_text()),
            json.loads(state.read_text()), lane='37-default-stream/01-klayout-streamout',
            name='another' if mutation == 'wrong_design' else record['design_name'])
        record['stream_state_sha256'] = LC.digest(state)
    elif mutation in ('image', 'pdk'):
        switch = p / 'phase3/librelane_switch.json'
        doc = json.loads(switch.read_text())
        if mutation == 'image':
            doc['image'] = BF.IMAGE.replace('a' * 64, 'b' * 64)
        else:
            doc['pdk_root_host'] = str(p / 'another-pdk')
            (p / 'another-pdk/neutral').mkdir(parents=True)
        BF.put(switch, doc)
    elif mutation == 'promotion_removed':
        (p / PROMOTION).unlink()
    elif mutation == 'consumer_record_removed':
        doc = json.loads((p / LF.RECORD_REL).read_text())
        del doc['default_stream']
        BF.put(p / LF.RECORD_REL, doc)
    else:
        # A coherent stream packet reads the unfilled DEF instead. Density
        # and all GDS outputs still match; only adoption is wrong.
        before = json.loads((state.parent / 'state_in.json').read_text())
        before['def'] = str(p / 'phase3/stage3/pnr/top.def')
        BF.producer(p, 'KLayout.StreamOut', before, json.loads(state.read_text()),
            lane='37-default-stream/01-klayout-streamout', name=record['design_name'])
        record['stream_state_sha256'] = LC.digest(state)
    if mutation in ('wrong_stage', 'wrong_design', 'wrong_tool', 'filled_def_gds_mismatch'):
        BF.put(p / PROMOTION, record)
        LF.update_record(p, 'default_stream', record)
    findings, _ = MFD.tool_arm_findings(p)
    assert any(f.severity == 'ERROR' for f in findings), findings
    assert DFM.audit(p)['density_ref']['step34_pass'] is False


def test_real_density_failure_remains_explicit_in_the_dfm_reference(tmp_path):
    p, _, arm = subject(tmp_path)
    # This is a failed tool arm, so no successful finishing promotion exists.
    (p / PROMOTION).unlink()
    (p / 'phase3/librelane/34-config/KLayout.StreamOut.json').unlink()
    doc = json.loads((p / LF.RECORD_REL).read_text())
    del doc['default_stream']
    BF.put(p / LF.RECORD_REL, doc)
    bad = BF.density_record(p, Path(arm['subject']), 5)
    LF.update_record(p, 'gds', {'shipped': 'librelane', 'librelane': bad})
    assert MFD.tool_arm_findings(p)[1]['density_errors'] == 5
    ref = DFM.audit(p)['density_ref']
    assert ref['step34_pass'] is False and ref['errors'] == 5
    assert ref.get('measurement_verdict') == 'FAIL'


def test_default_ordinary_caller_uses_one_primary_without_a_mode_switch(tmp_path, monkeypatch):
    p, record, _ = subject(tmp_path)
    assert 'steps' not in json.loads((p / 'phase3/librelane_switch.json').read_text())
    monkeypatch.setattr(R, '_layout_basis', lambda *a: ('current', None))
    monkeypatch.setattr(R._ga, 'stream_refusal', lambda *a: None)
    monkeypatch.setattr(R, '_vacuous_on_unrouted', lambda *a: None)
    calls = []
    def primary(*args):
        calls.append('primary')
        return {'canonical': record['canonical'], 'receipt': str(p / PROMOTION)}
    monkeypatch.setattr(LF, 'default_stream', primary, raising=False)
    # Stop a legacy producer at its first concrete decision; count observed
    # dispatch values rather than a missing symbol or a collection error.
    monkeypatch.setattr(R, 'publish_database_unit_declaration', lambda *a: {'verdict': 'PASS', 'reason': 'SOURCE_FIXTURE'})
    monkeypatch.setattr(R, '_streamout_top', lambda *a: ('top', ''))
    monkeypatch.setattr(R, 'publish_tapeout_declarations', lambda *a: {'published': [], 'not_determined': [], 'already_answered': []})
    def legacy(*args):
        calls.append('legacy')
        raise LC.Refusal('SOURCE_FIXTURE_LEGACY_SELECTED', 'observed legacy producer')
    monkeypatch.setattr(R, '_magic_def_to_gds', legacy)
    pdk = R.PdkConfig('neutral', '', '', '', None, '', None)
    try:
        R.step_gds(p, 'top', pdk, '')
    except LC.Refusal:
        pass
    assert calls == ['primary']
