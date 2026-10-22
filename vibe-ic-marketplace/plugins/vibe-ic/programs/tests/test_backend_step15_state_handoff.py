"""Bounded Step 15 state handoff remains portable and canonical."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import execution_backend_producers as producers
import execution_modes as em


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _make_published_project(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "producer"
    raw = project / "phase3/librelane/15-floorplan"
    pnr = project / "phase3/stage3/pnr"
    raw.mkdir(parents=True)
    pnr.mkdir(parents=True)
    floorplan = _write(raw / "floorplan.def", "VERSION 5.8 ;\nDESIGN bounded ;\n")
    odb = _write(raw / "librelane_floorplan.odb", "ODB-BYTES\n")
    netlist = _write(raw / "step15.nl.v", "module bounded; endmodule\n")
    sdc = _write(raw / "constraint.sdc", "create_clock -period 10 [get_ports clk]\n")
    upstream_sdc = _write(pnr / 'constraint.sdc', 'upstream-clock\n')
    canonical_def = pnr / "floorplan.def"
    canonical_odb = pnr / "librelane_floorplan.odb"
    shutil.copyfile(floorplan, canonical_def)
    shutil.copyfile(odb, canonical_odb)

    state = _write(raw / "state_out.json", json.dumps({
        "metrics": {"design__instance__count": 2},
        "def": str(floorplan), "odb": str(odb),
        "nl": str(netlist), "sdc": str(sdc),
    }, sort_keys=True))
    pdn = _write(raw / "pdn-state_out.json", json.dumps({
        "step": "OpenROAD.GeneratePDN", "state": "done",
    }, sort_keys=True))
    handoff = project / "reports/phase3/execution-backend-15-floorplan-handoff.json"
    _write(handoff, json.dumps({
        "state_sha256": producers.snap.sha(state),
        "views": {
            "def": {"source_sha256": producers.snap.sha(canonical_def),
                    "dest_sha256": producers.snap.sha(canonical_def)},
            "odb": {"source_sha256": producers.snap.sha(canonical_odb),
                    "dest_sha256": producers.snap.sha(canonical_odb)},
        },
    }, sort_keys=True))
    producers._publish_step15_bounded_state(project, SimpleNamespace(extras={
        "bounded_state": True,
        "native_state": str(state), "pdn_state": str(pdn),
        "handoff": str(handoff), "floorplan": str(canonical_def),
        "odb": str(canonical_odb),
    }))
    isolated = tmp_path / "isolated-consumer"
    shutil.copytree(pnr, isolated / "phase3/stage3/pnr")
    return project, isolated


def test_step15_publisher_to_isolated_reader_consumes_canonical_def_and_odb(tmp_path):
    _, isolated = _make_published_project(tmp_path)

    state = producers._read_step15_bounded_state(isolated)

    assert state["views"]["def"] == isolated / "phase3/stage3/pnr/floorplan.def"
    assert state["views"]["odb"] == isolated / "phase3/stage3/pnr/librelane_floorplan.odb"
    assert state["views"]["sdc_derived"] == isolated / "phase3/stage3/pnr/step15.constraint.sdc"
    assert (isolated / 'phase3/stage3/pnr/constraint.sdc').read_text() == 'upstream-clock\n'
    assert (isolated / 'phase3/stage3/pnr/step15.constraint.sdc').read_text() == 'create_clock -period 10 [get_ports clk]\n'


def test_step15_reader_refuses_coordinated_canonical_path_substitution(tmp_path):
    _, isolated = _make_published_project(tmp_path)
    pnr = isolated / "phase3/stage3/pnr"
    canonical = pnr / "floorplan.def"
    alternate = pnr / "alternate.def"
    shutil.copyfile(canonical, alternate)
    canonical.write_text("CHANGED-CANONICAL\n")

    record_path = pnr / "step15_state.json"
    handoff_path = pnr / "step15_handoff.json"
    record = json.loads(record_path.read_text())
    handoff = json.loads(handoff_path.read_text())
    alternate_sha = producers.snap.sha(alternate)
    record["views"]["def"] = {
        "path": "phase3/stage3/pnr/alternate.def", "sha256": alternate_sha,
    }
    handoff["views"]["def"]["source_sha256"] = alternate_sha
    handoff["views"]["def"]["dest_sha256"] = alternate_sha
    record["handoff_views"]["def"] = handoff["views"]["def"]
    handoff_path.write_text(json.dumps(handoff, sort_keys=True))
    record["handoff"]["sha256"] = producers.snap.sha(handoff_path)
    record_path.write_text(json.dumps(record, sort_keys=True))

    with pytest.raises(em.Refusal) as caught:
        producers._read_step15_bounded_state(isolated)
    assert caught.value.code == "LL_HANDOFF_VIEW_CHANGED"



@pytest.mark.parametrize('which', ['upstream', 'derived'])
def test_step15_reader_refuses_changed_upstream_or_derived_sdc(tmp_path, which):
    _, isolated = _make_published_project(tmp_path)
    pnr = isolated / 'phase3/stage3/pnr'
    target = pnr / ('constraint.sdc' if which == 'upstream' else 'step15.constraint.sdc')
    target.write_text('changed-' + which + '\n')
    with pytest.raises(em.Refusal, match='BACKEND_STEP15_SDC_BINDING_CHANGED'):
        producers._step15_sdc(isolated)


def test_step15_reader_refuses_canonical_path_substitution(tmp_path):
    _, isolated = _make_published_project(tmp_path)
    pnr = isolated / 'phase3/stage3/pnr'
    record = json.loads((pnr / 'step15_state.json').read_text())
    record['views']['sdc_derived']['path'] = 'phase3/stage3/pnr/escape.sdc'
    (pnr / 'step15_state.json').write_text(json.dumps(record))
    with pytest.raises(em.Refusal, match='BACKEND_STEP15_SDC_BINDING_CHANGED'):
        producers._step15_sdc(isolated)

def test_step15_reader_refuses_non_mapping_handoff_with_stable_code(tmp_path):
    _, isolated = _make_published_project(tmp_path)
    pnr = isolated / "phase3/stage3/pnr"
    handoff_path = pnr / "step15_handoff.json"
    record_path = pnr / "step15_state.json"
    record = json.loads(record_path.read_text())
    handoff_path.write_text("[]\n")
    record["handoff"]["sha256"] = producers.snap.sha(handoff_path)
    record_path.write_text(json.dumps(record, sort_keys=True))

    with pytest.raises(em.Refusal) as caught:
        producers._read_step15_bounded_state(isolated)
    assert caught.value.code == "LL_HANDOFF_ABSENT"


def test_portable_gate_publisher_uses_project_relative_paths(tmp_path):
    import phase3_one_shot_runner as phase3
    project = tmp_path / 'project'; project.mkdir()
    (project / 'phase3/stage3/pnr').mkdir(parents=True)
    report = {'netlist': str(project / 'phase2/stage2/synth/top_synth.v'),
              'sdc': str(project / 'phase3/stage3/pnr/step15.constraint.sdc'),
              'source': str(project / 'phase3/librelane/prelayout/state_out.json'),
              'netlist_sha256': 'a' * 64}
    portable = phase3._portable_prelayout_gate(project, report)
    assert portable['sdc'] == 'phase3/stage3/pnr/step15.constraint.sdc'
    assert not portable['sdc'].startswith('/')
    import librelane_contract as llc
    with pytest.raises(llc.Refusal, match='LL_PRELAYOUT_GATE_PATH_ESCAPE'):
        phase3._portable_prelayout_gate(project, {'sdc': '/outside/constraint.sdc'})


def test_legacy_schema_one_sdc_binding_accepts_then_refuses_changed_bytes(tmp_path):
    _, isolated = _make_published_project(tmp_path)
    pnr = isolated / 'phase3/stage3/pnr'
    record = json.loads((pnr / 'step15_state.json').read_text())
    derived = record['views'].pop('sdc_derived')
    record.pop('upstream_sdc')
    record['schema'] = 'vibeic/step15-state-handoff/1'
    record['views']['sdc'] = {'path': 'phase3/stage3/pnr/constraint.sdc',
                              'sha256': producers.snap.sha(pnr / 'constraint.sdc')}
    (pnr / 'step15_state.json').write_text(json.dumps(record))
    assert producers._step15_sdc(isolated) == pnr / 'constraint.sdc'
    (pnr / 'constraint.sdc').write_text('legacy-changed\n')
    with pytest.raises(em.Refusal, match='BACKEND_STEP15_SDC_BINDING_CHANGED'):
        producers._step15_sdc(isolated)


@pytest.mark.parametrize("deck", ["legacy", "derived"])
def test_legacy_reader_refuses_changed_raw_handoff_even_with_same_sdc(tmp_path, deck):
    _, isolated = _make_published_project(tmp_path)
    pnr = isolated / 'phase3/stage3/pnr'
    record = json.loads((pnr / 'step15_state.json').read_text())
    if deck == "legacy":
        record.pop('upstream_sdc')
        record['views'].pop('sdc_derived')
        record['views']['sdc'] = {'path': 'phase3/stage3/pnr/constraint.sdc',
                                  'sha256': producers.snap.sha(pnr / 'constraint.sdc')}
        (pnr / 'step15_state.json').write_text(json.dumps(record))
    assert producers._step15_sdc(isolated).is_file()
    handoff = pnr / 'step15_handoff.json'
    handoff.write_text('changed-handoff\n')
    record = json.loads((pnr / 'step15_state.json').read_text())
    record['handoff']['sha256'] = producers.snap.sha(handoff)
    (pnr / 'step15_state.json').write_text(json.dumps(record))
    try:
        accepted = producers._step15_sdc(isolated)
        outcome = 'ACCEPTED:' + accepted.name
    except em.Refusal as exc:
        outcome = exc.code
    assert outcome == 'BACKEND_STEP15_SDC_BINDING_CHANGED'


def test_legacy_reader_refuses_escaped_canonical_deck_path(tmp_path):
    _, isolated = _make_published_project(tmp_path)
    pnr = isolated / 'phase3/stage3/pnr'
    record = json.loads((pnr / 'step15_state.json').read_text())
    record.pop('upstream_sdc')
    record['views'].pop('sdc_derived')
    record['views']['sdc'] = {'path': '../outside/constraint.sdc', 'sha256': 'a' * 64}
    (pnr / 'step15_state.json').write_text(json.dumps(record))
    with pytest.raises(em.Refusal, match='BACKEND_STEP15_SDC_BINDING_CHANGED'):
        producers._step15_sdc(isolated)


def test_legacy_reader_refuses_non_mapping_record(tmp_path):
    _, isolated = _make_published_project(tmp_path)
    (isolated / 'phase3/stage3/pnr/step15_state.json').write_text('[]\n')
    with pytest.raises(em.Refusal, match='BACKEND_STEP15_SDC_BINDING_CHANGED'):
        producers._step15_sdc(isolated)


def test_actual_state_chain_bridge_passes_physical_sdc_to_state_from_direct(tmp_path, monkeypatch):
    import librelane_contract as ll
    import phase3_one_shot_runner as phase3
    _, isolated = _make_published_project(tmp_path)
    config = isolated / 'fixture-config.json'
    config.write_text(json.dumps({'meta': {'step': 'OpenROAD.GlobalPlacementSkipIO'},
                                  'DESIGN_NAME': 'bounded',
                                  'inputs': ['def', 'odb', 'nl', 'sdc'], 'outputs': []}))
    (isolated / 'fixture-config.views.json').write_text(json.dumps({'step': 'OpenROAD.GlobalPlacementSkipIO', 'inputs': ['def', 'odb', 'nl', 'sdc'], 'outputs': []}))
    captured = {}
    monkeypatch.setattr(ll, 'resolve_step_configs', lambda *a, **k: {'OpenROAD.GlobalPlacementSkipIO': config})
    monkeypatch.setattr(phase3, '_resolved_cell_policy', lambda configs, root, pdk: (configs, []))
    def run_chain(project, image, tasks, **kwargs):
        captured['state'] = tasks[0][2]
        return []
    monkeypatch.setattr(ll, 'run_chain', run_chain)
    params = {'image_id': 'fixture-image', 'pdk_name': 'fixture',
              'pdk_root': str(tmp_path), 'step_id': '17', 'top': 'bounded'}
    producers._state_chain(isolated, params, ['OpenROAD.GlobalPlacementSkipIO'], 'floorplan.def')
    state = json.loads(Path(captured['state']).read_text())
    assert state['sdc'] == str((isolated / 'phase3/stage3/pnr/step15.constraint.sdc').resolve())
    assert 'sdc_derived' not in state


def _resume_project(tmp_path, monkeypatch):
    import phase3_one_shot_runner as R
    from _ppa import timing
    project, _ = _make_published_project(tmp_path)
    pnr = project / producers.PNR
    constraints = project / 'phase2/stage2/constraints'
    sdc_rel = 'phase2/stage2/constraints/bounded.asic.sdc'
    rec_rel = 'phase2/stage2/constraints/asic_sdc.json'
    old_sdc = _write(project / sdc_rel, 'old canonical SDC\n')
    old_record = {'path': sdc_rel, 'sha256': producers.snap.sha(old_sdc)}
    _write(project / rec_rel, json.dumps(old_record))
    design = _write(project / 'input/design.v', 'module bounded; endmodule\n')
    input_hashes = {str(p.relative_to(project)): producers.snap.sha(p)
                    for p in (old_sdc, project / rec_rel, design)}
    folder = project / 'steps/retained15'
    for rel in input_hashes:
        target = folder / 'inputs' / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(project / rel, target)
    state_rel = str((pnr / 'step15_state.json').relative_to(project))
    _write(folder / 'outputs.json', json.dumps({
        'folder': 'retained15', 'canonical_pass': True, 'inputs': input_hashes,
        'written_files': [{'rel': state_rel, 'sha256': producers.snap.sha(project / state_rel)}]}))
    _write(project / 'steps/latest_pass.json', json.dumps({'steps': {'15': {
        'folder': 'retained15', 'inputs': input_hashes}}}))
    native = project / 'phase3/librelane/15-floorplan'
    # The real native receipt validator checks all retained bytes; this
    # fixture supplies its measurement transport, not an acceptance verdict.
    pdk_file = _write(tmp_path / 'pdk/fixture/cells.lib', 'library fixture {}\n')
    pdn_cfg = _write(project / 'input/pdn.tcl', 'set PDN_STRIPE 10\n')
    fingerprint = {'step': 'OpenROAD.GeneratePDN', 'image': 'fixture-image',
                   'config_files': {'/pdk/fixture/cells.lib': producers.snap.sha(pdk_file),
                                    str(pdn_cfg): producers.snap.sha(pdn_cfg),
                                    str(pnr / 'constraint.sdc'): producers.snap.sha(pnr / 'constraint.sdc')}}
    _write(native / 'input_fingerprint.json', json.dumps(fingerprint))
    _write(native / 'pdk_root.json', '{}\n')
    _write(native / 'invocation.log', 'source-control native invocation\n')
    _write(native / 'vibeic_receipt.json', json.dumps({'input': fingerprint,
        'sha256': {p.name: producers.snap.sha(p) for p in native.iterdir() if p.is_file()}}))
    # Original handoff fixture carries hashes; add its original source pointer
    # through the publisher so the complete chain remains self-consistent.
    raw_handoff = project / 'reports/phase3/execution-backend-15-floorplan-handoff.json'
    handoff = json.loads(raw_handoff.read_text())
    handoff['views']['odb']['source'] = str(native / 'librelane_floorplan.odb')
    raw_handoff.write_text(json.dumps(handoff))
    producers._publish_step15_bounded_state(project, SimpleNamespace(extras={
        'bounded_state': True, 'native_state': str(native / 'state_out.json'),
        'pdn_state': str(native / 'pdn-state_out.json'), 'handoff': str(raw_handoff),
        'floorplan': str(pnr / 'floorplan.def'), 'odb': str(pnr / 'librelane_floorplan.odb')}))
    retained = json.loads((folder / 'outputs.json').read_text())
    retained['written_files'][0]['sha256'] = producers.snap.sha(project / state_rel)
    (folder / 'outputs.json').write_text(json.dumps(retained))
    deck = 'create_clock -period 12 [get_ports clk]\nset_max_transition 3 [current_design]\n'
    (project / sdc_rel).write_text(deck)
    current = {'path': sdc_rel, 'sha256': producers.snap.sha(project / sdc_rel),
               'deck_sha256': producers.snap.sha(project / sdc_rel)}
    (project / rec_rel).write_text(json.dumps(current))
    (pnr / 'constraint.sdc').write_text(deck)
    _write(pnr / 'constraint_sdc_provenance.json', json.dumps({
        'step7_sha256': current['sha256'], 'deck_sha256': producers.snap.sha(pnr / 'constraint.sdc')}))
    canonical_pdk = SimpleNamespace(name='fixture', liberty='/container/canonical.lib')
    monkeypatch.setattr(R, '_detect_pdk', lambda *_: canonical_pdk)
    monkeypatch.setattr(producers, '_parameters', lambda *_: pytest.fail(
        'native host-rebased PDK must not replace canonical author representation'))
    monkeypatch.setattr(timing, 'read_step7_asic_sdc', lambda *_: (current, ''))
    def derive(rt, root, top, pdk, container):
        assert pdk is canonical_pdk and pdk.liberty == '/container/canonical.lib'
        return {'text': deck}
    monkeypatch.setattr(timing, 'asic_sdc_for_pnr', derive)
    params = {'image_id': 'fixture-image', 'pdk_name': 'fixture',
              'pdk_root': str(tmp_path / 'pdk'), 'step_id': '17', 'top': 'bounded',
              'execution_mode': 'default'}
    return project, params, pdk_file, pdn_cfg


@pytest.mark.parametrize('damage', [None, 'def', 'odb', 'nl', 'design', 'pdk', 'pdn_config',
                                   'current_sdc', 'provenance', 'old_record'])
def test_step17_derives_current_sdc_only_when_retained_floorplan_inputs_match(
        tmp_path, monkeypatch, damage):
    project, params, pdk_file, pdn_cfg = _resume_project(tmp_path, monkeypatch)
    pnr = project / producers.PNR
    retained = {p: p.read_bytes() for p in pnr.glob('step15*') if p.is_file()}
    paths = {'def': pnr / 'floorplan.def', 'odb': pnr / 'librelane_floorplan.odb',
             'nl': pnr / 'step15.nl.v', 'design': project / 'input/design.v',
             'pdk': pdk_file, 'pdn_config': pdn_cfg, 'current_sdc': pnr / 'constraint.sdc',
             'provenance': pnr / 'constraint_sdc_provenance.json',
             'old_record': project / 'steps/retained15/inputs/phase2/stage2/constraints/asic_sdc.json'}
    if damage:
        paths[damage].write_text('changed\n')
    if damage:
        with pytest.raises(em.Refusal):
            bound = producers._read_step15_bounded_state(project)
            producers._step17_current_sdc_bridge(project, params, bound)
    else:
        with pytest.raises(em.Refusal, match='BACKEND_STEP15_SDC_BINDING_CHANGED'):
            producers._step15_sdc(project)
        bound = producers._read_step15_bounded_state(project)
        sdc, receipt = producers._step17_current_sdc_bridge(project, params, bound)
        assert sdc == pnr / 'constraint.sdc'
        assert receipt['retained_input_count'] == 1
        assert receipt['changed_inputs'] == ['phase2/stage2/constraints/asic_sdc.json',
                                              'phase2/stage2/constraints/bounded.asic.sdc']
        assert receipt['retained_views'] == bound['record']['views']
        assert {p: p.read_bytes() for p in retained} == retained


def test_actual_step17_chain_uses_derivative_state_without_modifying_step15(tmp_path, monkeypatch):
    import librelane_contract as ll
    import phase3_one_shot_runner as R
    project, params, _, _ = _resume_project(tmp_path, monkeypatch)
    config = _write(project / 'config.json', json.dumps({
        'meta': {'step': 'OpenROAD.GlobalPlacementSkipIO'}, 'DESIGN_NAME': 'bounded'}))
    _write(project / 'config.views.json', json.dumps({'step': 'OpenROAD.GlobalPlacementSkipIO',
        'inputs': ['def', 'odb', 'nl', 'sdc'], 'outputs': []}))
    monkeypatch.setattr(ll, 'resolve_step_configs', lambda *_a, **_k: {'OpenROAD.GlobalPlacementSkipIO': config})
    monkeypatch.setattr(R, '_resolved_cell_policy', lambda configs, *_: (configs, []))
    seen = []
    def run(project, image, tasks, **kwargs):
        seen.append(tasks[0][2]); return []
    monkeypatch.setattr(ll, 'run_chain', run)
    old = (project / producers.PNR / 'step15_state.json').read_bytes()
    producers._state_chain(project, params, ['OpenROAD.GlobalPlacementSkipIO'], 'floorplan.def')
    assert len(seen) == 1
    state = json.loads(seen[0].read_text())
    assert state['sdc'] == str(project / producers.PNR / 'constraint.sdc')
    assert state['odb'] == str(project / producers.PNR / 'librelane_floorplan.odb')
    receipt = json.loads((seen[0].parent / 'sdc_derivation.json').read_text())
    assert receipt['state']['sha256'] == producers.snap.sha(seen[0])
    assert (project / producers.PNR / 'step15_state.json').read_bytes() == old


def _accepted_placement_bridge(tmp_path, monkeypatch):
    project, params, _, _ = _resume_project(tmp_path, monkeypatch)
    pnr = project / producers.PNR
    provenance_path = pnr / 'constraint_sdc_provenance.json'
    provenance = json.loads(provenance_path.read_text())
    provenance['step7_sdc'] = 'phase2/stage2/constraints/bounded.asic.sdc'
    provenance['step7_deck_sha256'] = producers.snap.sha(project / provenance['step7_sdc'])
    provenance_path.write_text(json.dumps(provenance))
    _, derivation = producers._step17_current_sdc_bridge(
        project, params, producers._read_step15_bounded_state(project))
    initial = _write(project / 'phase3/librelane/17-config/bridge/state_in.json', json.dumps({
        'sdc': str(pnr / 'constraint.sdc'), 'odb': str(pnr / 'librelane_floorplan.odb')}))
    derivation['state'] = {'path': str(initial.relative_to(project)), 'sha256': producers.snap.sha(initial)}
    derivative = _write(initial.parent / 'sdc_derivation.json', json.dumps(derivation))
    folder = project / 'phase3/librelane/17/01-detailed-placement'
    native_sdc = _write(folder / 'bounded.sdc', '# OpenROAD write_sdc rewrites valid syntax\ncreate_clock -period 12 -name clk [get_ports {clk}]\n')
    native_def = _write(folder / 'bounded.def', 'VERSION 5.8 ;\nDESIGN bounded ;\n')
    state = _write(folder / 'state_out.json', json.dumps({'sdc': str(native_sdc), 'def': str(native_def)}))
    fingerprint = {'step': 'OpenROAD.DetailedPlacement', 'image': 'fixture-image',
                   'state': producers.snap.sha(initial), 'config_files': {}}
    _write(folder / 'input_fingerprint.json', json.dumps(fingerprint))
    _write(folder / 'pdk_root.json', '{}\n')
    _write(folder / 'invocation.log', 'source-control placement ran\n')
    _write(folder / 'vibeic_receipt.json', json.dumps({'input': fingerprint,
        'sha256': {p.name: producers.snap.sha(p) for p in folder.iterdir() if p.is_file()}}))
    ledger = _write(project / producers.RPT / 'execution-backend-17.json', json.dumps({
        'initial_state': str(initial), 'initial_sha256': producers.snap.sha(initial),
        'ordered_steps': ['OpenROAD.DetailedPlacement'], 'states': [{
            'step': 'OpenROAD.DetailedPlacement', 'state': str(state), 'sha256': producers.snap.sha(state)}]}))
    shutil.copyfile(native_def, pnr / 'placed.def')
    _write(project / producers.RPT / 'librelane_placement_handoff.json', json.dumps({
        'state': str(state), 'state_sha256': producers.snap.sha(state), 'views': {'def': {
            'dest': str(pnr / 'placed.def'), 'dest_sha256': producers.snap.sha(pnr / 'placed.def')}}}))
    inputs = {'input/design.v': producers.snap.sha(project / 'input/design.v')}
    written = [{'rel': str(path.relative_to(project)), 'sha256': producers.snap.sha(path)}
               for path in (ledger, derivative)]
    _write(project / 'steps/accepted17/outputs.json', json.dumps({
        'canonical_pass': True, 'written_files': written, 'inputs': inputs}))
    latest = json.loads((project / 'steps/latest_pass.json').read_text())
    latest['steps']['17'] = {'folder': 'accepted17', 'inputs': inputs}
    (project / 'steps/latest_pass.json').write_text(json.dumps(latest))
    return project, params, derivative, native_sdc


@pytest.mark.parametrize('sid', ['19', '20', '27'])
def test_downstream_reuses_accepted_sdc_and_native_rewrite_without_upstream_rerun(
        tmp_path, monkeypatch, sid):
    project, params, _, native_sdc = _accepted_placement_bridge(tmp_path, monkeypatch)
    monkeypatch.setattr(producers, '_step17_current_sdc_bridge', lambda *_: pytest.fail('must not rebuild accepted placement bridge'))
    params['step_id'] = sid
    current = producers._current_physical_sdc(project, params)
    assert current == project / producers.PNR / 'constraint.sdc'
    assert current.read_bytes() != native_sdc.read_bytes()
    # Later reports are legitimate updates, not changed design inputs.
    _write(project / 'reports/phase3/downstream.json', '{"current":true}')
    assert producers._current_physical_sdc(project, params) == current


@pytest.mark.parametrize('damage', ['bridge', 'canonical_sdc', 'native_sdc', 'placement_def', 'step7', 'rollback'])
def test_downstream_rejects_invalid_adopted_bridge(tmp_path, monkeypatch, damage):
    project, params, derivative, native_sdc = _accepted_placement_bridge(tmp_path, monkeypatch)
    target = {'bridge': derivative, 'canonical_sdc': project / producers.PNR / 'constraint.sdc',
              'native_sdc': native_sdc, 'placement_def': project / producers.PNR / 'placed.def',
              'step7': project / 'phase2/stage2/constraints/bounded.asic.sdc',
              'rollback': project / producers.PNR / 'constraint.sdc'}[damage]
    target.write_text('upstream-clock\n' if damage == 'rollback' else 'changed\n')
    params['step_id'] = '19'
    with pytest.raises(em.Refusal, match='BACKEND_CURRENT_SDC_UNBOUND'):
        producers._current_physical_sdc(project, params)


@pytest.mark.parametrize('sid', ['19', '20'])
def test_actual_cts_hold_seam_reads_adopted_sdc_before_native(tmp_path, monkeypatch, sid):
    import librelane_cts_hold as ch
    import librelane_contract as ll
    import phase3_one_shot_runner as R
    project, params, _, _ = _accepted_placement_bridge(tmp_path, monkeypatch)
    params['step_id'] = sid
    class ReachedScene(Exception):
        pass
    def scene(rt, sdc, directory):
        assert sdc == project / producers.PNR / 'constraint.sdc'
        raise ReachedScene
    monkeypatch.setattr(ch, 'signoff_scene_sdc', scene)
    with pytest.raises(ReachedScene):
        producers._cts_split(project, params, R, ll, SimpleNamespace(name='fixture'), {})


def test_same_deck_step32_refresh_does_not_invalidate_step27(tmp_path, monkeypatch):
    from _ppa import timing
    import phase3_one_shot_runner as R
    project, params, _, _ = _accepted_placement_bridge(tmp_path, monkeypatch)
    sdc = project / producers.PNR / 'constraint.sdc'
    source = json.loads((project / 'phase2/stage2/constraints/asic_sdc.json').read_text())
    record = {'text': sdc.read_text(), 'path': source['path'],
              'step7_sha256': source['sha256'], 'deck_sha256': source['deck_sha256'],
              'regenerated': None, 'derivation': None}
    old = sdc.with_name('constraint_sdc_provenance.json').read_bytes()
    monkeypatch.setattr(timing, 'asic_sdc_for_pnr', lambda *_: record)
    R._refresh_default_step32_sdc(project, 'bounded', SimpleNamespace(name='fixture'), '')
    assert sdc.with_name('constraint_sdc_provenance.json').read_bytes() != old
    source['input_digest'] = 'new-author-metadata-with-identical-material-deck'
    (project / 'phase2/stage2/constraints/asic_sdc.json').write_text(json.dumps(source))
    params['step_id'] = '27'
    assert producers._current_physical_sdc(project, params) == sdc


def test_actual_step27_reads_adopted_sdc_before_native(tmp_path, monkeypatch):
    import phase3_one_shot_runner as R
    import librelane_contract as ll
    project, params, _, _ = _accepted_placement_bridge(tmp_path, monkeypatch)
    params['step_id'] = '27'
    _write(project / producers.PNR / 'bounded_pnr.v', 'module bounded; endmodule\n')
    monkeypatch.setattr(producers, '_parameters', lambda *_: (R, ll, SimpleNamespace(name='fixture'), ''))
    class ReachedVoltage(Exception):
        pass
    monkeypatch.setattr(R, '_pdk_nominal_voltage', lambda *_: (_ for _ in ()).throw(ReachedVoltage()))
    with pytest.raises(ReachedVoltage):
        producers.produce_signoff(project, params)


def test_step21_inherits_hold_native_sdc_without_reading_step15(tmp_path, monkeypatch):
    import librelane_contract as ll
    import librelane_route as route
    import phase3_one_shot_runner as R
    project, params, _, native_sdc = _accepted_placement_bridge(tmp_path, monkeypatch)
    params['step_id'] = '21'
    state = native_sdc.parent / 'state_out.json'
    hold_def = _write(project / producers.PNR / 'post_hold.def', 'VERSION 5.8 ;\nDESIGN bounded ;\n')
    _write(project / producers.RPT / 'librelane_cts_hold_handoff.json', json.dumps({
        'measured_state': str(state), 'measured_state_sha256': producers.snap.sha(state),
        'views': {'post_hold_def': {'dest': str(hold_def), 'dest_sha256': producers.snap.sha(hold_def)}}}))
    config = _write(project / 'routing-config.json', '{}\n')
    monkeypatch.setattr(ll, 'resolve_step_configs', lambda *_a, **_k: {'OpenROAD.DetailedRouting': config})
    monkeypatch.setattr(R, '_resolved_cell_policy', lambda configs, *_: (configs, []))
    monkeypatch.setattr(route, 'named_violation_config', lambda *_a, **_k: config)
    monkeypatch.setattr(producers, '_step15_sdc', lambda *_: pytest.fail('routing must inherit its current native state'))
    seen = []
    def run(project, image, tasks, **kwargs):
        seen.append(tasks[0][2]); return []
    monkeypatch.setattr(ll, 'run_chain', run)
    producers._state_chain(project, params, ['OpenROAD.DetailedRouting'], 'post_hold.def')
    assert seen == [state]
    assert json.loads(state.read_text())['sdc'] == str(native_sdc)


def test_downstream_without_new_bridge_keeps_original_strict_reader(tmp_path):
    project, _ = _make_published_project(tmp_path)
    params = {'step_id': '19', 'execution_mode': 'default'}
    assert producers._current_physical_sdc(project, params) == project / producers.PNR / 'step15.constraint.sdc'
    (project / producers.PNR / 'constraint.sdc').write_text('unapproved change\n')
    with pytest.raises(em.Refusal, match='BACKEND_STEP15_SDC_BINDING_CHANGED'):
        producers._current_physical_sdc(project, params)
